import asyncio
from types import SimpleNamespace

from backend.models import ModelAuthSaveRequest, UpdateModelRequest
from backend.route import models as model_routes


def run(coro):
    return asyncio.run(coro)


class FakeVault:
    def __init__(self):
        self.created = []
        self.deleted = []
        self.next_id = 1

    async def create(self, user_id, data):
        credential_id = f"cred-new-{self.next_id}"
        self.next_id += 1
        self.created.append({"user_id": user_id, "id": credential_id, "data": data})
        return SimpleNamespace(id=credential_id)

    async def delete(self, user_id, credential_id):
        self.deleted.append((user_id, credential_id))
        return True

    async def resolve_credential_values(self, user_id, credential_id):
        return {"username": "existing-user", "password": "existing-secret", "domain": "existing-domain"}

    async def resolve_model_auth(self, user_id, credential_id):
        return {"type": "static_headers", "config": {"headers": {}}, "variables": {}}


class FakeRepo:
    def __init__(self, existing):
        self.existing = existing
        self.update_filter = None
        self.update_doc = None

    async def find_one(self, query):
        return self.existing

    async def update_one(self, query, update):
        self.update_filter = query
        self.update_doc = update

    async def delete_one(self, query):
        self.delete_filter = query


def test_prepare_auth_config_stores_static_header_values_in_vault(monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    requested = ModelAuthSaveRequest(
        type="static_headers",
        static_headers=[
            {"name": "Authorization", "value": "Bearer secret-token"},
            {"name": "X-Tenant", "value": "tenant-a"},
        ],
    )

    auth_config, created_ids = run(
        model_routes._prepare_auth_config(
            user_id="user-1",
            model_id="model-1",
            model_name="company-gpt",
            base_url="https://model.example/v1",
            requested=requested,
        )
    )

    assert created_ids == {"cred-new-1", "cred-new-2"}
    assert [item["data"].password for item in vault.created] == ["Bearer secret-token", "tenant-a"]
    assert auth_config["headers"] == {
        "Authorization": "{{ header_authorization.password }}",
        "X-Tenant": "{{ header_x_tenant.password }}",
    }
    assert "Bearer secret-token" not in str(auth_config)
    assert "tenant-a" not in str(auth_config)


def test_prepare_auth_config_keeps_existing_secret_when_edit_value_is_blank(monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    existing_auth_config = {
        "type": "static_headers",
        "credentials": [{"alias": "header_authorization", "credential_id": "cred-old"}],
        "headers": {"Authorization": "{{ header_authorization.password }}"},
        "query": {},
    }
    requested = ModelAuthSaveRequest(
        type="static_headers",
        static_headers=[{"name": "Authorization", "value": ""}],
    )

    auth_config, created_ids = run(
        model_routes._prepare_auth_config(
            user_id="user-1",
            model_id="model-1",
            model_name="company-gpt",
            base_url="https://model.example/v1",
            requested=requested,
            existing_auth_config=existing_auth_config,
        )
    )

    assert created_ids == set()
    assert vault.created == []
    assert auth_config["credentials"] == [
        {"alias": "header_authorization", "credential_id": "cred-old", "owned_by_model": True}
    ]
    assert auth_config["headers"] == {"Authorization": "{{ header_authorization.password }}"}


def test_prepare_auth_config_stores_dynamic_token_credentials_in_vault(monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    requested = ModelAuthSaveRequest(
        type="dynamic_token",
        dynamic_token={
            "credentials": [
                {
                    "alias": "client",
                    "username": "client-id",
                    "password": "client-secret",
                    "domain": "tenant-a",
                }
            ],
            "token_request": {
                "method": "POST",
                "url": "https://auth.example/token",
                "headers": {"X-Client": "{{ client.username }}"},
                "body_type": "json",
                "body": {"secret": "{{ client.password }}"},
            },
            "inject": {
                "headers": {"Authorization": "Bearer {$.data.access_token}"},
                "query": {},
                "body": {"auth": {"access_token": "{$.data.access_token}"}},
            },
        },
    )

    auth_config, created_ids = run(
        model_routes._prepare_auth_config(
            user_id="user-1",
            model_id="model-1",
            model_name="company-gpt",
            base_url="https://model.example/v1",
            requested=requested,
        )
    )

    assert created_ids == {"cred-new-1"}
    assert [item["data"].username for item in vault.created] == ["client-id"]
    assert [item["data"].password for item in vault.created] == ["client-secret"]
    assert auth_config["type"] == "dynamic_token"
    assert auth_config["credentials"] == [
        {"alias": "client", "credential_id": "cred-new-1", "owned_by_model": True}
    ]
    assert auth_config["token_request"]["body"] == {"secret": "{{ client.password }}"}
    assert auth_config["inject"]["body"] == {"auth": {"access_token": "{$.data.access_token}"}}
    assert "client-secret" not in str(auth_config)


def test_model_auth_credential_dynamic_token_omits_legacy_token_path_fields(monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    monkeypatch.setattr(model_routes, "_unique_model_auth_name", lambda *args, **kwargs: asyncio.sleep(0, result="Company GPT 认证"))

    credential_id, created_ids = run(
        model_routes._ensure_model_auth_credential(
            user_id="user-1",
            model_name="company-gpt",
            provider="openai",
            base_url="https://model.example/v1",
            api_key="base-api-key",
            requested=ModelAuthSaveRequest(
                type="dynamic_token",
                dynamic_token={
                    "credentials": [
                        {
                            "alias": "client",
                            "username": "client-id",
                            "password": "client-secret",
                            "domain": "tenant-a",
                        }
                    ],
                    "token_request": {
                        "method": "POST",
                        "url": "https://auth.example/token",
                        "headers": {"X-Client": "{{ client.username }}"},
                        "body_type": "json",
                        "body": {"secret": "{{ client.password }}"},
                    },
                    "inject": {
                        "headers": {"Authorization": "Bearer {$.data.access_token}"},
                        "query": {},
                        "body": {},
                    },
                },
            ),
        )
    )

    assert credential_id == "cred-new-1"
    assert created_ids == {"cred-new-1"}
    token_request = vault.created[0]["data"].model_auth["config"]["token_request"]
    assert "token_path" not in token_request
    assert "expires_in_path" not in token_request
    assert "expires_at_path" not in token_request


def test_update_model_replaces_static_header_and_cleans_removed_credentials(monkeypatch):
    vault = FakeVault()
    repo = FakeRepo(
        {
            "_id": "model-1",
            "name": "Company GPT",
            "provider": "openai",
            "base_url": "https://model.example/v1",
            "api_key": "api-key",
            "model_name": "company-gpt",
            "is_system": False,
            "user_id": "user-1",
            "auth_config": {
                "type": "static_headers",
                "credentials": [
                    {"alias": "header_authorization", "credential_id": "cred-old"},
                    {"alias": "header_x_remove", "credential_id": "cred-remove"},
                ],
                "headers": {
                    "Authorization": "{{ header_authorization.password }}",
                    "X-Remove": "{{ header_x_remove.password }}",
                },
                "query": {},
            },
        }
    )
    verify_calls = []

    async def fake_verify(*args, **kwargs):
        verify_calls.append({"args": args, "kwargs": kwargs})
        return True

    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    monkeypatch.setattr(model_routes, "get_repository", lambda name: repo)
    monkeypatch.setattr(model_routes, "_unique_model_auth_name", lambda *args, **kwargs: asyncio.sleep(0, result="Company GPT 认证"))
    monkeypatch.setattr(model_routes, "verify_model_connection", fake_verify)

    response = run(
        model_routes.update_model(
            "model-1",
            UpdateModelRequest(
                auth_config={
                    "type": "static_headers",
                    "static_headers": [{"name": "Authorization", "value": "Bearer replacement"}],
                }
            ),
            current_user=SimpleNamespace(id="user-1", role="user"),
        )
    )

    assert response.data == {"id": "model-1"}
    assert [item["data"].kind for item in vault.created] == ["model_auth"]
    assert set(vault.deleted) == {("user-1", "cred-old"), ("user-1", "cred-remove")}
    assert repo.update_doc["$set"]["auth_credential_id"] == "cred-new-1"
    assert repo.update_doc["$set"]["auth_credential_owned"] is True
    assert repo.update_doc["$set"]["auth_config"] is None
    saved_model_auth = vault.created[0]["data"].model_auth
    assert saved_model_auth["config"]["headers"] == {"Authorization": "{{ authorization }}"}
    assert saved_model_auth["variables"]["authorization"] == {
        "sensitive": True,
        "value": "Bearer replacement",
    }
    assert verify_calls[0]["kwargs"]["auth_credential_id"] == "cred-new-1"


def test_update_model_none_auth_converts_existing_api_key_to_model_auth(monkeypatch):
    vault = FakeVault()
    repo = FakeRepo(
        {
            "_id": "model-1",
            "name": "Company GPT",
            "provider": "openai",
            "base_url": "https://model.example/v1",
            "api_key": "api-key",
            "model_name": "company-gpt",
            "is_system": False,
            "user_id": "user-1",
            "auth_config": {
                "type": "static_headers",
                "credentials": [{"alias": "header_authorization", "credential_id": "cred-old"}],
                "headers": {"Authorization": "{{ header_authorization.password }}"},
                "query": {},
            },
        }
    )

    async def fake_verify(*args, **kwargs):
        return True

    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    monkeypatch.setattr(model_routes, "get_repository", lambda name: repo)
    monkeypatch.setattr(model_routes, "_unique_model_auth_name", lambda *args, **kwargs: asyncio.sleep(0, result="Company GPT 认证"))
    monkeypatch.setattr(model_routes, "verify_model_connection", fake_verify)

    run(
        model_routes.update_model(
            "model-1",
            UpdateModelRequest(auth_config={"type": "none"}),
            current_user=SimpleNamespace(id="user-1", role="user"),
        )
    )

    assert repo.update_doc["$set"]["auth_config"] is None
    assert repo.update_doc["$set"]["auth_credential_id"] == "cred-new-1"
    assert repo.update_doc["$set"]["auth_credential_owned"] is True
    assert vault.created[0]["data"].model_auth["config"]["headers"] == {
        "Authorization": "Bearer {{ api_key }}"
    }
    assert vault.deleted == [("user-1", "cred-old")]


def test_update_model_replaces_owned_model_auth_credential_and_keeps_shared_credentials(monkeypatch):
    vault = FakeVault()
    repo = FakeRepo(
        {
            "_id": "model-1",
            "name": "Company GPT",
            "provider": "openai",
            "base_url": "https://model.example/v1",
            "api_key": None,
            "model_name": "company-gpt",
            "is_system": False,
            "user_id": "user-1",
            "auth_credential_id": "cred-owned-old",
            "auth_credential_owned": True,
            "auth_config": None,
        }
    )

    async def fake_verify(*args, **kwargs):
        return True

    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    monkeypatch.setattr(model_routes, "get_repository", lambda name: repo)
    monkeypatch.setattr(model_routes, "verify_model_connection", fake_verify)

    run(
        model_routes.update_model(
            "model-1",
            UpdateModelRequest(auth_credential_id="cred-shared"),
            current_user=SimpleNamespace(id="user-1", role="user"),
        )
    )

    assert repo.update_doc["$set"]["auth_credential_id"] == "cred-shared"
    assert repo.update_doc["$set"]["auth_credential_owned"] is False
    assert vault.deleted == [("user-1", "cred-owned-old")]


def test_update_model_does_not_delete_existing_shared_model_auth_credential(monkeypatch):
    vault = FakeVault()
    repo = FakeRepo(
        {
            "_id": "model-1",
            "name": "Company GPT",
            "provider": "openai",
            "base_url": "https://model.example/v1",
            "api_key": None,
            "model_name": "company-gpt",
            "is_system": False,
            "user_id": "user-1",
            "auth_credential_id": "cred-shared-old",
            "auth_credential_owned": False,
            "auth_config": None,
        }
    )

    async def fake_verify(*args, **kwargs):
        return True

    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    monkeypatch.setattr(model_routes, "get_repository", lambda name: repo)
    monkeypatch.setattr(model_routes, "verify_model_connection", fake_verify)

    run(
        model_routes.update_model(
            "model-1",
            UpdateModelRequest(auth_credential_id="cred-shared-new"),
            current_user=SimpleNamespace(id="user-1", role="user"),
        )
    )

    assert repo.update_doc["$set"]["auth_credential_id"] == "cred-shared-new"
    assert repo.update_doc["$set"]["auth_credential_owned"] is False
    assert vault.deleted == []


def test_delete_model_cleans_only_owned_model_auth_credential(monkeypatch):
    vault = FakeVault()
    repo = FakeRepo(
        {
            "_id": "model-1",
            "name": "Company GPT",
            "provider": "openai",
            "base_url": "https://model.example/v1",
            "api_key": None,
            "model_name": "company-gpt",
            "is_system": False,
            "user_id": "user-1",
            "auth_credential_id": "cred-owned",
            "auth_credential_owned": True,
            "auth_config": None,
        }
    )

    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    monkeypatch.setattr(model_routes, "get_repository", lambda name: repo)

    run(
        model_routes.delete_model(
            "model-1",
            current_user=SimpleNamespace(id="user-1", role="user"),
        )
    )

    assert repo.delete_filter == {"_id": "model-1"}
    assert vault.deleted == [("user-1", "cred-owned")]


def test_delete_model_keeps_shared_model_auth_credential(monkeypatch):
    vault = FakeVault()
    repo = FakeRepo(
        {
            "_id": "model-1",
            "name": "Company GPT",
            "provider": "openai",
            "base_url": "https://model.example/v1",
            "api_key": None,
            "model_name": "company-gpt",
            "is_system": False,
            "user_id": "user-1",
            "auth_credential_id": "cred-shared",
            "auth_credential_owned": False,
            "auth_config": None,
        }
    )

    monkeypatch.setattr(model_routes, "get_vault", lambda: vault)
    monkeypatch.setattr(model_routes, "get_repository", lambda name: repo)

    run(
        model_routes.delete_model(
            "model-1",
            current_user=SimpleNamespace(id="user-1", role="user"),
        )
    )

    assert repo.delete_filter == {"_id": "model-1"}
    assert vault.deleted == []
