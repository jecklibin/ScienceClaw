# Windows Desktop Tool Library Compatibility Design

## Background

The current external tool flow assumes a Linux-style permanent tool directory at `/app/Tools`.
That assumption is valid in Docker and shared sandbox deployments, but it breaks in the Windows desktop app:

- the desktop runtime injects `RPA_CLAW_HOME`, `WORKSPACE_DIR`, `EXTERNAL_SKILLS_DIR`, `LOCAL_DATA_DIR`, and `BUILTIN_SKILLS_DIR`, but not `TOOLS_DIR`
- the desktop home directory initializer creates `workspace`, `external_skills`, `data`, and `logs`, but not a persistent tool library directory
- `save_tool_from_session()` copies directly into `_TOOLS_DIR` without ensuring the destination directory exists
- external tool discovery currently depends on importing the top-level `Tools` Python package
- external tool execution currently proxies through `SANDBOX_BASE_URL`, which does not fit desktop `STORAGE_BACKEND=local`

The result is that tool creation can appear to work until the final save step, where the desktop app raises `FileNotFoundError: [WinError 3]`.

This is not just a missing-directory bug. The desktop app is missing a complete, first-class model for persistent external tools across save, discovery, loading, and execution.

## Goals

- Make the Windows desktop app fully support custom tool creation, saving, discovery, loading, and invocation.
- Preserve current Docker and shared sandbox behavior without changing the existing `/app/Tools` contract inside the sandbox.
- Decouple host-side tool storage from sandbox-side tool mount paths.
- Remove the packaging dependency on the repository-root `Tools` package for runtime correctness.
- Keep the agent-facing workflow unchanged: tools are developed in session workspace, staged in `tools_staging/`, then saved permanently after user confirmation.

## Non-Goals

- Changing the user-facing tool creation workflow in chat.
- Making saved tools hot-reload inside the already-running current session if current architecture intentionally only guarantees availability in new sessions.
- Introducing a database-backed tool library for local mode.
- Refactoring built-in skills, skill storage, or RPA storage beyond what is needed for tool compatibility.

## Current Problems

### 1. Storage path is hard-coded

Tool management routes read `TOOLS_DIR` directly and default to `/app/Tools`. This bypasses the main settings model used by workspace and skills.

### 2. Host path and sandbox path are conflated

`/app/Tools` is both:

- treated as the host-side permanent tool library path
- treated as the sandbox-side execution path

Those are only the same in containerized deployments. They are not the same on Windows desktop.

### 3. Discovery depends on a top-level Python package

`backend.deepagent.agent` imports `reload_external_tools` from the repository-root `Tools` package. That couples runtime behavior to:

- repository layout
- Python import path shape
- packaging decisions

This is brittle for desktop packaging and unnecessary for backend-owned functionality.

### 4. Execution is sandbox-only

The current proxy tool implementation posts to `SANDBOX_BASE_URL` and executes tool files from `/app/Tools`. In local desktop mode, external tools need a host-local execution path.

### 5. Desktop runtime does not provision persistent tool storage

The desktop startup path creates the home directory tree for workspace, skills, data, and logs, but not tools.

## Design Principles

- One canonical host-side tool library path per deployment.
- One explicit sandbox-side mount path for sandbox execution.
- Backend owns external tool discovery and execution adaptation.
- Local mode and sandbox mode share the same user workflow and metadata, but not the same executor implementation.
- Docker compatibility is preserved by keeping `/app/Tools` as the sandbox-visible tool path.

## Options Considered

### Option A: Minimal patch

Add `TOOLS_DIR` to desktop env, create the missing directory, and keep importing the top-level `Tools` package.

Pros:

- smallest code change
- fixes the immediate save failure

Cons:

- execution path remains sandbox-centric
- runtime still depends on top-level package layout
- desktop packaging still needs special handling for repository-root `Tools`
- future maintenance remains fragile

### Option B: Backend-owned tool library model with dual executors

Move external tool loading logic into backend modules, add explicit host and sandbox tool paths, and select local or sandbox execution based on `storage_backend`.

Pros:

- solves save, discovery, and execution together
- removes packaging-specific Python import coupling
- keeps Docker behavior intact
- clearer architecture boundaries

Cons:

- larger change than a minimal fix
- requires test updates across desktop and sandbox paths

### Option C: Database-backed tool registry plus file materialization

Persist tool source and metadata in database or local storage abstraction, then materialize files into runtime-specific directories before use.

Pros:

- future-proof for multi-tenant or sync scenarios
- central metadata model

Cons:

- much larger scope than required
- unnecessary for the current local desktop objective
- adds migration complexity now without solving a pressing product need

### Recommendation

Use Option B.

It is the smallest change set that actually fixes the whole tool lifecycle while preserving current Docker and sandbox behavior.

## Proposed Architecture

### 1. Introduce explicit tool path settings

Add two settings:

- `tools_dir`: host-side persistent tool library
- `sandbox_tools_dir`: sandbox-visible tool directory

Default behavior:

- Docker / current sandbox deployments
  - `tools_dir = TOOLS_DIR or /app/Tools`
  - `sandbox_tools_dir = SANDBOX_TOOLS_DIR or /app/Tools`
- Windows desktop
  - `tools_dir = {RPA_CLAW_HOME}/tools`
  - `sandbox_tools_dir = /app/Tools`

Key rule:

- `tools_dir` is where the backend reads and writes permanent tool files
- `sandbox_tools_dir` is only used when generating sandbox execution commands or mounting tool files into a sandbox

### 2. Move external tool loading into backend

Create backend-owned modules:

- `backend/deepagent/external_tools_loader.py`
- `backend/deepagent/tool_execution.py`

Responsibilities:

- scan `settings.tools_dir`
- parse tool Python files with AST
- build LangChain `StructuredTool` proxies
- choose the correct executor for local or sandbox mode

`agent.py` should stop importing `reload_external_tools` from the repository-root `Tools` package and instead use the backend loader directly.

This removes the need for the packaged desktop runtime to import a top-level `Tools` module.

### 3. Split execution into local and sandbox executors

Create two executors behind a small shared interface.

#### LocalToolExecutor

Used when `settings.storage_backend == "local"`.

Behavior:

- execute the saved tool file from `settings.tools_dir`
- run with the desktop bundled Python runtime and local filesystem semantics
- return the same structured result shape expected by the agent

Implementation note:

- this should not depend on `SANDBOX_BASE_URL`
- it can execute via a local Python subprocess or a local shell backend helper
- it should keep the current result-envelope semantics so agent and SSE behavior remain stable

#### SandboxToolExecutor

Used when `settings.storage_backend != "local"`.

Behavior:

- keep using sandbox REST shell execution
- execute the tool file from `settings.sandbox_tools_dir`
- preserve the `/app/Tools/<tool>.py` convention by default

This keeps existing Docker and shared runtime behavior unchanged.

### 4. Make tool management routes use settings

Update tool management endpoints in `backend/route/sessions.py` to use `settings.tools_dir` rather than a module-level `os.environ.get("TOOLS_DIR", "/app/Tools")`.

Operations affected:

- list tools
- read tool file
- save staged tool
- delete or replace old tool
- auto-detect saved vs staged tools

Behavior changes:

- ensure `tools_dir` exists before saving
- return better 4xx or 5xx errors for invalid path, missing directory, permission issues, or malformed tool files
- keep overwrite semantics unchanged unless later product requirements say otherwise

### 5. Make the directory watcher configurable

`dir_watcher.py` and any call sites should watch `settings.tools_dir`, not a hard-coded `/app/Tools`.

This preserves hot-reload behavior where it already exists and keeps desktop mode consistent with Docker mode.

### 6. Provision desktop persistent tool storage

Update Electron runtime and home initialization:

- inject `TOOLS_DIR = path.join(homeDir, "tools")`
- create `homeDir/tools` during first-run initialization

Desktop home directory tree becomes:

- `workspace/`
- `external_skills/`
- `tools/`
- `data/`
- `logs/`

### 7. Preserve sandbox mounts for Docker and runtime-managed sandboxes

Containerized deployments should continue to expose the host tool library inside the sandbox at `/app/Tools`.

Implications:

- existing Docker Compose mounts remain valid
- runtime-manager-based sandboxes should continue mounting the host-side tool library to `sandbox_tools_dir`
- tests that assert mounts to `/app/Tools` should remain green

This keeps the existing tool-creator skill instructions broadly true for sandbox execution while allowing the host-side storage path to differ by environment.

### 8. Update tool-creator documentation and prompts

The current skill text hard-codes `/app/Tools` as the permanent tool directory.

Revise language to distinguish:

- permanent tool library on the host, managed by the backend
- sandbox-visible mount path, typically `/app/Tools`

The user workflow should remain:

1. develop in `tools_dev/`
2. copy exact tested file to `tools_staging/`
3. call `propose_tool_save`
4. backend persists the file to the permanent tool library

## Detailed Data Flow

### Desktop local mode

1. Agent creates or updates `workspace/{session_id}/tools_dev/{tool_name}.py`
2. Agent tests that same `@tool` implementation
3. Agent copies the approved file into `workspace/{session_id}/tools_staging/{tool_name}.py`
4. Agent calls `propose_tool_save`
5. Frontend asks the user for confirmation
6. `save_tool_from_session()` copies the staged file into `{RPA_CLAW_HOME}/tools/{tool_name}.py`
7. Backend external tool loader scans `{RPA_CLAW_HOME}/tools`
8. New sessions expose the tool through `LocalToolExecutor`

### Docker / sandbox mode

1. Agent creates and tests the tool in session workspace
2. Staged file is confirmed for saving
3. `save_tool_from_session()` copies the file into host `tools_dir`
4. Host `tools_dir` is mounted into the sandbox at `/app/Tools`
5. Backend external tool loader scans host `tools_dir`
6. New sessions expose the tool through `SandboxToolExecutor`
7. Executor calls into sandbox and runs `/app/Tools/{tool_name}.py`

## Migration Strategy

### Desktop

No automatic migration is required for existing released builds because persistent tools do not currently work correctly there.

On upgrade:

- create `{RPA_CLAW_HOME}/tools` if missing
- start using it immediately as the canonical tool library

### Docker and existing local source development

No path migration is required if current deployments already use `/app/Tools` or mounted `./Tools`.

The main change is internal:

- the backend reads configured `tools_dir`
- the sandbox still sees `/app/Tools`

## Error Handling

### Save-time errors

Return explicit failures for:

- invalid tool names
- missing staged file
- missing `@tool` decorator
- destination directory creation failure
- destination write failure

### Load-time errors

If a saved tool file cannot be parsed:

- skip it from the active tool registry
- log the reason with file path
- surface enough metadata in tool list APIs for debugging if desired later

### Execute-time errors

Local and sandbox executors should normalize failures to the same structured result shape so the agent and frontend do not need environment-specific handling.

## Testing Strategy

### Backend unit tests

- config tests for `tools_dir` and `sandbox_tools_dir` resolution
- sessions route tests for save/list/read using temporary tool directories
- loader tests for AST parsing against files outside the repository-root `Tools` package
- local executor tests
- sandbox executor tests
- watcher tests using configurable tool directory paths

### Desktop tests

- Electron runtime env test includes `TOOLS_DIR`
- Electron home initializer creates `tools`
- packaged-runtime tests ensure tool-related env values resolve under `homeDir`

### Integration tests

- desktop local mode
  - create tool in conversation
  - save successfully
  - list and read successfully
  - start a new session and invoke the tool successfully
  - restart app and confirm persistence
- Docker / sandbox mode
  - save tool successfully
  - new session loads tool
  - sandbox execution still runs from `/app/Tools`

## Rollout Plan

### Phase 1

- introduce settings for host and sandbox tool paths
- add desktop `TOOLS_DIR` provisioning
- move loader logic into backend
- switch route handlers to `settings.tools_dir`

### Phase 2

- implement local and sandbox executors
- update agent integration to use backend loader
- update watcher logic

### Phase 3

- update docs and tool-creator skill text
- expand test coverage
- run desktop and Docker regression validation

## Risks

### 1. Divergent behavior between local and sandbox execution

Mitigation:

- keep both executors behind one interface
- normalize output and error envelopes
- run the same high-level tool lifecycle tests in both modes

### 2. Packaging regressions from import-path changes

Mitigation:

- move runtime-critical logic under `backend/`
- reduce reliance on repository-root Python packages
- add packaged desktop tests for env and startup paths

### 3. Hidden assumptions in tool-creator skill instructions

Mitigation:

- update skill docs to separate host persistence from sandbox path
- keep `/app/Tools` unchanged inside sandbox mode so the existing conceptual model mostly still holds

## Acceptance Criteria

- Windows desktop users can create, save, list, read, and invoke custom tools without manual directory setup.
- Saved desktop tools persist under the user home directory and survive application restarts.
- Docker and shared sandbox deployments continue using `/app/Tools` inside the sandbox without behavioral regression.
- External tool discovery no longer depends on importing the repository-root `Tools` package.
- Local mode external tool invocation no longer depends on `SANDBOX_BASE_URL`.
