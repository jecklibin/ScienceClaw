from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath


_WINDOWS_ABSOLUTE_RE = re.compile(r"^[a-zA-Z]:[/\\]")
_WINDOWS_DRIVE_RE = re.compile(r"^(?P<drive>[a-zA-Z]):(?P<rest>/.*)$")


def _canonicalize_posix_absolute_path(path: str) -> str:
    if not path.startswith("/"):
        raise ValueError(f"Local mode requires an absolute path: {path}")

    normalized = path.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")

    parts = PurePosixPath(normalized).parts
    if ".." in parts or "~" in normalized:
        raise ValueError(f"Path traversal not allowed: {path}")

    return normalized


def _canonicalize_windows_absolute_path(path: str) -> str:
    if not path:
        raise ValueError(f"Local mode requires an absolute path: {path}")

    if "~" in path:
        raise ValueError(f"Path traversal not allowed: {path}")

    if not _WINDOWS_ABSOLUTE_RE.match(path):
        raise ValueError(f"Local mode requires a Windows absolute path: {path}")

    parts = PureWindowsPath(path).parts
    if ".." in parts:
        raise ValueError(f"Path traversal not allowed: {path}")

    normalized = path.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")

    match = _WINDOWS_DRIVE_RE.match(normalized)
    if not match:
        raise ValueError(f"Local mode requires a Windows absolute path: {path}")

    drive = match.group("drive").upper()
    rest = match.group("rest")
    return f"{drive}:{rest}"


def canonicalize_local_agent_path(path: str, path_style: str = "windows") -> str:
    if path_style == "windows":
        return _canonicalize_windows_absolute_path(path)
    if path_style == "posix":
        return _canonicalize_posix_absolute_path(path)
    raise ValueError(f"Unsupported local path style: {path_style}")


def normalize_presented_local_path(path: str | None, path_style: str = "windows") -> str | None:
    if path is None:
        return None

    if path_style == "windows":
        if _WINDOWS_ABSOLUTE_RE.match(path):
            return canonicalize_local_agent_path(path, path_style=path_style)
        return path.replace("\\", "/")

    if path_style == "posix":
        if path.startswith("/"):
            return canonicalize_local_agent_path(path, path_style=path_style)
        return path.replace("\\", "/")

    raise ValueError(f"Unsupported local path style: {path_style}")
