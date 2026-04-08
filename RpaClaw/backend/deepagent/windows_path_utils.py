from __future__ import annotations

import re
from pathlib import PureWindowsPath


_WINDOWS_ABSOLUTE_RE = re.compile(r"^[a-zA-Z]:[/\\]")
_WINDOWS_DRIVE_RE = re.compile(r"^(?P<drive>[a-zA-Z]):(?P<rest>/.*)$")


def canonicalize_local_agent_path(path: str) -> str:
    if not path or not _WINDOWS_ABSOLUTE_RE.match(path):
        raise ValueError(f"Local mode requires a Windows absolute path: {path}")

    if "~" in path:
        raise ValueError(f"Path traversal not allowed: {path}")

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


def normalize_presented_local_path(path: str | None) -> str | None:
    if path is None:
        return None

    if _WINDOWS_ABSOLUTE_RE.match(path):
        return canonicalize_local_agent_path(path)

    return path.replace("\\", "/")
