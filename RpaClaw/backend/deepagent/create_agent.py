from __future__ import annotations

from contextlib import contextmanager

import deepagents.graph as deepagents_graph

from backend.deepagent.local_filesystem_middleware import LocalFilesystemMiddleware


@contextmanager
def _patched_filesystem_middleware(use_local_filesystem_paths: bool):
    if not use_local_filesystem_paths:
        yield
        return

    original = deepagents_graph.FilesystemMiddleware
    deepagents_graph.FilesystemMiddleware = LocalFilesystemMiddleware
    try:
        yield
    finally:
        deepagents_graph.FilesystemMiddleware = original


def create_rpaclaw_deep_agent(*, use_local_filesystem_paths: bool = False, **kwargs):
    with _patched_filesystem_middleware(use_local_filesystem_paths):
        return deepagents_graph.create_deep_agent(**kwargs)
