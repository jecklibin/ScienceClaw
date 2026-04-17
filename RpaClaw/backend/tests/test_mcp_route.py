from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.mcp.models import McpServerDefinition
from backend.route import mcp as mcp_route


class _User:
    id = "user-1"


class _BindingRepo:
    def __init__(self):
        self.calls = []

    async def update_one(self, filter_doc, update_doc, upsert=False):
        self.calls.append(
            {
                "filter": filter_doc,
                "update": update_doc,
                "upsert": upsert,
            }
        )
        return 1


def _build_app():
    app = FastAPI()
    app.include_router(mcp_route.router, prefix="/api/v1")
    app.dependency_overrides[mcp_route.require_user] = lambda: _User()
    return app


def test_list_mcp_servers_returns_normalized_system_and_user_entries(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                description="System search",
                transport="streamable_http",
                default_enabled=True,
                url="https://example.test/mcp",
                headers={"Authorization": "Bearer top-secret"},
                env={"API_KEY": "system-secret"},
                credential_ref="vault://system-secret",
            )
        ],
    )

    async def fake_user_servers(user_id: str):
        assert user_id == "user-1"
        return [
            {
                "_id": "mcp_user_1",
                "user_id": "user-1",
                "name": "Private MCP",
                "slug": "private-mcp",
                "description": "Private search",
                "transport": "sse",
                "enabled": True,
                "default_enabled": False,
                "endpoint_config": {"url": "https://user.example.test/sse"},
                "credential_binding": {"credential_id": "", "headers": {}, "env": {}, "query": {}},
                "tool_policy": {"allowed_tools": ["search"], "blocked_tools": []},
            }
        ]

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)

    response = client.get("/api/v1/mcp/servers")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["server_key"] == "system:pubmed"
    assert data[0]["readonly"] is True
    assert data[1]["server_key"] == "user:mcp_user_1"
    assert data[1]["readonly"] is False
    assert set(data[0].keys()) == set(data[1].keys())
    assert "_id" not in data[1]
    assert "user_id" not in data[1]
    assert "headers" not in data[0]["endpoint_config"]
    assert "env" not in data[0]["endpoint_config"]
    assert data[0]["credential_binding"] == {}


def test_create_mcp_server_rejects_stdio_outside_local(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    monkeypatch.setattr(mcp_route.settings, "storage_backend", "docker")

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Local Python MCP",
            "transport": "stdio",
            "endpoint_config": {"command": "python", "args": ["-m", "demo"]},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "stdio MCP is only allowed in local mode"


def test_create_mcp_server_rejects_missing_required_endpoint_field():
    app = _build_app()
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Broken HTTP MCP",
            "transport": "streamable_http",
            "endpoint_config": {},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "endpoint_config.url is required for HTTP/SSE MCP"


def test_create_mcp_server_rejects_wrong_endpoint_field_types(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    monkeypatch.setattr(mcp_route.settings, "storage_backend", "local")

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Broken Local MCP",
            "transport": "stdio",
            "endpoint_config": {"command": "python", "args": "-m demo"},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "endpoint_config.args must be a list of strings"


def test_update_session_override_writes_binding_for_owned_session(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    repo = _BindingRepo()

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="user-1")

    monkeypatch.setattr(
        mcp_route,
        "async_get_science_session",
        fake_get_session,
    )
    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                transport="streamable_http",
                url="https://example.test/mcp",
            )
        ],
    )
    monkeypatch.setattr(
        mcp_route,
        "get_repository",
        lambda collection_name: repo,
    )

    response = client.put(
        "/api/v1/sessions/session-123/mcp/servers/system:pubmed",
        json={"mode": "enabled"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "session_id": "session-123",
        "server_key": "system:pubmed",
        "mode": "enabled",
    }
    assert len(repo.calls) == 1
    assert repo.calls[0]["filter"] == {
        "session_id": "session-123",
        "user_id": "user-1",
        "server_key": "system:pubmed",
    }
    assert repo.calls[0]["update"]["$set"]["mode"] == "enabled"
    assert "updated_at" in repo.calls[0]["update"]["$set"]
    assert repo.calls[0]["upsert"] is True


def test_update_session_override_rejects_other_users_session(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    repo = _BindingRepo()

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="other-user")

    monkeypatch.setattr(
        mcp_route,
        "async_get_science_session",
        fake_get_session,
    )
    monkeypatch.setattr(
        mcp_route,
        "get_repository",
        lambda collection_name: repo,
    )

    response = client.put(
        "/api/v1/sessions/session-123/mcp/servers/system:pubmed",
        json={"mode": "disabled"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Access denied"
    assert repo.calls == []


def test_update_session_override_rejects_nonexistent_server_key(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    repo = _BindingRepo()

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="user-1")

    monkeypatch.setattr(mcp_route, "async_get_science_session", fake_get_session)
    monkeypatch.setattr(mcp_route, "load_system_mcp_servers", lambda: [])

    async def fake_user_servers(user_id: str):
        return []

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: repo)

    response = client.put(
        "/api/v1/sessions/session-123/mcp/servers/system:missing",
        json={"mode": "enabled"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "MCP server not found"
    assert repo.calls == []


def test_update_session_override_rejects_foreign_user_server_key(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    repo = _BindingRepo()

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="user-1")

    monkeypatch.setattr(mcp_route, "async_get_science_session", fake_get_session)
    monkeypatch.setattr(mcp_route, "load_system_mcp_servers", lambda: [])

    async def fake_user_servers(user_id: str):
        assert user_id == "user-1"
        return [
            {
                "_id": "mcp_user_1",
                "name": "Owned MCP",
                "transport": "sse",
                "endpoint_config": {"url": "https://owned.example.test/sse"},
            }
        ]

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: repo)

    response = client.put(
        "/api/v1/sessions/session-123/mcp/servers/user:mcp_foreign",
        json={"mode": "enabled"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "MCP server not found"
    assert repo.calls == []
