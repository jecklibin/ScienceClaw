import json
from types import SimpleNamespace

import pytest

from backend.rpa.recording_runtime_agent import RecordingRuntimeAgent, _parse_json_object


class _FakePage:
    url = "https://example.test/start"

    async def title(self):
        return "Example"

    async def goto(self, url, wait_until=None):
        self.url = url

    async def wait_for_load_state(self, _state):
        return None


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
async def test_recording_runtime_agent_repairs_after_empty_extract_output():
    plans = [
        {
            "description": "Extract latest issue title",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "latest_issue",
            "code": (
                "async def run(page, results):\n"
                "    return {'latest_issue_title': None, 'latest_issue_link': None}"
            ),
        },
        {
            "description": "Extract latest issue title with fallback selector",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "latest_issue",
            "code": "async def run(page, results):\n    return {'latest_issue_title': 'Latest issue'}",
        },
    ]

    async def planner(_payload):
        return plans.pop(0)

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=_FakePage(),
        instruction="find the latest issue title",
        runtime_results={},
    )

    assert result.success is True
    assert result.trace.ai_execution.repair_attempted is True
    assert result.output == {"latest_issue_title": "Latest issue"}
    assert "meaningful" in result.diagnostics[0].message


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

