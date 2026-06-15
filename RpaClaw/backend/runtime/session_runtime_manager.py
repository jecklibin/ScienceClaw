from __future__ import annotations

import logging
import re
import time

from backend.runtime.models import SessionRuntimeRecord
from backend.runtime.ownership import user_owns_runtime_session
from backend.runtime.provider import build_runtime_provider
from backend.runtime.repository import get_runtime_repository

_manager: SessionRuntimeManager | None = None
logger = logging.getLogger(__name__)

_SENSITIVE_LOG_VALUE_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_-]*(?:authorization|api[_-]?key|password|secret|token)[A-Za-z0-9_-]*)"
    r"\s*[:=]\s*[^,\s;]+"
)
_SENSITIVE_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class SessionRuntimeManager:
    def __init__(self, provider=None, repository=None, settings=None, owner_checker=None):
        if settings is None:
            from backend.config import settings as default_settings

            settings = default_settings
        if provider is None:
            provider = build_runtime_provider(settings)
        self.settings = settings
        self.provider = provider
        self.repository = repository or get_runtime_repository()
        self.owner_checker = owner_checker or self._default_owner_checker

    async def _default_owner_checker(self, runtime: SessionRuntimeRecord) -> bool:
        return await user_owns_runtime_session(runtime.session_id, runtime.user_id)

    def _compute_expires_at(self, now_ts: int) -> int:
        return now_ts + int(getattr(self.settings, "runtime_idle_ttl_seconds", 3600))

    @staticmethod
    def _is_duplicate_insert_error(exc: Exception) -> bool:
        return exc.__class__.__name__ == "DuplicateKeyError"

    @staticmethod
    def _sanitize_exception_for_log(
        exc: Exception,
        sensitive_values: list[str | None] | None = None,
    ) -> str:
        message = str(exc)
        message = _SENSITIVE_BEARER_RE.sub("Bearer <redacted>", message)
        message = _SENSITIVE_LOG_VALUE_RE.sub(
            lambda match: f"{match.group(1)}=<redacted>",
            message,
        )
        for value in sensitive_values or []:
            sensitive_value = (value or "").strip()
            if sensitive_value:
                message = message.replace(sensitive_value, "<redacted>")
        return message

    async def _refresh_existing_runtime(self, existing: dict, now_ts: int):
        refreshed = await self.provider.refresh_runtime(SessionRuntimeRecord(**existing))
        if refreshed.status == "missing":
            await self.repository.delete_one({"session_id": refreshed.session_id})
            return None
        refreshed.last_used_at = now_ts
        refreshed.expires_at = self._compute_expires_at(now_ts)
        refreshed_payload = refreshed.model_dump()
        await self.repository.update_one(
            {"session_id": refreshed.session_id},
            {
                "$set": refreshed_payload
            },
        )
        return refreshed

    async def _persist_refreshed_runtime(self, refreshed: SessionRuntimeRecord) -> None:
        await self.repository.update_one(
            {"session_id": refreshed.session_id},
            {
                "$set": refreshed.model_dump(),
            },
        )

    async def ensure_runtime(self, session_id: str, user_id: str):
        now_ts = int(time.time())
        existing = await self.repository.find_one({"session_id": session_id})
        if existing:
            refreshed = await self._refresh_existing_runtime(existing, now_ts)
            if refreshed is not None:
                return refreshed

        created = await self.provider.create_runtime(session_id, user_id)
        created_ts = max(int(created.created_at), now_ts)
        created.created_at = created_ts
        created.last_used_at = created_ts
        created.expires_at = self._compute_expires_at(created_ts)
        created_payload = created.model_dump()
        created_payload["_id"] = session_id
        try:
            await self.repository.insert_one(created_payload)
        except Exception as exc:
            if not self._is_duplicate_insert_error(exc):
                raise
            try:
                await self.provider.delete_runtime(created)
            except Exception as cleanup_exc:
                logger.warning(
                    "Failed to delete duplicate-created runtime for session "
                    f"{session_id}: "
                    f"{self._sanitize_exception_for_log(cleanup_exc, [created.runtime_token])}"
                )
            existing_after_duplicate = await self.repository.find_one({"session_id": session_id})
            if not existing_after_duplicate:
                raise
            refreshed = await self._refresh_existing_runtime(existing_after_duplicate, now_ts)
            if refreshed is not None:
                return refreshed
            return await self.ensure_runtime(session_id, user_id)
        return created

    async def get_runtime(self, session_id: str, refresh: bool = False) -> SessionRuntimeRecord | None:
        existing = await self.repository.find_one({"session_id": session_id})
        if not existing:
            return None

        record = SessionRuntimeRecord(**existing)
        if not refresh:
            return record

        refreshed = await self.provider.refresh_runtime(record)
        if refreshed.status == "missing":
            await self.repository.delete_one({"session_id": refreshed.session_id})
            return None
        await self._persist_refreshed_runtime(refreshed)
        return refreshed

    async def list_runtimes(
        self,
        user_id: str | None = None,
        refresh: bool = False,
    ) -> list[SessionRuntimeRecord]:
        query = {"user_id": user_id} if user_id else {}
        records = await self.repository.find_many(query)
        runtimes = [SessionRuntimeRecord(**item) for item in records]
        if not refresh:
            return runtimes

        refreshed_records: list[SessionRuntimeRecord] = []
        for runtime in runtimes:
            refreshed = await self.provider.refresh_runtime(runtime)
            if refreshed.status == "missing":
                await self.repository.delete_one({"session_id": refreshed.session_id})
                continue
            await self._persist_refreshed_runtime(refreshed)
            refreshed_records.append(refreshed)
        return refreshed_records

    async def destroy_runtime(self, session_id: str) -> bool:
        existing = await self.repository.find_one({"session_id": session_id})
        if not existing:
            return False

        record = SessionRuntimeRecord(**existing)
        await self.provider.delete_runtime(record)
        await self.repository.delete_one({"session_id": session_id})
        return True

    async def cleanup_orphans(self) -> int:
        records = await self.repository.find_many({})
        cleaned = 0
        for existing in records:
            record = SessionRuntimeRecord(**existing)
            if await self.owner_checker(record):
                continue
            try:
                await self.provider.delete_runtime(record)
            except Exception as exc:
                logger.warning(
                    "Failed to delete runtime for session "
                    f"{record.session_id}: "
                    f"{self._sanitize_exception_for_log(exc, [record.runtime_token])}"
                )
                continue
            await self.repository.delete_one({"session_id": record.session_id})
            cleaned += 1
        return cleaned

    async def cleanup_expired(self, now_ts: int | None = None) -> int:
        now_ts = now_ts or int(time.time())
        records = await self.repository.find_many({})
        cleaned = 0
        for existing in records:
            expires_at = existing.get("expires_at")
            if expires_at is None or expires_at > now_ts:
                continue
            record = SessionRuntimeRecord(**existing)
            try:
                await self.provider.delete_runtime(record)
            except Exception as exc:
                logger.warning(
                    "Failed to delete expired runtime for session "
                    f"{record.session_id}: "
                    f"{self._sanitize_exception_for_log(exc, [record.runtime_token])}"
                )
                continue
            await self.repository.delete_one({"session_id": record.session_id})
            cleaned += 1
        return cleaned


def get_session_runtime_manager(provider=None, repository=None, settings=None) -> SessionRuntimeManager:
    global _manager
    if _manager is None:
        _manager = SessionRuntimeManager(
            provider=provider,
            repository=repository,
            settings=settings,
        )
    return _manager


def reset_session_runtime_manager() -> None:
    global _manager
    _manager = None
