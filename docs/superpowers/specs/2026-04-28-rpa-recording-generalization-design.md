# RPA Recording Generalization Design

## Goal

Improve RpaClaw's AI recording path so generated traces and exported scripts remain reliable when runtime business data changes, without adding eval-case-specific rules or site templates.

## Constraints

- Keep the recording path trace-first: execute real browser operations and record observable traces.
- Do not add hard-coded knowledge of the current eval cases, fixed IDs, or app-specific flows.
- Separate stable UI anchors from dynamic business data.
- Treat Playwright errors and current page state as authoritative repair facts.
- Preserve existing skill export behavior unless richer trace metadata is present.

## Reference Model

Playwright should remain the execution substrate. Use locator contracts that re-resolve at action time: role, label, placeholder, test id, scoped table/form locators, and assertions/postconditions. Avoid persisting ordinal or business-value selectors unless the user explicitly asked for an ordinal target.

Browser-use's useful pattern is the observe/action/result loop: compact page state is shown to the model, the model emits a structured action, a deterministic controller executes the action, and the result is fed back to the next step. RpaClaw should adopt the structure without replacing trace-first recording with a heavy contract layer.

## Data Model

Classify values observed during recording:

- `ui_anchor`: Stable UI contract such as label text, role/name, test id, section title, table headers, dialog title.
- `user_param`: Value supplied or implied by the user's command, such as contract number, target record number, item quantity, unit price.
- `derived_data`: Data read from the page and reused later, such as supplier number or department.
- `runtime_output`: Data created by the application, such as submitted request numbers, generated order numbers, download names.

Only `ui_anchor` values are safe to embed directly in exported locators. Dynamic data must be represented as input bindings, previous output bindings, or runtime assertions.

## Runtime Plan Parsing

The planner output should be parsed as a structured JSON object even when the model adds surrounding text. The parser should:

- Prefer fenced JSON content.
- Fall back to the first complete JSON object with `JSONDecoder.raw_decode`.
- Normalize core fields exactly once.
- Preserve extra text only for diagnostics.
- Reject malformed plans with a clear error that includes a short raw-output excerpt.

## Recording Command Boundary

The recording runtime should not infer business intent by deleting natural-language wrapper sentences or negative constraints from the user's message. That approach is brittle because evaluation harness prompts and real business guardrails can use the same words, such as "do not" or "不要".

The implemented boundary is structural:

- `POST /api/v1/rpa/session/{session_id}/chat` accepts an optional `business_instruction`.
- Interactive users can keep sending only `message`; behavior stays backward-compatible.
- Evaluation and automation callers can send a rich `message` for UI/log context plus a separate `business_instruction` for the trace-first runtime.
- `RecordingRuntimeAgent.run()` receives the actual business goal and no longer contains `context_markers` or template-specific prompt filtering.

This keeps eval harness setup instructions out of the runtime planner without hard-coding the eval prompt template into RpaClaw.

## Snapshot Extraction

`extract_snapshot` should support both canonical field lists and planner-friendly maps:

- Canonical: `fields: [{"label": "合同编号", "value": "CT-..."}]`
- Compatible: `fields: {"合同编号": "CT-..."}`

Execution should convert both into a normalized field list and keep signal metadata usable by the compiler.

Snapshot collection uses semantic DOM structure as the default path:

- Tables: native `table`, ARIA grid/table roles, headers, row-local cells, row-local actions, and editable controls.
- Details: `section`, `article`, `form`, `fieldset`, ARIA regions/groups, `data-*`, `dl/dt/dd`, and two-column key/value tables.
- Modals: `[role="dialog"]` and `[aria-modal="true"]` are the default semantic roots.

Framework-specific selectors are isolated behind adapter registries:

- `tableViewAdapters` currently contains a Jalor iGrid adapter that outputs the same `table_view` schema as the semantic collector.
- `modalViewAdapters` contains Element, Ant, Vant, and generic class-modal adapters that output the same `modal_dialog` schema as the semantic collector.

Framework adapters should remain optional collection adapters, not business logic and not default locator rules embedded in generated skills.

The framework adapter source is now separated from the main snapshot collector:

- `assistant_snapshot_runtime.py` owns default semantic collection for tables, details, actions, dialogs, and text regions.
- `assistant_snapshot_adapters.py` owns optional framework adapter JavaScript fragments, currently including Jalor iGrid table support and Element/Ant/Vant/class-modal dialog support.
- New adapters must output the same `table_view` or `modal_dialog` schema as the semantic collector. They must not contain business vocabulary, eval IDs, or code-generation behavior.

## Effect Verification

`expected_effect=mixed` must not mean URL navigation. The verifier should accept browser-visible evidence from:

- URL change or explicit target URL.
- `effect.action_performed`.
- Non-empty structured output for action plans.
- Download signals.
- Postcondition metadata when supplied by planner or deterministic overlay.

When no direct evidence exists, the verifier may fail, but the error should explain that no postcondition/action evidence was observed rather than requiring navigation.

Before repair, the runtime should prefer "already achieved" evidence when available. A successful output or postcondition must not be turned into a repair attempt just because a SPA URL did not change.

The implementation now keeps terminal-effect concerns outside the main recording loop:

- `recording_contracts.py` normalizes planner-declared terminal contracts and evidence types.
- `recording_effects.py` verifies expected browser effects, explicit terminal contracts, and conservative state-change evidence.
- `recording_verifier.py` collects before/after browser evidence and validates terminal evidence such as row changes, downloads, visible feedback, and explicit empty results.
- `recording_terminal_recovery.py` recovers failed attempts only from structured inputs, declared postconditions, and observed before/after differences. Raw generated code and error text are diagnostic context, not strong recovery evidence.

Terminal evidence is local to the action it proves. A recovered row or feedback message must not override an explicit full-task completion failure. If the completion verifier says a multi-stage instruction is incomplete, recovered side-effect evidence can be used as repair input, but not as final success for the whole instruction.

Feedback evidence is intentionally conservative:

- Prefer `role=status`, `role=alert`, `aria-live`, or adapter-provided toast evidence.
- Compare before/after text so a pre-existing message region cannot satisfy a new terminal contract.
- Treat validation errors as blockers before accepting success evidence.
- Treat filenames alone as diagnostic data; download success requires a real download signal, artifact path, or explicit downloaded flag.

## Dynamic Binding Metadata

Accepted traces may carry optional metadata:

- `input_bindings`: named dynamic inputs with source, default, and value classification.
- `output_bindings`: named outputs or JSON paths produced by the trace.
- `postcondition`: generic verification contract, such as table row exists, text visible, download observed, or non-empty extraction.

This metadata should be optional and backward-compatible.

Current implementation wires this metadata through the real recording path:

- The planner schema documents `input_bindings`, `output_bindings`, and `postcondition`.
- `_accepted_trace()` copies these fields from the accepted plan into `RPAAcceptedTrace`.
- `RPAAcceptedTrace` serializes the metadata with default empty dictionaries.
- The compiler consumes declared `input_bindings` to parameterize embedded AI code, and consumes compilable postconditions to allow bounded recovered attempts.

The compiler must only rewrite values explicitly declared in `input_bindings`. It must keep UI labels, placeholders, roles, titles, table headers, and other stable anchors literal.

## Exported Script Strategy

The compiler should use dynamic bindings when available:

- Replace recorded values in embedded AI code only when they match declared input bindings.
- Keep stable UI anchors literal.
- Generate small generic helpers for table-row lookup and editable item-row filling when metadata indicates those patterns.

Helpers must be generic and operate on headers, labels, dynamic keys, and values. They must not mention eval app entities or fixed IDs.

For snapshot extraction scripts, the compiler now prefers replayable structural evidence in this order:

1. Explicit value locator.
2. Field locator or stable `data-prop`.
3. URL path extraction.
4. Text pattern extraction.
5. Observed DOM label adjacency.

Recorded unique text is treated as evidence only, not as a primary replay locator, because runtime data can change. If required fields do not have replayable evidence, the compiler falls back to runtime semantic instruction instead of generating a deterministic script that would silently use recorded data.

The label-adjacency extractor is generic: it uses ARIA label relationships, `label[for]`, `dt/dd`, table cells, sibling nodes, parent text, ancestor siblings, inputs, outputs, and `data-value`. It does not include AUI, Element, Ant, Jalor, eval app, GitHub, or fixed case selectors.

The compiler also applies these replay safety rules:

- Recovered attempts are replayable only when a strong compiled postcondition validates the resulting state; otherwise the original failure remains fatal.
- Failed precondition attempts may be preserved as replayable setup only when the final trace has a full postcondition. Without that terminal check, precondition failures stay fatal.
- Dynamic inputs are rewritten inside locator arguments and code literals only when explicitly declared as `input_bindings`. UI anchors such as labels, roles, placeholders, headings, and table headers stay literal.
- Table postconditions must keep header and row scopes bound to the same table/grid root. Broad row-text fallback is diagnostic only, not strong success evidence.
- Export tasks avoid double-triggering downloads. If generated code already owns the download event, the generic download helper is not appended.

`playwright_code_normalizer.py` contains replay normalization helpers that are intentionally limited to Playwright semantics:

- Fill helpers use user-like Playwright actions and then verify visible/control values. They do not treat direct DOM value mutation as success.
- Combobox fallback requires observable selected value evidence after `Enter`; otherwise it returns failure and lets repair handle it.
- Dialog action fallback only clicks buttons with structural submit/action evidence or an explicit preferred name. It does not click the last enabled button as a generic fallback.
- Download stabilization must stay scoped to the original download trigger block and must not wrap unrelated preceding actions.

## Evaluation Framework

The evaluation runner supports `--verify-replay`, which validates the full recording lifecycle:

- Record through RpaClaw.
- Generate the skill script.
- Reject scripts that still call runtime AI unless explicitly allowed.
- Execute the generated script.
- Apply expected API, telemetry, output, and download assertions.

The runner now passes `business_instruction` separately from its wrapper prompt so RpaClaw can evaluate real recording behavior without prompt-template coupling.

Replay verification is evaluated from a clear starting state:

- Reset case data.
- Establish eval authentication and navigate to the case start page.
- Record through RpaClaw.
- Compile the generated skill.
- Re-establish the replay start state.
- Run the compiled script.
- Apply API, output, telemetry, and download assertions.

The eval app uses user-facing feedback text in the UI and keeps internal `event_key` values in API telemetry. Visible text should look like a plausible enterprise application, not like test-only markers.

Latest verified full run after the current implementation:

- Date: 2026-05-02
- Command: `python -u rpa-eval-app\evals\runner.py --all --verify-replay --eval-backend-url http://127.0.0.1:8085 --eval-frontend-url http://127.0.0.1:5175 --rpaclaw-url http://127.0.0.1:12001 --case-timeout-s 180 --replay-timeout-s 120`
- Result: 15 passed, 5 failed, 75.0% pass rate.

Passing cases in that run:

- `contract_extract_001`
- `contract_filter_open_001`
- `empty_result_contract_001`
- `lab_body_click_export_001`
- `lab_collection_row_action_001`
- `lab_modal_supplier_contact_001`
- `lab_parameterized_contract_001`
- `lab_popup_report_download_001`
- `lab_split_grid_first_file_001`
- `login_navigation_001`
- `purchase_order_generate_001`
- `purchase_request_create_001`
- `purchase_request_then_order_001`
- `report_contract_export_001`
- `supplier_complete_001`

Known remaining failure classes:

- Record-stage failures when the current page has no visible rows yet or the generated plan scopes the wrong table.
- Multi-stage tasks where the first stage is read correctly but the write stage is not completed.
- Dataflow form filling where label-control binding is wrong even though the generated output claims success.
- Explicit empty-result extraction that still routes through slow planning instead of a deterministic snapshot path.
- Async task/download flows where detail tables and list tables share header text and need stricter table-root disambiguation.

## Test Strategy

Use focused unit tests for parser, extraction normalization, effect verification, trace metadata serialization, and compiler helper rendering. The tests must not depend on the eval app's fixed case IDs. They should use neutral examples such as invoices, projects, and purchase-like tables only where the behavior is generic.

Current focused verification also covers:

- Snapshot adapter isolation and absence of AUI selectors in generic collectors/compiler output.
- Runtime planner metadata wiring from accepted traces into compiler-visible fields.
- Dynamic binding rewrite in text and locator arguments.
- Conservative terminal contract verification and recovery gates.
- Eval runner `business_instruction`, replay start-state handling, structured empty-result assertions, and user-facing UI feedback.
