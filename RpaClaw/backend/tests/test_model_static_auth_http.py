import asyncio

from langchain_core.messages import HumanMessage

from backend.deepagent.engine import _build_llm_model
from backend.route.models import _probe_context_window_via_api
from backend.tests.fixtures.mock_model_auth_server import (
    MOCK_STATIC_MODEL,
    STATIC_AUTH_HEADERS,
    ModelAuthMockServer,
)


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


def test_context_window_probe_sends_static_auth_headers_over_http(monkeypatch):
    disable_proxy_env(monkeypatch)

    with ModelAuthMockServer() as server:
        result = run(
            _probe_context_window_via_api(
                server.base_url,
                "base-api-key",
                MOCK_STATIC_MODEL,
                default_headers=STATIC_AUTH_HEADERS,
            )
        )

    assert result == 8192
    assert server.requests[0]["method"] == "GET"
    assert server.requests[0]["path"] == f"/v1/models/{MOCK_STATIC_MODEL}"
    assert server.requests[0]["headers"]["Authorization"] == "Bearer static-token"
    assert server.requests[0]["headers"]["X-Tenant"] == "tenant-a"


def test_chat_model_sends_static_auth_headers_over_http(monkeypatch):
    disable_proxy_env(monkeypatch)

    with ModelAuthMockServer() as server:
        model = _build_llm_model(
            config={
                "provider": "openai",
                "model_name": MOCK_STATIC_MODEL,
                "base_url": server.base_url,
                "api_key": "base-api-key",
                "context_window": 8192,
            },
            resolved_auth={
                "api_key": "base-api-key",
                "default_headers": STATIC_AUTH_HEADERS,
            },
            streaming=False,
        )

        response = model.invoke([HumanMessage(content="hello")])

    assert response.content == "static auth ok"
    post_requests = [item for item in server.requests if item["method"] == "POST"]
    assert len(post_requests) == 1
    assert post_requests[0]["path"] == "/v1/chat/completions"
    assert post_requests[0]["headers"]["Authorization"] == "Bearer static-token"
    assert post_requests[0]["headers"]["X-Tenant"] == "tenant-a"
