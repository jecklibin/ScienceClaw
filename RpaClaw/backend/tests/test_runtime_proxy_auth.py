import importlib

import pytest


RUNTIME_PROXY = importlib.import_module("backend.route.runtime_proxy")


class FakeWebSocketRequest:
    headers = {}
    cookies = {}


@pytest.mark.anyio
async def test_runtime_proxy_websocket_no_auth_resolves_bootstrap_admin(monkeypatch):
    async def fake_get_current_user(_request):
        return RUNTIME_PROXY.User(id="admin-uuid", username="admin", role="admin")

    monkeypatch.setattr(RUNTIME_PROXY.settings, "storage_backend", "local")
    monkeypatch.setattr(RUNTIME_PROXY.settings, "auth_provider", "none")
    monkeypatch.setattr(RUNTIME_PROXY, "get_current_user", fake_get_current_user)

    user = await RUNTIME_PROXY._get_websocket_user(FakeWebSocketRequest())

    assert user is not None
    assert user.id == "admin-uuid"
