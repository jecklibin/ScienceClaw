# Design Document Status

This repository contains many design notes and implementation plans under `docs/superpowers`. They are useful history, but they are not all current architecture.

Use this order of authority:

1. Source code and tests.
2. `README.md`, `AGENTS.md`, and `CLAUDE.md`.
3. This status file and status blocks at the top of individual design docs.
4. Historical specs/plans under `docs/superpowers`.

## Current Or Implemented

These designs match important parts of the current codebase, though details should still be checked against code:

- `docs/superpowers/specs/2026-04-20-rpa-trace-first-recording-design.md`
  - Current baseline direction: trace-first recording, Python Playwright scripts, post-recording compilation.
- `docs/superpowers/specs/2026-04-24-rpa-manual-recording-single-source-design.md`
  - Implemented with compatibility: `recorded_actions` and `recording_diagnostics` exist, while legacy `steps` and `traces` remain for UI/API/compiler compatibility.
- `docs/superpowers/specs/2026-04-25-rpa-structured-snapshot-design.md`
  - Implemented core pieces: snapshot v2 emits `table_views` and `detail_views`; compression and runtime prompts consume them; guarded table/ordinal lanes exist.
- `docs/superpowers/specs/2026-04-18-rpa-mcp-gateway-design.md`
- `docs/superpowers/specs/2026-04-19-rpa-mcp-tool-studio-ia-design.md`
- `docs/superpowers/specs/2026-04-20-rpa-mcp-semantic-inference-design.md`
  - Implemented as the RPA MCP converter, registry, executor, frontend conversion/editor pages, and `/api/v1/rpa-mcp/*` routes.
- `docs/superpowers/specs/2026-04-06-task-service-local-mode-design.md`
  - Implemented in task-service storage abstraction. Note that task-service uses `STORAGE_BACKEND=local` for file storage; other values use MongoDB.
- `docs/superpowers/specs/2026-03-31-edge-cloud-storage-design.md`
  - Implemented as backend repository abstraction. Current backend code uses `STORAGE_BACKEND=local` for file storage and treats other values as MongoDB-backed.

## Superseded Or Not Implemented

These documents should not be used as current architecture without a fresh redesign:

- `docs/superpowers/specs/2026-04-09-rpa-node-engine-design.md`
  - Superseded. The current RPA runtime is Python/Playwright-led with vendored recorder runtime pieces, not a separate Node `rpa-engine` service.
- Older recorder-v1/v2, CRX-alignment, and early CDP docs from March/early April.
  - Historical context only. Current RPA facts are in `backend/rpa/*`, `AGENTS.md`, and the newer trace-first/manual-action/structured-snapshot docs.

## Reading Guidance

- If a design mentions replacing the Python RPA runtime with Node, treat it as superseded.
- If a design says `STORAGE_BACKEND=local|mongo`, verify the exact current behavior in `backend/config.py`, `backend/storage/__init__.py`, and task-service config. Current backend code uses `local` specially and treats other values as MongoDB-backed.
- If a design describes both `steps` and `traces` as durable truths for manual recording, use the newer `recorded_actions` model and compatibility notes instead.
- If a plan under `docs/superpowers/plans` contains code snippets, treat them as implementation instructions from that date, not as source code.
