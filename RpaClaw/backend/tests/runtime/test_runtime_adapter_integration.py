from __future__ import annotations

import base64

import httpx
import pytest

from backend.runtime.adapter_app import create_runtime_adapter_app
from backend.runtime.adapter_client import RuntimeAdapterClient
from backend.runtime.adapter_workspace import run_uploaded_skill
from backend.runtime.models import SessionRuntimeRecord


def _runtime_record() -> SessionRuntimeRecord:
    return SessionRuntimeRecord(
        session_id="sess-1",
        user_id="user-1",
        namespace="aio-local",
        pod_name="local-aio",
        service_name="local-aio",
        rest_base_url="http://adapter.test",
        route_base_url="http://adapter.test",
        runtime_token="session-token",
        status="ready",
    )


@pytest.mark.asyncio
async def test_runtime_adapter_client_talks_to_local_adapter_app(tmp_path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").write_text("print('skill-ok')", encoding="utf-8")
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    (downloads_dir / "download.txt").write_text("download-ok", encoding="utf-8")
    (tmp_path / "report.txt").write_text("report-ok", encoding="utf-8")
    app = create_runtime_adapter_app(
        workspace_root=tmp_path,
        enable_local_execution=True,
        cdp_url="ws://127.0.0.1:9222/devtools/browser/local",
        browser_view_url="http://127.0.0.1:6080/vnc.html",
        adapter_token="session-token",
    )
    transport = httpx.ASGITransport(app=app)

    def _client_factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    client = RuntimeAdapterClient(
        _runtime_record(),
        http_client_factory=_client_factory,
    )

    write_result = await client.write_file(
        "skills/generated/skill.py",
        "print('generated-ok')",
    )
    binary_content = b"\x00\x01binary-input"
    binary_write_result = await client.write_file_base64(
        "inputs/blob.bin",
        base64.b64encode(binary_content).decode("ascii"),
    )
    health = await client.health()
    browser = await client.browser_info()
    execution = await client.execute_step(
        {
            "command": [
                "python",
                "-c",
                "from pathlib import Path; Path('out.txt').write_text('done', encoding='utf-8'); print('exec-ok')",
            ],
            "cwd": ".",
            "timeout_seconds": 5,
        }
    )
    skill = await client.run_skill({"skill_path": "skills/demo", "timeout_seconds": 5})
    generated_skill = await client.run_skill({"skill_path": "skills/generated", "timeout_seconds": 5})
    downloads = await client.list_downloads()
    files = await client.list_files(".")
    downloaded = await client.download_file("report.txt")
    downloaded_artifact = await client.download_file("downloads/download.txt")
    downloaded_binary = await client.download_file("inputs/blob.bin")

    assert write_result == {
        "status": "success",
        "path": "skills/generated/skill.py",
        "size": 21,
    }
    assert binary_write_result == {
        "status": "success",
        "path": "inputs/blob.bin",
        "size": len(binary_content),
    }
    assert health["status"] == "ok"
    assert health["capabilities"] == {
        "browser_info": True,
        "browser_launch": False,
        "execute_step": True,
        "run_skill": True,
        "downloads": True,
        "files": True,
    }
    assert health["config"]["token_required"] is True
    assert browser["data"]["cdp_url"] == "ws://127.0.0.1:9222/devtools/browser/local"
    assert execution["status"] == "success"
    assert execution["stdout"].strip() == "exec-ok"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "done"
    assert skill["status"] == "success"
    assert skill["stdout"].strip() == "skill-ok"
    assert generated_skill["status"] == "success"
    assert generated_skill["stdout"].strip() == "generated-ok"
    assert downloads["downloads"] == [
        {
            "name": "download.txt",
            "path": "downloads/download.txt",
            "sha256": "87ed2e37b5d7b03ee6a3f17b3b25999160e64384a8ced5baeb6ba82b660bde2f",
            "size": 11,
        }
    ]
    assert "report.txt" in {entry["name"] for entry in files["entries"]}
    assert downloaded == b"report-ok"
    assert downloaded_artifact == b"download-ok"
    assert downloaded_binary == binary_content


@pytest.mark.asyncio
async def test_runtime_adapter_workspace_runs_uploaded_skill_against_local_app(tmp_path):
    source = tmp_path / "host_generated_skill"
    source.mkdir()
    (source / "skill.py").write_text(
        "from pathlib import Path\n"
        "report = Path('../../downloads/generated-report.txt').resolve()\n"
        "report.parent.mkdir(parents=True, exist_ok=True)\n"
        "report.write_text('generated-report', encoding='utf-8')\n"
        "print('uploaded-skill-ok')\n",
        encoding="utf-8",
    )
    app = create_runtime_adapter_app(
        workspace_root=tmp_path,
        enable_local_execution=True,
        adapter_token="session-token",
    )
    transport = httpx.ASGITransport(app=app)

    def _client_factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    client = RuntimeAdapterClient(
        _runtime_record(),
        http_client_factory=_client_factory,
    )
    host_downloads = tmp_path / "host_downloads"

    result = await run_uploaded_skill(
        client,
        source,
        "skills/uploaded",
        timeout_seconds=5,
        download_outputs_to=host_downloads,
    )

    assert result["upload"]["files"] == [
        {"path": "skills/uploaded/skill.py", "size": (source / "skill.py").stat().st_size}
    ]
    assert result["run"]["status"] == "success"
    assert result["run"]["stdout"].strip() == "uploaded-skill-ok"
    assert result["downloads"] == [
        {
            "name": "generated-report.txt",
            "path": "downloads/generated-report.txt",
            "sha256": "cc850c51d434a92969222e56b3583536e74436e4abb150a52c0f96378fa62a03",
            "size": 16,
            "local_path": str(host_downloads / "generated-report.txt"),
        }
    ]
    assert (host_downloads / "generated-report.txt").read_text(encoding="utf-8") == "generated-report"
