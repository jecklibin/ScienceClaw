from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from backend.runtime.models import SessionRuntimeRecord


class SharedRuntimeProvider:
    def __init__(self, settings):
        self.settings = settings

    def _resolve_rest_base_url(self) -> str:
        explicit = (os.environ.get("SHARED_SANDBOX_REST_URL") or "").strip()
        if explicit:
            return explicit.rstrip("/")

        mcp_url = (getattr(self.settings, "sandbox_mcp_url", "") or "").strip()
        if mcp_url:
            parsed = urlparse(mcp_url)
            derived_path = parsed.path[:-4] if parsed.path.endswith("/mcp") else parsed.path
            return parsed._replace(path=derived_path.rstrip("/"), params="", query="", fragment="").geturl().rstrip("/")

        fallback = getattr(self.settings, "shared_sandbox_rest_url", "http://sandbox:8080")
        return fallback.rstrip("/")

    async def create_runtime(self, session_id: str, user_id: str) -> SessionRuntimeRecord:
        rest_base_url = self._resolve_rest_base_url()
        now = int(time.time())
        return SessionRuntimeRecord(
            session_id=session_id,
            user_id=user_id,
            namespace=getattr(self.settings, "k8s_namespace", "local"),
            pod_name="shared-sandbox",
            service_name="shared-sandbox",
            rest_base_url=rest_base_url.rstrip("/"),
            status="ready",
            created_at=now,
            last_used_at=now,
        )

    async def delete_runtime(self, runtime_record) -> None:
        return None

    async def refresh_runtime(self, runtime_record):
        return runtime_record
