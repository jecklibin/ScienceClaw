from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.rpa.mcp_models import RpaMcpToolDefinition
from backend.storage import get_repository


class RpaMcpToolRegistry:
    def __init__(self, repository=None) -> None:
        self._repo = repository or get_repository("rpa_mcp_tools")

    async def list_for_user(self, user_id: str) -> list[RpaMcpToolDefinition]:
        docs = await self._repo.find_many({"user_id": user_id}, sort=[("updated_at", -1)])
        return [self._coerce(doc) for doc in docs]

    async def list_enabled_for_user(self, user_id: str) -> list[RpaMcpToolDefinition]:
        docs = await self._repo.find_many({"user_id": user_id, "enabled": True}, sort=[("updated_at", -1)])
        return [self._coerce(doc) for doc in docs]

    async def get_owned(self, tool_id: str, user_id: str) -> RpaMcpToolDefinition | None:
        doc = await self._repo.find_one({"_id": tool_id, "user_id": user_id})
        return self._coerce(doc) if doc else None

    async def get_by_tool_name(self, tool_name: str, user_id: str) -> RpaMcpToolDefinition | None:
        doc = await self._repo.find_one({"tool_name": tool_name, "user_id": user_id, "enabled": True})
        return self._coerce(doc) if doc else None

    async def save(self, tool: RpaMcpToolDefinition) -> RpaMcpToolDefinition:
        payload = tool.model_dump(mode="python")
        payload["_id"] = payload.pop("id")
        payload["updated_at"] = datetime.now()
        await self._repo.update_one(
            {"_id": payload["_id"], "user_id": tool.user_id},
            {"$set": payload, "$setOnInsert": {"created_at": payload["updated_at"]}},
            upsert=True,
        )
        return self._coerce(payload)

    async def delete(self, tool_id: str, user_id: str) -> bool:
        deleted = await self._repo.delete_one({"_id": tool_id, "user_id": user_id})
        return bool(deleted)

    def _coerce(self, doc: dict[str, Any] | None) -> RpaMcpToolDefinition | None:
        if not doc:
            return None
        payload = dict(doc)
        payload["id"] = str(payload.pop("_id", payload.get("id", "")))
        return RpaMcpToolDefinition(**payload)
