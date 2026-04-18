import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant")
ASSISTANT_RUNTIME_MODULE = importlib.import_module("backend.rpa.assistant_runtime")


class _FakeModel:
    def __init__(self, response):
        self._response = response

    async def ainvoke(self, _messages):
        return self._response


class _FakeStreamingModel:
    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, _messages):
        for chunk in self._chunks:
            yield chunk


class _FakePage:
    url = "https://example.com"

    async def title(self):
        return "Example"


class _FakeSnapshotFrame:
    def __init__(self, name, url, frame_path, elements=None, child_frames=None):
        self.name = name
        self.url = url
        self._frame_path = frame_path
        self._elements = elements or []
        self.child_frames = child_frames or []

    async def evaluate(self, _script):
        return json.dumps(self._elements)


class _FakeSnapshotPage:
    url = "https://example.com"

    def __init__(self, main_frame):
        self.main_frame = main_frame

    async def title(self):
        return "Example"


class _FakeLocator:
    def __init__(self, text=""):
        self.click_calls = 0
        self.text = text

    async def click(self):
        self.click_calls += 1

    async def inner_text(self):
        return self.text


class _FakeFrameScope:
    def __init__(self):
        self.locator_calls = []
        self.locator_obj = _FakeLocator("Resolved text")

    def locator(self, selector):
        self.locator_calls.append(selector)
        return self.locator_obj

    def frame_locator(self, selector):
        self.locator_calls.append(f"frame:{selector}")
        return self

    def get_by_role(self, role, **kwargs):
        self.locator_calls.append(f"role:{role}:{kwargs.get('name', '')}")
        return self.locator_obj

    def get_by_text(self, value):
        self.locator_calls.append(f"text:{value}")
        return self.locator_obj


class _FakeActionPage(_FakePage):
    def __init__(self):
        self.scope = _FakeFrameScope()
        self.goto_calls = []
        self.load_state_calls = []

    def frame_locator(self, selector):
        self.scope.locator_calls.append(f"frame:{selector}")
        return self.scope

    def locator(self, selector):
        self.scope.locator_calls.append(selector)
        return self.scope.locator_obj

    def get_by_role(self, role, **kwargs):
        self.scope.locator_calls.append(f"role:{role}:{kwargs.get('name', '')}")
        return self.scope.locator_obj

    def get_by_text(self, value):
        self.scope.locator_calls.append(f"text:{value}")
        return self.scope.locator_obj

    async def goto(self, url):
        self.goto_calls.append(url)

    async def wait_for_load_state(self, state):
        self.load_state_calls.append(state)


class RPAReActAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_llm_preserves_whitespace_between_stream_chunks(self):
        response_text = 'await page.goto("https://github.com/trending?since=weekly")\n'
        stream_chunks = [
            SimpleNamespace(content="await", additional_kwargs={}),
            SimpleNamespace(content=" page", additional_kwargs={}),
            SimpleNamespace(content='.goto("https://github.com/trending?since=weekly")\n', additional_kwargs={}),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_extracts_text_from_stream_content_blocks(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        stream_chunks = [
            SimpleNamespace(
                content=[
                    {"type": "thinking", "thinking": "inspect the page"},
                    {"type": "text", "text": response_text},
                ],
                additional_kwargs={},
            ),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_falls_back_to_stream_reasoning_content(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        stream_chunks = [
            SimpleNamespace(
                content="",
                additional_kwargs={"reasoning_content": response_text},
            ),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_extracts_text_from_content_blocks(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        fake_response = SimpleNamespace(
            content=[
                {"type": "thinking", "thinking": "inspect the page"},
                {"type": "text", "text": response_text},
            ],
            additional_kwargs={},
        )

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeModel(fake_response),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_run_falls_back_to_reasoning_content_when_text_is_empty(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        fake_response = SimpleNamespace(
            content="",
            additional_kwargs={"reasoning_content": response_text},
        )
        agent = ASSISTANT_MODULE.RPAReActAgent()

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeModel(fake_response),
        ), patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value={"url": "https://example.com", "title": "Example", "frames": []}),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=_FakePage(),
                goal="finish the task",
                existing_steps=[],
            ):
                events.append(event)

        self.assertEqual(
            [event["event"] for event in events],
            ["agent_thought", "agent_done"],
        )

    async def test_react_agent_build_observation_lists_frames_and_collections(self):
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
                    "collections": [],
                },
                {
                    "frame_hint": "iframe title=results",
                    "frame_path": ["iframe[title='results']"],
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Result A"}],
                    "collections": [{"kind": "search_results", "item_count": 2}],
                },
            ],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(snapshot, 0)

        self.assertIn("Frame: main document", content)
        self.assertIn("Frame: iframe title=results", content)
        self.assertIn("Collection: search_results (2 items)", content)

    async def test_react_agent_build_observation_lists_snapshot_v2_containers(self):
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [
                {
                    "container_id": "table-1",
                    "frame_path": [],
                    "container_kind": "table",
                    "name": "合同列表",
                    "summary": "合同下载列表",
                    "child_actionable_ids": ["a-1", "a-2"],
                    "child_content_ids": ["c-1", "c-2"],
                }
            ],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(snapshot, 0)

        self.assertIn("Container: table 合同列表", content)
        self.assertIn("actionable=2", content)
        self.assertIn("content=2", content)

    async def test_react_agent_executes_structured_collection_action_with_frame_context(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": ["iframe[title='results']"],
                    "frame_hint": "iframe title=results",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Result A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Result B"},
                            ],
                        }
                    ],
                }
            ],
        }
        responses = [
            json.dumps(
                {
                    "thought": "click the first item",
                    "action": "execute",
                    "operation": "click",
                    "description": "点击列表中的第一个项目",
                    "target_hint": {"role": "link", "name": "item"},
                    "collection_hint": {"kind": "search_results"},
                    "ordinal": "first",
                    "risk": "none",
                    "risk_reason": "",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                },
                ensure_ascii=False,
            ),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="点击列表中的第一个项目",
                existing_steps=[],
            ):
                events.append(event)

        step_done = next(event for event in events if event["event"] == "agent_step_done")
        self.assertEqual(page.scope.locator_calls[0], "frame:iframe[title='results']")
        self.assertEqual(
            json.loads(step_done["data"]["step"]["target"]),
            {
                "method": "collection_item",
                "collection": {"method": "css", "value": "main article.card"},
                "ordinal": "first",
                "item": {"method": "css", "value": "h2 a"},
            },
        )

class RPAAssistantFrameAwareSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_page_snapshot_v2_includes_actionable_content_and_containers(self):
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
        )
        page = _FakeSnapshotPage(main)

        with patch.object(
            ASSISTANT_RUNTIME_MODULE,
            "_extract_frame_snapshot_v2",
            new=AsyncMock(
                return_value={
                    "actionable_nodes": [
                        {
                            "node_id": "act-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "role": "link",
                            "name": "ContractList20260411124156",
                            "action_kinds": ["click"],
                            "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"},
                            "locator_candidates": [
                                {
                                    "kind": "role",
                                    "selected": True,
                                    "locator": {
                                        "method": "role",
                                        "role": "link",
                                        "name": "ContractList20260411124156",
                                    },
                                }
                            ],
                            "validation": {"status": "ok"},
                            "bbox": {"x": 10, "y": 20, "width": 120, "height": 24},
                            "center_point": {"x": 70, "y": 32},
                            "is_visible": True,
                            "is_enabled": True,
                            "hit_test_ok": True,
                            "element_snapshot": {"tag": "a", "text": "ContractList20260411124156"},
                        }
                    ],
                    "content_nodes": [
                        {
                            "node_id": "content-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "semantic_kind": "cell",
                            "text": "已归档",
                            "bbox": {"x": 300, "y": 20, "width": 80, "height": 24},
                            "locator": {"method": "text", "value": "已归档"},
                            "element_snapshot": {"tag": "td", "text": "已归档"},
                        }
                    ],
                    "containers": [
                        {
                            "container_id": "table-1",
                            "frame_path": [],
                            "container_kind": "table",
                            "name": "合同列表",
                            "bbox": {"x": 0, "y": 0, "width": 800, "height": 600},
                            "summary": "合同下载列表",
                            "child_actionable_ids": ["act-1"],
                            "child_content_ids": ["content-1"],
                        }
                    ],
                }
            ),
        ):
            snapshot = await ASSISTANT_MODULE.build_page_snapshot(
                page,
                frame_path_builder=lambda frame: frame._frame_path,
            )

        self.assertIn("actionable_nodes", snapshot)
        self.assertIn("content_nodes", snapshot)
        self.assertIn("containers", snapshot)
        self.assertEqual(snapshot["actionable_nodes"][0]["locator"]["method"], "role")
        self.assertEqual(snapshot["content_nodes"][0]["semantic_kind"], "cell")
        self.assertEqual(snapshot["containers"][0]["container_kind"], "table")

    async def test_build_page_snapshot_includes_iframe_elements_and_collections(self):
        iframe = _FakeSnapshotFrame(
            name="editor",
            url="https://example.com/editor",
            frame_path=["iframe[title='editor']"],
            elements=[
                {"index": 1, "tag": "a", "role": "link", "name": "Quarterly Report"},
                {"index": 2, "tag": "a", "role": "link", "name": "Annual Report"},
            ],
        )
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
            child_frames=[iframe],
        )
        page = _FakeSnapshotPage(main)

        snapshot = await ASSISTANT_MODULE.build_page_snapshot(
            page,
            frame_path_builder=lambda frame: frame._frame_path,
        )

        self.assertEqual(snapshot["title"], "Example")
        self.assertEqual(len(snapshot["frames"]), 2)
        self.assertEqual(snapshot["frames"][1]["frame_path"], ["iframe[title='editor']"])
        self.assertEqual(snapshot["frames"][1]["elements"][0]["name"], "Quarterly Report")
        self.assertEqual(snapshot["frames"][1]["collections"][0]["item_count"], 2)

    async def test_build_page_snapshot_skips_detached_child_frame(self):
        detached = _FakeSnapshotFrame(
            name="detached",
            url="https://example.com/detached",
            frame_path=["iframe[title='detached']"],
            elements=[{"index": 1, "tag": "a", "role": "link", "name": "Detached Link"}],
        )
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
            child_frames=[detached],
        )
        page = _FakeSnapshotPage(main)

        async def flaky_frame_path_builder(frame):
            if frame is detached:
                raise RuntimeError("Frame.frame_element: Frame has been detached.")
            return frame._frame_path

        snapshot = await ASSISTANT_MODULE.build_page_snapshot(
            page,
            frame_path_builder=flaky_frame_path_builder,
        )

        self.assertEqual(len(snapshot["frames"]), 1)
        self.assertEqual(snapshot["frames"][0]["frame_path"], [])

    async def test_detect_collections_builds_structured_template_from_repeated_context(self):
        collections = ASSISTANT_RUNTIME_MODULE._detect_collections(
            [
                {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                {
                    "index": 2,
                    "tag": "a",
                    "role": "link",
                    "name": "Item A",
                    "collection_container_selector": "main article.card",
                    "collection_item_selector": "h2 a",
                },
                {
                    "index": 3,
                    "tag": "a",
                    "role": "link",
                    "name": "Item B",
                    "collection_container_selector": "main article.card",
                    "collection_item_selector": "h2 a",
                },
            ],
            [],
        )

        self.assertGreaterEqual(len(collections), 1)
        self.assertEqual(collections[0]["kind"], "repeated_items")
        self.assertEqual(collections[0]["container_hint"]["locator"], {"method": "css", "value": "main article.card"})
        self.assertEqual(collections[0]["item_hint"]["locator"], {"method": "css", "value": "h2 a"})
        self.assertEqual(collections[0]["items"][0]["name"], "Item A")
        self.assertEqual(collections[0]["items"][1]["name"], "Item B")

    async def test_pick_first_item_uses_collection_scope_not_global_page_order(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "elements": [{"name": "Sidebar Link", "role": "link"}],
                    "collections": [],
                },
                {
                    "frame_path": ["iframe[title='results']"],
                    "elements": [],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "items": [
                                {"name": "Result A", "role": "link"},
                                {"name": "Result B", "role": "link"},
                            ],
                        }
                    ],
                },
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_collection_target(
            snapshot,
            {"action": "click", "ordinal": "first"},
        )

        self.assertEqual(resolved["frame_path"], ["iframe[title='results']"])
        self.assertEqual(resolved["resolved_target"]["name"], "Result A")

    async def test_sort_nodes_by_visual_position_orders_top_to_bottom_then_left_to_right(self):
        nodes = [
            {"node_id": "download-2", "name": "文件二", "bbox": {"x": 40, "y": 60, "width": 80, "height": 20}},
            {"node_id": "download-1", "name": "文件一", "bbox": {"x": 20, "y": 20, "width": 80, "height": 20}},
            {"node_id": "download-3", "name": "文件三", "bbox": {"x": 100, "y": 20, "width": 80, "height": 20}},
        ]

        ordered = ASSISTANT_RUNTIME_MODULE._sort_nodes_by_visual_position(nodes)

        self.assertEqual([node["name"] for node in ordered], ["文件一", "文件三", "文件二"])


class RPAAssistantStructuredExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_structured_intent_uses_bbox_order_for_first_match_in_single_pass(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "download-1",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124156",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124156"},
                    "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124156"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                    "bbox": {"x": 20, "y": 20, "width": 80, "height": 20},
                },
                {
                    "node_id": "download-2",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124157",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124157"},
                    "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124157"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                    "bbox": {"x": 20, "y": 60, "width": 80, "height": 20},
                },
            ],
            "content_nodes": [],
            "containers": [
                {
                    "container_id": "table-1",
                    "frame_path": [],
                    "container_kind": "table",
                    "name": "合同列表",
                    "bbox": {"x": 0, "y": 0, "width": 800, "height": 600},
                    "summary": "合同下载列表",
                    "child_actionable_ids": ["download-1", "download-2"],
                    "child_content_ids": [],
                }
            ],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个文件下载",
                "prompt": "点击第一个文件下载",
                "target_hint": {"role": "link", "name": "contractlist"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["value"], "ContractList20260411124156")
        self.assertEqual(resolved["resolved"]["ordinal"], "first")
        self.assertNotIn("assistant_diagnostics", resolved["resolved"])

    async def test_resolve_structured_intent_prefers_snapshot_locator_bundle_for_actionable_node(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "download-1",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124156",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124156"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": False,
                            "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"},
                        },
                        {
                            "kind": "text",
                            "selected": True,
                            "locator": {"method": "text", "value": "ContractList20260411124156"},
                        },
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个文件下载",
                "target_hint": {"role": "link", "name": "contractlist"},
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "text")
        self.assertTrue(resolved["resolved"]["locator_candidates"][1]["selected"])

    async def test_resolve_structured_intent_extract_text_prefers_content_nodes(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "button-1",
                    "frame_path": [],
                    "container_id": "card-1",
                    "role": "button",
                    "name": "复制标题",
                    "action_kinds": ["click"],
                    "locator": {"method": "role", "role": "button", "name": "复制标题"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "button", "name": "复制标题"},
                        }
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                }
            ],
            "content_nodes": [
                {
                    "node_id": "title-1",
                    "frame_path": [],
                    "container_id": "card-1",
                    "semantic_kind": "heading",
                    "role": "heading",
                    "text": "Quarterly Report",
                    "bbox": {"x": 20, "y": 20, "width": 200, "height": 24},
                    "locator": {"method": "text", "value": "Quarterly Report"},
                    "element_snapshot": {"tag": "h2", "text": "Quarterly Report"},
                }
            ],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "report title"},
                "result_key": "report_title",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "text")
        self.assertEqual(resolved["resolved"]["content_node"]["semantic_kind"], "heading")

    async def test_execute_structured_click_does_not_mark_local_expansion_in_single_pass_mode(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击第一个文件下载",
            "prompt": "点击第一个文件下载",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "text", "value": "ContractList20260411124156"},
                "locator_candidates": [
                    {
                        "kind": "text",
                        "selected": True,
                        "locator": {"method": "text", "value": "ContractList20260411124156"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": "first",
                "selected_locator_kind": "text",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_calls[0], "text:ContractList20260411124156")
        self.assertNotIn("used_local_expansion", result["step"]["assistant_diagnostics"])

    async def test_execute_structured_click_uses_frame_locator_chain(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击发送按钮",
            "prompt": "点击发送按钮",
            "resolved": {
                "frame_path": ["iframe[title='editor']"],
                "locator": {"method": "role", "role": "button", "name": "Send"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Send"},
                    }
                ],
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_calls[0], "frame:iframe[title='editor']")
        self.assertEqual(result["step"]["frame_path"], ["iframe[title='editor']"])
        self.assertEqual(result["step"]["source"], "ai")
        self.assertEqual(
            result["step"]["target"],
            '{"method": "role", "role": "button", "name": "Send"}',
        )

    async def test_execute_structured_click_persists_adaptive_collection_target_for_first_collection_item(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击第一个卡片项目",
            "prompt": "点击列表中的第一个项目",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "role", "role": "link", "name": "Item A"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "link", "name": "Item A"},
                    }
                ],
                "collection_hint": {
                    "kind": "repeated_items",
                    "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                },
                "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                "ordinal": "first",
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(
            json.loads(result["step"]["target"]),
            {
                "method": "collection_item",
                "collection": {"method": "css", "value": "main article.card"},
                "ordinal": "first",
                "item": {"method": "css", "value": "h2 a"},
            },
        )
        self.assertEqual(result["step"]["collection_hint"]["kind"], "repeated_items")
        self.assertEqual(result["step"]["item_hint"]["locator"], {"method": "css", "value": "h2 a"})
        self.assertEqual(result["step"]["ordinal"], "first")

    async def test_execute_structured_navigate_uses_page_goto(self):
        page = _FakeActionPage()
        intent = {
            "action": "navigate",
            "description": "打开 GitHub Trending 页面",
            "prompt": "打开 GitHub Trending 页面",
            "value": "https://github.com/trending",
            "resolved": {
                "frame_path": [],
                "locator": None,
                "locator_candidates": [],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "navigate",
                "url": "https://github.com/trending",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.goto_calls, ["https://github.com/trending"])
        self.assertEqual(page.load_state_calls, ["domcontentloaded"])
        self.assertEqual(result["step"]["action"], "navigate")
        self.assertEqual(result["step"]["url"], "https://github.com/trending")

    async def test_execute_structured_extract_text_persists_result_key(self):
        page = _FakeActionPage()
        intent = {
            "action": "extract_text",
            "description": "提取最近一条 issue 的标题",
            "prompt": "提取最近一条 issue 的标题",
            "result_key": "latest_issue_title",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "role", "role": "link", "name": "Issue Title"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "link", "name": "Issue Title"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Resolved text")
        self.assertEqual(result["step"]["action"], "extract_text")
        self.assertEqual(result["step"]["result_key"], "latest_issue_title")

    async def test_resolve_structured_intent_prefers_collection_item_inside_iframe(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Sidebar"}],
                    "collections": [],
                },
                {
                    "frame_path": ["iframe[title='results']"],
                    "frame_hint": "iframe title=results",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Result A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Result B"},
                            ],
                        }
                    ],
                },
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个结果",
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["frame_path"], ["iframe[title='results']"])
        self.assertEqual(resolved["resolved"]["locator"]["method"], "role")
        self.assertEqual(resolved["resolved"]["locator"]["name"], "Result A")

    async def test_resolve_structured_intent_prefers_structured_collection_over_flat_links(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                        {"index": 2, "tag": "a", "role": "link", "name": "Homepage", "href": "/"},
                        {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                        {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                    ],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": [],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "item_count": 4,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Homepage", "href": "/"},
                                {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "item"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Item A")
        self.assertEqual(resolved["resolved"]["collection_hint"]["kind"], "repeated_items")

    async def test_resolve_structured_intent_normalizes_first_ordinal_from_prompt(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "item"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "25",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Item A")
        self.assertEqual(resolved["resolved"]["ordinal"], "first")

    async def test_resolve_structured_intent_falls_back_to_direct_target_when_collection_hint_has_no_match(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "input", "role": "textbox", "name": "Search", "placeholder": "Search"}
                    ],
                    "collections": [],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "fill",
                "description": "在搜索框中输入关键词",
                "prompt": "在搜索框中输入关键词",
                "target_hint": {"role": "textbox", "name": "search"},
                "collection_hint": {"kind": "cards"},
                "ordinal": "1",
                "value": "github",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "role")
        self.assertEqual(resolved["resolved"]["locator"]["name"], "Search")
        self.assertEqual(resolved["resolved"]["collection_hint"], {})

    async def test_resolve_structured_intent_prefers_primary_collection_items_over_repeated_controls(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "div.actions a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Star project A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Star project B"},
                            ],
                        },
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 3, "tag": "a", "role": "link", "name": "Project A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Project B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目链接",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "project title link"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Project A")
        self.assertEqual(
            resolved["resolved"]["item_hint"]["locator"],
            {"method": "css", "value": "h2 a"},
        )


class RPAControlFlowHelperTests(unittest.TestCase):
    def test_detect_upgrade_reason_identifies_polling_loop(self):
        from backend.rpa.control_flow import detect_upgrade_reason

        text = "如果第一个条目状态不是完成，就每隔500毫秒点击刷新，直到状态变为完成"

        self.assertEqual(detect_upgrade_reason(text), "polling_loop")

    def test_detect_upgrade_reason_identifies_conditional_branch(self):
        from backend.rpa.control_flow import detect_upgrade_reason

        text = "如果列表为空，则显示提示，否则继续处理"

        self.assertEqual(detect_upgrade_reason(text), "conditional_branch")

    def test_detect_upgrade_reason_identifies_dynamic_selection(self):
        from backend.rpa.control_flow import detect_upgrade_reason

        text = "选择最新的结果或最低价格的选项"

        self.assertEqual(detect_upgrade_reason(text), "dynamic_selection")

    def test_detect_upgrade_reason_keeps_atomic_click_structured(self):
        from backend.rpa.control_flow import detect_upgrade_reason

        text = "点击列表中的第一个条目"

        self.assertEqual(detect_upgrade_reason(text), "none")

    def test_detect_upgrade_reason_does_not_match_at_least_threshold_phrases(self):
        from backend.rpa.control_flow import detect_upgrade_reason

        text = "wait at least 5 seconds"

        self.assertEqual(detect_upgrade_reason(text), "none")

    def test_normalize_ai_script_function_returns_empty_wrapper_for_blank_code(self):
        from backend.rpa.control_flow import normalize_ai_script_function

        self.assertEqual(
            normalize_ai_script_function(""),
            "async def run(page):\n    return None",
        )

    def test_normalize_ai_script_function_returns_existing_async_run_stripped(self):
        from backend.rpa.control_flow import normalize_ai_script_function

        code = "  async def run(page):\n      return page.url  \n"

        self.assertEqual(normalize_ai_script_function(code), "async def run(page):\n      return page.url")

    def test_normalize_ai_script_function_wraps_bare_body(self):
        from backend.rpa.control_flow import normalize_ai_script_function

        code = 'await page.get_by_role("button", name="刷新").click()\nawait page.wait_for_timeout(500)'

        self.assertEqual(
            normalize_ai_script_function(code),
            'async def run(page):\n    await page.get_by_role("button", name="刷新").click()\n    await page.wait_for_timeout(500)',
        )

    def test_build_ai_script_step_stores_normalized_function_and_diagnostics(self):
        from backend.rpa.control_flow import build_ai_script_step

        parsed = {
            "execution_mode": "code",
            "upgrade_reason": "polling_loop",
            "template": "poll_until_text_then_download",
            "interval_ms": 500,
            "timeout_ms": 60000,
        }
        step = build_ai_script_step(
            prompt="如果状态不是完成就刷新直到完成并下载",
            description="等待完成后下载",
            code='await page.get_by_role("button", name="刷新").click()',
            parsed=parsed,
        )

        self.assertEqual(step["action"], "ai_script")
        self.assertEqual(step["source"], "ai")
        self.assertTrue(step["value"].startswith("async def run(page):"))
        self.assertIn('await page.get_by_role("button", name="刷新").click()', step["value"])
        self.assertEqual(step["assistant_diagnostics"]["execution_mode"], "code")
        self.assertEqual(step["assistant_diagnostics"]["upgrade_reason"], "polling_loop")
        self.assertEqual(step["assistant_diagnostics"]["template"], "poll_until_text_then_download")
        self.assertEqual(step["assistant_diagnostics"]["interval_ms"], 500)
        self.assertEqual(step["assistant_diagnostics"]["timeout_ms"], 60000)

    def test_build_ai_script_step_falls_back_to_custom_logic_without_control_flow_reason(self):
        from backend.rpa.control_flow import build_ai_script_step

        parsed = {
            "execution_mode": "code",
            "upgrade_reason": "bogus_reason",
            "template": "custom_click",
        }
        step = build_ai_script_step(
            prompt="点击列表中的第一个条目",
            description="点击列表中的第一个条目",
            code='await page.get_by_role("button", name="提交").click()',
            parsed=parsed,
        )

        self.assertEqual(step["assistant_diagnostics"]["upgrade_reason"], "custom_logic")
        self.assertEqual(step["assistant_diagnostics"]["template"], "custom_click")
        self.assertNotIn("bogus_reason", str(step["assistant_diagnostics"]))


class RPAAssistantPromptFormattingTests(unittest.TestCase):
    def test_build_messages_lists_frames_and_collections(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        snapshot = {
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
                    "collections": [],
                },
                {
                    "frame_hint": "iframe title=results",
                    "frame_path": ["iframe[title='results']"],
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Result A"}],
                    "collections": [{"kind": "search_results", "item_count": 2}],
                },
            ]
        }

        messages = assistant._build_messages("点击第一个结果", [], snapshot, [])
        content = messages[-1]["content"]

        self.assertIn("Frame: main document", content)
        self.assertIn("Frame: iframe title=results", content)
        self.assertIn("Collection: search_results (2 items)", content)

    def test_react_system_prompt_requires_explicit_extraction_before_done(self):
        prompt = ASSISTANT_MODULE.REACT_SYSTEM_PROMPT

        self.assertIn("For extraction tasks, use operation=extract_text", prompt)
        self.assertIn('"result_key": "short_ascii_snake_case_key_for_extracted_value"', prompt)
        self.assertIn("Do not mark the task done just because the data is visible on the page.", prompt)
        self.assertIn("Execute the extraction step first and return the extracted value.", prompt)


if __name__ == "__main__":
    unittest.main()
