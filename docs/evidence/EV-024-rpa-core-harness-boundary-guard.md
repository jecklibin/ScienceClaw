---
id: EV-024
doc_kind: evidence
scope: project
feature_refs:
  - docs/features/F024-rpa-core-harness-boundary-guard.md
created: 2026-06-02
updated: 2026-06-03
evidence_level: exhaustive
---

# EV-024: RPA Core Harness Boundary Guard

## Scope

验证本次修复是否把“点击触发下载”归还给 RPA Core 录制事实，而不是由 Harness expected signals、controlled fixture 或前端显示补丁定义事实。范围包括：

- `RecordingRuntimeAgent` simple `click` plan 捕获 download event。
- route trace finalization 在 append accepted trace 前归并 paused pending download，避免 Full SOP Harness capture 改变 SOP->SKILL 主链路事实。
- timeline 投影只展示 trace 已有 `signals.download`。
- 既有 run_python download 捕获和 compiler download 语义保持。
- Harness controlled download 回放仍作为验证层，不反向定义产品录制事实。

## Commands

RED / GREEN focused tests:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_recording_runtime_agent_records_download_signal_from_simple_click_plan RpaClaw/backend/tests/test_rpa_trace_timeline.py::test_trace_timeline_projects_download_signal_in_summary -q
```

F024.1 RED / GREEN focused tests:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw/backend/tests/test_rpa_route_trace.py::test_apply_recording_agent_result_waits_for_paused_download_before_append -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-delayed-download RpaClaw/backend/tests/test_rpa_harness_ai_capture_integration.py::test_full_sop_capture_preserves_delayed_download_signal_in_core_trace -q
```

Focused regression commands to run before closeout:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-core RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_recording_runtime_agent_records_download_signal_from_ai_code RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_recording_runtime_agent_waits_briefly_for_click_triggered_download RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_recording_runtime_agent_records_download_signal_from_simple_click_plan RpaClaw/backend/tests/test_rpa_route_trace.py::test_apply_recording_agent_result_waits_for_paused_download_before_append -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-compiler RpaClaw/backend/tests/test_rpa_trace_timeline.py::test_trace_timeline_projects_download_signal_in_summary RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py::test_ai_operation_with_download_signal_compiles_to_expect_download RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py::test_standalone_download_trace_after_ai_operation_merges_into_trigger -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-harness RpaClaw/backend/tests/test_rpa_harness_ai_capture_integration.py::test_full_sop_capture_preserves_delayed_download_signal_in_core_trace RpaClaw/backend/tests/test_rpa_harness_skill_replay.py::test_skill_replay_serves_controlled_download_and_validates_saved_file RpaClaw/backend/tests/test_rpa_harness_live_agent_eval.py::test_live_agent_eval_controlled_download_is_captured_as_trace_signal -q
```

F024.2 live RecorderPage download projection regression:

```powershell
npm.cmd run test -- RecorderPage.test.ts -t "projects download signals"
```

```powershell
npm.cmd run test -- RecorderPage.test.ts
```

```powershell
npm.cmd run type-check
```

F024.3 duplicate password fill regression:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-password-focused RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_full_sop_harness_collapses_duplicate_sensitive_fill_with_sequence_gap RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_consecutive_fill_events_collapse_to_latest_value_on_same_target_frame_tab RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_non_consecutive_fill_events_do_not_collapse_when_same_target_arrives_out_of_order RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_fill_merge_rebuilds_recorded_action_value -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-password-harness RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_full_sop_harness_captures_manual_fill_with_parameterized_value RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_full_sop_harness_folds_text_input_focus_click_into_fill_checkpoint RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_full_sop_harness_reuses_focus_click_before_state_for_fill_checkpoint RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_full_sop_harness_backfills_out_of_order_fill_checkpoint_from_late_focus_click -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-password-manager RpaClaw/backend/tests/test_rpa_manager.py -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-password-compiler RpaClaw/backend/tests/test_rpa_trace_timeline.py::test_trace_timeline_exposes_sensitive_fill_contract_without_raw_trace_dependency RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py::test_manual_fill_uses_sensitive_credential_param RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py::test_manual_fill_uses_harness_input_placeholder_runtime_param RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py::test_duplicate_sensitive_fill_values_consume_params_in_order -q
```

F024.4 Core/Harness navigation boundary audit:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-nav-red RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_records_core_trace_without_harness_capture -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-f024-nav-focused RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_records_core_trace_without_harness_capture RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_does_not_record_trace_when_session_paused RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_normalizes_url_and_updates_metadata RpaClaw/backend/tests/test_rpa_harness_ai_capture_integration.py::test_full_sop_capture_records_entry_navigation_checkpoint -q
```

F024.5 projected timeline download display regression:

```powershell
npm.cmd run test -- RecorderPage.test.ts -t "preserves download summaries"
```

```powershell
npm.cmd run test -- RecorderPage.test.ts
```

```powershell
npm.cmd run type-check
```

F024.6 initial document click noise regression:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_manager.py::RPASessionManagerTabTests::test_full_sop_harness_ignores_pre_navigation_body_click_noise -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_manager.py -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_route_trace.py -q
```

F024.7 explicit navigation redirect suppression regression:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-pr59-nav-redirect-red RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_suppresses_redirect_navigation_event -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-pr59-nav-focused RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_suppresses_redirect_navigation_event RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_records_core_trace_without_harness_capture RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_does_not_record_trace_when_session_paused RpaClaw/backend/tests/test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_normalizes_url_and_updates_metadata RpaClaw/backend/tests/test_rpa_harness_ai_capture_integration.py::test_full_sop_capture_records_entry_navigation_checkpoint -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-pr59-nav-manager RpaClaw/backend/tests/test_rpa_manager.py -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-pr59-nav-compiler RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest --basetemp .pytest-tmp-pr59-nav-route RpaClaw/backend/tests/test_rpa_route_trace.py -q
```

Harness structure:

```powershell
python C:\Users\HUAWEI\.codex\skills\using-harness\scripts\knowledge_check.py --root E:\Work-Project\OtherWork\ScienceClaw --docs-path docs --strict
```

## Results

- RED focused tests: `2 failed`。`simple click` 缺少 `trace.signals.download`，timeline summary 未展示 download signal。
- RED replay-code guard: `1 failed`。simple `click` trace 捕获 download 后仍缺少可回放 `async def run(page, results)`，会威胁 SOP->SKILL 编译链路。
- F024.1 RED route finalization: `1 failed`。paused pending download 晚于 `_apply_recording_agent_result()` append 时，当前 AI trace 缺少 `signals.download`。
- F024.1 GREEN route finalization: `1 passed`。
- F024.1 Full SOP capture regression: first run failed before业务断言 because Windows default pytest temp root `C:\Users\HUAWEI\AppData\Local\Temp\pytest-of-HUAWEI` was not accessible; rerun with workspace `--basetemp` passed, final rerun: `1 passed`。
- GREEN focused tests: simple click download capture 和 replay-code guard passed。
- Core recording download focused regression: `4 passed in 1.62s`。
- Timeline + compiler download focused regression: `3 passed in 0.92s`。
- Harness controlled download focused regression: first run failed before业务断言 because Windows default pytest temp root `C:\Users\HUAWEI\AppData\Local\Temp\pytest-of-HUAWEI` was not accessible; rerun with workspace `--basetemp` passed, final rerun: `2 passed in 13.40s`。
- F024.1 Harness focused regression: `3 passed in 12.83s`。
- Changed core test files: `99 passed, 30 warnings in 5.28s`。Warnings are existing Python 3.14 / FastAPI deprecation warnings, not F024 behavior failures.
- F024.2 RED live RecorderPage projection test: failed as expected because the SSE `trace_added` raw trace contained `signals.download.filename=export.xlsx`, but the left timeline text did not include `export.xlsx`。
- F024.2 GREEN focused live RecorderPage projection: `1 passed`。The live trace display now shows the click title plus the existing download signal filename.
- F024.2 RecorderPage regression: `27 passed`。
- F024.2 Core recording download focused regression: `4 passed, 29 warnings`。Warnings are existing Python 3.14 / FastAPI deprecation warnings, not F024.2 behavior failures.
- F024.2 Timeline + compiler download focused regression: `3 passed`。
- F024.2 Harness controlled download focused regression: `3 passed, 29 warnings`。Warnings are existing Python 3.14 / FastAPI deprecation warnings.
- F024.2 frontend type-check: failed on pre-existing unrelated TypeScript errors in `ActivityPanel.vue`, `ChatMessage.vue`, `DesktopTitleBar.vue`, `SessionItem.vue`, `ChatPage.vue`, `desktopWindow.ts`, and related files; no reported error referenced `RecorderPage.vue` or `RecorderPage.test.ts`.
- F024.3 RED duplicate sensitive fill regression: `1 failed` before fix. Two same-target password fill events with a sequence gap produced two accepted steps/traces and two Harness checkpoints.
- F024.3 focused duplicate/fill regression: `4 passed`。Same-target same-value fill events across a short sequence gap now collapse, while non-consecutive different values remain separate.
- F024.3 Harness fill checkpoint regression: `4 passed`。Full SOP Harness capture still folds focus-click checkpoints and persists parameterized fill values without raw input text in `trace_events.json` / checkpoint / expected / captured HTML.
- F024.3 manager regression: `104 passed`。
- F024.3 timeline/compiler sensitive fill regression: `4 passed`。Credential and Harness input placeholders still compile to runtime parameters; intentionally distinct password fields still consume `password` / `password_2` in order.
- F024.4 boundary audit found one remaining intrusion: `navigate_active_tab()` created Core navigation trace only inside the Harness Full SOP branch. RED regression: `1 failed` with `len(session.traces) == 0` when Harness capture was disabled.
- F024.4 GREEN navigation boundary regression: `4 passed, 29 warnings`。Warnings are existing Python 3.14 / FastAPI deprecation warnings. Core navigation trace is now recorded without Harness capture, paused sessions still do not record navigation facts, and Full SOP Harness capture still writes the entry navigation checkpoint.
- F024.4 final audit regression: `test_rpa_manager.py` `106 passed`; entry-navigation/download Harness focused regression `2 passed, 29 warnings`; timeline/compiler download focused regression `3 passed`; strict Harness knowledge validation `Errors: 0. Warnings: 0`.
- F024.5 RED projected timeline download display regression: `1 failed` as expected. Polling `session.timeline` showed only `Click table row column action`; `export.xlsx` from the projected timeline summary/raw trace download signal was not visible.
- F024.5 GREEN focused projected timeline regression: `1 passed`. The recorded-step timeline now preserves the download filename when polling projected timeline data.
- F024.5 RecorderPage regression: `28 passed`.
- F024.5 frontend type-check: failed on pre-existing unrelated TypeScript errors in `ActivityPanel.vue`, `ChatMessage.vue`, `DesktopTitleBar.vue`, `FilePanel.vue`, `SessionItem.vue`, settings components, `ChatPage.vue`, `desktopWindow.ts`, and related files. No reported error referenced `src/utils/rpaConfigureTimeline.ts` or `src/pages/rpa/RecorderPage.test.ts`.
- F024.6 RED initial document click regression: `1 failed` as expected. With Full SOP Harness capture enabled, a pre-navigation `body` click entered `session.steps` before the real navigation.
- F024.6 GREEN focused regression: `1 passed`. Initial no-side-effect `body/html` clicks are ignored before Core accepted trace creation.
- F024.6 manager regression: `107 passed`. Neighboring Harness checkpoint, hover promotion, navigation upgrade, and fill dedupe behavior stayed green.
- F024.6 SOP->Skill output-side regression: trace compiler `110 passed`; route trace `42 passed, 29 warnings`. Warnings are existing Python 3.14 / FastAPI deprecation warnings, not F024.6 behavior failures.
- F024.7 RED explicit navigation redirect regression: `1 failed` as expected. A redirecting `navigate_active_tab()` created one event-based navigate step before the explicit navigation trace.
- F024.7 GREEN explicit navigation redirect regression: `1 passed`. Explicit navigation now suppresses in-flight main-frame navigation events while preserving the final `after_page.url`.
- F024.7 focused navigation regression: `5 passed, 29 warnings`. Core navigation remains recorded without Harness capture, paused sessions still skip navigation facts, and Full SOP entry navigation checkpoint still passes.
- F024.7 Core SOP->SKILL regression: manager `108 passed`; trace compiler `110 passed`; route trace `42 passed, 29 warnings`. Warnings are existing Python 3.14 / FastAPI deprecation warnings.
- F024.8 RED explicit SSO target-vs-observed regression: `3 failed` as expected. `TraceSkillCompiler` generated `_target_url` from `after_page.url`, and `navigate_active_tab()` traces did not yet expose `signals.navigation.target_url/observed_url/redirected`.
- F024.8 GREEN focused regression: `3 passed`. Standalone explicit navigation replay now uses the recorded target URL while preserving the SSO/login URL as observed evidence, and manager traces expose target/observed/redirected signals.
- F024.8 compiler + manager regression: `219 passed`. Existing redirect folding, tab replay, dynamic URL suffix, and manager navigation tests stayed green.
- F024.8 Core SOP->Skill e2e regression: `11 passed`. Trace-first runtime-to-compiler replay coverage stayed green after touching Core manager/compiler files.

F024.8 commands:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py::test_explicit_navigation_replays_recorded_target_not_redirect_login_url RpaClaw\backend\tests\test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_records_core_trace_without_harness_capture RpaClaw\backend\tests\test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_suppresses_redirect_navigation_event
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py RpaClaw\backend\tests\test_rpa_manager.py
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_trace_e2e.py
```

F024.9 late explicit-navigation redirect folding:

Commands:

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_folds_late_redirect_into_explicit_navigation_trace -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_records_core_trace_without_harness_capture RpaClaw\backend\tests\test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_suppresses_redirect_navigation_event RpaClaw\backend\tests\test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_folds_late_redirect_into_explicit_navigation_trace RpaClaw\backend\tests\test_rpa_manager.py::RPASessionManagerTabTests::test_navigate_active_tab_does_not_record_trace_when_session_paused -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_manager.py -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py -q
```

```powershell
$env:PYTHONPATH='RpaClaw'; python -m pytest RpaClaw\backend\tests\test_rpa_trace_e2e.py -q
```

Results:

- RED late redirect folding regression: `1 failed` as expected. A post-`navigate_active_tab()` same-tab `framenavigated` event was recorded as one standalone manual navigation step.
- GREEN focused regression: `1 passed`. Late same-tab redirects after explicit address-bar navigation now update the existing explicit navigation trace instead of adding a separate navigation step.
- Focused navigation regression: `4 passed`. Core explicit navigation still records without Harness capture, synchronous redirects remain suppressed, late redirects fold into the explicit trace, and paused sessions still skip explicit navigation facts.
- Core SOP->Skill regression: manager `109 passed`; trace compiler `111 passed`; trace e2e `11 passed`.

## Harness Validation

`knowledge_check.py --strict`: `Scanned 260 markdown file(s). Checked 54 knowledge artifact(s). Errors: 0. Warnings: 0.`

## Artifacts

- Feature: `docs/features/F024-rpa-core-harness-boundary-guard.md`
- ADR: `docs/decisions/ADR-004-rpa-core-owns-recording-facts-harness-adapts-only.md`
- Lesson: `docs/lessons/LL-002-harness-must-not-define-rpa-core-facts.md`
- Code: `RpaClaw/backend/rpa/recording_runtime_agent.py`
- Code: `RpaClaw/backend/rpa/manager.py`
- Code: `RpaClaw/backend/route/rpa.py`
- Code: `RpaClaw/backend/rpa/trace_timeline.py`
- Code: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- Code: `RpaClaw/frontend/src/utils/rpaConfigureTimeline.ts`
- Tests: `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`
- Tests: `RpaClaw/backend/tests/test_rpa_route_trace.py`
- Tests: `RpaClaw/backend/tests/test_rpa_harness_ai_capture_integration.py`
- Tests: `RpaClaw/backend/tests/test_rpa_trace_timeline.py`
- Tests: `RpaClaw/frontend/src/pages/rpa/RecorderPage.test.ts`
- Project rule: `AGENTS.md`

## Notes

本次修复明确拒绝三类路径：不从 Harness expected signals 合成产品 trace，不恢复 legacy step 作为事实源，不为单一站点或文件列表补关键词规则。Core 负责捕获真实浏览器下载事件；Harness 负责验证和治理。
