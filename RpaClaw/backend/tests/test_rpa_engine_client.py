import asyncio
import sys
from pathlib import Path

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.rpa.engine_client import RPAEngineClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | Exception):
        self._response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.calls.append((url, headers))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def post(self, url, headers=None, json=None):
        self.calls.append((url, headers, json))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_health_check_raises_on_503(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=503))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="rpa engine health check failed"):
        asyncio.run(client.health())


def test_health_check_raises_on_transport_error(monkeypatch):
    fake_client = _FakeAsyncClient(httpx.ConnectError("boom"))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="rpa engine health check failed"):
        asyncio.run(client.health())


def test_health_check_returns_payload_and_sends_auth_header(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=200, payload={"status": "ok"}))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="secret")

    response = asyncio.run(client.health())

    assert response.status == "ok"
    assert fake_client.calls == [
        ("http://127.0.0.1:3310/health", {"Authorization": "Bearer secret"})
    ]


def test_get_session_returns_payload(monkeypatch):
    payload = {
        "session": {
            "id": "session-1",
            "userId": "u1",
            "status": "idle",
            "sandboxSessionId": "sandbox-1",
            "activePageAlias": None,
            "pages": [],
            "actions": [],
        }
    }
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=200, payload=payload))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    response = asyncio.run(client.get_session("session-1"))

    assert response["session"]["sandboxSessionId"] == "sandbox-1"
    assert fake_client.calls == [
        ("http://127.0.0.1:3310/sessions/session-1", {})
    ]


def test_get_session_returns_none_on_404(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=404, payload={"message": "unknown session session-1"}))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    response = asyncio.run(client.get_session("session-1"))

    assert response is None
    assert fake_client.calls == [
        ("http://127.0.0.1:3310/sessions/session-1", {})
    ]


def test_get_session_includes_engine_error_message_on_500(monkeypatch):
    fake_client = _FakeAsyncClient(
        _FakeResponse(
            status_code=500,
            payload={"message": "session cache unavailable"},
        )
    )
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="session cache unavailable"):
        asyncio.run(client.get_session("session-1"))


def test_session_control_calls_post_expected_payloads(monkeypatch):
    payload = {
        "session": {
            "id": "session-1",
            "userId": "u1",
            "status": "recording",
            "sandboxSessionId": "sandbox-1",
            "activePageAlias": "page-1",
            "pages": [{"alias": "page-1", "url": "https://docs.example.com"}],
            "actions": [],
        }
    }
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=200, payload=payload))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    activate = asyncio.run(client.activate_tab("session-1", "page-1"))
    navigate = asyncio.run(client.navigate_session("session-1", "docs.example.com"))
    stopped = asyncio.run(client.stop_session("session-1"))

    assert activate["session"]["activePageAlias"] == "page-1"
    assert navigate["session"]["pages"][0]["url"] == "https://docs.example.com"
    assert stopped["session"]["id"] == "session-1"
    assert fake_client.calls == [
        ("http://127.0.0.1:3310/sessions/session-1/activate", {}, {"pageAlias": "page-1"}),
        ("http://127.0.0.1:3310/sessions/session-1/navigate", {}, {"url": "docs.example.com"}),
        ("http://127.0.0.1:3310/sessions/session-1/stop", {}, None),
    ]


def test_codegen_and_replay_calls_post_expected_payloads(monkeypatch):
    codegen_payload = {"script": "async def execute_skill(page, **kwargs):\n    return {}\n"}
    replay_payload = {
        "result": {"success": False, "output": "SKILL_ERROR: unavailable", "error": "unavailable", "data": {}},
        "logs": ["blocked"],
        "script": "async def execute_skill(page, **kwargs):\n    return {}\n",
        "plan": [],
    }
    clients = [
        _FakeAsyncClient(_FakeResponse(status_code=200, payload=codegen_payload)),
        _FakeAsyncClient(_FakeResponse(status_code=200, payload=replay_payload)),
    ]
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: clients.pop(0))
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")
    actions = [{"id": "action-1"}]
    params = {"url": {"original_value": "https://example.com"}}

    codegen = asyncio.run(client.generate_script("session-1", actions, params))
    replay = asyncio.run(client.replay("session-1", actions, params))

    assert codegen["script"].startswith("async def execute_skill")
    assert replay["result"]["success"] is False
    assert clients == []


def test_assistant_snapshot_and_execute_calls_expected_payloads(monkeypatch):
    snapshot_payload = {
        "snapshot": {
            "url": "https://example.com",
            "title": "Example",
            "frames": [],
        }
    }
    execute_payload = {
        "success": True,
        "output": "ok",
        "step": {"action": "click"},
    }
    clients = [
        _FakeAsyncClient(_FakeResponse(status_code=200, payload=snapshot_payload)),
        _FakeAsyncClient(_FakeResponse(status_code=200, payload=execute_payload)),
    ]
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: clients.pop(0))
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    snapshot = asyncio.run(client.capture_snapshot("session-1"))
    executed = asyncio.run(
        client.execute_assistant_intent(
            "session-1",
            {"action": "click", "resolved": {"frame_path": [], "locator": {"method": "css", "value": "#target"}}},
        )
    )

    assert snapshot["snapshot"]["url"] == "https://example.com"
    assert executed["success"] is True
    assert clients == []


def test_capture_snapshot_raises_session_not_found_on_404(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=404, payload={"message": "unknown session session-1"}))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="rpa engine session not found"):
        asyncio.run(client.capture_snapshot("session-1"))


def test_execute_assistant_intent_raises_session_not_found_on_404(monkeypatch):
    fake_client = _FakeAsyncClient(_FakeResponse(status_code=404, payload={"message": "unknown session session-1"}))
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="rpa engine session not found"):
        asyncio.run(client.execute_assistant_intent("session-1", {"action": "click"}))


def test_execute_assistant_intent_includes_engine_error_message_on_500(monkeypatch):
    fake_client = _FakeAsyncClient(
        _FakeResponse(
            status_code=500,
            payload={"message": "locator.fill: Error: strict mode violation"},
        )
    )
    monkeypatch.setattr("backend.rpa.engine_client.httpx.AsyncClient", lambda *args, **kwargs: fake_client)
    client = RPAEngineClient(base_url="http://127.0.0.1:3310", auth_token="")

    with pytest.raises(RuntimeError, match="strict mode violation"):
        asyncio.run(client.execute_assistant_intent("session-1", {"action": "fill"}))
