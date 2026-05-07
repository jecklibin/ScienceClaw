import asyncio

import httpx
from langchain_core.messages import HumanMessage

from backend.deepagent.engine import _build_llm_model
from backend.model_auth import ModelAuthResolver
from backend.tests.fixtures.mock_model_auth_server import MOCK_DYNAMIC_MODEL, ModelAuthMockServer


class FakeVault:
    def __init__(self, values):
        self.values = values
        self.calls = []

    async def resolve_credential_values(self, user_id: str, cred_id: str):
        self.calls.append((user_id, cred_id))
        return self.values.get(cred_id)


def run(coro):
    return asyncio.run(coro)


def disable_proxy_env(monkeypatch):
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def dynamic_http_config(server: ModelAuthMockServer):
    return {
        "id": "mock-dynamic-http-config",
        "api_key": "base-api-key",
        "auth_config": {
            "type": "dynamic_token",
            "credentials": [{"alias": "client", "credential_id": "cred-client"}],
            "token_request": {
                "method": "POST",
                "url": f"{server.origin}/token",
                "headers": {"X-Client-Id": "{{ client.username }}"},
                "query": {"aud": "{{ client.domain }}"},
                "body": {
                    "client_id": "{{ client.username }}",
                    "client_secret": "{{ client.password }}",
                    "tenant": "{{ client.domain }}",
                },
            },
            "inject": {
                "headers": {
                    "Authorization": "Bearer {$.data.access_token}",
                    "X-Tenant-Id": "{ $.data.tenant.id }",
                },
                "query": {},
            },
        },
    }


def test_dynamic_token_fetch_and_model_call_over_http(monkeypatch):
    disable_proxy_env(monkeypatch)
    ModelAuthResolver._token_cache.clear()
    ModelAuthResolver._locks.clear()
    vault = FakeVault(
        {"cred-client": {"username": "demo-client", "password": "demo-secret", "domain": "tenant-a"}}
    )

    with ModelAuthMockServer() as server:
        resolved = run(ModelAuthResolver(vault=vault).resolve(dynamic_http_config(server), "user-1"))
        model = _build_llm_model(
            config={
                "provider": "openai",
                "model_name": MOCK_DYNAMIC_MODEL,
                "base_url": server.base_url,
                "api_key": "base-api-key",
                "context_window": 8192,
            },
            resolved_auth=resolved.model_dump(),
            streaming=False,
        )

        response = model.invoke([HumanMessage(content="hello")])

    assert response.content == "dynamic auth ok"
    token_requests = [item for item in server.requests if item["path"] == "/token"]
    chat_requests = [item for item in server.requests if item["path"] == "/v1/chat/completions"]
    assert len(token_requests) == 1
    assert token_requests[0]["body"] == {
        "client_id": "demo-client",
        "client_secret": "demo-secret",
        "tenant": "tenant-a",
    }
    assert token_requests[0]["query"] == {"aud": "tenant-a"}
    assert len(chat_requests) == 1
    assert chat_requests[0]["headers"]["Authorization"].startswith("Bearer dyn-")
    assert chat_requests[0]["headers"]["X-Tenant-Id"] == "tenant-a"


def test_dynamic_model_rejects_invalid_token(monkeypatch):
    disable_proxy_env(monkeypatch)

    with ModelAuthMockServer() as server:
        response = httpx.post(
            f"{server.base_url}/chat/completions",
            headers={"Authorization": "Bearer invalid-token"},
            json={
                "model": MOCK_DYNAMIC_MODEL,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 401
    assert response.json() == {"error": "missing or invalid dynamic token"}
