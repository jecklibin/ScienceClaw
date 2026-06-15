from __future__ import annotations

import base64
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from backend.runtime.adapter_file_policy import MAX_INLINE_FILE_WRITE_BYTES


MAX_UPLOAD_FILE_BYTES = MAX_INLINE_FILE_WRITE_BYTES


def _normalize_remote_root(remote_root: str) -> PurePosixPath:
    root = PurePosixPath(str(remote_root or "").replace("\\", "/"))
    if root.is_absolute() or not root.parts or any(part in {"", ".", ".."} for part in root.parts):
        raise ValueError("remote_root must be a relative adapter workspace path")
    return root


def _remote_child_path(remote_root: PurePosixPath, relative_path: Path) -> str:
    remote = remote_root
    for part in relative_path.parts:
        if part in {"", ".", ".."}:
            raise ValueError("source_dir contains an invalid relative path")
        remote /= part
    return remote.as_posix()


def _is_relative_to(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _validate_download_target(source_dir: Path, download_outputs_to: str | Path | None) -> Path | None:
    if download_outputs_to is None:
        return None
    target = Path(download_outputs_to).resolve()
    source = source_dir.resolve()
    if _is_relative_to(target, source):
        raise ValueError("download_outputs_to must not be inside source_dir")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _validate_download_name(name: str) -> str:
    candidate = PurePosixPath(name.replace("\\", "/"))
    if candidate.is_absolute() or len(candidate.parts) != 1 or candidate.name in {"", ".", ".."}:
        raise ValueError("download name must be a plain filename")
    return candidate.name


def _validate_download_path(path: str) -> str:
    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("download path must be a relative adapter workspace path")
    return candidate.as_posix()


def _validate_download_sha256(expected: Any, content: bytes) -> None:
    if expected is None:
        return
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("download sha256 must be a lowercase hex digest")
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ValueError("download sha256 mismatch")


async def upload_directory(adapter_client: Any, source_dir: str | Path, remote_root: str) -> dict[str, Any]:
    """Upload a local directory into a runtime adapter workspace.

    The Host owns which files are staged. The adapter only receives workspace
    writes; it does not infer Skill semantics or product facts from them.
    """

    source = Path(source_dir)
    if not source.exists() or not source.is_dir():
        raise ValueError("source_dir must be an existing directory")
    root = _normalize_remote_root(remote_root)

    uploaded: list[dict[str, Any]] = []
    local_files = (item for item in source.rglob("*") if item.is_file())
    for path in sorted(local_files, key=lambda item: item.as_posix()):
        remote_path = _remote_child_path(root, path.relative_to(source))
        size = path.stat().st_size
        if size > MAX_UPLOAD_FILE_BYTES:
            raise ValueError(f"source file exceeds adapter upload limit: {remote_path}")
        content = path.read_bytes()
        await adapter_client.write_file_base64(
            remote_path,
            base64.b64encode(content).decode("ascii"),
        )
        uploaded.append({"path": remote_path, "size": len(content)})

    return {
        "root": root.as_posix(),
        "files": uploaded,
    }


async def run_uploaded_skill(
    adapter_client: Any,
    source_dir: str | Path,
    remote_root: str,
    *,
    args: list[str] | None = None,
    timeout_seconds: int | None = None,
    after_snapshot: dict[str, Any] | None = None,
    download_outputs_to: str | Path | None = None,
) -> dict[str, Any]:
    """Upload a Skill directory, run it in the adapter, and optionally fetch downloads."""

    source = Path(source_dir)
    download_dir = _validate_download_target(source, download_outputs_to)
    upload = await upload_directory(adapter_client, source, remote_root)

    run_payload: dict[str, Any] = {
        "skill_path": upload["root"],
    }
    if args is not None:
        run_payload["args"] = list(args)
    if timeout_seconds is not None:
        run_payload["timeout_seconds"] = int(timeout_seconds)
    if after_snapshot is not None:
        run_payload["after_snapshot"] = after_snapshot
    run = await adapter_client.run_skill(run_payload)

    download_listing = await adapter_client.list_downloads()
    downloaded: list[dict[str, Any]] = []
    for item in download_listing.get("downloads") or []:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        name = item.get("name")
        size = item.get("size")
        if not isinstance(path, str) or not isinstance(name, str) or not isinstance(size, int):
            continue
        safe_path = _validate_download_path(path)
        entry = dict(item)
        if download_dir is not None:
            if item.get("hash_status") == "skipped_oversized":
                entry["download_status"] = "skipped_oversized"
                downloaded.append(entry)
                continue
            filename = _validate_download_name(name)
            content = await adapter_client.download_file(safe_path)
            _validate_download_sha256(item.get("sha256"), content)
            local_path = download_dir / filename
            local_path.write_bytes(content)
            entry["local_path"] = str(local_path)
        downloaded.append(entry)

    return {
        "upload": upload,
        "run": run,
        "downloads": downloaded,
    }


__all__ = ["run_uploaded_skill", "upload_directory"]
