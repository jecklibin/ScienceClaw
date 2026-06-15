from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException

from backend.runtime.adapter_app import create_runtime_adapter_app, runtime_adapter_self_check
from backend.runtime.adapter_client import RuntimeAdapterClient
from backend.runtime.adapter_workspace import run_uploaded_skill
from backend.runtime.aio_runtime_provider import AioApiRuntimeProvider, AioRuntimeProvider
from backend.runtime.local_fake_aio_service import create_local_fake_aio_app


def _provider_settings(adapter_token: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        aio_runtime_sandbox_id="local-aio-smoke",
        aio_runtime_route_base_url="http://adapter-smoke.test",
        aio_runtime_browser_view_url="",
        aio_runtime_token=adapter_token or "",
        sandbox_base_url="http://adapter-smoke.test",
    )


def _aio_api_settings(adapter_token: str) -> SimpleNamespace:
    return SimpleNamespace(
        aio_runtime_api_base_url="http://fake-aio.test",
        aio_runtime_api_token="aio-api-token",
        aio_runtime_image="rpaclaw-runtime-adapter:smoke",
        aio_runtime_create_extra_json="",
        aio_runtime_adapter_env=(
            f"RUNTIME_ADAPTER_TOKEN={adapter_token},RUNTIME_ADAPTER_DOWNLOADS_DIR=downloads"
        ),
        aio_runtime_create_path="/v1/sandboxes",
        aio_runtime_status_path_template="/v1/sandboxes/{sandbox_id}",
        aio_runtime_delete_path_template="/v1/sandboxes/{sandbox_id}",
        aio_runtime_namespace="aio-smoke",
        aio_runtime_ttl_seconds=60,
    )


def _aio_container_settings(adapter_token: str, *, image: str) -> SimpleNamespace:
    return SimpleNamespace(
        aio_runtime_api_base_url="http://local-fake-aio.test",
        aio_runtime_api_token="local-fake-aio-token",
        aio_runtime_image=image,
        aio_runtime_create_extra_json="",
        aio_runtime_adapter_env=(
            f"RUNTIME_ADAPTER_TOKEN={adapter_token},"
            "RUNTIME_ADAPTER_DOWNLOADS_DIR=downloads,"
            "RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH=true"
        ),
        aio_runtime_create_path="/v1/sandboxes",
        aio_runtime_status_path_template="/v1/sandboxes/{sandbox_id}",
        aio_runtime_delete_path_template="/v1/sandboxes/{sandbox_id}",
        aio_runtime_namespace="local-fake-aio-container",
        aio_runtime_ttl_seconds=60,
    )


def _sanitize_smoke_payload(payload: Any, *, key: str = "") -> Any:
    key_lower = key.lower()
    if key_lower and any(part in key_lower for part in ("authorization", "password", "secret", "token")):
        return "<configured>" if payload not in (None, "", [], {}) else payload
    if isinstance(payload, dict):
        return {
            item_key: _sanitize_smoke_payload(item_value, key=str(item_key))
            for item_key, item_value in payload.items()
        }
    if isinstance(payload, list):
        return [_sanitize_smoke_payload(item) for item in payload]
    return payload


def _runtime_summary(runtime) -> dict[str, Any]:
    return runtime.model_dump(
        include={
            "session_id",
            "user_id",
            "namespace",
            "pod_name",
            "service_name",
            "sandbox_id",
            "rest_base_url",
            "route_base_url",
            "browser_view_url",
            "status",
            "metadata",
        }
    )


def _create_fake_aio_api_app(
    *,
    adapter_route_base_url: str,
    adapter_browser_view_url: str,
    lifecycle: list[dict[str, Any]],
) -> FastAPI:
    app = FastAPI(title="RpaClaw Fake AIO Runtime API")

    def _require_api_token(authorization: str | None) -> None:
        if authorization != "Bearer aio-api-token":
            raise HTTPException(status_code=401, detail="Invalid fake AIO API token")

    def _sandbox_payload() -> dict[str, Any]:
        return {
            "sandbox_id": "fake-aio-sandbox",
            "route_base_url": adapter_route_base_url,
            "browser_view_url": adapter_browser_view_url,
            "status": "ready",
        }

    @app.post("/v1/sandboxes")
    async def create_sandbox(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_api_token(authorization)
        lifecycle.append(
            {
                "method": "POST",
                "path": "/v1/sandboxes",
                "payload": _sanitize_smoke_payload(payload),
                "api_token_configured": True,
            }
        )
        return {"data": _sandbox_payload()}

    @app.get("/v1/sandboxes/{sandbox_id}")
    async def get_sandbox(
        sandbox_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_api_token(authorization)
        lifecycle.append(
            {
                "method": "GET",
                "path": f"/v1/sandboxes/{sandbox_id}",
                "api_token_configured": True,
            }
        )
        return {"data": _sandbox_payload()}

    @app.delete("/v1/sandboxes/{sandbox_id}")
    async def delete_sandbox(
        sandbox_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_api_token(authorization)
        lifecycle.append(
            {
                "method": "DELETE",
                "path": f"/v1/sandboxes/{sandbox_id}",
                "api_token_configured": True,
            }
        )
        return {"status": "deleted"}

    return app


def _write_smoke_skill(source_dir: Path) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "skill.py").write_text(
        "from pathlib import Path\n"
        "report = Path('../../downloads/smoke-report.txt').resolve()\n"
        "report.parent.mkdir(parents=True, exist_ok=True)\n"
        "report.write_text('smoke-report', encoding='utf-8')\n"
        "print('smoke-skill-ok')\n",
        encoding="utf-8",
    )


async def run_local_adapter_smoke(
    workspace_root: str | Path,
    *,
    adapter_token: str = "smoke-token",
) -> dict[str, Any]:
    """Run an in-process local Runtime Adapter contract smoke check."""

    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    app = create_runtime_adapter_app(
        workspace_root=root,
        enable_local_execution=True,
        adapter_token=adapter_token,
    )
    adapter_self_check = runtime_adapter_self_check(
        workspace_root=root,
        enable_local_execution=True,
        adapter_token=adapter_token,
    )
    transport = httpx.ASGITransport(app=app)

    def _client_factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    class _SmokeRuntimeAdapterClient(RuntimeAdapterClient):
        def __init__(self, runtime):
            super().__init__(
                runtime,
                http_client_factory=_client_factory,
            )

    provider = AioRuntimeProvider(
        _provider_settings(adapter_token),
        adapter_client_cls=_SmokeRuntimeAdapterClient,
    )
    runtime = await provider.create_runtime(
        "smoke-session",
        "smoke-user",
    )
    runtime = await provider.refresh_runtime(runtime)
    client = RuntimeAdapterClient(
        runtime,
        http_client_factory=_client_factory,
    )

    health = await client.health()
    await client.emit_snapshot(
        {
            "raw_snapshot": {"html": "<button>Smoke</button>"},
            "compact_snapshot": {"buttons": [{"text": "Smoke"}]},
            "page_state": {
                "url": "https://example.test/smoke",
                "title": "Smoke",
            },
        }
    )
    snapshot = await client.get_snapshot()
    await client.start_recording({"session_id": "smoke-session"})
    started = await client.get_events(cursor="0")
    emitted = await client.emit_event(
        {
            "event_id": "evt-smoke-click",
            "action": "click",
            "locator": {"role": "button", "name": "Smoke"},
        }
    )
    await client.stop_recording({"session_id": "smoke-session"})
    stopped = await client.get_events(cursor=emitted["cursor"])

    source = root / "host_generated_skill"
    _write_smoke_skill(source)
    skill_result = await run_uploaded_skill(
        client,
        source,
        "skills/smoke",
        timeout_seconds=10,
        download_outputs_to=root / "host_downloads",
    )

    return {
        "status": "ok",
        "runtime": _runtime_summary(runtime),
        "runtime_token_configured": bool(runtime.runtime_token),
        "adapter_self_check": adapter_self_check,
        "health": health,
        "snapshot": snapshot,
        "recording_events": [
            *started.get("events", []),
            emitted,
            *stopped.get("events", []),
        ],
        "skill": skill_result,
    }


async def run_aio_api_adapter_smoke(
    workspace_root: str | Path,
    *,
    adapter_token: str = "adapter-token",
) -> dict[str, Any]:
    """Run an in-process fake AIO API lifecycle plus Runtime Adapter smoke check."""

    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    adapter_route_base_url = "http://adapter-smoke.test"
    adapter_app = create_runtime_adapter_app(
        workspace_root=root,
        enable_local_execution=True,
        adapter_token=adapter_token,
        browser_view_url=f"{adapter_route_base_url}/browser",
    )
    adapter_self_check = runtime_adapter_self_check(
        workspace_root=root,
        enable_local_execution=True,
        adapter_token=adapter_token,
        browser_view_url=f"{adapter_route_base_url}/browser",
    )
    adapter_transport = httpx.ASGITransport(app=adapter_app)
    lifecycle: list[dict[str, Any]] = []
    aio_app = _create_fake_aio_api_app(
        adapter_route_base_url=adapter_route_base_url,
        adapter_browser_view_url=f"{adapter_route_base_url}/browser",
        lifecycle=lifecycle,
    )
    aio_transport = httpx.ASGITransport(app=aio_app)

    def _aio_client_factory(**kwargs):
        return httpx.AsyncClient(transport=aio_transport, **kwargs)

    def _adapter_client_factory(**kwargs):
        return httpx.AsyncClient(transport=adapter_transport, **kwargs)

    class _SmokeRuntimeAdapterClient(RuntimeAdapterClient):
        def __init__(self, runtime):
            super().__init__(
                runtime,
                http_client_factory=_adapter_client_factory,
            )

    provider = AioApiRuntimeProvider(
        _aio_api_settings(adapter_token),
        http_client_factory=_aio_client_factory,
        adapter_client_cls=_SmokeRuntimeAdapterClient,
    )
    runtime = await provider.create_runtime("smoke-session", "smoke-user")
    runtime = await provider.refresh_runtime(runtime)
    client = RuntimeAdapterClient(runtime, http_client_factory=_adapter_client_factory)

    health = await client.health()
    source = root / "host_generated_skill"
    _write_smoke_skill(source)
    skill_result = await run_uploaded_skill(
        client,
        source,
        "skills/smoke",
        timeout_seconds=10,
        download_outputs_to=root / "host_downloads",
    )
    await provider.delete_runtime(runtime)

    return {
        "status": "ok",
        "aio_lifecycle": lifecycle,
        "runtime": _runtime_summary(runtime),
        "runtime_token_configured": bool(runtime.runtime_token),
        "aio_api_token_configured": True,
        "adapter_self_check": adapter_self_check,
        "health": health,
        "skill": skill_result,
    }


async def run_aio_container_adapter_smoke(
    workspace_root: str | Path,
    *,
    adapter_token: str = "adapter-token",
    image: str = "rpaclaw-runtime-adapter:dev",
    adapter_host_port_start: int = 18081,
    cdp_host_port_start: int = 19222,
) -> dict[str, Any]:
    """Run local fake AIO lifecycle against a real Runtime Adapter container."""

    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    aio_app = create_local_fake_aio_app(
        image=image,
        adapter_host_port_start=adapter_host_port_start,
        cdp_host_port_start=cdp_host_port_start,
        api_token="local-fake-aio-token",
    )
    aio_transport = httpx.ASGITransport(app=aio_app)

    def _aio_client_factory(**kwargs):
        return httpx.AsyncClient(transport=aio_transport, **kwargs)

    class _RetryingRuntimeAdapterClient(RuntimeAdapterClient):
        async def health(self) -> dict[str, Any]:
            deadline = time.monotonic() + 30
            last_error: BaseException | None = None
            while time.monotonic() < deadline:
                try:
                    return await super().health()
                except BaseException as exc:
                    last_error = exc
                    await asyncio.sleep(0.5)
            if last_error:
                raise last_error
            return await super().health()

    provider = AioApiRuntimeProvider(
        _aio_container_settings(adapter_token, image=image),
        http_client_factory=_aio_client_factory,
        adapter_client_cls=_RetryingRuntimeAdapterClient,
    )
    runtime = await provider.create_runtime(f"smoke-container-{int(time.time())}", "smoke-user")
    primary_error: BaseException | None = None
    try:
        runtime = await provider.refresh_runtime(runtime)
        client = _RetryingRuntimeAdapterClient(runtime)
        health = await client.health()
        browser = await client.browser_info()
        return {
            "status": "ok",
            "mode": "aio_container",
            "runtime": _runtime_summary(runtime),
            "runtime_token_configured": bool(runtime.runtime_token),
            "aio_api_token_configured": True,
            "health": health,
            "browser": browser,
        }
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            await provider.delete_runtime(runtime)
        except Exception:
            if primary_error is None:
                raise


async def run_real_aio_adapter_smoke(
    workspace_root: str | Path,
    *,
    settings=None,
    http_client_factory=None,
    adapter_client_cls=RuntimeAdapterClient,
    delete_on_finish: bool = True,
) -> dict[str, Any]:
    """Run a smoke check against configured external AIO lifecycle endpoints."""

    if settings is None:
        from backend.config import settings as settings

    root = Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    provider = AioApiRuntimeProvider(
        settings,
        http_client_factory=http_client_factory,
        adapter_client_cls=adapter_client_cls,
    )
    runtime = await provider.create_runtime("smoke-session", "smoke-user")
    primary_error: BaseException | None = None
    try:
        runtime = await provider.refresh_runtime(runtime)
        client = adapter_client_cls(runtime)
        health = await client.health()
        source = root / "host_generated_skill"
        _write_smoke_skill(source)
        skill_result = await run_uploaded_skill(
            client,
            source,
            "skills/smoke",
            timeout_seconds=10,
            download_outputs_to=root / "host_downloads",
        )
        return {
            "status": "ok",
            "mode": "aio_real",
            "aio_config": provider.diagnose_config(
                session_id="smoke-session",
                user_id="smoke-user",
                sandbox_id=runtime.sandbox_id or "smoke-sandbox",
            ),
            "runtime": _runtime_summary(runtime),
            "runtime_token_configured": bool(runtime.runtime_token),
            "aio_api_token_configured": bool(
                (getattr(settings, "aio_runtime_api_token", "") or "").strip()
            ),
            "adapter_self_check": health,
            "health": health,
            "skill": skill_result,
        }
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if delete_on_finish:
            try:
                await provider.delete_runtime(runtime)
            except Exception:
                if primary_error is None:
                    raise


async def _main_async(args) -> dict[str, Any]:
    if args.workspace_root:
        if args.mode == "aio_container":
            return await run_aio_container_adapter_smoke(
                args.workspace_root,
                adapter_token=args.adapter_token,
                image=args.image,
            )
        if args.mode == "aio_real":
            return await run_real_aio_adapter_smoke(
                args.workspace_root,
                delete_on_finish=not args.keep_runtime,
            )
        if args.mode == "aio":
            return await run_aio_api_adapter_smoke(args.workspace_root, adapter_token=args.adapter_token)
        return await run_local_adapter_smoke(args.workspace_root, adapter_token=args.adapter_token)
    with tempfile.TemporaryDirectory(prefix="rpaclaw-adapter-smoke-") as temp_dir:
        if args.mode == "aio_container":
            return await run_aio_container_adapter_smoke(
                temp_dir,
                adapter_token=args.adapter_token,
                image=args.image,
            )
        if args.mode == "aio_real":
            return await run_real_aio_adapter_smoke(
                temp_dir,
                delete_on_finish=not args.keep_runtime,
            )
        if args.mode == "aio":
            return await run_aio_api_adapter_smoke(temp_dir, adapter_token=args.adapter_token)
        return await run_local_adapter_smoke(temp_dir, adapter_token=args.adapter_token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local Runtime Adapter smoke checks.")
    parser.add_argument("--workspace-root", default="", help="Workspace directory for the local adapter app.")
    parser.add_argument("--adapter-token", default="smoke-token", help="Bearer token used for the local adapter app.")
    parser.add_argument(
        "--mode",
        choices=["aio_fixed", "aio", "aio_container", "aio_real"],
        default="aio_fixed",
        help=(
            "Smoke mode: fixed local runtime record, fake AIO lifecycle API, "
            "local fake AIO service with a real adapter container, "
            "or configured real AIO lifecycle API."
        ),
    )
    parser.add_argument(
        "--image",
        default="rpaclaw-runtime-adapter:dev",
        help="Runtime Adapter image used by --mode aio_container.",
    )
    parser.add_argument(
        "--keep-runtime",
        action="store_true",
        help="Only for --mode aio_real: keep the AIO sandbox after smoke for inner-network debugging.",
    )
    args = parser.parse_args(argv)
    result = asyncio.run(_main_async(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "main",
    "run_aio_api_adapter_smoke",
    "run_aio_container_adapter_smoke",
    "run_local_adapter_smoke",
    "run_real_aio_adapter_smoke",
]
