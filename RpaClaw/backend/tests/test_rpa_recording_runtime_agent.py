import asyncio
import importlib
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

import backend.rpa.recording_runtime_agent as recording_runtime_agent
from backend.rpa.recording_runtime_agent import (
    RecordingRuntimeAgent,
    RECORDING_RUNTIME_SYSTEM_PROMPT,
    _classify_recording_failure,
    _build_detail_extract_plan,
    _combine_run_python_attempts,
    _ensure_expected_effect,
    _expected_effect,
    _instruction_is_detail_extract_only,
    _merge_runtime_ai_signal,
    _normalize_generated_playwright_code,
    _parse_json_object,
    _resolve_recording_snapshot_debug_dir,
    _resolve_recording_snapshot_debug_path,
    _snapshot_plan_fields,
)
from backend.rpa.trace_skill_compiler import TraceSkillCompiler
from backend.rpa.trace_models import RPAAcceptedTrace, RPAAIExecution, RPAPageState, RPATraceType
from backend.rpa.recording_terminal_recovery import recover_failed_side_effect_from_snapshot_diff
from backend.rpa.recording_effects import _filled_value_conflicts_with_source_output
from backend.rpa.recording_verifier import verify_terminal_contract


class _FakePage:
    url = "https://example.test/start"

    def __init__(self):
        self._event_handlers = {}

    async def title(self):
        return "Example"

    def locator(self, selector):
        if "input" in str(selector):
            return _FakeEditableLocator()
        return _FakeLocator()

    async def goto(self, url, wait_until=None):
        self.url = url

    async def wait_for_load_state(self, _state):
        return None

    def on(self, event, handler):
        self._event_handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        handlers = self._event_handlers.get(event) or []
        self._event_handlers[event] = [item for item in handlers if item is not handler]

    async def trigger_download(self, filename):
        download = SimpleNamespace(suggested_filename=filename)
        for handler in list(self._event_handlers.get("download") or []):
            result = handler(download)
            if hasattr(result, "__await__"):
                await result

    def trigger_download_later(self, filename, delay=0.05):
        async def emit():
            await asyncio.sleep(delay)
            await self.trigger_download(filename)

        asyncio.create_task(emit())


class _FakeLocator:
    @property
    def first(self):
        return self

    def locator(self, _selector):
        return self

    def nth(self, _index):
        return self

    async def count(self):
        return 0

    async def is_visible(self):
        return False

    async def is_enabled(self):
        return False

    async def wait_for(self, *args, **kwargs):
        return None

    async def inner_text(self, *args, **kwargs):
        return ""

    async def click(self):
        return None

    async def fill(self, _value):
        return None


class _FakeEditableLocator(_FakeLocator):
    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return True

    async def evaluate(self, script, *args, **kwargs):
        text = str(script or "")
        if "tagName" in text and "toLowerCase" in text:
            return "input"
        if "getAttribute('role')" in text or "getAttribute(\"role\")" in text:
            return False
        return True

    async def fill(self, _value, *args, **kwargs):
        return None


class _FakeModalClosedPage(_FakePage):
    def locator(self, selector):
        if selector == "body":
            return _FakeTextLocator("Saved successfully")
        return _FakeLocator()


class _FakeVisibleDialogPage(_FakePage):
    def locator(self, selector):
        if selector in {"[role='dialog']", "[aria-modal='true']"}:
            return _FakeVisibleLocator()
        if selector == "body":
            return _FakeTextLocator("")
        return _FakeLocator()


class _FakeTextLocator(_FakeLocator):
    def __init__(self, text):
        self._text = text

    async def inner_text(self, *args, **kwargs):
        return self._text


class _FakeVisibleLocator(_FakeLocator):
    async def count(self):
        return 1

    async def is_visible(self):
        return True


class _FakeListPage(_FakePage):
    def __init__(self):
        self.url = "https://github.com/trending"
        self.clicked = []
        self._selectors = {
            "h2.lh-condensed a": ["alpha / one", "beta / two", "gamma / three"],
            "a.download-link": ["Download", "Download", "Download"],
        }

    def locator(self, selector):
        return _FakeListLocator(self, selector, self._selectors.get(selector, []))


class _FakeListLocator:
    def __init__(self, page, selector, values, index=None):
        self.page = page
        self.selector = selector
        self.values = values
        self.index = index

    def nth(self, index):
        return _FakeListLocator(self.page, self.selector, self.values, index)

    async def count(self):
        return len(self.values)

    async def inner_text(self):
        return self.values[self.index or 0]

    async def click(self):
        self.page.clicked.append((self.selector, self.index or 0))
        if self.selector == "h2.lh-condensed a":
            self.page.url = f"https://github.com/{self.values[self.index or 0].replace(' / ', '/')}"


class _FakeNavigatedPage(_FakePage):
    url = "https://github.com/HKUDS/RAG-Anything"

    async def title(self):
        return "GitHub - HKUDS/RAG-Anything"


@pytest.fixture(autouse=True)
def _disable_recording_snapshot_debug_by_default(monkeypatch):
    monkeypatch.delenv("RPA_RECORDING_DEBUG_SNAPSHOT_DIR", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "backend.config",
        SimpleNamespace(settings=SimpleNamespace(rpa_recording_debug_snapshot_dir="")),
    )


def _find_region_with_pair(snapshot, label, value):
    for region in snapshot.get("expanded_regions") or []:
        if region.get("kind") != "label_value_group":
            continue
        for pair in (region.get("evidence") or {}).get("pairs") or []:
            if pair.get("label") == label and pair.get("value") == value:
                return region
    return None


def _required_terminal_contract(*evidence_types, kind="state_change", allow_semantic_judge=False):
    return {
        "required": True,
        "kind": kind,
        "success_evidence": [{"type": evidence_type} for evidence_type in evidence_types],
        "allow_semantic_judge": allow_semantic_judge,
    }


def _ordinal_snapshot():
    containers = []
    actionable_nodes = []
    repos = ["alpha / one", "beta / two", "gamma / three"]
    for index, repo in enumerate(repos):
        container_id = f"repo-{index}"
        containers.append(
            {
                "container_id": container_id,
                "container_kind": "card_group",
                "name": repo,
                "bbox": {"x": 10, "y": 100 + index * 90, "width": 800, "height": 80},
            }
        )
        actionable_nodes.append(
            {
                "node_id": f"title-{index}",
                "container_id": container_id,
                "role": "link",
                "name": repo,
                "text": repo,
                "href": f"/{repo.replace(' / ', '/')}",
                "collection_container_selector": "article",
                "collection_item_selector": "h2.lh-condensed a",
                "collection_item_count": len(repos),
            }
        )
        actionable_nodes.append(
            {
                "node_id": f"download-{index}",
                "container_id": container_id,
                "role": "link",
                "name": "Download",
                "text": "Download",
                "href": f"/{repo.replace(' / ', '/')}/archive.zip",
                "collection_container_selector": "article",
                "collection_item_selector": "a.download-link",
                "collection_item_count": len(repos),
            }
        )
    return {
        "url": "https://github.com/trending",
        "title": "Trending repositories",
        "frames": [],
        "content_nodes": [],
        "containers": containers,
        "actionable_nodes": actionable_nodes,
    }


def _ordinal_frame_collection_snapshot():
    return {
        "url": "https://github.com/trending",
        "title": "Trending repositories",
        "actionable_nodes": [],
        "content_nodes": [],
        "containers": [],
        "frames": [
            {
                "frame_path": [],
                "frame_hint": "main document",
                "elements": [],
                "collections": [
                    {
                        "kind": "repeated_items",
                        "item_count": 5,
                        "container_hint": {"locator": {"method": "css", "value": "li"}},
                        "item_hint": {
                            "locator": {"method": "css", "value": "button.js-details-target"},
                            "role": "button",
                        },
                        "items": [
                            {"index": 3, "tag": "button", "role": "button", "name": "Platform"},
                            {"index": 4, "tag": "button", "role": "button", "name": "Solutions"},
                            {"index": 5, "tag": "button", "role": "button", "name": "Resources"},
                            {"index": 6, "tag": "button", "role": "button", "name": "Open Source"},
                            {"index": 7, "tag": "button", "role": "button", "name": "Enterprise"},
                        ],
                    },
                    {
                        "kind": "repeated_items",
                        "item_count": 12,
                        "container_hint": {
                            "locator": {
                                "method": "css",
                                "value": "div.position-relative.container-lg div div article div",
                            }
                        },
                        "item_hint": {"locator": {"method": "css", "value": "a"}, "role": "link"},
                        "items": [
                            {"index": 26, "tag": "a", "role": "link", "name": "7,684"},
                            {"index": 27, "tag": "a", "role": "link", "name": "1,199"},
                            {"index": 35, "tag": "a", "role": "link", "name": "4,864"},
                            {"index": 36, "tag": "a", "role": "link", "name": "402"},
                        ],
                    },
                    {
                        "kind": "repeated_items",
                        "item_count": 12,
                        "container_hint": {
                            "locator": {
                                "method": "css",
                                "value": "div.position-relative.container-lg div div article",
                            }
                        },
                        "item_hint": {
                            "locator": {"method": "css", "value": "h2.lh-condensed a"},
                            "role": "link",
                        },
                        "items": [
                            {"index": 25, "tag": "a", "role": "link", "name": "Alishahryar1 / free-claude-code"},
                            {"index": 34, "tag": "a", "role": "link", "name": "huggingface / ml-intern"},
                            {"index": 42, "tag": "a", "role": "link", "name": "google / osv-scanner"},
                        ],
                    },
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_run_python_fill_accepts_action_evidence_from_output():
    page = _FakePage()
    result = await _ensure_expected_effect(
        page=page,
        instruction="fill the previous title into the PR summary field",
        plan={"action_type": "run_python", "expected_effect": "fill"},
        result={
            "success": True,
            "output": {
                "action_performed": True,
                "action_type": "fill",
                "filled_value": "Example",
            },
        },
        before=RPAPageState(url=page.url, title="Example"),
    )

    assert result["success"] is True
    assert result["effect"]["action_performed"] is True
    assert result["effect"]["type"] == "fill"


def test_ordinal_overlay_builds_relative_first_item_name_plan():
    build_plan = getattr(recording_runtime_agent, "_build_ordinal_overlay_plan")

    plan = build_plan("get the first project name", _ordinal_snapshot())

    assert plan is not None
    assert plan["expected_effect"] == "extract"
    assert "page.locator('h2.lh-condensed a').nth(0)" in plan["code"]
    assert "alpha / one" not in plan["code"]


def test_ordinal_overlay_builds_first_n_names_plan():
    build_plan = getattr(recording_runtime_agent, "_build_ordinal_overlay_plan")

    plan = build_plan("get the first 2 project names", _ordinal_snapshot())

    assert plan is not None
    assert plan["expected_effect"] == "extract"
    assert "_limit = min(2, await _items.count())" in plan["code"]
    assert "return _result" in plan["code"]


def test_ordinal_overlay_uses_frame_collection_when_actionable_nodes_are_unannotated():
    build_plan = getattr(recording_runtime_agent, "_build_ordinal_overlay_plan")

    plan = build_plan("获取第一个项目的名称", _ordinal_frame_collection_snapshot())

    assert plan is not None
    assert "page.locator('h2.lh-condensed a').nth(0)" in plan["code"]
    assert "Alishahryar1 / free-claude-code" not in plan["code"]


def test_ordinal_overlay_builds_second_download_plan():
    build_plan = getattr(recording_runtime_agent, "_build_ordinal_overlay_plan")

    plan = build_plan("点击第二项名字进行下载", _ordinal_snapshot())

    assert plan is not None
    assert plan["expected_effect"] == "none"
    assert "page.locator('a.download-link').nth(1).click()" in plan["code"]
    assert "beta / two" not in plan["code"]


def test_ordinal_overlay_falls_back_for_identical_action_only_collection():
    build_plan = getattr(recording_runtime_agent, "_build_ordinal_overlay_plan")
    snapshot = _ordinal_snapshot()
    snapshot["actionable_nodes"] = [
        node for node in snapshot["actionable_nodes"] if str(node.get("node_id", "")).startswith("download-")
    ]

    plan = build_plan("click the first item", snapshot)

    assert plan is None


def test_ordinal_overlay_falls_back_for_semantic_selection():
    build_plan = getattr(recording_runtime_agent, "_build_ordinal_overlay_plan")

    plan = build_plan("open the project most related to python", _ordinal_snapshot())

    assert plan is None


def _table_view_snapshot():
    return {
        "url": "https://example.test/grid",
        "title": "Grid",
        "frames": [],
        "actionable_nodes": [],
        "content_nodes": [],
        "containers": [],
        "table_views": [
            {
                "kind": "table_view",
                "framework_hint": "structured-grid",
                "columns": [
                    {"index": 0, "column_id": "col_23", "header": "", "role": "row_index"},
                    {"index": 1, "column_id": "col_24", "header": "", "role": "selection"},
                    {"index": 2, "column_id": "col_25", "header": "文件名称", "role": "file_link"},
                    {"index": 3, "column_id": "col_28", "header": "导出状态", "role": "status"},
                ],
                "rows": [
                    {
                        "index": 0,
                        "cells": [
                            {
                                "column_id": "col_25",
                                "column_index": 2,
                                "column_header": "文件名称",
                                "text": "File_189.xlsx",
                                "actions": [
                                    {
                                        "kind": "link",
                                        "label": "File_189.xlsx",
                                        "locator": {
                                            "method": "relative_css",
                                            "scope": "row",
                                            "value": "td[data-colid='col_25'] a",
                                        },
                                    }
                                ],
                            },
                            {"column_id": "col_28", "column_index": 3, "column_header": "导出状态", "text": "FINISH", "actions": []},
                        ],
                        "locator_hints": [{"kind": "playwright", "expression": "page.locator('table[data-role=\"grid-body\"] tbody tr').nth(0)"}],
                    },
                    {
                        "index": 1,
                        "cells": [
                            {
                                "column_id": "col_25",
                                "column_index": 2,
                                "column_header": "文件名称",
                                "text": "File_380.xlsx",
                                "actions": [
                                    {
                                        "kind": "link",
                                        "label": "File_380.xlsx",
                                        "locator": {
                                            "method": "relative_css",
                                            "scope": "row",
                                            "value": "td[data-colid='col_25'] a",
                                        },
                                    }
                                ],
                            },
                            {"column_id": "col_28", "column_index": 3, "column_header": "导出状态", "text": "FINISH", "actions": []},
                        ],
                        "locator_hints": [{"kind": "playwright", "expression": "page.locator('table[data-role=\"grid-body\"] tbody tr').nth(1)"}],
                    },
                ],
            }
        ],
        "detail_views": [],
    }


def test_table_ordinal_lane_clicks_first_row_named_column_link():
    build_plan = getattr(recording_runtime_agent, "_build_table_ordinal_overlay_plan")

    plan = build_plan("点击第一行的文件名称", _table_view_snapshot())

    assert plan is not None
    assert plan["table_ordinal_overlay"] is True
    assert "table[data-role=\"grid-body\"] tbody tr" in plan["code"]
    assert "td[data-colid='col_25'] a" in plan["code"]
    assert "File_189.xlsx" not in plan["code"]


def test_table_ordinal_lane_extracts_second_row_status():
    build_plan = getattr(recording_runtime_agent, "_build_table_ordinal_overlay_plan")

    plan = build_plan("提取第二行的导出状态", _table_view_snapshot())

    assert plan is not None
    assert "nth(1)" in plan["code"]
    assert "td[data-colid='col_28']" in plan["code"]
    assert plan["expected_effect"] == "extract"


def test_table_ordinal_lane_falls_back_without_column_match():
    build_plan = getattr(recording_runtime_agent, "_build_table_ordinal_overlay_plan")

    plan = build_plan("点击第一行的审批按钮", _table_view_snapshot())

    assert plan is None


def _named_multi_table_view_snapshot():
    snapshot = _table_view_snapshot()
    edm_table = snapshot["table_views"][0]
    edm_table["title"] = "EDM Request"
    edm_table["title_source"] = "nearest_preceding_heading"
    edm_table["nearby_headings"] = ["EDM Request"]
    edm_table["columns"][2]["column_id"] = "col_2"
    edm_table["columns"][2]["header"] = "File Name"
    edm_table["columns"][3]["column_id"] = "col_3"
    edm_table["columns"][3]["header"] = "Export Status"
    edm_table["rows"][0]["cells"][0]["column_id"] = "col_2"
    edm_table["rows"][0]["cells"][0]["column_header"] = "File Name"
    edm_table["rows"][0]["cells"][0]["text"] = "EquipmentConfigurationLevelSplitDataSheet_17728130.xlsx"
    edm_table["rows"][0]["cells"][0]["actions"][0]["locator"]["value"] = 'td[data-colid="col_2"] a'
    edm_table["rows"][0]["locator_hints"] = [{"kind": "playwright", "expression": "page.locator('tbody tr').nth(0)"}]
    edm_table["rows"][1]["cells"][0]["column_id"] = "col_2"
    edm_table["rows"][1]["cells"][0]["column_header"] = "File Name"
    edm_table["rows"][1]["locator_hints"] = [{"kind": "playwright", "expression": "page.locator('tbody tr').nth(1)"}]
    jalor_table = {
        **edm_table,
        "title": "Jalor Request",
        "nearby_headings": ["Jalor Request"],
    }
    snapshot["table_views"] = [jalor_table, edm_table]
    snapshot["actionable_nodes"] = [
        {
            "role": "link",
            "name": "Home",
            "text": "Home",
            "collection_item_selector": "div a",
            "collection_item_count": 6,
        },
        {
            "role": "link",
            "name": "Request",
            "text": "Request",
            "collection_item_selector": "div a",
            "collection_item_count": 6,
        },
    ]
    return snapshot


def test_table_ordinal_lane_scopes_named_table_without_observed_row_text():
    build_plan = getattr(recording_runtime_agent, "_build_table_ordinal_overlay_plan")

    plan = build_plan("获取EDM Request表格中第一行的File Name", _named_multi_table_view_snapshot())

    assert plan is not None
    assert plan["table_ordinal_overlay"] is True
    assert "get_by_text('EDM Request', exact=True)" in plan["code"]
    assert "following::table" in plan["code"]
    assert "col_2" in plan["code"]
    assert "div a" not in plan["code"]
    assert "EquipmentConfigurationLevelSplitDataSheet_17728130.xlsx" not in plan["code"]


def test_table_ordinal_lane_extracts_first_n_rows_as_headered_records():
    build_plan = getattr(recording_runtime_agent, "_build_table_ordinal_overlay_plan")

    plan = build_plan("获取EDM Request表格中前三行的信息", _named_multi_table_view_snapshot())

    assert plan is not None
    assert plan["table_ordinal_overlay"] is True
    assert plan["expected_effect"] == "extract"
    assert "get_by_text('EDM Request', exact=True)" in plan["code"]
    assert "_limit = min(3, await _rows.count())" in plan["code"]
    assert "'File Name'" in plan["code"]
    assert "'Export Status'" in plan["code"]


@pytest.mark.asyncio
async def test_recording_runtime_agent_uses_ordinal_overlay_without_planner(monkeypatch):
    async def fake_build_page_snapshot(*_args, **_kwargs):
        return _ordinal_snapshot()

    async def planner(_payload):
        raise AssertionError("planner should not be called for high-confidence ordinal tasks")

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    page = _FakeListPage()
    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="get the first project name",
        runtime_results={},
    )

    assert result.success is True
    assert result.output == "alpha / one"
    assert "page.locator('h2.lh-condensed a').nth(0)" in result.trace.ai_execution.code
    assert "alpha / one" not in result.trace.ai_execution.code


def test_backend_rpa_package_import_is_lazy():
    module = importlib.import_module("backend.rpa")

    assert "rpa_manager" not in module.__dict__
    assert "RPASession" not in module.__dict__
    assert "RPAStep" not in module.__dict__
    assert "cdp_connector" not in module.__dict__
    assert module.__all__ == ["rpa_manager", "RPASession", "RPAStep", "cdp_connector"]


def test_recording_runtime_agent_module_import_does_not_require_llm_stack(monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "rpa" / "recording_runtime_agent.py"
    blocked_modules = [
        "langchain_core",
        "langchain_core.messages",
        "backend.deepagent",
        "backend.deepagent.engine",
    ]
    for name in blocked_modules:
        monkeypatch.setitem(sys.modules, name, None)

    spec = importlib.util.spec_from_file_location(
        "backend.rpa.recording_runtime_agent_lazy_import_test",
        module_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "RecordingRuntimeAgent")


def test_recording_runtime_prompt_defines_result_return_contract():
    assert "`results` 是普通 Python dict" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "只能通过 `return`" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "禁止调用 `results.set(...)`" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "`output_key` 只是给后置 trace compiler 使用的元数据" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "internal_ref" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "不是 DOM id、CSS selector 或 Playwright locator" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "locator_hints" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "action_performed" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "filled_value" in RECORDING_RUNTIME_SYSTEM_PROMPT


def test_recording_runtime_prompt_prefers_structured_snapshot_views():
    assert "extract_snapshot" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "table_views" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "detail_views" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "form_views" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "row-relative" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "column-relative" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "Do not turn summary text into placeholder" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "Do not use observed row text as the primary selector when the instruction is ordinal" in RECORDING_RUNTIME_SYSTEM_PROMPT


def test_recording_runtime_prompt_does_not_advertise_table_snapshot_extracts():
    assert "snapshot.detail_views or snapshot.table_views" not in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "fields/rows" not in RECORDING_RUNTIME_SYSTEM_PROMPT


def test_recording_runtime_prompt_requires_terminal_business_evidence():
    assert "business-visible terminal condition" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "do not unconditionally add a new row" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "scope field locators to the dialog/form container" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "structured snapshot.detail_views fields as the source of truth" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "Do not substitute current user/menu/role text" in RECORDING_RUNTIME_SYSTEM_PROMPT


def test_recording_runtime_prompt_uses_bounded_waits_and_avoids_callable_locator_names():
    assert "short bounded waits" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "Do not pass Python lambda" in RECORDING_RUNTIME_SYSTEM_PROMPT


def test_recording_runtime_prompt_includes_replay_metadata_contract():
    assert "input_bindings" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "output_bindings" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "postcondition" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "terminal_contract" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "semantic_intent must be \"semantic_candidate_selection\"" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "in-page filter/search forms" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "intercepts pointer events" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "Do not click unnamed increment/decrement controls repeatedly" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "open a specific record" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "Do not return `downloaded: false`" in RECORDING_RUNTIME_SYSTEM_PROMPT


def test_recording_snapshot_debug_dir_falls_back_to_backend_settings(monkeypatch):
    monkeypatch.delenv("RPA_RECORDING_DEBUG_SNAPSHOT_DIR", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "backend.config",
        SimpleNamespace(settings=SimpleNamespace(rpa_recording_debug_snapshot_dir="data/from-settings")),
    )

    assert _resolve_recording_snapshot_debug_dir() == "data/from-settings"


def test_recording_snapshot_debug_path_resolves_relative_path_from_project_root():
    resolved = _resolve_recording_snapshot_debug_path("data/rpa_recording_snapshots")

    assert resolved == Path(__file__).resolve().parents[3] / "data" / "rpa_recording_snapshots"


@pytest.mark.asyncio
async def test_recording_runtime_agent_accepts_successful_python_plan():
    plans = [
        {
            "description": "Extract title",
            "action_type": "run_python",
            "output_key": "page_title",
            "code": "async def run(page, results):\n    return {'title': await page.title()}",
        }
    ]

    async def planner(_payload):
        return plans.pop(0)

    agent = RecordingRuntimeAgent(planner=planner)
    result = await agent.run(page=_FakePage(), instruction="extract title", runtime_results={})

    assert result.success is True
    assert result.trace.output_key == "page_title"
    assert result.trace.output == {"title": "Example"}
    assert result.trace.ai_execution.repair_attempted is False

def test_recording_runtime_agent_persists_runtime_ai_preserve_signal():
    async def planner(_payload):
        return {
            "description": "Select the closest matching project",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "selected_project",
            "preserve_runtime_ai": True,
            "semantic_intent": "select_best_matching_candidate",
            "code": (
                "async def run(page, results):\n"
                "    return {'target': 'alpha', 'url': 'https://example.test/projects/alpha'}"
            ),
        }

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="open the closest matching project",
            runtime_results={},
        )
    )

    assert result.success is True
    assert result.trace.signals["runtime_ai"]["preserve"] is True
    assert result.trace.signals["runtime_ai"]["reason"] == "select_best_matching_candidate"


def test_recording_runtime_agent_does_not_preserve_mutating_runtime_ai_plan():
    async def planner(_payload):
        return {
            "description": "Select the closest matching project",
            "action_type": "run_python",
            "expected_effect": "click",
            "output_key": "selected_project",
            "preserve_runtime_ai": True,
            "semantic_intent": "select_best_matching_candidate",
            "code": (
                "async def run(page, results):\n"
                "    await page.locator('a.project').nth(0).click()\n"
                "    return {'action_performed': True, 'action_type': 'click', 'target': 'alpha'}"
            ),
        }

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="open the closest matching project",
            runtime_results={},
        )
    )

    assert result.success is True
    assert "runtime_ai" not in result.trace.signals


def test_recording_runtime_agent_persists_replay_metadata_into_compilable_trace(monkeypatch):
    async def fake_build_page_snapshot(*_args, **_kwargs):
        return {
            "table_views": [
                {
                    "columns": [{"header": "Invoice"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Invoice", "text": "INV-001"},
                                {"column_header": "Status", "text": "Submitted"},
                            ]
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    async def planner(_payload):
        return {
            "description": "Search invoice and verify row",
            "action_type": "run_python",
            "expected_effect": "fill",
            "output_key": "invoice_search",
            "input_bindings": {
                "invoice_number": {
                    "source": "user_param",
                    "default": "INV-001",
                    "classification": "user_param",
                }
            },
            "output_bindings": {
                "invoice_number": {"path": "invoice_number"},
            },
            "postcondition": {
                "kind": "table_row_exists",
                "source": "observed",
                "table_headers": ["Invoice", "Status"],
                "key": {"Invoice": "{{invoice_number}}"},
                "expect": {"Status": "Submitted"},
            },
            "code": (
                "async def run(page, results):\n"
                "    await page.locator('input[name=invoice]').fill('INV-001')\n"
                "    return {'action_performed': True, 'action_type': 'fill', 'invoice_number': 'INV-001'}"
            ),
        }

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="search invoice INV-001 and verify submitted row",
            runtime_results={},
        )
    )

    assert result.success is True
    assert result.trace.input_bindings["invoice_number"]["default"] == "INV-001"
    assert result.trace.output_bindings["invoice_number"]["path"] == "invoice_number"
    assert result.trace.postcondition["kind"] == "table_row_exists"

    script = TraceSkillCompiler().generate_script([result.trace], is_local=True)
    assert "kwargs.get('invoice_number', 'INV-001')" in script
    assert "await _find_table_row_by_headers" in script
    assert ".fill('INV-001')" not in script


def test_recording_runtime_agent_ignores_untrusted_planner_postcondition():
    async def planner(_payload):
        return {
            "description": "Search invoice",
            "action_type": "run_python",
            "expected_effect": "fill",
            "output_key": "invoice_search",
            "postcondition": {
                "kind": "table_row_exists",
                "table_headers": ["Invoice", "Status"],
                "key": {"Invoice": "INV-001"},
                "expect": {"Status": "Done"},
            },
            "code": (
                "async def run(page, results):\n"
                "    await page.locator('input[name=invoice]').fill('INV-001')\n"
                "    return {'action_performed': True, 'action_type': 'fill', 'invoice_number': 'INV-001'}"
            ),
        }

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="search invoice INV-001",
            runtime_results={},
        )
    )

    assert result.success is True
    assert result.trace.postcondition == {}


def test_recording_runtime_agent_ignores_postcondition_without_snapshot_evidence():
    async def planner(_payload):
        return {
            "description": "Search invoice",
            "action_type": "run_python",
            "expected_effect": "fill",
            "output_key": "invoice_search",
            "input_bindings": {
                "invoice_number": {
                    "source": "user_param",
                    "default": "INV-001",
                    "classification": "user_param",
                }
            },
            "postcondition": {
                "kind": "table_row_exists",
                "source": "observed",
                "table_headers": ["Header"],
                "key": {"Header": "{{invoice_number}}"},
                "expect": {"Status": "Done"},
            },
            "code": (
                "async def run(page, results):\n"
                "    await page.locator('input[name=invoice]').fill('INV-001')\n"
                "    return {'action_performed': True, 'action_type': 'fill', 'invoice_number': 'INV-001'}"
            ),
        }

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="search invoice INV-001",
            runtime_results={},
        )
    )

    assert result.success is True
    assert result.trace.postcondition == {}


def test_recording_runtime_agent_infers_idempotent_row_absent_postcondition(monkeypatch):
    snapshots = iter(
        [
            {
                "table_views": [
                    {
                        "columns": [{"header": "Task ID"}, {"header": "Status"}],
                        "rows": [
                            {
                                "cells": [
                                    {"column_header": "Task ID", "text": "TASK-001"},
                                    {"column_header": "Status", "text": "pending"},
                                ]
                            }
                        ],
                    }
                ]
            },
            {"table_views": []},
        ]
    )

    async def fake_build_page_snapshot(*_args, **_kwargs):
        return next(snapshots)

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    async def planner(_payload):
        return {
            "description": "Complete task TASK-001",
            "action_type": "run_python",
            "expected_effect": "mixed",
            "code": (
                "async def run(page, results):\n"
                "    return {'action_performed': True, 'task_id': 'TASK-001'}"
            ),
        }

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="complete task TASK-001",
            runtime_results={},
        )
    )

    assert result.success is True
    assert result.trace.postcondition["kind"] == "table_row_absent"
    assert result.trace.signals["idempotent_postcondition_replay"]["ignore_precondition_errors"] is True


def test_recording_runtime_agent_accepts_extract_snapshot_plan(monkeypatch):
    async def fake_build_page_snapshot(_page, _build_frame_path):
        return {
            "url": "https://example.test/detail",
            "title": "Detail",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
            "detail_views": [],
        }

    async def planner(_payload):
        return {
            "description": "Extract procurement info",
            "action_type": "extract_snapshot",
            "expected_effect": "extract",
            "output_key": "procurement_info",
            "source": "detail_views",
            "section_title": "采购信息",
            "fields": [
                {
                    "label": "预计总金额 (含税）",
                    "value": "100.00",
                    "data_prop": "2652409177955720363",
                    "visible": True,
                    "value_kind": "number",
                },
                {
                    "label": "预计到货时间 (UTC+08:00)",
                    "value": "",
                    "data_prop": "7757927649859165361",
                    "visible": False,
                    "value_kind": "empty",
                },
            ],
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
        instruction="提取采购信息中的内容",
            runtime_results={},
        )
    )

    assert result.success is True
    assert result.output == {"预计总金额 (含税）": "100.00"}
    assert result.trace.ai_execution.language == "snapshot"
    assert "extract_snapshot" in result.trace.ai_execution.code
    assert "预计总金额 (含税）" in result.trace.ai_execution.code
    assert result.trace.signals["extract_snapshot"]["source"] == "detail_views"
    assert result.trace.signals["extract_snapshot"]["fields"][0]["data_prop"] == "2652409177955720363"


def test_recording_runtime_agent_repairs_incomplete_snapshot_extract(monkeypatch):
    async def fake_build_page_snapshot(_page, _build_frame_path):
        return {
            "url": "https://example.test/detail",
            "title": "Detail",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
            "detail_views": [],
        }

    plans = [
        {
            "description": "Read current record fields",
            "action_type": "extract_snapshot",
            "expected_effect": "extract",
            "output_key": "record_info",
            "source": "detail_views",
            "fields": [{"label": "Record", "value": "R-001", "visible": True}],
        },
        {
            "description": "Complete requested browser action",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "created_record",
            "code": "async def run(page, results):\n    return {'created': True}",
        },
    ]

    async def planner(_payload):
        return plans.pop(0)

    async def completion_verifier(payload):
        assert payload["plan"]["action_type"] == "extract_snapshot"
        return {
            "passed": False,
            "missing_requirements": ["requested browser action after data read"],
            "reason": "Only data extraction was completed.",
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner, completion_verifier=completion_verifier).run(
            page=_FakePage(),
            instruction="read the current record and then create the next item",
            runtime_results={},
        )
    )

    assert result.success is True
    assert result.output == {"created": True}
    assert result.diagnostics
    assert "Instruction completion verification failed" in result.diagnostics[0].message


def test_preplanned_snapshot_extract_skips_default_completion_llm(monkeypatch):
    async def fake_build_page_snapshot(_page, _build_frame_path):
        return {
            "url": "https://example.test/detail",
            "title": "Detail",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
            "detail_views": [
                {
                    "section_title": "Record",
                    "fields": [
                        {
                            "label": "Record number",
                            "value": "R-001",
                            "visible": True,
                            "field_locator": {"method": "role", "role": "row", "name": "Record number R-001"},
                        }
                    ],
                }
            ],
        }

    async def forbidden_completion_verifier(_payload):
        raise AssertionError("default completion verifier should not run for deterministic preplans")

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)
    agent = RecordingRuntimeAgent()
    agent._default_instruction_completion_judge = forbidden_completion_verifier

    result = asyncio.run(
        agent.run(
            page=_FakePage(),
            instruction="extract the visible record fields",
            runtime_results={},
        )
    )

    assert result.success is True
    assert result.trace.signals["instruction_completion"]["source"] == "structural_preplan"
    assert result.trace.signals["extract_snapshot"]["fields"][0]["label"] == "Record number"


def test_extract_snapshot_plan_resolves_alias_fields_from_observed_detail_snapshot():
    execute = getattr(recording_runtime_agent, "_execute_extract_snapshot_plan")
    plan = {
        "action_type": "extract_snapshot",
        "source": "detail_views",
        "fields": {
            "record_no": {"label": "Record No", "value_kind": "text"},
            "status": {"label": "Status", "value_kind": "text"},
        },
    }
    snapshot = {
        "detail_views": [
            {
                "fields": [
                    {"label": "Record No", "value": "REQ-100", "visible": True},
                    {"label": "Status", "value": "submitted", "visible": True},
                ]
            }
        ]
    }

    result = execute(plan, snapshot=snapshot)

    assert result["success"] is True
    assert result["output"] == {"record_no": "REQ-100", "status": "submitted"}
    fields = result["signals"]["extract_snapshot"]["fields"]
    assert fields[0]["observed_label"] == "Record No"


def test_recording_llm_call_has_internal_timeout(monkeypatch):
    class SlowModel:
        async def ainvoke(self, _messages):
            await asyncio.sleep(0.05)
            return SimpleNamespace(content="{}")

    monkeypatch.setattr(recording_runtime_agent, "_RECORDING_LLM_TIMEOUT_S", 0.001)

    with pytest.raises(TimeoutError, match="recording LLM call exceeded"):
        asyncio.run(recording_runtime_agent._ainvoke_model_with_recording_timeout(SlowModel(), []))


def test_identifier_gap_triggers_instruction_completion_verification():
    should_verify = getattr(recording_runtime_agent, "_should_verify_instruction_completion")

    assert should_verify(
        {"action_type": "run_python", "expected_effect": "mixed"},
        {
            "success": True,
            "output": {"created": True, "record": "PR-100"},
            "effect": {"terminal_evidence": "row_exists"},
        },
        "create PR-100 and then create PO-200",
    )


def test_weak_terminal_evidence_still_triggers_instruction_completion_verification():
    should_verify = getattr(recording_runtime_agent, "_should_verify_instruction_completion")

    assert should_verify(
        {"action_type": "run_python", "expected_effect": "mixed"},
        {
            "success": True,
            "output": {"submitted": True, "record": "R-001"},
            "effect": {"terminal_evidence": "feedback_visible"},
        },
        "submit the form and generate the follow-up record",
    )


def test_verified_terminal_contract_skips_instruction_completion_verification():
    should_verify = getattr(recording_runtime_agent, "_should_verify_instruction_completion")

    assert not should_verify(
        {"action_type": "run_python", "expected_effect": "mixed"},
        {
            "success": True,
            "terminal_verification": {"passed": True, "evidence": [{"type": "row_exists"}]},
        },
        "submit the form",
    )


def test_action_only_success_is_checked_for_instruction_completion(monkeypatch):
    plans = [
        {
            "description": "Open target dialog",
            "action_type": "run_python",
            "expected_effect": "click",
            "output_key": "dialog",
        },
        {
            "description": "Submit target dialog",
            "action_type": "run_python",
            "expected_effect": "click",
            "output_key": "submitted",
        },
    ]
    verifier_calls = []

    async def planner(_payload):
        return plans.pop(0)

    async def executor(_page, plan, _results):
        if plan["output_key"] == "dialog":
            return {
                "success": True,
                "output": {"action_performed": True, "action_type": "click", "target": "dialog"},
            }
        return {
            "success": True,
            "output": {"submitted": True, "record": "R-001"},
            "effect": {"type": "click", "action_performed": True, "terminal_evidence": "feedback_visible"},
        }

    async def completion_verifier(_payload):
        verifier_calls.append(_payload)
        return {
            "passed": False,
            "missing_requirements": ["terminal submit"],
            "reason": "Only an intermediate action was completed.",
        }

    async def fake_build_page_snapshot(_page, _build_frame_path):
        return {
            "url": "https://example.test",
            "title": "Example",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner, executor=executor, completion_verifier=completion_verifier).run(
            page=_FakePage(),
            instruction="open the dialog and submit it",
            runtime_results={},
        )
    )

    assert result.success is False
    assert verifier_calls
    assert result.output is None
    assert "Instruction completion verification failed" in result.diagnostics[0].message


def test_recording_runtime_agent_enriches_snapshot_extract_with_replay_evidence(monkeypatch):
    async def fake_build_page_snapshot(_page, _build_frame_path):
        return {
            "url": "https://github.com/mattpocock/skills",
            "title": "Repository",
            "frames": [],
            "actionable_nodes": [
                {
                    "role": "link",
                    "tag": "a",
                    "name": "Star 32.2k",
                    "text": "Star 32.2k",
                    "element_snapshot": {"tag": "a", "text": "Star 32.2k"},
                }
            ],
            "content_nodes": [
                {
                    "semantic_kind": "text",
                    "tag": "a",
                    "text": "32.2k stars",
                    "element_snapshot": {"tag": "a", "text": "32.2k stars"},
                },
                {
                    "semantic_kind": "text",
                    "tag": "a",
                    "text": "2.5k forks",
                    "element_snapshot": {"tag": "a", "text": "2.5k forks"},
                },
            ],
            "containers": [],
            "detail_views": [],
        }

    async def planner(_payload):
        return {
            "description": "Extract repository summary",
            "action_type": "extract_snapshot",
            "expected_effect": "extract",
            "output_key": "repo_basic_info",
            "source": "visible_page",
            "fields": [
                {"label": "project_name", "value": "mattpocock/skills", "visible": True},
                {"label": "star_count", "value": "32.2k", "visible": True},
                {"label": "fork_count", "value": "2.5k", "visible": True},
            ],
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="Extract repository project name, stars, and forks",
            runtime_results={},
        )
    )

    fields = {field["label"]: field for field in result.trace.signals["extract_snapshot"]["fields"]}

    assert fields["project_name"]["url_extraction"] == {
        "kind": "url_path_join",
        "start": 0,
        "count": 2,
        "separator": "/",
    }
    assert fields["star_count"]["text_pattern"]["suffix"] == "stars"
    assert fields["fork_count"]["text_pattern"]["suffix"] == "forks"
    assert fields["star_count"]["text_pattern"]["value"] == "32.2k"
    assert fields["fork_count"]["text_pattern"]["value"] == "2.5k"


@pytest.mark.asyncio
async def test_recording_runtime_agent_preserves_extract_snapshot_frame_path(monkeypatch):
    async def fake_build_page_snapshot(_page, _build_frame_path):
        return {
            "url": "https://example.test/detail",
            "title": "Detail",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
            "detail_views": [],
        }

    async def planner(_payload):
        return {
            "description": "Extract iframe detail",
            "action_type": "extract_snapshot",
            "expected_effect": "extract",
            "output_key": "iframe_detail",
            "source": "detail_views",
            "section_title": "Detail",
            "frame_path": ["iframe[title='detail']"],
            "fields": [
                {
                    "label": "Amount",
                    "value": "100.00",
                    "data_prop": "amount",
                    "visible": True,
                    "value_kind": "number",
                }
            ],
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="extract iframe detail",
        runtime_results={},
    )

    assert result.success is True
    assert result.trace.signals["extract_snapshot"]["frame_path"] == ["iframe[title='detail']"]


@pytest.mark.asyncio
async def test_recording_runtime_agent_attaches_locator_stability_metadata_when_available():
    async def planner(_payload):
        return {
            "description": "Open stable action menu",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "opened_menu",
            "code": (
                "async def run(page, results):\n"
                "    await page.locator('[data-testid=\"menu-btn-a1b2c3d4\"]').click()\n"
                "    return {'opened': True}"
            ),
        }

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="inspect the action menu button",
        runtime_results={},
    )

    assert result.success is True
    metadata = result.trace.locator_stability
    assert metadata is not None
    assert metadata.primary_locator["method"] == "css"
    assert metadata.unstable_signals[0]["attribute"] == "data-testid"


@pytest.mark.asyncio
async def test_recording_runtime_agent_keeps_trace_success_when_no_locator_stability_metadata_is_found():
    async def planner(_payload):
        return {
            "description": "Return summary",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "summary",
            "code": "async def run(page, results):\n    return {'summary': 'ok'}",
        }

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="summarize page",
        runtime_results={},
    )

    assert result.success is True
    assert result.trace.locator_stability is None


@pytest.mark.asyncio
async def test_recording_runtime_agent_extracts_stable_self_and_anchor_signals_from_snapshot(monkeypatch):
    snapshot = {
        "url": "https://example.test/dashboard",
        "title": "Dashboard",
        "actionable_nodes": [
            {
                "role": "button",
                "name": "Open menu",
                "text": "Open menu",
                "locator": {"method": "role", "role": "button", "name": "Open menu"},
                "container": {"title": "Quarterly Report"},
            }
        ],
        "content_nodes": [],
        "containers": [],
        "frames": [],
    }

    async def fake_build_page_snapshot(_page, _build_frame_path):
        return snapshot

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    async def planner(_payload):
        return {
            "description": "Inspect report menu",
            "action_type": "run_python",
            "expected_effect": "extract",
            "code": (
                "async def run(page, results):\n"
                "    await page.locator('[data-testid=\"menu-btn-a1b2c3d4\"]').click()\n"
                "    return {'opened': True}"
            ),
        }

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="inspect the report menu button",
        runtime_results={},
    )

    assert result.success is True
    metadata = result.trace.locator_stability
    assert metadata is not None
    assert metadata.stable_self_signals["role"] == "button"
    assert metadata.stable_self_signals["name"] == "Open menu"
    assert metadata.stable_anchor_signals["title"] == "Quarterly Report"
    assert metadata.alternate_locators[0].locator == {
        "method": "role",
        "role": "button",
        "name": "Open menu",
    }


@pytest.mark.asyncio
async def test_ensure_expected_effect_accepts_run_python_click_when_url_changes():
    page = _FakeNavigatedPage()
    result = await _ensure_expected_effect(
        page=page,
        instruction="click the third project",
        plan={
            "action_type": "run_python",
            "expected_effect": "click",
            "code": 'async def run(page, results):\n    await page.get_by_role("link", name="HKUDS / RAG-Anything").click()',
        },
        result={"success": True, "output": None},
        before=RPAPageState(url="https://github.com/trending", title="Trending repositories on GitHub today · GitHub"),
    )

    assert result["success"] is True
    assert result["effect"]["type"] == "click"
    assert result["effect"]["action_performed"] is True
    assert result["effect"]["observed_url_change"] is True
    assert result["effect"]["url"] == "https://github.com/HKUDS/RAG-Anything"


def test_ensure_expected_effect_accepts_mixed_with_action_evidence_without_url_change():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="open the tools panel",
            plan={"action_type": "run_python", "expected_effect": "mixed"},
            result={"success": True, "effect": {"type": "fill", "action_performed": True}},
            before=before,
        )
    )

    assert result["success"] is True
    assert page.url == before.url
    assert result["effect"]["action_performed"] is True


def test_ensure_expected_effect_rejects_terminal_write_with_only_action_evidence():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="submit the form and create the record",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            },
            result={"success": True, "output": {"action_performed": True, "action_type": "click", "target": "Submit"}},
            before=before,
        )
    )

    assert result["success"] is False
    assert "required terminal evidence" in result["error"]


def test_ensure_expected_effect_accepts_closed_modal_after_terminal_submit():
    page = _FakeModalClosedPage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="submit the current dialog form and confirm completion",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("feedback_visible", kind="record_updated"),
                "code": (
                    "async def run(page, results):\n"
                    "    dialog = page.get_by_role('dialog', name='Edit record')\n"
                    "    await dialog.get_by_role('button', name='Submit').click()\n"
                ),
            },
            result={
                "success": True,
                "output": {"action_performed": True, "action_type": "submit_form"},
                "browser_evidence": {
                    "before": {"visible_dialog_count": 1, "feedback_texts": [], "validation_texts": []},
                    "after": {"visible_dialog_count": 0, "feedback_texts": ["Saved successfully"], "validation_texts": []},
                },
            },
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "feedback_visible"


def test_ensure_expected_effect_rejects_feedback_when_url_change_is_required():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="perform an action and confirm the page changed state",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("url_changed", kind="state_change"),
            },
            result={
                "success": True,
                "output": {"action_performed": True, "action_type": "click"},
                "browser_evidence": {
                    "before": {"visible_dialog_count": 0, "feedback_texts": [], "validation_texts": []},
                    "after": {"visible_dialog_count": 0, "feedback_texts": ["Action completed"], "validation_texts": []},
                },
            },
            before=before,
        )
    )

    assert result["success"] is False
    assert result["terminal_verification"]["missing_evidence"] == ["url_changed"]


def test_ensure_expected_effect_accepts_new_feedback_for_generic_state_change_without_specific_evidence():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="perform an action and confirm the page changed state",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": {
                    "required": True,
                    "kind": "state_change",
                    "success_evidence": [],
                    "allow_semantic_judge": False,
                },
            },
            result={
                "success": True,
                "output": {"action_performed": True, "action_type": "click"},
                "browser_evidence": {
                    "before": {"visible_dialog_count": 0, "feedback_texts": [], "validation_texts": []},
                    "after": {"visible_dialog_count": 0, "feedback_texts": ["Action completed"], "validation_texts": []},
                },
            },
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "feedback_visible"


def test_ensure_expected_effect_rejects_preexisting_feedback_as_terminal_success():
    page = _FakeModalClosedPage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="submit the current dialog form and confirm completion",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("feedback_visible", kind="record_updated"),
            },
            result={
                "success": True,
                "output": {"action_performed": True, "action_type": "submit_form"},
                "browser_evidence": {
                    "before": {"visible_dialog_count": 1, "feedback_texts": ["Saved successfully"], "validation_texts": []},
                    "after": {"visible_dialog_count": 0, "feedback_texts": ["Saved successfully"], "validation_texts": []},
                },
            },
            before=before,
        )
    )

    assert result["success"] is False
    assert "required terminal evidence" in result["error"]


def test_ensure_expected_effect_rejects_terminal_submit_when_modal_stays_visible():
    page = _FakeVisibleDialogPage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="submit the current dialog form and confirm completion",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("feedback_visible", kind="record_updated"),
                "code": (
                    "async def run(page, results):\n"
                    "    dialog = page.get_by_role('dialog', name='Edit record')\n"
                    "    await dialog.get_by_role('button', name='Submit').click()\n"
                ),
            },
            result={"success": True, "output": {"action_performed": True, "action_type": "submit_form"}},
            before=before,
        )
    )

    assert result["success"] is False
    assert "required terminal evidence" in result["error"]


def test_ensure_expected_effect_rejects_validation_error_even_with_success_feedback():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="submit the form and create the record",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("feedback_visible", kind="record_created"),
            },
            result={
                "success": True,
                "output": {"action_performed": True, "action_type": "click"},
                "browser_evidence": {
                    "before": {"visible_dialog_count": 0, "feedback_texts": [], "validation_texts": []},
                    "after": {
                        "visible_dialog_count": 0,
                        "feedback_texts": ["Saved successfully"],
                        "validation_texts": ["Please complete required fields"],
                    },
                },
            },
            before=before,
        )
    )

    assert result["success"] is False
    assert result["terminal_verification"]["reason"] == "validation_error_visible"


def test_ensure_expected_effect_accepts_terminal_write_with_structured_terminal_evidence():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="submit the form and create the record",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            },
            result={
                "success": True,
                "output": {
                    "action_performed": True,
                    "action_type": "click",
                    "created_record": {"id": "REQ-1", "status": "Submitted"},
                    "terminal_evidence": {"type": "row_exists", "source": "observed", "observed": True},
                },
            },
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "row_exists"


def test_ensure_expected_effect_rejects_terminal_write_with_unrelated_structured_output():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="open the detail, create the request, and submit it",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            },
            result={
                "success": True,
                "output": {
                    "action_performed": True,
                    "opened_record": {"id": "A-1", "status": "effective"},
                    "detail_page_text_sample": "record detail",
                },
            },
            before=before,
        )
    )

    assert result["success"] is False
    assert "required terminal evidence" in result["error"]


def test_ensure_expected_effect_rejects_create_with_only_source_status():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="read the source record, create a new request, and submit it",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            },
            result={
                "success": True,
                "output": {
                    "source_record": {"id": "SRC-1", "status": "effective"},
                    "action_performed": True,
                },
            },
            before=before,
        )
    )

    assert result["success"] is False
    assert "required terminal evidence" in result["error"]


def test_ensure_expected_effect_accepts_expected_empty_result_state():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="search for a record that should have no matching results",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("empty_result", kind="empty_result"),
            },
            result={"success": True, "output": {"empty_state": "No matching results found", "row_count": 0}},
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "empty_result"


def test_ensure_expected_effect_accepts_match_count_zero_for_empty_result_state():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="search and confirm no records",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("empty_result", kind="empty_result"),
            },
            result={"success": True, "output": {"match_count": 0, "rows": []}},
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "empty_result"


def test_ensure_expected_effect_accepts_matched_rows_zero_for_empty_result_state():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="search and confirm no matching records",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("empty_result", kind="empty_result"),
            },
            result={"success": True, "output": {"matched_rows": 0, "conclusion": "no matching records"}},
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "empty_result"


def test_ensure_expected_effect_rejects_contradictory_empty_result_output():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="filter failed audit records and confirm the result list is empty",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("empty_result", kind="empty_result"),
            },
            result={
                "success": True,
                "output": {
                    "empty_result": True,
                    "confirmed_empty": True,
                    "matched_rows": 0,
                    "visible_rows": 2,
                    "empty_state_text": "",
                },
            },
            before=before,
        )
    )

    assert result["success"] is False
    assert "required terminal evidence" in result["error"]


def test_ensure_expected_effect_rejects_false_row_found_when_visible_rows_remain():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="search and confirm no matching records",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("empty_result", kind="empty_result"),
            },
            result={"success": True, "output": {"row_found": False, "visible_rows": 2}},
            before=before,
        )
    )

    assert result["success"] is False
    assert "required terminal evidence" in result["error"]


def test_ensure_expected_effect_accepts_structured_false_match_for_empty_result_state():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="search and confirm no matching records",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("empty_result", kind="empty_result"),
            },
            result={"success": True, "output": {"matched_result": False, "query": "NOT-FOUND"}},
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "empty_result"


def test_ensure_expected_effect_accepts_empty_state_text_for_empty_result_state():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="search and confirm no matching records",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("empty_result", kind="empty_result"),
            },
            result={"success": True, "output": {"empty_state_text": "No matching results"}},
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "empty_result"


def test_ensure_expected_effect_accepts_explicit_no_matching_results_output():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="search and confirm no matching records",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("empty_result", "row_absent", kind="empty_result"),
            },
            result={"success": True, "output": {"no_matching_results": True, "visible_row_count": 0}},
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] in {"empty_result", "row_absent"}


def test_ensure_expected_effect_does_not_accept_postcondition_values_from_structured_output_only():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="create a record and verify the resulting row",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("row_exists", "field_value_equals", kind="record_created"),
                "postcondition": {
                    "kind": "table_row_exists",
                    "source": "observed",
                    "key": {"Record ID": "{{record_id}}"},
                    "expect": {"Status": "Submitted"},
                },
                "output_bindings": {"record_id": "output.record_id", "status": "output.status"},
            },
            result={
                "success": True,
                "output": {
                    "record_id": "REQ-100",
                    "status": "Submitted",
                    "action_performed": True,
                },
            },
            before=before,
        )
    )

    assert result["success"] is False
    assert "required terminal evidence" in result["error"]


def test_completion_verifier_error_fails_without_strong_terminal_evidence():
    async def planner(_payload):
        return {
            "description": "Submit a form",
            "action_type": "run_python",
            "expected_effect": "mixed",
            "code": (
                "async def run(page, results):\n"
                "    return {'action_performed': True, 'action_type': 'click'}"
            ),
        }

    async def verifier(_payload):
        raise TimeoutError("verifier infrastructure timeout")

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner, completion_verifier=verifier).run(
            page=_FakePage(),
            instruction="submit the form",
            runtime_results={},
        )
    )

    assert result.success is False
    assert result.diagnostics
    assert "Instruction completion verification failed" in result.diagnostics[0].message


def test_ensure_expected_effect_recovers_structured_row_absent_failure():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="search and confirm the record is absent",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("row_absent", kind="empty_result"),
            },
            result={
                "success": False,
                "error": "{'match_found': False}",
                "output": {"match_found": False, "visible_rows": ["other row"]},
            },
            before=before,
        )
    )

    assert result["success"] is True
    assert result["structured_failure_recovered"] is True
    assert result["effect"]["terminal_evidence"] == "row_absent"


@pytest.mark.asyncio
async def test_ensure_expected_effect_accepts_mixed_with_structured_output_without_url_change():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = await _ensure_expected_effect(
        page=page,
        instruction="submit the search and capture the selected row",
        plan={"action_type": "run_python", "expected_effect": "mixed"},
        result={"success": True, "output": {"selected_row": {"name": "alpha", "status": "ready"}}},
        before=before,
    )

    assert result["success"] is True
    assert page.url == before.url
    assert result["output"]["selected_row"]["name"] == "alpha"


@pytest.mark.asyncio
async def test_ensure_expected_effect_rejects_mixed_error_shaped_output_without_url_change():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = await _ensure_expected_effect(
        page=page,
        instruction="submit the form",
        plan={"action_type": "run_python", "expected_effect": "mixed"},
        result={"success": True, "output": {"error": "submit button was not found"}},
        before=before,
    )

    assert result["success"] is False
    assert "visible error or validation output" in result["error"]


def test_ensure_expected_effect_rejects_visible_error_output_even_when_url_changes():
    page = _FakeNavigatedPage()

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="submit the form and create the record",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("feedback_visible", kind="record_created"),
            },
            result={
                "success": True,
                "output": {"body_text_excerpt": "Record not found\nPlease complete required fields"},
                "browser_evidence": {
                    "before": {"visible_dialog_count": 0, "feedback_texts": [], "validation_texts": []},
                    "after": {"visible_dialog_count": 0, "feedback_texts": [], "validation_texts": ["Please complete required fields"]},
                },
            },
            before=RPAPageState(url="https://example.test/form", title="Form"),
        )
    )

    assert result["success"] is False
    assert result["terminal_verification"]["reason"] == "validation_error_visible"


def test_ensure_expected_effect_rejects_nonterminal_download_output():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="generate the report and download it",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("download_created", kind="download_created"),
            },
            result={"success": True, "output": {"task_state": "not_confirmed_complete", "downloaded": False}},
            before=before,
        )
    )

    assert result["success"] is False
    assert "terminal success evidence" in result["error"]


def test_ensure_expected_effect_accepts_structured_download_created_output():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="generate the report and download it",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("download_created", kind="download_created"),
            },
            result={"success": True, "output": {"download_created": True, "download_suggested_filename": "report.csv"}},
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "download_created"


def test_ensure_expected_effect_rejects_download_filename_without_event_or_path():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="generate the report and download it",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("download_created", kind="download_created"),
            },
            result={"success": True, "output": {"downloaded_file": "report.csv"}},
            before=before,
        )
    )

    assert result["success"] is False
    assert result["terminal_verification"]["missing_evidence"] == ["download_created"]


def test_ensure_expected_effect_accepts_download_artifact_path_output():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = asyncio.run(
        _ensure_expected_effect(
            page=page,
            instruction="generate the report and download it",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("download_created", kind="download_created"),
            },
            result={"success": True, "output": {"download": {"filename": "report.csv", "path": "/tmp/report.csv"}}},
            before=before,
        )
    )

    assert result["success"] is True
    assert result["effect"]["terminal_evidence"] == "download_created"


@pytest.mark.asyncio
async def test_ensure_expected_effect_accepts_mixed_with_download_signal_without_url_change():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = await _ensure_expected_effect(
        page=page,
        instruction="generate and download the report",
        plan={"action_type": "run_python", "expected_effect": "mixed"},
        result={"success": True, "signals": {"download": {"filename": "report.xlsx", "count": 1}}},
        before=before,
    )

    assert result["success"] is True
    assert page.url == before.url
    assert result["signals"]["download"]["filename"] == "report.xlsx"


def test_expected_effect_treats_extract_snapshot_as_extract_even_when_plan_says_navigate():
    assert (
        _expected_effect(
            {
                "action_type": "extract_snapshot",
                "expected_effect": "navigate",
            },
            "打开详情并提取字段",
        )
        == "extract"
    )


@pytest.mark.asyncio
async def test_ensure_expected_effect_accepts_extract_snapshot_signal_even_if_plan_says_navigate():
    page = _FakePage()

    result = await _ensure_expected_effect(
        page=page,
        instruction="打开详情并提取字段",
        plan={"action_type": "extract_snapshot", "expected_effect": "navigate"},
        result={
            "success": True,
            "output": {"合同编号": "CT-001"},
            "signals": {"extract_snapshot": {"source": "detail_views"}},
        },
        before=RPAPageState(url=page.url, title="Example"),
    )

    assert result["success"] is True
    assert result["output"] == {"合同编号": "CT-001"}


def test_compact_snapshot_preserves_active_modal_dialogs():
    compact = recording_runtime_agent._compact_snapshot(
        {
            "url": "https://example.test/orders",
            "title": "Orders",
            "frames": [],
            "content_nodes": [],
            "actionable_nodes": [],
            "containers": [],
            "table_views": [],
            "detail_views": [],
            "modal_dialogs": [
                {
                    "title": "Approve order",
                    "role": "dialog",
                    "modal": True,
                    "fields": [{"label": "Comment", "value": ""}],
                    "actions": [
                        {
                            "label": "Approve",
                            "locator": {"method": "testid", "value": "approve"},
                        }
                    ],
                }
            ],
        },
        "approve the order in the dialog",
    )

    assert compact["modal_dialogs"][0]["title"] == "Approve order"
    assert compact["modal_dialogs"][0]["fields"][0]["label"] == "Comment"
    assert compact["modal_dialogs"][0]["actions"][0]["label"] == "Approve"


def test_detail_extract_plan_uses_visible_detail_fields_for_extract_requests():
    plan = _build_detail_extract_plan(
        "提取当前合同详情字段",
        {
            "detail_views": [
                {
                    "section_title": "合同详情",
                    "frame_path": [],
                    "fields": [
                        {"label": "合同编号", "value": "CT-001", "visible": True},
                        {"label": "内部标识", "value": "hidden", "visible": False},
                    ],
                }
            ]
        },
    )

    assert plan is not None
    assert plan["action_type"] == "extract_snapshot"
    assert plan["expected_effect"] == "extract"
    assert plan["fields"][0]["label"] == "合同编号"
    assert plan["fields"][0]["value"] == "CT-001"
    assert plan["fields"][0]["visible"] is True
    assert len(plan["fields"]) == 1


def test_empty_search_plan_extracts_query_token_and_returns_run_python():
    pytest.skip("empty-result search is now planner-driven, not a deterministic shortcut")
    plan = _build_empty_search_plan(
        "搜索不存在的编号 NO-SUCH-RECORD-001，确认没有匹配结果",
        {"table_views": [{"rows": [{"cells": []}]}]},
    )

    assert plan is not None
    assert plan["action_type"] == "run_python"
    assert plan["expected_effect"] == "mixed"
    assert "NO-SUCH-RECORD-001" in plan["code"]
    assert "没有匹配结果" in plan["code"]


def test_normalize_generated_playwright_code_repairs_common_python_api_typo():
    assert (
        _normalize_generated_playwright_code("await page.get_by_testid('submit').click()")
        == "await page.get_by_test_id('submit').click()"
    )


def test_normalize_generated_playwright_code_preserves_bare_text_click_strictness():
    code = "await page.get_by_text('合同台账', exact=True).click()"

    assert _normalize_generated_playwright_code(code) == code


def test_normalize_generated_playwright_code_routes_fill_to_editable_descendant_helper():
    code = "async def run(page, results):\n    search = page.get_by_test_id('filter')\n    await search.fill('ABC')"

    normalized = _normalize_generated_playwright_code(code)

    assert "async def _rpa_fill(" in normalized
    assert "await _rpa_fill(search, 'ABC')" in normalized
    assert "await search.fill('ABC')" not in normalized
    assert "get_by_role(\"option\"" in normalized
    assert "[role=combobox], [aria-haspopup=listbox]" in normalized
    assert "_try_active_dialog_single_editable" in normalized
    assert "_try_nearest_single_editable" in normalized
    assert "_try_following_label_editable" not in normalized


def test_normalize_generated_playwright_code_falls_back_to_dialog_submit_action():
    code = (
        "async def run(page, results):\n"
        "    dialog = page.get_by_role('dialog')\n"
        "    submit_button = dialog.get_by_role('button', name='Open').first\n"
        "    await submit_button.click()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "async def _rpa_click_dialog_primary_action(" in normalized
    assert "await _rpa_click_dialog_primary_action(dialog, preferred_name='Open')" in normalized
    assert "await submit_button.click()" not in normalized
    assert "[data-testid*='submit']" in normalized
    assert "for index in range(count - 1, -1, -1)" not in normalized


def test_normalize_generated_playwright_code_does_not_rewrite_fill_helper_body():
    code = (
        "async def _rpa_fill(locator, value):\n"
        "    await locator.fill(str(value))\n\n"
        "async def run(page, results):\n"
        "    search = page.get_by_test_id('filter')\n"
        "    await search.fill('ABC')"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "await locator.fill(str(value))" in normalized
    assert "await _rpa_fill(locator" not in normalized
    assert "await _rpa_fill(search, 'ABC')" in normalized


def test_normalize_generated_playwright_code_rewrites_callable_role_name_filter():
    code = (
        "async def run(page, results):\n"
        "    row = page.get_by_role('row', name=lambda n: 'REQ-001' in n)\n"
        "    return await row.count()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "name=lambda" not in normalized
    assert "page.get_by_role('row').filter(has_text='REQ-001')" in normalized


def test_normalize_generated_playwright_code_rewrites_callable_false_placeholder():
    code = (
        "async def run(page, results):\n"
        "    rows = [page.get_by_role('row', name=lambda name: False).first]\n"
        "    return rows\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "name=lambda" not in normalized
    assert "page.locator('__rpa_no_match__').first" in normalized


def test_normalize_generated_playwright_code_rewrites_star_arg_callable_false_placeholder():
    code = (
        "async def run(page, results):\n"
        "    rows = [page.get_by_role('row', name=lambda *_: False).first]\n"
        "    return rows\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "name=lambda" not in normalized
    assert "page.locator('__rpa_no_match__').first" in normalized


def test_normalize_generated_playwright_code_wraps_combobox_clicks():
    code = (
        "async def run(page, results):\n"
        "    await page.get_by_role('combobox', name='Status').click()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "async def _rpa_click_combobox(" in normalized
    assert "await _rpa_click_combobox(page.get_by_role('combobox', name='Status'))" in normalized
    assert "get_by_role('combobox', name='Status').click()" not in normalized


def test_normalize_generated_playwright_code_wraps_combobox_variable_clicks():
    code = (
        "async def run(page, results):\n"
        "    status_filter = page.get_by_role('combobox', name='Status')\n"
        "    await status_filter.click()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "async def _rpa_click_combobox(" in normalized
    assert "await _rpa_click_combobox(status_filter)" in normalized


def test_normalize_generated_playwright_code_wraps_enter_submission():
    code = (
        "async def run(page, results):\n"
        "    target = page.locator('input').first\n"
        "    await target.fill('abc')\n"
        "    await target.press('Enter')\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "async def _rpa_submit_text_input(" in normalized
    assert "await _rpa_submit_text_input(target)" in normalized
    assert "button[type=submit]" in normalized
    assert "[role=button]" not in normalized
    assert "Enter submission failed" in normalized


def test_normalize_generated_playwright_code_broadens_row_cell_action_locator():
    code = (
        "async def run(page, results):\n"
        "    row = page.locator('tbody tr').nth(0)\n"
        "    await row.locator('td:nth-child(3) button').click()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "td:nth-child(3) a" in normalized
    assert "td:nth-child(3) button" in normalized
    assert "td:nth-child(3) [role=button]" in normalized


def test_normalize_generated_playwright_code_waits_for_required_locator_guard():
    code = (
        "async def run(page, results):\n"
        "    region = page.get_by_text('Dynamic first item list').first\n"
        "    if await region.count() == 0:\n"
        "        raise RuntimeError('missing region')\n"
        "    return await region.inner_text()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "await region.wait_for(state=\"visible\", timeout=10000)" in normalized
    assert normalized.index("await region.wait_for") < normalized.index("if await region.count() == 0")


def test_normalize_generated_playwright_code_does_not_wait_for_fallback_locator_guard():
    code = (
        "async def run(page, results):\n"
        "    table = page.get_by_role('table', name='Orders')\n"
        "    if await table.count() == 0:\n"
        "        table = page.locator('table').filter(has_text='Order ID')\n"
        "    return await table.count()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "await table.wait_for" not in normalized


def test_normalize_generated_playwright_code_expands_region_container_xpath():
    code = (
        "async def run(page, results):\n"
        "    region = page.get_by_text('Orders').first\n"
        "    container = region.locator('xpath=ancestor::*[self::section or self::div][1]')\n"
        "    return await container.count()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "self::article" in normalized
    assert '@role=\\"grid\\"' in normalized
    assert "ancestor::*[self::section or self::div][1]" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_expands_ancestor_or_self_region_container_xpath():
    code = (
        "async def run(page, results):\n"
        "    region = page.get_by_text('Orders').first\n"
        "    container = region.locator('xpath=ancestor-or-self::*[self::section or self::div or self::article][1]')\n"
        "    return await container.count()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert '@role=\\"grid\\"' in normalized
    assert "ancestor-or-self::*[self::section or self::div or self::article][1]" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_prepares_disabled_download_control():
    code = (
        "async def run(page, results):\n"
        "    download_loc = page.get_by_test_id('download-report')\n"
        "    async with page.expect_download() as download_info:\n"
        "        await download_loc.click()\n"
        "    return await download_info.value\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "async def _rpa_prepare_download_control" in normalized
    assert "await _rpa_prepare_download_control(page, download_loc)" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_wraps_combobox_click_with_timeout():
    code = (
        "async def run(page, results):\n"
        "    await page.get_by_role('combobox', name='Status').first.click(timeout=12000)\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "async def _rpa_click_combobox" in normalized
    assert "await _rpa_click_combobox(page.get_by_role('combobox', name='Status').first, timeout=12000)" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_wraps_named_table_lookup():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    table = page.get_by_role('table', name='Split header/body grid')\n"
        "    row = table.locator('tbody tr').first\n"
    )

    assert "async def _rpa_named_table(page, name" in normalized
    assert "table = await _rpa_named_table(page, 'Split header/body grid')" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_removes_recorded_link_name_from_ordinal_row():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    target_name = 'recorded-first-row.csv'\n"
        "    table = page.get_by_role('table', name='Files')\n"
        "    row = table.get_by_role('row').first\n"
        "    link = row.get_by_role('link', name=target_name)\n"
        "    await link.click()\n"
    )

    assert "link = row.get_by_role('link').first" in normalized
    assert "link = row.get_by_role('link', name=target_name)" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_repairs_playwright_async_api_shapes():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    value = page.get_by_text('Status').locator('xpath=following::*[1]').inner_text()\n"
        "    filename = await download.suggested_filename\n"
        "    return {'value': value, 'filename': filename}\n"
    )

    assert "value = await page.get_by_text('Status').locator('xpath=following::*[1]').inner_text()" in normalized
    assert "filename = download.suggested_filename" in normalized
    assert "await download.suggested_filename" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_rebinds_misaligned_testid_form_controls():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    request_input = page.get_by_test_id('supplier-input')\n"
        "    supplier_input = page.get_by_test_id('department-input')\n"
        "    cost_center_input = page.locator('input').filter(has=page.get_by_text('Cost center'))\n"
        "    await _rpa_fill(request_input, 'REQ-001')\n"
        "    await _rpa_fill(supplier_input, 'SUP-001')\n"
    )

    assert "async def _rpa_form_control_by_semantic_name(" in normalized
    assert "div, td, th" not in normalized
    assert "request_input = await _rpa_form_control_by_semantic_name(page, 'request_input')" in normalized
    assert "supplier_input = await _rpa_form_control_by_semantic_name(page, 'supplier_input')" in normalized
    assert "cost_center_input = await _rpa_form_control_by_semantic_name(page, 'Cost center')" in normalized
    assert "any(token in text" not in normalized
    assert "Ambiguous form control" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_does_not_rebind_non_fill_testid_controls():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    opener = page.get_by_test_id('open-popup-report')\n"
        "    await opener.click()\n"
    )

    assert "opener = page.get_by_test_id('open-popup-report')" in normalized
    assert "_rpa_form_control_by_semantic_name(page, 'opener')" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_waits_for_text_count_assertions():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    await page.get_by_text('Contracts', exact=True).click()\n"
        "    await page.wait_for_load_state('networkidle')\n"
        "    target_visible = await page.get_by_text('CT-2026-RPA-001', exact=True).count() > 0\n"
        "    if not target_visible:\n"
        "        raise RuntimeError('missing target')\n"
    )

    assert "async def _rpa_text_present(" in normalized
    assert "target_visible = await _rpa_text_present(page, 'CT-2026-RPA-001', exact=True)" in normalized
    assert ".count() > 0" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_uses_first_for_direct_text_waits():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    await page.get_by_text('PO-2026-RPA-NEW-001').wait_for(state='visible')\n"
    )

    assert "await page.get_by_text('PO-2026-RPA-NEW-001').first.wait_for(state='visible')" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_uses_first_for_expect_text_visible():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    await expect(page.get_by_text('PR-2026-RPA-NEW-001')).to_be_visible(timeout=5000)\n"
    )

    assert "await expect(page.get_by_text('PR-2026-RPA-NEW-001').first).to_be_visible(timeout=5000)" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_rewrites_css_table_row_text_locator():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    target_row = page.locator('.framework-table-body tbody tr', has_text='ROW-001').first\n"
        "    await target_row.wait_for(state='visible', timeout=5000)\n"
    )

    assert "def _rpa_find_row_by_text(" in normalized
    assert "target_row = _rpa_find_row_by_text(page, 'ROW-001')" in normalized
    assert ".framework-table-body tbody tr" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_rewrites_scoped_table_filter_row_text_locator():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    row = page.locator('table').filter(has_text='合同编号').first.locator('tbody tr').filter(has_text='CT-001').first\n"
        "    await row.wait_for(state='visible', timeout=5000)\n"
    )

    assert "def _rpa_find_row_by_text(" in normalized
    assert "row = _rpa_find_row_by_text(page, 'CT-001')" in normalized
    assert ".filter(has_text='合同编号').first.locator('tbody tr')" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_rejects_unscoped_empty_textbox_fallback():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    for value in ['item', '5', '6800']:\n"
        "        loc = page.get_by_role('textbox').filter(has_not_text='').first\n"
        "        if await loc.count():\n"
        "            await _rpa_fill(loc, value, timeout=5000)\n"
    )

    assert "__rpa_rejected_unscoped_textbox_fallback__" in normalized
    assert "get_by_role('textbox').filter(has_not_text='').first" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_keeps_common_abbreviated_field_testid():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    dept_input = page.get_by_test_id('dataflow-department-input')\n"
        "    await _rpa_fill(dept_input, department)\n"
    )

    assert "dept_input = page.get_by_test_id('dataflow-department-input')" in normalized
    assert "_rpa_form_control_by_semantic_name(page, 'dept_input')" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_filters_row_collection_by_checked_text():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    contract_no = 'ROW-001'\n"
        "    table = page.locator('table').filter(has_text='Header')\n"
        "    rows = table.locator('tbody tr')\n"
        "    for i in range(await rows.count()):\n"
        "        row = rows.nth(i)\n"
        "        if contract_no in await row.inner_text():\n"
        "            return {'ok': True}\n"
        "    raise RuntimeError('missing')\n"
    )

    assert "rows = _rpa_rows_containing_text(page, contract_no)" in normalized
    assert "def _rpa_rows_containing_text(" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_does_not_guess_dialog_opener():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    modal = page.get_by_role('dialog')\n"
        "    await modal.wait_for(state='visible')\n"
    )

    assert "async def _rpa_ensure_visible_dialog(" not in normalized
    assert "await _rpa_ensure_visible_dialog(page, modal)" not in normalized
    assert "await modal.wait_for(state='visible')" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_removes_body_text_navigation_guard():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    if 'Contracts' not in await page.locator('body').inner_text():\n"
        "        nav = page.get_by_text('Contracts', exact=True)\n"
        "        await nav.click()\n"
        "        await page.wait_for_load_state('networkidle')\n"
        "    return {'ok': True}\n"
    )

    assert "if 'Contracts' not in await page.locator('body').inner_text():" not in normalized
    assert "    nav = page.get_by_text('Contracts', exact=True)" in normalized
    assert "    await nav.click()" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_keeps_non_navigation_body_text_guard():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    if 'Exported' not in await page.locator('body').inner_text():\n"
        "        await page.get_by_role('button', name='Export').click()\n"
        "    return {'ok': True}\n"
    )

    assert "if 'Exported' not in await page.locator('body').inner_text():" in normalized
    assert "await page.get_by_role('button', name='Export').click()" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_removes_post_navigation_body_text_assertions():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    await page.get_by_role('menuitem', name='合同台账').click()\n"
        "    await page.wait_for_load_state('networkidle')\n"
        "    body_text = await page.locator('body').inner_text()\n"
        "    if 'CT-2026-RPA-001' not in body_text:\n"
        "        raise RuntimeError('contract row not visible')\n"
        "    return {'ok': True}\n"
    )

    assert "body_text = await page.locator('body').inner_text()" in normalized
    assert "if 'CT-2026-RPA-001' not in body_text:" not in normalized
    assert "raise RuntimeError('contract row not visible')" not in normalized
    compile(normalized, "<normalized>", "exec")


def test_named_table_helper_allows_empty_tables():
    normalized = _normalize_generated_playwright_code(
        "async def run(page, results):\n"
        "    table = page.get_by_role('table', name='Audit records')\n"
        "    row_count = await table.locator('tbody tr').count()\n"
        "    return {'row_count': row_count}\n"
    )

    assert "async def _rpa_named_table(" in normalized
    assert "following::table[.//tbody/tr]" not in normalized
    assert "return await candidate.first.is_visible()" in normalized
    compile(normalized, "<normalized>", "exec")


def test_verify_terminal_contract_requires_explicit_field_value_evidence():
    outcome = verify_terminal_contract(
        plan={
            "expected_effect": "state_change",
            "terminal_contract": {
                "kind": "state_change",
                "success_evidence": [{"type": "field_value_equals"}],
            },
        },
        result={"action_performed": True, "feedback_visible": True},
        before=RPAPageState(url="https://example.test/form"),
        after=RPAPageState(url="https://example.test/form"),
    )

    assert outcome["passed"] is False
    assert outcome["reason"] == "missing_terminal_evidence"


def test_trace_skill_compiler_normalizes_embedded_ai_code_before_export():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Click first file row",
        output_key="clicked_file",
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    target_name = 'recorded-first-row.csv'\n"
                "    table = page.get_by_role('table', name='Files')\n"
                "    row = table.get_by_role('row').first\n"
                "    link = row.get_by_role('link', name=target_name)\n"
                "    await link.click()\n"
                "    return {'action_performed': True}\n"
            )
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)

    assert "async def _rpa_named_table(page, name" in script
    assert "table = await _rpa_named_table(page, 'Files')" in script
    assert "link = row.get_by_role('link').first" in script
    assert "link = row.get_by_role('link', name=target_name)" not in script
    compile(script, "<skill>", "exec")


def test_normalize_generated_playwright_code_does_not_rewrite_awaited_dialog_button_lookup():
    code = (
        "async def run(page, results):\n"
        "    _submit = await _first_visible(_dialog.get_by_role('button', name=_submit_pattern))\n"
        "    await _submit.click(timeout=8000)\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "await _submit.click(timeout=8000)" in normalized
    compile(normalized, "<normalized>", "exec")


def test_normalize_generated_playwright_code_broadens_table_checkbox_locator():
    code = (
        "async def run(page, results):\n"
        "    checkboxes = table.locator('tbody input[type=\"checkbox\"]')\n"
        "    return await checkboxes.count()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert 'tbody input[type="checkbox"]' in normalized
    assert 'tbody [role="checkbox"]' in normalized


def test_normalize_generated_playwright_code_moves_visible_option_to_locator_filter():
    code = (
        "async def run(page, results):\n"
        "    button = page.get_by_role('button', name='审批', visible=True).first\n"
        "    await button.click()\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "get_by_role('button', name='审批').filter(visible=True).first" in normalized
    assert "get_by_role('button', name='审批', visible=True)" not in normalized


def test_normalize_generated_playwright_code_normalizes_download_contexts_without_recovery_wrapper():
    code = (
        "async def run(page, results):\n"
        "    await page.get_by_test_id('generate-report').click()\n"
        "    with page.expect_download(timeout=1000) as download_info:\n"
        "        await page.get_by_test_id('download-report').click()\n"
        "    download = await download_info.value\n"
        "    return {'filename': download.suggested_filename}\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "async with page.expect_download(timeout=1000) as download_info:" in normalized
    assert "_rpa_try_visible_download" not in normalized
    assert "except Exception as _rpa_download_error:" not in normalized


def test_combine_run_python_attempts_preserves_failed_precondition_before_repair():
    failed_plan = {
        "action_type": "run_python",
        "code": "async def run(page, results):\n    await page.get_by_role('button', name='Open').click()\n",
    }
    repair_plan = {
        "action_type": "run_python",
        "description": "submit visible dialog",
        "code": "async def run(page, results):\n    await page.get_by_role('button', name='Submit').click()\n    return {'ok': True}\n",
    }

    combined = _combine_run_python_attempts([failed_plan], repair_plan)

    assert combined is not None
    assert "_RPA_PRECONDITION_CODES" in combined["code"]
    assert "_RPA_REPAIR_CODE" in combined["code"]
    assert "async def _rpa_run_isolated(code, page, results):" in combined["code"]
    assert "results.setdefault('_rpa_precondition_errors'" in combined["code"]
    assert "return await _rpa_run_isolated(_RPA_REPAIR_CODE, page, results)" in combined["code"]


def test_combine_run_python_attempts_isolates_helper_names_between_attempts():
    failed_plan = {
        "action_type": "run_python",
        "code": (
            "def helper():\n"
            "    return 'open'\n"
            "async def run(page, results):\n"
            "    results.setdefault('calls', []).append(helper())\n"
        ),
    }
    repair_plan = {
        "action_type": "run_python",
        "code": (
            "def helper():\n"
            "    return 'submit'\n"
            "async def run(page, results):\n"
            "    results.setdefault('calls', []).append(helper())\n"
            "    return {'ok': True}\n"
        ),
    }

    combined = _combine_run_python_attempts([failed_plan], repair_plan)
    namespace = {}
    exec(combined["code"], namespace, namespace)
    results = {}
    output = asyncio.run(namespace["run"](None, results))

    assert output == {"ok": True}
    assert results["calls"] == ["open", "submit"]


def test_recording_runtime_main_path_has_no_domain_specific_terms():
    source = Path(recording_runtime_agent.__file__).read_text(encoding="utf-8")
    domain_terms = [
        "采购申请",
        "采购订单",
        "合同编号",
        "供应商",
        "报表中心",
    ]

    for term in domain_terms:
        assert term not in source


def test_recording_failure_classifies_active_overlay_interception():
    analysis = _classify_recording_failure(
        'Locator.click: Timeout 60000ms exceeded. <div role="dialog"> intercepts pointer events'
    )

    assert analysis["type"] == "active_overlay_intercepted_click"
    assert "visible dialog" in analysis["hint"]


def test_recording_failure_classifies_non_editable_fill_target():
    analysis = _classify_recording_failure(
        "Locator.fill: Error: Element is not an <input>, <textarea>, <select> or [contenteditable]"
    )

    assert analysis["type"] == "non_editable_fill_target"


def test_recording_failure_classifies_number_input_text_fill():
    analysis = _classify_recording_failure(
        "Locator.fill: Error: Cannot type text into input[type=number]\n"
        "  - locator resolved to <input type=\"number\" role=\"spinbutton\"/>"
    )

    assert analysis["type"] == "numeric_input_text_mismatch"
    assert "number input" in analysis["hint"]


def test_recording_failure_classifies_locator_not_subscriptable():
    analysis = _classify_recording_failure("TypeError: 'Locator' object is not subscriptable")

    assert analysis["type"] == "locator_not_subscriptable"
    assert "count()" in analysis["hint"]


def test_runtime_agent_uses_planner_for_search_empty_result_semantics(monkeypatch):
    calls = []

    async def fake_snapshot(_page):
        return {
            "url": "https://example.test/items",
            "title": "Items",
            "table_views": [
                {
                    "columns": [{"header": "Number"}],
                    "rows": [],
                }
            ],
        }

    async def planner(payload):
        calls.append(payload)
        return {
            "description": "LLM semantic plan",
            "action_type": "run_python",
            "expected_effect": "extract",
            "allow_empty_output": False,
            "output_key": "llm_result",
            "code": "async def run(page, results):\n    return {'ok': True}",
        }

    async def executor(_page, plan, _runtime_results):
        return {"success": True, "output": {"used": plan["output_key"]}}

    monkeypatch.setattr(recording_runtime_agent, "_safe_page_snapshot", fake_snapshot)

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner, executor=executor).run(
            page=_FakePage(),
            instruction="Search for ABC-404 and confirm that there is no matching record.",
        )
    )

    assert result.success is True
    assert calls
    assert result.output == {"used": "llm_result"}


@pytest.mark.asyncio
async def test_ensure_expected_effect_accepts_run_python_fill_with_structured_output():
    page = _FakePage()
    before = RPAPageState(url=page.url, title="Example")

    result = await _ensure_expected_effect(
        page=page,
        instruction="fill and submit the dialog",
        plan={
            "action_type": "run_python",
            "expected_effect": "fill",
            "code": "async def run(page, results):\n    await page.locator('input').fill('ok')\n    return {'submitted': True}",
        },
        result={"success": True, "output": {"submitted": True}},
        before=before,
    )

    assert result["success"] is True
    assert result["effect"]["action_performed"] is True
    assert result["effect"]["generic_evidence"] == "structured_output"


def test_filled_value_conflicts_with_source_output_detects_mismatched_mapped_field():
    error = _filled_value_conflicts_with_source_output(
        {
            "action_performed": True,
            "action_type": "fill",
            "filled_value": {"Department": "SUP-2026-001"},
            "source_owner_department": "Procurement Automation",
        }
    )

    assert "conflicts with its declared source output" in error


def test_terminal_contract_accepts_structured_feedback_for_toast_evidence():
    before = RPAPageState(url="http://app/items", title="Items")
    after = RPAPageState(url="http://app/items", title="Items")

    result = verify_terminal_contract(
        plan={
            "terminal_contract": {
                "required": True,
                "kind": "record_updated",
                "success_evidence": [{"type": "toast_visible"}],
            }
        },
        result={
            "browser_evidence": {
                "before": {"feedback_texts": []},
                "after": {"feedback_texts": ["Saved"]},
            }
        },
        before=before,
        after=after,
    )

    assert result["passed"] is True
    assert result["evidence"][0]["type"] == "feedback_visible"


def test_terminal_contract_accepts_explicit_field_value_output_evidence():
    before = RPAPageState(url="http://app/items", title="Items")
    after = RPAPageState(url="http://app/items", title="Items")

    result = verify_terminal_contract(
        plan={
            "terminal_contract": {
                "required": True,
                "kind": "state_change",
                "success_evidence": [{"type": "field_value_equals"}],
            }
        },
        result={"output": {"field_value_equals": "current-value"}},
        before=before,
        after=after,
    )

    assert result["passed"] is True
    assert result["evidence"][0]["type"] == "field_value_equals"


def test_terminal_contract_accepts_structured_clicked_row_output_for_row_exists_click():
    result = verify_terminal_contract(
        plan={
            "expected_effect": "click",
            "terminal_contract": {
                "required": True,
                "kind": "state_change",
                "success_evidence": [{"type": "row_exists"}],
            },
        },
        result={"output": {"action_performed": True, "clicked_row_text": "ROW-001\tFirst row"}},
        before=RPAPageState(url="https://example.test/table"),
        after=RPAPageState(url="https://example.test/table"),
    )

    assert result["passed"] is True
    assert result["evidence"][0]["type"] == "row_exists"


def test_terminal_contract_keeps_required_field_value_evidence_strict():
    result = verify_terminal_contract(
        plan={
            "expected_effect": "click",
            "terminal_contract": {
                "required": True,
                "kind": "state_change",
                "success_evidence": [{"type": "field_value_equals"}],
            },
        },
        result={
            "browser_evidence": {
                "before": {"feedback_texts": []},
                "after": {"feedback_texts": ["Project opened"]},
            }
        },
        before=RPAPageState(url="https://example.test/list"),
        after=RPAPageState(url="https://example.test/list"),
    )

    assert result["passed"] is False
    assert result["reason"] == "missing_terminal_evidence"
    assert result["missing_evidence"] == ["field_value_equals"]


def test_terminal_contract_treats_new_not_found_feedback_as_validation_error():
    result = verify_terminal_contract(
        plan={
            "expected_effect": "click",
            "terminal_contract": {
                "required": True,
                "kind": "state_change",
                "success_evidence": [{"type": "feedback_visible"}],
            },
        },
        result={
            "browser_evidence": {
                "before": {"feedback_texts": []},
                "after": {"feedback_texts": ["未找到完成审批的按钮"]},
            }
        },
        before=RPAPageState(url="https://example.test/approval"),
        after=RPAPageState(url="https://example.test/approval"),
    )

    assert result["passed"] is False
    assert result["reason"] == "validation_error_visible"


def test_terminal_contract_treats_empty_result_feedback_as_positive_when_declared():
    result = verify_terminal_contract(
        plan={
            "expected_effect": "extract",
            "terminal_contract": {
                "required": True,
                "kind": "empty_result",
                "success_evidence": [{"type": "empty_result"}],
            },
        },
        result={
            "output": {"empty_result": True, "row_count": 0},
            "browser_evidence": {
                "before": {"feedback_texts": []},
                "after": {"feedback_texts": ["No failed records found"]},
            },
        },
        before=RPAPageState(url="https://example.test/audit"),
        after=RPAPageState(url="https://example.test/audit"),
    )

    assert result["passed"] is True
    assert not any(item["type"] == "validation_error_visible" for item in result["evidence"])


def test_terminal_contract_accepts_download_action_with_observed_filename():
    result = verify_terminal_contract(
        plan={
            "terminal_contract": {
                "required": True,
                "kind": "download_created",
                "success_evidence": [{"type": "download_created"}],
            },
        },
        result={"output": {"action_type": "download", "downloaded_filename": "report.csv"}},
        before=RPAPageState(url="https://example.test/report"),
        after=RPAPageState(url="https://example.test/report"),
    )

    assert result["passed"] is True
    assert result["evidence"][0]["type"] == "download_created"


def test_terminal_contract_accepts_downloaded_flag_with_filename_like_key():
    result = verify_terminal_contract(
        plan={
            "terminal_contract": {
                "required": True,
                "kind": "download_created",
                "success_evidence": [{"type": "download_created"}],
            },
        },
        result={
            "output": {
                "action_performed": True,
                "action_type": "open_popup_and_download",
                "downloaded": True,
                "download_suggested_filename": "popup_report_2026.csv",
            }
        },
        before=RPAPageState(url="https://example.test/report"),
        after=RPAPageState(url="https://example.test/report"),
    )

    assert result["passed"] is True
    assert result["evidence"][0]["type"] == "download_created"


def test_ensure_expected_effect_allows_error_words_in_extract_output():
    async def run_check():
        page = _FakePage()
        before = RPAPageState(url=page.url, title="Example")

        return await _ensure_expected_effect(
            page=page,
            instruction="extract validation status fields",
            plan={"action_type": "run_python", "expected_effect": "extract"},
            result={
                "success": True,
                "output": {
                    "status": "not found",
                    "required_action": "manual review",
                    "error_code": "none",
                },
            },
            before=before,
        )

    result = asyncio.run(run_check())

    assert result["success"] is True
    assert result["output"]["status"] == "not found"


def test_recording_runtime_agent_repairs_once_after_failure():
    async def run_check():
        calls = []

        async def planner(payload):
            calls.append(payload)
            if "repair" not in payload:
                return {
                    "description": "Broken",
                    "action_type": "run_python",
                    "code": "async def run(page, results):\n    raise RuntimeError('boom')",
                }
            return {
                "description": "Fixed",
                "action_type": "run_python",
                "output_key": "fixed",
                "code": "async def run(page, results):\n    return {'ok': True}",
            }

        agent = RecordingRuntimeAgent(planner=planner)
        result = await agent.run(page=_FakePage(), instruction="do it", runtime_results={})
        return calls, result

    calls, result = asyncio.run(run_check())

    assert result.success is True
    assert len(calls) == 2
    assert result.trace.ai_execution.repair_attempted is True
    assert result.diagnostics[0].message == "boom"


def test_recording_runtime_agent_can_recover_on_second_repair():
    async def run_check():
        calls = []

        async def planner(payload):
            calls.append(payload)
            if "repair" not in payload:
                return {
                    "description": "Broken first attempt",
                    "action_type": "run_python",
                    "code": "async def run(page, results):\n    raise RuntimeError('first boom')",
                }
            if not payload["repair"].get("previous_failures"):
                return {
                    "description": "Broken repair",
                    "action_type": "run_python",
                    "code": "async def run(page, results):\n    raise RuntimeError('repair boom')",
                }
            return {
                "description": "Second repair fixed",
                "action_type": "run_python",
                "output_key": "fixed",
                "code": "async def run(page, results):\n    return {'ok': True}",
            }

        agent = RecordingRuntimeAgent(planner=planner)
        result = await agent.run(page=_FakePage(), instruction="do it", runtime_results={})
        return calls, result

    calls, result = asyncio.run(run_check())

    assert result.success is True
    assert len(calls) == 3
    assert [diagnostic.message for diagnostic in result.diagnostics] == ["first boom", "repair boom"]
    assert result.output == {"ok": True}
    assert "after repair" in result.message


def test_recording_runtime_agent_fails_after_two_failed_repairs():
    async def run_check():
        calls = []

        async def planner(payload):
            calls.append(payload)
            attempt = len(calls)
            return {
                "description": f"Broken attempt {attempt}",
                "action_type": "run_python",
                "code": f"async def run(page, results):\n    raise RuntimeError('boom {attempt}')",
            }

        agent = RecordingRuntimeAgent(planner=planner)
        result = await agent.run(page=_FakePage(), instruction="do it", runtime_results={})
        return calls, result

    calls, result = asyncio.run(run_check())

    assert result.success is False
    assert len(calls) == 3
    assert [diagnostic.message for diagnostic in result.diagnostics] == ["boom 1", "boom 2", "boom 3"]
    assert "after two repairs" in result.message


@pytest.mark.asyncio
async def test_recording_runtime_agent_repair_payload_has_traceback_and_omits_unknown_failure_analysis():
    calls = []

    async def planner(payload):
        calls.append(payload)
        if "repair" not in payload:
            return {
                "description": "Broken result write",
                "action_type": "run_python",
                "expected_effect": "extract",
                "code": (
                    "async def run(page, results):\n"
                    "    details = [{'name': 'paper'}]\n"
                    "    results.set('purchase_details', details)\n"
                    "    return details"
                ),
            }
        return {
            "description": "Return extracted result",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "purchase_details",
            "code": (
                "async def run(page, results):\n"
                "    details = [{'name': 'paper'}]\n"
                "    return details"
            ),
        }

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="extract purchase details",
        runtime_results={},
    )

    repair_payload = calls[1]["repair"]
    assert result.success is True
    assert "failure_analysis" not in repair_payload
    assert repair_payload["error"] == "'dict' object has no attribute 'set'"
    assert repair_payload["error_type"] == "AttributeError"
    assert "Traceback (most recent call last)" in repair_payload["traceback"]
    assert "results.set('purchase_details', details)" in repair_payload["traceback"]
    assert result.diagnostics[0].message == repair_payload["error"]


def test_recording_runtime_agent_sends_advisory_failure_hint_to_repair_planner():
    async def run_check():
        calls = []

        async def planner(payload):
            calls.append(payload)
            if "repair" not in payload:
                return {
                    "description": "Wait for brittle issue selector",
                    "action_type": "run_python",
                    "expected_effect": "extract",
                    "code": (
                        "async def run(page, results):\n"
                        "    raise TimeoutError('Page.wait_for_selector: Timeout 15000ms exceeded waiting for locator(\"[data-testid=issue-list]\")')"
                    ),
                }
            return {
                "description": "Scan issue links",
                "action_type": "run_python",
                "expected_effect": "none",
                "output_key": "latest_issue",
                "code": "async def run(page, results):\n    return {'latest_issue_title': 'Latest issue'}",
            }

        result = await RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="find the latest issue title",
            runtime_results={},
        )
        return calls, result

    calls, result = asyncio.run(run_check())

    repair_payload = calls[1]["repair"]
    assert result.success is True
    assert result.diagnostics[0].message.startswith("Page.wait_for_selector")
    assert repair_payload["error"].startswith("Page.wait_for_selector")
    assert repair_payload["failure_analysis"]["type"] == "selector_timeout"
    assert "hint" in repair_payload["failure_analysis"]
    assert "confidence" not in repair_payload["failure_analysis"]
    assert any(item["kind"] == "selector_retarget" for item in repair_payload["guidance"])
    assert result.diagnostics[0].raw["failure_analysis"]["type"] == "selector_timeout"


def test_recording_runtime_agent_sends_terminal_guidance_to_repair_planner():
    async def run_check():
        calls = []

        async def planner(payload):
            calls.append(payload)
            if "repair" not in payload:
                return {
                    "description": "Submit form",
                    "action_type": "run_python",
                    "expected_effect": "mixed",
                    "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
                    "code": "async def run(page, results):\n    return {'action_performed': True, 'action_type': 'click'}",
                }
            return {
                "description": "Submit form and verify terminal state",
                "action_type": "run_python",
                "expected_effect": "mixed",
                "output_key": "created_record",
                "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
                "code": (
                    "async def run(page, results):\n"
                    "    return {'created_record': {'id': 'REQ-1', 'status': 'Submitted'}, "
                    "'terminal_evidence': {'type': 'row_exists', 'source': 'observed', 'observed': True}}"
                ),
            }

        result = await RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="submit the form and create the record",
            runtime_results={},
        )
        return calls, result

    calls, result = asyncio.run(run_check())

    repair_payload = calls[1]["repair"]
    assert result.success is True
    assert "required terminal evidence" in repair_payload["error"]
    assert any(item["kind"] == "terminal_effect" for item in repair_payload["guidance"])


def test_runtime_ai_preserve_signal_is_ignored_for_deterministic_form_code():
    signals = _merge_runtime_ai_signal(
        {},
        {
            "preserve_runtime_ai": True,
            "semantic_intent": "Use visible form controls and submit the form",
            "code": (
                "async def run(page, results):\n"
                "    await page.get_by_label('Name').fill('Ada')\n"
                "    await page.get_by_role('button', name='Submit').click()\n"
            ),
        },
    )

    assert "runtime_ai" not in signals


def test_runtime_ai_preserve_signal_is_kept_for_best_matching_candidate_selection():
    signals = _merge_runtime_ai_signal(
        {},
        {
            "preserve_runtime_ai": True,
            "semantic_intent": "select_best_matching_candidate",
            "code": "async def run(page, results):\n    return {'selected': True}\n",
        },
    )

    assert signals["runtime_ai"]["preserve"] is True


def test_recording_runtime_agent_does_not_preserve_failed_browser_mutation_attempts():
    plans = [
        {
            "description": "Submit form",
            "action_type": "run_python",
            "expected_effect": "mixed",
            "code": "async def run(page, results):\n    await page.get_by_role('button', name='Submit').click()\n    raise RuntimeError('terminal state not observed')",
        },
        {
            "description": "Verify result",
            "action_type": "run_python",
            "expected_effect": "none",
            "output_key": "created_record",
            "code": "async def run(page, results):\n    return {'id': 'ID-1'}",
        },
    ]
    calls = []

    async def planner(_payload):
        return plans[len(calls)]

    async def executor(_page, plan, _runtime_results):
        calls.append(plan)
        if len(calls) == 1:
            return {"success": False, "error": "terminal state not observed", "output": {"submitted": True}}
        return {"success": True, "output": {"id": "ID-1"}}

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner, executor=executor).run(
            page=_FakePage(),
            instruction="submit the form and verify the created record",
            runtime_results={},
        )
    )

    assert result.success is True
    assert len(result.traces) == 1
    assert "recovered_attempt" not in result.traces[0].signals
    assert result.trace == result.traces[0]


def test_recording_runtime_agent_repairs_invalid_planner_output():
    calls = []

    async def planner(payload):
        calls.append(payload)
        if "repair" not in payload:
            raise ValueError("Recording planner must return Python code defining async def run(page, results)")
        return {
            "description": "Verify result after planner repair",
            "action_type": "run_python",
            "expected_effect": "none",
            "output_key": "verified",
            "code": "async def run(page, results):\n    return {'status': 'ok'}",
        }

    result = asyncio.run(
        RecordingRuntimeAgent(planner=planner).run(
            page=_FakePage(),
            instruction="click submit and verify result",
            runtime_results={},
        )
    )

    assert result.success is True
    assert len(calls) == 2
    assert calls[1]["repair"]["error"].startswith("Recording planner must return Python code")
    assert result.diagnostics[0].raw["error_type"] == "ValueError"


def test_detail_extract_intent_excludes_open_filter_navigation_tasks():
    assert _instruction_is_detail_extract_only("提取当前详情页中的供应商和金额")
    assert not _instruction_is_detail_extract_only("筛选合同并打开详情页读取供应商和金额")
    assert not _instruction_is_detail_extract_only("navigate to the contract page and read the amount")


def test_detail_extract_intent_does_not_strip_context_or_negative_guardrails():
    instruction = """
    你正在执行 RPA 任务。系统已经完成登录，并已导航到起始页面。
    请只执行下面的业务任务，不要重新登录，不要把打开当前页面当作完成。
    当前已经在详情页。请从页面字段中提取供应商、金额和有效期，并在回答中列出。
    """

    assert not _instruction_is_detail_extract_only(instruction)
    assert _instruction_is_detail_extract_only("当前已经在详情页。请从页面字段中提取供应商、金额和有效期，并在回答中列出。")


def test_detail_extract_plan_combines_multiple_detail_views():
    snapshot = {
        "detail_views": [
            {"section_title": "基本信息", "fields": [{"label": "编号", "value": "A-1", "visible": True}]},
            {"section_title": "供应商", "fields": [{"label": "名称", "value": "Acme", "visible": True}]},
        ]
    }

    plan = _build_detail_extract_plan("提取当前详情页中的字段", snapshot)

    assert plan is not None
    assert [field["label"] for field in plan["fields"]] == ["编号", "名称"]
    assert plan["section_title"] == "基本信息 / 供应商"


def test_normalize_generated_playwright_code_preserves_unsupported_filter_kwargs():
    code = (
        "async def run(page, results):\n"
        "    loc = page.locator('input').filter(has_attribute='placeholder', has_text='')\n"
        "    other = page.locator('input').filter(has_attribute='disabled')\n"
    )

    normalized = _normalize_generated_playwright_code(code)

    assert "has_attribute='placeholder'" in normalized
    assert "has_attribute='disabled'" in normalized


@pytest.mark.asyncio
async def test_recording_runtime_agent_payload_includes_structured_regions(monkeypatch):
    calls = []

    async def planner(payload):
        calls.append(payload)
        return {
            "description": "Extract buyer and value",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "buyer_info",
            "code": "async def run(page, results):\n    return {'buyer': '李雨晨', 'amount': '1000'}",
        }

    snapshot = {
        "url": "https://example.test/detail",
        "title": "Detail Page",
        "content_nodes": [
            {
                "node_id": "label-1",
                "container_id": "detail-card",
                "semantic_kind": "label",
                "role": "label",
                "text": "购买人",
                "bbox": {"x": 20, "y": 20, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "购买人"},
                "element_snapshot": {"tag": "label", "text": "购买人"},
            },
            {
                "node_id": "value-1",
                "container_id": "detail-card",
                "semantic_kind": "field_value",
                "role": "",
                "text": "李雨晨",
                "bbox": {"x": 120, "y": 20, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "李雨晨"},
                "element_snapshot": {"tag": "span", "text": "李雨晨", "class": "field-value"},
            },
        ],
        "containers": [
            {
                "container_id": "detail-card",
                "frame_path": [],
                "container_kind": "card",
                "name": "单据基本信息",
                "summary": "",
                "child_actionable_ids": [],
                "child_content_ids": ["label-1", "value-1"],
            }
        ],
        "actionable_nodes": [],
        "frames": [],
    }

    async def fake_build_page_snapshot(_page, _build_frame_path):
        return snapshot

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="提取单据基本信息中的购买人和金额",
        runtime_results={},
    )

    assert result.success is True
    region = _find_region_with_pair(calls[0]["snapshot"], "购买人", "李雨晨")
    assert region is not None
    assert "region_catalogue" in calls[0]["snapshot"]


@pytest.mark.asyncio
async def test_recording_runtime_agent_forwards_structured_views_to_planner(monkeypatch):
    snapshot = {
        "url": "https://example.test/grid",
        "title": "Grid",
        "frames": [],
        "actionable_nodes": [],
        "content_nodes": [],
        "containers": [],
        "table_views": [
            {
                "kind": "table_view",
                "columns": [{"index": 0, "column_id": "col_25", "header": "文件名称", "role": "file_link"}],
                "rows": [
                    {
                        "index": 0,
                        "cells": [
                            {
                                "column_id": "col_25",
                                "column_header": "文件名称",
                                "text": "File_189.xlsx",
                                "actions": [],
                            }
                        ],
                    }
                ],
            }
        ],
        "detail_views": [],
    }
    calls = []

    async def fake_build_page_snapshot(_page, _build_frame_path):
        return snapshot

    async def fake_planner(payload):
        calls.append(payload)
        return {
            "description": "Extract grid",
            "action_type": "run_python",
            "expected_effect": "extract",
            "code": "async def run(page, results):\n    return 'ok'",
            "output_key": "grid_result",
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    agent = RecordingRuntimeAgent(planner=fake_planner)
    result = await agent.run(page=_FakePage(), instruction="提取第一行文件名称", runtime_results={})

    assert result.success is True
    assert calls[0]["snapshot"]["table_views"][0]["columns"][0]["header"] == "文件名称"


@pytest.mark.asyncio
async def test_recording_runtime_agent_forwards_instruction_into_snapshot_compaction(monkeypatch):
    compact_calls = []
    planner_calls = []

    def fake_compact_recording_snapshot(snapshot, instruction, *, char_budget=20000):
        compact_calls.append(
            {
                "instruction": instruction,
                "snapshot_url": snapshot.get("url"),
                "char_budget": char_budget,
            }
        )
        return {
            "mode": "clean_snapshot",
            "url": snapshot.get("url", ""),
            "title": snapshot.get("title", ""),
            "expanded_regions": [],
            "sampled_regions": [],
            "region_catalogue": [],
        }

    async def planner(payload):
        planner_calls.append(payload)
        if "repair" not in payload:
            return {
                "description": "Broken first pass",
                "action_type": "run_python",
                "code": "async def run(page, results):\n    raise RuntimeError('boom')",
            }
        return {
            "description": "Repair pass",
            "action_type": "run_python",
            "output_key": "done",
            "code": "async def run(page, results):\n    return {'ok': True}",
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.compact_recording_snapshot", fake_compact_recording_snapshot)
    async def fake_build_page_snapshot(*_args, **_kwargs):
        return {
            "url": "https://example.test/detail",
            "title": "Detail Page",
            "frames": [],
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="提取单据基本信息中的购买人和金额",
        runtime_results={},
    )

    assert result.success is True
    assert [call["instruction"] for call in compact_calls] == [
        "提取单据基本信息中的购买人和金额",
        "提取单据基本信息中的购买人和金额",
    ]
    assert planner_calls[0]["snapshot"]["url"] == "https://example.test/detail"
    assert planner_calls[1]["repair"]["snapshot_after_failure"]["url"] == "https://example.test/detail"


@pytest.mark.asyncio
async def test_recording_runtime_agent_dumps_initial_snapshot_when_debug_dir_is_enabled(monkeypatch):
    raw_snapshot = {
        "url": "https://github.com/trending",
        "title": "Trending",
        "content_nodes": [{"text": "Claude Code SDK"}],
        "actionable_nodes": [{"role": "link", "text": "anthropics/claude-code"}],
        "containers": [],
        "frames": [],
    }
    compact_snapshot = {
        "mode": "clean_snapshot",
        "url": "https://github.com/trending",
        "title": "Trending",
        "expanded_regions": [{"title": "Claude Code SDK"}],
        "sampled_regions": [],
        "region_catalogue": [],
    }

    async def fake_build_page_snapshot(*_args, **_kwargs):
        return raw_snapshot

    def fake_compact_recording_snapshot(_snapshot, _instruction, *, char_budget=20000):
        return compact_snapshot

    async def planner(_payload):
        return {
            "description": "Open related project",
            "action_type": "run_python",
            "expected_effect": "none",
            "code": "async def run(page, results):\n    return {'opened': True}",
        }

    debug_dir = Path(__file__).resolve().parents[1] / "recording_debug_test_output"
    debug_dir.mkdir(exist_ok=True)
    for pattern in ("*-snapshot-*.json", "*-attempt-*.json", "*-code-*.py", "snapshot-*.json", "attempt-*.json", "code-*.py", "recording-snapshot-*.json", "recording-attempt-*.json", "recording-code-*.py"):
        for existing in debug_dir.glob(pattern):
            existing.unlink()

    monkeypatch.setenv("RPA_RECORDING_DEBUG_SNAPSHOT_DIR", str(debug_dir))
    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)
    monkeypatch.setattr("backend.rpa.recording_runtime_agent.compact_recording_snapshot", fake_compact_recording_snapshot)

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="打开和Claudecode最相关的项目",
        runtime_results={"previous": "value"},
        debug_context={"session_id": "sess-debug-1"},
    )

    session_debug_dir = debug_dir / "sess-debug-1"
    files = list(session_debug_dir.glob("*-snapshot-*.json"))
    assert result.success is True
    assert len(files) == 1
    assert not list(debug_dir.glob("*-snapshot-*.json"))
    assert files[0].name == "001-initial-snapshot-打开和Claudecode最相关的项目.json"

    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["stage"] == "initial"
    assert payload["debug_context"]["session_id"] == "sess-debug-1"
    assert payload["instruction"] == "打开和Claudecode最相关的项目"
    assert payload["raw_snapshot"] == raw_snapshot
    assert payload["compact_snapshot"] == compact_snapshot
    assert payload["snapshot_metrics"]["raw_snapshot"]["content_node_count"] == 1
    assert payload["snapshot_metrics"]["compact_snapshot"]["mode"] == "clean_snapshot"
    assert payload["snapshot_comparison"]["classification"] == "present_in_both"
    assert payload["runtime_results"] == {"previous": "value"}
    for pattern in ("*-snapshot-*.json", "*-attempt-*.json", "*-code-*.py", "snapshot-*.json", "attempt-*.json", "code-*.py", "recording-snapshot-*.json", "recording-attempt-*.json", "recording-code-*.py"):
        for file in session_debug_dir.glob(pattern):
            file.unlink()
    if session_debug_dir.exists():
        session_debug_dir.rmdir()


@pytest.mark.asyncio
async def test_recording_runtime_agent_dumps_repair_snapshot_after_first_failure(monkeypatch):
    calls = []
    raw_snapshots = [
        {
            "url": "https://github.com/trending",
            "title": "Trending",
            "content_nodes": [{"text": "Claude Code"}],
            "actionable_nodes": [],
            "containers": [],
            "frames": [],
        },
        {
            "url": "https://github.com/search",
            "title": "Search",
            "content_nodes": [],
            "actionable_nodes": [],
            "containers": [],
            "frames": [],
        },
    ]

    async def fake_build_page_snapshot(*_args, **_kwargs):
        return raw_snapshots.pop(0)

    def fake_compact_recording_snapshot(snapshot, _instruction, *, char_budget=20000):
        return {
            "mode": "clean_snapshot",
            "url": snapshot.get("url", ""),
            "title": snapshot.get("title", ""),
            "expanded_regions": [],
            "sampled_regions": [],
            "region_catalogue": [],
        }

    async def planner(payload):
        calls.append(payload)
        if "repair" not in payload:
            return {
                "description": "Broken search strategy",
                "action_type": "run_python",
                "expected_effect": "none",
                "code": (
                    "async def run(page, results):\n"
                    "    raise TimeoutError('Locator.click: Timeout 60000ms exceeded\\n"
                    "Call log:\\n  - waiting for get_by_placeholder(\"Search or jump to…\")')"
                ),
            }
        return {
            "description": "Recovered",
            "action_type": "run_python",
            "expected_effect": "none",
            "code": "async def run(page, results):\n    return {'ok': True}",
        }

    debug_dir = Path(__file__).resolve().parents[1] / "recording_debug_test_output"
    debug_dir.mkdir(exist_ok=True)
    for pattern in ("*-snapshot-*.json", "*-attempt-*.json", "*-code-*.py", "snapshot-*.json", "attempt-*.json", "code-*.py", "recording-snapshot-*.json", "recording-attempt-*.json", "recording-code-*.py"):
        for existing in debug_dir.glob(pattern):
            existing.unlink()

    monkeypatch.setenv("RPA_RECORDING_DEBUG_SNAPSHOT_DIR", str(debug_dir))
    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)
    monkeypatch.setattr("backend.rpa.recording_runtime_agent.compact_recording_snapshot", fake_compact_recording_snapshot)

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="打开和Claudecode最相关的项目",
        runtime_results={},
    )

    files = sorted(debug_dir.glob("*-snapshot-*.json"))
    attempt_files = sorted(debug_dir.glob("*-attempt-*.json"))
    code_files = sorted(debug_dir.glob("*-code-*.py"))
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    repair_payload = next(item for item in payloads if item["stage"] == "repair")
    attempt_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in attempt_files]
    failed_attempt = next(item for item in attempt_payloads if item["stage"] == "initial_attempt")

    assert result.success is True
    assert len(files) == 2
    assert len(attempt_files) == 2
    assert len(code_files) == 2
    assert [path.name for path in files] == [
        "001-initial-snapshot-打开和Claudecode最相关的项目.json",
        "003-repair-snapshot-打开和Claudecode最相关的项目.json",
    ]
    assert [path.name for path in attempt_files] == [
        "002-initial_attempt-attempt-Broken_search_strategy.json",
        "004-repair_attempt-attempt-Recovered.json",
    ]
    assert [path.name for path in code_files] == [
        "002-initial_attempt-code-Broken_search_strategy.py",
        "004-repair_attempt-code-Recovered.py",
    ]
    assert calls[1]["repair"]["snapshot_after_failure"]["url"] == "https://github.com/search"
    assert repair_payload["compact_snapshot"]["url"] == "https://github.com/search"
    assert repair_payload["error"].startswith("Locator.click")
    assert repair_payload["failure_analysis"]["type"] == "selector_timeout"
    assert failed_attempt["plan"]["description"] == "Broken search strategy"
    assert failed_attempt["generated_code"].startswith("async def run")
    assert failed_attempt["execution_result"]["success"] is False
    assert failed_attempt["failure_analysis"]["type"] == "selector_timeout"
    for file in files + attempt_files + code_files:
        file.unlink()


def test_classify_recording_failure_returns_unknown_without_hint_for_unseen_errors():
    analysis = _classify_recording_failure("some new browser error shape")

    assert analysis == {"type": "unknown"}


def test_classify_recording_failure_identifies_selector_timeout_without_confidence():
    analysis = _classify_recording_failure(
        'Page.wait_for_selector: Timeout 15000ms exceeded waiting for locator("a.Link--primary[href*=issues]")'
    )

    assert analysis["type"] == "selector_timeout"
    assert "hint" in analysis
    assert "confidence" not in analysis


def test_classify_recording_failure_identifies_actionability_failure_before_selector_timeout():
    analysis = _classify_recording_failure(
        "Locator.fill: Timeout 60000ms exceeded\n"
        "Call log:\n"
        "  - waiting for locator(\"#kw\")\n"
        "    - locator resolved to <input id=\"kw\" />\n"
        "  - attempting fill action\n"
        "    - element is not visible\n"
        "    - waiting for element to be visible, enabled and editable"
    )

    assert analysis["type"] == "element_not_visible_or_not_editable"
    assert "hint" in analysis
    assert "confidence" not in analysis


@pytest.mark.asyncio
async def test_recording_runtime_agent_repair_payload_includes_page_after_failure():
    calls = []

    async def planner(payload):
        calls.append(payload)
        if "repair" not in payload:
            return {
                "description": "Open search engine and fill hidden input",
                "action_type": "run_python",
                "expected_effect": "mixed",
                "code": (
                    "async def run(page, results):\n"
                    "    await page.goto('https://www.baidu.com')\n"
                    "    raise RuntimeError('Locator.fill: Timeout 60000ms exceeded; element is not visible')"
                ),
            }
        return {
            "description": "Search by visible input",
            "action_type": "run_python",
            "expected_effect": "navigate",
            "output_key": "search_result",
            "code": (
                "async def run(page, results):\n"
                "    await page.goto('https://www.baidu.com/s?wd=pi-hole%2Fpi-hole')\n"
                "    return {'url': page.url}"
            ),
        }

    page = _FakePage()
    page.url = "https://github.com/pi-hole/pi-hole"
    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction='填写"pi-hole/pi-hole"到搜索框点击搜索',
        runtime_results={},
    )

    repair = calls[1]["repair"]
    assert result.success is True
    assert calls[1]["page"]["url"] == "https://github.com/pi-hole/pi-hole"
    assert repair["page_after_failure"]["url"] == "https://www.baidu.com"
    assert repair["snapshot_after_failure"]["url"] == "https://www.baidu.com"
    assert repair["failure_analysis"]["type"] == "element_not_visible_or_not_editable"


@pytest.mark.asyncio
async def test_recording_runtime_agent_auto_navigates_when_open_command_returns_target_url():
    async def planner(_payload):
        return {
            "description": "Find highest-star repo",
            "action_type": "run_python",
            "expected_effect": "navigate",
            "output_key": "selected_project",
            "code": (
                "async def run(page, results):\n"
                "    return {'name': 'ruvnet/RuView', 'url': 'https://github.com/ruvnet/RuView', 'stars': 47505}"
            ),
        }

    page = _FakePage()
    page.url = "https://github.com/trending"
    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="打开star数最多的项目",
        runtime_results={},
    )

    assert result.success is True
    assert page.url == "https://github.com/ruvnet/RuView"
    assert result.trace.after_page.url == "https://github.com/ruvnet/RuView"
    assert result.trace.ai_execution.output["url"] == "https://github.com/ruvnet/RuView"


@pytest.mark.asyncio
async def test_recording_runtime_agent_keeps_page_when_extract_command_returns_url():
    async def planner(_payload):
        return {
            "description": "Find highest-star repo",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "selected_project",
            "code": (
                "async def run(page, results):\n"
                "    return {'name': 'ruvnet/RuView', 'url': 'https://github.com/ruvnet/RuView', 'stars': 47505}"
            ),
        }

    page = _FakePage()
    page.url = "https://github.com/trending"
    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="找到star数最多的项目",
        runtime_results={},
    )

    assert result.success is True
    assert page.url == "https://github.com/trending"
    assert result.trace.after_page.url == "https://github.com/trending"
    assert result.output["url"] == "https://github.com/ruvnet/RuView"


@pytest.mark.asyncio
async def test_recording_runtime_agent_restores_page_after_extract_uses_machine_endpoint():
    async def planner(_payload):
        return {
            "description": "Extract latest issue title",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "latest_issue",
            "code": (
                "async def run(page, results):\n"
                "    await page.goto('https://api.github.com/repos/ruvnet/RuView/issues?per_page=1')\n"
                "    return {'title': 'Latest issue'}"
            ),
        }

    page = _FakePage()
    page.url = "https://github.com/ruvnet/RuView"
    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="find the latest issue title",
        runtime_results={},
    )

    assert result.success is True
    assert page.url == "https://github.com/ruvnet/RuView"
    assert result.trace.after_page.url == "https://github.com/ruvnet/RuView"
    assert result.output == {"title": "Latest issue"}


@pytest.mark.asyncio
async def test_recording_runtime_agent_restores_to_last_user_page_after_extract_api_fallback():
    async def planner(_payload):
        return {
            "description": "Extract latest issue title",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "latest_issue",
            "code": (
                "async def run(page, results):\n"
                "    await page.goto('https://github.com/ruvnet/RuView/issues?q=is%3Aissue')\n"
                "    await page.goto('https://api.github.com/repos/ruvnet/RuView/issues?per_page=1')\n"
                "    return {'title': 'Latest issue'}"
            ),
        }

    page = _FakePage()
    page.url = "https://github.com/ruvnet/RuView"
    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="find the latest issue title",
        runtime_results={},
    )

    assert result.success is True
    assert page.url == "https://github.com/ruvnet/RuView/issues?q=is%3Aissue"
    assert result.trace.after_page.url == "https://github.com/ruvnet/RuView/issues?q=is%3Aissue"
    assert result.trace.ai_execution.output == {"title": "Latest issue"}


@pytest.mark.asyncio
async def test_recording_runtime_agent_accepts_empty_extract_output_without_forcing_repair():
    async def planner(_payload):
        return {
            "description": "Extract latest issue title",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "latest_issue",
            "code": "async def run(page, results):\n    return {'latest_issue_title': None, 'latest_issue_link': None}",
        }

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="find the latest issue title",
        runtime_results={},
    )

    assert result.success is True
    assert result.trace.ai_execution.repair_attempted is False
    assert result.output == {"latest_issue_title": None, "latest_issue_link": None}
    assert result.diagnostics == []


@pytest.mark.asyncio
async def test_recording_runtime_agent_accepts_empty_extract_when_plan_explicitly_allows_empty():
    async def planner(_payload):
        return {
            "description": "Collect optional notifications",
            "action_type": "run_python",
            "expected_effect": "extract",
            "allow_empty_output": True,
            "output_key": "notifications",
            "code": "async def run(page, results):\n    return {'notifications': []}",
        }

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="collect notifications if any, empty is acceptable",
        runtime_results={},
    )

    assert result.success is True
    assert result.output == {"notifications": []}


def test_recording_runtime_agent_records_download_signal_from_ai_code():
    async def run_check():
        async def planner(_payload):
            return {
                "description": "Download report",
                "action_type": "run_python",
                "expected_effect": "click",
                "output_key": "download_report",
                "code": (
                    "async def run(page, results):\n"
                    "    await page.trigger_download('report.xlsx')\n"
                    "    return {'action_performed': True}"
                ),
            }

        page = _FakePage()
        result = await RecordingRuntimeAgent(planner=planner).run(
            page=page,
            instruction="download the report",
            runtime_results={},
        )
        return result

    result = asyncio.run(run_check())

    assert result.success is True
    assert result.trace.signals["download"]["filename"] == "report.xlsx"
    assert result.trace.signals["download"]["count"] == 1


def test_recording_runtime_agent_waits_briefly_for_click_triggered_download():
    async def run_check():
        async def planner(_payload):
            return {
                "description": "Click table row column action",
                "action_type": "run_python",
                "expected_effect": "none",
                "output_key": "table_row_action",
                "code": (
                    "async def run(page, results):\n"
                    "    page.trigger_download_later('delayed-report.xlsx')\n"
                    "    await page.locator('tbody tr').nth(0).click()\n"
                    "    return {'action_performed': True}"
                ),
            }

        page = _FakePage()
        result = await RecordingRuntimeAgent(planner=planner).run(
            page=page,
            instruction="click the first file name in the export table",
            runtime_results={},
        )
        return result

    result = asyncio.run(run_check())

    assert result.success is True
    assert result.trace.signals["download"]["filename"] == "delayed-report.xlsx"
    assert result.trace.output_key == "table_row_action"


@pytest.mark.asyncio
async def test_recording_runtime_agent_rejects_open_command_without_navigation_evidence_or_url():
    async def planner(_payload):
        return {
            "description": "Broken open",
            "action_type": "run_python",
            "expected_effect": "navigate",
            "code": "async def run(page, results):\n    return {'ok': True}",
        }

    page = _FakePage()
    page.url = "https://github.com/trending"
    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="打开star数最多的项目",
        runtime_results={},
    )

    assert result.success is False
    assert page.url == "https://github.com/trending"
    assert result.trace is None
    assert "navigation" in result.diagnostics[-1].message.lower()


def test_parse_json_object_accepts_fenced_json():
    payload = {
        "description": "Run",
        "action_type": "run_python",
        "code": "async def run(page, results):\n    return {'ok': True}",
    }

    parsed = _parse_json_object("prefix\n```json\n" + json.dumps(payload) + "\n```")

    assert parsed["description"] == "Run"
    assert "async def run(page, results)" in parsed["code"]


def test_parse_json_object_accepts_fenced_json_with_trailing_prose():
    payload = {
        "description": "Run",
        "action_type": "run_python",
        "code": "async def run(page, results):\n    return {'ok': True}",
    }

    parsed = _parse_json_object("```json\n" + json.dumps(payload) + "\n```\nThis is the plan.")

    assert parsed["description"] == "Run"
    assert "async def run(page, results)" in parsed["code"]


def test_parse_json_object_accepts_first_valid_object_before_trailing_text():
    payload = {
        "description": "Run",
        "action_type": "run_python",
        "code": "async def run(page, results):\n    return {'ok': True}",
    }

    parsed = _parse_json_object(json.dumps(payload) + "\nI will now execute this step with {notes}.")

    assert parsed["description"] == "Run"
    assert "async def run(page, results)" in parsed["code"]


def test_parse_json_object_skips_prose_example_before_valid_plan():
    payload = {
        "description": "Run actual plan",
        "action_type": "run_python",
        "code": "async def run(page, results):\n    return {'ok': True}",
    }

    parsed = _parse_json_object('Example: {"foo": "bar"}\nPlan:\n' + json.dumps(payload))

    assert parsed["description"] == "Run actual plan"
    assert "async def run(page, results)" in parsed["code"]


def test_parse_json_object_rejects_invalid_primary_plan_before_later_example():
    invalid_primary = {
        "description": "Invalid primary plan",
        "action_type": "run_python",
        "code": "print('missing async runner')",
    }
    later_example = {
        "description": "Example only",
        "action_type": "run_python",
        "code": "async def run(page, results):\n    return {'example': True}",
    }

    with pytest.raises(ValueError, match="async def run"):
        _parse_json_object(json.dumps(invalid_primary) + "\nExample fallback:\n" + json.dumps(later_example))


def test_snapshot_plan_fields_accepts_mapping_values_and_preserves_list_fields():
    list_fields = [{"label": "Owner", "value": "Ada", "visible": False}]

    assert _snapshot_plan_fields({"fields": {"Project": "Apollo"}}) == [
        {"label": "Project", "value": "Apollo"}
    ]
    assert _snapshot_plan_fields({"fields": list_fields}) == list_fields


def test_snapshot_plan_fields_flattens_nested_label_value_objects():
    assert _snapshot_plan_fields(
        {
            "fields": {
                "contract_number": {
                    "label": "合同编号",
                    "value": "CT-001",
                }
            }
        }
    ) == [{"label": "contract_number", "value": "CT-001", "observed_label": "合同编号"}]


def test_extract_snapshot_enrichment_backfills_observed_label_from_detail_value():
    result = {
        "signals": {
            "extract_snapshot": {
                "fields": [
                    {
                        "label": "compliance_summary",
                        "value": "Must keep audit logs.",
                        "replay_required": True,
                    }
                ]
            }
        }
    }
    snapshot = {
        "detail_views": [
            {
                "fields": [
                    {
                        "label": "Compliance clause",
                        "value": "Must keep audit logs.",
                    }
                ]
            }
        ]
    }

    enriched = recording_runtime_agent._enrich_extract_snapshot_result_with_replay_evidence(result, snapshot)

    field = enriched["signals"]["extract_snapshot"]["fields"][0]
    assert field["observed_label"] == "Compliance clause"


def test_recover_failed_side_effect_requires_correlated_new_table_row_postcondition():
    before_snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Record No"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Record No", "text": "REQ-001"},
                            {"column_header": "Status", "text": "submitted"},
                        ]
                    }
                ],
            }
        ]
    }
    after_snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Record No"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Record No", "text": "REQ-001"},
                            {"column_header": "Status", "text": "submitted"},
                        ]
                    },
                    {
                        "cells": [
                            {"column_header": "Record No", "text": "REQ-002"},
                            {"column_header": "Status", "text": "pending"},
                        ]
                    },
                ],
            }
        ]
    }

    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            "input_bindings": {"record_no": {"default": "REQ-002"}},
        },
        result={"success": False, "error": "selector timed out after the click"},
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
    )

    assert recovered is not None
    recovered_plan, recovered_result = recovered
    assert recovered_result["success"] is True
    assert recovered_result["signals"]["recovered_attempt"]["ignore_errors"] is True
    assert recovered_plan["postcondition"]["key"] == {"Record No": "REQ-002"}
    assert recovered_plan["postcondition"]["expect"] == {"Status": "pending"}


def test_terminal_recovery_carries_snapshot_row_selector_hint():
    before_snapshot = {"table_views": []}
    after_snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Order No"}, {"header": "Status"}],
                "rows": [
                    {
                        "locator_hints": [
                            {
                                "kind": "playwright",
                                "expression": "page.locator('.el-table__body-wrapper tbody tr').nth(0)",
                            }
                        ],
                        "cells": [
                            {"column_header": "Order No", "text": "PO-002"},
                            {"column_header": "Status", "text": "pending"},
                        ],
                    }
                ],
            }
        ]
    }

    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            "input_bindings": {"order_no": {"default": "PO-002"}},
        },
        result={"success": False, "error": "post action assertion failed"},
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
    )

    assert recovered is not None
    assert recovered[0]["postcondition"]["row_selector"] == ".el-table__body-wrapper tbody tr"


def test_terminal_recovery_keeps_headers_needed_by_expectations_after_wide_columns():
    after_snapshot = {
        "table_views": [
            {
                "columns": [
                    {"header": "Supplier No"},
                    {"header": "Name"},
                    {"header": "Category"},
                    {"header": "Region"},
                    {"header": "Risk"},
                    {"header": "Rating"},
                    {"header": "Contact"},
                    {"header": "Phone"},
                    {"header": "Status"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Supplier No", "text": "SUP-002"},
                            {"column_header": "Name", "text": "Acme"},
                            {"column_header": "Category", "text": "Audit"},
                            {"column_header": "Region", "text": "North"},
                            {"column_header": "Risk", "text": "medium"},
                            {"column_header": "Rating", "text": "B"},
                            {"column_header": "Contact", "text": "Alice"},
                            {"column_header": "Phone", "text": "139-0000"},
                            {"column_header": "Status", "text": "active"},
                        ],
                    }
                ],
            }
        ]
    }

    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("field_value_equals", kind="record_updated"),
            "input_bindings": {"supplier_no": {"default": "SUP-002"}},
        },
        result={"success": False, "error": "post action assertion failed", "output": {"status": "active"}},
        before_snapshot={
            "table_views": [
                {
                    "columns": after_snapshot["table_views"][0]["columns"],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Supplier No", "text": "SUP-002"},
                                {"column_header": "Name", "text": "Acme"},
                                {"column_header": "Category", "text": "Audit"},
                                {"column_header": "Region", "text": "North"},
                                {"column_header": "Risk", "text": "medium"},
                                {"column_header": "Rating", "text": "B"},
                                {"column_header": "Contact", "text": ""},
                                {"column_header": "Phone", "text": ""},
                                {"column_header": "Status", "text": "inactive"},
                            ],
                        }
                    ],
                }
            ]
        },
        after_snapshot=after_snapshot,
    )

    assert recovered is not None
    headers = recovered[0]["postcondition"]["table_headers"]
    assert "Supplier No" in headers
    assert "Status" in headers


def test_successful_action_can_recover_terminal_row_from_current_snapshot():
    recovery = recording_runtime_agent.current_snapshot_terminal_postcondition(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            "input_bindings": {"record_no": {"default": "REQ-002"}},
        },
        result={"success": True, "output": {"record_no": "REQ-002", "action_performed": True}},
        snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Record No"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Record No", "text": "REQ-001"},
                                {"column_header": "Status", "text": "submitted"},
                            ]
                        },
                        {
                            "cells": [
                                {"column_header": "Record No", "text": "REQ-002"},
                                {"column_header": "Status", "text": "pending"},
                            ]
                        },
                    ],
                }
            ]
        },
    )

    assert recovery["postcondition"]["key"] == {"Record No": "REQ-002"}
    assert recovery["evidence"][0]["type"] == "row_exists"


def test_current_snapshot_recovery_does_not_replace_required_navigation_with_row_presence():
    recovery = recording_runtime_agent.current_snapshot_terminal_postcondition(
        plan={
            "action_type": "run_python",
            "expected_effect": "navigate",
            "terminal_contract": _required_terminal_contract("url_changed", "field_value_equals", kind="state_change"),
            "input_bindings": {"record_no": {"default": "REQ-002"}},
        },
        result={"success": True, "output": {"record_no": "REQ-002", "action_performed": True}},
        snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Record No"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Record No", "text": "REQ-002"},
                                {"column_header": "Status", "text": "pending"},
                            ]
                        }
                    ],
                }
            ]
        },
    )

    assert recovery == {}


def test_current_snapshot_recovery_accepts_explicit_table_postcondition_for_state_change():
    recovery = recording_runtime_agent.current_snapshot_terminal_postcondition(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="state_change"),
            "postcondition": {
                "kind": "table_row_exists",
                "key": {"Order No": "PO-002"},
                "expect": {"Status": "pending"},
            },
        },
        result={"success": True, "output": {"order_no": "PO-002", "status": "pending"}},
        snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Order No"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Order No", "text": "PO-002"},
                                {"column_header": "Status", "text": "pending"},
                            ]
                        }
                    ],
                }
            ]
        },
    )

    assert recovery["postcondition"]["key"] == {"Order No": "PO-002"}
    assert any(item["type"] == "row_exists" for item in recovery["evidence"])


def test_structural_terminal_recovery_is_detected_as_additional_evidence():
    original = {
        "effect": {
            "terminal_evidence": "url_changed",
            "terminal_evidence_items": [{"type": "url_changed"}],
        }
    }
    recovered = {
        "signals": {
            "terminal_evidence": [
                {"type": "row_exists", "source": "snapshot"},
                {"type": "field_value_equals", "source": "snapshot"},
            ]
        }
    }

    assert recording_runtime_agent._terminal_recovery_adds_structural_evidence(original, recovered)


def test_recover_failed_side_effect_rejects_row_exists_for_generic_state_change():
    before_snapshot = {"table_views": []}
    after_snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Record No"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Record No", "text": "REQ-002"},
                            {"column_header": "Status", "text": "pending"},
                        ]
                    }
                ],
            }
        ]
    }

    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="state_change"),
            "input_bindings": {"record_no": {"default": "REQ-002"}},
        },
        result={"success": False, "error": "failed before terminal action"},
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
    )

    assert recovered is None


def test_recover_failed_side_effect_requires_matching_contract_evidence_type():
    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_status_changed", "toast_visible", kind="state_change"),
            "input_bindings": {"order_no": {"default": "PO-001"}},
        },
        result={"success": False, "error": "approval click timed out"},
        before_snapshot={"table_views": []},
        after_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Order No"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Order No", "text": "PO-001"},
                                {"column_header": "Status", "text": "pending"},
                            ]
                        }
                    ],
                }
            ]
        },
    )

    assert recovered is None


def test_recovered_side_effect_does_not_override_failed_instruction_completion():
    snapshot_before = {"table_views": []}
    snapshot_after = {
        "table_views": [
            {
                "columns": [{"header": "Request No"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Request No", "text": "PR-001"},
                            {"column_header": "Status", "text": "submitted"},
                        ]
                    }
                ],
            }
        ]
    }

    recovered = asyncio.run(
        RecordingRuntimeAgent(planner=lambda _payload: None)._accept_recovered_side_effect(
            page=_FakePage(),
            instruction="create request PR-001 and then generate order PO-001",
            plan={
                "action_type": "run_python",
                "expected_effect": "mixed",
                "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
                "input_bindings": {"request_no": {"default": "PR-001"}},
            },
            result={
                "success": False,
                "error": "Instruction completion verification failed",
                "signals": {
                    "instruction_completion": {
                        "passed": False,
                        "missing_requirements": ["generate order PO-001"],
                    }
                },
            },
            before=RPAPageState(url="https://example.test/request/new", title="Example"),
            before_snapshot=snapshot_before,
            after_snapshot=snapshot_after,
            diagnostics=[],
            repair_attempted=True,
        )
    )

    assert recovered is None


def test_recovered_side_effect_does_not_preserve_failed_preconditions_without_postcondition():
    snapshot_before = {"table_views": []}
    snapshot_after = {
        "table_views": [
            {
                "columns": [{"header": "Order No"}, {"header": "Request No"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Order No", "text": "PO-001"},
                            {"column_header": "Request No", "text": "PR-001"},
                        ]
                    }
                ],
            }
        ]
    }
    precondition_plan = {
        "action_type": "run_python",
        "expected_effect": "mixed",
        "code": "async def run(page, results):\n    await page.get_by_role('button', name='Create PR').click()\n    raise RuntimeError('PR submitted but navigation timed out')",
    }
    recovered_plan = {
        "action_type": "run_python",
        "expected_effect": "mixed",
        "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
        "input_bindings": {"order_no": {"default": "PO-001"}, "request_no": {"default": "PR-001"}},
        "postcondition": {
            "kind": "table_row_exists",
            "source": "observed",
            "key": {"Order No": "PO-001"},
            "expect": {"Request No": "PR-001"},
        },
        "code": "async def run(page, results):\n    await page.get_by_role('button', name='Create PO').click()\n    raise RuntimeError('terminal check timed out')",
    }

    recovered = asyncio.run(
        RecordingRuntimeAgent(planner=lambda _payload: None)._accept_recovered_side_effect(
            page=_FakePage(),
            instruction="create request PR-001 and then generate order PO-001",
            plan=recovered_plan,
            result={"success": False, "error": "terminal check timed out"},
            before=RPAPageState(url="https://example.test/request/new", title="Example"),
            before_snapshot=snapshot_before,
            after_snapshot=snapshot_after,
            diagnostics=[],
            repair_attempted=True,
            precondition_plans=[precondition_plan],
        )
    )

    assert recovered is not None
    code = recovered.trace.ai_execution.code
    assert "_RPA_PRECONDITION_CODES" not in code
    assert "Create PR" not in code
    assert "Create PO" in code


def test_replayable_failed_preconditions_skip_side_effectful_failed_attempts():
    precondition_plan = {
        "action_type": "run_python",
        "expected_effect": "mixed",
        "code": "async def run(page, results):\n    await page.get_by_role('button', name='Create PR').click()\n    raise RuntimeError('PR submitted but navigation timed out')",
    }
    repair_plan = {
        "action_type": "run_python",
        "expected_effect": "mixed",
        "code": "async def run(page, results):\n    await page.get_by_role('button', name='Create PO').click()\n    return {'ok': True}",
    }
    fallback_trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Create PO",
        ai_execution=RPAAIExecution(code=repair_plan["code"]),
        postcondition={
            "kind": "table_row_exists",
            "key": {"Order No": "PO-001"},
            "expect": {"Request No": "PR-001"},
        },
    )

    trace = asyncio.run(
        RecordingRuntimeAgent(planner=lambda _payload: None)._trace_with_replayable_failed_preconditions(
            page=_FakePage(),
            instruction="create request PR-001 and then generate order PO-001",
            failed_plans=[precondition_plan],
            repair_plan=repair_plan,
            repair_result={"success": True, "output": {"ok": True}},
            before=RPAPageState(url="https://example.test/request/new", title="Example"),
            repair_snapshot={},
            fallback_trace=fallback_trace,
        )
    )

    code = trace.ai_execution.code
    assert "_RPA_PRECONDITION_CODES" not in code
    assert "Create PR" not in code
    assert "Create PO" in code
    assert trace.postcondition == fallback_trace.postcondition


def test_recover_failed_side_effect_rejects_uncorrelated_new_table_row():
    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
        },
        result={"success": False, "error": "selector timed out after the click"},
        before_snapshot={"table_views": []},
        after_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Record No"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Record No", "text": "REQ-UNRELATED"},
                                {"column_header": "Status", "text": "pending"},
                            ]
                        }
                    ],
                }
            ]
        },
    )

    assert recovered is None


def test_recover_failed_side_effect_requires_new_instruction_identifier_for_created_record():
    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            "input_bindings": {"source_contract": {"default": "CT-001"}},
        },
        result={"success": False, "error": "submit timed out after partially opening the source row"},
        before_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Contract No"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Contract No", "text": "CT-001"},
                                {"column_header": "Status", "text": "effective"},
                            ]
                        }
                    ],
                }
            ]
        },
        after_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Contract No"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Contract No", "text": "CT-001"},
                                {"column_header": "Status", "text": "effective"},
                            ]
                        },
                        {
                            "cells": [
                                {"column_header": "Contract No", "text": "CT-001"},
                                {"column_header": "Status", "text": "opened"},
                            ]
                        }
                    ],
                }
            ]
        },
        instruction="open contract CT-001 and create purchase request PR-002",
    )

    assert recovered is None


def test_recover_failed_side_effect_accepts_correlated_disappeared_table_row():
    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "description": "Complete task TASK-001",
            "input_bindings": {"task_id": {"default": "TASK-001"}},
            "terminal_contract": _required_terminal_contract("row_absent", kind="record_removed"),
        },
        result={"success": False, "error": "TASK-001 disappeared before verification completed"},
        before_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Task ID"}, {"header": "Status"}, {"header": "Action"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Task ID", "text": "TASK-001"},
                                {"column_header": "Status", "text": "pending"},
                                {"column_header": "Action", "text": "Approve"},
                            ]
                        }
                    ],
                }
            ]
        },
        after_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Task ID"}, {"header": "Status"}, {"header": "Action"}],
                    "rows": [],
                }
            ]
        },
    )

    assert recovered is not None
    recovered_plan, recovered_result = recovered
    assert recovered_plan["postcondition"]["kind"] == "table_row_absent"
    assert recovered_plan["postcondition"]["key"] == {"Task ID": "TASK-001"}
    assert recovered_plan["postcondition"]["expect"] == {"Status": "pending"}
    assert recovered_result["effect"]["terminal_evidence"] == "row_absent"


def test_recover_failed_side_effect_ignores_code_and_error_tokens_as_correlation_source():
    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "description": "Complete task TASK-001",
            "code": "async def run(page, results):\n    await page.get_by_text('TASK-001').click()",
            "terminal_contract": _required_terminal_contract("row_absent", kind="record_removed"),
        },
        result={"success": False, "error": "TASK-001 disappeared before verification completed"},
        before_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Task ID"}, {"header": "Status"}, {"header": "Action"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Task ID", "text": "TASK-001"},
                                {"column_header": "Status", "text": "pending"},
                                {"column_header": "Action", "text": "Approve"},
                            ]
                        }
                    ],
                }
            ]
        },
        after_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Task ID"}, {"header": "Status"}, {"header": "Action"}],
                    "rows": [],
                }
            ]
        },
    )

    assert recovered is None


def test_recover_failed_side_effect_rejects_disappeared_row_without_comparable_table():
    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "description": "Complete task TASK-001",
            "terminal_contract": _required_terminal_contract("row_absent", kind="record_removed"),
        },
        result={"success": False, "error": "TASK-001 disappeared before verification completed"},
        before_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Task ID"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Task ID", "text": "TASK-001"},
                                {"column_header": "Status", "text": "pending"},
                            ]
                        }
                    ],
                }
            ]
        },
        after_snapshot={"table_views": []},
    )

    assert recovered is None


def test_snapshot_diff_terminal_postcondition_detects_correlated_updated_row():
    recovery = recording_runtime_agent.snapshot_diff_terminal_postcondition(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "description": "Update supplier SUP-001 contact Alice 139-0000",
            "terminal_contract": _required_terminal_contract("field_value_equals", kind="record_updated"),
        },
        result={"success": True, "output": {"supplier_id": "SUP-001", "contact": "Alice", "phone": "139-0000"}},
        before_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Supplier ID"}, {"header": "Contact"}, {"header": "Phone"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Supplier ID", "text": "SUP-001"},
                                {"column_header": "Contact", "text": ""},
                                {"column_header": "Phone", "text": ""},
                            ]
                        }
                    ],
                }
            ]
        },
        after_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Supplier ID"}, {"header": "Contact"}, {"header": "Phone"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Supplier ID", "text": "SUP-001"},
                                {"column_header": "Contact", "text": "Alice"},
                                {"column_header": "Phone", "text": "139-0000"},
                            ]
                        }
                    ],
                }
            ]
        },
    )

    assert recovery["postcondition"]["kind"] == "table_row_exists"
    assert recovery["postcondition"]["key"] == {"Supplier ID": "SUP-001"}
    assert recovery["postcondition"]["expect"] == {"Contact": "Alice", "Phone": "139-0000"}


def test_recover_failed_side_effect_uses_plan_bindings_when_start_page_has_no_comparable_table():
    before_snapshot = {"table_views": []}
    after_snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Record No"}, {"header": "Source"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Record No", "text": "REQ-001"},
                            {"column_header": "Source", "text": "SRC-001"},
                            {"column_header": "Status", "text": "approved"},
                        ]
                    },
                    {
                        "cells": [
                            {"column_header": "Record No", "text": "REQ-NEW"},
                            {"column_header": "Source", "text": "SRC-NEW"},
                            {"column_header": "Status", "text": "pending"},
                        ]
                    },
                ],
            }
        ]
    }

    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
            "input_bindings": {
                "record_no": {"default": "REQ-NEW"},
                "source_no": {"default": "SRC-NEW"},
            },
        },
        result={"success": False, "error": "final assertion used a display label not present in the table"},
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
    )

    assert recovered is not None
    recovered_plan, recovered_result = recovered
    assert recovered_result["success"] is True
    assert recovered_plan["postcondition"]["key"] == {"Record No": "REQ-NEW"}
    assert recovered_plan["postcondition"]["expect"] == {"Status": "pending"}


def test_recover_failed_side_effect_does_not_use_action_only_row_as_postcondition():
    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
        },
        result={"success": False, "error": "terminal state was not observed"},
        before_snapshot={"table_views": []},
        after_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Action"}],
                    "rows": [{"cells": [{"column_header": "Action", "text": "Delete"}]}],
                }
            ]
        },
    )

    assert recovered is None


def test_recover_failed_side_effect_ignores_entity_tokens_from_failure_facts():
    recovered = recover_failed_side_effect_from_snapshot_diff(
        plan={
            "action_type": "run_python",
            "expected_effect": "mixed",
            "description": "Create the generated order PO-NEW-001 from PR-NEW-001",
            "terminal_contract": _required_terminal_contract("row_exists", kind="record_created"),
        },
        result={"success": False, "error": "Created PO-NEW-001 for PR-NEW-001 but verification failed"},
        before_snapshot={"table_views": []},
        after_snapshot={
            "table_views": [
                {
                    "columns": [{"header": "Order No"}, {"header": "Request No"}, {"header": "Status"}],
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Order No", "text": "PO-NEW-001"},
                                {"column_header": "Request No", "text": "PR-NEW-001"},
                                {"column_header": "Status", "text": "pending"},
                            ]
                        }
                    ],
                }
            ]
        },
    )

    assert recovered is None


def test_literal_postcondition_is_trusted_only_for_recovered_attempts():
    snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Record No"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Record No", "text": "REQ-002"},
                            {"column_header": "Status", "text": "pending"},
                        ]
                    }
                ],
            }
        ]
    }
    postcondition = {
        "kind": "table_row_exists",
        "source": "observed",
        "key": {"Record No": "REQ-002"},
        "expect": {"Status": "pending"},
    }

    assert not recording_runtime_agent._validated_postcondition(postcondition, snapshot=snapshot)
    assert recording_runtime_agent._validated_postcondition(
        postcondition,
        snapshot=snapshot,
        allow_literal_key=True,
    )


def test_postcondition_prunes_expect_values_without_execution_evidence():
    snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Record No"}, {"header": "Status"}, {"header": "Owner"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Record No", "text": "REQ-002"},
                            {"column_header": "Status", "text": "pending"},
                            {"column_header": "Owner", "text": "Alice"},
                        ]
                    }
                ],
            }
        ]
    }
    postcondition = {
        "kind": "table_row_exists",
        "source": "observed",
        "key": {"Record No": "REQ-002"},
        "expect": {"Status": "pending", "Owner": "Alice"},
    }

    validated = recording_runtime_agent._validated_postcondition(
        postcondition,
        snapshot=snapshot,
        allow_literal_key=True,
        result={"success": True, "output": {"status": "pending", "observed_row_text": "REQ-002 pending Alice"}},
    )

    assert validated["key"] == {"Record No": "REQ-002"}
    assert validated["expect"] == {"Status": "pending"}
    assert validated["expect_pruned"] is True


def test_extract_snapshot_enrichment_overrides_unobserved_label_from_detail_value():
    result = {
        "signals": {
            "extract_snapshot": {
                "fields": [
                    {
                        "label": "compliance_summary",
                        "value": "Must keep audit logs.",
                        "observed_label": "Compliance summary",
                        "replay_required": True,
                    }
                ]
            }
        }
    }
    snapshot = {
        "detail_views": [
            {
                "fields": [
                    {
                        "label": "Compliance clause",
                        "value": "Must keep audit logs.",
                    }
                ]
            }
        ]
    }

    enriched = recording_runtime_agent._enrich_extract_snapshot_result_with_replay_evidence(result, snapshot)

    field = enriched["signals"]["extract_snapshot"]["fields"][0]
    assert field["observed_label"] == "Compliance clause"


def test_extract_snapshot_enrichment_resolves_label_value_mistake_from_detail_view():
    result = {
        "output": {"contract_number": "Contract number"},
        "signals": {
            "extract_snapshot": {
                "fields": [
                    {
                        "label": "contract_number",
                        "value": "Contract number",
                        "replay_required": True,
                    }
                ]
            }
        },
    }
    snapshot = {
        "detail_views": [
            {
                "fields": [
                    {
                        "label": "Contract number",
                        "value": "CT-001",
                    }
                ]
            }
        ]
    }

    enriched = recording_runtime_agent._enrich_extract_snapshot_result_with_replay_evidence(result, snapshot)

    field = enriched["signals"]["extract_snapshot"]["fields"][0]
    assert field["observed_label"] == "Contract number"
    assert field["value"] == "CT-001"
    assert enriched["output"]["contract_number"] == "CT-001"


def test_extract_snapshot_enrichment_preserves_table_cell_value_that_matches_detail_label():
    result = {
        "output": {"status": "pending_approval"},
        "signals": {
            "extract_snapshot": {
                "fields": [
                    {
                        "label": "status",
                        "value": "pending_approval",
                        "replay_required": True,
                    }
                ]
            }
        },
    }
    snapshot = {
        "detail_views": [{"fields": [{"label": "pending_approval", "value": "¥188,000.00"}]}],
        "table_views": [
            {
                "columns": [{"header": "Order"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Order", "column_index": 0, "text": "PO-001"},
                            {"column_header": "Status", "column_index": 1, "text": "pending_approval"},
                        ]
                    }
                ],
            }
        ],
    }

    enriched = recording_runtime_agent._enrich_extract_snapshot_result_with_replay_evidence(result, snapshot)

    field = enriched["signals"]["extract_snapshot"]["fields"][0]
    assert field["value"] == "pending_approval"
    assert enriched["output"]["status"] == "pending_approval"


def test_extract_snapshot_enrichment_adds_table_cell_evidence_from_row_anchor():
    result = {
        "output": {"order_no": "PO-001", "status": "pending_approval"},
        "signals": {
            "extract_snapshot": {
                "fields": [
                    {
                        "label": "order_no",
                        "value": "PO-001",
                        "text_pattern": {"prefix": "Created:", "suffix": ""},
                        "replay_required": True,
                    },
                    {
                        "label": "status",
                        "value": "pending_approval",
                        "replay_required": True,
                    },
                ]
            }
        },
    }
    snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Order"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Order", "column_index": 0, "text": "PO-001"},
                            {"column_header": "Status", "column_index": 1, "text": "pending_approval"},
                        ]
                    }
                ],
            }
        ],
    }

    enriched = recording_runtime_agent._enrich_extract_snapshot_result_with_replay_evidence(result, snapshot)

    status_field = enriched["signals"]["extract_snapshot"]["fields"][1]
    assert status_field["table_cell"] == {
        "table_headers": ["Order", "Status"],
        "row_key": {"Order": "PO-001"},
        "column_header": "Status",
        "column_index": 1,
    }


def test_extract_snapshot_enrichment_adds_table_cell_from_multi_field_row_match_without_unique_text():
    result = {
        "output": {"supplier_id": "SUP-001", "contact_name": "Alice", "phone": "139-0000"},
        "signals": {
            "extract_snapshot": {
                "fields": [
                    {"label": "supplier_id", "value": "SUP-001", "replay_required": True},
                    {"label": "contact_name", "value": "Alice", "replay_required": True},
                    {"label": "phone", "value": "139-0000", "replay_required": True},
                ]
            }
        },
    }
    snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Supplier ID"}, {"header": "Contact"}, {"header": "Phone"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Supplier ID", "column_index": 0, "text": "SUP-001"},
                            {"column_header": "Contact", "column_index": 1, "text": "Alice"},
                            {"column_header": "Phone", "column_index": 2, "text": "139-0000"},
                        ]
                    }
                ],
            }
        ],
    }

    enriched = recording_runtime_agent._enrich_extract_snapshot_result_with_replay_evidence(result, snapshot)

    fields = enriched["signals"]["extract_snapshot"]["fields"]
    assert fields[1]["table_cell"] == {
        "table_headers": ["Supplier ID", "Contact", "Phone"],
        "row_key": {"Supplier ID": "SUP-001"},
        "column_header": "Contact",
        "column_index": 1,
    }
    assert fields[2]["table_cell"]["row_key"] == {"Supplier ID": "SUP-001"}


def test_table_anchor_prefers_structurally_unique_value_over_identifier_header_marker():
    snapshot = {
        "table_views": [
            {
                "columns": [{"header": "Invoice number"}, {"header": "Team"}, {"header": "Status"}],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Invoice number", "column_index": 0, "text": "INV-001"},
                            {"column_header": "Team", "column_index": 1, "text": "Blue Team"},
                            {"column_header": "Status", "column_index": 2, "text": "Open"},
                        ]
                    },
                    {
                        "cells": [
                            {"column_header": "Invoice number", "column_index": 0, "text": "INV-001"},
                            {"column_header": "Team", "column_index": 1, "text": "Red Team"},
                            {"column_header": "Status", "column_index": 2, "text": "Open"},
                        ]
                    },
                ],
            }
        ]
    }

    match = recording_runtime_agent._best_table_row_match(snapshot, ["INV-001", "Blue Team"], min_score=2)

    assert match["anchor_cell"]["column_header"] == "Team"
    assert match["anchor_cell"]["text"] == "Blue Team"


def test_parse_json_object_rejects_run_python_without_runner():
    payload = {"description": "Bad", "action_type": "run_python", "code": "print('bad')"}

    with pytest.raises(ValueError):
        _parse_json_object(json.dumps(payload))


def test_parse_json_object_accepts_plan_with_extra_planner_output():
    payload = {
        "description": "Run",
        "action_type": "run_python",
        "code": "async def run(page, results):\n    return {'ok': True}",
    }

    parsed = _parse_json_object(json.dumps(payload) + "\n" + json.dumps({"reason": "extra"}))

    assert parsed["description"] == "Run"
    assert "async def run(page, results)" in parsed["code"]


def test_parse_json_object_ignores_analysis_and_evidence_json_before_plan():
    payload = {
        "description": "Extract fork count",
        "action_type": "run_python",
        "expected_effect": "extract",
        "allow_empty_output": False,
        "output_key": "fork_count",
        "code": "async def run(page, results):\n    return {'fork_count': 315}",
    }
    text = (
        "1. Analyze the request.\n"
        'Evidence: {"label": "Fork 315", "locator": {"method": "role"}}.\n'
        "The output should be JSON.\n"
        "```json\n"
        + json.dumps(payload)
        + "\n```"
    )

    parsed = _parse_json_object(text)

    assert parsed["description"] == "Extract fork count"
    assert parsed["output_key"] == "fork_count"
    assert "async def run(page, results)" in parsed["code"]


def test_parse_json_object_finds_unfenced_plan_after_evidence_json():
    payload = {
        "description": "Extract fork count",
        "action_type": "run_python",
        "expected_effect": "extract",
        "allow_empty_output": False,
        "output_key": "fork_count",
        "code": "async def run(page, results):\n    return {'fork_count': 315}",
    }
    text = (
        "Analysis before the answer.\n"
        'Evidence: {"label": "Fork 315", "locator": {"method": "role"}}.\n'
        + json.dumps(payload)
    )

    parsed = _parse_json_object(text)

    assert parsed["description"] == "Extract fork count"
    assert parsed["output_key"] == "fork_count"


@pytest.mark.asyncio
async def test_planner_json_parse_failure_returns_agent_diagnostic(monkeypatch):
    async def fake_snapshot(_page):
        return {
            "url": "https://github.com/trending",
            "title": "Trending",
            "frames": [],
            "content_nodes": [],
            "actionable_nodes": [],
            "containers": [],
        }

    async def bad_planner(_payload):
        _parse_json_object("I could not build a JSON plan")

    monkeypatch.setattr(recording_runtime_agent, "_safe_page_snapshot", fake_snapshot)

    result = await RecordingRuntimeAgent(planner=bad_planner).run(
        page=_FakePage(),
        instruction="打开和Skill最相关的项目",
        runtime_results={},
    )

    assert result.success is False
    assert result.trace is None
    assert result.diagnostics
    assert result.diagnostics[0].source == "ai"
    assert "planner" in result.diagnostics[0].message.lower()
    assert result.diagnostics[0].raw["error_type"] == "planner_contract"


@pytest.mark.asyncio
async def test_default_planner_contract_diagnostic_includes_llm_call_summary(monkeypatch):
    async def fake_snapshot(_page):
        return {
            "url": "https://github.com/trending",
            "title": "Trending",
            "frames": [],
            "content_nodes": [],
            "actionable_nodes": [],
            "containers": [],
        }

    class FakeModel:
        model_name = "glm-4.7"
        openai_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        max_tokens = 100000
        model_kwargs = {}
        profile = {"max_input_tokens": 200000}

        async def ainvoke(self, messages):
            assert len(messages) == 2
            return SimpleNamespace(content="I cannot return JSON")

    import backend.deepagent.engine as engine

    monkeypatch.setattr(recording_runtime_agent, "_safe_page_snapshot", fake_snapshot)
    monkeypatch.setattr(
        engine,
        "get_llm_model",
        lambda config=None, max_tokens_override=None, streaming=False: FakeModel(),
    )

    result = await RecordingRuntimeAgent(
        model_config={
            "id": "model-glm",
            "provider": "other",
            "model_name": "glm-4.7",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "context_window": 200000,
            "user_id": "admin-uuid",
        }
    ).run(
        page=_FakePage(),
        instruction="find repo star count",
        runtime_results={},
    )

    raw = result.diagnostics[0].raw
    assert raw["error_type"] == "planner_contract"
    assert raw["llm_call"]["request"]["effective_model"]["model_name"] == "glm-4.7"
    assert raw["llm_call"]["request"]["effective_model"]["max_tokens"] == 100000
    assert raw["llm_call"]["request"]["effective_model"]["profile"]["max_input_tokens"] == 200000
    assert "preview" not in raw["llm_call"]["request"]["messages"][0]
    assert raw["llm_call"]["response"]["preview"] == "I cannot return JSON"


@pytest.mark.asyncio
async def test_default_planner_prompt_preview_requires_debug_flag(monkeypatch):
    async def fake_snapshot(_page):
        return {
            "url": "https://github.com/trending",
            "title": "Trending",
            "frames": [],
            "content_nodes": [],
            "actionable_nodes": [],
            "containers": [],
        }

    class FakeModel:
        model_name = "glm-4.7"
        max_tokens = 8192
        model_kwargs = {}

        async def ainvoke(self, _messages):
            return SimpleNamespace(content="not json")

    import backend.deepagent.engine as engine

    monkeypatch.setattr(recording_runtime_agent, "_safe_page_snapshot", fake_snapshot)
    monkeypatch.setattr(
        engine,
        "get_llm_model",
        lambda config=None, max_tokens_override=None, streaming=False: FakeModel(),
    )
    monkeypatch.setenv("RPA_LLM_DIAGNOSTIC_PROMPT_PREVIEW", "true")

    result = await RecordingRuntimeAgent(model_config={"model_name": "glm-4.7"}).run(
        page=_FakePage(),
        instruction="find repo star count",
        runtime_results={},
    )

    messages = result.diagnostics[0].raw["llm_call"]["request"]["messages"]
    assert messages[0]["preview"].startswith("You operate exactly one RPA recording command.")
    assert "find repo star count" in messages[1]["preview"]


@pytest.mark.asyncio
async def test_default_planner_uses_recording_token_floor(monkeypatch):
    async def fake_snapshot(_page):
        return {
            "url": "https://github.com/example/repo",
            "title": "Repo",
            "frames": [],
            "content_nodes": [],
            "actionable_nodes": [],
            "containers": [],
        }

    calls = []

    class FakeModel:
        model_name = "glm-4.7"
        openai_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        max_tokens = 8192
        model_kwargs = {}
        profile = {"max_input_tokens": 200000}

        async def ainvoke(self, _messages):
            return SimpleNamespace(content="not json")

    import backend.deepagent.engine as engine

    def fake_get_llm_model(config=None, max_tokens_override=None, streaming=False):
        calls.append(
            {
                "config": config,
                "max_tokens_override": max_tokens_override,
                "streaming": streaming,
            }
        )
        return FakeModel()

    monkeypatch.setattr(recording_runtime_agent, "_safe_page_snapshot", fake_snapshot)
    monkeypatch.setattr(engine, "get_llm_model", fake_get_llm_model)

    result = await RecordingRuntimeAgent(model_config={"model_name": "glm-4.7"}).run(
        page=_FakePage(),
        instruction="get fork count",
        runtime_results={},
    )

    assert result.success is False
    assert calls == [
        {
            "config": {"model_name": "glm-4.7"},
            "max_tokens_override": 8192,
            "streaming": False,
        }
    ]
    assert result.diagnostics[0].raw["llm_call"]["request"]["effective_model"]["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_execution_failure_diagnostic_includes_planner_llm_call_summary(monkeypatch):
    async def fake_snapshot(_page):
        return {
            "url": "https://github.com/mattpocock/skills",
            "title": "Skills",
            "frames": [],
            "content_nodes": [],
            "actionable_nodes": [],
            "containers": [],
        }

    bad_plan = {
        "description": "Extract stars",
        "action_type": "run_python",
        "expected_effect": "extract",
        "allow_empty_output": False,
        "code": (
            "async def run(page, results):\n"
            "    link = page.get_by_role('link', name=lambda text: 'stars' in text)\n"
            "    return await link.text_content()"
        ),
    }

    class FakeModel:
        model_name = "glm-4.7"
        openai_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        max_tokens = 100000
        model_kwargs = {}
        profile = {"max_input_tokens": 200000}

        async def ainvoke(self, _messages):
            return SimpleNamespace(content=json.dumps(bad_plan))

    class FailingPage(_FakePage):
        def get_by_role(self, *_args, **_kwargs):
            raise AttributeError("'function' object has no attribute 'replace'")

    import backend.deepagent.engine as engine

    monkeypatch.setattr(recording_runtime_agent, "_safe_page_snapshot", fake_snapshot)
    monkeypatch.setattr(
        engine,
        "get_llm_model",
        lambda config=None, max_tokens_override=None, streaming=False: FakeModel(),
    )

    result = await RecordingRuntimeAgent(model_config={"model_name": "glm-4.7"}).run(
        page=FailingPage(),
        instruction="get the repository star count",
        runtime_results={},
    )

    assert result.success is False
    raw = result.diagnostics[0].raw
    assert "'function' object has no attribute 'replace'" in raw["result"]["error"]
    assert raw["llm_call"]["response"]["preview"].startswith("{")
    assert raw["llm_call"]["request"]["message_count"] == 2
