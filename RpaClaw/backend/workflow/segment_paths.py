from __future__ import annotations

from pathlib import PurePosixPath


def normalize_script_segment_entry(segment_id: str, entry: str | None) -> str:
    default_entry = f"segments/{segment_id}_script.py"
    raw = str(entry or "").strip().replace("\\", "/")
    if not raw:
        return default_entry

    try:
        path = PurePosixPath(raw)
    except Exception:
        return default_entry

    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts:
        return default_entry
    if path.is_absolute() or any(part == ".." for part in parts):
        return default_entry

    filename = parts[-1]
    if not filename.endswith(".py"):
        return default_entry

    if parts[0] != "segments":
        return str(PurePosixPath("segments", filename))
    return str(PurePosixPath(*parts))
