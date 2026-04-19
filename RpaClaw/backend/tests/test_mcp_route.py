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


class _MemoryRepo:
    def __init__(self, docs=None):
        self.docs = {str(doc["_id"]): dict(doc) for doc in (docs or [])}
        self.calls = []

    async def find_one(self, filter_doc, projection=None):
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                return dict(doc)
        return None

    async def find_many(self, filter_doc, projection=None, sort=None, skip=0, limit=0):
        docs = [
            dict(doc)
            for doc in self.docs.values()
            if all(doc.get(key) == value for key, value in filter_doc.items())
        ]
        return docs

    async def update_one(self, filter_doc, update_doc, upsert=False):
        self.calls.append(
            {
                "filter": filter_doc,
                "update": update_doc,
                "upsert": upsert,
            }
        )
        for doc_id, doc in self.docs.items():
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                updated = dict(doc)
                updated.update(update_doc.get("$set", {}))
                self.docs[doc_id] = updated
                return 1
        return 0

    async def delete_one(self, filter_doc):
        for doc_id, doc in list(self.docs.items()):
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                del self.docs[doc_id]
                return 1
        return 0


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
    assert data[0]["endpoint_config"]["headers"] == {"Authorization": "Bearer top-secret"}
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


def test_create_mcp_server_rejects_boolean_timeout(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Broken HTTP Timeout MCP",
            "transport": "streamable_http",
            "endpoint_config": {"url": "https://example.test/mcp", "timeout_ms": True},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "endpoint_config.timeout_ms must be a positive integer"


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
                headers={"Authorization": "Bearer top-secret"},
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


def test_get_mcp_server_returns_system_detail_by_server_key(monkeypatch):
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
                tool_policy={"allowed_tools": ["search_articles"]},
            )
        ],
    )
    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", lambda user_id: [])

    response = client.get("/api/v1/mcp/servers/system:pubmed")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["server_key"] == "system:pubmed"
    assert data["readonly"] is True
    assert data["endpoint_config"]["headers"] == {"Authorization": "Bearer top-secret"}
    assert data["tool_policy"]["allowed_tools"] == ["search_articles"]


def test_update_mcp_server_updates_owned_user_doc(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    repo = _MemoryRepo(
        [
            {
                "_id": "mcp_user_1",
                "user_id": "user-1",
                "name": "Private MCP",
                "description": "Before",
                "transport": "streamable_http",
                "enabled": True,
                "default_enabled": False,
                "endpoint_config": {"url": "https://old.example.test/mcp", "timeout_ms": 20000, "env": {}, "headers": {}},
                "credential_binding": {"credential_id": "", "headers": {}, "env": {}, "query": {}},
                "tool_policy": {"allowed_tools": [], "blocked_tools": []},
            }
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda _: repo)

    response = client.put(
        "/api/v1/mcp/servers/mcp_user_1",
        json={
            "name": "Updated MCP",
            "description": "After",
            "transport": "streamable_http",
            "enabled": False,
            "default_enabled": True,
            "endpoint_config": {
                "url": "https://new.example.test/mcp",
                "timeout_ms": 30000,
                "headers": {"Authorization": "Bearer user-token"},
            },
            "credential_binding": {"credential_id": "cred-1"},
            "tool_policy": {"allowed_tools": ["search_articles"]},
        },
    )

    assert response.status_code == 200
    assert repo.docs["mcp_user_1"]["name"] == "Updated MCP"
    assert repo.docs["mcp_user_1"]["enabled"] is False
    assert repo.docs["mcp_user_1"]["default_enabled"] is True
    assert repo.docs["mcp_user_1"]["endpoint_config"]["url"] == "https://new.example.test/mcp"
    assert repo.docs["mcp_user_1"]["endpoint_config"]["headers"] == {"Authorization": "Bearer user-token"}
    assert repo.docs["mcp_user_1"]["credential_binding"]["credential_id"] == "cred-1"


def test_update_mcp_server_persists_multiple_credential_bindings(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    repo = _MemoryRepo(
        [
            {
                "_id": "mcp_user_1",
                "user_id": "user-1",
                "name": "Private MCP",
                "description": "Before",
                "transport": "streamable_http",
                "enabled": True,
                "default_enabled": False,
                "endpoint_config": {"url": "https://old.example.test/mcp", "timeout_ms": 20000, "env": {}, "headers": {}},
                "credential_binding": {"credential_id": "", "credentials": [], "headers": {}, "env": {}, "query": {}},
                "tool_policy": {"allowed_tools": [], "blocked_tools": []},
            }
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda _: repo)

    response = client.put(
        "/api/v1/mcp/servers/mcp_user_1",
        json={
            "name": "Updated MCP",
            "description": "After",
            "transport": "streamable_http",
            "enabled": True,
            "default_enabled": False,
            "endpoint_config": {
                "url": "https://new.example.test/mcp",
                "timeout_ms": 30000,
                "headers": {"Accept": "application/json"},
            },
            "credential_binding": {
                "credentials": [
                    {"alias": "github", "credential_id": "cred-github"},
                    {"alias": "sentry", "credential_id": "cred-sentry"},
                ],
                "headers": {
                    "Authorization": "Bearer {{ github.password }}",
                    "X-Sentry-Token": "{{ sentry.password }}",
                },
                "env": {"GITHUB_USER": "{{ github.username }}"},
                "query": {"api_key": "{{ sentry.password }}"},
            },
            "tool_policy": {"allowed_tools": ["search_articles"], "blocked_tools": []},
        },
    )

    assert response.status_code == 200
    binding = repo.docs["mcp_user_1"]["credential_binding"]
    assert binding["credential_id"] == ""
    assert binding["credentials"] == [
        {"alias": "github", "credential_id": "cred-github"},
        {"alias": "sentry", "credential_id": "cred-sentry"},
    ]
    assert binding["headers"]["Authorization"] == "Bearer {{ github.password }}"
    assert binding["env"] == {"GITHUB_USER": "{{ github.username }}"}
    assert binding["query"] == {"api_key": "{{ sentry.password }}"}


def test_delete_mcp_server_removes_owned_user_doc(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    repo = _MemoryRepo(
        [
            {
                "_id": "mcp_user_1",
                "user_id": "user-1",
                "name": "Private MCP",
                "transport": "sse",
                "endpoint_config": {"url": "https://user.example.test/sse"},
            }
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda _: repo)

    response = client.delete("/api/v1/mcp/servers/mcp_user_1")

    assert response.status_code == 200
    assert response.json()["data"] == {"id": "mcp_user_1", "deleted": True}
    assert repo.docs == {}


def test_discover_tools_returns_marshaled_tools(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                transport="streamable_http",
                url="https://example.test/mcp",
                headers={"Authorization": "Bearer top-secret"},
            )
        ],
    )
    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", lambda user_id: [])

    class _Runtime:
        async def list_tools(self):
            return [
                {
                    "name": "search_articles",
                    "description": "Search PubMed",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                }
            ]

    class _RuntimeFactory:
        def create_runtime(self, server):
            assert server.id == "pubmed"
            assert server.headers == {"Authorization": "Bearer top-secret"}
            return _Runtime()

    monkeypatch.setattr(mcp_route, "McpSdkRuntimeFactory", lambda: _RuntimeFactory())

    response = client.post("/api/v1/mcp/servers/system:pubmed/discover-tools")

    assert response.status_code == 200
    assert response.json()["data"]["tools"] == [
        {
            "name": "search_articles",
            "description": "Search PubMed",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
    ]


def test_discover_tools_for_rpa_gateway_uses_internal_registry(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="rpa_gateway",
                name="RPA Tool Gateway",
                description="Unified entry for published RPA MCP tools",
                transport="streamable_http",
                url="http://localhost:12001/api/v1/rpa-mcp/mcp",
            )
        ],
    )
    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", lambda user_id: [])

    async def fake_rpa_gateway_tools(user_id: str):
        assert user_id == "user-1"
        return [
            {
                "name": "rpa_download_invoice",
                "description": "Download invoice",
                "input_schema": {"type": "object", "properties": {"month": {"type": "string"}}},
                "output_schema": {"type": "object", "properties": {"success": {"type": "boolean"}}},
            }
        ]

    class _RuntimeFactory:
        def create_runtime(self, server):
            raise AssertionError("RPA gateway discovery should not use the generic MCP SDK runtime")

    monkeypatch.setattr(mcp_route, "_build_rpa_gateway_tools", fake_rpa_gateway_tools, raising=False)
    monkeypatch.setattr(mcp_route, "McpSdkRuntimeFactory", lambda: _RuntimeFactory())

    response = client.post("/api/v1/mcp/servers/system:rpa_gateway/discover-tools")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "server_key": "system:rpa_gateway",
        "tools": [
            {
                "name": "rpa_download_invoice",
                "description": "Download invoice",
                "input_schema": {"type": "object", "properties": {"month": {"type": "string"}}},
            }
        ],
        "tool_count": 1,
    }


def test_discover_tools_resolves_user_mcp_credentials_before_runtime(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(mcp_route, "load_system_mcp_servers", lambda: [])

    async def fake_user_servers(user_id: str):
        return [
            {
                "_id": "mcp_user_1",
                "user_id": user_id,
                "name": "Private MCP",
                "description": "Private search",
                "transport": "streamable_http",
                "enabled": True,
                "default_enabled": False,
                "endpoint_config": {
                    "url": "https://user.example.test/mcp",
                    "headers": {"Accept": "application/json"},
                    "env": {"STATIC_ENV": "1"},
                    "timeout_ms": 20000,
                },
                "credential_binding": {
                    "credentials": [{"alias": "github", "credential_id": "cred-github"}],
                    "headers": {"Authorization": "Bearer {{ github.password }}"},
                    "env": {"GITHUB_TOKEN": "{{ github.password }}"},
                    "query": {"api_key": "{{ github.password }}"},
                },
                "tool_policy": {"allowed_tools": [], "blocked_tools": []},
            }
        ]

    async def fake_apply(server, user_id: str):
        assert user_id == "user-1"
        return server.model_copy(
            update={
                "headers": {**server.headers, "Authorization": "Bearer resolved"},
                "env": {**server.env, "GITHUB_TOKEN": "resolved"},
                "url": "https://user.example.test/mcp?api_key=resolved",
            }
        )

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(mcp_route, "apply_mcp_credentials", fake_apply)

    class _Runtime:
        async def list_tools(self):
            return []

    class _RuntimeFactory:
        def create_runtime(self, server):
            assert server.id == "mcp_user_1"
            assert server.headers == {
                "Accept": "application/json",
                "Authorization": "Bearer resolved",
            }
            assert server.env == {
                "STATIC_ENV": "1",
                "GITHUB_TOKEN": "resolved",
            }
            assert server.url == "https://user.example.test/mcp?api_key=resolved"
            return _Runtime()

    monkeypatch.setattr(mcp_route, "McpSdkRuntimeFactory", lambda: _RuntimeFactory())

    response = client.post("/api/v1/mcp/servers/user:mcp_user_1/discover-tools")

    assert response.status_code == 200
    assert response.json()["data"]["tool_count"] == 0


def test_list_session_mcp_returns_session_modes_for_owner(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="user-1")

    binding_repo = _MemoryRepo(
        [
            {
                "_id": "binding-1",
                "session_id": "session-123",
                "user_id": "user-1",
                "server_key": "user:mcp_user_1",
                "mode": "disabled",
            }
        ]
    )
    monkeypatch.setattr(mcp_route, "async_get_science_session", fake_get_session)
    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                transport="streamable_http",
                default_enabled=True,
                url="https://example.test/mcp",
            )
        ],
    )

    async def fake_user_servers(user_id: str):
        return [
            {
                "_id": "mcp_user_1",
                "user_id": user_id,
                "name": "Private MCP",
                "transport": "sse",
                "default_enabled": True,
                "endpoint_config": {"url": "https://user.example.test/sse"},
            }
        ]

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(
        mcp_route,
        "get_repository",
        lambda collection_name: binding_repo if collection_name == "session_mcp_bindings" else _MemoryRepo(),
    )

    response = client.get("/api/v1/sessions/session-123/mcp")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["server_key"] == "system:pubmed"
    assert data[0]["session_mode"] == "inherit"
    assert data[0]["effective_enabled"] is True
    assert data[1]["server_key"] == "user:mcp_user_1"
    assert data[1]["session_mode"] == "disabled"
    assert data[1]["effective_enabled"] is False
