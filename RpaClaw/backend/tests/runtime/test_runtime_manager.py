from backend.runtime.models import SessionRuntimeRecord
from backend.runtime.docker_runtime_provider import DockerRuntimeProvider
from backend.runtime.k8s_runtime_provider import K8sRuntimeProvider
from backend.runtime.provider import build_runtime_provider
from backend.runtime.shared_runtime_provider import SharedRuntimeProvider
from backend.runtime.aio_runtime_provider import AioNativeRuntimeProvider, AioRuntimeProvider
from backend.runtime.adapter_client import RuntimeAdapterClientError
from backend.config import (
    _derive_sandbox_vnc_ws_url,
    _resolve_sandbox_base_url,
    _resolve_sandbox_mcp_url,
    _resolve_sandbox_tools_dir,
    _resolve_sandbox_vnc_ws_url,
    _resolve_tools_dir,
)
from backend.runtime.session_runtime_manager import (
    SessionRuntimeManager,
    get_session_runtime_manager,
    reset_session_runtime_manager,
)
import hashlib
import tempfile
from pathlib import Path
import pytest


def test_runtime_record_roundtrip_defaults():
    record = SessionRuntimeRecord(
        session_id="sess-1",
        user_id="user-1",
        namespace="beta",
        pod_name="rpaclaw-sess-sess1",
        service_name="rpaclaw-sess-sess1-svc",
        rest_base_url="http://rpaclaw-sess-sess1-svc:8080",
        status="creating",
    )

    payload = record.model_dump()

    assert payload["session_id"] == "sess-1"
    assert payload["service_name"].endswith("-svc")
    assert payload["status"] == "creating"
    assert "created_at" in payload
    assert "last_used_at" in payload


class _Settings:
    def __init__(self, runtime_mode: str):
        self.runtime_mode = runtime_mode
        self.runtime_idle_ttl_seconds = 3600
        self.runtime_wait_timeout_seconds = 0


def test_provider_factory_returns_shared_provider_when_requested():
    provider = build_runtime_provider(_Settings("shared"))
    assert provider.__class__.__name__ == "SharedRuntimeProvider"


def test_provider_factory_returns_docker_provider_when_requested():
    provider = build_runtime_provider(_Settings("docker"))
    assert provider.__class__.__name__ == "DockerRuntimeProvider"


def test_provider_factory_returns_k8s_provider_when_requested():
    provider = build_runtime_provider(_Settings("session_pod"))
    assert provider.__class__.__name__ == "K8sRuntimeProvider"


def test_provider_factory_returns_aio_provider_for_local_fixed_aio_mode():
    provider = build_runtime_provider(_Settings("aio_fixed"))
    assert provider.__class__.__name__ == "AioRuntimeProvider"


def test_provider_factory_returns_aio_api_provider_for_real_aio_mode():
    provider = build_runtime_provider(_Settings("aio"))
    assert provider.__class__.__name__ == "AioApiRuntimeProvider"


def test_provider_factory_returns_aio_native_provider_for_native_aio_mode():
    provider = build_runtime_provider(_Settings("aio_native"))
    assert provider.__class__.__name__ == "AioNativeRuntimeProvider"


@pytest.mark.asyncio
async def test_aio_native_runtime_provider_uses_fixed_native_aio_browser_info():
    class _AioNativeSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_native_base_url = "http://localhost:18090"
        aio_runtime_sandbox_id = "native-aio-sandbox"

    class _NativeAioResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "cdp_url": "ws://127.0.0.1:9222/devtools/browser/native",
                    "vnc_url": "http://127.0.0.1:8080/vnc/index.html",
                }
            }

    class _NativeAioClient:
        calls = []

        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            self.calls.append(url)
            return _NativeAioResponse()

    _NativeAioClient.calls = []
    provider = AioNativeRuntimeProvider(
        _AioNativeSettings("aio_native"),
        http_client_factory=_NativeAioClient,
    )

    runtime = await provider.create_runtime("sess-native", "user-1")
    refreshed = await provider.refresh_runtime(runtime)

    assert runtime.session_id == "sess-native"
    assert runtime.user_id == "user-1"
    assert runtime.namespace == "aio-native"
    assert runtime.sandbox_id == "native-aio-sandbox"
    assert runtime.rest_base_url == "http://localhost:18090"
    assert runtime.route_base_url == "http://localhost:18090"
    assert runtime.status == "ready"
    assert refreshed.status == "ready"
    assert refreshed.browser_view_url == "http://localhost:18090/vnc/index.html"
    assert refreshed.metadata["browser_info_ok"] is True
    assert refreshed.metadata["cdp_url_available"] is True
    assert _NativeAioClient.calls == ["http://localhost:18090/v1/browser/info"]


@pytest.mark.asyncio
async def test_aio_native_runtime_provider_can_use_intranet_lifecycle_api():
    class _AioNativeLifecycleSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_native_base_url = "http://browser-route.internal/{sandbox_id}"
        aio_runtime_sandbox_id = ""
        aio_native_api_base_url = "https://apig.internal"
        aio_native_api_token = "aio-api-token"
        aio_native_template_id = "lf-jsdklalfdan5sf1a1dd1"
        aio_native_refresh_duration_seconds = 300

    class _NativeLifecycleResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class _NativeLifecycleClient:
        calls = []
        responses = []

        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            self.calls.append({"method": method, "url": url, "kwargs": kwargs})
            return _NativeLifecycleResponse(self.responses.pop(0))

    _NativeLifecycleClient.calls = []
    _NativeLifecycleClient.responses = [
        {
            "code": 200,
            "message": "success",
            "data": {
                "templateId": "lf-jsdklalfdan5sf1a1dd1",
                "sandboxId": "6v8s62vtbsxlvup8",
                "cpu": 2000,
                "memory": 4096,
                "timeout": 300,
                "status": "running",
                "startAt": "2026-05-08T18:50:31.493441369",
            },
        },
        {
            "code": 200,
            "message": "success",
            "data": {
                "templateId": "lf-jsdklalfdan5sf1a1dd1",
                "sandboxId": "6v8s62vtbsxlvup8",
                "cpu": 2000,
                "memory": 4096,
                "timeout": 1200,
                "status": "running",
                "startAt": "2026-05-08T18:50:31.493441369",
            },
        },
        {"code": 200, "message": "success"},
        {"code": 200, "message": "Delete sandbox success"},
    ]
    provider = AioNativeRuntimeProvider(
        _AioNativeLifecycleSettings("aio_native"),
        http_client_factory=_NativeLifecycleClient,
    )

    runtime = await provider.create_runtime("sess-native", "user-1")
    refreshed = await provider.refresh_runtime(runtime)
    await provider.keepalive_runtime(refreshed)
    await provider.delete_runtime(refreshed)

    assert runtime.status == "ready"
    assert runtime.namespace == "aio-native"
    assert runtime.sandbox_id == "6v8s62vtbsxlvup8"
    assert runtime.rest_base_url == "http://browser-route.internal/6v8s62vtbsxlvup8"
    assert runtime.route_base_url == "http://browser-route.internal/6v8s62vtbsxlvup8"
    assert runtime.metadata["runtime_contract"] == "aio_native"
    assert runtime.metadata["template_id"] == "lf-jsdklalfdan5sf1a1dd1"
    assert runtime.metadata["aio_status"] == "running"
    assert runtime.metadata["cpu"] == 2000
    assert runtime.metadata["memory"] == 4096
    assert runtime.metadata["timeout"] == 300
    assert runtime.metadata["start_at"] == "2026-05-08T18:50:31.493441369"
    assert refreshed.status == "ready"
    assert refreshed.metadata["timeout"] == 1200

    create_call, status_call, refresh_call, delete_call = _NativeLifecycleClient.calls
    assert create_call == {
        "method": "POST",
        "url": "https://apig.internal/api/livefunction/sandboxes",
        "kwargs": {
            "headers": {"Authorization": "Bearer aio-api-token"},
            "json": {"templateId": "lf-jsdklalfdan5sf1a1dd1"},
        },
    }
    assert status_call["method"] == "GET"
    assert status_call["url"] == "https://apig.internal/api/livefunction/sandboxes/6v8s62vtbsxlvup8"
    assert status_call["kwargs"]["headers"] == {"Authorization": "Bearer aio-api-token"}
    assert refresh_call == {
        "method": "POST",
        "url": "https://apig.internal/api/livefunction/sandboxes/refresh/6v8s62vtbsxlvup8",
        "kwargs": {
            "headers": {"Authorization": "Bearer aio-api-token"},
            "json": {"duration": 300},
        },
    }
    assert delete_call["method"] == "DELETE"
    assert delete_call["url"] == "https://apig.internal/api/livefunction/sandboxes/6v8s62vtbsxlvup8"


@pytest.mark.asyncio
async def test_aio_native_runtime_provider_marks_stopped_sandbox_missing():
    class _AioNativeLifecycleSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_native_base_url = "http://browser-route.internal/{sandbox_id}"
        aio_native_api_base_url = "https://apig.internal"
        aio_native_api_token = ""
        aio_native_template_id = "lf-jsdklalfdan5sf1a1dd1"

    class _NativeLifecycleResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 200,
                "message": "success",
                "data": {
                    "templateId": "lf-jsdklalfdan5sf1a1dd1",
                    "sandboxId": "sb-stopped",
                    "status": "stopped",
                    "endAt": "2026-05-06T18:08:28.146305101",
                },
            }

    class _NativeLifecycleClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            return _NativeLifecycleResponse()

    provider = AioNativeRuntimeProvider(
        _AioNativeLifecycleSettings("aio_native"),
        http_client_factory=lambda *args, **kwargs: _NativeLifecycleClient(),
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-native",
        user_id="user-1",
        namespace="aio-native",
        pod_name="sb-stopped",
        service_name="sb-stopped",
        rest_base_url="http://browser-route.internal/sb-stopped",
        route_base_url="http://browser-route.internal/sb-stopped",
        sandbox_id="sb-stopped",
        status="ready",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"
    assert refreshed.metadata["runtime_status_reason"] == "aio_native_sandbox_stopped"
    assert refreshed.metadata["aio_status"] == "stopped"
    assert refreshed.metadata["end_at"] == "2026-05-06T18:08:28.146305101"


@pytest.mark.asyncio
async def test_aio_fixed_runtime_provider_returns_preprovisioned_sandbox_record():
    class _AioFixedSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_runtime_sandbox_id = "local-aio-sandbox"
        aio_runtime_route_base_url = "http://localhost:18080/adapter"
        aio_runtime_browser_view_url = "http://localhost:18080/browser"
        aio_runtime_token = "session-token"

    provider = AioRuntimeProvider(_AioFixedSettings("aio_fixed"))

    first = await provider.create_runtime("sess-a", "user-1")
    second = await provider.create_runtime("sess-b", "user-2")

    assert first.session_id == "sess-a"
    assert first.user_id == "user-1"
    assert first.sandbox_id == "local-aio-sandbox"
    assert first.namespace == "aio-local"
    assert first.pod_name == "local-aio-sandbox"
    assert first.service_name == "local-aio-sandbox"
    assert first.rest_base_url == "http://localhost:18080/adapter"
    assert first.route_base_url == "http://localhost:18080/adapter"
    assert first.browser_view_url == "http://localhost:18080/browser"
    assert first.runtime_token == "session-token"
    assert first.status == "ready"

    assert second.session_id == "sess-b"
    assert second.user_id == "user-2"
    assert second.sandbox_id == first.sandbox_id
    assert second.rest_base_url == first.rest_base_url


@pytest.mark.asyncio
async def test_aio_fixed_runtime_provider_refreshes_ready_from_adapter_health():
    class _AioFixedSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_runtime_sandbox_id = "local-aio-sandbox"
        aio_runtime_route_base_url = "http://localhost:18080/adapter"

    class _HealthyAdapterClient:
        calls = []

        def __init__(self, runtime):
            self.runtime = runtime

        async def health(self):
            self.calls.append(self.runtime.rest_base_url)
            return {"status": "ok", "contract_version": "v1"}

    provider = AioRuntimeProvider(
        _AioFixedSettings("aio_fixed"),
        adapter_client_cls=_HealthyAdapterClient,
    )
    runtime = await provider.create_runtime("sess-a", "user-1")
    runtime.status = "creating"

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "ready"
    assert _HealthyAdapterClient.calls == ["http://localhost:18080/adapter"]


@pytest.mark.asyncio
async def test_aio_fixed_runtime_provider_records_adapter_file_policy_metadata():
    class _AioFixedSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_runtime_sandbox_id = "local-aio-sandbox"
        aio_runtime_route_base_url = "http://localhost:18080/adapter"

    class _HealthyAdapterClient:
        def __init__(self, runtime):
            self.runtime = runtime

        async def health(self):
            return {
                "status": "ok",
                "contract_version": "v1",
                "config": {
                    "file_policy": {
                        "max_inline_file_write_bytes": 10 * 1024 * 1024,
                        "max_file_download_bytes": 50 * 1024 * 1024,
                        "oversized_hash_status": "skipped_oversized",
                    },
                    "adapter_version": "policy-test",
                },
            }

    provider = AioRuntimeProvider(
        _AioFixedSettings("aio_fixed"),
        adapter_client_cls=_HealthyAdapterClient,
    )
    runtime = await provider.create_runtime("sess-a", "user-1")

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "ready"
    assert refreshed.metadata["adapter_version"] == "policy-test"
    assert refreshed.metadata["adapter_file_policy"] == {
        "max_inline_file_write_bytes": 10 * 1024 * 1024,
        "max_file_download_bytes": 50 * 1024 * 1024,
        "oversized_hash_status": "skipped_oversized",
    }


@pytest.mark.asyncio
async def test_aio_fixed_runtime_provider_refresh_reports_missing_when_adapter_health_fails():
    class _AioFixedSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_runtime_sandbox_id = "local-aio-sandbox"
        aio_runtime_route_base_url = "http://localhost:18080/adapter"

    class _FailingAdapterClient:
        def __init__(self, runtime):
            self.runtime = runtime

        async def health(self):
            raise RuntimeError("adapter unavailable")

    provider = AioRuntimeProvider(
        _AioFixedSettings("aio_fixed"),
        adapter_client_cls=_FailingAdapterClient,
    )
    runtime = await provider.create_runtime("sess-a", "user-1")

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"
    assert refreshed.metadata["runtime_status_reason"] == "adapter_health_unavailable"
    assert refreshed.metadata["supported_adapter_contract_version"] == "v1"


@pytest.mark.asyncio
async def test_aio_fixed_runtime_provider_refresh_reports_sanitized_adapter_health_auth_error():
    class _AioFixedSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_runtime_sandbox_id = "local-aio-sandbox"
        aio_runtime_route_base_url = "http://localhost:18080/adapter"

    class _UnauthorizedAdapterClient:
        def __init__(self, runtime):
            self.runtime = runtime

        async def health(self):
            raise RuntimeAdapterClientError(
                method="GET",
                path="/health",
                status_code=403,
                detail={"detail": f"Invalid bearer token {self.runtime.runtime_token}"},
            )

    provider = AioRuntimeProvider(
        _AioFixedSettings("aio_fixed"),
        adapter_client_cls=_UnauthorizedAdapterClient,
    )
    runtime = await provider.create_runtime("sess-a", "user-1")
    runtime.runtime_token = "secret-runtime-token"

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"
    assert refreshed.metadata["runtime_status_reason"] == "adapter_health_unauthorized"
    assert refreshed.metadata["adapter_health_status_code"] == 403
    assert refreshed.metadata["adapter_health_error_method"] == "GET"
    assert refreshed.metadata["adapter_health_error_path"] == "/health"
    assert refreshed.metadata["supported_adapter_contract_version"] == "v1"
    assert "secret-runtime-token" not in str(refreshed.metadata)


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_refresh_reports_missing_when_sandbox_id_missing():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    provider = AioApiRuntimeProvider(_AioApiSettings("aio"))
    runtime = SessionRuntimeRecord(
        session_id="sess-a",
        user_id="user-1",
        namespace="aio",
        pod_name="missing-sandbox",
        service_name="missing-sandbox",
        rest_base_url="http://route.internal/missing-sandbox",
        status="ready",
        route_base_url="http://route.internal/missing-sandbox",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"
    assert refreshed.metadata["runtime_status_reason"] == "aio_sandbox_id_missing"


@pytest.mark.asyncio
async def test_aio_fixed_runtime_provider_refresh_reports_missing_when_adapter_health_not_ok():
    class _AioFixedSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_runtime_sandbox_id = "local-aio-sandbox"
        aio_runtime_route_base_url = "http://localhost:18080/adapter"

    class _DegradedAdapterClient:
        def __init__(self, runtime):
            self.runtime = runtime

        async def health(self):
            return {"status": "degraded"}

    provider = AioRuntimeProvider(
        _AioFixedSettings("aio_fixed"),
        adapter_client_cls=_DegradedAdapterClient,
    )
    runtime = await provider.create_runtime("sess-a", "user-1")

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"
    assert refreshed.metadata["runtime_status_reason"] == "adapter_health_not_ready"
    assert refreshed.metadata["adapter_health_status"] == "degraded"
    assert refreshed.metadata["supported_adapter_contract_version"] == "v1"


@pytest.mark.asyncio
async def test_aio_fixed_runtime_provider_refresh_reports_missing_when_adapter_contract_mismatches():
    class _AioFixedSettings(_Settings):
        sandbox_base_url = "http://localhost:8080"
        aio_runtime_sandbox_id = "local-aio-sandbox"
        aio_runtime_route_base_url = "http://localhost:18080/adapter"

    class _OldAdapterClient:
        def __init__(self, runtime):
            self.runtime = runtime

        async def health(self):
            return {"status": "ok", "contract_version": "v0"}

    provider = AioRuntimeProvider(
        _AioFixedSettings("aio_fixed"),
        adapter_client_cls=_OldAdapterClient,
    )
    runtime = await provider.create_runtime("sess-a", "user-1")

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"
    assert refreshed.metadata["runtime_status_reason"] == "adapter_contract_mismatch"
    assert refreshed.metadata["adapter_contract_version"] == "v0"
    assert refreshed.metadata["supported_adapter_contract_version"] == "v1"


class _FakeAioHttpResponse:
    def __init__(self, payload=None):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        if self.payload is None:
            raise ValueError("empty response body")
        return self.payload


class _FakeAioHttpClient:
    calls = []
    responses = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _FakeAioHttpResponse(self.responses.pop(0))


class _HealthyRuntimeAdapterClient:
    calls = []

    def __init__(self, runtime):
        self.runtime = runtime

    async def health(self):
        self.calls.append(self.runtime.route_base_url or self.runtime.rest_base_url)
        return {"status": "ok", "contract_version": "v1"}


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_creates_refreshes_and_deletes_sandbox():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "aio-api-token"
        aio_runtime_image = "rpaclaw-runtime-adapter:dev"
        aio_runtime_create_path = "/sandboxes"
        aio_runtime_status_path_template = "/sandboxes/{sandbox_id}"
        aio_runtime_delete_path_template = "/sandboxes/{sandbox_id}"
        aio_runtime_ttl_seconds = 7200

    _FakeAioHttpClient.calls = []
    _HealthyRuntimeAdapterClient.calls = []
    _FakeAioHttpClient.responses = [
        {
            "sandbox_id": "sb-1",
            "route_base_url": "http://route.internal/sb-1",
            "browser_view_url": "http://route.internal/sb-1/browser",
            "runtime_token": "runtime-token",
            "status": "ready",
            "expires_at": 1780000000,
        },
        {
            "sandbox_id": "sb-1",
            "route_base_url": "http://route.internal/sb-1",
            "browser_view_url": "http://route.internal/sb-1/browser",
            "runtime_token": "runtime-token-2",
            "status": "ready",
            "expires_at": 1780000300,
        },
        {},
    ]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
        adapter_client_cls=_HealthyRuntimeAdapterClient,
    )

    runtime = await provider.create_runtime("sess-a", "user-1")
    refreshed = await provider.refresh_runtime(runtime)
    await provider.delete_runtime(refreshed)

    assert runtime.session_id == "sess-a"
    assert runtime.user_id == "user-1"
    assert runtime.namespace == "aio"
    assert runtime.sandbox_id == "sb-1"
    assert runtime.rest_base_url == "http://route.internal/sb-1"
    assert runtime.route_base_url == "http://route.internal/sb-1"
    assert runtime.browser_view_url == "http://route.internal/sb-1/browser"
    assert runtime.runtime_token == "runtime-token"
    assert runtime.status == "ready"
    assert runtime.expires_at == 1780000000
    assert refreshed.runtime_token == "runtime-token-2"
    assert refreshed.status == "ready"
    assert refreshed.expires_at == 1780000300
    assert _HealthyRuntimeAdapterClient.calls == [
        "http://route.internal/sb-1",
        "http://route.internal/sb-1",
    ]

    create_call, refresh_call, delete_call = _FakeAioHttpClient.calls
    assert create_call == {
        "method": "POST",
        "url": "http://aio.internal/api/sandboxes",
        "kwargs": {
            "headers": {"Authorization": "Bearer aio-api-token"},
            "json": {
                "session_id": "sess-a",
                "user_id": "user-1",
                "image": "rpaclaw-runtime-adapter:dev",
                "ttl_seconds": 7200,
            },
        },
    }
    assert refresh_call["method"] == "GET"
    assert refresh_call["url"] == "http://aio.internal/api/sandboxes/sb-1"
    assert refresh_call["kwargs"]["headers"] == {"Authorization": "Bearer aio-api-token"}
    assert delete_call["method"] == "DELETE"
    assert delete_call["url"] == "http://aio.internal/api/sandboxes/sb-1"
    assert delete_call["kwargs"]["headers"] == {"Authorization": "Bearer aio-api-token"}


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_refresh_reports_missing_on_status_failure():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    class _FailingHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            raise RuntimeError("aio status unavailable")

    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=lambda *args, **kwargs: _FailingHttpClient(),
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-a",
        user_id="user-1",
        namespace="aio",
        pod_name="sb-1",
        service_name="sb-1",
        rest_base_url="http://route.internal/sb-1",
        status="ready",
        sandbox_id="sb-1",
        route_base_url="http://route.internal/sb-1",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"
    assert refreshed.metadata["runtime_status_reason"] == "aio_status_unavailable"


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_create_wraps_lifecycle_failure_without_leaking_tokens():
    from backend.runtime.aio_runtime_provider import (
        AioApiRuntimeProvider,
        AioRuntimeProviderError,
    )

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "aio-api-token"
        aio_runtime_adapter_env = "RUNTIME_ADAPTER_TOKEN=adapter-secret"

    class _FailingHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            raise RuntimeError("aio-api-token adapter-secret")

    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=lambda *args, **kwargs: _FailingHttpClient(),
    )

    with pytest.raises(AioRuntimeProviderError) as exc_info:
        await provider.create_runtime("sess-a", "user-1")

    assert exc_info.value.reason == "aio_create_unavailable"
    assert str(exc_info.value) == "AIO runtime create failed: aio_create_unavailable"
    assert "aio-api-token" not in str(exc_info.value)
    assert "adapter-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_create_wraps_invalid_response():
    from backend.runtime.aio_runtime_provider import (
        AioApiRuntimeProvider,
        AioRuntimeProviderError,
    )

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    _FakeAioHttpClient.calls = []
    _FakeAioHttpClient.responses = [{"route_base_url": "http://route.internal/missing-id"}]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
    )

    with pytest.raises(AioRuntimeProviderError) as exc_info:
        await provider.create_runtime("sess-a", "user-1")

    assert exc_info.value.reason == "aio_create_response_invalid"
    assert str(exc_info.value) == "AIO runtime create failed: aio_create_response_invalid"


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_refresh_reports_missing_when_adapter_contract_mismatches():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    class _OldAdapterClient:
        def __init__(self, runtime):
            self.runtime = runtime

        async def health(self):
            return {"status": "ok", "contract_version": "v0"}

    _FakeAioHttpClient.calls = []
    _HealthyRuntimeAdapterClient.calls = []
    _FakeAioHttpClient.responses = [
        {
            "sandbox_id": "sb-old-adapter",
            "route_base_url": "http://route.internal/sb-old-adapter",
            "status": "ready",
        },
    ]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
        adapter_client_cls=_OldAdapterClient,
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-a",
        user_id="user-1",
        namespace="aio",
        pod_name="sb-old-adapter",
        service_name="sb-old-adapter",
        rest_base_url="http://route.internal/sb-old-adapter",
        status="creating",
        sandbox_id="sb-old-adapter",
        route_base_url="http://route.internal/sb-old-adapter",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_create_reports_missing_when_adapter_contract_mismatches():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    class _OldAdapterClient:
        def __init__(self, runtime):
            self.runtime = runtime

        async def health(self):
            return {"status": "ok", "contract_version": "v0"}

    _FakeAioHttpClient.calls = []
    _HealthyRuntimeAdapterClient.calls = []
    _FakeAioHttpClient.responses = [
        {
            "sandbox_id": "sb-old-adapter",
            "route_base_url": "http://route.internal/sb-old-adapter",
            "status": "ready",
        },
    ]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
        adapter_client_cls=_OldAdapterClient,
    )

    runtime = await provider.create_runtime("sess-a", "user-1")

    assert runtime.status == "missing"
    assert runtime.metadata["runtime_status_reason"] == "adapter_contract_mismatch"
    assert runtime.metadata["adapter_contract_version"] == "v0"
    assert runtime.metadata["supported_adapter_contract_version"] == "v1"


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_refresh_maps_terminal_aio_status_to_missing():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    _FakeAioHttpClient.calls = []
    _HealthyRuntimeAdapterClient.calls = []
    _FakeAioHttpClient.responses = [
        {
            "sandbox_id": "sb-failed",
            "route_base_url": "http://route.internal/sb-failed",
            "status": "failed",
        },
    ]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
        adapter_client_cls=_HealthyRuntimeAdapterClient,
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-a",
        user_id="user-1",
        namespace="aio",
        pod_name="sb-failed",
        service_name="sb-failed",
        rest_base_url="http://route.internal/sb-failed",
        status="ready",
        sandbox_id="sb-failed",
        route_base_url="http://route.internal/sb-failed",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_refresh_allows_terminal_status_without_route():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    _FakeAioHttpClient.calls = []
    _FakeAioHttpClient.responses = [
        {
            "sandbox_id": "sb-deleted",
            "status": "deleted",
        },
    ]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
        adapter_client_cls=_HealthyRuntimeAdapterClient,
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-a",
        user_id="user-1",
        namespace="aio",
        pod_name="sb-deleted",
        service_name="sb-deleted",
        rest_base_url="http://route.internal/sb-deleted",
        status="ready",
        sandbox_id="sb-deleted",
        route_base_url="http://route.internal/sb-deleted",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"
    assert refreshed.route_base_url == "http://route.internal/sb-deleted"


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_accepts_nested_payload_aliases():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    _FakeAioHttpClient.calls = []
    _HealthyRuntimeAdapterClient.calls = []
    _FakeAioHttpClient.responses = [
        {
            "data": {
                "sandbox": {
                    "id": "sb-nested",
                    "adapter_url": "http://route.internal/sb-nested",
                    "view_url": "http://route.internal/sb-nested/browser",
                    "adapter_token": "adapter-token",
                    "status": "running",
                }
            }
        },
    ]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
        adapter_client_cls=_HealthyRuntimeAdapterClient,
    )

    runtime = await provider.create_runtime("sess-nested", "user-1")

    assert runtime.sandbox_id == "sb-nested"
    assert runtime.route_base_url == "http://route.internal/sb-nested"
    assert runtime.browser_view_url == "http://route.internal/sb-nested/browser"
    assert runtime.runtime_token == "adapter-token"
    assert runtime.status == "ready"
    assert _HealthyRuntimeAdapterClient.calls == ["http://route.internal/sb-nested"]


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_delete_accepts_empty_response_body():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    _FakeAioHttpClient.calls = []
    _FakeAioHttpClient.responses = [None]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-a",
        user_id="user-1",
        namespace="aio",
        pod_name="sb-1",
        service_name="sb-1",
        rest_base_url="http://route.internal/sb-1",
        status="ready",
        sandbox_id="sb-1",
        route_base_url="http://route.internal/sb-1",
    )

    await provider.delete_runtime(runtime)

    assert _FakeAioHttpClient.calls[0]["method"] == "DELETE"


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_delete_wraps_lifecycle_failure_without_leaking_tokens():
    from backend.runtime.aio_runtime_provider import (
        AioApiRuntimeProvider,
        AioRuntimeProviderError,
    )

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "aio-api-token"
        aio_runtime_adapter_env = "RUNTIME_ADAPTER_TOKEN=adapter-secret"

    class _FailingHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, **kwargs):
            raise RuntimeError("aio delete unavailable with aio-api-token and adapter-secret")

    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=lambda *args, **kwargs: _FailingHttpClient(),
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-a",
        user_id="user-1",
        namespace="aio",
        pod_name="sb-1",
        service_name="sb-1",
        rest_base_url="http://route.internal/sb-1",
        status="ready",
        sandbox_id="sb-1",
        route_base_url="http://route.internal/sb-1",
    )

    with pytest.raises(AioRuntimeProviderError) as exc_info:
        await provider.delete_runtime(runtime)

    assert exc_info.value.operation == "delete"
    assert exc_info.value.reason == "aio_delete_unavailable"
    assert "aio-api-token" not in str(exc_info.value)
    assert "adapter-secret" not in str(exc_info.value)


def test_aio_api_runtime_provider_diagnoses_config_without_leaking_token():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api/"
        aio_runtime_api_token = "secret-token"
        aio_runtime_image = "rpaclaw-runtime-adapter:dev"
        aio_runtime_create_path = "sandboxes"
        aio_runtime_status_path_template = "sandboxes/{sandbox_id}"
        aio_runtime_delete_path_template = "sandboxes/{sandbox_id}"
        aio_runtime_ttl_seconds = 7200

    provider = AioApiRuntimeProvider(_AioApiSettings("aio"))

    diagnostic = provider.diagnose_config(
        session_id="sess-diagnostic",
        user_id="user-1",
        sandbox_id="sb-1",
    )

    assert diagnostic == {
        "mode": "aio",
        "ready": True,
        "missing": [],
        "invalid": [],
        "auth": {"api_token_configured": True},
        "endpoints": {
            "create": "http://aio.internal/api/sandboxes",
            "status": "http://aio.internal/api/sandboxes/sb-1",
            "delete": "http://aio.internal/api/sandboxes/sb-1",
        },
        "create_payload": {
            "session_id": "sess-diagnostic",
            "user_id": "user-1",
            "image": "rpaclaw-runtime-adapter:dev",
            "ttl_seconds": 7200,
        },
    }
    assert "secret-token" not in str(diagnostic)


@pytest.mark.asyncio
async def test_aio_api_runtime_provider_create_payload_accepts_extra_json_and_adapter_env():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "aio-api-token"
        aio_runtime_image = "rpaclaw-runtime-adapter:dev"
        aio_runtime_create_extra_json = (
            '{"resources":{"cpu":"1","memory":"2Gi"},"labels":{"app":"rpaclaw"}}'
        )
        aio_runtime_adapter_env = "RUNTIME_ADAPTER_TOKEN=adapter-secret,RUNTIME_ADAPTER_DOWNLOADS_DIR=downloads"

    _FakeAioHttpClient.calls = []
    _FakeAioHttpClient.responses = [
        {
            "sandbox_id": "sb-extra",
            "route_base_url": "http://route.internal/sb-extra",
        }
    ]
    provider = AioApiRuntimeProvider(
        _AioApiSettings("aio"),
        http_client_factory=_FakeAioHttpClient,
    )

    runtime = await provider.create_runtime("sess-extra", "user-1")
    diagnostic = provider.diagnose_config(session_id="sess-extra", user_id="user-1")

    assert runtime.sandbox_id == "sb-extra"
    assert runtime.runtime_token == "adapter-secret"
    create_payload = _FakeAioHttpClient.calls[0]["kwargs"]["json"]
    assert create_payload == {
        "session_id": "sess-extra",
        "user_id": "user-1",
        "image": "rpaclaw-runtime-adapter:dev",
        "ttl_seconds": 3600,
        "resources": {"cpu": "1", "memory": "2Gi"},
        "labels": {"app": "rpaclaw"},
        "env": {
            "RUNTIME_ADAPTER_TOKEN": "adapter-secret",
            "RUNTIME_ADAPTER_DOWNLOADS_DIR": "downloads",
        },
    }
    assert diagnostic["create_payload"] == {
        "session_id": "sess-extra",
        "user_id": "user-1",
        "image": "rpaclaw-runtime-adapter:dev",
        "ttl_seconds": 3600,
        "resources": {"cpu": "1", "memory": "2Gi"},
        "labels": {"app": "rpaclaw"},
        "env": {
            "RUNTIME_ADAPTER_TOKEN": "<configured>",
            "RUNTIME_ADAPTER_DOWNLOADS_DIR": "downloads",
        },
    }
    assert "adapter-secret" not in str(diagnostic)


def test_aio_api_runtime_provider_diagnoses_missing_base_url():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = ""
        aio_runtime_api_token = ""

    provider = AioApiRuntimeProvider(_AioApiSettings("aio"))

    diagnostic = provider.diagnose_config()

    assert diagnostic["ready"] is False
    assert diagnostic["missing"] == ["AIO_RUNTIME_API_BASE_URL"]
    assert diagnostic["endpoints"] == {}


def test_aio_api_runtime_provider_diagnoses_invalid_create_payload_config():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "secret-token"
        aio_runtime_create_extra_json = "not-json"
        aio_runtime_adapter_env = "RUNTIME_ADAPTER_TOKEN=adapter-secret"

    provider = AioApiRuntimeProvider(_AioApiSettings("aio"))

    diagnostic = provider.diagnose_config()

    assert diagnostic["ready"] is False
    assert diagnostic["invalid"] == ["AIO_RUNTIME_CREATE_EXTRA_JSON must be a JSON object"]
    assert diagnostic["create_payload"] == {}
    assert "secret-token" not in str(diagnostic)
    assert "adapter-secret" not in str(diagnostic)


def test_aio_api_runtime_provider_diagnoses_invalid_adapter_env_config():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "secret-token"
        aio_runtime_adapter_env = "RUNTIME_ADAPTER_TOKEN"

    provider = AioApiRuntimeProvider(_AioApiSettings("aio"))

    diagnostic = provider.diagnose_config()

    assert diagnostic["ready"] is False
    assert diagnostic["invalid"] == ["AIO_RUNTIME_ADAPTER_ENV entries must use KEY=VALUE"]
    assert diagnostic["create_payload"] == {}
    assert "secret-token" not in str(diagnostic)


def test_aio_api_runtime_provider_diagnoses_create_response_sample_without_leaking_token():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "secret-token"
        aio_runtime_adapter_env = "RUNTIME_ADAPTER_TOKEN=adapter-secret"

    provider = AioApiRuntimeProvider(_AioApiSettings("aio"))

    diagnostic = provider.diagnose_response_sample(
        {
            "data": {
                "runtime": {
                    "id": "sb-sample",
                    "adapter_url": "http://route.internal/sb-sample",
                    "view_url": "http://route.internal/sb-sample/browser",
                    "adapter_token": "response-token",
                    "status": "running",
                    "expires_at": 1780000000,
                }
            }
        },
        session_id="sess-sample",
        user_id="user-1",
    )

    assert diagnostic == {
        "ready": True,
        "invalid": [],
        "runtime": {
            "session_id": "sess-sample",
            "user_id": "user-1",
            "namespace": "aio",
            "sandbox_id": "sb-sample",
            "route_base_url": "http://route.internal/sb-sample",
            "browser_view_url": "http://route.internal/sb-sample/browser",
            "status": "ready",
            "runtime_token_configured": True,
            "expires_at": 1780000000,
        },
    }
    assert "response-token" not in str(diagnostic)
    assert "adapter-secret" not in str(diagnostic)
    assert "secret-token" not in str(diagnostic)


def test_aio_api_runtime_provider_diagnoses_invalid_response_sample():
    from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = ""

    provider = AioApiRuntimeProvider(_AioApiSettings("aio"))

    diagnostic = provider.diagnose_response_sample(
        {"data": {"route_base_url": "http://route.internal/missing-id"}},
        session_id="sess-sample",
        user_id="user-1",
    )

    assert diagnostic == {
        "ready": False,
        "invalid": ["AIO sandbox response missing sandbox_id"],
        "runtime": {},
    }


def test_aio_api_runtime_provider_diagnostic_cli_prints_sanitized_json(capsys):
    from backend.runtime import aio_runtime_provider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "secret-token"
        aio_runtime_image = "rpaclaw-runtime-adapter:dev"
        aio_runtime_create_path = "/sandboxes"
        aio_runtime_status_path_template = "/sandboxes/{sandbox_id}"
        aio_runtime_delete_path_template = "/sandboxes/{sandbox_id}"
        aio_runtime_ttl_seconds = 7200

    exit_code = aio_runtime_provider.main(
        [
            "--diagnose",
            "--session-id",
            "sess-cli",
            "--user-id",
            "user-1",
            "--sandbox-id",
            "sb-cli",
        ],
        settings_obj=_AioApiSettings("aio"),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"ready": true' in output
    assert '"create": "http://aio.internal/api/sandboxes"' in output
    assert '"session_id": "sess-cli"' in output
    assert "secret-token" not in output


def test_aio_api_runtime_provider_diagnostic_cli_reads_sample_response_file(tmp_path, capsys):
    from backend.runtime import aio_runtime_provider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "secret-token"

    sample = tmp_path / "aio-response.json"
    sample.write_text(
        '{"sandbox_id":"sb-cli","route_base_url":"http://route.internal/sb-cli","runtime_token":"runtime-secret"}',
        encoding="utf-8",
    )

    exit_code = aio_runtime_provider.main(
        [
            "--diagnose",
            "--sample-response",
            str(sample),
            "--session-id",
            "sess-cli",
            "--user-id",
            "user-1",
        ],
        settings_obj=_AioApiSettings("aio"),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"response_sample"' in output
    assert '"sandbox_id": "sb-cli"' in output
    assert '"runtime_token_configured": true' in output
    assert "runtime-secret" not in output
    assert "secret-token" not in output


def test_aio_api_runtime_provider_diagnostic_cli_returns_nonzero_for_invalid_create_config(capsys):
    from backend.runtime import aio_runtime_provider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = "http://aio.internal/api"
        aio_runtime_api_token = "secret-token"
        aio_runtime_create_extra_json = "not-json"

    exit_code = aio_runtime_provider.main(["--diagnose"], settings_obj=_AioApiSettings("aio"))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"ready": false' in output
    assert "AIO_RUNTIME_CREATE_EXTRA_JSON must be a JSON object" in output
    assert "secret-token" not in output


def test_aio_api_runtime_provider_diagnostic_cli_returns_nonzero_when_not_ready(capsys):
    from backend.runtime import aio_runtime_provider

    class _AioApiSettings(_Settings):
        aio_runtime_api_base_url = ""
        aio_runtime_api_token = ""

    exit_code = aio_runtime_provider.main(["--diagnose"], settings_obj=_AioApiSettings("aio"))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"ready": false' in output
    assert "AIO_RUNTIME_API_BASE_URL" in output


def test_sandbox_urls_are_derived_from_base_url(monkeypatch):
    monkeypatch.setenv("SANDBOX_BASE_URL", "http://sandbox:8080")
    monkeypatch.delenv("SANDBOX_MCP_URL", raising=False)
    monkeypatch.delenv("SANDBOX_VNC_WS_URL", raising=False)

    assert _resolve_sandbox_base_url() == "http://sandbox:8080"
    assert _resolve_sandbox_mcp_url() == "http://sandbox:8080/mcp"
    assert _resolve_sandbox_vnc_ws_url() == "ws://sandbox:6080"


def test_sandbox_optional_overrides_take_precedence(monkeypatch):
    monkeypatch.setenv("SANDBOX_BASE_URL", "http://sandbox:8080")
    monkeypatch.setenv("SANDBOX_MCP_URL", "http://sandbox-mcp:8080/mcp")
    monkeypatch.setenv("SANDBOX_VNC_WS_URL", "wss://sandbox-vnc.example.com/socket")

    assert _resolve_sandbox_base_url() == "http://sandbox:8080"
    assert _resolve_sandbox_mcp_url() == "http://sandbox-mcp:8080/mcp"
    assert _resolve_sandbox_vnc_ws_url() == "wss://sandbox-vnc.example.com/socket"


def test_tools_dir_defaults_to_home_subdirectory(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        home_dir = Path(temp_dir) / "rpaclaw-home"
        monkeypatch.delenv("TOOLS_DIR", raising=False)
        monkeypatch.setenv("RPA_CLAW_HOME", str(home_dir))

        assert _resolve_tools_dir() == str(home_dir / "tools")


def test_sandbox_tools_dir_uses_trimmed_override(monkeypatch):
    monkeypatch.setenv("SANDBOX_TOOLS_DIR", "/custom/tools///")

    assert _resolve_sandbox_tools_dir() == "/custom/tools"


def test_sandbox_base_url_falls_back_to_mcp(monkeypatch):
    monkeypatch.delenv("SANDBOX_BASE_URL", raising=False)
    monkeypatch.delenv("SANDBOX_MCP_URL", raising=False)

    monkeypatch.setenv("SANDBOX_MCP_URL", "http://sandbox-mcp:8080/mcp")
    assert _resolve_sandbox_base_url() == "http://sandbox-mcp:8080"


def test_vnc_ws_url_is_derived_from_base_url(monkeypatch):
    monkeypatch.delenv("SANDBOX_VNC_WS_URL", raising=False)
    monkeypatch.setenv("SANDBOX_BASE_URL", "https://sandbox.example.com")

    assert _resolve_sandbox_vnc_ws_url() == "wss://sandbox.example.com/vnc/websockify"


def test_vnc_ws_helper_handles_known_ports():
    assert _derive_sandbox_vnc_ws_url("http://sandbox:8080") == "ws://sandbox:6080"
    assert _derive_sandbox_vnc_ws_url("https://sandbox.example.com:18080") == "wss://sandbox.example.com:16080"


@pytest.mark.asyncio
async def test_shared_runtime_provider_derives_rest_base_from_sandbox_mcp_url_when_env_not_set(monkeypatch):
    settings = _Settings("shared")
    settings.sandbox_base_url = "http://sandbox:8080"
    settings.sandbox_mcp_url = "http://sandbox:8080/mcp"
    settings.k8s_namespace = "default"

    runtime = await SharedRuntimeProvider(settings).create_runtime("sess-1", "user-1")

    assert runtime.rest_base_url == "http://sandbox:8080"


class _FakeProvider:
    def __init__(self):
        self.create_calls = []
        self.delete_calls = []
        self.refresh_calls = []

    async def create_runtime(self, session_id: str, user_id: str):
        self.create_calls.append((session_id, user_id))
        return SessionRuntimeRecord(
            session_id=session_id,
            user_id=user_id,
            namespace="beta",
            pod_name=f"rpaclaw-sess-{session_id}",
            service_name=f"rpaclaw-sess-{session_id}-svc",
            rest_base_url=f"http://rpaclaw-sess-{session_id}-svc:8080",
            status="ready",
        )

    async def delete_runtime(self, runtime_record) -> None:
        self.delete_calls.append(runtime_record)
        return None

    async def refresh_runtime(self, runtime_record):
        self.refresh_calls.append(runtime_record)
        return runtime_record


class _FakeRepository:
    def __init__(self, existing=None, records=None):
        self.existing = existing
        self.records = list(records or [])
        self.inserted = []
        self.updated = []
        self.deleted = []

    async def find_one(self, query):
        return self.existing

    async def find_many(self, query):
        if not query:
            return list(self.records)
        return [
            record
            for record in self.records
            if all(record.get(key) == value for key, value in query.items())
        ]

    async def insert_one(self, document):
        self.inserted.append(document)

    async def update_one(self, query, update):
        self.updated.append((query, update))

    async def delete_one(self, query):
        self.deleted.append(query)


class _StrictFakeRepository(_FakeRepository):
    async def find_one(self, query):
        if not self.existing:
            return None
        return self.existing if all(self.existing.get(key) == value for key, value in query.items()) else None


class DuplicateKeyError(Exception):
    pass


class _DuplicateOnInsertRepository(_FakeRepository):
    def __init__(self, *, duplicate_existing):
        super().__init__(existing=None)
        self.duplicate_existing = duplicate_existing
        self.find_calls = 0

    async def find_one(self, query):
        self.find_calls += 1
        if self.find_calls == 1:
            return None
        return self.duplicate_existing

    async def insert_one(self, document):
        self.inserted.append(document)
        raise DuplicateKeyError("duplicate session runtime")


@pytest.mark.asyncio
async def test_ensure_runtime_reuses_ready_record():
    existing = {
        "session_id": "sess-1",
        "user_id": "user-1",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-1",
        "service_name": "rpaclaw-sess-sess-1-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": None,
    }
    provider = _FakeProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.ensure_runtime("sess-1", "user-1")

    assert isinstance(runtime, SessionRuntimeRecord)
    assert runtime.session_id == "sess-1"
    assert provider.create_calls == []
    assert repository.inserted == []
    assert len(repository.updated) == 1


@pytest.mark.asyncio
async def test_ensure_runtime_recreates_ready_record_when_runtime_is_missing():
    existing = {
        "session_id": "sess-stale-ready",
        "user_id": "user-1",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-stale-ready",
        "service_name": "rpaclaw-sess-sess-stale-ready-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-stale-ready-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": 10,
    }

    class _RefreshingMissingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "missing"
            return runtime_record

    provider = _RefreshingMissingProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.ensure_runtime("sess-stale-ready", "user-1")

    assert runtime.session_id == "sess-stale-ready"
    assert provider.create_calls == [("sess-stale-ready", "user-1")]
    assert len(provider.refresh_calls) == 1
    assert repository.deleted == [{"session_id": "sess-stale-ready"}]
    assert len(repository.inserted) == 1


@pytest.mark.asyncio
async def test_ensure_runtime_creates_when_missing():
    provider = _FakeProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.ensure_runtime("sess-2", "user-2")

    assert runtime.session_id == "sess-2"
    assert provider.create_calls == [("sess-2", "user-2")]
    assert len(repository.inserted) == 1
    assert repository.inserted[0]["_id"] == "sess-2"


@pytest.mark.asyncio
async def test_ensure_runtime_reuses_creating_record_without_duplicate_create():
    existing = {
        "session_id": "sess-creating",
        "user_id": "user-1",
        "namespace": "aio",
        "pod_name": "sb-creating",
        "service_name": "sb-creating",
        "rest_base_url": "http://route.internal/sb-creating",
        "route_base_url": "http://route.internal/sb-creating",
        "sandbox_id": "sb-creating",
        "status": "creating",
        "created_at": 10,
        "last_used_at": 10,
        "expires_at": 20,
    }

    class _RefreshingReadyProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "ready"
            runtime_record.route_base_url = "http://route.internal/sb-ready"
            runtime_record.rest_base_url = "http://route.internal/sb-ready"
            return runtime_record

    provider = _RefreshingReadyProvider()
    repository = _StrictFakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.ensure_runtime("sess-creating", "user-1")

    assert runtime.session_id == "sess-creating"
    assert runtime.status == "ready"
    assert runtime.route_base_url == "http://route.internal/sb-ready"
    assert provider.create_calls == []
    assert len(provider.refresh_calls) == 1
    assert repository.inserted == []
    assert repository.updated[0][0] == {"session_id": "sess-creating"}
    assert repository.updated[0][1]["$set"]["status"] == "ready"
    assert repository.updated[0][1]["$set"]["route_base_url"] == "http://route.internal/sb-ready"


@pytest.mark.asyncio
async def test_ensure_runtime_recovers_from_duplicate_insert_created_by_another_host_instance():
    existing = {
        "_id": "sess-race",
        "session_id": "sess-race",
        "user_id": "user-1",
        "namespace": "aio",
        "pod_name": "sb-existing",
        "service_name": "sb-existing",
        "rest_base_url": "http://route.internal/sb-existing",
        "route_base_url": "http://route.internal/sb-existing",
        "sandbox_id": "sb-existing",
        "status": "creating",
        "created_at": 10,
        "last_used_at": 10,
        "expires_at": 20,
    }
    provider = _FakeProvider()
    repository = _DuplicateOnInsertRepository(duplicate_existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.ensure_runtime("sess-race", "user-1")

    assert runtime.sandbox_id == "sb-existing"
    assert provider.create_calls == [("sess-race", "user-1")]
    assert [record.sandbox_id for record in provider.delete_calls] == [None]
    assert repository.inserted[0]["_id"] == "sess-race"
    assert repository.updated[0][0] == {"session_id": "sess-race"}
    assert repository.updated[0][1]["$set"]["sandbox_id"] == "sb-existing"


@pytest.mark.asyncio
async def test_ensure_runtime_sanitizes_duplicate_created_cleanup_failure_log(caplog):
    existing = {
        "_id": "sess-race-log",
        "session_id": "sess-race-log",
        "user_id": "user-1",
        "namespace": "aio",
        "pod_name": "sb-existing",
        "service_name": "sb-existing",
        "rest_base_url": "http://route.internal/sb-existing",
        "route_base_url": "http://route.internal/sb-existing",
        "sandbox_id": "sb-existing",
        "status": "creating",
        "created_at": 10,
        "last_used_at": 10,
        "expires_at": 20,
    }

    class _FailingDeleteProvider(_FakeProvider):
        async def delete_runtime(self, runtime_record) -> None:
            self.delete_calls.append(runtime_record)
            raise RuntimeError(
                "delete failed token=secret-token Authorization=Bearer adapter-secret"
            )

    provider = _FailingDeleteProvider()
    repository = _DuplicateOnInsertRepository(duplicate_existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    with caplog.at_level("WARNING", logger="backend.runtime.session_runtime_manager"):
        runtime = await manager.ensure_runtime("sess-race-log", "user-1")

    assert runtime.sandbox_id == "sb-existing"
    assert "secret-token" not in caplog.text
    assert "adapter-secret" not in caplog.text
    assert "<redacted>" in caplog.text


@pytest.mark.asyncio
async def test_ensure_runtime_does_not_insert_when_provider_create_fails():
    from backend.runtime.aio_runtime_provider import AioRuntimeProviderError

    class _FailingProvider(_FakeProvider):
        async def create_runtime(self, session_id: str, user_id: str):
            self.create_calls.append((session_id, user_id))
            raise AioRuntimeProviderError("create", "aio_create_unavailable")

    provider = _FailingProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    with pytest.raises(AioRuntimeProviderError) as exc_info:
        await manager.ensure_runtime("sess-fail", "user-1")

    assert exc_info.value.reason == "aio_create_unavailable"
    assert provider.create_calls == [("sess-fail", "user-1")]
    assert repository.inserted == []


@pytest.mark.asyncio
async def test_ensure_runtime_keeps_created_and_last_used_timestamps_consistent(monkeypatch):
    import backend.runtime.session_runtime_manager as runtime_manager_module

    class _SkewedTimestampProvider(_FakeProvider):
        async def create_runtime(self, session_id: str, user_id: str):
            runtime = await super().create_runtime(session_id, user_id)
            runtime.created_at = 150
            runtime.last_used_at = 150
            return runtime

    provider = _SkewedTimestampProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)
    monkeypatch.setattr(runtime_manager_module.time, "time", lambda: 100)

    runtime = await manager.ensure_runtime("sess-created", "user-created")

    assert runtime.created_at == 150
    assert runtime.last_used_at == 150
    assert runtime.expires_at == 3750
    assert repository.inserted[0]["created_at"] == 150
    assert repository.inserted[0]["last_used_at"] == 150


class _FakeContainer:
    def __init__(self, name="rpaclaw-sandbox-1", status="running", health_status="healthy"):
        self.name = name
        self.removed = False
        self.attrs = {"State": {"Status": status, "Health": {"Status": health_status}}}

    def remove(self, force=False):
        self.removed = force


class _FakeContainersApi:
    def __init__(self):
        self.run_calls = []
        self._container = _FakeContainer()
        self.list_calls = []
        self.list_result = []
        self.get_error = None

    def run(self, image, **kwargs):
        self.run_calls.append((image, kwargs))
        return self._container

    def get(self, name):
        if self.get_error is not None:
            raise self.get_error
        return self._container

    def list(self, filters=None):
        self.list_calls.append(filters or {})
        return list(self.list_result)


class _FakeDockerClient:
    def __init__(self):
        self.containers = _FakeContainersApi()


class _DockerSettings:
    runtime_mode = "docker"
    runtime_image = "rpaclaw-sandbox:local"
    docker_runtime_network = "rpaclaw_default"
    docker_runtime_volumes_from = ""
    docker_runtime_shm_size = "2gb"
    docker_runtime_mem_limit = "8g"
    docker_runtime_security_opt = "seccomp:unconfined"
    docker_runtime_extra_hosts = "host.docker.internal:host-gateway"
    runtime_service_port = 8080
    runtime_wait_timeout_seconds = 0
    k8s_namespace = "local"
    k8s_runtime_service_account = ""
    k8s_runtime_image_pull_policy = "IfNotPresent"


@pytest.mark.asyncio
async def test_docker_runtime_provider_creates_session_scoped_record():
    client = _FakeDockerClient()
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)

    runtime = await provider.create_runtime("sess-12345678", "user-1")
    expected_name = (
        "rpaclaw-sess-sess-12345678-"
        + hashlib.sha1("sess-12345678".encode("utf-8")).hexdigest()[:8]
    )

    assert runtime.session_id == "sess-12345678"
    assert runtime.service_name == expected_name
    assert runtime.rest_base_url == f"http://{expected_name}:8080"
    assert len(client.containers.run_calls) == 1
    image, kwargs = client.containers.run_calls[0]
    assert image == "rpaclaw-sandbox:local"
    assert kwargs["detach"] is True
    assert kwargs["name"] == expected_name
    assert kwargs["network"] == "rpaclaw_default"
    assert kwargs["shm_size"] == "2gb"
    assert kwargs["mem_limit"] == "8g"
    assert kwargs["security_opt"] == ["seccomp:unconfined"]
    assert kwargs["extra_hosts"] == {"host.docker.internal": "host-gateway"}


@pytest.mark.asyncio
async def test_docker_runtime_provider_deletes_container():
    client = _FakeDockerClient()
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)
    runtime = SessionRuntimeRecord(
        session_id="sess-12345678",
        user_id="user-1",
        namespace="local",
        pod_name="rpaclaw-sess-sess-123",
        service_name="rpaclaw-sess-sess-123",
        rest_base_url="http://rpaclaw-sess-sess-123:8080",
        status="ready",
    )

    await provider.delete_runtime(runtime)

    assert client.containers._container.removed is True


def test_docker_runtime_provider_container_name_uses_hash_suffix_for_uniqueness():
    name1 = DockerRuntimeProvider._container_name(
        "rpa-12345678-aaaaaaaa-bbbb-cccc-dddddddddddd"
    )
    name2 = DockerRuntimeProvider._container_name(
        "rpa-12345678-eeeeeeee-ffff-1111-222222222222"
    )

    assert name1 != name2
    assert name1.startswith("rpaclaw-sess-rpa-12345678-")
    assert name2.startswith("rpaclaw-sess-rpa-12345678-")
    assert len(name1.rsplit("-", 1)[-1]) == 8


@pytest.mark.asyncio
async def test_docker_runtime_provider_delete_is_idempotent_when_container_missing():
    client = _FakeDockerClient()
    client.containers.get_error = Exception('404 Client Error: Not Found ("No such container: rpaclaw-sess-missing")')
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)
    runtime = SessionRuntimeRecord(
        session_id="sess-missing",
        user_id="user-1",
        namespace="local",
        pod_name="rpaclaw-sess-missing",
        service_name="rpaclaw-sess-missing",
        rest_base_url="http://rpaclaw-sess-missing:8080",
        status="ready",
    )

    await provider.delete_runtime(runtime)


@pytest.mark.asyncio
async def test_docker_runtime_provider_refresh_reports_health_status():
    client = _FakeDockerClient()
    client.containers._container = _FakeContainer(
        name="rpaclaw-sess-healthy",
        status="running",
        health_status="healthy",
    )
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)
    runtime = SessionRuntimeRecord(
        session_id="sess-refresh-docker",
        user_id="user-1",
        namespace="local",
        pod_name="rpaclaw-sess-healthy",
        service_name="rpaclaw-sess-healthy",
        rest_base_url="http://rpaclaw-sess-healthy:8080",
        status="creating",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "ready"


@pytest.mark.asyncio
async def test_docker_runtime_provider_refresh_reports_missing_container():
    client = _FakeDockerClient()
    client.containers.get_error = Exception(
        '404 Client Error: Not Found ("No such container: rpaclaw-sess-missing")'
    )
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)
    runtime = SessionRuntimeRecord(
        session_id="sess-refresh-missing",
        user_id="user-1",
        namespace="local",
        pod_name="rpaclaw-sess-missing",
        service_name="rpaclaw-sess-missing",
        rest_base_url="http://rpaclaw-sess-missing:8080",
        status="ready",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"


@pytest.mark.asyncio
async def test_docker_runtime_provider_uses_configured_volumes_from():
    client = _FakeDockerClient()

    class _ConfiguredDockerSettings(_DockerSettings):
        docker_runtime_volumes_from = "rpaclaw-sandbox-1"

    provider = DockerRuntimeProvider(_ConfiguredDockerSettings(), client=client)

    await provider.create_runtime("sess-aaaa1111", "user-1")

    _, kwargs = client.containers.run_calls[0]
    assert kwargs["volumes_from"] == ["rpaclaw-sandbox-1"]


@pytest.mark.asyncio
async def test_docker_runtime_provider_discovers_compose_sandbox_for_volumes():
    client = _FakeDockerClient()
    client.containers.list_result = [_FakeContainer(name="rpaclaw-sandbox-1")]
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)

    await provider.create_runtime("sess-bbbb2222", "user-2")

    _, kwargs = client.containers.run_calls[0]
    assert kwargs["volumes_from"] == ["rpaclaw-sandbox-1"]
    assert client.containers.list_calls == [
        {"label": "com.docker.compose.service=sandbox"}
    ]


@pytest.mark.asyncio
async def test_docker_runtime_provider_waits_for_runtime_readiness(monkeypatch):
    client = _FakeDockerClient()

    class _WaitingDockerSettings(_DockerSettings):
        runtime_wait_timeout_seconds = 15

    provider = DockerRuntimeProvider(_WaitingDockerSettings(), client=client)
    waited = []

    async def _fake_wait(rest_base_url: str):
        waited.append(rest_base_url)

    monkeypatch.setattr(provider, "_wait_until_ready", _fake_wait)

    runtime = await provider.create_runtime("sess-ready01", "user-1")

    assert waited == [runtime.rest_base_url]


class _FakeK8sContainerStatus:
    def __init__(self, ready: bool):
        self.ready = ready


class _FakeK8sStatus:
    def __init__(self, phase: str, ready: bool = True):
        self.phase = phase
        self.container_statuses = [_FakeK8sContainerStatus(ready)]


class _FakeK8sPod:
    def __init__(self, phase: str = "Running", ready: bool = True):
        self.status = _FakeK8sStatus(phase, ready)


class _FakeApiException(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class _FakeCoreV1Api:
    def __init__(self):
        self.created_pods = []
        self.created_services = []
        self.deleted_pods = []
        self.deleted_services = []
        self.read_pod = _FakeK8sPod()
        self.delete_pod_error = None
        self.delete_service_error = None

    def create_namespaced_pod(self, namespace, body):
        self.created_pods.append((namespace, body))

    def create_namespaced_service(self, namespace, body):
        self.created_services.append((namespace, body))

    def read_namespaced_pod(self, name, namespace):
        return self.read_pod

    def delete_namespaced_pod(self, name, namespace, grace_period_seconds=0):
        if self.delete_pod_error is not None:
            raise self.delete_pod_error
        self.deleted_pods.append((namespace, name, grace_period_seconds))

    def delete_namespaced_service(self, name, namespace, grace_period_seconds=0):
        if self.delete_service_error is not None:
            raise self.delete_service_error
        self.deleted_services.append((namespace, name, grace_period_seconds))


class _K8sSettings(_DockerSettings):
    runtime_mode = "session_pod"
    k8s_namespace = "beta"
    runtime_wait_timeout_seconds = 5
    k8s_runtime_service_account = ""
    k8s_runtime_image_pull_policy = "IfNotPresent"
    k8s_runtime_image_pull_secrets = ""
    k8s_runtime_node_selector = ""
    k8s_runtime_env = ""
    k8s_runtime_labels = ""
    k8s_runtime_annotations = ""
    k8s_runtime_cpu_request = ""
    k8s_runtime_cpu_limit = ""
    k8s_runtime_memory_request = ""
    k8s_runtime_memory_limit = ""
    k8s_runtime_tolerations_json = ""
    k8s_runtime_workspace_volume_name = "workspace"
    k8s_runtime_workspace_mount_path = "/home/rpaclaw"
    k8s_runtime_workspace_pvc_claim = ""
    k8s_runtime_extra_volumes_json = ""
    k8s_runtime_extra_volume_mounts_json = ""


@pytest.mark.asyncio
async def test_k8s_runtime_provider_creates_pod_and_service(monkeypatch):
    api = _FakeCoreV1Api()
    provider = K8sRuntimeProvider(
        _K8sSettings(),
        core_v1_api=api,
        api_exception_cls=_FakeApiException,
        config_loader=lambda: None,
    )

    async def _no_wait(api_client, name):
        return None

    monkeypatch.setattr(provider, "_wait_until_ready", _no_wait)

    runtime = await provider.create_runtime("sess-k8s-1", "user-1")

    assert runtime.namespace == "beta"
    assert runtime.service_name.startswith("rpaclaw-sess-sess-k8s-1-")
    assert runtime.rest_base_url == (
        f"http://{runtime.service_name}.beta.svc.cluster.local:8080"
    )
    assert len(api.created_pods) == 1
    assert len(api.created_services) == 1
    _, pod_body = api.created_pods[0]
    _, service_body = api.created_services[0]
    assert pod_body["metadata"]["name"] == runtime.pod_name
    assert pod_body["spec"]["containers"][0]["image"] == "rpaclaw-sandbox:local"
    assert service_body["spec"]["selector"]["rpaclaw/runtime-name"] == runtime.service_name


@pytest.mark.asyncio
async def test_k8s_runtime_provider_delete_is_idempotent_when_resources_missing():
    api = _FakeCoreV1Api()
    api.delete_service_error = _FakeApiException(404, "service missing")
    api.delete_pod_error = _FakeApiException(404, "pod missing")
    provider = K8sRuntimeProvider(
        _K8sSettings(),
        core_v1_api=api,
        api_exception_cls=_FakeApiException,
        config_loader=lambda: None,
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-k8s-missing",
        user_id="user-1",
        namespace="beta",
        pod_name="rpaclaw-sess-sess-k8s-missing-aaaa1111",
        service_name="rpaclaw-sess-sess-k8s-missing-aaaa1111",
        rest_base_url="http://rpaclaw-sess-sess-k8s-missing-aaaa1111.beta.svc.cluster.local:8080",
        status="ready",
    )

    await provider.delete_runtime(runtime)


def test_k8s_runtime_provider_builds_configurable_pod_manifest():
    class _ConfiguredK8sSettings(_K8sSettings):
        k8s_runtime_service_account = "rpaclaw-runtime"
        k8s_runtime_image_pull_policy = "Always"
        k8s_runtime_image_pull_secrets = "regcred,another-secret"
        k8s_runtime_node_selector = "pool:runtime,topology.kubernetes.io/zone:cn-beijing-a"
        k8s_runtime_env = "TZ:Asia/Shanghai,PLAYWRIGHT_BROWSERS_PATH:/ms-playwright"
        k8s_runtime_labels = "team:beta,track:session-runtime"
        k8s_runtime_annotations = "prometheus.io/scrape:false,owner:rpaclaw"
        k8s_runtime_cpu_request = "500m"
        k8s_runtime_cpu_limit = "2"
        k8s_runtime_memory_request = "1Gi"
        k8s_runtime_memory_limit = "4Gi"
        k8s_runtime_tolerations_json = '[{"key":"dedicated","operator":"Equal","value":"runtime","effect":"NoSchedule"}]'
        k8s_runtime_workspace_volume_name = "workspace-data"
        k8s_runtime_workspace_mount_path = "/home/rpaclaw"
        k8s_runtime_workspace_pvc_claim = "rpaclaw-workspace"
        k8s_runtime_extra_volumes_json = '[{"name":"tools","persistentVolumeClaim":{"claimName":"rpaclaw-tools"}}]'
        k8s_runtime_extra_volume_mounts_json = '[{"name":"tools","mountPath":"/app/Tools","readOnly":true}]'

    provider = K8sRuntimeProvider(
        _ConfiguredK8sSettings(),
        core_v1_api=_FakeCoreV1Api(),
        api_exception_cls=_FakeApiException,
        config_loader=lambda: None,
    )

    pod = provider._build_pod_manifest("rpaclaw-sess-demo-abcd1234", "sess-demo", "user-1")

    metadata = pod["metadata"]
    container = pod["spec"]["containers"][0]

    assert metadata["labels"]["team"] == "beta"
    assert metadata["labels"]["track"] == "session-runtime"
    assert metadata["annotations"]["owner"] == "rpaclaw"
    assert pod["spec"]["serviceAccountName"] == "rpaclaw-runtime"
    assert pod["spec"]["imagePullSecrets"] == [{"name": "regcred"}, {"name": "another-secret"}]
    assert pod["spec"]["nodeSelector"] == {
        "pool": "runtime",
        "topology.kubernetes.io/zone": "cn-beijing-a",
    }
    assert pod["spec"]["tolerations"] == [
        {"key": "dedicated", "operator": "Equal", "value": "runtime", "effect": "NoSchedule"}
    ]
    assert pod["spec"]["volumes"] == [
        {
            "name": "workspace-data",
            "persistentVolumeClaim": {"claimName": "rpaclaw-workspace"},
        },
        {
            "name": "tools",
            "persistentVolumeClaim": {"claimName": "rpaclaw-tools"},
        },
    ]
    assert container["imagePullPolicy"] == "Always"
    assert container["env"] == [
        {"name": "TZ", "value": "Asia/Shanghai"},
        {"name": "PLAYWRIGHT_BROWSERS_PATH", "value": "/ms-playwright"},
    ]
    assert container["volumeMounts"] == [
        {"name": "workspace-data", "mountPath": "/home/rpaclaw"},
        {"name": "tools", "mountPath": "/app/Tools", "readOnly": True},
    ]
    assert container["resources"] == {
        "requests": {"cpu": "500m", "memory": "1Gi"},
        "limits": {"cpu": "2", "memory": "4Gi"},
    }


def test_k8s_runtime_provider_defaults_workspace_to_empty_dir():
    provider = K8sRuntimeProvider(
        _K8sSettings(),
        core_v1_api=_FakeCoreV1Api(),
        api_exception_cls=_FakeApiException,
        config_loader=lambda: None,
    )

    pod = provider._build_pod_manifest("rpaclaw-sess-demo-abcd1234", "sess-demo", "user-1")

    assert pod["spec"]["volumes"][0] == {"name": "workspace", "emptyDir": {}}
    assert pod["spec"]["containers"][0]["volumeMounts"][0] == {
        "name": "workspace",
        "mountPath": "/home/rpaclaw",
    }


def test_get_session_runtime_manager_is_singleton():
    reset_session_runtime_manager()
    repository = _FakeRepository(existing=None)
    provider = _FakeProvider()
    manager1 = get_session_runtime_manager(
        settings=_Settings("shared"),
        provider=provider,
        repository=repository,
    )
    manager2 = get_session_runtime_manager(settings=_Settings("docker"))

    assert manager1 is manager2


@pytest.mark.asyncio
async def test_destroy_runtime_deletes_record_and_calls_provider():
    existing = {
        "session_id": "sess-9",
        "user_id": "user-9",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-9",
        "service_name": "rpaclaw-sess-sess-9-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-9-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": None,
    }
    provider = _FakeProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    destroyed = await manager.destroy_runtime("sess-9")

    assert destroyed is True
    assert len(provider.delete_calls) == 1
    assert provider.delete_calls[0].session_id == "sess-9"
    assert repository.deleted == [{"session_id": "sess-9"}]


@pytest.mark.asyncio
async def test_destroy_runtime_returns_false_when_missing():
    provider = _FakeProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    destroyed = await manager.destroy_runtime("sess-missing")

    assert destroyed is False
    assert provider.delete_calls == []
    assert repository.deleted == []


@pytest.mark.asyncio
async def test_ensure_runtime_refreshes_expiration_window(monkeypatch):
    import backend.runtime.session_runtime_manager as runtime_manager_module

    existing = {
        "session_id": "sess-ttl",
        "user_id": "user-ttl",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-ttl",
        "service_name": "rpaclaw-sess-sess-ttl-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-ttl-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": 2,
    }
    provider = _FakeProvider()
    repository = _FakeRepository(existing=existing)
    settings = _Settings("shared")
    settings.runtime_idle_ttl_seconds = 120
    manager = SessionRuntimeManager(
        provider=provider,
        repository=repository,
        settings=settings,
    )
    monkeypatch.setattr(runtime_manager_module.time, "time", lambda: 100)

    runtime = await manager.ensure_runtime("sess-ttl", "user-ttl")

    assert runtime.last_used_at == 100
    assert runtime.expires_at == 220
    assert repository.updated[0][0] == {"session_id": "sess-ttl"}
    assert repository.updated[0][1]["$set"]["status"] == "ready"
    assert repository.updated[0][1]["$set"]["last_used_at"] == 100
    assert repository.updated[0][1]["$set"]["expires_at"] == 220


@pytest.mark.asyncio
async def test_cleanup_orphans_deletes_only_unowned_runtime_records():
    provider = _FakeProvider()
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-1",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-1",
                "service_name": "rpaclaw-sess-sess-1-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
            {
                "session_id": "sess-2",
                "user_id": "user-2",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-2",
                "service_name": "rpaclaw-sess-sess-2-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-2-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
        ]
    )
    async def _owner_checker(record):
        return record.session_id == "sess-1"

    manager = SessionRuntimeManager(
        provider=provider,
        repository=repository,
        owner_checker=_owner_checker,
    )

    cleaned = await manager.cleanup_orphans()

    assert cleaned == 1
    assert [record.session_id for record in provider.delete_calls] == ["sess-2"]
    assert repository.deleted == [{"session_id": "sess-2"}]


@pytest.mark.asyncio
async def test_cleanup_expired_deletes_only_expired_records():
    provider = _FakeProvider()
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-expired",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-expired",
                "service_name": "rpaclaw-sess-sess-expired-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-expired-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 50,
            },
            {
                "session_id": "sess-active",
                "user_id": "user-2",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-active",
                "service_name": "rpaclaw-sess-sess-active-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-active-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 90,
                "expires_at": 200,
            },
        ]
    )
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    cleaned = await manager.cleanup_expired(now_ts=100)

    assert cleaned == 1
    assert [record.session_id for record in provider.delete_calls] == ["sess-expired"]
    assert repository.deleted == [{"session_id": "sess-expired"}]


@pytest.mark.asyncio
async def test_cleanup_expired_sanitizes_delete_failure_log(caplog):
    class _FailingDeleteProvider(_FakeProvider):
        async def delete_runtime(self, runtime_record) -> None:
            self.delete_calls.append(runtime_record)
            raise RuntimeError(
                "delete failed api_token=secret-token Authorization=Bearer adapter-secret"
            )

    provider = _FailingDeleteProvider()
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-expired-log",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-expired-log",
                "service_name": "rpaclaw-sess-sess-expired-log-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-expired-log-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 50,
            },
        ]
    )
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    with caplog.at_level("WARNING", logger="backend.runtime.session_runtime_manager"):
        cleaned = await manager.cleanup_expired(now_ts=100)

    assert cleaned == 0
    assert [record.session_id for record in provider.delete_calls] == ["sess-expired-log"]
    assert repository.deleted == []
    assert "secret-token" not in caplog.text
    assert "adapter-secret" not in caplog.text
    assert "<redacted>" in caplog.text


@pytest.mark.asyncio
async def test_cleanup_orphans_sanitizes_bare_runtime_token_delete_failure_log(caplog):
    class _FailingDeleteProvider(_FakeProvider):
        async def delete_runtime(self, runtime_record) -> None:
            self.delete_calls.append(runtime_record)
            raise RuntimeError(f"delete failed with {runtime_record.runtime_token}")

    provider = _FailingDeleteProvider()
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-orphan-log",
                "user_id": "user-1",
                "namespace": "aio",
                "pod_name": "sb-orphan-log",
                "service_name": "sb-orphan-log",
                "rest_base_url": "http://route.internal/sb-orphan-log",
                "route_base_url": "http://route.internal/sb-orphan-log",
                "runtime_token": "bare-runtime-secret",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 50,
            },
        ]
    )

    async def _owner_checker(record):
        return False

    manager = SessionRuntimeManager(
        provider=provider,
        repository=repository,
        owner_checker=_owner_checker,
    )

    with caplog.at_level("WARNING", logger="backend.runtime.session_runtime_manager"):
        cleaned = await manager.cleanup_orphans()

    assert cleaned == 0
    assert [record.session_id for record in provider.delete_calls] == ["sess-orphan-log"]
    assert repository.deleted == []
    assert "bare-runtime-secret" not in caplog.text
    assert "<redacted>" in caplog.text


@pytest.mark.asyncio
async def test_get_runtime_returns_none_when_missing():
    provider = _FakeProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.get_runtime("sess-missing")

    assert runtime is None
    assert provider.refresh_calls == []


@pytest.mark.asyncio
async def test_get_runtime_can_refresh_status_and_persist():
    existing = {
        "session_id": "sess-refresh",
        "user_id": "user-refresh",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-refresh",
        "service_name": "rpaclaw-sess-sess-refresh-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-refresh-svc:8080",
        "status": "creating",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": 10,
    }

    class _RefreshingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "ready"
            runtime_record.route_base_url = "http://adapter-refreshed.test"
            runtime_record.metadata = {
                "adapter_health_status": "ok",
                "adapter_file_policy": {
                    "max_inline_file_write_bytes": 10 * 1024 * 1024,
                    "max_file_download_bytes": 50 * 1024 * 1024,
                    "oversized_hash_status": "skipped_oversized",
                },
            }
            return runtime_record

    provider = _RefreshingProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.get_runtime("sess-refresh", refresh=True)

    assert runtime is not None
    assert runtime.status == "ready"
    assert runtime.route_base_url == "http://adapter-refreshed.test"
    assert runtime.metadata["adapter_health_status"] == "ok"
    assert len(provider.refresh_calls) == 1
    assert repository.updated[0][0] == {"session_id": "sess-refresh"}
    persisted = repository.updated[0][1]["$set"]
    assert persisted["status"] == "ready"
    assert persisted["route_base_url"] == "http://adapter-refreshed.test"
    assert persisted["metadata"]["adapter_file_policy"]["oversized_hash_status"] == "skipped_oversized"


@pytest.mark.asyncio
async def test_get_runtime_refresh_deletes_missing_runtime_record():
    existing = {
        "session_id": "sess-missing-refresh",
        "user_id": "user-refresh",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-missing-refresh",
        "service_name": "rpaclaw-sess-sess-missing-refresh-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-missing-refresh-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": 10,
    }

    class _MissingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "missing"
            return runtime_record

    provider = _MissingProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.get_runtime("sess-missing-refresh", refresh=True)

    assert runtime is None
    assert len(provider.refresh_calls) == 1
    assert repository.updated == []
    assert repository.deleted == [{"session_id": "sess-missing-refresh"}]


@pytest.mark.asyncio
async def test_list_runtimes_filters_by_user_without_side_effects():
    provider = _FakeProvider()
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-1",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-1",
                "service_name": "rpaclaw-sess-sess-1-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
            {
                "session_id": "sess-2",
                "user_id": "user-2",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-2",
                "service_name": "rpaclaw-sess-sess-2-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-2-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
        ]
    )
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtimes = await manager.list_runtimes(user_id="user-1")

    assert [runtime.session_id for runtime in runtimes] == ["sess-1"]
    assert provider.refresh_calls == []


@pytest.mark.asyncio
async def test_list_runtimes_refreshes_and_persists_updates():
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-1",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-1",
                "service_name": "rpaclaw-sess-sess-1-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
                "status": "creating",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            }
        ]
    )

    class _RefreshingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "ready"
            runtime_record.route_base_url = "http://adapter-list-refreshed.test"
            runtime_record.metadata = {
                "adapter_health_status": "ok",
                "adapter_contract_version": "v1",
            }
            return runtime_record

    provider = _RefreshingProvider()
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtimes = await manager.list_runtimes(refresh=True)

    assert len(runtimes) == 1
    assert runtimes[0].status == "ready"
    assert runtimes[0].route_base_url == "http://adapter-list-refreshed.test"
    assert runtimes[0].metadata["adapter_contract_version"] == "v1"
    assert len(provider.refresh_calls) == 1
    assert repository.updated[0][0] == {"session_id": "sess-1"}
    persisted = repository.updated[0][1]["$set"]
    assert persisted["status"] == "ready"
    assert persisted["route_base_url"] == "http://adapter-list-refreshed.test"
    assert persisted["metadata"]["adapter_contract_version"] == "v1"


@pytest.mark.asyncio
async def test_list_runtimes_refresh_omits_missing_records_and_deletes_them():
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-stale",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-stale",
                "service_name": "rpaclaw-sess-sess-stale-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-stale-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
            {
                "session_id": "sess-live",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-live",
                "service_name": "rpaclaw-sess-sess-live-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-live-svc:8080",
                "status": "creating",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
        ]
    )

    class _RefreshingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "missing" if runtime_record.session_id == "sess-stale" else "ready"
            return runtime_record

    provider = _RefreshingProvider()
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtimes = await manager.list_runtimes(refresh=True)

    assert [runtime.session_id for runtime in runtimes] == ["sess-live"]
    assert len(provider.refresh_calls) == 2
    assert repository.deleted == [{"session_id": "sess-stale"}]
    assert repository.updated[0][0] == {"session_id": "sess-live"}
    assert repository.updated[0][1]["$set"]["status"] == "ready"
    assert repository.updated[0][1]["$set"]["session_id"] == "sess-live"
