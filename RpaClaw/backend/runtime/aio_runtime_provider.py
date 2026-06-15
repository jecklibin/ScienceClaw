from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from backend.runtime.adapter_client import RuntimeAdapterClient, RuntimeAdapterClientError
from backend.runtime.models import SessionRuntimeRecord


SENSITIVE_PAYLOAD_KEYWORDS = (
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "password",
    "secret",
    "token",
)

AIO_READY_STATUSES = {"ok", "ready", "running"}
AIO_CREATING_STATUSES = {"creating", "pending", "provisioning", "starting"}
AIO_MISSING_STATUSES = {"deleted", "deleting", "error", "failed", "missing", "stopped", "terminated"}
SUPPORTED_RUNTIME_ADAPTER_CONTRACT_VERSION = "v1"


def _extract_adapter_file_policy(health: dict[str, Any]) -> dict[str, Any] | None:
    file_policy = ((health or {}).get("config") or {}).get("file_policy")
    if not isinstance(file_policy, dict):
        return None
    extracted: dict[str, Any] = {}
    for key in ("max_inline_file_write_bytes", "max_file_download_bytes"):
        value = file_policy.get(key)
        if isinstance(value, int) and value >= 0:
            extracted[key] = value
    oversized_hash_status = file_policy.get("oversized_hash_status")
    if isinstance(oversized_hash_status, str) and oversized_hash_status:
        extracted["oversized_hash_status"] = oversized_hash_status
    return extracted or None


class AioRuntimeProviderError(RuntimeError):
    """Non-sensitive provider acquisition error for AIO runtime lifecycle."""

    def __init__(self, operation: str, reason: str):
        self.operation = operation
        self.reason = reason
        super().__init__(f"AIO runtime {operation} failed: {reason}")


def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _rewrite_absolute_url_base(value: str, base_url: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parsed_value = urlparse(value)
    if not parsed_value.scheme or not parsed_value.netloc:
        return f"{base_url.rstrip('/')}/{value.lstrip('/')}"
    parsed_base = urlparse(base_url)
    return parsed_value._replace(
        scheme=parsed_base.scheme,
        netloc=parsed_base.netloc,
    ).geturl()


def _native_aio_http_client(**kwargs):
    return httpx.AsyncClient(trust_env=False, **kwargs)


async def _adapter_health_contract_diagnostic(
    adapter_client_cls,
    runtime_record,
) -> dict[str, Any]:
    diagnostic = {
        "supported_adapter_contract_version": SUPPORTED_RUNTIME_ADAPTER_CONTRACT_VERSION,
    }
    try:
        health = await adapter_client_cls(runtime_record).health()
    except RuntimeAdapterClientError as exc:
        if exc.status_code in {401, 403}:
            reason = "adapter_health_unauthorized"
        elif exc.status_code == 404:
            reason = "adapter_health_route_not_found"
        else:
            reason = "adapter_health_unavailable"
        return {
            **diagnostic,
            "adapter_health_ok": False,
            "runtime_status_reason": reason,
            "adapter_health_status_code": exc.status_code,
            "adapter_health_error_method": exc.method,
            "adapter_health_error_path": exc.path,
        }
    except Exception:
        return {
            **diagnostic,
            "adapter_health_ok": False,
            "runtime_status_reason": "adapter_health_unavailable",
        }
    health_status = str((health or {}).get("status") or "").strip().lower()
    contract_version = str((health or {}).get("contract_version") or "").strip()
    adapter_version = str(((health or {}).get("config") or {}).get("adapter_version") or "").strip()
    adapter_file_policy = _extract_adapter_file_policy(health or {})
    if health_status:
        diagnostic["adapter_health_status"] = health_status
    if contract_version:
        diagnostic["adapter_contract_version"] = contract_version
    if adapter_version:
        diagnostic["adapter_version"] = adapter_version
    if adapter_file_policy:
        diagnostic["adapter_file_policy"] = adapter_file_policy
    if health_status not in {"ok", "ready"}:
        return {
            **diagnostic,
            "adapter_health_ok": False,
            "runtime_status_reason": "adapter_health_not_ready",
        }
    if contract_version != SUPPORTED_RUNTIME_ADAPTER_CONTRACT_VERSION:
        return {
            **diagnostic,
            "adapter_health_ok": False,
            "runtime_status_reason": "adapter_contract_mismatch",
        }
    return {
        **diagnostic,
        "adapter_health_ok": True,
    }


async def _apply_adapter_health_contract(
    adapter_client_cls,
    runtime_record,
) -> bool:
    diagnostic = await _adapter_health_contract_diagnostic(adapter_client_cls, runtime_record)
    metadata = dict(getattr(runtime_record, "metadata", None) or {})
    for key in (
        "adapter_health_status",
        "adapter_contract_version",
        "adapter_version",
        "supported_adapter_contract_version",
        "adapter_health_status_code",
        "adapter_health_error_method",
        "adapter_health_error_path",
        "adapter_file_policy",
    ):
        if key in diagnostic:
            metadata[key] = diagnostic[key]
    if diagnostic["adapter_health_ok"]:
        metadata.pop("runtime_status_reason", None)
        runtime_record.metadata = metadata
        return True
    metadata["runtime_status_reason"] = diagnostic["runtime_status_reason"]
    runtime_record.metadata = metadata
    return False


def _mark_runtime_missing(runtime_record, reason: str):
    runtime_record.status = "missing"
    metadata = dict(getattr(runtime_record, "metadata", None) or {})
    metadata["runtime_status_reason"] = reason
    runtime_record.metadata = metadata
    return runtime_record


class AioRuntimeProvider:
    """Adapter for a pre-provisioned local AIO sandbox.

    This provider intentionally does not create or destroy the sandbox. It lets
    local development exercise the Host -> Adapter runtime contract while the
    real intranet AIO create/route/delete APIs remain a swappable provider
    concern.
    """

    def __init__(self, settings, *, adapter_client_cls=RuntimeAdapterClient):
        self.settings = settings
        self.adapter_client_cls = adapter_client_cls

    def _sandbox_id(self) -> str:
        configured = (getattr(self.settings, "aio_runtime_sandbox_id", "") or "").strip()
        return configured or "local-aio-sandbox"

    def _route_base_url(self) -> str:
        configured = (getattr(self.settings, "aio_runtime_route_base_url", "") or "").strip()
        fallback = getattr(self.settings, "sandbox_base_url", "http://sandbox:8080")
        return (configured or fallback).rstrip("/")

    def _browser_view_url(self) -> str | None:
        configured = (getattr(self.settings, "aio_runtime_browser_view_url", "") or "").strip()
        return configured.rstrip("/") if configured else None

    def _runtime_token(self) -> str | None:
        configured = (getattr(self.settings, "aio_runtime_token", "") or "").strip()
        return configured or None

    async def create_runtime(self, session_id: str, user_id: str) -> SessionRuntimeRecord:
        now = int(time.time())
        sandbox_id = self._sandbox_id()
        route_base_url = self._route_base_url()
        return SessionRuntimeRecord(
            session_id=session_id,
            user_id=user_id,
            namespace="aio-local",
            pod_name=sandbox_id,
            service_name=sandbox_id,
            rest_base_url=route_base_url,
            status="ready",
            sandbox_id=sandbox_id,
            route_base_url=route_base_url,
            browser_view_url=self._browser_view_url(),
            runtime_token=self._runtime_token(),
            created_at=now,
            last_used_at=now,
        )

    async def delete_runtime(self, runtime_record) -> None:
        return None

    async def refresh_runtime(self, runtime_record):
        if not getattr(runtime_record, "sandbox_id", None):
            runtime_record.sandbox_id = self._sandbox_id()
        if not getattr(runtime_record, "route_base_url", None):
            runtime_record.route_base_url = runtime_record.rest_base_url
        if not getattr(runtime_record, "runtime_token", None):
            runtime_record.runtime_token = self._runtime_token()
        if not await _apply_adapter_health_contract(self.adapter_client_cls, runtime_record):
            runtime_record.status = "missing"
            return runtime_record
        runtime_record.status = "ready"
        return runtime_record


class AioNativeRuntimeProvider:
    """Provider for a fixed native AIO sandbox without Runtime Adapter.

    Local validation can point Host Backend at an already-running
    agent-infra/sandbox instance. The Host still owns recorder injection,
    navigation/download listeners, accepted trace construction, and Skill
    compilation; AIO only supplies the browser/CDP/VNC execution surface.
    """

    def __init__(
        self,
        settings,
        *,
        http_client_factory: Callable[..., Any] | None = None,
        timeout: float = 10.0,
    ):
        self.settings = settings
        self.http_client_factory = http_client_factory or _native_aio_http_client
        self.timeout = timeout

    def _sandbox_id(self) -> str:
        configured = (getattr(self.settings, "aio_runtime_sandbox_id", "") or "").strip()
        return configured or "local-aio-native-sandbox"

    def _lifecycle_enabled(self) -> bool:
        return bool(self._api_base_url() and self._template_id())

    def _api_base_url(self) -> str:
        configured = (getattr(self.settings, "aio_native_api_base_url", "") or "").strip()
        return configured.rstrip("/")

    def _template_id(self) -> str:
        return (getattr(self.settings, "aio_native_template_id", "") or "").strip()

    def _refresh_duration_seconds(self) -> int:
        return int(getattr(self.settings, "aio_native_refresh_duration_seconds", 300) or 300)

    def _base_url(self) -> str:
        configured = (getattr(self.settings, "aio_native_base_url", "") or "").strip()
        fallback = (
            getattr(self.settings, "aio_runtime_route_base_url", "")
            or getattr(self.settings, "sandbox_base_url", "http://sandbox:8080")
        )
        return (configured or fallback).rstrip("/")

    def _sandbox_base_url(self, sandbox_id: str) -> str:
        base_url = self._base_url()
        try:
            return base_url.format(sandbox_id=sandbox_id).rstrip("/")
        except Exception:
            return base_url.rstrip("/")

    def _path(self, value: str, **params) -> str:
        path = (value or "").format(**params).strip() or "/"
        return "/" + path.lstrip("/")

    def _api_url(self, path: str) -> str:
        return f"{self._api_base_url()}{self._path(path)}"

    def _create_path(self) -> str:
        return getattr(
            self.settings,
            "aio_native_create_path",
            "/api/livefunction/sandboxes",
        )

    def _status_path(self, sandbox_id: str) -> str:
        template = getattr(
            self.settings,
            "aio_native_status_path_template",
            "/api/livefunction/sandboxes/{sandbox_id}",
        )
        return self._path(template, sandbox_id=sandbox_id)

    def _delete_path(self, sandbox_id: str) -> str:
        template = getattr(
            self.settings,
            "aio_native_delete_path_template",
            "/api/livefunction/sandboxes/{sandbox_id}",
        )
        return self._path(template, sandbox_id=sandbox_id)

    def _refresh_path(self, sandbox_id: str) -> str:
        template = getattr(
            self.settings,
            "aio_native_refresh_path_template",
            "/api/livefunction/sandboxes/refresh/{sandbox_id}",
        )
        return self._path(template, sandbox_id=sandbox_id)

    def _headers(self) -> dict[str, str]:
        token = (getattr(self.settings, "aio_native_api_token", "") or "").strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    async def _request_json(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        async with self.http_client_factory(timeout=self.timeout) as client:
            response = await client.request(
                method,
                self._api_url(path),
                headers=self._headers(),
                **kwargs,
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {"data": payload}

    def _configured_browser_view_url(self) -> str | None:
        configured = (getattr(self.settings, "aio_runtime_browser_view_url", "") or "").strip()
        return configured.rstrip("/") if configured else None

    async def _browser_info(self) -> dict[str, Any]:
        async with self.http_client_factory(timeout=self.timeout) as client:
            response = await client.get(f"{self._base_url()}/v1/browser/info")
        response.raise_for_status()
        payload = response.json()
        return _payload_data(payload if isinstance(payload, dict) else {"data": payload})

    def _browser_view_url_from_info(self, browser_info: dict[str, Any]) -> str | None:
        configured = self._configured_browser_view_url()
        if configured:
            return configured
        vnc_url = str(browser_info.get("vnc_url") or "").strip()
        if not vnc_url:
            return None
        return _rewrite_absolute_url_base(vnc_url, self._base_url())

    def _native_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _payload_data(payload)

    def _normalize_lifecycle_status(self, status: Any) -> str:
        value = str(status or "").strip().lower()
        if value in {"running", "ready", "ok"}:
            return "ready"
        if value in {"creating", "pending", "provisioning", "starting"}:
            return "creating"
        if value in {"stopped", "error", "failed", "missing", "deleted", "terminated"}:
            return "missing"
        return value or "creating"

    def _record_from_lifecycle_payload(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        user_id: str,
        existing: SessionRuntimeRecord | None = None,
    ) -> SessionRuntimeRecord:
        data = self._native_payload(payload)
        sandbox_id = str(
            data.get("sandboxId")
            or data.get("sandbox_id")
            or data.get("id")
            or (existing.sandbox_id if existing else "")
        ).strip()
        if not sandbox_id:
            raise RuntimeError("AIO native sandbox response missing sandboxId")
        base_url = self._sandbox_base_url(sandbox_id)
        now = int(time.time())
        status = self._normalize_lifecycle_status(data.get("status") or (existing.status if existing else ""))
        metadata = dict(getattr(existing, "metadata", None) or {})
        metadata["runtime_contract"] = "aio_native"
        field_map = {
            "template_id": data.get("templateId") or data.get("template_id"),
            "cpu": data.get("cpu"),
            "memory": data.get("memory"),
            "timeout": data.get("timeout"),
            "aio_status": data.get("status"),
            "start_at": data.get("startAt") or data.get("start_at"),
            "end_at": data.get("endAt") or data.get("end_at"),
        }
        for key, value in field_map.items():
            if value not in (None, ""):
                metadata[key] = value
        if status == "missing":
            aio_status = str(data.get("status") or "missing").strip().lower() or "missing"
            metadata["runtime_status_reason"] = f"aio_native_sandbox_{aio_status}"
        else:
            metadata.pop("runtime_status_reason", None)
        return SessionRuntimeRecord(
            session_id=session_id,
            user_id=user_id,
            namespace="aio-native",
            pod_name=sandbox_id,
            service_name=sandbox_id,
            rest_base_url=base_url,
            status=status,
            sandbox_id=sandbox_id,
            route_base_url=base_url,
            browser_view_url=(
                self._configured_browser_view_url()
                or (existing.browser_view_url if existing else None)
            ),
            created_at=existing.created_at if existing else now,
            last_used_at=now,
            expires_at=getattr(existing, "expires_at", None) if existing else None,
            metadata=metadata,
        )

    async def create_runtime(self, session_id: str, user_id: str) -> SessionRuntimeRecord:
        if self._lifecycle_enabled():
            try:
                payload = await self._request_json(
                    "POST",
                    self._create_path(),
                    json={"templateId": self._template_id()},
                )
            except Exception:
                raise AioRuntimeProviderError("create", "aio_native_create_unavailable") from None
            try:
                return self._record_from_lifecycle_payload(
                    payload,
                    session_id=session_id,
                    user_id=user_id,
                )
            except RuntimeError:
                raise AioRuntimeProviderError("create", "aio_native_create_response_invalid") from None
        now = int(time.time())
        sandbox_id = self._sandbox_id()
        base_url = self._base_url()
        return SessionRuntimeRecord(
            session_id=session_id,
            user_id=user_id,
            namespace="aio-native",
            pod_name=sandbox_id,
            service_name=sandbox_id,
            rest_base_url=base_url,
            status="ready",
            sandbox_id=sandbox_id,
            route_base_url=base_url,
            browser_view_url=self._configured_browser_view_url(),
            created_at=now,
            last_used_at=now,
            metadata={"runtime_contract": "aio_native"},
        )

    async def delete_runtime(self, runtime_record) -> None:
        if self._lifecycle_enabled():
            sandbox_id = getattr(runtime_record, "sandbox_id", None)
            if not sandbox_id:
                return None
            try:
                await self._request_json("DELETE", self._delete_path(str(sandbox_id)))
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    return None
                raise AioRuntimeProviderError("delete", "aio_native_delete_unavailable") from None
            except Exception:
                raise AioRuntimeProviderError("delete", "aio_native_delete_unavailable") from None
        return None

    async def keepalive_runtime(self, runtime_record):
        if not self._lifecycle_enabled():
            return runtime_record
        sandbox_id = getattr(runtime_record, "sandbox_id", None)
        if not sandbox_id:
            return _mark_runtime_missing(runtime_record, "aio_native_sandbox_id_missing")
        try:
            await self._request_json(
                "POST",
                self._refresh_path(str(sandbox_id)),
                json={"duration": self._refresh_duration_seconds()},
            )
        except Exception:
            metadata = dict(getattr(runtime_record, "metadata", None) or {})
            metadata["runtime_status_reason"] = "aio_native_refresh_unavailable"
            runtime_record.metadata = metadata
            return runtime_record
        return runtime_record

    async def refresh_runtime(self, runtime_record):
        if self._lifecycle_enabled():
            sandbox_id = getattr(runtime_record, "sandbox_id", None)
            if not sandbox_id:
                return _mark_runtime_missing(runtime_record, "aio_native_sandbox_id_missing")
            try:
                payload = await self._request_json("GET", self._status_path(str(sandbox_id)))
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    return _mark_runtime_missing(runtime_record, "aio_native_sandbox_not_found")
                return _mark_runtime_missing(runtime_record, "aio_native_status_unavailable")
            except Exception:
                return _mark_runtime_missing(runtime_record, "aio_native_status_unavailable")
            return self._record_from_lifecycle_payload(
                payload,
                session_id=runtime_record.session_id,
                user_id=runtime_record.user_id,
                existing=runtime_record,
            )
        if not getattr(runtime_record, "sandbox_id", None):
            runtime_record.sandbox_id = self._sandbox_id()
        if not getattr(runtime_record, "route_base_url", None):
            runtime_record.route_base_url = runtime_record.rest_base_url or self._base_url()
        metadata = dict(getattr(runtime_record, "metadata", None) or {})
        metadata["runtime_contract"] = "aio_native"
        try:
            browser_info = await self._browser_info()
        except Exception:
            metadata["runtime_status_reason"] = "aio_native_browser_info_unavailable"
            metadata["browser_info_ok"] = False
            runtime_record.metadata = metadata
            runtime_record.status = "missing"
            return runtime_record

        browser_view_url = self._browser_view_url_from_info(browser_info)
        if browser_view_url:
            runtime_record.browser_view_url = browser_view_url
        metadata["browser_info_ok"] = True
        metadata["cdp_url_available"] = bool(str(browser_info.get("cdp_url") or "").strip())
        metadata.pop("runtime_status_reason", None)
        runtime_record.metadata = metadata
        runtime_record.status = "ready" if metadata["cdp_url_available"] else "missing"
        if runtime_record.status == "missing":
            metadata["runtime_status_reason"] = "aio_native_cdp_url_missing"
            runtime_record.metadata = metadata
        return runtime_record


class AioApiRuntimeProvider:
    """HTTP provider for real AIO sandbox lifecycle APIs.

    The exact intranet AIO service can keep its own schema. This provider owns
    the small Host-side mapping from create/status/delete responses to
    SessionRuntimeRecord so RPA and chat execution keep using the same runtime
    boundary.
    """

    def __init__(
        self,
        settings,
        *,
        http_client_factory: Callable[..., Any] | None = None,
        adapter_client_cls=RuntimeAdapterClient,
        timeout: float = 30.0,
    ):
        self.settings = settings
        self.http_client_factory = http_client_factory or httpx.AsyncClient
        self.adapter_client_cls = adapter_client_cls
        self.timeout = timeout

    def _api_base_url(self) -> str:
        configured = (getattr(self.settings, "aio_runtime_api_base_url", "") or "").strip()
        if not configured:
            raise RuntimeError("AIO_RUNTIME_API_BASE_URL is required for RUNTIME_MODE=aio")
        return configured.rstrip("/")

    def _path(self, value: str, **params) -> str:
        path = (value or "").format(**params).strip() or "/"
        return "/" + path.lstrip("/")

    def _url(self, path: str) -> str:
        return f"{self._api_base_url()}{self._path(path)}"

    def _url_from_base(self, base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}{self._path(path)}"

    def _headers(self) -> dict[str, str]:
        token = (getattr(self.settings, "aio_runtime_api_token", "") or "").strip()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    async def _request_json(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        async with self.http_client_factory(timeout=self.timeout) as client:
            response = await client.request(
                method,
                self._url(path),
                headers=self._headers(),
                **kwargs,
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {"data": payload}

    def _sandbox_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = payload
        for key in ("data", "sandbox", "runtime"):
            nested = current.get(key)
            if isinstance(nested, dict):
                current = nested
        return current

    def _create_path(self) -> str:
        return getattr(self.settings, "aio_runtime_create_path", "/v1/sandboxes")

    def _status_path(self, sandbox_id: str) -> str:
        template = getattr(
            self.settings,
            "aio_runtime_status_path_template",
            "/v1/sandboxes/{sandbox_id}",
        )
        return self._path(template, sandbox_id=sandbox_id)

    def _delete_path(self, sandbox_id: str) -> str:
        template = getattr(
            self.settings,
            "aio_runtime_delete_path_template",
            "/v1/sandboxes/{sandbox_id}",
        )
        return self._path(template, sandbox_id=sandbox_id)

    def _namespace(self) -> str:
        return (getattr(self.settings, "aio_runtime_namespace", "") or "aio").strip() or "aio"

    def _image(self) -> str:
        return (
            getattr(self.settings, "aio_runtime_image", "")
            or getattr(self.settings, "runtime_image", "")
            or "rpaclaw-runtime-adapter:local"
        )

    def _ttl_seconds(self) -> int:
        configured = getattr(self.settings, "aio_runtime_ttl_seconds", None)
        if configured is None:
            configured = getattr(self.settings, "runtime_idle_ttl_seconds", 3600)
        return int(configured)

    def _create_extra(self) -> dict[str, Any]:
        configured = (getattr(self.settings, "aio_runtime_create_extra_json", "") or "").strip()
        if not configured:
            return {}
        try:
            payload = json.loads(configured)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AIO_RUNTIME_CREATE_EXTRA_JSON must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("AIO_RUNTIME_CREATE_EXTRA_JSON must be a JSON object")
        return payload

    def _adapter_env(self) -> dict[str, str]:
        configured = (getattr(self.settings, "aio_runtime_adapter_env", "") or "").strip()
        if not configured:
            return {}
        env: dict[str, str] = {}
        for item in configured.replace("\n", ",").split(","):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                raise RuntimeError("AIO_RUNTIME_ADAPTER_ENV entries must use KEY=VALUE")
            key, value = item.split("=", 1)
            key = key.strip()
            if not key:
                raise RuntimeError("AIO_RUNTIME_ADAPTER_ENV entries must include a key")
            env[key] = value.strip()
        return env

    def _sanitize_payload(self, payload: Any, *, key: str = "") -> Any:
        key_lower = key.lower()
        if key_lower and any(keyword in key_lower for keyword in SENSITIVE_PAYLOAD_KEYWORDS):
            return "<configured>" if payload not in (None, "", [], {}) else payload
        if isinstance(payload, dict):
            return {
                item_key: self._sanitize_payload(item_value, key=str(item_key))
                for item_key, item_value in payload.items()
            }
        if isinstance(payload, list):
            return [self._sanitize_payload(item) for item in payload]
        return payload

    def _runtime_token_from_create_payload(self, payload: dict[str, Any]) -> str | None:
        env = payload.get("env")
        if not isinstance(env, dict):
            return None
        token = str(env.get("RUNTIME_ADAPTER_TOKEN") or "").strip()
        return token or None

    def _normalize_runtime_status(self, value: Any, *, fallback: str = "ready") -> str:
        status = str(value or fallback).strip().lower()
        if status in AIO_READY_STATUSES:
            return "ready"
        if status in AIO_CREATING_STATUSES:
            return "creating"
        if status in AIO_MISSING_STATUSES:
            return "missing"
        return status or fallback

    def _create_payload(self, session_id: str, user_id: str) -> dict[str, Any]:
        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "image": self._image(),
            "ttl_seconds": self._ttl_seconds(),
        }
        extra = self._create_extra()
        payload.update(extra)
        adapter_env = self._adapter_env()
        if adapter_env:
            existing_env = payload.get("env")
            if existing_env is None:
                existing_env = {}
            if not isinstance(existing_env, dict):
                raise RuntimeError("AIO create payload env must be a JSON object")
            payload["env"] = {**existing_env, **adapter_env}
        return payload

    def diagnose_config(
        self,
        *,
        session_id: str = "diagnostic-session",
        user_id: str = "diagnostic-user",
        sandbox_id: str = "diagnostic-sandbox",
    ) -> dict[str, Any]:
        """Return a sanitized local diagnostic for AIO provider wiring."""

        base_url = (getattr(self.settings, "aio_runtime_api_base_url", "") or "").strip()
        missing = []
        if not base_url:
            missing.append("AIO_RUNTIME_API_BASE_URL")
        endpoints = {}
        if base_url:
            endpoints = {
                "create": self._url_from_base(base_url, self._create_path()),
                "status": self._url_from_base(base_url, self._status_path(sandbox_id)),
                "delete": self._url_from_base(base_url, self._delete_path(sandbox_id)),
            }
        invalid = []
        try:
            create_payload = self._sanitize_payload(self._create_payload(session_id, user_id))
        except RuntimeError as exc:
            invalid.append(str(exc))
            create_payload = {}
        return {
            "mode": "aio",
            "ready": not missing and not invalid,
            "missing": missing,
            "invalid": invalid,
            "auth": {
                "api_token_configured": bool(
                    (getattr(self.settings, "aio_runtime_api_token", "") or "").strip()
                ),
            },
            "endpoints": endpoints,
            "create_payload": create_payload,
        }

    def _runtime_diagnostic_summary(self, runtime: SessionRuntimeRecord) -> dict[str, Any]:
        return {
            "session_id": runtime.session_id,
            "user_id": runtime.user_id,
            "namespace": runtime.namespace,
            "sandbox_id": runtime.sandbox_id,
            "route_base_url": runtime.route_base_url or runtime.rest_base_url,
            "browser_view_url": runtime.browser_view_url,
            "status": runtime.status,
            "runtime_token_configured": bool(runtime.runtime_token),
            "expires_at": runtime.expires_at,
        }

    def diagnose_response_sample(
        self,
        payload: dict[str, Any],
        *,
        session_id: str = "diagnostic-session",
        user_id: str = "diagnostic-user",
    ) -> dict[str, Any]:
        """Validate an AIO create/status response sample without HTTP or adapter calls."""

        try:
            create_payload = self._create_payload(session_id, user_id)
            runtime = self._record_from_payload(
                payload,
                session_id=session_id,
                user_id=user_id,
                create_payload=create_payload,
            )
        except RuntimeError as exc:
            return {
                "ready": False,
                "invalid": [str(exc)],
                "runtime": {},
            }
        return {
            "ready": True,
            "invalid": [],
            "runtime": self._runtime_diagnostic_summary(runtime),
        }

    def _record_from_payload(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        user_id: str,
        existing: SessionRuntimeRecord | None = None,
        create_payload: dict[str, Any] | None = None,
    ) -> SessionRuntimeRecord:
        payload = self._sandbox_payload(payload)
        sandbox_id = str(
            payload.get("sandbox_id")
            or payload.get("id")
            or (existing.sandbox_id if existing else "")
        ).strip()
        if not sandbox_id:
            raise RuntimeError("AIO sandbox response missing sandbox_id")
        route_base_url = str(
            payload.get("route_base_url")
            or payload.get("adapter_url")
            or payload.get("rest_base_url")
            or (existing.route_base_url if existing else "")
            or (existing.rest_base_url if existing else "")
        ).rstrip("/")
        if not route_base_url:
            raise RuntimeError("AIO sandbox response missing route_base_url")
        now = int(time.time())
        runtime_token = (
            payload.get("runtime_token")
            or payload.get("adapter_token")
            or (existing.runtime_token if existing else None)
            or (self._runtime_token_from_create_payload(create_payload or {}) if create_payload else None)
        )
        return SessionRuntimeRecord(
            session_id=session_id,
            user_id=user_id,
            namespace=self._namespace(),
            pod_name=sandbox_id,
            service_name=sandbox_id,
            rest_base_url=route_base_url,
            route_base_url=route_base_url,
            browser_view_url=payload.get("browser_view_url")
            or payload.get("view_url")
            or (existing.browser_view_url if existing else None),
            runtime_token=runtime_token,
            sandbox_id=sandbox_id,
            status=self._normalize_runtime_status(
                payload.get("status"),
                fallback=existing.status if existing else "ready",
            ),
            created_at=existing.created_at if existing else now,
            last_used_at=now,
            expires_at=payload.get("expires_at") or (existing.expires_at if existing else None),
        )

    async def _mark_missing_when_adapter_contract_mismatches(
        self,
        runtime: SessionRuntimeRecord,
    ) -> SessionRuntimeRecord:
        if runtime.status == "ready":
            if not await _apply_adapter_health_contract(self.adapter_client_cls, runtime):
                runtime.status = "missing"
        return runtime

    async def create_runtime(self, session_id: str, user_id: str) -> SessionRuntimeRecord:
        try:
            create_payload = self._create_payload(session_id, user_id)
        except RuntimeError:
            raise AioRuntimeProviderError("create", "aio_create_config_invalid") from None
        try:
            payload = await self._request_json(
                "POST",
                self._create_path(),
                json=create_payload,
            )
        except Exception:
            raise AioRuntimeProviderError("create", "aio_create_unavailable") from None
        try:
            runtime = self._record_from_payload(
                payload,
                session_id=session_id,
                user_id=user_id,
                create_payload=create_payload,
            )
        except RuntimeError:
            raise AioRuntimeProviderError("create", "aio_create_response_invalid") from None
        return await self._mark_missing_when_adapter_contract_mismatches(
            runtime
        )

    async def delete_runtime(self, runtime_record) -> None:
        sandbox_id = getattr(runtime_record, "sandbox_id", None)
        if not sandbox_id:
            return None
        try:
            await self._request_json("DELETE", self._delete_path(str(sandbox_id)))
        except Exception:
            raise AioRuntimeProviderError("delete", "aio_delete_unavailable") from None
        return None

    async def refresh_runtime(self, runtime_record):
        sandbox_id = getattr(runtime_record, "sandbox_id", None)
        if not sandbox_id:
            return _mark_runtime_missing(runtime_record, "aio_sandbox_id_missing")
        try:
            payload = await self._request_json("GET", self._status_path(str(sandbox_id)))
        except Exception:
            return _mark_runtime_missing(runtime_record, "aio_status_unavailable")
        return await self._mark_missing_when_adapter_contract_mismatches(
            self._record_from_payload(
                payload,
                session_id=runtime_record.session_id,
                user_id=runtime_record.user_id,
                existing=runtime_record,
            )
        )


def main(argv: list[str] | None = None, *, settings_obj=None) -> int:
    parser = argparse.ArgumentParser(description="AIO runtime provider utilities.")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Print sanitized AIO provider configuration diagnostics.",
    )
    parser.add_argument("--session-id", default="diagnostic-session")
    parser.add_argument("--user-id", default="diagnostic-user")
    parser.add_argument("--sandbox-id", default="diagnostic-sandbox")
    parser.add_argument(
        "--sample-response",
        default="",
        help="Path to an AIO create/status response JSON sample to validate locally.",
    )
    args = parser.parse_args(argv)
    if not args.diagnose:
        parser.error("Only --diagnose is supported")

    if settings_obj is None:
        from backend.config import settings as settings_obj

    provider = AioApiRuntimeProvider(settings_obj)
    diagnostic = provider.diagnose_config(
        session_id=args.session_id,
        user_id=args.user_id,
        sandbox_id=args.sandbox_id,
    )
    if args.sample_response:
        with open(args.sample_response, "r", encoding="utf-8") as response_file:
            payload = json.load(response_file)
        if not isinstance(payload, dict):
            payload = {"data": payload}
        diagnostic["response_sample"] = provider.diagnose_response_sample(
            payload,
            session_id=args.session_id,
            user_id=args.user_id,
        )
        diagnostic["ready"] = bool(diagnostic.get("ready")) and diagnostic["response_sample"]["ready"]
    print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
    return 0 if diagnostic.get("ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
