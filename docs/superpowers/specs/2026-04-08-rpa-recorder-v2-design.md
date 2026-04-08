# RPA Recorder V2 Final Design

## Summary

Recorder V2 solves the two structural problems of the previous recorder:

- recorded locators were often not aligned with what replay could reliably resolve
- actions inside `iframe` documents were either missed or replayed without correct frame context

The final design is influenced by Playwright recorder / Playwright CRX practices, but it is implemented inside the existing Python backend rather than as a separate Node service.

The key design choices are:

- inject the recorder at the `BrowserContext` level
- emit frame-aware events from inside each frame document
- generate multiple locator candidates with diagnostics
- track tabs and popup relationships explicitly
- generate Playwright code from enriched steps that already contain frame/tab metadata

This is the final target design for this repository. The separate `rpa-engine` design is abandoned.

## Design Goals

- Make recording and replay use the same mental model for locator resolution
- Capture iframe interactions as first-class recorded steps
- Preserve popup/new-tab behavior in the recorded step stream
- Expose locator diagnostics and manual promotion in the configure UI
- Keep the existing FastAPI, Vue, sandbox, and skill-export integration model
- Avoid introducing an unnecessary second runtime process

## Non-Goals

- Backward compatibility with legacy recorder step payloads
- Reproducing Playwright Inspector UI exactly
- Building a separate Node runtime for recorder ownership
- Keeping both V1 and V2 recorder semantics alive long term

## Architectural Decision

### Chosen Architecture

Recorder V2 is implemented in the existing Python backend and browser context:

1. Python session manager owns session, tab, and page bookkeeping
2. Recorder JavaScript is injected into every document through Playwright context APIs
3. Browser-side JS computes locator candidates and frame path
4. Python converts emitted events into enriched `RPAStep` records
5. Code generation consumes those enriched steps directly
6. Frontend pages display diagnostics and allow locator promotion

### Rejected Architecture

The earlier proposal introduced a standalone Node `rpa-engine`. That path was rejected because:

- it duplicated runtime ownership already available in Python Playwright usage
- it added cross-process lifecycle and protocol complexity without being required to solve the actual bugs
- the main benefits sought from Playwright CRX could be adopted directly in the existing recorder:
  - context-wide injection
  - frame-aware payloads
  - locator candidate diagnostics
  - popup/new-tab modeling

## Runtime Model

### Session Model

Each recorder session tracks:

- `id`
- `user_id`
- `sandbox_session_id`
- `status`
- `steps`
- `paused`
- `active_tab_id`

The session is the product-facing unit for recording, testing, and export.

### Tab Model

Each tab tracks:

- `tab_id`
- `title`
- `url`
- `opener_tab_id`
- `created_at`
- `last_seen_at`
- `status`

Tabs are first-class state because recorder/test/configure flows must preserve:

- popup relationships
- active preview target
- tab switching
- close fallback behavior

### Step Model

The live runtime step contract is `RPAStep` in `backend/rpa/manager.py`. Important V2 fields are:

- `action`
- `target`
- `frame_path`
- `locator_candidates`
- `validation`
- `signals`
- `element_snapshot`
- `value`
- `tab_id`
- `source_tab_id`
- `target_tab_id`
- `url`
- `description`

There is also a typed `RecordedActionV2` model in `backend/rpa/action_models.py`. It reflects the intended V2 structure and is used in tests and future cleanup, but the main runtime currently persists `RPAStep`.

## Recorder Injection Design

### Context-Level Injection

The recorder uses:

- `BrowserContext.expose_binding(...)`
- `BrowserContext.add_init_script(...)`

This is the critical recorder design change.

Why it matters:

- every new tab created in the context inherits the recorder automatically
- iframe documents receive the recorder script on document initialization
- event delivery is centralized instead of depending on per-page hook timing

### Browser-Side Event Emission

The injected script emits normalized recorder events through `window.__rpa_emit(...)`.

For interactive targets it performs:

- target retargeting to the nearest interactive ancestor
- accessible-name and role derivation
- locator candidate generation
- validation metadata generation
- element snapshot extraction
- frame-path collection

This keeps the event payload close to the DOM context where the action occurred.

## Frame Model

Frame context is stored per step in `frame_path`.

`frame_path` is an ordered list of iframe selectors from the page main frame down to the target frame.

Example:

```text
[
  "iframe[title=\"preview\"]",
  "iframe[src=\"https://example.com/editor\"]"
]
```

Design rules:

- frame context is never inferred later from a plain locator alone
- locator validation happens relative to the current document where the event occurred
- replay must reconstruct the same frame chain before resolving the target locator

There are two capture paths:

1. preferred: browser-side JS computes `frame_path` before emitting the event
2. fallback: Python derives the frame chain from Playwright `source.frame`

The browser-side path is the source of truth because it proved more reliable in real iframe scenarios.

## Locator Model

### Candidate Generation

For each interacted element, the recorder generates multiple candidates.

Current candidate kinds include:

- `testid`
- `role`
- `placeholder`
- `label`
- `alt`
- `text`
- `title`
- `css`
- `role_only`
- nested parent-child fallback
- absolute CSS fallback

### Score Semantics

`score` is a priority score. Lower is better.

It reflects heuristic preference, not guaranteed runtime success.

Current ordering is roughly:

1. `testid`
2. `role + name`
3. `placeholder`
4. `label`
5. `alt`
6. `text`
7. `title`
8. `css id`
9. `role only`
10. `css attr`
11. `css tag/class`
12. absolute fallback

### Selection Rules

Locator selection follows this algorithm:

1. build candidates
2. sort by ascending `score`
3. choose the first candidate whose strict match count is exactly one
4. if none are strict-unique, try nested parent-child scoping
5. if still unresolved, use absolute CSS fallback

This means:

- lower score is preferred
- strict uniqueness is still the main acceptance gate
- a higher-score strict-unique candidate is often better than a lower-score ambiguous candidate

### Locator Diagnostics

Each candidate exposes:

- `kind`
- `score`
- `strict_match_count`
- `visible_match_count`
- `selected`
- `reason`
- normalized locator payload

The configure page shows these values and allows a user to promote a different candidate.

## Validation Model

Validation is produced during recording and stored on each step.

Current persisted shape:

- `status`
- `details`

Typical statuses:

- `ok`
- `fallback`

Design intent:

- `ok` means the selected locator is strict-unique in current evaluation
- `fallback` means the selected locator is a degraded or synthetic fallback, or uniqueness is weak

This validation is diagnostic, not yet a full replay-time actionability audit.

## Tab And Popup Model

Recorder V2 preserves browser-level behavior explicitly.

Supported runtime behaviors:

- navigation in the active tab
- popup/new-tab creation
- tab activation
- tab close and fallback to opener/remaining tab

Recorded step actions include:

- `navigate`
- `click`
- `fill`
- `press`
- `select`
- `navigate_click`
- `open_tab_click`
- `switch_tab`
- `close_tab`
- `download_click`

Important rule:

- popup behavior is not ignored as a side effect
- it becomes explicit in the step stream through upgraded action types and `source_tab_id` / `target_tab_id`

## Code Generation Design

### Input Model

The generator consumes enriched recorder steps, not just a raw locator string.

Relevant step fields:

- `action`
- `target`
- `frame_path`
- `tab_id`
- `source_tab_id`
- `target_tab_id`
- `url`
- `value`

### Frame Replay

When `frame_path` exists, generation emits chained `frame_locator(...)` scopes before the final locator action.

Example:

```python
frame_scope = current_page.frame_locator("iframe[title=\"preview\"]")
frame_scope = frame_scope.frame_locator("iframe[src=\"https://example.com/editor\"]")
await frame_scope.get_by_role("link", name="Open Notes", exact=True).click()
```

### Popup Replay

When a click is recognized as opening a new tab, generation emits `expect_popup()` and updates the current page reference.

Example:

```python
async with current_page.expect_popup() as popup_info:
    await frame_scope.get_by_role("link", name="Open Notes", exact=True).click()
new_page = await popup_info.value
await new_page.wait_for_load_state("domcontentloaded")
current_page = new_page
```

### Other Replay Signals

The generator also supports:

- `expect_navigation(...)` for same-tab navigation clicks
- `expect_download(...)` for download-triggering clicks
- tab dictionary bookkeeping for popup/switch flows

## API Design

Recorder V2 continues to use the existing backend route surface.

Important routes:

- `POST /rpa/session/start`
- `GET /rpa/session/{session_id}`
- `GET /rpa/session/{session_id}/tabs`
- `POST /rpa/session/{session_id}/tabs/{tab_id}/activate`
- `POST /rpa/session/{session_id}/navigate`
- `POST /rpa/session/{session_id}/step/{step_index}/locator`
- `POST /rpa/session/{session_id}/generate`
- `POST /rpa/session/{session_id}/test`
- `POST /rpa/session/{session_id}/save`

The frontend does not need a separate engine protocol.

## Frontend Design

### Recorder Page

Displays:

- tab strip
- active preview
- recorded step timeline
- frame summary
- locator summary
- validation summary

### Test Page

Displays:

- tab strip
- active preview
- playback logs
- frame summary per step
- validation summary per step

### Configure Page

Displays:

- step action summary
- frame breadcrumb
- validation badge
- locator candidate list
- candidate score / strict count
- current selected candidate
- manual promotion action

This page is now the main operator-facing diagnostic surface for locator quality.

## Design Principles Borrowed From Playwright CRX

The implementation does not embed Playwright CRX itself, but it borrows the practices that matter:

- align recorder semantics with replay semantics as much as practical
- capture events in the browser context where they actually happen
- keep frame context explicit
- store locator alternatives, not just one final selector
- treat popup/new-tab behavior as part of the recorded runtime truth

These were the valuable takeaways. The separate extension/runtime architecture was not required here.

## Known Limitations

- `visible_match_count` currently mirrors total strict match count instead of a true visibility-only count
- validation is still lighter than full actionability auditing
- runtime step storage is still `RPAStep`; a future cleanup can unify it with `RecordedActionV2`
- repo-wide frontend type-check is not yet a clean gate because of unrelated pre-existing issues

## Conclusion

The final Recorder V2 design for this repository is a Python-based, context-injected, frame-aware recorder with locator diagnostics and tab-aware replay generation. It intentionally keeps the existing product architecture, while incorporating the parts of Playwright recorder / Playwright CRX design that actually solve the recorder correctness problems.
