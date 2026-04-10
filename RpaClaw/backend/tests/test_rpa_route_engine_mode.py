import copy
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import backend.route.rpa as rpa_route
from backend.rpa.manager import RPASessionManager
from backend.user.dependencies import User


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(rpa_route.router, prefix="/api/v1/rpa")
    app.dependency_overrides[rpa_route.get_current_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    return TestClient(app)


def _engine_session_payload() -> dict:
    return {
        "id": "session-1",
        "userId": "user-1",
        "sandboxSessionId": "sandbox-1",
        "status": "recording",
        "activePageAlias": "page-2",
        "pages": [
            {
                "alias": "page-1",
                "title": "Search",
                "url": "https://example.com",
            },
            {
                "alias": "page-2",
                "title": "Popup",
                "url": "https://example.com/popup",
                "openerPageAlias": "page-1",
            },
        ],
        "actions": [
            {
                "id": "action-1",
                "kind": "click",
                "pageAlias": "page-1",
                "framePath": ["iframe[name='editor']"],
                "locator": {
                    "selector": 'internal:role=button[name="Save"]',
                    "locatorAst": {"kind": "role", "role": "button", "name": "Save"},
                },
                "locatorAlternatives": [
                    {
                        "selector": 'internal:role=button[name="Save"]',
                        "locatorAst": {"kind": "role", "role": "button", "name": "Save"},
                        "score": 100,
                        "matchCount": 1,
                        "visibleMatchCount": 1,
                        "isSelected": True,
                        "engine": "playwright",
                        "reason": "strict unique role match",
                    },
                    {
                        "selector": 'internal:testid=[data-testid="save-button"]',
                        "locatorAst": {"kind": "testId", "value": "save-button"},
                        "score": 1,
                        "matchCount": 1,
                        "visibleMatchCount": 1,
                        "isSelected": False,
                        "engine": "playwright",
                        "reason": "stable test id",
                    },
                ],
                "validation": {"status": "ok"},
                "signals": {"popup": {"targetPageAlias": "page-2"}},
            }
        ],
    }


class _FakeGateway:
    def __init__(self):
        self.calls = []
        self.session = _engine_session_payload()

    async def start_session(self, user_id: str, sandbox_session_id: str):
        self.calls.append(("start_session", user_id, sandbox_session_id))
        session = copy.deepcopy(self.session)
        session["userId"] = user_id
        session["sandboxSessionId"] = sandbox_session_id
        return {"session": session}

    async def get_session(self, session_id: str):
        self.calls.append(("get_session", session_id))
        assert session_id == self.session["id"]
        return {"session": copy.deepcopy(self.session)}

    async def activate_tab(self, session_id: str, page_alias: str):
        self.calls.append(("activate_tab", session_id, page_alias))
        self.session["activePageAlias"] = page_alias
        return {"session": copy.deepcopy(self.session)}

    async def navigate_session(self, session_id: str, url: str):
        self.calls.append(("navigate_session", session_id, url))
        normalized = url if url.startswith("http") else f"https://{url}"
        active_alias = self.session["activePageAlias"]
        for page in self.session["pages"]:
            if page["alias"] == active_alias:
                page["url"] = normalized
        return {"session": copy.deepcopy(self.session)}

    async def stop_session(self, session_id: str):
        self.calls.append(("stop_session", session_id))
        self.session["status"] = "stopped"
        return {"session": copy.deepcopy(self.session)}


def test_start_session_uses_engine_mode(monkeypatch):
    manager = RPASessionManager()
    gateway = _FakeGateway()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    async def fail_legacy_start_session(*args, **kwargs):
        raise RuntimeError("legacy _start_legacy_session should not be used")

    monkeypatch.setattr(manager, "_gateway", gateway)
    monkeypatch.setattr(manager, "_start_legacy_session", fail_legacy_start_session, raising=False)

    client = _build_client()
    response = client.post("/api/v1/rpa/session/start", json={"sandbox_session_id": "sandbox-1"})

    assert response.status_code == 200
    assert response.json()["session"]["id"] == "session-1"
    assert gateway.calls == [("start_session", "user-1", "sandbox-1")]


def test_tabs_route_uses_engine_compat_mapping(monkeypatch):
    manager = RPASessionManager()
    gateway = _FakeGateway()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(manager, "_gateway", gateway)

    client = _build_client()
    response = client.get("/api/v1/rpa/session/session-1/tabs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_tab_id"] == "page-2"
    assert payload["tabs"] == [
        {
            "tab_id": "page-1",
            "title": "Search",
            "url": "https://example.com",
            "opener_tab_id": None,
            "status": "open",
            "active": False,
        },
        {
            "tab_id": "page-2",
            "title": "Popup",
            "url": "https://example.com/popup",
            "opener_tab_id": "page-1",
            "status": "open",
            "active": True,
        },
    ]


def test_get_session_route_returns_404_when_engine_session_is_missing(monkeypatch):
    manager = RPASessionManager()

    class _MissingGateway:
        async def get_session(self, session_id: str):
            assert session_id == "session-1"
            return None

    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(manager, "_gateway", _MissingGateway())

    client = _build_client()
    response = client.get("/api/v1/rpa/session/session-1")

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_promote_locator_route_uses_engine_compat_step(monkeypatch):
    manager = RPASessionManager()
    gateway = _FakeGateway()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(manager, "_gateway", gateway)

    client = _build_client()
    response = client.post(
        "/api/v1/rpa/session/session-1/step/0/locator",
        json={"candidate_index": 1},
    )

    assert response.status_code == 200
    step = response.json()["step"]
    assert step["target"] == 'internal:testid=[data-testid="save-button"]'
    assert step["locator_candidates"][0]["selected"] is False
    assert step["locator_candidates"][1]["selected"] is True


def test_promoted_locator_survives_followup_session_fetch(monkeypatch):
    manager = RPASessionManager()
    gateway = _FakeGateway()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(manager, "_gateway", gateway)

    client = _build_client()
    promote_response = client.post(
        "/api/v1/rpa/session/session-1/step/0/locator",
        json={"candidate_index": 1},
    )

    assert promote_response.status_code == 200

    session_response = client.get("/api/v1/rpa/session/session-1")

    assert session_response.status_code == 200
    step = session_response.json()["session"]["steps"][0]
    assert step["target"] == 'internal:testid=[data-testid="save-button"]'
    assert step["locator_candidates"][0]["selected"] is False
    assert step["locator_candidates"][1]["selected"] is True


def test_activate_tab_route_uses_node_mode_compat_state(monkeypatch):
    manager = RPASessionManager()
    gateway = _FakeGateway()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(manager, "_gateway", gateway)

    client = _build_client()
    activate_response = client.post("/api/v1/rpa/session/session-1/tabs/page-1/activate")

    assert activate_response.status_code == 200
    payload = activate_response.json()
    assert payload["active_tab_id"] == "page-1"
    assert payload["result"] == {"tab_id": "page-1", "source": "user"}
    assert payload["tabs"][0]["active"] is True
    assert payload["tabs"][1]["active"] is False

    tabs_response = client.get("/api/v1/rpa/session/session-1/tabs")

    assert tabs_response.status_code == 200
    assert tabs_response.json()["active_tab_id"] == "page-1"
    assert tabs_response.json()["tabs"][0]["active"] is True
    assert ("activate_tab", "session-1", "page-1") in gateway.calls


def test_navigate_route_uses_node_mode_compat_state(monkeypatch):
    manager = RPASessionManager()
    gateway = _FakeGateway()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(manager, "_gateway", gateway)

    client = _build_client()
    navigate_response = client.post(
        "/api/v1/rpa/session/session-1/navigate",
        json={"url": "docs.example.com"},
    )

    assert navigate_response.status_code == 200
    payload = navigate_response.json()
    assert payload["result"] == {"tab_id": "page-2", "url": "https://docs.example.com"}
    assert payload["tabs"][1]["url"] == "https://docs.example.com"
    assert payload["active_tab_id"] == "page-2"

    tabs_response = client.get("/api/v1/rpa/session/session-1/tabs")

    assert tabs_response.status_code == 200
    assert tabs_response.json()["tabs"][1]["url"] == "https://docs.example.com"
    assert ("navigate_session", "session-1", "https://docs.example.com") in gateway.calls


def test_stop_route_clears_node_mode_compat_state(monkeypatch):
    manager = RPASessionManager()
    gateway = _FakeGateway()
    monkeypatch.setattr(rpa_route, "rpa_manager", manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(manager, "_gateway", gateway)

    client = _build_client()
    promote_response = client.post(
        "/api/v1/rpa/session/session-1/step/0/locator",
        json={"candidate_index": 1},
    )
    assert promote_response.status_code == 200

    stop_response = client.post("/api/v1/rpa/session/session-1/stop")

    assert stop_response.status_code == 200
    assert stop_response.json()["session"]["status"] == "stopped"
    assert "session-1" not in manager.sessions
    assert "session-1" not in manager._compat_tabs
    assert "session-1" not in manager._engine_sessions
    assert "session-1" not in manager._engine_locator_overrides
    assert ("stop_session", "session-1") in gateway.calls


def test_generate_route_uses_manager_codegen_in_node_mode(monkeypatch):
    class _FakeManager:
        def __init__(self):
            self.calls = []

        async def get_session(self, session_id: str):
            assert session_id == "session-1"
            return SimpleNamespace(id=session_id, user_id="user-1", steps=[])

        async def generate_script_with_engine(self, session_id: str, params: dict):
            self.calls.append(("generate", session_id, params))
            return "async def execute_skill(page, **kwargs):\n    await page.goto('https://example.com')\n"

    fake_manager = _FakeManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", fake_manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(
        rpa_route.generator,
        "generate_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy generator should not be used")),
    )

    client = _build_client()
    response = client.post("/api/v1/rpa/session/session-1/generate", json={"params": {"url": {"original_value": "https://example.com"}}})

    assert response.status_code == 200
    assert response.json()["script"].startswith("async def execute_skill")
    assert fake_manager.calls == [("generate", "session-1", {"url": {"original_value": "https://example.com"}})]


def test_test_route_uses_manager_replay_in_node_mode(monkeypatch):
    class _FakeManager:
        def __init__(self):
            self.calls = []

        async def get_session(self, session_id: str):
            assert session_id == "session-1"
            return SimpleNamespace(id=session_id, user_id="user-1", sandbox_session_id="sandbox-1", steps=[])

        async def replay_with_engine(self, session_id: str, params: dict):
            self.calls.append(("replay", session_id, params))
            return {
                "result": {
                    "success": False,
                    "output": "SKILL_ERROR: replay execution unavailable",
                    "error": "replay execution unavailable",
                    "data": {},
                },
                "logs": ["Engine replay cannot execute without a runtime adapter"],
                "script": "async def execute_skill(page, **kwargs):\n    return {}\n",
                "plan": [],
            }

    fake_manager = _FakeManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", fake_manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(
        rpa_route.executor,
        "execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy executor should not be used")),
    )

    client = _build_client()
    response = client.post("/api/v1/rpa/session/session-1/test", json={"params": {}})

    assert response.status_code == 200
    assert response.json()["result"]["success"] is False
    assert fake_manager.calls == [("replay", "session-1", {})]


def test_test_route_resolves_descriptor_params_before_node_engine_replay(monkeypatch):
    class _FakeManager:
        def __init__(self):
            self.calls = []

        async def get_session(self, session_id: str):
            assert session_id == "session-1"
            return SimpleNamespace(id=session_id, user_id="user-1", sandbox_session_id="sandbox-1", steps=[])

        async def replay_with_engine(self, session_id: str, params: dict):
            self.calls.append(("replay", session_id, params))
            return {
                "result": {"success": False, "output": "SKILL_ERROR", "error": "blocked", "data": {}},
                "logs": [],
                "script": "async def execute_skill(page, **kwargs):\n    return {}\n",
                "plan": [],
            }

    async def fake_inject_credentials(_user_id: str, params: dict, _seed: dict):
        assert params["api_key"]["credential_id"] == "cred-1"
        return {"api_key": "resolved-secret"}

    fake_manager = _FakeManager()
    monkeypatch.setattr(rpa_route, "rpa_manager", fake_manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(rpa_route, "inject_credentials", fake_inject_credentials)

    client = _build_client()
    response = client.post(
        "/api/v1/rpa/session/session-1/test",
        json={
            "params": {
                "api_key": {"sensitive": True, "credential_id": "cred-1", "original_value": "{{credential}}"},
                "query": {"original_value": "science"},
            }
        },
    )

    assert response.status_code == 200
    assert fake_manager.calls == [("replay", "session-1", {"api_key": "resolved-secret", "query": "science"})]


def test_save_route_uses_manager_codegen_in_node_mode(monkeypatch):
    class _FakeManager:
        def __init__(self):
            self.calls = []
            self.session = SimpleNamespace(id="session-1", user_id="user-1", status="recording", steps=[])

        async def get_session(self, session_id: str):
            assert session_id == "session-1"
            return self.session

        async def generate_script_with_engine(self, session_id: str, params: dict):
            self.calls.append(("generate", session_id, params))
            return "async def execute_skill(page, **kwargs):\n    await page.goto('https://example.com')\n"

    fake_manager = _FakeManager()
    exported = {}

    async def fake_export_skill(user_id: str, skill_name: str, description: str, script: str, params: dict):
        exported.update(
            {
                "user_id": user_id,
                "skill_name": skill_name,
                "description": description,
                "script": script,
                "params": params,
            }
        )
        return skill_name

    monkeypatch.setattr(rpa_route, "rpa_manager", fake_manager)
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(rpa_route.exporter, "export_skill", fake_export_skill)
    monkeypatch.setattr(
        rpa_route.generator,
        "generate_script",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy generator should not be used")),
    )

    client = _build_client()
    response = client.post(
        "/api/v1/rpa/session/session-1/save",
        json={"skill_name": "engine-skill", "description": "desc", "params": {"url": {"original_value": "https://example.com"}}},
    )

    assert response.status_code == 200
    assert response.json()["skill_name"] == "engine-skill"
    assert fake_manager.calls == [("generate", "session-1", {"url": {"original_value": "https://example.com"}})]
    assert exported["script"].startswith("async def execute_skill")


def test_chat_route_uses_engine_assistant_without_python_page(monkeypatch):
    class _FakeManager:
        async def get_session(self, session_id: str):
            assert session_id == "session-1"
            return SimpleNamespace(id=session_id, user_id="user-1", steps=[], paused=False)

        def pause_recording(self, _session_id: str):
            return None

        def resume_recording(self, _session_id: str):
            return None

        def get_page(self, _session_id: str):
            return None

        async def capture_engine_snapshot(self, session_id: str):
            assert session_id == "session-1"
            return {"url": "https://example.com", "title": "Example", "frames": []}

        async def execute_engine_assistant_intent(self, session_id: str, intent: dict):
            assert session_id == "session-1"
            assert intent["action"] == "click"
            return {"success": True, "output": "ok", "step": {"action": "click", "source": "ai"}}

        async def add_step(self, session_id: str, step: dict):
            assert session_id == "session-1"
            assert step["action"] == "click"

    async def fake_chat_with_engine(*, session_id: str, message: str, steps: list, model_config, snapshot_provider, intent_executor):
        assert session_id == "session-1"
        assert message == "click it"
        assert steps == []
        snapshot = await snapshot_provider()
        assert snapshot["url"] == "https://example.com"
        result = await intent_executor({"action": "click", "resolved": {"frame_path": [], "locator": {"method": "css", "value": "#target"}}})
        yield {"event": "result", "data": {**result, "step": result["step"]}}
        yield {"event": "done", "data": {}}

    monkeypatch.setattr(rpa_route, "rpa_manager", _FakeManager())
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    async def fake_model_config(_user_id: str):
        return {}

    monkeypatch.setattr(rpa_route, "_resolve_user_model_config", fake_model_config)
    monkeypatch.setattr(rpa_route.assistant, "chat_with_engine", fake_chat_with_engine)

    client = _build_client()
    response = client.post(
        "/api/v1/rpa/session/session-1/chat",
        json={"message": "click it", "mode": "chat"},
    )

    assert response.status_code == 200
    assert "event: result" in response.text
    assert '"success": true' in response.text


def test_chat_route_uses_engine_react_agent_without_python_page(monkeypatch):
    class _FakeManager:
        async def get_session(self, session_id: str):
            assert session_id == "session-1"
            return SimpleNamespace(id=session_id, user_id="user-1", steps=[], paused=False)

        def pause_recording(self, _session_id: str):
            return None

        def resume_recording(self, _session_id: str):
            return None

        def get_page(self, _session_id: str):
            return None

        async def capture_engine_snapshot(self, session_id: str):
            assert session_id == "session-1"
            return {"url": "https://example.com", "title": "Example", "frames": []}

        async def execute_engine_assistant_intent(self, session_id: str, intent: dict):
            assert session_id == "session-1"
            assert intent["action"] == "click"
            return {"success": True, "output": "ok", "step": {"action": "click", "source": "ai"}}

        async def add_step(self, session_id: str, step: dict):
            assert session_id == "session-1"
            assert step["action"] == "click"

    class _FakeAgent:
        async def run_with_engine(self, *, session_id: str, goal: str, existing_steps: list, model_config, snapshot_provider, intent_executor):
            assert session_id == "session-1"
            assert goal == "click it"
            assert existing_steps == []
            snapshot = await snapshot_provider()
            assert snapshot["url"] == "https://example.com"
            result = await intent_executor({"action": "click", "resolved": {"frame_path": [], "locator": {"method": "css", "value": "#target"}}})
            yield {"event": "agent_step_done", "data": {"step": result["step"]}}
            yield {"event": "agent_done", "data": {"total_steps": 1}}

    monkeypatch.setattr(rpa_route, "rpa_manager", _FakeManager())
    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")

    async def fake_model_config(_user_id: str):
        return {}

    monkeypatch.setattr(rpa_route, "_resolve_user_model_config", fake_model_config)
    monkeypatch.setattr(rpa_route, "_active_agents", {})
    monkeypatch.setattr(rpa_route, "RPAReActAgent", _FakeAgent)

    client = _build_client()
    response = client.post(
        "/api/v1/rpa/session/session-1/chat",
        json={"message": "click it", "mode": "react"},
    )

    assert response.status_code == 200
    assert "ReAct mode is not yet supported in node engine mode" not in response.text
    assert "event: agent_step_done" in response.text
    assert "event: agent_done" in response.text
