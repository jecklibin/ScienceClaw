from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.runtime.models import SessionRuntimeRecord
from backend.user.dependencies import User


class _FakeManager:
    async def ensure_runtime(self, session_id: str, user_id: str) -> SessionRuntimeRecord:
        return SessionRuntimeRecord(
            session_id=session_id,
            user_id=user_id,
            namespace="beta",
            pod_name="rpaclaw-sess-sess-1",
            service_name="rpaclaw-sess-sess-1-svc",
            rest_base_url="http://rpaclaw-sess-sess-1-svc:8080",
            route_base_url="http://aio-route.local/session/sess-1",
            runtime_token="runtime-token",
            status="ready",
            metadata={
                "runtime_status_reason": "adapter_contract_mismatch",
                "adapter_contract_version": "v0",
                "supported_adapter_contract_version": "v1",
                "runtime_token": "must-not-leak",
                "nested": {
                    "api_token": "nested-secret",
                    "safe_note": "kept",
                },
            },
        )

    async def get_runtime(self, session_id: str, refresh: bool = False):
        if session_id == "missing":
            return None
        record = await self.ensure_runtime(session_id, "user-1")
        if refresh:
            record.status = "refreshed"
        return record

    async def list_runtimes(self, user_id: str | None = None, refresh: bool = False):
        records = [
            SessionRuntimeRecord(
                session_id="sess-1",
                user_id="user-1",
                namespace="beta",
                pod_name="rpaclaw-sess-sess-1",
                service_name="rpaclaw-sess-sess-1-svc",
                rest_base_url="http://rpaclaw-sess-sess-1-svc:8080",
                status="ready",
            ),
            SessionRuntimeRecord(
                session_id="sess-2",
                user_id="user-2",
                namespace="beta",
                pod_name="rpaclaw-sess-sess-2",
                service_name="rpaclaw-sess-sess-2-svc",
                rest_base_url="http://rpaclaw-sess-sess-2-svc:8080",
                status="ready",
            ),
        ]
        if user_id:
            records = [record for record in records if record.user_id == user_id]
        if refresh:
            for record in records:
                record.status = "refreshed"
        return records


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._json_body

    @property
    def content(self):
        import json

        return json.dumps(self._json_body).encode("utf-8")


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, headers=None, content=None, params=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
                "params": params,
            }
        )
        return _FakeResponse(
            json_body={
                "proxied_to": url,
                "method": method,
            }
        )


class _FakeUnsafeHeaderAsyncClient(_FakeAsyncClient):
    async def request(self, method, url, headers=None, content=None, params=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
                "params": params,
            }
        )
        return _FakeResponse(
            json_body={"ok": True},
            headers={
                "content-type": "application/json",
                "set-cookie": "adapter_session=secret; HttpOnly",
                "authorization": "Bearer adapter-token",
                "www-authenticate": "Bearer realm=adapter",
                "x-adapter-debug": "kept",
            },
        )


async def _allow_runtime_session(session_id, current_user):
    return True


def test_runtime_proxy_path_includes_session_id(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)
    monkeypatch.setattr(runtime_proxy.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/sess-1/http/v1/browser/info")

    assert response.status_code == 200
    assert response.json()["proxied_to"] == "http://aio-route.local/session/sess-1/v1/browser/info"
    assert fake_client.calls[0]["method"] == "GET"
    assert fake_client.calls[0]["headers"]["authorization"] == "Bearer runtime-token"


def test_runtime_proxy_http_replaces_client_authorization_with_runtime_token(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)
    monkeypatch.setattr(runtime_proxy.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/runtime/session/sess-1/http/health",
        headers={"Authorization": "Bearer user-session-token"},
    )

    assert response.status_code == 200
    assert fake_client.calls[0]["headers"]["authorization"] == "Bearer runtime-token"


def test_runtime_proxy_http_does_not_forward_host_cookie_or_proxy_auth(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)
    monkeypatch.setattr(runtime_proxy.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/runtime/session/sess-1/http/health",
        headers={
            "Cookie": "session=host-secret",
            "Proxy-Authorization": "Basic host-secret",
            "X-Trace-Id": "trace-1",
        },
    )

    assert response.status_code == 200
    upstream_headers = fake_client.calls[0]["headers"]
    assert "cookie" not in {key.lower() for key in upstream_headers}
    assert "proxy-authorization" not in {key.lower() for key in upstream_headers}
    assert upstream_headers["authorization"] == "Bearer runtime-token"
    assert upstream_headers["x-trace-id"] == "trace-1"


def test_runtime_proxy_websocket_headers_do_not_forward_client_handshake_headers():
    import backend.route.runtime_proxy as runtime_proxy

    runtime = SessionRuntimeRecord(
        session_id="sess-1",
        user_id="user-1",
        namespace="beta",
        pod_name="rpaclaw-sess-sess-1",
        service_name="rpaclaw-sess-sess-1-svc",
        rest_base_url="http://rpaclaw-sess-sess-1-svc:8080",
        route_base_url="http://aio-route.local/session/sess-1",
        runtime_token="runtime-token",
        status="ready",
    )

    headers = runtime_proxy._runtime_adapter_headers(
        runtime,
        {
            "Authorization": "Bearer user-session-token",
            "Cookie": "session=host-secret",
            "Sec-WebSocket-Key": "client-key",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Extensions": "permessage-deflate",
            "Sec-WebSocket-Protocol": "chat",
            "X-Trace-Id": "trace-1",
        },
    )

    lowered = {key.lower() for key in headers}
    assert "cookie" not in lowered
    assert "sec-websocket-key" not in lowered
    assert "sec-websocket-version" not in lowered
    assert "sec-websocket-extensions" not in lowered
    assert "sec-websocket-protocol" not in lowered
    assert headers["authorization"] == "Bearer runtime-token"
    assert headers["X-Trace-Id"] == "trace-1"


def test_runtime_proxy_http_filters_sensitive_upstream_response_headers(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    fake_client = _FakeUnsafeHeaderAsyncClient()
    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)
    monkeypatch.setattr(runtime_proxy.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/sess-1/http/health")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert "authorization" not in response.headers
    assert "www-authenticate" not in response.headers
    assert response.headers["x-adapter-debug"] == "kept"


def test_build_runtime_ws_url_preserves_query():
    from backend.route.runtime_proxy import _build_runtime_ws_url

    url = _build_runtime_ws_url(
        "http://rpaclaw-sess-sess-1-svc:8080",
        "websockify",
        "autoconnect=true&resize=scale",
    )

    assert url == "ws://rpaclaw-sess-sess-1-svc:8080/websockify?autoconnect=true&resize=scale"


def test_runtime_status_returns_existing_runtime(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/sess-1/status?refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["session_id"] == "sess-1"
    assert payload["runtime"]["status"] == "refreshed"
    assert "runtime_token" not in payload["runtime"]
    assert payload["runtime"]["metadata"] == {
        "runtime_status_reason": "adapter_contract_mismatch",
        "adapter_contract_version": "v0",
        "supported_adapter_contract_version": "v1",
        "nested": {
            "safe_note": "kept",
        },
    }


def test_runtime_status_returns_missing_without_creating_runtime(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/missing/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "missing",
        "session_id": "missing",
        "runtime": None,
    }


def test_runtime_status_hides_runtime_owned_by_another_user(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    class _OtherUserManager(_FakeManager):
        async def get_runtime(self, session_id: str, refresh: bool = False):
            return SessionRuntimeRecord(
                session_id=session_id,
                user_id="user-2",
                namespace="beta",
                pod_name="rpaclaw-sess-sess-1",
                service_name="rpaclaw-sess-sess-1-svc",
                rest_base_url="http://rpaclaw-sess-sess-1-svc:8080",
                status="ready",
            )

    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _OtherUserManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/sess-1/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "missing",
        "session_id": "sess-1",
        "runtime": None,
    }


def test_runtime_sessions_lists_only_current_user_runtimes(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/sessions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert [item["session_id"] for item in payload["runtimes"]] == ["sess-1"]
    assert "runtime_token" not in payload["runtimes"][0]


def test_runtime_sessions_can_refresh(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/sessions?refresh=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtimes"][0]["status"] == "refreshed"


def test_runtime_sessions_hide_unowned_session_records(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    class _MixedOwnershipManager(_FakeManager):
        async def list_runtimes(self, user_id: str | None = None, refresh: bool = False):
            return [
                SessionRuntimeRecord(
                    session_id="sess-1",
                    user_id="user-1",
                    namespace="beta",
                    pod_name="rpaclaw-sess-sess-1",
                    service_name="rpaclaw-sess-sess-1-svc",
                    rest_base_url="http://rpaclaw-sess-sess-1-svc:8080",
                    status="ready",
                ),
                SessionRuntimeRecord(
                    session_id="sess-stale",
                    user_id="user-1",
                    namespace="beta",
                    pod_name="rpaclaw-sess-sess-stale",
                    service_name="rpaclaw-sess-sess-stale-svc",
                    rest_base_url="http://rpaclaw-sess-sess-stale-svc:8080",
                    status="ready",
                ),
            ]

    async def _owns_only_first(session_id, current_user):
        return session_id == "sess-1"

    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _MixedOwnershipManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _owns_only_first)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/sessions")

    assert response.status_code == 200
    payload = response.json()
    assert [item["session_id"] for item in payload["runtimes"]] == ["sess-1"]


def test_runtime_proxy_http_rejects_runtime_owned_by_another_user(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    class _OtherUserManager(_FakeManager):
        async def ensure_runtime(self, session_id: str, user_id: str) -> SessionRuntimeRecord:
            return SessionRuntimeRecord(
                session_id=session_id,
                user_id="user-2",
                namespace="beta",
                pod_name="rpaclaw-sess-sess-1",
                service_name="rpaclaw-sess-sess-1-svc",
                rest_base_url="http://rpaclaw-sess-sess-1-svc:8080",
                status="ready",
            )

    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _OtherUserManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)
    monkeypatch.setattr(runtime_proxy.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/sess-1/http/v1/browser/info")

    assert response.status_code == 404
    assert fake_client.calls == []


def test_runtime_proxy_http_returns_not_ready_for_creating_runtime(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    class _CreatingManager(_FakeManager):
        async def ensure_runtime(self, session_id: str, user_id: str) -> SessionRuntimeRecord:
            record = await super().ensure_runtime(session_id, user_id)
            record.status = "creating"
            record.metadata = {
                "runtime_status_reason": "aio_status_creating",
                "runtime_token": "must-not-leak",
            }
            return record

    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _CreatingManager())
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _allow_runtime_session)
    monkeypatch.setattr(runtime_proxy.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/sess-1/http/v1/browser/info")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "runtime_not_ready",
        "session_id": "sess-1",
        "runtime": {
            "session_id": "sess-1",
            "user_id": "user-1",
            "namespace": "beta",
            "pod_name": "rpaclaw-sess-sess-1",
            "service_name": "rpaclaw-sess-sess-1-svc",
            "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
            "status": "creating",
            "sandbox_id": None,
            "route_base_url": "http://aio-route.local/session/sess-1",
            "browser_view_url": None,
            "created_at": response.json()["detail"]["runtime"]["created_at"],
            "last_used_at": response.json()["detail"]["runtime"]["last_used_at"],
            "expires_at": None,
            "metadata": {
                "runtime_status_reason": "aio_status_creating",
            },
        },
    }
    assert fake_client.calls == []


def test_runtime_status_returns_missing_when_session_is_not_owned(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    async def _deny(session_id, current_user):
        return False
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _deny)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/sess-1/status")

    assert response.status_code == 200
    assert response.json() == {
        "status": "missing",
        "session_id": "sess-1",
        "runtime": None,
    }


def test_runtime_proxy_http_rejects_unowned_session_before_runtime_creation(monkeypatch):
    import backend.route.runtime_proxy as runtime_proxy

    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(runtime_proxy, "get_session_runtime_manager", lambda: _FakeManager())
    async def _deny(session_id, current_user):
        return False
    monkeypatch.setattr(runtime_proxy, "_user_owns_runtime_session", _deny)
    monkeypatch.setattr(runtime_proxy.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    app = FastAPI()
    app.include_router(runtime_proxy.router, prefix="/api/v1")
    app.dependency_overrides[runtime_proxy.require_user] = lambda: User(
        id="user-1",
        username="tester",
        role="admin",
    )
    client = TestClient(app)

    response = client.get("/api/v1/runtime/session/sess-1/http/v1/browser/info")

    assert response.status_code == 404
    assert fake_client.calls == []
