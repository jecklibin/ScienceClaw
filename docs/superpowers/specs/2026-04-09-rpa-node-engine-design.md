# RPA Node Engine Design

> Status: Superseded / not implemented as the current architecture.
>
> This document is historical context. The current RPA runtime remains Python/Playwright-led in `RpaClaw/backend/rpa/*`, with vendored recorder runtime pieces where useful. Do not use this document as authority for current runtime ownership, process topology, or implementation tasks.

## Summary

The current RPA recorder is implemented as a Python-driven Playwright session plus a custom injected recorder script. In practice, this design still suffers from two structural problems:

- locator generation is only Playwright-like, not Playwright-native, so recorded selectors often diverge from what replay actually resolves
- frame context is not modeled as a first-class runtime boundary, so actions inside `iframe` documents are either missed, degraded, or replayed nondeterministically

This design replaces the recorder and replay runtime with a dedicated Node-based `rpa-engine` that uses Playwright recorder semantics as the authoritative source of actions, selectors, frames, tabs, and runtime signals.

The Python backend remains the product-facing orchestration layer. It continues to own authentication, session persistence, API compatibility, skill export packaging, and frontend integration.

The resulting architecture is:

- `rpa-engine`: browser runtime, recorder pipeline, selector generation, frame-aware action modeling, replay, validation, screencast
- FastAPI backend: session persistence, user-facing APIs, gateway/proxy, AI orchestration, skill export
- existing Vue frontend: preserved routes and pages, consuming the same backend API surface with compatible payloads

## Design Goals

- Make recording and replay use the same underlying Playwright semantics
- Make `iframe` and nested frame interactions first-class and deterministic
- Model popup/new-tab behavior explicitly instead of inferring it after the fact
- Preserve existing frontend routes and the current `/api/v1/rpa/*` product API surface as much as possible
- Support local mode with a same-machine engine process and cloud mode with separate engine deployment
- Keep skill export in the Python product layer while sourcing generated code from the new engine
- Enable gradual migration from the current Python-owned runtime without breaking the product shell

## Non-Goals

- Preserving the current Python recorder internals
- Maintaining two long-term recorder/replay implementations in parallel
- Embedding Playwright CRX UI or extension UX directly into the product
- Reproducing the Playwright Inspector or CRX sidepanel as a product feature
- Backward compatibility with all historical step payload quirks if they conflict with the new authoritative action model

## Why The Current Recorder Is Not Sufficient

The current design already adopted some recorder-v2 ideas such as context-wide injection, frame metadata, locator candidates, and tab-aware replay. Those improvements help, but they still leave the fundamental issue unresolved: Python owns the runtime while selector semantics are approximated by custom code.

This creates persistent correctness problems:

- locator heuristics drift from Playwright's own selector generation and resolution rules
- click classification such as navigation, popup, or download is partly inferred rather than recorded from a single authoritative runtime
- frame path capture is better than before but still treated as metadata attached to Python-managed actions, not as a native context boundary
- replay still depends on Python-generated code paths and Python runtime assumptions rather than the same recorder runtime that observed the action

The problem is therefore architectural, not just heuristic. The recording and replay runtime must move behind a single engine.

## Alternatives Considered

### 1. Keep Evolving The Python Recorder

This would continue extending `backend/rpa/manager.py`, `generator.py`, and `executor.py` with more selector heuristics and more frame/popup metadata.

Pros:

- smaller immediate code movement
- reuses the current backend runtime

Cons:

- recorder and replay semantics remain split
- locator quality still depends on custom approximation
- long-term maintenance complexity continues to accumulate in Python

### 2. Wrap `playwright-crx` As-Is

This would treat the local `playwright-crx` codebase as the runtime core and adapt it directly into the product.

Pros:

- inherits recorder experience from an existing project

Cons:

- the CRX product model is centered on extension windows, sidepanels, and recorder UI concerns that do not match this repository
- too much UI and extension behavior would be imported merely to obtain runtime semantics
- it would couple the product to a codebase that is broader than the actual need

### 3. Build A Dedicated Node `rpa-engine` Using Playwright Recorder Internals And CRX Learnings

This creates a focused service whose responsibility is only browser runtime truth and recorder/replay semantics.

Pros:

- aligns recording and replay under one runtime
- cleanly separates browser truth from product orchestration
- fits both local and cloud deployment modes
- allows selective reuse of Playwright recorder internals and `playwright-crx` lessons without adopting CRX product architecture

Cons:

- requires new process management, service contracts, and packaging
- requires deliberate extraction or vendoring of recorder-related Node modules

### Chosen Approach

Option 3 is the chosen architecture. It solves the actual structural problem while keeping the product shell stable.

## Architectural Overview

### Product Boundary

The backend remains the only public API surface for the frontend.

The frontend continues to call the existing FastAPI routes such as:

- `POST /api/v1/rpa/session/start`
- `GET /api/v1/rpa/session/{session_id}`
- `GET /api/v1/rpa/session/{session_id}/tabs`
- `POST /api/v1/rpa/session/{session_id}/tabs/{tab_id}/activate`
- `POST /api/v1/rpa/session/{session_id}/navigate`
- `POST /api/v1/rpa/session/{session_id}/step/{step_index}/locator`
- `POST /api/v1/rpa/session/{session_id}/generate`
- `POST /api/v1/rpa/session/{session_id}/test`
- `POST /api/v1/rpa/session/{session_id}/save`

Internally, these routes become a thin orchestration layer that delegates runtime work to `rpa-engine`.

### Responsibility Split

#### Node `rpa-engine`

Owns:

- Playwright browser, context, page, frame, and tab lifecycle
- recording actions using Playwright-native recorder semantics
- selector generation and selector validation
- frame-aware action modeling
- popup, download, and navigation signal capture
- screencast and input injection runtime
- test replay using the same runtime model used for recording
- code generation for skill export

Does not own:

- user authentication
- product session persistence
- skill packaging format
- frontend routing or presentation
- AI orchestration

#### FastAPI Backend

Owns:

- auth and user context
- product-facing RPA session records and persistence
- compatibility DTOs for current frontend consumers
- WebSocket fan-out to existing frontend subscriptions
- local-process engine bootstrap and health supervision
- cloud-mode engine client configuration
- skill export packaging into `SKILL.md` and `skill.py`

Does not own:

- browser runtime truth
- locator heuristics
- replay semantics

### Deployment Modes

#### Local Mode

- `rpa-engine` runs as a same-machine local process
- FastAPI lazily starts or reconnects to the engine on first RPA use
- browser processes are launched by the engine on the host machine
- the frontend still talks only to FastAPI

#### Cloud Mode

- `rpa-engine` is deployed as an independent service
- FastAPI talks to it through configured base URL and auth token
- the frontend still talks only to FastAPI

The engine API is identical in both modes. Only the transport endpoint and startup ownership change.

## Engine Process Model

`rpa-engine` should be a long-lived service, not a per-request CLI process.

Reasons:

- recording and screencast are session-oriented and need durable in-memory browser state
- replay logs and live events are easier to stream from a resident process
- local-mode startup cost and browser reuse are lower
- local and cloud behavior stay aligned under one operational model

The engine therefore keeps short-lived runtime state in memory:

- live browser instances
- browser contexts
- open pages and page aliases
- frame chains
- active tab state
- recorder event buffers
- active replay logs

The backend keeps product state:

- persisted session record
- persisted step list
- user ownership and permissions
- exported skill assets

The design rule is:

- browser truth lives in the engine
- product truth lives in the backend

## Backend To Engine Protocol

The engine should expose a simple HTTP + WebSocket contract.

### HTTP Endpoints

Recommended minimum endpoints:

- `GET /health`
- `POST /sessions`
- `GET /sessions/{id}`
- `DELETE /sessions/{id}`
- `POST /sessions/{id}/navigate`
- `POST /sessions/{id}/recording/start`
- `POST /sessions/{id}/recording/stop`
- `GET /sessions/{id}/tabs`
- `POST /sessions/{id}/tabs/{tabId}/activate`
- `POST /sessions/{id}/steps/{stepId}/locator/select`
- `POST /sessions/{id}/replay`
- `POST /sessions/{id}/codegen`

### WebSocket Endpoints

Recommended minimum streams:

- `WS /sessions/{id}/events`
- `WS /sessions/{id}/screencast`
- optional `WS /sessions/{id}/replay/logs` if replay logging is separated from generic events

### Why Not gRPC

This repository already uses REST and WebSocket product flows. HTTP + WebSocket keeps:

- local debugging simple
- backend integration straightforward
- deployment requirements minimal
- route compatibility easy for FastAPI proxying

## Unified Action Model

The engine should own a single authoritative runtime step model. The backend should persist that model and, where needed, project it into existing compatibility fields.

### RecordedAction

Each recorded step must contain at least:

- `id`
- `sessionId`
- `seq`
- `kind`
- `pageAlias`
- `framePath`
- `locator`
- `locatorAlternatives`
- `signals`
- `input`
- `timing`
- `snapshot`
- `status`

### Action Kind

`kind` describes the user action itself, not all of its side effects.

Recommended action kinds:

- `openPage`
- `navigate`
- `click`
- `fill`
- `press`
- `selectOption`
- `check`
- `uncheck`
- `closePage`

Important rule:

- do not encode side effects into separate pseudo-actions such as `navigate_click`, `open_tab_click`, or `download_click`

Those effects belong in `signals`.

### Execution Context

Execution context is defined by:

- `pageAlias`
- `framePath`

`pageAlias` identifies the page or popup context, for example:

- `page`
- `popup1`
- `popup2`

`framePath` is an ordered chain of selectors from the main document to the target frame.

The replay algorithm is therefore deterministic:

1. resolve page by `pageAlias`
2. enter frame scope by `framePath`
3. execute the selected locator in that scope

### Locator Representation

The selected locator should be stored as both:

- a structured locator AST suitable for internal transforms and language generation
- a normalized Playwright selector or locator string suitable for display and debugging

The engine should treat the AST as authoritative and the string as a debug and display representation.

### Locator Alternatives

Each candidate locator should include:

- `selector`
- `locatorAst`
- `score`
- `matchCount`
- `visibleMatchCount`
- `isSelected`
- `engine`
- `reason`

These candidates must be generated and validated using Playwright-native selector generation and evaluation semantics, not custom Python heuristics.

### Signals

`signals` captures side effects and runtime consequences of the action. Suggested fields include:

- `navigation`
- `sameDocumentNavigation`
- `popup`
- `download`
- `dialog`

Examples:

- a `click` that opens a popup records `signals.popup`
- a `click` that navigates records `signals.navigation`
- a `click` that triggers a download records `signals.download`

Replay must follow the recorded signals instead of re-inferring them.

### Snapshot

`snapshot` should remain lightweight and diagnostic. It may include:

- tag name
- role
- accessible name
- text summary
- url at record time
- actionability hints
- selector match counts at record time

It should not become a large product-specific DOM dump.

## Frontend Compatibility Strategy

The current frontend pages remain in place:

- `RecorderPage.vue`
- `TestPage.vue`
- `ConfigurePage` and related RPA pages

The frontend continues using current backend routes and WebSocket endpoints.

The backend will adapt engine-native steps into compatibility payloads with fields such as:

- `action`
- `target`
- `frame_path`
- `locator_candidates`
- `validation`
- `signals`
- `tab_id`
- `source_tab_id`
- `target_tab_id`

Compatibility is implemented in the backend adapter layer, not by constraining the engine to the old Python step format.

## Runtime Data Flow

### Recording Flow

1. Frontend calls `POST /api/v1/rpa/session/start`
2. Backend creates a product session and requests an engine session
3. Engine launches browser runtime, recorder, tab tracking, and screencast
4. Backend stores the mapping between product session and engine session
5. Backend subscribes to engine event streams
6. Backend persists incoming steps and forwards them to existing frontend WebSocket subscribers

The frontend remains unaware of the engine boundary.

### Configure Flow

1. Frontend loads the recorded session from backend persistence
2. User selects a different locator candidate
3. Frontend calls the existing locator-promotion route
4. Backend forwards the request to the engine
5. Engine updates the authoritative selected locator for that step
6. Backend persists the returned step and responds with the updated compatibility payload

Important rule:

- locator promotion authority belongs to the engine

### Test Replay Flow

1. Frontend calls `POST /api/v1/rpa/session/{session_id}/test`
2. Backend loads the persisted authoritative actions
3. Backend asks the engine to replay those actions
4. Engine executes replay directly from the action model
5. Engine streams logs, current step, errors, page alias, and frame context
6. Backend forwards replay updates to the frontend
7. Backend persists replay result summary

Important rule:

- test replay should execute the structured action model directly
- generating Python code is not a prerequisite for testing

### Skill Export Flow

1. Frontend calls the existing generate or save route
2. Backend loads the confirmed action stream
3. Backend asks the engine to generate Python Playwright code
4. Engine returns code plus optional metadata and parameter hints
5. Backend wraps the code using the existing skill export packaging flow
6. Backend writes `SKILL.md` and `skill.py`

Important rule:

- the engine generates reliable Playwright code
- the backend packages product-facing skill artifacts

### Screencast Flow

The engine should become the sole owner of screencast truth because it already owns:

- active page
- active tab
- browser runtime
- input injection target

The backend should proxy and authorize screencast traffic rather than maintain a second browser-state model.

## Local Process Supervision

In local mode, the backend needs a lightweight engine supervisor.

Responsibilities:

- determine whether the configured engine endpoint is external or local
- lazily spawn the local engine process when required
- persist the chosen local port and process identifier if needed
- perform health checks before creating sessions
- restart once on failure before surfacing `rpa_engine_unavailable`

This supervision should be small and explicit. It should not recreate browser logic in Python.

## Migration Strategy

Migration should happen in stages so the product shell remains stable while runtime ownership moves.

### Phase 1: Introduce Engine And Client Adapter

- add `RpaClaw/rpa-engine`
- add backend `RPAEngineClient`
- add health and session bootstrap support
- do not switch frontend routes yet

### Phase 2: Switch Recording Ownership

- backend session start, tabs, steps stream, and screencast become engine-backed
- current recorder pages remain unchanged from the frontend point of view
- current Python manager becomes an orchestration adapter instead of the browser owner

### Phase 3: Switch Test Replay Ownership

- backend `/test` delegates replay to the engine
- Python `executor.py` stops being the primary runtime path

### Phase 4: Switch Code Generation Ownership

- backend `/generate` and `/save` delegate code generation to the engine
- Python skill export remains responsible only for product packaging
- old Python locator generation and replay internals can then be removed

## Error Handling

Errors should be structured and classified instead of surfaced as generic runtime strings.

### Engine Availability Errors

Examples:

- local engine not running
- cloud engine unreachable
- engine health check failing

Backend behavior:

- try one restart in local mode
- if unavailable, return a stable product error such as `rpa_engine_unavailable`

### Browser Runtime Errors

Examples:

- target closed
- page crashed
- frame detached
- strict mode violation
- popup not observed

Engine should return structured fields such as:

- `code`
- `message`
- `stepId`
- `pageAlias`
- `framePath`
- `selector`

### State Desynchronization Errors

Examples:

- backend session exists but engine session was lost
- locator promotion references an outdated step version
- requested page alias no longer exists

Recommended stable error codes:

- `session_desynced`
- `step_version_conflict`
- `page_alias_missing`

The backend may react by refreshing session state from the engine where safe.

## Testing Strategy

### Engine Unit Tests

Cover:

- locator generation and candidate ranking
- frame path derivation
- signal capture for navigation, popup, and download
- action-model to replay-command translation

### Engine Integration Tests

Run against a real Playwright browser and verify:

- click, fill, press, select flows
- interaction inside single and nested `iframe` trees
- popup and new-tab behavior
- page close and tab activation behavior
- download-triggering clicks

### Backend Adapter Tests

Cover:

- engine client request and response mapping
- current `/api/v1/rpa/*` compatibility payloads
- step persistence from engine events
- WebSocket forwarding behavior

### End-To-End Product Tests

Verify:

- start a recording from the current frontend flow
- receive steps in the recorder page
- promote a locator in configure flow
- run test replay successfully from test page
- export a skill successfully

## Implementation Constraints

- `rpa-engine` should live in-repo, under `RpaClaw/rpa-engine`
- local mode uses a same-machine process
- cloud mode uses separate deployment
- frontend routes and current backend route names remain stable
- the backend is the only frontend-facing API gateway
- dual-stack legacy and node runtimes may coexist temporarily behind a flag, but the legacy path is a migration fallback only, not a permanent architecture

## Risks And Mitigations

### Risk: Depending Too Deeply On Playwright Internals

Mitigation:

- isolate vendored or adapted recorder code inside the engine
- define a small internal engine abstraction so the rest of the product does not depend on Playwright internal module layout

### Risk: Eventual DTO Drift Between Engine And Frontend Compatibility Payloads

Mitigation:

- make backend compatibility mapping explicit and centrally tested
- keep the engine model authoritative and avoid re-deriving semantics in the backend

### Risk: Local Engine Process Management On Windows

Mitigation:

- keep local supervision minimal
- prefer health checks and one restart over complex embedded process orchestration
- keep browser logic fully inside Node

### Risk: Maintaining Two Runtime Paths Too Long

Mitigation:

- define the legacy runtime only as a temporary migration flag
- remove the old Python recorder and replay implementation after the node path is verified

## Conclusion

The chosen design introduces a dedicated Node `rpa-engine` as the sole owner of browser runtime truth for recording and replay. This addresses the structural correctness issues that remain in the current Python-led recorder by unifying locator generation, frame context, tab and popup handling, validation, screencast, and replay under the same Playwright-native runtime.

The FastAPI backend and Vue frontend remain the product shell. The backend preserves API compatibility, persistence, and skill export packaging, while the frontend continues to use the existing routes and pages with minimal change. This creates a clean migration path from the current recorder to a more reliable long-term architecture.
