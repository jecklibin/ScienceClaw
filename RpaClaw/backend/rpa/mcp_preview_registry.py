from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.storage import get_repository


class RpaMcpPreviewDraftRegistry:
    def __init__(self, repository=None) -> None:
        self._repo = repository or get_repository("rpa_mcp_preview_drafts")

    async def get(self, session_id: str, user_id: str, config_signature: str) -> dict[str, Any] | None:
        return await self._repo.find_one({
            "session_id": session_id,
            "user_id": user_id,
            "config_signature": config_signature,
        })

    async def save(self, session_id: str, user_id: str, config_signature: str, payload: dict[str, Any]) -> dict[str, Any]:
        doc = {
            "session_id": session_id,
            "user_id": user_id,
            "config_signature": config_signature,
            "updated_at": datetime.now(),
            **payload,
        }
        await self._repo.update_one(
            {
                "session_id": session_id,
                "user_id": user_id,
                "config_signature": config_signature,
            },
            {"$set": doc},
            upsert=True,
        )
        return doc
