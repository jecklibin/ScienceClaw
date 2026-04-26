# RPA Trace-first Recording Design

> Status: Current baseline direction, partially implemented.
>
> The current code follows this direction: operate the browser first, record facts/traces, and compile/generalize later. Verify exact behavior in `RpaClaw/backend/rpa/manager.py`, `recording_runtime_agent.py`, `trace_recorder.py`, and `trace_skill_compiler.py`.

Date: 2026-04-20

Branch: `codex/rpa-trace-first-recording`

Baseline: `upstream/master`

## 1. Problem Statement

The RPA skill recording feature needs to support a mixed workflow:

- Users record precise browser operations manually whenever that is faster and more reliable.
- Users use natural language only for operations that are tedious or inherently semantic, such as finding the repository with the highest star count, selecting the most relevant project, summarizing a page, extracting the top N records, or transferring data from one page to another.
- The final saved Skill should replay reliably and should avoid unnecessary runtime token usage by preferring deterministic Playwright code when possible.

The previous Contract-first recording architecture made recording itself too heavy. A single instruction could trigger multiple LLM calls, repeated snapshots, compilation, validation, repair, and done checks. In practice this made recording slower, less predictable, and more fragile than the upstream direct-agent experience.

The new design therefore moves complexity out of the interactive recording path.

Core principle:

```text
Recording time: run fast, operate the browser, and record rich factual traces.
Skill generation time: analyze traces, generalize, generate stable code, test, and repair.
Replay time: run mostly deterministic Playwright code, with runtime AI only where semantic reasoning is truly required.
```

## 2. Lessons From Previous Attempts

This design intentionally incorporates the failures from the previous two redesigns.

### 2.1 Runtime AI instruction patch on top of the old step model

What worked:

- It identified a real need: some steps require runtime semantic reasoning and cannot be safely compiled into fixed Playwright code at recording time.
- It clarified the useful distinction between deterministic page-data logic and runtime semantic judgment.
- It proved that recording-time execution must still happen; saving a rule without operating the browser breaks the user's recording flow.

What failed:

- Step classification was scattered across LLM prompts, local keyword rules, coercion helpers, and repair logic.
- Local heuristics started to become the semantic source of truth.
- Coercion sometimes rewrote user intent and dropped important constraints.
- Recording-time success and exported Skill success diverged.
- Same-page actions and extraction results were easy to misclassify as success or failure.
- Generated code quality was inconsistent, especially with free-form JavaScript.

Lesson:

```text
Adding one more step type does not solve the architecture problem if recording, execution, validation, and export still lack a simple shared lifecycle.
```

### 2.2 Recording-time Contract-first pipeline

What worked:

- It made semantic boundaries explicit.
- It introduced structured outputs, blackboard-style dataflow, and replay validation.
- It made manual and AI steps easier to reason about in a common model.

What failed:

- It moved too much complexity into the interactive recording path.
- A single user command could trigger repeated snapshot capture, multiple LLM calls, compile/execute/validate cycles, repair attempts, and done checks.
- Selector failures could be amplified by Playwright default timeouts.
- Deterministic operators were too few, so simple tasks were split into too many steps.
- The user experience became slower and less reliable than the upstream baseline.

Lesson:

```text
Strong abstractions are useful after recording, but they are too expensive as the default live recording control loop.
```

### 2.3 Design rule derived from both failures

The new design must preserve this boundary:

```text
Recording path: direct, bounded, trace-producing.
Compilation path: heavier reasoning, generalization, replay testing, and repair.
```

## 3. Design Goals

- Preserve the upstream direct RPA assistant experience during recording.
- Make natural-language browser operations fast enough for interactive use.
- Do not require generated recording-time code to be fully generalized.
- Record enough factual evidence to generalize after recording completes.
- Support cross-page dataflow, especially extracting data from page A and filling corresponding fields on page B.
- Show users a clear left-side timeline of accepted recorded steps.
- Keep failed attempts out of the primary recorded step list.
- Generate Skills that are more stable than raw traces by applying post-hoc generalization and replay testing.
- Keep architecture simple enough to debug and evolve.

## 4. Non-goals

- Do not reintroduce Contract-first as the default recording-time control loop.
- Do not run multiple Planner/Compiler/Validator LLM rounds for every interactive natural-language command.
- Do not force every recorded step into a perfect final abstraction during recording.
- Do not make local keyword rules the primary semantic classifier.
- Do not make full DeepAgent the default recording-time controller.
- Do not freely generate complex JavaScript for browser DOM operations when Python Playwright can do the job.
- Do not optimize every possible RPA scenario in the first implementation. The first implementation must fully support the target scenarios listed in this document and leave clear extension points for later.

## 5. Recommended Architecture

The architecture is split into two stages.

### Stage 1: Trace-first Recording Runtime

Recording runtime is optimized for fast browser operation. It records facts, not final Skill abstractions.

```text
Manual action or natural-language command
        ->
Operate browser using upstream-style direct assistant path
        ->
Record accepted trace with before/after evidence
        ->
Update lightweight runtime results when data is extracted
        ->
Show accepted trace in the left-side timeline
```

Recording runtime may use one LLM call for a natural-language operation. If a generated script fails, one bounded local execution repair is allowed, but repeated planning loops are not part of the default path.

### Stage 2: Post-hoc Skill Compilation

Skill compilation runs after the user clicks recording complete or save/generate.

```text
Trace timeline
        ->
Trace analyzer
        ->
Generalizer and dataflow resolver
        ->
Skill script generator
        ->
Replay tester
        ->
Failure repair loop
        ->
skill.py + optional trace/manifest metadata
```

Compilation may use heavier reasoning because it does not block the live recording interaction. This is where URL generalization, dataflow inference, validation generation, and replay repair belong.

## 6. Agent Strategy

The system should not choose an agent framework because it is powerful. It should choose the smallest control loop that satisfies the job.

### 6.1 RecordingRuntimeAgent

RecordingRuntimeAgent is a lightweight, recording-time browser operator.

It should be custom-built or narrowly wrapped around the existing upstream RPA assistant path. It should not be a full DeepAgent loop.

Responsibilities:

- Accept one natural-language command.
- Read a lightweight page snapshot and current `runtime_results`.
- Generate one browser action or one Python Playwright script.
- Execute it immediately.
- Record an accepted trace if it succeeds.
- Record failed attempts only as diagnostics.

It may use Micro-ReAct:

```text
Plan once
-> execute once
-> if execution fails, observe the error and minimal page state
-> repair once
-> execute once
-> accept trace or fail gracefully
```

Hard limits:

- Normal successful command: at most one LLM call.
- Failed command: at most one repair LLM call.
- No open-ended ReAct loop.
- No separate LLM done-check after every accepted trace.
- No multi-step autonomous SOP planning during recording.
- Failed attempts never appear as primary timeline steps.

### 6.2 Python Playwright first

Generated recording-time scripts should default to Python Playwright.

Reason:

- Prior attempts showed that free-form LLM-generated JavaScript frequently caused syntax, escaping, and Python/JavaScript mixing errors.
- Python Playwright is easier to validate, repair, and reuse in final Skill code.

Policy:

- Prefer Python Playwright locator APIs for navigation, extraction, ranking, filling, and clicking.
- Avoid free-form complex JavaScript generated by the LLM.
- Allow JavaScript only for small read-only DOM extraction snippets or system-owned templates.
- Do not put complex business logic inside `page.evaluate(...)` unless there is a measured performance reason and the snippet is simple.

This is not a JavaScript ban. It is a ban on uncontrolled JavaScript generation in the recording loop.

### 6.3 SkillCompilationAgent

Skill compilation should default to a deterministic orchestrator plus targeted LLM calls, not DeepAgent.

Default compilation components:

- Trace analyzer.
- Dataflow resolver.
- Generalizer.
- Script generator.
- Replay tester.
- Targeted repair prompt when replay fails.

DeepAgent is optional for advanced post-hoc repair only when the deterministic compilation pipeline cannot diagnose or repair a failure. It must not become the default recording-time controller.

## 7. Trace Model

The recorder should store accepted traces separately from low-level diagnostics.

### 7.1 Common Trace Fields

Every accepted trace should include:

- `trace_id`
- `trace_type`
- `source`: `manual` or `ai`
- `user_instruction` when applicable
- `status`: accepted traces are successful by definition
- `started_at_ms`
- `ended_at_ms`
- `before_page`: URL, title, optional snapshot summary
- `after_page`: URL, title, optional snapshot summary
- `diagnostics_ref` for failed attempts or raw details

### 7.2 Manual Action Trace

Manual actions should record:

- `action`: `goto`, `click`, `fill`, `press`, `select`, or equivalent existing action
- locator candidates from the existing recorder
- target text, role, href, label, placeholder, and stable CSS hints when available
- typed or selected value for fill/select operations
- whether navigation happened
- before and after URL

The primary timeline should display only the accepted manual action, not internal event noise.

### 7.3 AI Operation Trace

Natural-language operations should record:

- user instruction
- generated code or structured action that actually ran
- execution result
- resulting URL or data output
- before and after page evidence
- whether the step changed the page, extracted data, filled fields, downloaded files, or opened a new tab

The recording-time generated code may contain concrete URLs or selectors. That is acceptable because generalization happens later.

### 7.4 Data Capture Trace

Whenever a step extracts structured data, store a data capture trace:

- `output_key`, such as `customer_info`, `selected_project`, or `top10_prs`
- structured `output`
- source page URL/title
- field provenance when available: nearby label, source text, selector hint, row context, URL, and confidence
- schema inferred from the output shape

Example:

```json
{
  "trace_type": "data_capture",
  "source": "ai",
  "user_instruction": "Extract customer name, email, and phone",
  "output_key": "customer_info",
  "output": {
    "name": "Alice Zhang",
    "email": "alice@example.com",
    "phone": "13800000000"
  },
  "source_page": {
    "url": "https://example.test/customer/1",
    "title": "Customer Detail"
  }
}
```

### 7.5 Dataflow Trace

Cross-page workflows require a first-class dataflow trace. When a value is filled into a target page, the recorder should try to link the filled value to prior runtime results.

Record:

- target field locator candidates
- target field label, placeholder, role, and nearby text
- actual filled value
- candidate source refs, such as `customer_info.name`
- selected source ref when the match is exact or high-confidence
- confidence and reason

Example:

```json
{
  "trace_type": "dataflow_fill",
  "source": "manual",
  "target_page": {
    "url": "https://example.test/create-order"
  },
  "target_field": {
    "label": "Customer Name",
    "locator_candidates": []
  },
  "value": "Alice Zhang",
  "source_ref_candidates": ["customer_info.name"],
  "selected_source_ref": "customer_info.name",
  "confidence": "exact_value_match"
}
```

If no confident source ref exists, the trace should keep the literal value and mark the mapping as unresolved. The compilation UI can later ask for confirmation if needed.

## 8. Runtime Results Store

Recording should maintain a lightweight `runtime_results` object for the current session.

It is not a heavy contract blackboard. Its responsibilities are:

- Store structured outputs from accepted data capture traces.
- Allow later natural-language commands to reference previously captured data.
- Support immediate A-to-B workflows, such as "fill the current form with the data captured earlier".
- Provide candidate refs for dataflow trace inference.

Example:

```json
{
  "selected_project": {
    "name": "openai/openai-agents-python",
    "url": "https://github.com/openai/openai-agents-python"
  },
  "customer_info": {
    "name": "Alice Zhang",
    "email": "alice@example.com"
  }
}
```

## 9. Natural-language Recording Rules

Natural-language commands are recording-time browser assistance, not final Skill compilation.

Rules:

- Prefer the upstream direct assistant/ReAct path for operating the browser, bounded by Micro-ReAct limits.
- A single user instruction should normally use one LLM planning/code-generation call.
- One bounded repair is allowed for execution errors caused by generated code syntax or obvious selector failure.
- Failed attempts are diagnostics, not accepted traces.
- If the operation succeeds, record the successful action/code/result trace.
- Do not run a separate done-check LLM call after every accepted trace.
- Do not force recording-time steps into final generalized Skill code.
- Default to Python Playwright when generating scripts.
- Avoid free-form complex JavaScript.

Recommended behavior for common examples:

- "Open the project with the highest star count": generate and run one Python Playwright script that parses the current list, finds the max star count, and navigates to the selected URL. Record the script, selected target, and after URL.
- "Open the project most related to Python": use runtime semantic judgment once, navigate, and record selected target with reason.
- "Collect the first 10 PRs in the current repository": run deterministic extraction, record array output and source page.
- "Fill the current form with the data captured earlier": consume `runtime_results`, fill fields, and record dataflow mappings.

## 10. Left-side Timeline UX

The recorder left panel should show accepted traces, not internal agent attempts.

Suggested card categories:

- Manual operation
- AI browser operation
- Data captured
- Data filled
- Page navigation
- Generated script step

Each card should show a concise human-readable summary.

Examples:

```text
01 Manual navigation
Open https://github.com/trending

02 AI operation
Open the project with the highest star count
Result: openai/openai-agents-python

03 Manual operation
Enter the Pull requests page

04 AI data capture
Collect the first 10 PRs
Output: top10_prs, 10 records

05 Data fill
customer_info.name -> Customer Name
customer_info.email -> Email
```

Expanded details may show:

- before/after URL
- raw instruction
- generated code preview
- output preview
- locator candidates
- dataflow refs
- diagnostics for failed attempts

The default collapsed timeline should remain simple and confidence-building.

## 11. Post-hoc Skill Compilation

Compilation should transform traces into a replayable Skill.

Compilation tasks:

1. Normalize trace order and remove failed attempts.
2. Identify data-producing traces and data-consuming traces.
3. Replace literal values with runtime result refs when confidence is high.
4. Generalize URLs when a URL came from a previous selected target.
5. Convert successful natural-language logic traces into deterministic Playwright helpers when possible.
6. Preserve runtime AI only when the trace represents semantic judgment that cannot be deterministically encoded.
7. Generate validation checks for important outcomes.
8. Run replay tests and repair generated code when replay fails.

Examples:

- A recorded literal URL `https://github.com/openai/openai-agents-python/pulls` can become `{selected_project.url}/pulls` when `selected_project.url` was captured in a prior trace.
- A literal filled value `"Alice Zhang"` can become `customer_info["name"]` when it exactly matches a prior captured field.
- A natural-language trace that selected the highest star count can become a deterministic script that parses star counts during replay.
- A semantic trace that selected the most relevant project can remain a runtime AI instruction, but must output structured JSON for downstream steps.

## 12. Validation Strategy

Validation should be strongest during replay and Skill generation, not during interactive recording.

Recording-time validation:

- Did the browser action execute?
- Did URL/title/data output change as expected?
- Was structured data captured when requested?

Generation/replay-time validation:

- Required arrays are not empty unless the user explicitly allowed empty results.
- Required record fields are non-empty.
- Filled form fields equal the intended source values.
- URL contains expected stable subpaths when applicable.
- Extracted text is not generic page chrome such as "Navigation Menu".
- Runtime AI outputs match the required structured schema.

## 13. Error Handling

Recording:

- Keep the UI responsive.
- Record failed attempts in diagnostics only.
- If a natural-language action fails after the bounded repair, show a concise failure and let the user continue manually.
- Do not add failed attempts to the primary timeline.

Compilation:

- If generalization is low confidence, keep the literal action and mark it as a review warning.
- If dataflow mapping is ambiguous, generate a review item instead of guessing silently.
- If replay fails, repair generated code using the original trace and error message.

Replay:

- Fail loudly on validation errors that would produce false success.
- Surface step index, trace source, and repair hint.

## 14. Migration From Current State

The new implementation should start from the upstream-style RPA path.

Keep:

- Existing manual recorder and locator candidate capture.
- Existing `RPAAssistant` / `RPAReActAgent` direct browser operation style as the recording-time base.
- Existing `PlaywrightGenerator` as the initial Skill generation fallback.
- Useful runtime AI helper concepts only when semantic judgment is truly needed.

Do not keep as default recording path:

- Recording-time Contract-first Planner/Compiler/Validator loop.
- Multi-step contract repair for every natural-language instruction.
- Heavy snapshot/re-plan/done-check cycle after every accepted action.
- Full DeepAgent as the default recording-time controller.
- Free-form LLM-generated JavaScript as the default browser scripting style.

Add:

- Trace store for accepted traces.
- Runtime results store.
- Dataflow trace inference.
- Left-side trace timeline.
- Post-hoc Skill compiler and replay repair loop.

## 15. Target Scenarios For First Complete Implementation

The first implementation should fully support these scenarios:

1. Manual-only Skill:
   - User manually opens pages, clicks, fills, and saves.
   - Timeline shows accepted manual traces.
   - Generated Skill replays successfully.

2. Deterministic natural-language operation:
   - User opens GitHub Trending.
   - User says "open the project with the highest star count".
   - Recording completes quickly with one accepted AI trace.
   - Generated Skill does not hard-code the selected repo URL; it recomputes from the current page.

3. Semantic natural-language operation:
   - User opens GitHub Trending.
   - User says "open the project most related to Python".
   - Recording records selected target and reason.
   - Generated Skill preserves runtime semantic selection only for this step.

4. Mixed manual + natural-language extraction:
   - User opens a project.
   - User manually enters PR page.
   - User asks for first 10 PR titles and creators.
   - Timeline shows manual navigation and data capture.
   - Generated Skill replays and returns a non-empty structured array.

5. A-to-B dataflow:
   - User extracts structured data from page A.
   - User navigates to page B manually or by natural language.
   - User fills fields manually or asks AI to fill them.
   - Trace records source data and target field mappings.
   - Generated Skill fills B using extracted values, not recording-time literals.

## 16. Architectural Trade-offs

This design intentionally accepts less abstraction during recording in exchange for better interactive experience.

Benefits:

- Fewer LLM calls during recording.
- Less opportunity for schema/contract mismatch.
- Better alignment with the upstream experience that has proven more usable.
- More complete context for post-hoc generalization because the compiler sees the full trace, not just one current step.
- Easier debugging because accepted traces are factual records.

Costs:

- Skill generation becomes more important and may take longer.
- Some generalization happens later, so the first generated script may need replay repair.
- Dataflow inference requires careful trace capture and may need user review when ambiguous.
- Runtime AI boundaries still need discipline to avoid token-heavy replay.

The trade-off is deliberate: slow work belongs after recording, not in the user's interactive loop.

## 17. Guardrails

These guardrails are mandatory because prior attempts failed by letting complexity creep back into the recording path.

- RecordingRuntimeAgent is Micro-ReAct only, not an open-ended ReAct or DeepAgent loop.
- Normal successful natural-language recording commands should use one LLM call.
- Execution failure may trigger at most one repair LLM call.
- Python Playwright is the default generated scripting style.
- Free-form complex JavaScript from the LLM is avoided by default.
- JavaScript is allowed only for small read-only extraction snippets or system-owned templates.
- Failed attempts are diagnostics, never primary timeline steps.
- Skill compilation defaults to deterministic orchestration plus targeted LLM calls.
- DeepAgent is optional for advanced post-hoc repair, not the default compilation path.
- Any new abstraction must either shorten the recording path or improve replay reliability; otherwise it should not be added.

## 18. Open Extension Points

The design leaves room for:

- More deterministic compilation patterns.
- Field mapping confirmation UI.
- Trace diff and replay diagnostics.
- Domain-specific compilers for common sites.
- Optional advanced Contract manifest generated after recording, not during recording.
- A future optimizer that merges adjacent traces into more compact replay code.

These extension points should not block the first implementation.
