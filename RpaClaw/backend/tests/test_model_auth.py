import asyncio
from types import SimpleNamespace

import pytest

from backend.model_auth import ModelAuthResolutionError, ModelAuthResolver


class FakeVault:
    def __init__(self, values, model_auths=None):
        self.values = values
        self.model_auths = model_auths or {}
        self.calls = []

    async def resolve_credential_values(self, user_id: str, cred_id: str):
        self.calls.append((user_id, cred_id))
        return self.values.get(cred_id)

    async def resolve_model_auth(self, user_id: str, cred_id: str):
        self.calls.append((user_id, cred_id))
        return self.model_auths.get(cred_id)


def run(coro):
    return asyncio.run(coro)


def static_config(headers=None, query=None, credentials=None):
    return {
        "api_key": "base-api-key",
        "auth_config": {
            "type": "static_headers",
            "credentials": credentials
            or [
                {"alias": "gateway", "credential_id": "cred-gateway"},
                {"alias": "tenant", "credential_id": "cred-tenant"},
            ],
            "headers": {
                "Authorization": "Bearer {{ gateway.password }}",
                "X-Tenant": "{{ tenant.username }}",
            } if headers is None else headers,
            "query": {} if query is None else query,
        },
    }


def test_static_headers_resolve_headers_and_query_templates():
    vault = FakeVault(
        {
            "cred-gateway": {"username": "", "password": "company-token", "domain": "gw.example"},
            "cred-tenant": {"username": "tenant-a", "password": "unused", "domain": "tenant.example"},
        }
    )
    resolver = ModelAuthResolver(vault=vault)

    resolved = run(
        resolver.resolve(
            static_config(
                query={"tenant": "{{ tenant.username }}", "host": "{{ gateway.domain }}"},
            ),
            "user-1",
        )
    )

    assert resolved.api_key == "base-api-key"
    assert resolved.default_headers == {
        "Authorization": "Bearer company-token",
        "X-Tenant": "tenant-a",
    }
    assert resolved.default_query == {"tenant": "tenant-a", "host": "gw.example"}
    assert resolved.default_body == {}
    assert vault.calls == [("user-1", "cred-gateway"), ("user-1", "cred-tenant")]


def test_model_auth_profile_static_headers_resolve_variables():
    vault = FakeVault(
        {},
        model_auths={
            "cred-auth": {
                "type": "static_headers",
                "config": {
                    "headers": {
                        "Authorization": "Bearer {{ api_key }}",
                        "X-Tenant": "{{ tenant_id }}",
                    },
                    "query": {"workspace": "{{ tenant_id }}"},
                },
                "variables": {
                    "api_key": {"sensitive": True, "value": "profile-key"},
                    "tenant_id": {"sensitive": False, "value": "tenant-a"},
                },
            }
        },
    )

    resolved = run(
        ModelAuthResolver(vault=vault).resolve(
            {"auth_credential_id": "cred-auth", "api_key": "base-api-key"},
            "user-1",
        )
    )

    assert resolved.api_key == "base-api-key"
    assert resolved.default_headers == {
        "Authorization": "Bearer profile-key",
        "X-Tenant": "tenant-a",
    }
    assert resolved.default_query == {"workspace": "tenant-a"}


def test_static_headers_reject_missing_credential():
    resolver = ModelAuthResolver(vault=FakeVault({}))

    with pytest.raises(ModelAuthResolutionError, match="凭据不存在"):
        run(resolver.resolve(static_config(), "user-1"))


def test_static_headers_reject_unknown_alias():
    resolver = ModelAuthResolver(
        vault=FakeVault({"cred-gateway": {"username": "", "password": "company-token", "domain": ""}})
    )

    with pytest.raises(ModelAuthResolutionError, match="未知凭据"):
        run(
            resolver.resolve(
                static_config(
                    headers={"Authorization": "Bearer {{ missing.password }}"},
                    credentials=[{"alias": "gateway", "credential_id": "cred-gateway"}],
                ),
                "user-1",
            )
        )


class FakeTokenResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    calls = []
    payloads = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, headers=None, params=None, json=None):
        self.__class__.calls.append(
            {"method": "POST", "url": url, "headers": headers, "params": params, "json": json}
        )
        return FakeTokenResponse(self.__class__.payloads.pop(0))

    async def request(self, method, url, headers=None, params=None, json=None, data=None, content=None):
        self.__class__.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json": json,
                "data": data,
                "content": content,
            }
        )
        return FakeTokenResponse(self.__class__.payloads.pop(0))

    async def get(self, url, headers=None, params=None):
        self.__class__.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return FakeTokenResponse(self.__class__.payloads.pop(0))


def dynamic_config(model_id="model-1"):
    return {
        "id": model_id,
        "api_key": "base-api-key",
        "auth_config": {
            "type": "dynamic_token",
            "credentials": [{"alias": "client", "credential_id": "cred-client"}],
            "token_request": {
                "method": "POST",
                "url": "https://auth.example/token",
                "headers": {"X-Client": "{{ client.username }}"},
                "query": {"aud": "{{ client.domain }}"},
                "body": {"client_secret": "{{ client.password }}"},
            },
            "inject": {
                "headers": {"Authorization": "Bearer {$.data.access_token}"},
                "query": {"access_token": "{$.data.access_token}"},
            },
        },
    }


def reset_dynamic_state():
    ModelAuthResolver._token_cache.clear()
    ModelAuthResolver._locks.clear()
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.payloads.clear()


def test_dynamic_token_first_request_renders_request_and_injects_token(monkeypatch):
    reset_dynamic_state()
    FakeAsyncClient.payloads = [{"data": {"access_token": "token-first"}, "expires_in": 3600}]
    monkeypatch.setattr("backend.model_auth.httpx.AsyncClient", FakeAsyncClient)
    vault = FakeVault(
        {"cred-client": {"username": "client-id", "password": "client-secret", "domain": "model-api"}}
    )

    resolved = run(ModelAuthResolver(vault=vault).resolve(dynamic_config(), "user-1"))

    assert resolved.default_headers == {"Authorization": "Bearer token-first"}
    assert resolved.default_query == {"access_token": "token-first"}
    assert FakeAsyncClient.calls == [
        {
            "method": "POST",
            "url": "https://auth.example/token",
            "headers": {"X-Client": "client-id"},
            "params": {"aud": "model-api"},
            "json": {"client_secret": "client-secret"},
            "data": None,
            "content": None,
        }
    ]


def test_dynamic_token_injects_response_fields_with_brace_paths(monkeypatch):
    reset_dynamic_state()
    FakeAsyncClient.payloads = [
        {
            "data": {
                "access_token": "field-token",
                "tenant": {"id": "tenant-a"},
                "session": {"meta": {"trace": "trace-1"}},
            },
            "expires_in": 3600,
        }
    ]
    monkeypatch.setattr("backend.model_auth.httpx.AsyncClient", FakeAsyncClient)
    vault = FakeVault(
        {"cred-client": {"username": "client-id", "password": "client-secret", "domain": "model-api"}}
    )
    config = dynamic_config()
    config["auth_config"]["inject"] = {
        "headers": {
            "Authorization": "Bearer {$.data.access_token}",
            "X-Tenant-Id": "{ $.data.tenant.id }",
            "X-Trace": "trace={$.data.session.meta.trace}",
        },
        "query": {},
        "body": {},
    }

    resolved = run(ModelAuthResolver(vault=vault).resolve(config, "user-1"))

    assert resolved.default_headers == {
        "Authorization": "Bearer field-token",
        "X-Tenant-Id": "tenant-a",
        "X-Trace": "trace=trace-1",
    }


def test_model_auth_profile_dynamic_token_fetches_and_injects_response_fields(monkeypatch):
    reset_dynamic_state()
    FakeAsyncClient.payloads = [
        {
            "data": {
                "access_token": "profile-token",
                "expires_in": 3600,
                "tenant": {"id": "tenant-a"},
            }
        }
    ]
    monkeypatch.setattr("backend.model_auth.httpx.AsyncClient", FakeAsyncClient)
    vault = FakeVault(
        {},
        model_auths={
            "cred-dynamic": {
                "type": "dynamic_token",
                "config": {
                    "token_request": {
                        "method": "POST",
                        "url": "https://auth.example/token",
                        "headers": {"X-Client": "{{ client_id }}"},
                        "query": {"aud": "{{ tenant_id }}"},
                        "body_type": "json",
                        "body": {"client_secret": "{{ client_secret }}"},
                    },
                    "inject": {
                        "headers": {
                            "Authorization": "Bearer {$.data.access_token}",
                            "X-Tenant-Id": "{$.data.tenant.id}",
                        },
                        "query": {},
                        "body": {},
                    },
                },
                "variables": {
                    "client_id": {"sensitive": False, "value": "client-a"},
                    "client_secret": {"sensitive": True, "value": "secret-a"},
                    "tenant_id": {"sensitive": False, "value": "tenant-a"},
                },
            }
        },
    )

    resolved = run(
        ModelAuthResolver(vault=vault).resolve(
            {"auth_credential_id": "cred-dynamic", "model_name": "company-gpt"},
            "user-1",
        )
    )

    assert resolved.default_headers == {
        "Authorization": "Bearer profile-token",
        "X-Tenant-Id": "tenant-a",
    }
    assert FakeAsyncClient.calls == [
        {
            "method": "POST",
            "url": "https://auth.example/token",
            "headers": {"X-Client": "client-a"},
            "params": {"aud": "tenant-a"},
            "json": {"client_secret": "secret-a"},
            "data": None,
            "content": None,
        }
    ]


def test_dynamic_token_supports_form_body_array_path_and_body_injection(monkeypatch):
    reset_dynamic_state()
    FakeAsyncClient.payloads = [{"items": [{"access": {"token": "form-token"}}], "ttl": 3600}]
    monkeypatch.setattr("backend.model_auth.httpx.AsyncClient", FakeAsyncClient)
    vault = FakeVault(
        {"cred-client": {"username": "client-id", "password": "client-secret", "domain": "tenant-a"}}
    )

    config = dynamic_config()
    config["auth_config"]["token_request"]["headers"] = {"X-Client": "{{ client.username }}"}
    config["auth_config"]["token_request"]["body_type"] = "form"
    config["auth_config"]["token_request"]["body"] = {
        "client_secret": "{{ client.password }}",
        "tenant": "{{ client.domain }}",
    }
    config["auth_config"]["inject"] = {
        "headers": {},
        "query": {},
        "body": {"auth": {"access_token": "{$.items[0].access.token}"}},
    }

    resolved = run(ModelAuthResolver(vault=vault).resolve(config, "user-1"))

    assert resolved.default_body == {"auth": {"access_token": "form-token"}}
    assert FakeAsyncClient.calls == [
        {
            "method": "POST",
            "url": "https://auth.example/token",
            "headers": {"X-Client": "client-id"},
            "params": {"aud": "tenant-a"},
            "json": None,
            "data": {"client_secret": "client-secret", "tenant": "tenant-a"},
            "content": None,
        }
    ]


def test_dynamic_token_uses_cache_for_unexpired_token(monkeypatch):
    reset_dynamic_state()
    FakeAsyncClient.payloads = [{"data": {"access_token": "cached-token"}, "expires_in": 3600}]
    monkeypatch.setattr("backend.model_auth.httpx.AsyncClient", FakeAsyncClient)
    vault = FakeVault(
        {"cred-client": {"username": "client-id", "password": "client-secret", "domain": "model-api"}}
    )
    resolver = ModelAuthResolver(vault=vault)

    first = run(resolver.resolve(dynamic_config(), "user-1"))
    second = run(resolver.resolve(dynamic_config(), "user-1"))

    assert first.default_headers == second.default_headers == {"Authorization": "Bearer cached-token"}
    assert len(FakeAsyncClient.calls) == 1
    assert vault.calls == [("user-1", "cred-client")]


def test_dynamic_token_uses_default_cache_ttl(monkeypatch):
    reset_dynamic_state()
    FakeAsyncClient.payloads = [
        {"data": {"access_token": "old-token"}, "expires_in": 1},
        {"data": {"access_token": "new-token"}, "expires_in": 3600},
    ]
    monkeypatch.setattr("backend.model_auth.httpx.AsyncClient", FakeAsyncClient)
    vault = FakeVault(
        {"cred-client": {"username": "client-id", "password": "client-secret", "domain": "model-api"}}
    )
    resolver = ModelAuthResolver(vault=vault)

    old = run(resolver.resolve(dynamic_config(), "user-1"))
    new = run(resolver.resolve(dynamic_config(), "user-1"))

    assert old.default_headers == {"Authorization": "Bearer old-token"}
    assert new.default_headers == {"Authorization": "Bearer old-token"}
    assert len(FakeAsyncClient.calls) == 1


def test_dynamic_token_can_inject_cached_response_fields(monkeypatch):
    reset_dynamic_state()
    config = dynamic_config()
    config["auth_config"]["inject"] = {"headers": {"Authorization": "Bearer {$.data.access_token}"}}
    FakeAsyncClient.payloads = [{"data": {"access_token": "response-token"}, "expires_in": 3600}]
    monkeypatch.setattr("backend.model_auth.httpx.AsyncClient", FakeAsyncClient)
    vault = FakeVault(
        {"cred-client": {"username": "client-id", "password": "client-secret", "domain": "model-api"}}
    )

    resolved = run(ModelAuthResolver(vault=vault).resolve(config, "user-1"))

    assert resolved.default_headers == {"Authorization": "Bearer response-token"}


def test_build_llm_model_passes_default_headers_and_query(monkeypatch):
    from backend.deepagent import engine

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.profile = None

    monkeypatch.setattr(engine, "_SafeChatOpenAI", FakeChatOpenAI)

    model = engine._build_llm_model(
        config={
            "provider": "openai",
            "model_name": "gpt-compatible",
            "base_url": "https://model.example/v1",
            "api_key": "base-api-key",
        },
        resolved_auth={
            "api_key": "resolved-api-key",
            "default_headers": {"X-Gateway": "present"},
            "default_query": {"tenant": "tenant-a"},
            "default_body": {"auth": {"access_token": "present"}},
        },
        streaming=False,
    )

    assert model.profile == {"max_input_tokens": engine._resolve_context_window("gpt-compatible")}
    assert captured["api_key"] == "resolved-api-key"
    assert captured["default_headers"] == {"X-Gateway": "present"}
    assert captured["default_query"] == {"tenant": "tenant-a"}
    assert captured["extra_body"] == {"auth": {"access_token": "present"}}


def test_get_llm_model_for_user_handles_legacy_model_without_auth_config(monkeypatch):
    from backend.deepagent import engine

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.profile = None

    monkeypatch.setattr(engine, "_SafeChatOpenAI", FakeChatOpenAI)

    model = run(
        engine.get_llm_model_for_user(
            config={
                "provider": "openai",
                "model_name": "legacy-compatible",
                "base_url": "https://legacy.example/v1",
                "api_key": "legacy-key",
            },
            user_id="user-1",
            streaming=False,
        )
    )

    assert isinstance(model, FakeChatOpenAI)
    assert captured["api_key"] == "legacy-key"
    assert "default_headers" not in captured
    assert "default_query" not in captured
