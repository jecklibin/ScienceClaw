import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant")
ASSISTANT_RUNTIME_MODULE = importlib.import_module("backend.rpa.assistant_runtime")
SEGMENT_MODELS_MODULE = importlib.import_module("backend.rpa.segment_models")
SEGMENT_VALIDATOR_MODULE = importlib.import_module("backend.rpa.segment_validator")
RPA_ROUTE_MODULE = importlib.import_module("backend.route.rpa")


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


class _FailingLocator(_FakeLocator):
    def __init__(self, exc: Exception):
        super().__init__("")
        self._exc = exc

    async def click(self):
        raise self._exc


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


class _FailingActionPage(_FakeActionPage):
    def __init__(self, exc: Exception):
        super().__init__()
        self.scope.locator_obj = _FailingLocator(exc)


class _FailingNavigationPage(_FakeActionPage):
    def __init__(self, exc: Exception, phase: str = "goto"):
        super().__init__()
        self._exc = exc
        self._phase = phase

    async def goto(self, url):
        self.goto_calls.append(url)
        if self._phase == "goto":
            raise self._exc

    async def wait_for_load_state(self, state):
        self.load_state_calls.append(state)
        if self._phase == "load_state":
            raise self._exc


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
            ["segment_planned", "recording_done"],
        )
        self.assertEqual(events[0]["data"], {"thought": "task done"})
        self.assertEqual(events[1]["data"], {"total_steps": 0})

    async def test_react_agent_recovers_from_invalid_planner_json_response(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        snapshot = {"url": "https://example.com/issues", "title": "Issues", "frames": []}
        responses = [
            '{"thought":"need read title","action":"execute","segment_goal":"读取第一个 issue 标题","segment_kind":"read_only","stop_reason":',
            json.dumps(
                {
                    "thought": "retry with valid json",
                    "action": "execute",
                    "segment_goal": "读取第一个 issue 标题",
                    "segment_kind": "read_only",
                    "stop_reason": "goal_reached",
                    "expected_outcome": {"type": "observation", "summary": "返回第一个 issue 标题"},
                    "completion_check": {},
                    "code": "async def run(page):\n    return {'output': 'Bug: example title'}",
                },
                ensure_ascii=False,
            ),
            json.dumps({"thought": "done", "action": "done"}, ensure_ascii=False),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        run_result = SEGMENT_MODELS_MODULE.SegmentRunResult(
            success=True,
            output="Bug: example title",
            page_changed=False,
            selected_artifacts={"output": "Bug: example title"},
            before_snapshot=snapshot,
            after_snapshot=snapshot,
        )

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "run_segment",
            new=AsyncMock(return_value=run_result),
        ):
            events = [event async for event in agent.run(
                session_id="recover-invalid-json",
                page=_FakePage(),
                goal="获取第一个 issues 的标题",
                existing_steps=[],
            )]

        recovering = [event for event in events if event["event"] == "segment_recovering"]
        self.assertTrue(any(event["data"]["error_code"] == "invalid_planner_response" for event in recovering))
        committed = [event for event in events if event["event"] == "segment_committed"]
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["data"]["output"], "Bug: example title")
        self.assertEqual(events[-1], {"event": "recording_done", "data": {"total_steps": 1}})

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

    @unittest.skip("legacy structured-agent path removed in unified segment mode")
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

        step_done = next(event for event in events if event["event"] == "agent_step_committed")
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

    @unittest.skip("legacy structured-agent path removed in unified segment mode")
    async def test_react_agent_emits_recovering_and_retries_after_retryable_structured_failure(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        responses = [
            json.dumps(
                {
                    "thought": "click save",
                    "action": "execute",
                    "execution_mode": "structured",
                    "operation": "click",
                    "description": "Click the save button",
                    "target_hint": {"role": "button", "name": "Save"},
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "click save again",
                    "action": "execute",
                    "execution_mode": "structured",
                    "operation": "click",
                    "description": "Try pressing the save control again",
                    "target_hint": {"role": "button", "name": "Save"},
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "click save one more time",
                    "action": "execute",
                    "execution_mode": "structured",
                    "operation": "click",
                    "description": "Press the Save button once more",
                    "target_hint": {"role": "button", "name": "Save"},
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
        ]
        execution_results = [
            {
                "success": False,
                "error": "Timeout",
                "error_code": "execution_timeout",
                "retryable": True,
                "output": "",
                "step": None,
                "resolved_intent": None,
            },
            {
                "success": False,
                "error": "Timeout again",
                "error_code": "execution_timeout",
                "retryable": True,
                "output": "",
                "step": None,
                "resolved_intent": None,
            },
            {
                "success": True,
                "error": "",
                "error_code": "",
                "retryable": False,
                "output": "ok",
                "step": {
                    "action": "click",
                    "source": "ai",
                    "description": "Click the save button",
                    "prompt": "Click the save button",
                },
                "resolved_intent": {
                    "action": "click",
                    "resolved": {"locator": {"method": "role", "role": "button", "name": "Save"}},
                },
            },
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "run_structured_intent",
            new=AsyncMock(side_effect=execution_results),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="Click the save button",
                existing_steps=[],
            ):
                events.append(event)

        recovering_events = [event for event in events if event["event"] == "agent_recovering"]
        self.assertEqual(len(recovering_events), 2)
        self.assertEqual(
            recovering_events[0]["data"],
            {
                "description": "Click the save button",
                "error_code": "execution_timeout",
                "message": "Timeout",
                "attempt": 1,
            },
        )
        self.assertEqual(
            recovering_events[1]["data"],
            {
                "description": "Try pressing the save control again",
                "error_code": "execution_timeout",
                "message": "Timeout again",
                "attempt": 2,
            },
        )
        warning_events = [event for event in events if event["event"] == "agent_warning"]
        self.assertEqual(len(warning_events), 1)
        self.assertEqual(
            warning_events[0]["data"],
            {
                "description": "Try pressing the save control again",
                "error_code": "execution_timeout",
                "message": "Retry budget nearly exhausted",
            },
        )
        self.assertIn("agent_step_committed", [event["event"] for event in events])
        self.assertEqual(events[-1]["event"], "agent_done")
        self.assertTrue(
            any(
                message["role"] == "user"
                and "Error code: execution_timeout" in message["content"]
                for message in agent._history
            )
        )

    @unittest.skip("legacy code/structured dual-mode events removed in unified segment mode")
    async def test_react_agent_emits_escalated_and_persists_ai_script_for_control_flow(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        code = "await page.wait_for_timeout(500)"
        responses = [
            json.dumps(
                {
                    "thought": "need to poll until save completes",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "polling_loop",
                    "description": "Poll until the save completes",
                    "code": code,
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=AsyncMock(return_value={"success": True, "output": "ok", "error": None}),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="Wait until save completes",
                existing_steps=[],
            ):
                events.append(event)

        escalated_event = next(event for event in events if event["event"] == "agent_escalated")
        self.assertEqual(
            escalated_event["data"],
            {
                "description": "Poll until the save completes",
                "upgrade_reason": "polling_loop",
            },
        )
        step_done = next(event for event in events if event["event"] == "agent_step_committed")
        step = step_done["data"]["step"]
        self.assertEqual(step["action"], "ai_script")
        self.assertEqual(step["assistant_diagnostics"]["execution_mode"], "code")
        self.assertEqual(step["assistant_diagnostics"]["upgrade_reason"], "polling_loop")
        self.assertEqual(step["assistant_diagnostics"]["recovery_attempts"], 0)
        self.assertTrue(step["value"].startswith("async def run(page):"))

    @unittest.skip("legacy code/structured dual-mode events removed in unified segment mode")
    async def test_react_agent_upgrades_runtime_comparison_request_to_ai_script(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {"url": "https://example.com/trending", "title": "Trending", "frames": []}
        code = "\n".join(
            [
                "cards = page.locator('article')",
                "count = await cards.count()",
                "best = None",
                "best_score = -1",
                "for i in range(count):",
                "    card = cards.nth(i)",
                "    text = await card.inner_text()",
                "    score = text.count('stars')",
                "    if score > best_score:",
                "        best = card",
                "        best_score = score",
                "await best.get_by_role('link').first.click()",
            ]
        )
        responses = [
            json.dumps(
                {
                    "thought": "need to inspect and compare all rows",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "dynamic_selection",
                    "description": "Open the project with the most stars this week",
                    "code": code,
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=AsyncMock(return_value={"success": True, "output": "ok", "error": None}),
        ):
            events = []
            async for event in agent.run(
                session_id="session-2",
                page=page,
                goal="Click the project with the most stars this week",
                existing_steps=[],
            ):
                events.append(event)

        event_names = [event["event"] for event in events]
        self.assertIn("agent_escalated", event_names)
        step_done = next(event for event in events if event["event"] == "agent_step_committed")
        step = step_done["data"]["step"]
        self.assertEqual(step["action"], "ai_script")
        self.assertEqual(step["assistant_diagnostics"]["execution_mode"], "code")
        self.assertEqual(step["assistant_diagnostics"]["upgrade_reason"], "dynamic_selection")
        self.assertTrue(step["value"].startswith("async def run(page):"))

    @unittest.skip("legacy structured-agent path removed in unified segment mode")
    async def test_react_agent_emits_attempt_events_but_commits_only_validated_navigation_step(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        responses = [
            json.dumps(
                {
                    "thought": "try clicking issues",
                    "action": "execute",
                    "execution_mode": "structured",
                    "operation": "click",
                    "subgoal": "Enter the Issues page",
                    "description": "Click Issues tab",
                    "target_hint": {"role": "link", "name": "Issues"},
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "navigate directly",
                    "action": "execute",
                    "execution_mode": "structured",
                    "operation": "navigate",
                    "subgoal": "Enter the Issues page",
                    "description": "Open the Issues page directly",
                    "value": "https://example.com/issues",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
        ]
        results = [
            {"success": True, "output": "ok", "step": {"action": "click", "source": "ai", "description": "Click Issues tab", "prompt": "Get the latest issue title"}},
            {"success": True, "output": "ok", "step": {"action": "navigate", "source": "ai", "description": "Open the Issues page directly", "prompt": "Get the latest issue title", "value": "https://example.com/issues"}},
        ]
        snapshots = [
            {"url": "https://example.com/repo", "title": "Repo", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
            {"url": "https://example.com/repo", "title": "Repo", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
            {"url": "https://example.com/repo", "title": "Repo", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
            {"url": "https://example.com/issues", "title": "Issues", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
            {"url": "https://example.com/issues", "title": "Issues", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(side_effect=lambda *_args, **_kwargs: snapshots.pop(0)),
        ), patch.object(
            ASSISTANT_MODULE,
            "run_structured_intent",
            new=AsyncMock(side_effect=lambda *_args, **_kwargs: results.pop(0)),
        ):
            events = []
            async for event in agent.run(
                session_id="session-commit",
                page=page,
                goal="Get the latest issue title",
                existing_steps=[],
            ):
                events.append(event)

        event_names = [event["event"] for event in events]
        self.assertIn("agent_attempted", event_names)
        self.assertIn("agent_step_committed", event_names)
        committed = [event for event in events if event["event"] == "agent_step_committed"]
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["data"]["step"]["action"], "navigate")

    @unittest.skip("legacy code/structured dual-mode events removed in unified segment mode")
    async def test_react_agent_retries_code_mode_after_invalid_generated_code(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        responses = [
            json.dumps(
                {
                    "thought": "need code mode for comparison",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "dynamic_selection",
                    "description": "Compare visible projects and open the highest-star repository",
                    "code": "const repoLinks = [...document.querySelectorAll('a[href]')]",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "rewrite it as Python",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "dynamic_selection",
                    "description": "Compare visible projects and open the highest-star repository",
                    "code": "async def run(page):\n    await page.wait_for_timeout(10)\n    return 'ok'",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
        ]
        executed_codes = []

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        async def fake_execute(_page, code):
            executed_codes.append(code)
            return {"success": True, "output": "ok", "error": None}

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=fake_execute,
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="Click the project with the most stars this week",
                existing_steps=[],
            ):
                events.append(event)

        self.assertEqual(len(executed_codes), 1)
        self.assertTrue(executed_codes[0].startswith("async def run(page):"))
        event_names = [event["event"] for event in events]
        self.assertIn("agent_recovering", event_names)
        self.assertIn("agent_escalated", event_names)
        self.assertIn("agent_step_committed", event_names)
        self.assertNotIn("agent_aborted", event_names)

    @unittest.skip("legacy code/structured dual-mode events removed in unified segment mode")
    async def test_react_agent_aborts_code_mode_after_retry_budget_exhausted(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        responses = [
            json.dumps(
                {
                    "thought": "need code mode for polling",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "polling_loop",
                    "description": "Poll until the save completes",
                    "code": "await page.wait_for_timeout(500)",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "try code again",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "polling_loop",
                    "description": "Poll until the save completes",
                    "code": "await page.wait_for_timeout(500)",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "one more try",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "polling_loop",
                    "description": "Poll until the save completes",
                    "code": "await page.wait_for_timeout(500)",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=AsyncMock(return_value={"success": False, "output": "", "error": "boom"}),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="Wait until save completes",
                existing_steps=[],
            ):
                events.append(event)

        self.assertEqual(
            [event["event"] for event in events],
            [
                "agent_thought",
                "agent_attempted",
                "agent_action",
                "agent_recovering",
                "agent_thought",
                "agent_attempted",
                "agent_action",
                "agent_warning",
                "agent_recovering",
                "agent_thought",
                "agent_attempted",
                "agent_action",
                "agent_aborted",
            ],
        )
        self.assertEqual(
            events[-1]["data"],
            {
                "description": "Poll until the save completes",
                "error_code": "code_execution_failed",
                "message": "boom",
                "reason": "boom",
            },
        )

    def test_react_prompt_requires_python_async_run_for_segment_mode(self):
        self.assertIn("Python", ASSISTANT_MODULE.REACT_SYSTEM_PROMPT)
        self.assertIn("async def run(page):", ASSISTANT_MODULE.REACT_SYSTEM_PROMPT)
        self.assertIn("Never return structured browser actions", ASSISTANT_MODULE.REACT_SYSTEM_PROMPT)

    async def test_react_agent_commits_single_segment_for_dynamic_click_goal(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshots = [
            {"url": "https://example.com/trending", "title": "Trending", "frames": []},
            {"url": "https://example.com/trending", "title": "Trending", "frames": []},
            {"url": "https://example.com/microsoft/markitdown", "title": "markitdown", "frames": []},
            {"url": "https://example.com/microsoft/markitdown", "title": "markitdown", "frames": []},
        ]
        responses = [
            json.dumps(
                {
                    "thought": "compare visible rows and click the highest-star repository",
                    "action": "execute",
                    "segment_goal": "在当前趋势列表中动态比较 stars 并点击最高项",
                    "segment_kind": "state_changing",
                    "stop_reason": "after_state_change",
                    "expected_outcome": {"type": "page_state_changed", "summary": "进入目标仓库页"},
                    "completion_check": {
                        "url_not_same": True,
                        "selected_target_key": "selected_repo_name",
                        "page_contains_selected_target": True,
                    },
                    "code": (
                        "async def run(page):\n"
                        "    return {'selected_repo_name': 'markitdown', 'page_changed': True}"
                    ),
                }
            ),
            json.dumps({"thought": "done", "action": "done"}),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        async def fake_execute(_page, code):
            self.assertTrue(code.startswith("async def run(page):"))
            return {
                "success": True,
                "output": "clicked",
                "error": None,
                "selected_artifacts": {"selected_repo_name": "markitdown"},
                "page_changed": True,
            }

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(side_effect=lambda *_args, **_kwargs: snapshots.pop(0)),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=fake_execute,
        ):
            events = []
            async for event in agent.run(
                session_id="segment-1",
                page=page,
                goal="点击 stars 最多的项目",
                existing_steps=[],
            ):
                events.append(event)

        committed = [event for event in events if event["event"] == "segment_committed"]
        self.assertEqual(len(committed), 1)
        step = committed[0]["data"]["step"]
        self.assertEqual(step["action"], "ai_script")
        self.assertEqual(step["assistant_diagnostics"]["execution_mode"], "segment")
        self.assertEqual(step["assistant_diagnostics"]["segment_kind"], "state_changing")
        self.assertNotIn("microsoft / markitdown", step["description"])
        self.assertEqual(events[-1]["event"], "recording_done")

    async def test_react_agent_uses_selection_compiler_for_first_project_without_llm(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {
            "url": "https://github.com/trending",
            "title": "Trending repositories on GitHub today · GitHub",
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.Box-row"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "owner1 / repo1", "href": "/owner1/repo1"},
                                {"index": 2, "tag": "a", "role": "link", "name": "owner2 / repo2", "href": "/owner2/repo2"},
                            ],
                        }
                    ],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }
        captured_specs = []

        async def should_not_stream(_history, _model_config=None):
            raise AssertionError("LLM planner should not be called for deterministic first-item selection")
            yield  # pragma: no cover

        async def fake_run_segment(*, page, spec, executor, snapshot_builder):
            del page, executor, snapshot_builder
            captured_specs.append(spec)
            return SEGMENT_MODELS_MODULE.SegmentRunResult(
                success=True,
                output="ok",
                page_changed=True,
                selected_artifacts={
                    "selected_repo_name": "owner1 / repo1",
                    "selected_repo_href": "/owner1/repo1",
                },
                before_snapshot=snapshot,
                after_snapshot={
                    "url": "https://github.com/owner1/repo1",
                    "title": "owner1/repo1",
                    "frames": [],
                    "actionable_nodes": [],
                    "content_nodes": [],
                    "containers": [],
                },
            )

        agent._stream_llm = should_not_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "run_segment",
            new=fake_run_segment,
        ):
            events = [event async for event in agent.run(
                session_id="compiled-first-project",
                page=page,
                goal="点击第一个项目",
                existing_steps=[],
            )]

        self.assertEqual(len(captured_specs), 1)
        self.assertEqual(captured_specs[0].segment_kind, "state_changing")
        self.assertIn("article.Box-row", captured_specs[0].code)
        self.assertNotIn("owner1 / repo1", captured_specs[0].code)
        self.assertEqual(events[-1], {"event": "recording_done", "data": {"total_steps": 1}})

    async def test_react_agent_uses_selection_compiler_for_reading_first_issue_title_without_llm(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {
            "url": "https://github.com/public-apis/public-apis/issues",
            "title": "Issues · public-apis/public-apis · GitHub",
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "div[aria-label='Issues'] > div"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "a[href*='/issues/']"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Bug: first visible issue", "href": "/public-apis/public-apis/issues/100"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Bug: second visible issue", "href": "/public-apis/public-apis/issues/99"},
                            ],
                        }
                    ],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }
        captured_specs = []

        async def should_not_stream(_history, _model_config=None):
            raise AssertionError("LLM planner should not be called for deterministic first-issue read")
            yield  # pragma: no cover

        async def fake_run_segment(*, page, spec, executor, snapshot_builder):
            del page, executor, snapshot_builder
            captured_specs.append(spec)
            return SEGMENT_MODELS_MODULE.SegmentRunResult(
                success=True,
                output="Bug: first visible issue",
                page_changed=False,
                selected_artifacts={"selected_issue_title": "Bug: first visible issue"},
                before_snapshot=snapshot,
                after_snapshot={
                    **snapshot,
                    "actionable_nodes": [{"name": "Bug: first visible issue"}],
                    "content_nodes": [{"text": "Bug: first visible issue"}],
                },
            )

        agent._stream_llm = should_not_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "run_segment",
            new=fake_run_segment,
        ):
            events = [event async for event in agent.run(
                session_id="compiled-first-issue",
                page=page,
                goal="获取第一个 issues 的标题",
                existing_steps=[],
            )]

        self.assertEqual(len(captured_specs), 1)
        self.assertEqual(captured_specs[0].segment_kind, "read_only")
        self.assertIn("/issues/", captured_specs[0].code)
        committed = [event for event in events if event["event"] == "segment_committed"]
        self.assertEqual(committed[0]["data"]["output"], "Bug: first visible issue")
        self.assertEqual(events[-1], {"event": "recording_done", "data": {"total_steps": 1}})

    async def test_react_agent_uses_selection_compiler_for_max_stars_without_llm(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {
            "url": "https://github.com/trending",
            "title": "Trending repositories on GitHub today · GitHub",
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.Box-row"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "owner1 / repo1", "href": "/owner1/repo1"},
                                {"index": 2, "tag": "a", "role": "link", "name": "owner2 / repo2", "href": "/owner2/repo2"},
                            ],
                        }
                    ],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }
        captured_specs = []

        async def should_not_stream(_history, _model_config=None):
            raise AssertionError("LLM planner should not be called for deterministic max-selection")
            yield  # pragma: no cover

        async def fake_run_segment(*, page, spec, executor, snapshot_builder):
            del page, executor, snapshot_builder
            captured_specs.append(spec)
            return SEGMENT_MODELS_MODULE.SegmentRunResult(
                success=True,
                output="ok",
                page_changed=True,
                selected_artifacts={
                    "selected_repo_name": "owner2 / repo2",
                    "selected_repo_href": "/owner2/repo2",
                },
                before_snapshot=snapshot,
                after_snapshot={
                    "url": "https://github.com/owner2/repo2",
                    "title": "owner2/repo2",
                    "frames": [],
                    "actionable_nodes": [],
                    "content_nodes": [],
                    "containers": [],
                },
            )

        agent._stream_llm = should_not_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "run_segment",
            new=fake_run_segment,
        ):
            events = [event async for event in agent.run(
                session_id="compiled-max-stars",
                page=page,
                goal="点击 stars 数最多的项目",
                existing_steps=[],
            )]

        self.assertEqual(len(captured_specs), 1)
        self.assertIn("/stargazers", captured_specs[0].code)
        self.assertNotIn("owner1 / repo1", captured_specs[0].code)
        self.assertNotIn("owner2 / repo2", captured_specs[0].code)
        self.assertEqual(events[-1], {"event": "recording_done", "data": {"total_steps": 1}})

    async def test_react_agent_rejects_javascript_segment_and_recovers_with_python(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshots = [
            {"url": "https://example.com/trending", "title": "Trending", "frames": []},
            {"url": "https://example.com/trending", "title": "Trending", "frames": []},
            {"url": "https://example.com/repo", "title": "Repo", "frames": []},
            {"url": "https://example.com/repo", "title": "Repo", "frames": []},
            {"url": "https://example.com/repo", "title": "Repo", "frames": []},
        ]
        responses = [
            json.dumps(
                {
                    "thought": "plan a dynamic segment",
                    "action": "execute",
                    "segment_goal": "找出 stars 最高的项目并点击",
                    "segment_kind": "state_changing",
                    "stop_reason": "after_state_change",
                    "expected_outcome": {"type": "page_state_changed", "summary": "进入目标仓库页"},
                    "completion_check": {"url_not_same": True},
                    "code": "const repoLinks = [...document.querySelectorAll('a[href]')];",
                }
            ),
            json.dumps(
                {
                    "thought": "rewrite as python",
                    "action": "execute",
                    "segment_goal": "找出 stars 最高的项目并点击",
                    "segment_kind": "state_changing",
                    "stop_reason": "after_state_change",
                    "expected_outcome": {"type": "page_state_changed", "summary": "进入目标仓库页"},
                    "completion_check": {"url_not_same": True},
                    "code": "async def run(page):\n    return {'page_changed': True}",
                }
            ),
            json.dumps({"thought": "done", "action": "done"}),
        ]
        executed_codes = []

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        async def fake_execute(_page, code):
            executed_codes.append(code)
            return {"success": True, "output": "ok", "error": None, "page_changed": True}

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(side_effect=lambda *_args, **_kwargs: snapshots.pop(0)),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=fake_execute,
        ):
            events = []
            async for event in agent.run(
                session_id="segment-2",
                page=page,
                goal="点击 stars 最多的项目",
                existing_steps=[],
            ):
                events.append(event)

        recovering = [event for event in events if event["event"] == "segment_recovering"]
        self.assertGreaterEqual(len(recovering), 1)
        self.assertEqual(recovering[0]["data"]["error_code"], "invalid_generated_code")
        self.assertEqual(len(executed_codes), 1)
        self.assertTrue(executed_codes[0].startswith("async def run(page):"))
        self.assertIn("segment_committed", [event["event"] for event in events])

    async def test_react_agent_requires_read_segment_before_done_for_read_goal(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshots = [
            {"url": "https://github.com/microsoft/markitdown", "title": "Repo", "frames": []},
            {"url": "https://github.com/microsoft/markitdown", "title": "Repo", "frames": []},
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
        ]
        responses = [
            json.dumps(
                {
                    "thought": "go to issues first",
                    "action": "execute",
                    "segment_goal": "打开当前仓库的 Issues 列表页以便读取最近一条 issue 标题",
                    "segment_kind": "state_changing",
                    "stop_reason": "after_state_change",
                    "expected_outcome": {"type": "page_state_changed", "summary": "进入 Issues 列表页"},
                    "completion_check": {"url_not_same": True},
                    "code": "async def run(page):\n    return {'page_changed': True}",
                }
            ),
            json.dumps({"thought": "issues page is visible now", "action": "done"}),
            json.dumps(
                {
                    "thought": "extract the first issue title",
                    "action": "execute",
                    "segment_goal": "提取当前 Issues 列表页第一条 issue 的标题",
                    "segment_kind": "read_only",
                    "stop_reason": "goal_reached",
                    "expected_outcome": {"type": "observation", "summary": "拿到最近一条 issue 标题"},
                    "completion_check": {},
                    "code": "async def run(page):\n    return {'output': 'Bug: example title'}",
                }
            ),
            json.dumps({"thought": "done", "action": "done"}),
        ]
        execute_results = [
            {
                "success": True,
                "output": "ok",
                "error": None,
                "selected_artifacts": {},
                "page_changed": True,
            },
            {
                "success": True,
                "output": "Bug: example title",
                "error": None,
                "selected_artifacts": {},
                "page_changed": False,
            },
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        async def fake_execute(_page, _code):
            return execute_results.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(side_effect=lambda *_args, **_kwargs: snapshots.pop(0)),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=fake_execute,
        ):
            events = [event async for event in agent.run(
                session_id="segment-read-goal",
                page=page,
                goal="获取最近一条 issues 的标题",
                existing_steps=[],
            )]

        recovering = [event for event in events if event["event"] == "segment_recovering"]
        self.assertTrue(any(event["data"]["error_code"] == "goal_not_complete" for event in recovering))
        committed = [event for event in events if event["event"] == "segment_committed"]
        self.assertEqual(len(committed), 2)
        self.assertEqual(committed[1]["data"]["output"], "Bug: example title")
        self.assertEqual(events[-1]["event"], "recording_done")
        self.assertEqual(events[-1]["data"]["total_steps"], 2)

    async def test_react_agent_rejects_non_adaptive_hard_coded_segment_code(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshots = [
            {
                "url": "https://github.com/microsoft/markitdown",
                "title": "Repo",
                "frames": [
                    {
                        "frame_hint": "main document",
                        "frame_path": [],
                        "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Issues 340"}],
                        "collections": [],
                    }
                ],
            },
            {
                "url": "https://github.com/microsoft/markitdown",
                "title": "Repo",
                "frames": [
                    {
                        "frame_hint": "main document",
                        "frame_path": [],
                        "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Issues 340"}],
                        "collections": [],
                    }
                ],
            },
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
            {"url": "https://github.com/microsoft/markitdown/issues", "title": "Issues", "frames": []},
        ]
        responses = [
            json.dumps(
                {
                    "thought": "open issues",
                    "action": "execute",
                    "segment_goal": "打开当前仓库的 Issues 列表页",
                    "segment_kind": "state_changing",
                    "stop_reason": "after_state_change",
                    "expected_outcome": {"type": "page_state_changed", "summary": "进入 Issues 列表页"},
                    "completion_check": {"url_not_same": True},
                    "code": "async def run(page):\n    await page.get_by_role('link', name='Issues 340').click()",
                }
            ),
            json.dumps(
                {
                    "thought": "rewrite with adaptive matching",
                    "action": "execute",
                    "segment_goal": "打开当前仓库的 Issues 列表页",
                    "segment_kind": "state_changing",
                    "stop_reason": "after_state_change",
                    "expected_outcome": {"type": "page_state_changed", "summary": "进入 Issues 列表页"},
                    "completion_check": {"url_not_same": True},
                    "code": "async def run(page):\n    await page.get_by_role('link', name='Issues').click()",
                }
            ),
            json.dumps({"thought": "done", "action": "done"}),
        ]
        executed_codes = []

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        async def fake_execute(_page, code):
            executed_codes.append(code)
            return {"success": True, "output": "ok", "error": None, "page_changed": True}

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(side_effect=lambda *_args, **_kwargs: snapshots.pop(0)),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=fake_execute,
        ):
            events = [event async for event in agent.run(
                session_id="segment-non-adaptive",
                page=page,
                goal="打开当前仓库的 Issues 列表页",
                existing_steps=[],
            )]

        recovering = [event for event in events if event["event"] == "segment_recovering"]
        self.assertTrue(any(event["data"]["error_code"] == "non_adaptive_code" for event in recovering))
        self.assertEqual(len(executed_codes), 1)
        self.assertNotIn("Issues 340", executed_codes[0])

    def test_find_non_adaptive_literal_ignores_numeric_sentinel_string(self):
        snapshot = {
            "url": "https://example.com/trending?page=-1",
            "title": "Trending",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }
        code = (
            "async def run(page):\n"
            "    best_score = '-1'\n"
            "    return {'output': best_score}\n"
        )

        literal = ASSISTANT_MODULE.RPAReActAgent._find_non_adaptive_literal(
            code,
            snapshot,
            "点击 stars 数最多的项目",
        )

        self.assertEqual(literal, "")

    @unittest.skip("legacy code/structured dual-mode events removed in unified segment mode")
    async def test_react_agent_rejects_javascript_code_before_exec_and_recovers(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        responses = [
            json.dumps(
                {
                    "thought": "need code mode for comparison",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "dynamic_selection",
                    "description": "Compare visible projects and open the highest-star repository",
                    "code": "const repoLinks = [...document.querySelectorAll('a[href]')];",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "rewrite it as Python",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "dynamic_selection",
                    "description": "Compare visible projects and open the highest-star repository",
                    "code": "async def run(page):\n    await page.wait_for_timeout(10)\n    return 'ok'",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
        ]
        executed_codes = []

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        async def fake_execute(_page, code):
            executed_codes.append(code)
            return {"success": True, "output": "ok", "error": None}

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=fake_execute,
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="Click the project with the most stars this week",
                existing_steps=[],
            ):
                events.append(event)

        self.assertEqual(len(executed_codes), 1)
        self.assertTrue(executed_codes[0].startswith("async def run(page):"))
        recovering = [event for event in events if event["event"] == "agent_recovering"]
        self.assertGreaterEqual(len(recovering), 1)
        self.assertEqual(recovering[0]["data"]["error_code"], "invalid_generated_code")

    @unittest.skip("legacy code/structured dual-mode events removed in unified segment mode")
    async def test_react_agent_normalizes_sync_run_function_before_code_execution(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        executed_codes = []
        responses = [
            json.dumps(
                {
                    "thought": "use code mode for a custom check",
                    "action": "execute",
                    "execution_mode": "code",
                    "upgrade_reason": "custom_logic",
                    "description": "Run a custom page check",
                    "code": "def run(page):\n    return 'ok'",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                }
            ),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        async def fake_execute(_page, code):
            executed_codes.append(code)
            return {"success": True, "output": "ok", "error": None}

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "_execute_on_page",
            new=fake_execute,
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="Run a custom page check",
                existing_steps=[],
            ):
                events.append(event)

        self.assertEqual(len(executed_codes), 1)
        self.assertTrue(executed_codes[0].startswith("async def run(page):"))
        step_done = next(event for event in events if event["event"] == "agent_step_committed")
        self.assertTrue(step_done["data"]["step"]["value"].startswith("async def run(page):"))

    def test_normalize_run_function_wraps_body_and_preserves_existing_async_definition(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()

        wrapped = agent._normalize_run_function('await page.get_by_role("button", name="Save").click()')
        already_wrapped = agent._normalize_run_function(
            "async def run(page):\n    await page.wait_for_timeout(500)"
        )

        self.assertTrue(wrapped.startswith("async def run(page):"))
        self.assertIn('await page.get_by_role("button", name="Save").click()', wrapped)
        self.assertEqual(
            already_wrapped,
            "async def run(page):\n    await page.wait_for_timeout(500)",
        )

    def test_normalize_run_function_converts_sync_run_playwright_calls_to_awaits(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()

        normalized = agent._normalize_run_function(
            'def run(page):\n    page.goto("https://example.com")'
        )

        self.assertEqual(
            normalized,
            'async def run(page):\n    await page.goto("https://example.com")',
        )

    def test_normalize_run_function_converts_locator_variable_actions_to_awaits(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()

        normalized = agent._normalize_run_function(
            '\n'.join([
                'def run(page):',
                '    save = page.get_by_role("button", name="Save")',
                '    save.click()',
                '    text = save.inner_text()',
            ])
        )

        self.assertIn('save = page.get_by_role("button", name="Save")', normalized)
        self.assertIn("await save.click()", normalized)
        self.assertIn("text = await save.inner_text()", normalized)
        self.assertNotIn('save = await page.get_by_role("button", name="Save")', normalized)

    def test_normalize_run_function_normalizes_already_async_wrapper_body(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()

        normalized = agent._normalize_run_function(
            '\n'.join([
                'async def run(page):',
                '    save = page.get_by_role("button", name="Save")',
                '    save.click()',
                '    text = save.inner_text()',
            ])
        )

        self.assertTrue(normalized.startswith("async def run(page):"))
        self.assertIn('save = page.get_by_role("button", name="Save")', normalized)
        self.assertIn("await save.click()", normalized)
        self.assertIn("text = await save.inner_text()", normalized)
        self.assertNotIn('save = await page.get_by_role("button", name="Save")', normalized)

    def test_normalize_run_function_awaits_playwright_calls_in_return_expressions(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()

        normalized = agent._normalize_run_function(
            'def run(page):\n    return page.locator("h1").inner_text()'
        )

        self.assertIn('return await page.locator("h1").inner_text()', normalized)

    def test_normalize_execution_mode_defaults_to_structured_and_infers_code_for_control_flow(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()

        self.assertEqual(
            agent._normalize_execution_mode(
                {"description": "Click the save button", "operation": "click"},
                "Click the save button",
            ),
            "structured",
        )
        self.assertEqual(
            agent._normalize_execution_mode(
                {"description": "Refresh until the status changes", "operation": "click"},
                "Refresh until the status changes",
            ),
            "code",
        )
        self.assertEqual(
            agent._normalize_execution_mode(
                {"execution_mode": "code", "description": "Click save"},
                "Click the save button",
            ),
            "code",
        )
        self.assertEqual(
            agent._normalize_execution_mode(
                {"description": "Open the project with the most stars this week"},
                "Click the project with the most stars this week",
            ),
            "code",
        )
        self.assertEqual(
            agent._normalize_execution_mode(
                {"description": "Open the latest issue"},
                "Get the latest issue title",
            ),
            "code",
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


class RPARoutePersistenceTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skip("legacy agent_step_committed event removed in unified segment mode")
    async def test_route_persists_only_agent_step_committed(self):
        request = SimpleNamespace(mode="react", message="Get the latest issue title")
        session = SimpleNamespace(
            steps=[],
            user_id="user-1",
        )
        fake_agent_events = [
            {"event": "agent_attempted", "data": {"description": "Click Issues tab"}},
            {"event": "agent_step_committed", "data": {"step": {"action": "navigate", "description": "Open Issues"}}},
            {"event": "agent_done", "data": {"total_steps": 1}},
        ]

        class _FakeAgent:
            async def run(self, **_kwargs):
                for event in fake_agent_events:
                    yield event

        add_step = AsyncMock()

        with patch.object(RPA_ROUTE_MODULE.rpa_manager, "get_session", new=AsyncMock(return_value=session)), patch.object(
            RPA_ROUTE_MODULE, "_resolve_user_model_config", new=AsyncMock(return_value={})
        ), patch.object(
            RPA_ROUTE_MODULE.rpa_manager, "get_page", return_value=_FakePage()
        ), patch.object(
            RPA_ROUTE_MODULE.rpa_manager, "pause_recording"
        ), patch.object(
            RPA_ROUTE_MODULE.rpa_manager, "resume_recording"
        ), patch.object(
            RPA_ROUTE_MODULE.rpa_manager, "add_step", new=add_step
        ), patch.dict(
            RPA_ROUTE_MODULE._active_agents, {"session-1": _FakeAgent()}
        ):
            response = await RPA_ROUTE_MODULE.chat_with_assistant("session-1", request, SimpleNamespace(id="user-1"))
            async for _chunk in response.body_iterator:
                pass

        add_step.assert_awaited_once_with("session-1", {"action": "navigate", "description": "Open Issues"})

    async def test_route_persists_only_segment_committed(self):
        request = SimpleNamespace(mode="segment", message="点击 stars 最多的项目")
        session = SimpleNamespace(
            steps=[],
            user_id="user-1",
        )
        fake_agent_events = [
            {"event": "segment_planned", "data": {"segment_goal": "动态比较 stars 并点击最高项"}},
            {"event": "segment_validation_failed", "data": {"segment_goal": "动态比较 stars 并点击最高项", "reason": "timeout"}},
            {"event": "segment_committed", "data": {"step": {"action": "ai_script", "description": "动态比较 stars 并点击最高项"}}},
            {"event": "recording_done", "data": {"total_steps": 1}},
        ]

        class _FakeAgent:
            async def run(self, **_kwargs):
                for event in fake_agent_events:
                    yield event

        add_step = AsyncMock()

        with patch.object(RPA_ROUTE_MODULE.rpa_manager, "get_session", new=AsyncMock(return_value=session)), patch.object(
            RPA_ROUTE_MODULE, "_resolve_user_model_config", new=AsyncMock(return_value={})
        ), patch.object(
            RPA_ROUTE_MODULE.rpa_manager, "get_page", return_value=_FakePage()
        ), patch.object(
            RPA_ROUTE_MODULE.rpa_manager, "pause_recording"
        ), patch.object(
            RPA_ROUTE_MODULE.rpa_manager, "resume_recording"
        ), patch.object(
            RPA_ROUTE_MODULE.rpa_manager, "add_step", new=add_step
        ), patch.dict(
            RPA_ROUTE_MODULE._active_agents, {"session-1": _FakeAgent()}
        ):
            response = await RPA_ROUTE_MODULE.chat_with_assistant("session-1", request, SimpleNamespace(id="user-1"))
            async for _chunk in response.body_iterator:
                pass

        add_step.assert_awaited_once_with("session-1", {"action": "ai_script", "description": "动态比较 stars 并点击最高项"})

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

    async def test_run_structured_intent_returns_target_not_found_failure_when_resolution_fails(self):
        with self.subTest("resolution_failure"):
            snapshot = {"frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []}
            intent = {
                "action": "click",
                "description": "Click the save button",
                "prompt": "Click the save button",
                "target_hint": {"role": "button", "name": "Save"},
            }

            result = await ASSISTANT_RUNTIME_MODULE.run_structured_intent(
                _FakeActionPage(),
                snapshot,
                intent,
            )

            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "No frame-aware target matched the structured intent")
            self.assertEqual(result["error_code"], "target_not_found")
            self.assertTrue(result["retryable"])
            self.assertEqual(result["output"], "")
            self.assertIsNone(result["step"])
            self.assertEqual(result["intent"], intent)
            self.assertIsNone(result["resolved_intent"])

        with self.subTest("legacy_direct_api_contract"):
            snapshot = {"frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []}
            intent = {
                "action": "click",
                "description": "Click the save button",
                "prompt": "Click the save button",
                "target_hint": {"role": "button", "name": "Save"},
            }

            with self.assertRaisesRegex(ValueError, "No frame-aware target matched the structured intent"):
                ASSISTANT_RUNTIME_MODULE.resolve_structured_intent(snapshot, intent)

            with self.assertRaisesRegex(ValueError, "No collection target matched"):
                ASSISTANT_RUNTIME_MODULE.resolve_collection_target(snapshot, intent)

            with self.assertRaisesRegex(ValueError, "No ordinal node candidates"):
                ASSISTANT_RUNTIME_MODULE._select_ordinal_node([], intent)

        with self.subTest("success_contract"):
            snapshot = {
                "frames": [],
                "actionable_nodes": [
                    {
                        "node_id": "save-1",
                        "frame_path": [],
                        "role": "button",
                        "name": "Save",
                        "action_kinds": ["click"],
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                        "locator_candidates": [
                            {
                                "kind": "role",
                                "selected": True,
                                "locator": {"method": "role", "role": "button", "name": "Save"},
                            }
                        ],
                        "validation": {"status": "ok"},
                        "hit_test_ok": True,
                        "is_visible": True,
                        "is_enabled": True,
                    }
                ],
                "content_nodes": [],
                "containers": [],
            }
            intent = {
                "action": "click",
                "description": "Click Save",
                "prompt": "Click Save",
                "target_hint": {"role": "button", "name": "Save"},
            }

            result = await ASSISTANT_RUNTIME_MODULE.run_structured_intent(
                _FakeActionPage(),
                snapshot,
                intent,
            )

            self.assertTrue(result["success"])
            self.assertEqual(result["error"], "")
            self.assertEqual(result["error_code"], "")
            self.assertFalse(result["retryable"])
            self.assertEqual(result["output"], "ok")
            self.assertEqual(result["intent"], intent)
            self.assertEqual(result["resolved_intent"]["resolved"]["locator"]["name"], "Save")
            self.assertEqual(result["step"]["action"], "click")

    async def test_run_structured_intent_classifies_timeout_as_retryable_execution_timeout(self):
        with self.subTest("resolution_failure_with_extra_context"):
            self.assertEqual(
                ASSISTANT_RUNTIME_MODULE._classify_structured_intent_error(
                    ValueError("No frame-aware target matched the structured intent while resolving click target"),
                    action="click",
                ),
                ("target_not_found", True),
            )

        click_snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "save-1",
                    "frame_path": [],
                    "role": "button",
                    "name": "Save",
                    "action_kinds": ["click"],
                    "locator": {"method": "role", "role": "button", "name": "Save"},
                    "locator_candidates": [{"kind": "role", "selected": True, "locator": {"method": "role", "role": "button", "name": "Save"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                }
            ],
            "content_nodes": [],
            "containers": [],
        }
        click_intent = {
            "action": "click",
            "description": "Click Save",
            "prompt": "Click Save",
            "target_hint": {"role": "button", "name": "Save"},
        }

        cases = [
            (
                "timeout",
                _FailingActionPage(RuntimeError("Timeout 30000ms exceeded while waiting for click")),
                click_snapshot,
                click_intent,
                "execution_timeout",
                True,
                "Save",
            ),
            (
                "detached_frame",
                _FailingActionPage(RuntimeError("Frame has been detached during click")),
                click_snapshot,
                click_intent,
                "page_changed",
                True,
                "Save",
            ),
            (
                "navigation_timeout_without_navigation_keyword",
                _FailingNavigationPage(RuntimeError("Timeout 30000ms exceeded"), phase="load_state"),
                {"frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
                {
                    "action": "navigate",
                    "description": "Open GitHub Trending",
                    "prompt": "Open GitHub Trending",
                    "value": "https://github.com/trending",
                },
                "navigation_timeout",
                True,
                "https://github.com/trending",
            ),
            (
                "unexpected_runtime_fallback",
                _FailingActionPage(RuntimeError("socket blew up")),
                click_snapshot,
                click_intent,
                "unexpected_runtime_error",
                False,
                "Save",
            ),
        ]
        for label, page, snapshot, intent, expected_code, expected_retryable, expected_target in cases:
            with self.subTest(label):
                result = await ASSISTANT_RUNTIME_MODULE.run_structured_intent(
                    page,
                    snapshot,
                    intent,
                )

                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], expected_code)
                self.assertEqual(result["retryable"], expected_retryable)
                self.assertEqual(result["output"], "")
                self.assertIsNone(result["step"])
                self.assertEqual(result["intent"], intent)
                resolved = result["resolved_intent"]["resolved"]
                if resolved.get("locator"):
                    self.assertEqual(resolved["locator"]["name"], expected_target)
                else:
                    self.assertEqual(resolved["url"], expected_target)

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

    def test_react_system_prompt_requires_segment_completion_before_done(self):
        prompt = ASSISTANT_MODULE.REACT_SYSTEM_PROMPT

        self.assertIn("Plan exactly one ai_script segment at a time.", prompt)
        self.assertIn('"segment_goal": "what this segment will achieve on the current page"', prompt)
        self.assertIn('Do not split a dynamic task into "extract a concrete name first, then click that fixed name later".', prompt)
        self.assertIn(
            "For action goals such as click, open, enter, download, or submit, do not claim success with a read-only segment that only extracts text.",
            prompt,
        )
        self.assertIn("Only output action=done when the user goal is actually complete after the previous segment.", prompt)
        self.assertIn("For read/extraction goals, do not output action=done until a prior read-only segment has returned the requested value in output.", prompt)
        self.assertIn("For read-only extraction segments, return the exact visible value from the page in output or a named field.", prompt)
        self.assertIn("Do not use selected_target_key/page_contains_selected_target for read-only extraction unless you need an explicit post-segment visibility check.", prompt)
        self.assertIn("Use the current page snapshot to derive adaptive locators and comparisons.", prompt)
        self.assertIn("Do not hard-code volatile values observed on the page or in previous segments", prompt)


class RPASegmentValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_segment_does_not_force_page_changed_for_state_changing_segment(self):
        spec = SEGMENT_MODELS_MODULE.SegmentSpec(
            segment_goal="点击第一个项目",
            segment_kind="state_changing",
            stop_reason="after_state_change",
            expected_outcome={"type": "page_state_changed", "summary": "打开项目"},
            completion_check={"url_not_same": True},
            code="async def run(page):\n    return {'page_changed': False}",
        )
        snapshots = [
            {"url": "https://github.com/trending", "title": "Trending", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
            {"url": "https://github.com/trending", "title": "Trending", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
        ]

        result = await ASSISTANT_MODULE.run_segment(
            page=_FakePage(),
            spec=spec,
            executor=AsyncMock(return_value={"success": True, "output": "ok", "page_changed": False}),
            snapshot_builder=AsyncMock(side_effect=snapshots),
        )

        self.assertFalse(result.page_changed)

    async def test_run_segment_detects_delayed_page_changed_during_short_reobserve(self):
        spec = SEGMENT_MODELS_MODULE.SegmentSpec(
            segment_goal="点击第一个项目",
            segment_kind="state_changing",
            stop_reason="after_state_change",
            expected_outcome={"type": "page_state_changed", "summary": "打开项目"},
            completion_check={"url_not_same": True},
            code="async def run(page):\n    return {'page_changed': False}",
        )
        page = _FakeActionPage()
        snapshots = [
            {"url": "https://github.com/trending", "title": "Trending", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
            {"url": "https://github.com/trending", "title": "Trending", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
            {"url": "https://github.com/owner/repo", "title": "owner/repo", "frames": [], "actionable_nodes": [], "content_nodes": [], "containers": []},
        ]

        result = await ASSISTANT_MODULE.run_segment(
            page=page,
            spec=spec,
            executor=AsyncMock(return_value={"success": True, "output": "ok", "page_changed": False}),
            snapshot_builder=AsyncMock(side_effect=snapshots),
        )

        self.assertTrue(result.page_changed)

    async def test_validate_segment_result_accepts_selected_repo_after_navigation_when_href_matches_snapshot(self):
        spec = SEGMENT_MODELS_MODULE.SegmentSpec(
            segment_goal="在当前 Trending 列表中找出 stars 数最多的项目并点击打开其仓库页面",
            segment_kind="state_changing",
            stop_reason="after_state_change",
            expected_outcome={"type": "page_state_changed", "summary": "进入所选仓库页面"},
            completion_check={
                "url_not_same": True,
                "selected_target_key": "selected_repo_name",
                "page_contains_selected_target": True,
            },
            code="async def run(page):\n    return {}",
        )
        run_result = SEGMENT_MODELS_MODULE.SegmentRunResult(
            success=True,
            page_changed=True,
            selected_artifacts={
                "selected_repo_name": "microsoft / markitdown",
                "selected_repo_href": "/microsoft/markitdown",
                "selected_repo_stars": 109222,
            },
            after_snapshot={
                "url": "https://github.com/microsoft/markitdown",
                "title": "GitHub - microsoft/markitdown: Python tool for converting files and office documents to Markdown. · GitHub",
            },
        )

        result = await SEGMENT_VALIDATOR_MODULE.validate_segment_result(
            goal="点击 stars 数最多的项目",
            spec=spec,
            run_result=run_result,
        )

        self.assertTrue(result.passed)
        self.assertTrue(result.goal_completed)

    async def test_validate_segment_result_does_not_complete_read_goal_after_navigation_only(self):
        spec = SEGMENT_MODELS_MODULE.SegmentSpec(
            segment_goal="打开当前仓库的 Issues 页面，为读取最近一条 issue 标题做准备",
            segment_kind="state_changing",
            stop_reason="after_state_change",
            expected_outcome={"type": "page_state_changed", "summary": "进入 Issues 列表页"},
            completion_check={"url_not_same": True},
            code="async def run(page):\n    return {}",
        )
        run_result = SEGMENT_MODELS_MODULE.SegmentRunResult(
            success=True,
            output="ok",
            page_changed=True,
            after_snapshot={
                "url": "https://github.com/public-apis/public-apis/issues",
                "title": "Issues · public-apis/public-apis · GitHub",
                "frames": [],
                "actionable_nodes": [],
                "content_nodes": [],
                "containers": [],
            },
        )

        result = await SEGMENT_VALIDATOR_MODULE.validate_segment_result(
            goal="获取最近一条 issues 的标题",
            spec=spec,
            run_result=run_result,
        )

        self.assertTrue(result.passed)
        self.assertFalse(result.goal_completed)

    async def test_validate_segment_result_accepts_read_only_visible_text_from_snapshot_body(self):
        spec = SEGMENT_MODELS_MODULE.SegmentSpec(
            segment_goal="提取当前 Issues 列表中第一个 issue 条目的标题",
            segment_kind="read_only",
            stop_reason="goal_reached",
            expected_outcome={"type": "observation", "summary": "返回第一个 issue 的标题"},
            completion_check={
                "selected_target_key": "latest_issue_title",
                "page_contains_selected_target": True,
            },
            code="async def run(page):\n    return {}",
        )
        run_result = SEGMENT_MODELS_MODULE.SegmentRunResult(
            success=True,
            output="Bug: visible issue title",
            selected_artifacts={
                "latest_issue_title": "Bug: visible issue title",
            },
            after_snapshot={
                "url": "https://github.com/public-apis/public-apis/issues",
                "title": "Issues · public-apis/public-apis · GitHub",
                "frames": [
                    {
                        "frame_hint": "main document",
                        "frame_path": [],
                        "elements": [
                            {
                                "index": 1,
                                "tag": "a",
                                "role": "link",
                                "name": "Bug: visible issue title",
                                "href": "/public-apis/public-apis/issues/1000",
                            }
                        ],
                        "collections": [],
                    }
                ],
                "actionable_nodes": [
                    {"name": "Bug: visible issue title"},
                ],
                "content_nodes": [
                    {"text": "Bug: visible issue title"},
                ],
                "containers": [],
            },
        )

        result = await SEGMENT_VALIDATOR_MODULE.validate_segment_result(
            goal="获取第一个 issues 的标题",
            spec=spec,
            run_result=run_result,
        )

        self.assertTrue(result.passed)
        self.assertTrue(result.goal_completed)


class RPAAssistantExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_single_response_normalizes_code_before_execution(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        executed_codes = []

        async def fake_execute(_page, code):
            executed_codes.append(code)
            return {"success": True, "output": "ok", "error": None}

        with patch.object(
            assistant,
            "_execute_on_page",
            new=fake_execute,
        ):
            result, code, resolution = await assistant._execute_single_response(
                page,
                snapshot,
                '```python\n'
                'def run(page):\n'
                '    save = page.get_by_role("button", name="Save")\n'
                '    save.click()\n'
                '```',
            )

        self.assertTrue(result["success"])
        self.assertIsNone(resolution)
        self.assertEqual(len(executed_codes), 1)
        self.assertEqual(code, executed_codes[0])
        self.assertTrue(code.startswith("async def run(page):"))
        self.assertIn("await save.click()", code)


if __name__ == "__main__":
    unittest.main()
