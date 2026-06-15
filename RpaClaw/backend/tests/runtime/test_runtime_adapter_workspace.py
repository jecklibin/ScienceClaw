from __future__ import annotations

import base64

import pytest

import backend.runtime.adapter_workspace as adapter_workspace
from backend.runtime.adapter_workspace import run_uploaded_skill, upload_directory


class _FakeAdapterClient:
    def __init__(self):
        self.writes: list[dict[str, str]] = []
        self.run_payloads: list[dict] = []
        self.downloads = {
            "downloads": [
                {"name": "report.txt", "path": "downloads/report.txt", "size": 9},
                {"name": "ignored-dir", "path": "downloads/ignored-dir", "size": None},
            ]
        }
        self.download_bytes = {"downloads/report.txt": b"report-ok"}

    async def write_file_base64(self, path: str, content_base64: str):
        self.writes.append({"path": path, "content_base64": content_base64})
        return {"status": "success", "path": path, "size": len(base64.b64decode(content_base64))}

    async def run_skill(self, payload: dict):
        self.run_payloads.append(payload)
        return {"status": "success", "stdout": "skill-ok", "stderr": ""}

    async def list_downloads(self):
        return self.downloads

    async def download_file(self, path: str):
        return self.download_bytes[path]


@pytest.mark.asyncio
async def test_upload_directory_writes_files_to_adapter_workspace(tmp_path):
    source = tmp_path / "generated_skill"
    (source / "nested").mkdir(parents=True)
    skill_content = "print('ok')\n".encode("utf-8")
    (source / "skill.py").write_bytes(skill_content)
    (source / "nested" / "asset.bin").write_bytes(b"\x00\x01asset")
    client = _FakeAdapterClient()

    manifest = await upload_directory(client, source, "skills/generated")

    assert manifest == {
        "root": "skills/generated",
        "files": [
            {"path": "skills/generated/nested/asset.bin", "size": 7},
            {"path": "skills/generated/skill.py", "size": len(skill_content)},
        ],
    }
    assert client.writes == [
        {
            "path": "skills/generated/nested/asset.bin",
            "content_base64": base64.b64encode(b"\x00\x01asset").decode("ascii"),
        },
        {
            "path": "skills/generated/skill.py",
            "content_base64": base64.b64encode(skill_content).decode("ascii"),
        },
    ]


@pytest.mark.asyncio
async def test_upload_directory_rejects_remote_path_escape(tmp_path):
    source = tmp_path / "generated_skill"
    source.mkdir()
    (source / "skill.py").write_text("print('ok')", encoding="utf-8")

    with pytest.raises(ValueError, match="remote_root must be a relative adapter workspace path"):
        await upload_directory(_FakeAdapterClient(), source, "../outside")


@pytest.mark.asyncio
async def test_upload_directory_requires_existing_source_directory(tmp_path):
    with pytest.raises(ValueError, match="source_dir must be an existing directory"):
        await upload_directory(_FakeAdapterClient(), tmp_path / "missing", "skills/generated")


@pytest.mark.asyncio
async def test_upload_directory_rejects_oversized_file_before_reading(tmp_path, monkeypatch):
    source = tmp_path / "generated_skill"
    source.mkdir()
    (source / "large.bin").write_bytes(b"abcd")
    monkeypatch.setattr(adapter_workspace, "MAX_UPLOAD_FILE_BYTES", 3)
    client = _FakeAdapterClient()

    with pytest.raises(ValueError, match="source file exceeds adapter upload limit"):
        await upload_directory(client, source, "skills/generated")

    assert client.writes == []


@pytest.mark.asyncio
async def test_run_uploaded_skill_uploads_runs_and_downloads_outputs(tmp_path):
    source = tmp_path / "generated_skill"
    source.mkdir()
    skill_content = "print('ok')\n".encode("utf-8")
    (source / "skill.py").write_bytes(skill_content)
    download_dir = tmp_path / "host_downloads"
    client = _FakeAdapterClient()

    result = await run_uploaded_skill(
        client,
        source,
        "skills/generated",
        args=["--query", "science"],
        timeout_seconds=9,
        download_outputs_to=download_dir,
    )

    assert client.writes == [
        {
            "path": "skills/generated/skill.py",
            "content_base64": base64.b64encode(skill_content).decode("ascii"),
        }
    ]
    assert client.run_payloads == [
        {
            "skill_path": "skills/generated",
            "args": ["--query", "science"],
            "timeout_seconds": 9,
        }
    ]
    assert result["upload"]["root"] == "skills/generated"
    assert result["run"]["status"] == "success"
    assert result["downloads"] == [
        {
            "name": "report.txt",
            "path": "downloads/report.txt",
            "size": 9,
            "local_path": str(download_dir / "report.txt"),
        }
    ]
    assert (download_dir / "report.txt").read_bytes() == b"report-ok"


@pytest.mark.asyncio
async def test_run_uploaded_skill_can_pass_local_after_snapshot(tmp_path):
    source = tmp_path / "generated_skill"
    source.mkdir()
    (source / "skill.py").write_text("print('ok')", encoding="utf-8")
    client = _FakeAdapterClient()
    after_snapshot = {
        "page_state": {"url": "https://example.test/done", "title": "Done"},
        "compact_snapshot": {"status": "done"},
    }

    await run_uploaded_skill(
        client,
        source,
        "skills/generated",
        after_snapshot=after_snapshot,
    )

    assert client.run_payloads == [
        {
            "skill_path": "skills/generated",
            "after_snapshot": after_snapshot,
        }
    ]


@pytest.mark.asyncio
async def test_run_uploaded_skill_rejects_download_target_inside_source_dir(tmp_path):
    source = tmp_path / "generated_skill"
    source.mkdir()
    (source / "skill.py").write_text("print('ok')", encoding="utf-8")

    with pytest.raises(ValueError, match="download_outputs_to must not be inside source_dir"):
        await run_uploaded_skill(
            _FakeAdapterClient(),
            source,
            "skills/generated",
            download_outputs_to=source / "downloads",
        )


@pytest.mark.asyncio
async def test_run_uploaded_skill_rejects_unsafe_download_name(tmp_path):
    source = tmp_path / "generated_skill"
    source.mkdir()
    (source / "skill.py").write_text("print('ok')", encoding="utf-8")
    client = _FakeAdapterClient()
    client.downloads = {
        "downloads": [
            {"name": "../escape.txt", "path": "downloads/escape.txt", "size": 6},
        ]
    }

    with pytest.raises(ValueError, match="download name must be a plain filename"):
        await run_uploaded_skill(
            client,
            source,
            "skills/generated",
            download_outputs_to=tmp_path / "host_downloads",
        )


@pytest.mark.asyncio
async def test_run_uploaded_skill_rejects_unsafe_download_path(tmp_path):
    source = tmp_path / "generated_skill"
    source.mkdir()
    (source / "skill.py").write_text("print('ok')", encoding="utf-8")
    client = _FakeAdapterClient()
    client.downloads = {
        "downloads": [
            {"name": "report.txt", "path": "../secret.txt", "size": 6},
        ]
    }

    with pytest.raises(ValueError, match="download path must be a relative adapter workspace path"):
        await run_uploaded_skill(
            client,
            source,
            "skills/generated",
            download_outputs_to=tmp_path / "host_downloads",
        )


@pytest.mark.asyncio
async def test_run_uploaded_skill_rejects_download_sha256_mismatch(tmp_path):
    source = tmp_path / "generated_skill"
    source.mkdir()
    (source / "skill.py").write_text("print('ok')", encoding="utf-8")
    client = _FakeAdapterClient()
    client.downloads = {
        "downloads": [
            {
                "name": "report.txt",
                "path": "downloads/report.txt",
                "sha256": "0" * 64,
                "size": 9,
            },
        ]
    }

    with pytest.raises(ValueError, match="download sha256 mismatch"):
        await run_uploaded_skill(
            client,
            source,
            "skills/generated",
            download_outputs_to=tmp_path / "host_downloads",
        )


@pytest.mark.asyncio
async def test_run_uploaded_skill_skips_oversized_download_without_fetching(tmp_path):
    source = tmp_path / "generated_skill"
    source.mkdir()
    (source / "skill.py").write_text("print('ok')", encoding="utf-8")
    download_dir = tmp_path / "host_downloads"
    client = _FakeAdapterClient()
    client.downloads = {
        "downloads": [
            {
                "name": "large.bin",
                "path": "downloads/large.bin",
                "sha256": None,
                "size": 52428801,
                "hash_status": "skipped_oversized",
            },
        ]
    }
    client.download_bytes = {}

    result = await run_uploaded_skill(
        client,
        source,
        "skills/generated",
        download_outputs_to=download_dir,
    )

    assert result["downloads"] == [
        {
            "name": "large.bin",
            "path": "downloads/large.bin",
            "sha256": None,
            "size": 52428801,
            "hash_status": "skipped_oversized",
            "download_status": "skipped_oversized",
        }
    ]
    assert list(download_dir.iterdir()) == []
