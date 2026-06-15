from __future__ import annotations

from typing import Any, Callable

import httpx

from backend.runtime.models import SessionRuntimeRecord


SENSITIVE_DETAIL_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "runtime_token",
    "secret",
    "token",
}


class RuntimeAdapterClientError(RuntimeError):
    """Sanitized adapter HTTP error for Host-side diagnostics."""

    def __init__(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        detail: Any,
    ):
        self.method = method
        self.path = path
        self.status_code = status_code
        self.detail = detail
        super().__init__(
            f"Runtime adapter request failed: method={method}, "
            f"path={path}, status_code={status_code}, detail={detail}"
        )


def _internal_adapter_http_client(**kwargs):
    return httpx.AsyncClient(trust_env=False, **kwargs)


class RuntimeAdapterClient:
    """Semantic HTTP client for a session runtime adapter."""

    def __init__(
        self,
        runtime: SessionRuntimeRecord,
        *,
        http_client_factory: Callable[..., Any] | None = None,
        timeout: float = 30.0,
    ):
        self.runtime = runtime
        self.timeout = timeout
        self.http_client_factory = http_client_factory or _internal_adapter_http_client

    def _base_url(self) -> str:
        return (self.runtime.route_base_url or self.runtime.rest_base_url).rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self._base_url()}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        token = (self.runtime.runtime_token or "").strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _sanitize_error_detail(self, value: Any, *, key: str = "") -> Any:
        key_lower = key.lower()
        if key_lower and any(sensitive in key_lower for sensitive in SENSITIVE_DETAIL_KEYS):
            return "<redacted>"
        token = (self.runtime.runtime_token or "").strip()
        if isinstance(value, str):
            if token:
                return value.replace(token, "<redacted>")
            return value
        if isinstance(value, dict):
            return {
                item_key: self._sanitize_error_detail(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [self._sanitize_error_detail(item) for item in value]
        return value

    async def _request_json(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        async with self.http_client_factory(timeout=self.timeout) as client:
            response = await client.request(
                method,
                self._url(path),
                headers=self._headers(),
                **kwargs,
            )
        self._raise_for_status(response, method=method, path=path)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _request_bytes(self, method: str, path: str, **kwargs) -> bytes:
        async with self.http_client_factory(timeout=self.timeout) as client:
            response = await client.request(
                method,
                self._url(path),
                headers=self._headers(),
                **kwargs,
            )
        self._raise_for_status(response, method=method, path=path)
        return bytes(response.content)

    def _raise_for_status(self, response, *, method: str, path: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail: Any
            try:
                detail = exc.response.json()
            except Exception:
                detail = getattr(exc.response, "text", "") or str(exc.response)
            raise RuntimeAdapterClientError(
                method=method,
                path=path,
                status_code=getattr(exc.response, "status_code", 0),
                detail=self._sanitize_error_detail(detail),
            ) from exc

    async def health(self) -> dict[str, Any]:
        return await self._request_json("GET", "/health")

    async def browser_info(self) -> dict[str, Any]:
        return await self._request_json("GET", "/v1/browser/info")

    async def start_recording(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request_json("POST", "/rpa/recording/start", json=payload or {})

    async def stop_recording(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request_json("POST", "/rpa/recording/stop", json=payload or {})

    async def get_events(self, *, cursor: str | int | None = None) -> dict[str, Any]:
        params = {"cursor": str(cursor)} if cursor is not None else None
        return await self._request_json("GET", "/rpa/events", params=params)

    async def emit_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("POST", "/rpa/events/emit", json=payload)

    async def get_snapshot(self) -> dict[str, Any]:
        return await self._request_json("GET", "/rpa/snapshot")

    async def emit_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("POST", "/rpa/snapshot/emit", json=payload)

    async def execute_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("POST", "/rpa/execute-step", json=payload)

    async def run_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("POST", "/rpa/run-skill", json=payload)

    async def list_downloads(self) -> dict[str, Any]:
        return await self._request_json("GET", "/rpa/downloads")

    async def list_files(self, path: str) -> dict[str, Any]:
        return await self._request_json("GET", "/files/list", params={"path": path})

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            "/files/write",
            json={"path": path, "content": content},
        )

    async def write_file_base64(self, path: str, content_base64: str) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            "/files/write",
            json={"path": path, "content_base64": content_base64},
        )

    async def download_file(self, path: str) -> bytes:
        return await self._request_bytes("GET", "/files/download", params={"path": path})
