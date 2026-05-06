import importlib

import pytest


MODELS = importlib.import_module("backend.models")
MIGRATION = importlib.import_module("backend.user.asset_migration")
DEPENDENCIES = importlib.import_module("backend.user.dependencies")
AUTH_ROUTE = importlib.import_module("backend.route.auth")


class MemoryRepo:
    def __init__(self, docs=None):
        self.docs = {str(doc["_id"]): dict(doc) for doc in (docs or [])}

    async def find_one(self, filter, projection=None):
        for doc in self.docs.values():
            if self._matches(doc, filter):
                return dict(doc)
        return None

    async def find_many(self, filter, projection=None, sort=None, skip=0, limit=0):
        rows = [dict(doc) for doc in self.docs.values() if self._matches(doc, filter)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda doc: doc.get(key), reverse=direction == -1)
        if skip:
            rows = rows[skip:]
        if limit:
            rows = rows[:limit]
        return rows

    async def update_many(self, filter, update):
        count = 0
        for key, doc in list(self.docs.items()):
            if self._matches(doc, filter):
                doc.update(update.get("$set", {}))
                self.docs[key] = doc
                count += 1
        return count

    async def update_one(self, filter, update, upsert=False):
        for key, doc in list(self.docs.items()):
            if self._matches(doc, filter):
                doc.update(update.get("$set", {}))
                self.docs[key] = doc
                return 1
        return 0

    async def delete_one(self, filter):
        return 0

    @staticmethod
    def _matches(doc, filter):
        for key, value in filter.items():
            if key == "$or":
                if not any(MemoryRepo._matches(doc, branch) for branch in value):
                    return False
                continue
            actual = doc.get(key)
            if isinstance(value, dict):
                if "$nin" in value and actual in value["$nin"]:
                    return False
                continue
            if actual != value:
                return False
        return True


class FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


@pytest.mark.anyio
async def test_migrates_legacy_local_admin_assets_to_bootstrap_admin(monkeypatch):
    repos = {
        "users": MemoryRepo(
            [
                {
                    "_id": "admin-uuid",
                    "username": "admin",
                    "role": "admin",
                    "is_active": True,
                }
            ]
        ),
        "models": MemoryRepo([{"_id": "model-1", "user_id": "local_admin"}]),
        "credentials": MemoryRepo([{"_id": "cred-1", "user_id": "local_admin"}]),
        "skills": MemoryRepo([{"_id": "skill-1", "user_id": "local_admin"}]),
        "task_settings": MemoryRepo([{"_id": "settings-1", "user_id": "local_admin"}]),
        "user_mcp_servers": MemoryRepo([{"_id": "mcp-1", "user_id": "local_admin"}]),
        "session_mcp_bindings": MemoryRepo([{"_id": "binding-1", "user_id": "local_admin"}]),
        "rpa_mcp_tools": MemoryRepo([{"_id": "tool-1", "user_id": "local_admin"}]),
        "rpa_mcp_preview_drafts": MemoryRepo([{"_id": "draft-1", "user_id": "local_admin"}]),
        "blocked_tools": MemoryRepo([{"_id": "blocked-1", "user_id": "local_admin"}]),
    }

    monkeypatch.setattr(MIGRATION, "get_repository", lambda name: repos[name])
    monkeypatch.setattr(MIGRATION.settings, "storage_backend", "local")
    monkeypatch.setattr(MIGRATION.settings, "auth_provider", "local")

    report = await MIGRATION.migrate_local_admin_assets_to_bootstrap_admin()

    assert report["target_user_id"] == "admin-uuid"
    assert report["migrated_collections"]["models"] == 1
    assert repos["models"].docs["model-1"]["user_id"] == "admin-uuid"
    assert repos["credentials"].docs["cred-1"]["user_id"] == "admin-uuid"
    assert repos["skills"].docs["skill-1"]["user_id"] == "admin-uuid"


@pytest.mark.anyio
async def test_migrates_legacy_asset_migration_in_no_auth_local_mode(monkeypatch):
    repos = {
        "users": MemoryRepo([{"_id": "admin-uuid", "username": "admin"}]),
        "models": MemoryRepo([{"_id": "model-1", "user_id": "local_admin"}]),
        "credentials": MemoryRepo([]),
        "skills": MemoryRepo([]),
        "task_settings": MemoryRepo([]),
        "user_mcp_servers": MemoryRepo([]),
        "session_mcp_bindings": MemoryRepo([]),
        "rpa_mcp_tools": MemoryRepo([]),
        "rpa_mcp_preview_drafts": MemoryRepo([]),
        "blocked_tools": MemoryRepo([]),
    }

    monkeypatch.setattr(MIGRATION, "get_repository", lambda name: repos[name])
    monkeypatch.setattr(MIGRATION.settings, "storage_backend", "local")
    monkeypatch.setattr(MIGRATION.settings, "auth_provider", "none")

    report = await MIGRATION.migrate_local_admin_assets_to_bootstrap_admin()

    assert report["skipped"] is False
    assert report["target_user_id"] == "admin-uuid"
    assert report["migrated_collections"]["models"] == 1
    assert repos["models"].docs["model-1"]["user_id"] == "admin-uuid"


@pytest.mark.anyio
async def test_resolve_default_model_config_reports_user_model_resolution(monkeypatch):
    repo = MemoryRepo(
        [
            {
                "_id": "system-default",
                "is_system": True,
                "is_active": True,
                "api_key": "sk-system",
                "model_name": "system-model",
                "base_url": "https://system.example/v1",
                "updated_at": 20,
                "created_at": 20,
            },
            {
                "_id": "user-model",
                "user_id": "user-1",
                "is_system": False,
                "is_active": True,
                "api_key": "sk-user",
                "model_name": "user-model",
                "base_url": "https://user.example/v1",
                "updated_at": 10,
                "created_at": 10,
            },
        ]
    )

    monkeypatch.setattr(MODELS, "get_repository", lambda name: repo)

    config = await MODELS.resolve_default_model_config("user-1")

    assert config["id"] == "user-model"
    assert config["resolution_reason"] == "user_active_model"
    assert config["requested_user_id"] == "user-1"
    assert config["selected_owner"] == "user"


@pytest.mark.anyio
async def test_resolve_default_model_config_without_user_ignores_user_models(monkeypatch):
    repo = MemoryRepo(
        [
            {
                "_id": "user-model",
                "user_id": "user-1",
                "is_system": False,
                "is_active": True,
                "api_key": "sk-user",
                "model_name": "user-model",
                "base_url": "https://user.example/v1",
                "updated_at": 30,
                "created_at": 30,
            },
            {
                "_id": "system-default",
                "is_system": True,
                "is_active": True,
                "api_key": "sk-system",
                "model_name": "system-model",
                "base_url": "https://system.example/v1",
                "updated_at": 10,
                "created_at": 10,
            },
        ]
    )

    monkeypatch.setattr(MODELS, "get_repository", lambda name: repo)

    config = await MODELS.resolve_default_model_config()

    assert config["id"] == "system-default"
    assert config["resolution_reason"] == "system_fallback"
    assert config["requested_user_id"] is None
    assert config["selected_owner"] == "system"


@pytest.mark.anyio
async def test_resolve_default_model_config_without_user_returns_none_for_user_only_models(monkeypatch):
    repo = MemoryRepo(
        [
            {
                "_id": "user-model",
                "user_id": "user-1",
                "is_system": False,
                "is_active": True,
                "api_key": "sk-user",
                "model_name": "user-model",
                "base_url": "https://user.example/v1",
                "updated_at": 30,
                "created_at": 30,
            },
        ]
    )

    monkeypatch.setattr(MODELS, "get_repository", lambda name: repo)

    assert await MODELS.resolve_default_model_config() is None


@pytest.mark.anyio
async def test_local_storage_with_local_auth_uses_session_user(monkeypatch):
    repo = MemoryRepo(
        [
            {
                "_id": "session-1",
                "user_id": "admin-uuid",
                "username": "admin",
                "role": "admin",
                "expires_at": 9999999999,
            }
        ]
    )

    monkeypatch.setattr(DEPENDENCIES.settings, "storage_backend", "local")
    monkeypatch.setattr(DEPENDENCIES.settings, "auth_provider", "local")
    monkeypatch.setattr(DEPENDENCIES.settings, "session_cookie", "sid")
    monkeypatch.setattr(DEPENDENCIES, "get_repository", lambda name: repo)

    user = await DEPENDENCIES.get_current_user(FakeRequest(cookies={"sid": "session-1"}))

    assert user is not None
    assert user.id == "admin-uuid"


@pytest.mark.anyio
async def test_local_storage_with_auth_disabled_uses_bootstrap_admin_identity(monkeypatch):
    repo = MemoryRepo(
        [
            {
                "_id": "admin-uuid",
                "username": "admin",
                "role": "admin",
                "is_active": True,
            }
        ]
    )

    monkeypatch.setattr(DEPENDENCIES.settings, "storage_backend", "local")
    monkeypatch.setattr(DEPENDENCIES.settings, "auth_provider", "none")
    monkeypatch.setattr(DEPENDENCIES.settings, "bootstrap_admin_username", "admin")
    monkeypatch.setattr(DEPENDENCIES, "get_repository", lambda name: repo)

    user = await DEPENDENCIES.get_current_user(FakeRequest())

    assert user is not None
    assert user.id == "admin-uuid"
    assert user.username == "admin"


@pytest.mark.anyio
async def test_auth_status_no_auth_reports_bootstrap_admin_identity(monkeypatch):
    current_user = DEPENDENCIES.User(id="admin-uuid", username="admin", role="admin")

    monkeypatch.setattr(AUTH_ROUTE.settings, "storage_backend", "local")
    monkeypatch.setattr(AUTH_ROUTE.settings, "auth_provider", "none")

    response = await AUTH_ROUTE.get_auth_status(current_user=current_user)

    assert response.data["authenticated"] is True
    assert response.data["auth_provider"] == "none"
    assert response.data["user"]["id"] == "admin-uuid"


@pytest.mark.anyio
async def test_auth_status_keeps_local_auth_provider_for_local_storage(monkeypatch):
    monkeypatch.setattr(AUTH_ROUTE.settings, "storage_backend", "local")
    monkeypatch.setattr(AUTH_ROUTE.settings, "auth_provider", "local")

    response = await AUTH_ROUTE.get_auth_status(current_user=None)

    assert response.data["authenticated"] is False
    assert response.data["auth_provider"] == "local"
