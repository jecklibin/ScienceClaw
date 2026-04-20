"""Storage abstraction 鈥?get_repository() is the only public API."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.storage.base import Repository

_repositories: dict[str, "Repository"] = {}
_initialized = False


def get_repository(collection_name: str) -> "Repository":
    """Return a cached Repository instance for the given collection."""
    if collection_name in _repositories:
        return _repositories[collection_name]

    from backend.config import settings

    if settings.storage_backend == "local":
        from backend.storage.local.repository import FileRepository
        repo = FileRepository(collection_name)
    else:
        from backend.storage.mongo.repository import MongoRepository
        repo = MongoRepository(collection_name)

    _repositories[collection_name] = repo
    return repo


async def init_storage() -> None:
    """Called once at startup. For local backend, loads all JSON into memory."""
    global _initialized
    if _initialized:
        return

    from backend.config import settings

    if settings.storage_backend == "local":
        from backend.storage.local.repository import FileRepository
        for name in (
            "users", "user_sessions", "sessions", "models",
            "skills", "blocked_tools", "task_settings", "session_events",
            "session_runtimes", "credentials", "rpa_mcp_tools",
        ):
            repo = FileRepository(name)
            await repo.load()
            _repositories[name] = repo
    else:
        from backend.mongodb.db import db
        await db.connect()

    _initialized = True


async def close_storage() -> None:
    """Called once at shutdown."""
    from backend.config import settings

    if settings.storage_backend == "mongo":
        from backend.mongodb.db import db
        await db.close()

    _repositories.clear()
    global _initialized
    _initialized = False

