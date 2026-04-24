# TraceSkillCompiler Generalization Strategy

## Purpose

`TraceSkillCompiler` converts accepted recording traces into replayable Skill code. Its job is not to recreate the recording exactly. Its job is to preserve the SOP intent while removing recording-time accidents such as fixed URLs, fixed list items, or fixed field values.

This document describes generalization principles. Site-specific examples such as GitHub or Baidu are validation samples, not the architecture.

## Core Principle

```text
Observed recording values are evidence, not replay logic.
```

Examples of observed values:

- The repository URL selected during recording.
- The issue title visible during recording.
- The project name copied during recording.
- The search keyword typed during recording.
- The row text extracted from a list during recording.

Observed values may help infer structure, suffixes, field names, and validation expectations. They should not be hard-coded into replay when a dynamic source exists.

## Generalization Problem Types

### 1. Cross-step URL dependency

Pattern:

```text
Step A selects or opens an object.
Step B opens a subpage or related page for that object.
```

Replay should use:

```text
{Step A dynamic result URL} + {stable suffix inferred from Step B}
```

GitHub examples such as `/issues` or `/pulls` are instances of this pattern. The same abstraction applies to customer detail tabs, order subpages, report detail pages, or internal admin routes.

### 2. Recording-time value de-hardcoding

If a later step uses a value produced by an earlier step, replay should reference the earlier output.

Examples:

- Extract project name, then search that project name.
- Extract customer name, then fill a customer form.
- Select a list record, then open its detail page.

The compiler should prefer `_results` and `output_key` references over literals when the dependency is clear.

### 3. Dynamic collection extraction

Pattern:

```text
Find a collection -> extract fields -> validate output
```

Examples:

- Pull requests.
- Issues.
- Search results.
- Table rows.
- Order lists.

The compiler should generalize around collection structure and requested fields, not around the specific items observed during recording.

### 4. Stable subpage navigation

Manual clicks into stable subpages can often become URL construction when the current object base URL is known.

Examples:

- Open an object's issues tab.
- Open an object's PR tab.
- Open an object's activity tab.
- Open an object's attachments page.

The stable part is the suffix. The object base should come from current page state or a previous trace result.

### 5. Runtime semantic steps

Some steps should remain runtime AI because deterministic replay would incorrectly freeze a semantic judgment.

Examples:

- Most relevant.
- Best match.
- Highest risk.
- Most similar.
- Summarize current page.

The compiler should preserve these as structured runtime AI calls, with clear output keys for downstream deterministic steps.

## Site Examples Are Not Core Abstractions

GitHub currently appears often because it is a convenient public test site with lists, counters, tabs, issues, PRs, and repository pages. It should be treated as a testbed for these general abstractions:

- Ranking visible records by a numeric field.
- Opening a stable subpage of a dynamically selected object.
- Extracting records from a dynamic list.
- Preserving semantic selection when relevance cannot be deterministically encoded.

Baidu or other search engines are similarly only examples for:

- Cross-page dataflow into search.
- Search result navigation.
- Visible/editable input actionability.

Do not design compiler architecture around one site's DOM.

## Compiler Rules Of Thumb

- If an observed URL starts with a previous dynamic result URL, replace the base with the dynamic result reference and keep only the suffix.
- If a literal value equals a prior structured output field, prefer the result reference.
- If the user requested a count, page range, or fields, encode those constraints explicitly.
- If a task explicitly requires non-empty output, replay may validate non-empty output. Do not infer that contract solely from one non-empty recording sample.
- If a step is semantic, preserve runtime AI rather than pretending the recorded answer is universally valid.
- If generalization is ambiguous, prefer a review warning or conservative replay over silently inventing a dependency.

## Anti-patterns

- Hard-coding the recorded selected object when the previous step selected it dynamically.
- Treating a specific site's selector as a universal abstraction.
- Replacing semantic judgment with the recorded result.
- Adding keyword rules that become the main source of task understanding.
- Optimizing one successful E2E case in a way that makes other sites harder to support.

## Pending: Redundant Action Handling

Trace-first recording intentionally preserves what the user actually did, including accidental or redundant actions. This is correct for recording, but replay may need to remove recording-time accidents that no longer make sense after page state changes.

Example pattern:

```text
Fill password -> press Enter -> click "Login"
```

If `press Enter` already submits the form and moves the page to a new state, replaying the following `click "Login"` can become a stale old-page action and fail with a timeout.

This is not a login-page special case. The same abstraction appears in:

- Enter-to-submit plus button click
- Search box Enter plus search button click
- Repeated save/submit clicks after the first click already succeeded
- Expand/collapse toggles where the second action targets the pre-transition state

Planned handling order:

1. Restore manual trace deletion/editing in the recorder/configure UI so users can remove obvious redundant traces.
2. Add compiler-stage redundant action reduction for high-confidence cases where an earlier action already caused page-state transition and the following action still targets the old page.
3. Be cautious about execution-time auto-skip rules; they should only be considered after recorder editing and compiler-stage handling are in place, because runtime skipping can easily become experience-driven behavior instead of fact-driven compilation.

Guardrails:

- Keep recording factual; do not suppress actions during live recording just because they look redundant.
- Prefer user-visible editing or compiler-stage cleanup over runtime heuristics.
- Generalize around page-state transition and duplicate intent, not around site-specific button labels such as "登录" or "搜索".

## Pending: Empty Output Contract

Some extraction tasks legitimately return an empty array or empty object at replay time. A non-empty recording sample is evidence that the page once had data, but it is not by itself a stable replay contract.

Short-term choice:

- Disable automatic compiler-inserted `_validate_non_empty_records(...)` checks.

Longer-term direction:

- Model emptiness as part of the task contract, not as an inference from one recording sample.
- Only enforce non-empty output when the user intent or trace metadata explicitly requires it.
- Keep empty outputs available to diagnostics and repair analysis instead of hard-coding them into generic replay behavior.
