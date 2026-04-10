from __future__ import annotations

import platform
from typing import Any


def local_mode_uses_windows_paths(
    storage_backend: str | None,
    *,
    system_name: str | None = None,
) -> bool:
    return storage_backend == "local" and (system_name or platform.system()) == "Windows"


def adapt_local_backend_for_platform(
    backend: Any,
    *,
    storage_backend: str | None,
    system_name: str | None = None,
) -> Any:
    if local_mode_uses_windows_paths(storage_backend, system_name=system_name):
        from backend.deepagent.windows_local_path_backend import WindowsLocalPathBackend

        return WindowsLocalPathBackend(backend)
    return backend
