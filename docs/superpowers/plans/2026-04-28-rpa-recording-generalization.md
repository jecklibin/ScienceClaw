# RPA Recording Generalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AI recording traces and exported scripts more robust to runtime data changes without adding eval-case-specific behavior.

**Architecture:** Add backward-compatible structured metadata and generic helpers around the existing trace-first runtime. Runtime changes stay in `recording_runtime_agent.py`; trace schema changes stay in `trace_models.py`; export-time dynamic reuse stays in `trace_skill_compiler.py`. Evaluation callers pass a structured `business_instruction` to keep harness setup text out of the recording runtime without natural-language prompt filtering.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, Playwright async APIs.

---

## File Structure

- Modify `RpaClaw/backend/rpa/recording_runtime_agent.py`: robust planner JSON parsing, field normalization, generic mixed-effect evidence handling.
- Modify `RpaClaw/backend/route/rpa.py`: accept optional `business_instruction` and pass it to the recording runtime.
- Modify `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`: collect semantic table/detail/modal snapshots and isolate framework-specific collectors in adapter registries.
- Modify `RpaClaw/backend/rpa/trace_models.py`: optional binding and postcondition models on accepted traces.
- Modify `RpaClaw/backend/rpa/trace_skill_compiler.py`: generic dynamic parameter and helper rendering when trace metadata exists.
- Modify `rpa-eval-app/evals/runner.py` and `rpa-eval-app/evals/rpa_client.py`: run full record/compile/replay verification and pass structured business instructions.
- Modify `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`: focused parser, extract, and effect verifier tests.
- Modify `RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py`: adapter-registry tests for snapshot collectors.
- Modify `RpaClaw/backend/tests/test_rpa_trace_models.py`: serialization tests for new metadata.
- Modify `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`: compiler tests for dynamic binding behavior.

### Task 1: Runtime Plan Parsing And Snapshot Normalization

**Files:**
- Modify: `RpaClaw/backend/rpa/recording_runtime_agent.py`
- Test: `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`

- [x] **Step 1: Add failing parser tests**

Add tests that show `_parse_json_object` accepts fenced JSON with trailing prose and accepts the first valid JSON object when extra text follows it.

- [x] **Step 2: Add failing extract field normalization tests**

Add tests that show `_snapshot_plan_fields({"fields": {"Project": "Apollo"}})` returns `[{"label": "Project", "value": "Apollo"}]` and preserves list input unchanged.

- [x] **Step 3: Verify RED**

Run:

```bash
uv run pytest tests/test_rpa_recording_runtime_agent.py::test_parse_json_object_accepts_trailing_text_after_fenced_json tests/test_rpa_recording_runtime_agent.py::test_parse_json_object_accepts_first_json_object_with_trailing_text tests/test_rpa_recording_runtime_agent.py::test_snapshot_plan_fields_accepts_mapping_values -q
```

Expected: tests fail before implementation.

- [x] **Step 4: Implement robust parsing and normalization**

Use `json.JSONDecoder().raw_decode()` after extracting the best candidate string. Extend `_snapshot_plan_fields` to accept dict maps and convert entries into `{"label": key, "value": value}`.

- [x] **Step 5: Verify GREEN**

Run the same focused tests and confirm they pass.

### Task 2: Generic Mixed Effect Evidence

**Files:**
- Modify: `RpaClaw/backend/rpa/recording_runtime_agent.py`
- Test: `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`

- [x] **Step 1: Add failing mixed-effect tests**

Add tests for `_ensure_expected_effect` showing that `expected_effect="mixed"` accepts a successful `effect.action_performed`, non-empty structured output, and download signal without URL change.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_rpa_recording_runtime_agent.py::test_ensure_expected_effect_accepts_mixed_action_evidence_without_navigation tests/test_rpa_recording_runtime_agent.py::test_ensure_expected_effect_accepts_mixed_structured_output_without_navigation tests/test_rpa_recording_runtime_agent.py::test_ensure_expected_effect_accepts_mixed_download_signal_without_navigation -q
```

Expected: tests fail before implementation.

- [x] **Step 3: Implement evidence classifier**

Add a small helper that detects generic action evidence from result `effect`, `signals.download`, and meaningful non-empty output. Use it for `mixed` before attempting target URL auto-navigation. Keep strict navigation behavior for `expected_effect="navigate"`.

- [x] **Step 4: Verify GREEN**

Run the focused tests and confirm they pass.

### Task 3: Trace Metadata For Dynamic Bindings

**Files:**
- Modify: `RpaClaw/backend/rpa/trace_models.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_models.py`

- [x] **Step 1: Add failing metadata serialization tests**

Add tests that construct `RPAAcceptedTrace` with `input_bindings`, `output_bindings`, and `postcondition`, then assert `model_dump()` preserves the metadata.

- [x] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_rpa_trace_models.py::test_trace_preserves_dynamic_binding_metadata -q
```

Expected: test fails because fields do not exist.

- [x] **Step 3: Implement optional metadata fields**

Add Pydantic models or `Dict[str, Any]` fields:

- `input_bindings: Dict[str, Any]`
- `output_bindings: Dict[str, Any]`
- `postcondition: Dict[str, Any]`

Defaults must use `Field(default_factory=dict)`.

- [x] **Step 4: Verify GREEN**

Run the focused test and confirm it passes.

### Task 4: Compiler Dynamic Binding Helpers

**Files:**
- Modify: `RpaClaw/backend/rpa/trace_skill_compiler.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`

- [x] **Step 1: Add failing compiler tests**

Add tests that compile a trace with embedded AI code containing a recorded business value and an `input_bindings` entry for that value. Assert generated code uses `kwargs.get("invoice_number", "INV-001")` or equivalent parameter expression instead of the literal inside the interaction code.

- [x] **Step 2: Add generic helper rendering test**

Add a test that a trace postcondition with `kind="table_row_exists"` causes the compiler output to include a generic `_find_table_row_by_headers` helper, without app-specific identifiers.

- [x] **Step 3: Verify RED**

Run:

```bash
uv run pytest tests/test_rpa_trace_skill_compiler.py::test_compiler_parameterizes_declared_business_binding_in_ai_code tests/test_rpa_trace_skill_compiler.py::test_compiler_renders_generic_table_row_helper_for_postcondition -q
```

Expected: tests fail before implementation.

- [x] **Step 4: Implement dynamic binding rewrite**

Use trace `input_bindings` as an additional parameter lookup source when rendering embedded AI code. Only rewrite values declared in metadata. Do not infer eval-specific IDs or rewrite stable UI labels.

- [x] **Step 5: Implement generic helper inclusion**

When any trace contains `postcondition.kind == "table_row_exists"`, include a small helper that scopes a table by header text and finds a row by dynamic key values.

- [x] **Step 6: Verify GREEN**

Run the focused compiler tests and confirm they pass.

### Task 5: Focused Regression Run

**Files:**
- No production file changes expected.

- [x] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_rpa_recording_runtime_agent.py tests/test_rpa_trace_models.py tests/test_rpa_trace_skill_compiler.py -q
```

- [x] **Step 2: Document environment failures if present**

If async pytest plugin issues persist in the worktree baseline, record that as an environment limitation and run the focused synchronous tests individually where possible.

### Task 6: Snapshot Collector Adapter Cleanup

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py`

- [x] **Step 1: Remove framework classes from the default semantic collectors**

AUI selectors were removed from detail and display-value extraction. Default table/detail/modal collection now uses HTML, ARIA, `data-*`, and label/value semantics.

- [x] **Step 2: Move framework selectors behind adapter registries**

Jalor iGrid is isolated under `tableViewAdapters`. Element, Ant, Vant, and class-based modals are isolated under `modalViewAdapters`.

- [x] **Step 3: Verify adapter structure**

Focused tests assert that framework collectors are registered as adapters and that AUI selectors are not emitted into the generic snapshot and skill compiler paths.

### Task 7: Structured Business Instruction Boundary

**Files:**
- Modify: `RpaClaw/backend/route/rpa.py`
- Modify: `RpaClaw/backend/rpa/recording_runtime_agent.py`
- Modify: `rpa-eval-app/evals/rpa_client.py`
- Modify: `rpa-eval-app/evals/runner.py`
- Test: `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`
- Test: `rpa-eval-app/evals/test_runner.py`
- Test: `rpa-eval-app/evals/test_rpa_client.py`

- [x] **Step 1: Add structured business instruction support**

`ChatRequest` accepts optional `business_instruction`. The recording runtime receives that value when provided, while the original `message` remains available in debug context.

- [x] **Step 2: Remove natural-language wrapper filtering from runtime**

`context_markers` was removed. The runtime no longer strips evaluation prompt templates or broad negative constraints such as "不要" and "do not".

- [x] **Step 3: Update evaluation runner**

The eval runner still sends the full wrapped message for observability, but passes `case["instruction"]` separately as `business_instruction`.

### Task 8: Full Verify-Replay Evaluation

**Files:**
- No production file changes expected.

- [x] **Step 1: Run full verification**

Command used:

```bash
uv run python rpa-eval-app/evals/runner.py --all --verify-replay --case-timeout-s 240 --replay-timeout-s 180 --rpaclaw-url http://127.0.0.1:12011 --eval-backend-url http://127.0.0.1:8085 --eval-frontend-url http://127.0.0.1:5175 --model gpt-5.4-mini
```

- [x] **Step 2: Record result**

Latest full run after the adapter and structured-instruction cleanup passed 7 of 12 cases. Passing cases were:

- `approval_high_priority_001`
- `contract_extract_001`
- `login_navigation_001`
- `purchase_order_generate_001`
- `purchase_request_create_001`
- `report_async_download_001`
- `report_contract_export_001`

Remaining failures were timeouts, replay start-state mismatch, and record-stage failures after bounded repairs.

### Task 9: Runtime Module Boundary Cleanup

**Files:**
- Add: `RpaClaw/backend/rpa/recording_contracts.py`
- Add: `RpaClaw/backend/rpa/recording_effects.py`
- Add: `RpaClaw/backend/rpa/recording_repair.py`
- Add: `RpaClaw/backend/rpa/recording_terminal_recovery.py`
- Add: `RpaClaw/backend/rpa/recording_verifier.py`
- Add: `RpaClaw/backend/rpa/playwright_code_normalizer.py`
- Modify: `RpaClaw/backend/rpa/recording_runtime_agent.py`
- Modify: `RpaClaw/backend/rpa/trace_skill_compiler.py`
- Tests: `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`
- Tests: `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`

- [x] **Step 1: Extract terminal contract and effect verification responsibilities**

Move terminal contract normalization and effect verification out of the main runtime agent. The runtime remains responsible for the observe/plan/execute/repair loop; `recording_effects.py` and `recording_verifier.py` own terminal evidence and state-change validation.

- [x] **Step 2: Keep recovery evidence structured**

`recording_terminal_recovery.py` only promotes recovery evidence from declared input bindings, terminal contracts, postconditions, structured outputs, and before/after browser evidence. Raw generated code and error strings remain diagnostic context.

- [x] **Step 3: Normalize Playwright code with conservative helpers**

`playwright_code_normalizer.py` centralizes fill, combobox, dialog action, and download stabilization helpers. Direct DOM value mutation is not considered a success path; combobox and dialog fallbacks require observable structural evidence.

- [x] **Step 4: Preserve trace-first failure semantics**

Recovered attempts and replayable preconditions are only non-fatal when a strong compiled postcondition validates the terminal state. Otherwise browser-side failures remain fatal.

### Task 10: Snapshot Adapter Module Split

**Files:**
- Add: `RpaClaw/backend/rpa/assistant_snapshot_adapters.py`
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
- Tests: `RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py`

- [x] **Step 1: Keep the default collector semantic**

Default snapshot collection is based on HTML, ARIA, `data-*`, label/value, table, dialog, and region semantics. It does not include AUI selectors or eval-app business selectors.

- [x] **Step 2: Move framework selectors into adapters**

Jalor iGrid table support and Element/Ant/Vant/class-modal dialog support live in `assistant_snapshot_adapters.py`. Each adapter emits the same schema as the default collector.

### Task 11: Evaluation Framework Hardening

**Files:**
- Modify: `rpa-eval-app/evals/runner.py`
- Modify: `rpa-eval-app/evals/rpa_client.py`
- Modify: `rpa-eval-app/frontend/src/views/RegressionLab.vue`
- Modify: `rpa-eval-app/frontend/src/views/PopupReport.vue`
- Tests: `rpa-eval-app/evals/test_runner.py`
- Tests: `rpa-eval-app/evals/test_rpa_client.py`

- [x] **Step 1: Verify replay from a clear starting state**

The runner resets data, establishes eval authentication, records the task, compiles the generated skill, re-establishes the replay start state, and then runs the script.

- [x] **Step 2: Strengthen assertion sources**

Extraction assertions prefer structured output/trace data. Empty-result assertions parse structured outputs instead of flat text markers. Download assertions require real API/download evidence instead of filename mentions.

- [x] **Step 3: Replace test-only visible messages**

The eval pages keep internal `event_key` values in API telemetry and display user-facing status messages in the UI.

### Task 12: Full Verify-Replay Evaluation Refresh

**Files:**
- No production file changes expected.

- [x] **Step 1: Clean stale local processes**

Clear stale listeners on `8085`, `5175`, and `12001` before running full verification.

- [x] **Step 2: Run full verification**

Command used:

```powershell
python -u rpa-eval-app\evals\runner.py --all --verify-replay --eval-backend-url http://127.0.0.1:8085 --eval-frontend-url http://127.0.0.1:5175 --rpaclaw-url http://127.0.0.1:12001 --case-timeout-s 180 --replay-timeout-s 120
```

- [x] **Step 3: Record result**

Latest full run passed 15 of 20 cases, for a 75.0% pass rate. Remaining failures are:

- `approval_high_priority_001`: record-stage table/list row not visible or not loaded.
- `contract_lookup_then_purchase_request_001`: multi-stage read/write flow stops after contract detail read.
- `lab_dataflow_cost_center_001`: generated output claims correct dataflow, but API assertion shows form state was not submitted with the expected values.
- `lab_empty_audit_extract_001`: explicit empty extraction routes through slow planning and times out.
- `report_async_download_001`: async report polling/download scopes the wrong table when detail and list tables share header text.
