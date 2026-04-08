# Local Windows DeepAgent Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `STORAGE_BACKEND=local` accept and preserve Windows absolute paths in deepagent filesystem tools without modifying upstream deepagents source.

**Architecture:** Keep cloud mode unchanged. In local mode only, normalize tool input paths to canonical `D:/...` form, wrap `LocalPreviewShellBackend` to normalize returned paths, and patch deepagents agent assembly so it uses a project-local Windows-aware filesystem middleware.

**Tech Stack:** Python 3.13, deepagents middleware/backend protocol, LocalPreviewShellBackend, unittest

---

## Implementation Summary

### Task 1: Windows path normalization
- Add `backend/deepagent/windows_path_utils.py`
- Accept `D:\...` and `D:/...`
- Canonicalize to `D:/...`
- Reject relative paths and traversal

### Task 2: Local backend wrapper
- Add `backend/deepagent/windows_local_path_backend.py`
- Wrap `LocalPreviewShellBackend`
- Normalize incoming filesystem paths
- Normalize returned `FileInfo`, `WriteResult`, `EditResult`, grep matches, upload/download response paths
- Preserve execute support by delegating `SandboxBackendProtocol`

### Task 3: Windows-aware filesystem middleware
- Add `backend/deepagent/windows_filesystem_middleware.py`
- Subclass deepagents `FilesystemMiddleware`
- Replace path validation in `ls`, `read_file`, `write_file`, `edit_file`, and `glob`
- Override the filesystem prompt so local mode explicitly uses Windows absolute paths

### Task 4: Local agent assembly hook
- Add `backend/deepagent/create_agent.py`
- Patch `deepagents.graph.FilesystemMiddleware` only during `create_deep_agent()` assembly in local mode
- Avoid copying upstream `create_deep_agent()` implementation

### Task 5: Wire local mode
- Update `backend/deepagent/agent.py`
- In local mode, wrap `LocalPreviewShellBackend` with `WindowsLocalPathBackend`
- Use canonical forward-slash workspace path in prompts and offload paths
- Route both main agent and eval agent creation through the project-local factory

### Task 6: Verification
- Add unit tests under `backend/tests/deepagent/`
- Verify path normalization, backend wrapper behavior, and middleware validator behavior
- Run unittest suite for the new modules
- Run compile check for the new modules
