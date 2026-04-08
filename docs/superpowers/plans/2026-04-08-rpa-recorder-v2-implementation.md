# RPA Recorder V2 Implementation Status

## Summary

This document records the implementation status of Recorder V2 as it actually landed in the codebase.

The original implementation plan proposed a separate Node `rpa-engine` process. That is no longer the chosen architecture. Recorder V2 was delivered by evolving the existing Python recorder stack in-place, while borrowing the useful design practices from Playwright recorder / Playwright CRX:

- context-level recorder injection instead of per-page ad hoc hooks
- frame-aware event payloads
- locator candidate generation plus diagnostics
- popup/new-tab signals modeled in the recorded step stream
- code generation aligned with frame/tab runtime behavior

The removed `rpa-engine` scaffold is not part of the final solution and should not be referenced as active scope.

## Final Architecture Decision

Recorder V2 is implemented inside the existing Python backend:

- `backend/rpa/manager.py` owns recorder injection, session state, tab tracking, frame-path capture, and step persistence
- `backend/rpa/generator.py` generates Playwright Python code from enriched V2-style steps
- `backend/route/rpa.py` exposes tabs, navigation, locator promotion, generation, testing, and export APIs
- `frontend/src/pages/rpa/*.vue` render the richer recorder/test/configure experience
- `backend/rpa/action_models.py` provides typed V2 action models for tests and future cleanup work

There is no active Node engine layer in the final implementation.

## Scope Changes From The Original Plan

### Removed

- `RpaClaw/rpa-engine/` is removed from scope
- `backend/rpa/engine_client.py` is not needed
- `backend/tests/test_rpa_engine_client.py` is removed
- the plan item that migrated runtime ownership from Python to Node is obsolete

### Kept In Spirit, Reimplemented In Python

- V2 action/step enrichment
- frame-aware recording
- popup/new-tab awareness
- locator candidate diagnostics and promotion
- frame-aware code generation

## Delivered Work

### 1. Recorder Session Model Upgraded

`RPAStep` in `backend/rpa/manager.py` now stores V2-style metadata:

- `frame_path`
- `locator_candidates`
- `validation`
- `signals`
- `element_snapshot`
- `tab_id`
- `source_tab_id`
- `target_tab_id`

This replaces the old single opaque locator-only step model as the effective recorder runtime contract.

### 2. Context-Level Recorder Injection Implemented

The recorder now uses `BrowserContext.expose_binding(...)` together with `context.add_init_script(...)` in `backend/rpa/manager.py`.

This changed the injection model from fragile page-local wiring to context-level wiring so that:

- newly opened tabs inherit the recorder automatically
- same-origin and cross-origin iframe documents run the recorder script on load
- event delivery is no longer dependent on per-page `expose_function` setup timing

### 3. Frame-Aware Event Capture Implemented

The browser-side recorder script now computes `frame_path` in-page before emitting events.

Important behaviors:

- iframe interactions are no longer reported as main-frame actions by default
- nested iframe chains are preserved as ordered iframe selectors
- Python keeps a fallback `_build_frame_path(...)` path for cases where the binding source frame is available but the emitted payload does not contain `frame_path`

This is the main fix for the original "iframe operations not recorded" problem.

### 4. Locator Candidate Diagnostics Implemented

Recorder V2 now generates multiple locator candidates per target element, with metadata such as:

- `kind`
- `score`
- `strict_match_count`
- `visible_match_count`
- `selected`
- `reason`
- normalized locator payload

The scoring model is Playwright-codegen-style heuristic ordering where lower score is better. Current priority is roughly:

1. `testid`
2. `role + accessible name`
3. `placeholder`
4. `label`
5. `alt`
6. `text`
7. `title`
8. `css id`
9. `role only`
10. `css attr`
11. `css tag/class`
12. absolute CSS fallback

Selection rule:

1. build candidates
2. sort by ascending score
3. choose the first strict-unique candidate
4. otherwise try parent-child nested fallback
5. otherwise use absolute CSS fallback

### 5. Locator Promotion API Implemented

The configure flow now supports promoting an alternate locator candidate through:

- `POST /rpa/session/{session_id}/step/{step_index}/locator`

Backend behavior:

- mark the chosen candidate as `selected`
- rewrite `step.target` to the chosen locator payload
- preserve validation metadata about the selected candidate

This gives users a practical recovery path when the auto-selected locator is not the best one.

### 6. Multi-Tab And Popup Tracking Implemented

Recorder session state now tracks:

- known tabs
- opener relationships
- active tab
- tab switching
- tab close fallback

Step upgrades were added for common browser behaviors:

- recent click + subsequent same-tab navigation -> `navigate_click`
- recent click + popup/tab creation -> `open_tab_click`
- explicit tab activation -> `switch_tab`
- tab close -> `close_tab`

The event binding callback also falls back to the session's `active_tab_id` when Playwright does not provide a resolvable `source.page`, which fixes missing `tab_id` on popup follow-up events.

### 7. Frame-Aware Code Generation Implemented

`backend/rpa/generator.py` now emits frame-scoped code when `frame_path` exists:

```python
frame_scope = current_page.frame_locator("iframe[...]")
frame_scope = frame_scope.frame_locator("iframe[...]")
```

The generator also emits popup-aware code:

```python
async with current_page.expect_popup() as popup_info:
    await frame_scope.get_by_role(...).click()
new_page = await popup_info.value
current_page = new_page
```

Other supported upgrades remain in place:

- `expect_navigation(...)` for navigation clicks
- `expect_download(...)` for download clicks
- tab dictionary management through `tabs[...]`

### 8. Frontend Recorder/Test/Configure Pages Updated

The Vue pages now expose the richer recorder state:

- Recorder page shows tabs, frame summaries, and validation summaries
- Test page shows tabs, frame info, and validation details during playback
- Configure page shows locator candidates, scores, strict counts, frame breadcrumb, and an explicit `Use This` promotion action

### 9. Tests Added Or Updated

The following backend tests cover the delivered V2 behavior:

- `backend/tests/test_rpa_manager.py`
- `backend/tests/test_rpa_generator.py`
- `backend/tests/test_rpa_action_compiler.py`

Notable regressions covered:

- iframe capture includes `frame_path`
- context binding fallback uses `active_tab_id` when `source.page` is missing
- popup creation upgrades clicks to `open_tab_click`
- popup follow-up navigation is not double-recorded
- generator emits frame chains and popup-aware code

## Files In Final Scope

### Added

- `RpaClaw/backend/rpa/action_models.py`
- `RpaClaw/backend/tests/test_rpa_action_compiler.py`

### Modified

- `RpaClaw/backend/rpa/manager.py`
- `RpaClaw/backend/rpa/generator.py`
- `RpaClaw/backend/route/rpa.py`
- `RpaClaw/backend/tests/test_rpa_manager.py`
- `RpaClaw/backend/tests/test_rpa_generator.py`
- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- `RpaClaw/frontend/src/pages/rpa/TestPage.vue`
- `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

### Removed / Not Used

- `RpaClaw/rpa-engine/`
- `RpaClaw/backend/tests/test_rpa_engine_client.py`

## Verification Status

Focused backend verification was run with:

```powershell
@'
import unittest
patterns = ['test_rpa_manager.py', 'test_rpa_generator.py', 'test_rpa_action_compiler.py']
loader = unittest.defaultTestLoader
suite = unittest.TestSuite(loader.discover('backend/tests', pattern=pattern) for pattern in patterns)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'@ | python -
```

Result:

- 28 tests passed

Frontend repo-wide `npm run type-check` is still not a reliable completion gate because the repository contains unrelated pre-existing type errors outside the Recorder V2 slice. Recorder V2 page-local type issues introduced during this work were cleaned up.

## Known Gaps / Follow-Ups

The core Recorder V2 goals are implemented, but a few follow-up items remain reasonable:

- `visible_match_count` currently mirrors strict count and is not yet a true visibility-only metric
- actionability metadata is still lightweight and can be expanded if the configure page needs stronger diagnostics
- `backend/rpa/action_models.py` exists as a typed V2 model, but the live runtime still persists `RPAStep`; a later cleanup can unify these
- repo-wide frontend type health should be fixed separately if type-check is to become a hard gate

## Conclusion

Recorder V2 has been implemented without introducing a separate `rpa-engine`. The final delivered system is a Python-based recorder/runtime that incorporates the important Playwright CRX-inspired practices needed to solve the original issues:

- locator quality is materially improved through candidate generation and validation
- iframe interactions are captured and replayed with frame context
- popup/new-tab flows are modeled and generated correctly
- users can inspect and promote alternate locators in the configure page
