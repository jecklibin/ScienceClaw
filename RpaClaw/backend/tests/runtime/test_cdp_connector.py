import importlib
import sys
import types

import pytest

from backend.runtime.models import SessionRuntimeRecord


def _install_fake_playwright_modules():
    playwright_module = types.ModuleType("playwright")
    async_api_module = types.ModuleType("playwright.async_api")
    async_api_module.async_playwright = lambda: None
    async_api_module.Browser = object
    async_api_module.Playwright = object
    async_api_module.Page = object
    async_api_module.BrowserContext = object
    sys.modules["playwright"] = playwright_module
    sys.modules["playwright.async_api"] = async_api_module


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
        )


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "data": {
                "cdp_url": "ws://127.0.0.1:9222/devtools/browser/test-id",
            }
        }


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        self.calls.append(url)
        return _FakeResponse()


class _FakeRuntimeAdapterClient:
    records = []

    def __init__(self, runtime):
        self.runtime = runtime
        self.__class__.records.append(runtime)

    async def browser_info(self):
        return {
            "data": {
                "cdp_url": "ws://127.0.0.1:9222/devtools/browser/test-id",
            }
        }


class _FakeChromium:
    def __init__(self):
        self.connect_calls = []
        self.launch_calls = []

    async def connect_over_cdp(self, cdp_url):
        self.connect_calls.append(cdp_url)
        return object()

    async def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return object()


class _FakePlaywrightHandle:
    def __init__(self):
        self.chromium = _FakeChromium()


class _FakeAsyncPlaywrightFactory:
    def __init__(self, handle):
        self.handle = handle

    async def start(self):
        return self.handle


@pytest.mark.anyio
async def test_fetch_cdp_url_uses_runtime_endpoint_for_session(monkeypatch):
    _install_fake_playwright_modules()
    sys.modules.pop("backend.rpa.cdp_connector", None)
    cdp_connector = importlib.import_module("backend.rpa.cdp_connector")

    monkeypatch.setattr(cdp_connector, "get_session_runtime_manager", lambda: _FakeManager())
    _FakeRuntimeAdapterClient.records = []
    monkeypatch.setattr(cdp_connector, "RuntimeAdapterClient", _FakeRuntimeAdapterClient)

    connector = cdp_connector.CDPConnector()

    cdp_url = await connector._fetch_cdp_url(session_id="sess-1", user_id="user-1")

    assert _FakeRuntimeAdapterClient.records[0].route_base_url == "http://aio-route.local/session/sess-1"
    assert _FakeRuntimeAdapterClient.records[0].runtime_token == "runtime-token"
    assert cdp_url == "ws://aio-route.local/devtools/browser/test-id"


@pytest.mark.anyio
async def test_fetch_cdp_url_rejects_non_ready_session_runtime(monkeypatch):
    _install_fake_playwright_modules()
    sys.modules.pop("backend.rpa.cdp_connector", None)
    cdp_connector = importlib.import_module("backend.rpa.cdp_connector")

    class _CreatingManager(_FakeManager):
        async def ensure_runtime(self, session_id: str, user_id: str) -> SessionRuntimeRecord:
            record = await super().ensure_runtime(session_id, user_id)
            record.status = "creating"
            record.metadata = {"runtime_token": "must-not-leak"}
            return record

    monkeypatch.setattr(cdp_connector, "get_session_runtime_manager", lambda: _CreatingManager())
    _FakeRuntimeAdapterClient.records = []
    monkeypatch.setattr(cdp_connector, "RuntimeAdapterClient", _FakeRuntimeAdapterClient)

    connector = cdp_connector.CDPConnector()

    with pytest.raises(RuntimeError) as exc_info:
        await connector._fetch_cdp_url(session_id="sess-1", user_id="user-1")

    assert "Runtime is not ready for CDP connection" in str(exc_info.value)
    assert "runtime-token" not in str(exc_info.value)
    assert _FakeRuntimeAdapterClient.records == []


@pytest.mark.anyio
async def test_local_launch_uses_relaxed_security_browser_args(monkeypatch):
    _install_fake_playwright_modules()
    sys.modules.pop("backend.rpa.cdp_connector", None)
    cdp_connector = importlib.import_module("backend.rpa.cdp_connector")

    fake_playwright = _FakePlaywrightHandle()
    monkeypatch.setattr(
        cdp_connector,
        "async_playwright",
        lambda: _FakeAsyncPlaywrightFactory(fake_playwright),
    )

    _pw, _browser = await cdp_connector.LocalCDPConnector._launch()

    from backend.rpa.playwright_security import RPA_RELAXED_CHROMIUM_ARGS

    assert fake_playwright.chromium.launch_calls == [
        {
            "headless": False,
            "args": list(RPA_RELAXED_CHROMIUM_ARGS),
        }
    ]


def test_get_cdp_connector_prefers_explicit_runtime_mode_over_local_storage(monkeypatch):
    _install_fake_playwright_modules()
    sys.modules.pop("backend.rpa.cdp_connector", None)
    cdp_connector = importlib.import_module("backend.rpa.cdp_connector")

    monkeypatch.setattr(cdp_connector.settings, "storage_backend", "local")
    monkeypatch.setattr(cdp_connector.settings, "runtime_mode", "aio_native")

    assert cdp_connector.get_cdp_connector() is cdp_connector.cdp_connector


@pytest.mark.anyio
async def test_get_browser_for_aio_native_connects_in_current_event_loop(monkeypatch):
    _install_fake_playwright_modules()
    sys.modules.pop("backend.rpa.cdp_connector", None)
    cdp_connector = importlib.import_module("backend.rpa.cdp_connector")

    monkeypatch.setattr(cdp_connector.settings, "runtime_mode", "aio_native")
    monkeypatch.setattr(cdp_connector, "get_session_runtime_manager", lambda: _FakeManager())
    monkeypatch.setattr(cdp_connector, "RuntimeAdapterClient", _FakeRuntimeAdapterClient)

    fake_playwright = _FakePlaywrightHandle()
    monkeypatch.setattr(
        cdp_connector,
        "async_playwright",
        lambda: _FakeAsyncPlaywrightFactory(fake_playwright),
    )

    connector = cdp_connector.CDPConnector()

    async def fail_if_background_loop_used(_coro):
        raise AssertionError("aio_native CDP connect should stay on the caller event loop")

    monkeypatch.setattr(connector, "_run_in_pw_loop", fail_if_background_loop_used)

    browser = await connector.get_browser(session_id="sess-1", user_id="user-1")

    assert browser is not None
    assert fake_playwright.chromium.connect_calls == [
        "ws://aio-route.local/devtools/browser/test-id"
    ]
