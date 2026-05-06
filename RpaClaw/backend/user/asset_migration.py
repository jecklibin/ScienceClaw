from __future__ import annotations

import time
from typing import Any, Dict

from loguru import logger

from backend.config import settings
from backend.storage import get_repository


LEGACY_LOCAL_ADMIN_ID = "local_admin"

_LOCAL_ADMIN_ASSET_COLLECTIONS = (
    "models",
    "credentials",
    "skills",
    "task_settings",
    "user_mcp_servers",
    "session_mcp_bindings",
    "rpa_mcp_tools",
    "rpa_mcp_preview_drafts",
    "blocked_tools",
)


async def migrate_local_admin_assets_to_bootstrap_admin() -> Dict[str, Any]:
    """Move legacy local-mode assets onto the real bootstrap admin user.

    `local_admin` is a development shortcut returned by local-mode auth. It is
    not a durable user record, so product data owned by it becomes invisible
    once the app runs with real login sessions.
    """

    admin_username = str(getattr(settings, "bootstrap_admin_username", "admin") or "admin").strip()
    users = get_repository("users")
    admin = await users.find_one({"username": admin_username})
    if not admin or not admin.get("_id"):
        logger.warning(
            "Skipping legacy local_admin asset migration because bootstrap admin user '{}' was not found.",
            admin_username,
        )
        return {
            "skipped": True,
            "reason": "bootstrap_admin_user_not_found",
            "target_user_id": "",
            "migrated_collections": {},
        }

    target_user_id = str(admin["_id"])
    if target_user_id == LEGACY_LOCAL_ADMIN_ID:
        logger.warning(
            "Skipping legacy local_admin asset migration because bootstrap admin already uses the legacy id."
        )
        return {
            "skipped": True,
            "reason": "bootstrap_admin_is_legacy_local_admin",
            "target_user_id": target_user_id,
            "migrated_collections": {},
        }

    now = int(time.time())
    migrated: Dict[str, int] = {}
    for collection in _LOCAL_ADMIN_ASSET_COLLECTIONS:
        repo = get_repository(collection)
        count = await repo.update_many(
            {"user_id": LEGACY_LOCAL_ADMIN_ID},
            {
                "$set": {
                    "user_id": target_user_id,
                    "updated_at": now,
                    "owner_migrated_from": LEGACY_LOCAL_ADMIN_ID,
                }
            },
        )
        if count:
            migrated[collection] = count

    if migrated:
        total = sum(migrated.values())
        collections = ", ".join(f"{name}={count}" for name, count in sorted(migrated.items()))
        logger.info(
            "Migrated {} legacy local_admin-owned assets to bootstrap admin user_id={} ({})",
            total,
            target_user_id,
            collections,
        )
    else:
        logger.info(
            "No legacy local_admin-owned assets found for migration to bootstrap admin user_id={}.",
            target_user_id,
        )

    return {
        "skipped": False,
        "reason": "migrated_legacy_local_admin_assets" if migrated else "no_legacy_local_admin_assets",
        "target_user_id": target_user_id,
        "migrated_collections": migrated,
    }
