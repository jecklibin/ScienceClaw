from __future__ import annotations

from typing import Any


def sanitize_workflow_artifact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove recording-time local paths from runtime download artifacts."""
    sanitized = dict(payload)
    if not _is_runtime_download_artifact_payload(sanitized):
        return sanitized

    sanitized["path"] = None
    value = sanitized.get("value")
    if isinstance(value, dict):
        value = dict(value)
        value.pop("recorded_path", None)
        value.pop("path", None)
        value["runtime"] = str(value.get("runtime") or "downloads_dir")
        sanitized["value"] = value
    return sanitized


def _is_runtime_download_artifact_payload(payload: dict[str, Any]) -> bool:
    labels = payload.get("labels")
    if isinstance(labels, list) and "runtime-download" in labels:
        return True
    value = payload.get("value")
    if isinstance(value, dict) and value.get("runtime") == "downloads_dir":
        return True
    path = str(payload.get("path") or "")
    return "playwright-artifacts-" in path
