import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import backend.route.rpa as rpa_route
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


def test_test_route_keeps_result_and_script_fields(monkeypatch):
    class _FakeManager:
        async def get_session(self, session_id: str):
            return SimpleNamespace(id=session_id, user_id="user-1", sandbox_session_id="sandbox-1", steps=[])

        async def replay_with_engine(self, session_id: str, params: dict):
            assert session_id == "session-1"
            assert params == {}
            return {
                "result": {"success": True, "output": "SKILL_SUCCESS"},
                "logs": ["Executing replay..."],
                "script": "async def execute_skill(page, **kwargs):\n    return {}\n",
                "plan": [{"id": "action-1"}],
            }

    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(rpa_route, "rpa_manager", _FakeManager())

    client = _build_client()
    response = client.post("/api/v1/rpa/session/session-1/test", json={"params": {}})

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["success"] is True
    assert payload["logs"] == ["Executing replay..."]
    assert payload["script"].startswith("async def execute_skill")
    assert payload["plan"] == [{"id": "action-1"}]


def test_generate_route_keeps_script_field_in_node_mode(monkeypatch):
    class _FakeManager:
        async def get_session(self, session_id: str):
            return SimpleNamespace(id=session_id, user_id="user-1", steps=[])

        async def generate_script_with_engine(self, session_id: str, params: dict):
            assert session_id == "session-1"
            assert params == {"query": {"original_value": "science"}}
            return "async def execute_skill(page, **kwargs):\n    await page.goto('https://example.com')\n"

    monkeypatch.setattr(rpa_route.settings, "rpa_engine_mode", "node")
    monkeypatch.setattr(rpa_route, "rpa_manager", _FakeManager())

    client = _build_client()
    response = client.post(
        "/api/v1/rpa/session/session-1/generate",
        json={"params": {"query": {"original_value": "science"}}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert "script" in payload
    assert "await page.goto" in payload["script"]
