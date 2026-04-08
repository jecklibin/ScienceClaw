from __future__ import annotations

from contextlib import contextmanager

import deepagents.graph as deepagents_graph

from backend.deepagent.windows_filesystem_middleware import WindowsFilesystemMiddleware


@contextmanager
def _patched_filesystem_middleware(local_windows_paths: bool):
    if not local_windows_paths:
        yield
        return

    original = deepagents_graph.FilesystemMiddleware
    deepagents_graph.FilesystemMiddleware = WindowsFilesystemMiddleware
    try:
        yield
    finally:
        deepagents_graph.FilesystemMiddleware = original


def create_rpaclaw_deep_agent(*, local_windows_paths: bool = False, **kwargs):
    with _patched_filesystem_middleware(local_windows_paths):
        return deepagents_graph.create_deep_agent(**kwargs)
