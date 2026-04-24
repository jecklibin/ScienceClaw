import importlib

import pytest

from backend.rpa.manager import RPASession
from backend.rpa.manual_recording_models import ManualActionKind, ManualRecordedAction, ManualRecordingDiagnostic
from backend.rpa.recording_runtime_agent import RecordingAgentResult
from backend.rpa.trace_models import RPAAcceptedTrace, RPAAIExecution, RPATraceType


ROUTE_MODULE = importlib.import_module("backend.route.rpa")


def test_generate_session_script_prefers_traces_over_legacy_steps():
    session = RPASession(id="s1", user_id="u1", sandbox_session_id="sandbox")
    session.traces.append(
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            user_instruction="collect the first 10 PRs with title and creator",
            output_key="top10_prs",
            output=[{"title": "Fix", "creator": "alice"}],
            ai_execution=RPAAIExecution(
                code="async def run(page, results):\n    return [{'title': 'Fix', 'creator': 'alice'}]",
            ),
        )
    )

    script = ROUTE_MODULE._generate_session_script(session, {}, test_mode=True)

    assert "Auto-generated skill from RPA trace recording" in script
    assert "top10_prs" in script


def test_generate_session_script_uses_recorded_actions_when_present():
    session = RPASession(id="s2", user_id="u2", sandbox_session_id="sandbox")
    session.recorded_actions.append(
        ManualRecordedAction(
            step_id="step-search",
            action_kind=ManualActionKind.CLICK,
            description='点击 button("Search")',
            target={"method": "role", "role": "button", "name": "Search"},
            validation={"status": "ok"},
        )
    )

    script = ROUTE_MODULE._generate_session_script(session, {}, test_mode=True)

    assert "get_by_role('button'" in script or 'get_by_role("button"' in script
    assert 'name="Search"' in script or "name='Search'" in script or "name=\"Search\"" in script


def test_generate_session_script_keeps_ai_traces_when_recorded_actions_replace_manual_traces():
    session = RPASession(id="s3", user_id="u3", sandbox_session_id="sandbox")
    session.recorded_actions.append(
        ManualRecordedAction(
            step_id="step-search",
            action_kind=ManualActionKind.CLICK,
            description='click button("Search")',
            target={"method": "role", "role": "button", "name": "Search"},
            validation={"status": "ok"},
        )
    )
    session.traces.extend(
        [
            RPAAcceptedTrace(
                trace_id="trace-ai-1",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="collect the repository url",
                output_key="selected_repo",
                output={"url": "https://github.com/openai/openai-agents-python"},
                ai_execution=RPAAIExecution(
                    code="async def run(page, results):\n    return {'url': 'https://github.com/openai/openai-agents-python'}",
                ),
            ),
            RPAAcceptedTrace(
                trace_id="trace-step-search",
                trace_type=RPATraceType.MANUAL_ACTION,
                source="manual",
                action="click",
                description="legacy manual click",
            ),
        ]
    )

    script = ROUTE_MODULE._generate_session_script(session, {}, test_mode=True)

    assert "selected_repo" in script
    assert "get_by_role('button'" in script or 'get_by_role(\"button\"' in script


@pytest.mark.asyncio
async def test_generate_script_blocks_when_recording_diagnostics_exist():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-diagnostic-generate", user_id="u1", sandbox_session_id="sandbox")
    session.recording_diagnostics.append(
        ManualRecordingDiagnostic(
            related_action_kind=ManualActionKind.FILL,
            failure_reason="canonical_target_missing",
        )
    )
    manager.sessions[session.id] = session

    try:
        user = type("User", (), {"id": "u1"})()
        with pytest.raises(ROUTE_MODULE.HTTPException) as exc_info:
            await ROUTE_MODULE.generate_script(session.id, ROUTE_MODULE.GenerateRequest(), user)
        assert exc_info.value.status_code == 400
        assert "diagnostic" in exc_info.value.detail
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_test_script_blocks_when_recording_diagnostics_exist():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-diagnostic-test", user_id="u1", sandbox_session_id="sandbox")
    session.recording_diagnostics.append(
        ManualRecordingDiagnostic(
            related_action_kind=ManualActionKind.CLICK,
            failure_reason="canonical_target_missing",
        )
    )
    manager.sessions[session.id] = session

    try:
        user = type("User", (), {"id": "u1"})()
        with pytest.raises(ROUTE_MODULE.HTTPException) as exc_info:
            await ROUTE_MODULE.test_script(session.id, ROUTE_MODULE.GenerateRequest(), user)
        assert exc_info.value.status_code == 400
        assert "diagnostic" in exc_info.value.detail
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_apply_recording_agent_result_persists_trace_and_runtime_output():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-trace-test", user_id="u1", sandbox_session_id="sandbox")
    manager.sessions[session.id] = session
    try:
        trace = RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            output_key="selected_project",
            output={"url": "https://github.com/owner/repo"},
            ai_execution=RPAAIExecution(code="async def run(page, results):\n    return {}"),
        )

        await ROUTE_MODULE._apply_recording_agent_result(
            session.id,
            RecordingAgentResult(success=True, trace=trace, output_key="selected_project", output=trace.output),
        )

        assert session.traces[0].output_key == "selected_project"
        assert session.runtime_results.resolve_ref("selected_project.url") == "https://github.com/owner/repo"
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_test_script_passes_route_timeout_to_executor(monkeypatch):
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-timeout-test", user_id="u1", sandbox_session_id="sandbox")
    manager.sessions[session.id] = session

    captured: dict = {}

    class FakeConnector:
        async def get_browser(self, **kwargs):
            return object()

        def run_in_pw_loop(self, coro):
            return coro

    async def fake_execute(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return {"success": True, "output": "SKILL_SUCCESS", "data": {}}

    async def fake_inject_credentials(user_id, params, extra):
        return {}

    monkeypatch.setattr(ROUTE_MODULE, "_generate_session_script", lambda *args, **kwargs: "async def execute_skill(page, **kwargs):\n    return {}")
    monkeypatch.setattr(ROUTE_MODULE, "get_cdp_connector", lambda: FakeConnector())
    monkeypatch.setattr(ROUTE_MODULE.executor, "execute", fake_execute)
    monkeypatch.setattr(ROUTE_MODULE, "inject_credentials", fake_inject_credentials)
    monkeypatch.setattr(ROUTE_MODULE.settings, "storage_backend", "local")
    monkeypatch.setattr(ROUTE_MODULE.settings, "workspace_dir", "E:/Work-Project/OtherWork/ScienceClaw")

    try:
        user = type("User", (), {"id": "u1"})()
        await ROUTE_MODULE.test_script(session.id, ROUTE_MODULE.GenerateRequest(), user)
        assert captured["timeout"] == ROUTE_MODULE.RPA_TEST_TIMEOUT_S
    finally:
        manager.sessions.pop(session.id, None)

