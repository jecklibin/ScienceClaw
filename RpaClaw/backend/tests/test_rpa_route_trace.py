import importlib
from datetime import datetime

import pytest

from backend.rpa.manager import RPASession, RPAStep
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


def test_generate_session_script_preserves_step_signals_on_recorded_actions():
    session = RPASession(id="s-popup", user_id="u-popup", sandbox_session_id="sandbox")
    session.steps.append(
        RPAStep(
            id="step-export",
            action="click",
            target='{"method": "text", "value": "Export all"}',
            description='click text("Export all")',
            signals={"popup": {"source_tab_id": "tab-main", "target_tab_id": "tab-export"}},
            tab_id="tab-main",
            source_tab_id="tab-main",
            target_tab_id="tab-export",
        )
    )
    session.recorded_actions.append(
        ManualRecordedAction(
            step_id="step-export",
            action_kind=ManualActionKind.CLICK,
            description='click text("Export all")',
            target={"method": "text", "value": "Export all"},
            validation={"status": "ok"},
        )
    )

    script = ROUTE_MODULE._generate_session_script(session, {}, test_mode=True)

    assert "expect_popup() as popup_info" in script
    assert 'tabs["tab-export"] = new_page' in script
    assert "current_page = new_page" in script


def test_generate_session_script_merges_step_tab_fields_into_existing_trace():
    session = RPASession(id="s-switch", user_id="u-switch", sandbox_session_id="sandbox")
    session.steps.append(
        RPAStep(
            id="step-switch",
            action="switch_tab",
            target="",
            description="切换到标签页 iSales+",
            tab_id="tab-root",
            source_tab_id="tab-root",
            target_tab_id="tab-sales",
        )
    )
    session.traces.append(
        RPAAcceptedTrace(
            trace_id="trace-step-switch",
            trace_type=RPATraceType.MANUAL_ACTION,
            source="manual",
            action="switch_tab",
            description="切换到标签页 iSales+",
        )
    )

    script = ROUTE_MODULE._generate_session_script(session, {}, test_mode=True)

    assert "No stable locator was recorded" not in script
    assert 'tabs.setdefault("tab-root", current_page)' in script
    assert 'current_page = tabs["tab-sales"]' in script


def test_generate_session_script_preserves_frame_path_on_recorded_actions():
    session = RPASession(id="s-frame", user_id="u-frame", sandbox_session_id="sandbox")
    session.recorded_actions.append(
        ManualRecordedAction(
            step_id="step-notes",
            action_kind=ManualActionKind.CLICK,
            description='点击 link("菜鸟笔记") 并在新标签页打开',
            target={"method": "role", "role": "link", "name": "菜鸟笔记"},
            validation={"status": "ok"},
            frame_path=["iframe[title='运行结果预览']", "iframe"],
        )
    )
    session.steps.append(
        RPAStep(
            id="step-notes",
            action="click",
            target='{"method": "role", "role": "link", "name": "菜鸟笔记"}',
            description='点击 link("菜鸟笔记") 并在新标签页打开',
            frame_path=["iframe[title='运行结果预览']", "iframe"],
            signals={"popup": {"source_tab_id": "tab-main", "target_tab_id": "tab-note"}},
            tab_id="tab-main",
            source_tab_id="tab-main",
            target_tab_id="tab-note",
        )
    )

    script = ROUTE_MODULE._generate_session_script(session, {}, test_mode=True)

    assert 'frame_scope = current_page.frame_locator("iframe[title=\'运行结果预览\']")' in script
    assert 'frame_scope = frame_scope.frame_locator("iframe")' in script
    assert "await frame_scope.get_by_role('link', name='菜鸟笔记', exact=True).click()" in script or 'await frame_scope.get_by_role("link", name="菜鸟笔记", exact=True).click()' in script
    assert "expect_popup() as popup_info" in script


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


def test_session_traces_for_compile_preserves_manual_ai_manual_order():
    session = RPASession(id="s-order", user_id="u-order", sandbox_session_id="sandbox")
    session.steps.extend(
        [
            RPAStep(
                id="step-first",
                action="click",
                target='{"method": "role", "role": "button", "name": "First"}',
                description="First manual action",
                timestamp=datetime.fromtimestamp(1),
                event_timestamp_ms=1000,
            ),
            RPAStep(
                id="step-second",
                action="click",
                target='{"method": "role", "role": "button", "name": "Second"}',
                description="Second manual action",
                timestamp=datetime.fromtimestamp(3),
                event_timestamp_ms=3000,
            ),
        ]
    )
    session.recorded_actions.extend(
        [
            ManualRecordedAction(
                step_id="step-first",
                action_kind=ManualActionKind.CLICK,
                description="First manual action",
                target={"method": "role", "role": "button", "name": "First"},
                validation={"status": "ok"},
            ),
            ManualRecordedAction(
                step_id="step-second",
                action_kind=ManualActionKind.CLICK,
                description="Second manual action",
                target={"method": "role", "role": "button", "name": "Second"},
                validation={"status": "ok"},
            ),
        ]
    )
    session.traces.append(
        RPAAcceptedTrace(
            trace_id="trace-ai-middle",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            description="AI middle action",
            started_at=datetime.fromtimestamp(2),
            ended_at=datetime.fromtimestamp(2),
            ai_execution=RPAAIExecution(code="async def run(page, results):\n    return {}"),
        )
    )

    traces = ROUTE_MODULE._session_traces_for_compile(session)

    assert [trace.description for trace in traces] == [
        "First manual action",
        "AI middle action",
        "Second manual action",
    ]
    assert [step["description"] for step in ROUTE_MODULE.session_to_mcp_steps(session)] == [
        "First manual action",
        "AI middle action",
        "Second manual action",
    ]


def test_build_session_recording_meta_preserves_step_fields_in_trace_and_legacy_steps():
    session = RPASession(id="route-meta-trace", user_id="u1", sandbox_session_id="sandbox")
    session.steps.append(
        RPAStep(
            id="step-export",
            action="click",
            target='{"method": "text", "value": "Export all"}',
            description='click text("Export all")',
            locator_candidates=[
                {
                    "kind": "text",
                    "locator": {"method": "text", "value": "Export all"},
                    "selected": True,
                }
            ],
            validation={"status": "ok"},
            signals={"popup": {"source_tab_id": "tab-main", "target_tab_id": "tab-export"}},
            tab_id="tab-main",
            source_tab_id="tab-main",
            target_tab_id="tab-export",
            sequence=7,
            event_timestamp_ms=12345,
        )
    )
    session.recorded_actions.append(
        ManualRecordedAction(
            step_id="step-export",
            action_kind=ManualActionKind.CLICK,
            description='click text("Export all")',
            target={"method": "text", "value": "Export all"},
            validation={"status": "ok"},
        )
    )

    meta = ROUTE_MODULE._build_session_recording_meta(session)

    assert meta["recording_source"] == "trace"
    assert meta["legacy_steps"][0]["sequence"] == 7
    trace = meta["traces"][0]
    assert trace["trace_id"] == "trace-step-export"
    assert trace["locator_candidates"][0]["locator"]["value"] == "Export all"
    assert trace["signals"]["popup"]["target_tab_id"] == "tab-export"
    assert trace["signals"]["tab"]["tab_id"] == "tab-main"
    assert trace["validation"]["status"] == "ok"


def test_build_session_recording_meta_derives_traces_for_legacy_step_only_session():
    session = RPASession(id="route-meta-legacy", user_id="u1", sandbox_session_id="sandbox")
    session.steps.append(
        RPAStep(
            id="step-open",
            action="goto",
            target="https://example.com/dashboard",
            description="Open dashboard",
            validation={"status": "ok"},
            sequence=1,
        )
    )

    meta = ROUTE_MODULE._build_session_recording_meta(session)

    assert meta["recording_source"] == "legacy_step"
    assert meta["legacy_steps"][0]["id"] == "step-open"
    assert meta["traces"][0]["trace_id"] == "trace-step-open"
    assert meta["traces"][0]["action"] == "goto"
    assert meta["traces"][0]["after_page"]["url"] == "https://example.com/dashboard"
    assert meta["traces"][0]["signals"]["recording"]["sequence"] == 1


@pytest.mark.asyncio
async def test_save_skill_exports_trace_first_recording_meta(monkeypatch):
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-save-trace", user_id="u1", sandbox_session_id="sandbox")
    session.traces.append(
        RPAAcceptedTrace(
            trace_id="trace-ai-1",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="collect result",
            output_key="result",
            output={"name": "demo"},
            ai_execution=RPAAIExecution(code="async def run(page, results):\n    return {'name': 'demo'}"),
        )
    )
    session.steps.append(RPAStep(id="legacy-step", action="goto", target="https://example.com"))
    manager.sessions[session.id] = session
    captured: dict = {}

    async def fake_wait_for_pending_events(target_session_id: str, timeout_ms: int = 1500):
        assert target_session_id == session.id
        return True

    async def fake_export_skill(**kwargs):
        captured.update(kwargs)
        return kwargs["skill_name"]

    monkeypatch.setattr(manager, "wait_for_pending_events", fake_wait_for_pending_events)
    monkeypatch.setattr(ROUTE_MODULE, "_generate_session_script", lambda *args, **kwargs: "print('ok')\n")
    monkeypatch.setattr(ROUTE_MODULE.exporter, "export_skill", fake_export_skill)

    try:
        user = type("User", (), {"id": "u1"})()
        response = await ROUTE_MODULE.save_skill(
            session.id,
            ROUTE_MODULE.SaveSkillRequest(skill_name="saved_trace", description="Saved trace"),
            user,
        )
        assert response == {"status": "success", "skill_name": "saved_trace"}
        assert captured["recording_meta"]["recording_source"] == "trace"
        assert captured["recording_meta"]["traces"][0]["trace_id"] == "trace-ai-1"
        assert captured["recording_meta"]["legacy_steps"][0]["id"] == "legacy-step"
        assert captured["steps"][0]["id"] == "trace-ai-1"
        assert captured["steps"][0]["result_key"] == "result"
    finally:
        manager.sessions.pop(session.id, None)


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
async def test_generate_script_waits_for_pending_events(monkeypatch):
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-generate-wait", user_id="u1", sandbox_session_id="sandbox")
    manager.sessions[session.id] = session

    called: dict[str, bool] = {"waited": False}

    async def fake_wait_for_pending_events(target_session_id: str, timeout_ms: int = 1500):
        assert target_session_id == session.id
        called["waited"] = True
        session.recorded_actions.append(
            ManualRecordedAction(
                step_id="step-search",
                action_kind=ManualActionKind.CLICK,
                description='点击 button("Search")',
                target={"method": "role", "role": "button", "name": "Search"},
                validation={"status": "ok"},
            )
        )
        return True

    monkeypatch.setattr(manager, "wait_for_pending_events", fake_wait_for_pending_events)

    try:
        user = type("User", (), {"id": "u1"})()
        response = await ROUTE_MODULE.generate_script(session.id, ROUTE_MODULE.GenerateRequest(), user)
        assert called["waited"] is True
        assert "Search" in response["script"]
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_generate_script_rejects_non_owner():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-generate-owner", user_id="owner", sandbox_session_id="sandbox")
    manager.sessions[session.id] = session

    try:
        user = type("User", (), {"id": "intruder"})()
        with pytest.raises(ROUTE_MODULE.HTTPException) as exc_info:
            await ROUTE_MODULE.generate_script(session.id, ROUTE_MODULE.GenerateRequest(), user)
        assert exc_info.value.status_code == 403
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_delete_timeline_manual_step_removes_generate_input():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-delete-manual-step", user_id="u1", sandbox_session_id="sandbox")
    session.steps.append(
        RPAStep(
            id="step-search",
            action="click",
            target='{"method": "role", "role": "button", "name": "Search"}',
            description='click button("Search")',
            validation={"status": "ok"},
        )
    )
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
                trace_id="trace-step-search",
                trace_type=RPATraceType.MANUAL_ACTION,
                source="manual",
                action="click",
                description='click button("Search")',
            ),
            RPAAcceptedTrace(
                trace_id="trace-ai-keep",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="collect title",
                output_key="page_title",
                ai_execution=RPAAIExecution(code="async def run(page, results):\n    return 'ok'"),
            ),
        ]
    )
    manager.sessions[session.id] = session

    try:
        user = type("User", (), {"id": "u1"})()
        response = await ROUTE_MODULE.delete_timeline_item(
            session.id,
            ROUTE_MODULE.DeleteTimelineItemRequest(kind="manual_step", step_id="step-search"),
            user,
        )
        script = ROUTE_MODULE._generate_session_script(session, {}, test_mode=True)

        assert response["status"] == "success"
        assert "Search" not in script
        assert "page_title" in script
        assert [trace.trace_id for trace in session.traces] == ["trace-ai-keep"]
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_delete_timeline_trace_removes_ai_trace_without_touching_steps():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-delete-ai-trace", user_id="u1", sandbox_session_id="sandbox")
    session.steps.append(RPAStep(id="step-keep", action="goto", target="https://example.test"))
    session.traces.append(
        RPAAcceptedTrace(
            trace_id="trace-ai-delete",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="collect title",
            output_key="page_title",
            ai_execution=RPAAIExecution(code="async def run(page, results):\n    return 'ok'"),
        )
    )
    manager.sessions[session.id] = session

    try:
        user = type("User", (), {"id": "u1"})()
        response = await ROUTE_MODULE.delete_timeline_item(
            session.id,
            ROUTE_MODULE.DeleteTimelineItemRequest(kind="trace", trace_id="trace-ai-delete"),
            user,
        )

        assert response["status"] == "success"
        assert session.traces == []
        assert [step.id for step in session.steps] == ["step-keep"]
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_delete_timeline_manual_trace_removes_legacy_step_fallback():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-delete-manual-trace", user_id="u1", sandbox_session_id="sandbox")
    session.steps.append(
        RPAStep(
            id="step-search",
            action="click",
            target='{"method": "role", "role": "button", "name": "Search"}',
            description='click button("Search")',
            validation={"status": "ok"},
        )
    )
    session.traces.append(
        RPAAcceptedTrace(
            trace_id="trace-step-search",
            trace_type=RPATraceType.MANUAL_ACTION,
            source="manual",
            action="click",
            description='click button("Search")',
        )
    )
    manager.sessions[session.id] = session

    try:
        user = type("User", (), {"id": "u1"})()
        await ROUTE_MODULE.delete_timeline_item(
            session.id,
            ROUTE_MODULE.DeleteTimelineItemRequest(kind="trace", trace_id="trace-step-search"),
            user,
        )
        script = ROUTE_MODULE._generate_session_script(session, {}, test_mode=True)

        assert session.traces == []
        assert session.steps == []
        assert "Search" not in script
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
async def test_test_script_rejects_non_owner(monkeypatch):
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-test-owner", user_id="owner", sandbox_session_id="sandbox")
    manager.sessions[session.id] = session

    class ForbiddenConnector:
        async def get_browser(self, **_kwargs):
            raise AssertionError("test_script should reject before opening a browser")

    monkeypatch.setattr(ROUTE_MODULE, "get_cdp_connector", lambda: ForbiddenConnector())

    try:
        user = type("User", (), {"id": "intruder"})()
        with pytest.raises(ROUTE_MODULE.HTTPException) as exc_info:
            await ROUTE_MODULE.test_script(session.id, ROUTE_MODULE.GenerateRequest(), user)
        assert exc_info.value.status_code == 403
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_save_skill_exports_projected_trace_steps(monkeypatch):
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-save-trace", user_id="u1", sandbox_session_id="sandbox")
    session.traces.append(
        RPAAcceptedTrace(
            trace_id="trace-ai-select",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="click the first project",
            description="Click first project",
            ai_execution=RPAAIExecution(code="async def run(page, results):\n    return {}"),
        )
    )
    manager.sessions[session.id] = session
    captured: dict = {}

    async def fake_export_skill(**kwargs):
        captured.update(kwargs)
        return kwargs["skill_name"]

    monkeypatch.setattr(
        ROUTE_MODULE,
        "_generate_session_script",
        lambda *args, **kwargs: "async def execute_skill(page, **kwargs):\n    return {}",
    )
    monkeypatch.setattr(ROUTE_MODULE.exporter, "export_skill", fake_export_skill)

    try:
        user = type("User", (), {"id": "u1"})()
        response = await ROUTE_MODULE.save_skill(
            session.id,
            ROUTE_MODULE.SaveSkillRequest(skill_name="trace_skill", description="Trace skill", params={}),
            user,
        )

        assert response == {"status": "success", "skill_name": "trace_skill"}
        assert captured["steps"][0]["action"] == "ai_script"
        assert captured["steps"][0]["rpa_trace"]["trace_id"] == "trace-ai-select"
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_save_skill_rejects_non_owner(monkeypatch):
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-save-owner", user_id="owner", sandbox_session_id="sandbox")
    manager.sessions[session.id] = session

    async def forbidden_export_skill(**_kwargs):
        raise AssertionError("save_skill should reject before exporting")

    monkeypatch.setattr(ROUTE_MODULE.exporter, "export_skill", forbidden_export_skill)

    try:
        user = type("User", (), {"id": "intruder"})()
        with pytest.raises(ROUTE_MODULE.HTTPException) as exc_info:
            await ROUTE_MODULE.save_skill(
                session.id,
                ROUTE_MODULE.SaveSkillRequest(skill_name="stolen", description=""),
                user,
            )
        assert exc_info.value.status_code == 403
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_agent_confirm_rejects_non_owner():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="route-agent-owner", user_id="owner", sandbox_session_id="sandbox")
    manager.sessions[session.id] = session

    class FakeAgent:
        resolved = False

        def resolve_confirm(self, _approved):
            self.resolved = True

    agent = FakeAgent()
    ROUTE_MODULE._active_agents[session.id] = agent

    try:
        user = type("User", (), {"id": "intruder"})()
        with pytest.raises(ROUTE_MODULE.HTTPException) as exc_info:
            await ROUTE_MODULE.agent_confirm(session.id, ROUTE_MODULE.ConfirmRequest(approved=True), user)
        assert exc_info.value.status_code == 403
        assert agent.resolved is False
    finally:
        ROUTE_MODULE._active_agents.pop(session.id, None)
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

