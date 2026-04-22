import importlib
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from backend.rpa.recording_runtime_agent import (
    RecordingRuntimeAgent,
    _classify_recording_failure,
    _parse_json_object,
    _resolve_recording_snapshot_debug_dir,
    _resolve_recording_snapshot_debug_path,
)


class _FakePage:
    url = "https://example.test/start"

    async def title(self):
        return "Example"

    async def goto(self, url, wait_until=None):
        self.url = url

    async def wait_for_load_state(self, _state):
        return None


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
        for pair in region.get("pairs") or []:
            if pair.get("label") == label and pair.get("value") == value:
                return region
    return None


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


@pytest.mark.asyncio
async def test_recording_runtime_agent_repairs_once_after_failure():
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

    assert result.success is True
    assert len(calls) == 2
    assert result.trace.ai_execution.repair_attempted is True
    assert result.diagnostics[0].message == "boom"


@pytest.mark.asyncio
async def test_recording_runtime_agent_sends_advisory_failure_hint_to_repair_planner():
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

    repair_payload = calls[1]["repair"]
    assert result.success is True
    assert result.diagnostics[0].message.startswith("Page.wait_for_selector")
    assert repair_payload["error"].startswith("Page.wait_for_selector")
    assert repair_payload["failure_analysis"]["type"] == "selector_timeout"
    assert "hint" in repair_payload["failure_analysis"]
    assert "confidence" not in repair_payload["failure_analysis"]
    assert result.diagnostics[0].raw["failure_analysis"]["type"] == "selector_timeout"


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
async def test_recording_runtime_agent_forwards_instruction_into_snapshot_compaction(monkeypatch):
    compact_calls = []
    planner_calls = []

    def fake_compact_recording_snapshot(snapshot, instruction, *, char_budget=12000):
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
        "expanded_regions": [{"title": "Trending repositories"}],
        "sampled_regions": [],
        "region_catalogue": [],
    }

    async def fake_build_page_snapshot(*_args, **_kwargs):
        return raw_snapshot

    def fake_compact_recording_snapshot(_snapshot, _instruction, *, char_budget=12000):
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
    for existing in debug_dir.glob("recording-snapshot-*.json"):
        existing.unlink()

    monkeypatch.setenv("RPA_RECORDING_DEBUG_SNAPSHOT_DIR", str(debug_dir))
    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)
    monkeypatch.setattr("backend.rpa.recording_runtime_agent.compact_recording_snapshot", fake_compact_recording_snapshot)

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="打开和Claudecode最相关的项目",
        runtime_results={"previous": "value"},
    )

    files = list(debug_dir.glob("recording-snapshot-*.json"))
    assert result.success is True
    assert len(files) == 1

    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["stage"] == "initial"
    assert payload["instruction"] == "打开和Claudecode最相关的项目"
    assert payload["raw_snapshot"] == raw_snapshot
    assert payload["compact_snapshot"] == compact_snapshot
    assert payload["runtime_results"] == {"previous": "value"}
    for file in files:
        file.unlink()


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

    def fake_compact_recording_snapshot(snapshot, _instruction, *, char_budget=12000):
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
    for existing in debug_dir.glob("recording-snapshot-*.json"):
        existing.unlink()

    monkeypatch.setenv("RPA_RECORDING_DEBUG_SNAPSHOT_DIR", str(debug_dir))
    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)
    monkeypatch.setattr("backend.rpa.recording_runtime_agent.compact_recording_snapshot", fake_compact_recording_snapshot)

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="打开和Claudecode最相关的项目",
        runtime_results={},
    )

    files = sorted(debug_dir.glob("recording-snapshot-*.json"))
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    repair_payload = next(item for item in payloads if item["stage"] == "repair")

    assert result.success is True
    assert len(files) == 2
    assert calls[1]["repair"]["snapshot_after_failure"]["url"] == "https://github.com/search"
    assert repair_payload["compact_snapshot"]["url"] == "https://github.com/search"
    assert repair_payload["error"].startswith("Locator.click")
    assert repair_payload["failure_analysis"]["type"] == "selector_timeout"
    for file in files:
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


def test_parse_json_object_rejects_run_python_without_runner():
    payload = {"description": "Bad", "action_type": "run_python", "code": "print('bad')"}

    with pytest.raises(ValueError):
        _parse_json_object(json.dumps(payload))

