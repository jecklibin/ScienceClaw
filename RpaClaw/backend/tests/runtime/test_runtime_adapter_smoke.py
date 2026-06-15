from __future__ import annotations

import json

import pytest

from types import SimpleNamespace

import httpx
from fastapi import Body
from fastapi import FastAPI
from fastapi import HTTPException

from backend.runtime.adapter_app import create_runtime_adapter_app
from backend.runtime.adapter_client import RuntimeAdapterClient
from backend.runtime.adapter_client import RuntimeAdapterClientError
from backend.runtime.adapter_smoke import (
    main,
    run_aio_api_adapter_smoke,
    run_aio_container_adapter_smoke,
    run_local_adapter_smoke,
    run_real_aio_adapter_smoke,
)
import backend.runtime.adapter_smoke as adapter_smoke_module
from backend.runtime.adapter_smoke import _create_fake_aio_api_app


@pytest.mark.asyncio
async def test_local_adapter_smoke_exercises_core_contracts(tmp_path):
    result = await run_local_adapter_smoke(tmp_path)

    assert result["status"] == "ok"
    assert result["runtime"] == {
        "session_id": "smoke-session",
        "user_id": "smoke-user",
        "namespace": "aio-local",
        "pod_name": "local-aio-smoke",
        "service_name": "local-aio-smoke",
        "sandbox_id": "local-aio-smoke",
        "rest_base_url": "http://adapter-smoke.test",
        "route_base_url": "http://adapter-smoke.test",
        "browser_view_url": None,
        "status": "ready",
        "metadata": {
            "adapter_health_status": "ok",
            "adapter_contract_version": "v1",
            "adapter_version": "local-dev",
            "adapter_file_policy": {
                "max_inline_file_write_bytes": 10 * 1024 * 1024,
                "max_file_download_bytes": 50 * 1024 * 1024,
                "oversized_hash_status": "skipped_oversized",
            },
            "supported_adapter_contract_version": "v1",
        },
    }
    assert result["runtime_token_configured"] is True
    assert "smoke-token" not in json.dumps(result["runtime"], sort_keys=True)
    assert result["adapter_self_check"]["status"] == "ok"
    assert result["adapter_self_check"]["config"]["token_required"] is True
    assert "smoke-token" not in json.dumps(result["adapter_self_check"], sort_keys=True)
    assert result["health"]["status"] == "ok"
    assert result["snapshot"] == {
        "raw_snapshot": {"html": "<button>Smoke</button>"},
        "compact_snapshot": {"buttons": [{"text": "Smoke"}]},
        "page_state": {
            "url": "https://example.test/smoke",
            "title": "Smoke",
        },
    }
    assert result["recording_events"] == [
        {
            "cursor": "1",
            "type": "recording_started",
            "session_id": "smoke-session",
        },
        {
            "cursor": "2",
            "type": "raw_event",
            "event_id": "evt-smoke-click",
            "action": "click",
            "locator": {"role": "button", "name": "Smoke"},
        },
        {
            "cursor": "3",
            "type": "recording_stopped",
            "session_id": "smoke-session",
        },
    ]
    assert result["skill"]["run"]["status"] == "success"
    assert result["skill"]["run"]["stdout"].strip() == "smoke-skill-ok"
    assert result["skill"]["downloads"][0]["name"] == "smoke-report.txt"
    assert (tmp_path / "host_downloads" / "smoke-report.txt").read_text(encoding="utf-8") == "smoke-report"


@pytest.mark.asyncio
async def test_aio_api_adapter_smoke_exercises_lifecycle_and_adapter_contract(tmp_path):
    result = await run_aio_api_adapter_smoke(tmp_path)

    assert result["status"] == "ok"
    assert result["aio_lifecycle"] == [
        {
            "method": "POST",
            "path": "/v1/sandboxes",
            "payload": {
                "session_id": "smoke-session",
                "user_id": "smoke-user",
                "image": "rpaclaw-runtime-adapter:smoke",
                "ttl_seconds": 60,
                "env": {
                    "RUNTIME_ADAPTER_TOKEN": "<configured>",
                    "RUNTIME_ADAPTER_DOWNLOADS_DIR": "downloads",
                },
            },
            "api_token_configured": True,
        },
        {
            "method": "GET",
            "path": "/v1/sandboxes/fake-aio-sandbox",
            "api_token_configured": True,
        },
        {
            "method": "DELETE",
            "path": "/v1/sandboxes/fake-aio-sandbox",
            "api_token_configured": True,
        },
    ]
    assert result["runtime"] == {
        "session_id": "smoke-session",
        "user_id": "smoke-user",
        "namespace": "aio-smoke",
        "pod_name": "fake-aio-sandbox",
        "service_name": "fake-aio-sandbox",
        "sandbox_id": "fake-aio-sandbox",
        "rest_base_url": "http://adapter-smoke.test",
        "route_base_url": "http://adapter-smoke.test",
        "browser_view_url": "http://adapter-smoke.test/browser",
        "status": "ready",
        "metadata": {
            "adapter_health_status": "ok",
            "adapter_contract_version": "v1",
            "adapter_version": "local-dev",
            "adapter_file_policy": {
                "max_inline_file_write_bytes": 10 * 1024 * 1024,
                "max_file_download_bytes": 50 * 1024 * 1024,
                "oversized_hash_status": "skipped_oversized",
            },
            "supported_adapter_contract_version": "v1",
        },
    }
    assert result["runtime_token_configured"] is True
    assert result["aio_api_token_configured"] is True
    assert result["adapter_self_check"]["status"] == "ok"
    assert result["adapter_self_check"]["config"]["token_required"] is True
    assert result["health"]["status"] == "ok"
    assert result["skill"]["run"]["status"] == "success"
    safe_summary = json.dumps(
        {
            "aio_lifecycle": result["aio_lifecycle"],
            "runtime": result["runtime"],
            "adapter_self_check": result["adapter_self_check"],
            "runtime_token_configured": result["runtime_token_configured"],
            "aio_api_token_configured": result["aio_api_token_configured"],
        },
        sort_keys=True,
    )
    assert "adapter-token" not in safe_summary
    assert "aio-api-token" not in safe_summary


@pytest.mark.asyncio
async def test_real_aio_adapter_smoke_uses_configured_provider_instead_of_builtin_fake(tmp_path):
    adapter_token = "real-smoke-token"
    adapter_route_base_url = "http://real-adapter-smoke.test"
    adapter_app = create_runtime_adapter_app(
        workspace_root=tmp_path,
        enable_local_execution=True,
        adapter_token=adapter_token,
        browser_view_url=f"{adapter_route_base_url}/browser",
    )
    adapter_transport = httpx.ASGITransport(app=adapter_app)
    lifecycle: list[dict] = []
    aio_app = _create_fake_aio_api_app(
        adapter_route_base_url=adapter_route_base_url,
        adapter_browser_view_url=f"{adapter_route_base_url}/browser",
        lifecycle=lifecycle,
    )
    aio_transport = httpx.ASGITransport(app=aio_app)

    def aio_client_factory(**kwargs):
        return httpx.AsyncClient(transport=aio_transport, **kwargs)

    def adapter_client_factory(**kwargs):
        return httpx.AsyncClient(transport=adapter_transport, **kwargs)

    class SmokeRuntimeAdapterClient(RuntimeAdapterClient):
        def __init__(self, runtime):
            super().__init__(runtime, http_client_factory=adapter_client_factory)

    settings = SimpleNamespace(
        aio_runtime_api_base_url="http://configured-real-aio.test",
        aio_runtime_api_token="aio-api-token",
        aio_runtime_image="rpaclaw-runtime-adapter:real-smoke",
        aio_runtime_create_extra_json='{"labels":{"source":"real-smoke"}}',
        aio_runtime_adapter_env=(
            f"RUNTIME_ADAPTER_TOKEN={adapter_token},RUNTIME_ADAPTER_DOWNLOADS_DIR=downloads"
        ),
        aio_runtime_create_path="/v1/sandboxes",
        aio_runtime_status_path_template="/v1/sandboxes/{sandbox_id}",
        aio_runtime_delete_path_template="/v1/sandboxes/{sandbox_id}",
        aio_runtime_namespace="real-aio-smoke",
        aio_runtime_ttl_seconds=120,
    )

    result = await run_real_aio_adapter_smoke(
        tmp_path,
        settings=settings,
        http_client_factory=aio_client_factory,
        adapter_client_cls=SmokeRuntimeAdapterClient,
    )

    assert result["status"] == "ok"
    assert result["mode"] == "aio_real"
    assert result["runtime"]["namespace"] == "real-aio-smoke"
    assert result["runtime"]["route_base_url"] == adapter_route_base_url
    assert result["runtime"]["metadata"]["adapter_file_policy"] == {
        "max_inline_file_write_bytes": 10 * 1024 * 1024,
        "max_file_download_bytes": 50 * 1024 * 1024,
        "oversized_hash_status": "skipped_oversized",
    }
    assert result["skill"]["run"]["status"] == "success"
    assert result["aio_api_token_configured"] is True
    assert result["adapter_self_check"] == result["health"]
    assert result["adapter_self_check"]["status"] == "ok"
    assert lifecycle[0]["payload"]["image"] == "rpaclaw-runtime-adapter:real-smoke"
    assert lifecycle[0]["payload"]["labels"] == {"source": "real-smoke"}
    safe_summary = json.dumps(result, sort_keys=True)
    assert adapter_token not in safe_summary
    assert "aio-api-token" not in safe_summary


@pytest.mark.asyncio
async def test_real_aio_adapter_smoke_preserves_primary_failure_when_cleanup_fails(tmp_path):
    adapter_token = "real-smoke-token"
    adapter_app = create_runtime_adapter_app(
        workspace_root=tmp_path,
        enable_local_execution=True,
        adapter_token=adapter_token,
    )
    adapter_transport = httpx.ASGITransport(app=adapter_app)

    def adapter_client_factory(**kwargs):
        return httpx.AsyncClient(transport=adapter_transport, **kwargs)

    class WrongTokenRuntimeAdapterClient(RuntimeAdapterClient):
        def __init__(self, runtime):
            runtime.runtime_token = "wrong-token"
            super().__init__(runtime, http_client_factory=adapter_client_factory)

    aio_app = FastAPI()

    async def create_sandbox(payload: dict = Body(...), authorization=None):
        return {
            "data": {
                "sandbox_id": "cleanup-fail-sandbox",
                "route_base_url": "http://adapter-cleanup-fail.test",
                "status": "ready",
            }
        }

    async def get_sandbox(sandbox_id, authorization=None):
        return {
            "data": {
                "sandbox_id": sandbox_id,
                "route_base_url": "http://adapter-cleanup-fail.test",
                "status": "ready",
            }
        }

    async def delete_sandbox(sandbox_id, authorization=None):
        raise HTTPException(status_code=500, detail={"token": "aio-api-token"})

    aio_app.post("/v1/sandboxes")(create_sandbox)
    aio_app.get("/v1/sandboxes/{sandbox_id}")(get_sandbox)
    aio_app.delete("/v1/sandboxes/{sandbox_id}")(delete_sandbox)
    aio_transport = httpx.ASGITransport(app=aio_app)

    def aio_client_factory(**kwargs):
        return httpx.AsyncClient(transport=aio_transport, **kwargs)

    settings = SimpleNamespace(
        aio_runtime_api_base_url="http://cleanup-fail-aio.test",
        aio_runtime_api_token="aio-api-token",
        aio_runtime_image="rpaclaw-runtime-adapter:real-smoke",
        aio_runtime_create_extra_json="",
        aio_runtime_adapter_env=f"RUNTIME_ADAPTER_TOKEN={adapter_token}",
        aio_runtime_create_path="/v1/sandboxes",
        aio_runtime_status_path_template="/v1/sandboxes/{sandbox_id}",
        aio_runtime_delete_path_template="/v1/sandboxes/{sandbox_id}",
        aio_runtime_namespace="real-aio-smoke",
        aio_runtime_ttl_seconds=120,
    )

    with pytest.raises(RuntimeAdapterClientError) as exc_info:
        await run_real_aio_adapter_smoke(
            tmp_path,
            settings=settings,
            http_client_factory=aio_client_factory,
            adapter_client_cls=WrongTokenRuntimeAdapterClient,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.path == "/health"
    assert "aio_delete_unavailable" not in str(exc_info.value)
    assert "aio-api-token" not in str(exc_info.value)


def test_adapter_smoke_cli_keep_runtime_disables_real_aio_cleanup(monkeypatch, tmp_path, capsys):
    captured = {}

    async def fake_real_smoke(workspace_root, *, delete_on_finish=True):
        captured["workspace_root"] = workspace_root
        captured["delete_on_finish"] = delete_on_finish
        return {"status": "ok", "mode": "aio_real"}

    monkeypatch.setattr(adapter_smoke_module, "run_real_aio_adapter_smoke", fake_real_smoke)

    exit_code = main(
        [
            "--mode",
            "aio_real",
            "--keep-runtime",
            "--workspace-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert captured == {
        "workspace_root": str(tmp_path),
        "delete_on_finish": False,
    }
    assert '"mode": "aio_real"' in capsys.readouterr().out


def test_adapter_smoke_cli_supports_local_fake_aio_container_mode(monkeypatch, tmp_path, capsys):
    captured = {}

    async def fake_container_smoke(workspace_root, *, adapter_token="adapter-token", image=""):
        captured["workspace_root"] = workspace_root
        captured["adapter_token"] = adapter_token
        captured["image"] = image
        return {"status": "ok", "mode": "aio_container"}

    monkeypatch.setattr(adapter_smoke_module, "run_aio_container_adapter_smoke", fake_container_smoke)

    exit_code = main(
        [
            "--mode",
            "aio_container",
            "--workspace-root",
            str(tmp_path),
            "--adapter-token",
            "adapter-token",
            "--image",
            "rpaclaw-runtime-adapter:test",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "workspace_root": str(tmp_path),
        "adapter_token": "adapter-token",
        "image": "rpaclaw-runtime-adapter:test",
    }
    assert '"mode": "aio_container"' in capsys.readouterr().out
