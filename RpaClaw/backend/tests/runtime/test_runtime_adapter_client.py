import pytest
import httpx

import backend.runtime.adapter_client as adapter_client_module
from backend.runtime.models import SessionRuntimeRecord
from backend.runtime.adapter_client import RuntimeAdapterClient, RuntimeAdapterClientError


class _FakeResponse:
    def __init__(self, *, json_body=None, content=b"file-bytes"):
        self._json_body = json_body or {"ok": True}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_body


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse(json_body={"method": method, "url": url, "kwargs": kwargs})


class _FakeErrorResponse:
    status_code = 403
    text = '{"detail":"Invalid runtime adapter bearer token"}'

    def __init__(self, request):
        self.request = request

    def json(self):
        return {"detail": "Invalid runtime adapter bearer token"}

    def raise_for_status(self):
        raise httpx.HTTPStatusError(
            "403 Forbidden for url with session-token",
            request=self.request,
            response=self,
        )


class _FakeErrorAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        request = httpx.Request(method, url, headers=kwargs.get("headers") or {})
        return _FakeErrorResponse(request)


class _FakeSensitiveErrorResponse:
    status_code = 500
    text = '{"detail":"adapter failed with session-token"}'

    def __init__(self, request):
        self.request = request

    def json(self):
        return {
            "detail": "adapter failed with session-token",
            "nested": {
                "runtime_token": "session-token",
                "authorization": "Bearer session-token",
            },
        }

    def raise_for_status(self):
        raise httpx.HTTPStatusError(
            "500 Server Error",
            request=self.request,
            response=self,
        )


class _FakeSensitiveErrorAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        request = httpx.Request(method, url, headers=kwargs.get("headers") or {})
        return _FakeSensitiveErrorResponse(request)


def _runtime_record() -> SessionRuntimeRecord:
    return SessionRuntimeRecord(
        session_id="sess-1",
        user_id="user-1",
        namespace="aio-local",
        pod_name="local-aio",
        service_name="local-aio",
        rest_base_url="http://localhost:18080/adapter/",
        route_base_url="http://localhost:18080/adapter/",
        runtime_token="session-token",
        status="ready",
    )


@pytest.mark.asyncio
async def test_runtime_adapter_client_calls_semantic_endpoints_with_runtime_token():
    fake_client = _FakeAsyncClient()
    client = RuntimeAdapterClient(_runtime_record(), http_client_factory=lambda **_: fake_client)

    health = await client.health()
    browser_info = await client.browser_info()
    events = await client.get_events(cursor="42")
    emitted = await client.emit_event({"event_id": "evt-1", "action": "click"})
    emitted_snapshot = await client.emit_snapshot(
        {
            "raw_snapshot": {"html": "<button>Search</button>"},
            "compact_snapshot": {"buttons": [{"text": "Search"}]},
            "page_state": {"url": "https://example.test", "title": "Example"},
        }
    )
    snapshot = await client.get_snapshot()
    execution = await client.execute_step({"instruction": "click search"})
    run = await client.run_skill({"skill_id": "skill-1", "kwargs": {"q": "paper"}})

    assert health["url"] == "http://localhost:18080/adapter/health"
    assert browser_info["url"] == "http://localhost:18080/adapter/v1/browser/info"
    assert events["kwargs"]["params"] == {"cursor": "42"}
    assert emitted["url"] == "http://localhost:18080/adapter/rpa/events/emit"
    assert emitted["kwargs"]["json"] == {"event_id": "evt-1", "action": "click"}
    assert emitted_snapshot["url"] == "http://localhost:18080/adapter/rpa/snapshot/emit"
    assert emitted_snapshot["kwargs"]["json"]["page_state"]["title"] == "Example"
    assert snapshot["url"] == "http://localhost:18080/adapter/rpa/snapshot"
    assert execution["kwargs"]["json"] == {"instruction": "click search"}
    assert run["kwargs"]["json"] == {"skill_id": "skill-1", "kwargs": {"q": "paper"}}
    assert [call["method"] for call in fake_client.calls] == ["GET", "GET", "GET", "POST", "POST", "GET", "POST", "POST"]
    assert all(
        call["headers"]["Authorization"] == "Bearer session-token"
        for call in fake_client.calls
    )


@pytest.mark.asyncio
async def test_runtime_adapter_client_exposes_file_and_download_contracts():
    fake_client = _FakeAsyncClient()
    client = RuntimeAdapterClient(_runtime_record(), http_client_factory=lambda **_: fake_client)

    downloads = await client.list_downloads()
    files = await client.list_files("/workspace/downloads")
    write = await client.write_file("/workspace/skill.py", "print('ok')")
    write_binary = await client.write_file_base64("/workspace/blob.bin", "AAFi")
    content = await client.download_file("/workspace/downloads/report.pdf")

    assert downloads["url"] == "http://localhost:18080/adapter/rpa/downloads"
    assert files["url"] == "http://localhost:18080/adapter/files/list"
    assert files["kwargs"]["params"] == {"path": "/workspace/downloads"}
    assert write["url"] == "http://localhost:18080/adapter/files/write"
    assert write["kwargs"]["json"] == {"path": "/workspace/skill.py", "content": "print('ok')"}
    assert write_binary["url"] == "http://localhost:18080/adapter/files/write"
    assert write_binary["kwargs"]["json"] == {"path": "/workspace/blob.bin", "content_base64": "AAFi"}
    assert content == b"file-bytes"
    assert fake_client.calls[-1]["url"] == "http://localhost:18080/adapter/files/download"
    assert fake_client.calls[-1]["params"] == {"path": "/workspace/downloads/report.pdf"}


@pytest.mark.asyncio
async def test_runtime_adapter_client_wraps_http_errors_without_leaking_runtime_token():
    client = RuntimeAdapterClient(
        _runtime_record(),
        http_client_factory=lambda **_: _FakeErrorAsyncClient(),
    )

    with pytest.raises(RuntimeAdapterClientError) as exc_info:
        await client.browser_info()

    error = exc_info.value
    assert error.status_code == 403
    assert error.method == "GET"
    assert error.path == "/v1/browser/info"
    assert error.detail == {"detail": "Invalid runtime adapter bearer token"}
    assert "session-token" not in str(error)


@pytest.mark.asyncio
async def test_runtime_adapter_client_sanitizes_sensitive_adapter_error_detail():
    client = RuntimeAdapterClient(
        _runtime_record(),
        http_client_factory=lambda **_: _FakeSensitiveErrorAsyncClient(),
    )

    with pytest.raises(RuntimeAdapterClientError) as exc_info:
        await client.browser_info()

    error = exc_info.value
    assert error.status_code == 500
    assert error.detail == {
        "detail": "adapter failed with <redacted>",
        "nested": {
            "runtime_token": "<redacted>",
            "authorization": "<redacted>",
        },
    }
    assert "session-token" not in str(error)


@pytest.mark.asyncio
async def test_runtime_adapter_client_default_http_client_ignores_system_proxy(monkeypatch):
    captured = {}

    class CapturingAsyncClient(_FakeAsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured.update(kwargs)

    monkeypatch.setattr(adapter_client_module.httpx, "AsyncClient", CapturingAsyncClient)

    client = RuntimeAdapterClient(_runtime_record())
    await client.health()

    assert captured["trust_env"] is False
