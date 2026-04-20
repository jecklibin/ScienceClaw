# RPA Control-Flow Recording Enhancement Design

## Goal

Enhance the RPA skill recording assistant so it can handle runtime logic such as
conditions, polling loops, basic branching, and explicit delays while continuing
to reuse Playwright locators and action semantics wherever possible.

The motivating scenario is:

> If the first list item is not complete, click refresh every 500 ms until the
> status becomes complete, then click the first item's name to download it.

This must be exported as a reliable skill. The generated code must keep the
selection dynamic at replay time instead of hard-coding the item name observed
during recording.

## Context

The current recorder is strong for linear workflows:

- Browser-side recorder captures Playwright-style locator candidates.
- `RPAStep` stores actions, targets, frame paths, signals, validation, and
  locator candidates.
- `PlaywrightGenerator` turns recorded steps into async Playwright Python.
- AI assistant runtime already supports structured actions, collection hints,
  ordinal selection, frame-aware snapshots, and direct `ai_script` execution.
- Download and popup signals are already materialized with Playwright
  `expect_download()` and `expect_popup()` wrappers.

The weak point is not basic locator generation. The weak point is that the final
recording is primarily a linear list of browser actions. Repeated refresh clicks
or conditional behavior need runtime state checks, but the current recording
shape does not express those checks directly.

## Design Decision

Use a hybrid model:

1. Keep ordinary click, fill, press, navigate, extract, and download workflows as
   structured steps.
2. Detect explicit control-flow intent and upgrade only that subtask to a single
   advanced Playwright script step.
3. Store the advanced step as `action = "ai_script"` for compatibility, but add
   structured diagnostics describing why it was upgraded and which logical
   template it follows.

This avoids a full visual workflow DSL in the first version while preserving a
clear path to one later.

## Alternatives Considered

### Prompt-only script generation

The assistant could simply emit raw Python whenever it sees condition or loop
language. This is fast to implement, but too much behavior would live in
unstructured model output. Locator replacement, test diagnostics, and future UI
editing would be harder.

### Full control-flow DSL

A first-class workflow graph with condition nodes, loop nodes, wait nodes, and
download nodes would be the most editable long-term model. It is also a larger
project touching storage, configure UI, generator, test UI, and exported skill
format. It is not necessary for the first useful iteration.

### Hybrid advanced step

The recommended approach stores complex runtime logic as one advanced
Playwright-backed step while keeping metadata structured. It fits the current
generator, can reuse existing `ai_script` support, and prevents exploratory
refresh clicks from polluting the final step list.

## Trigger Rules

The assistant should upgrade a subtask to advanced script mode when the user
instruction or observed recording pattern contains:

- Conditional language: `if`, `else`, `unless`, `otherwise`, `如果`, `否则`,
  `不然`.
- Polling language: `until`, `wait until`, `repeat`, `retry`, `every N ms`,
  `直到`, `每隔`, `重复`, `轮询`.
- State-dependent selection: first item whose status is complete, item with
  latest date, highest score, lowest price, or similar runtime comparisons.
- Runtime branching based on extracted page text.
- Repeated refresh or retry clicks followed by a state check.

The assistant should stay in structured mode for:

- Click the first or nth item when no condition is involved.
- Fill a field, submit, and download when the path is static.
- Extract visible text.
- Navigate to a known URL.
- One-off waits that do not require state checks.

## Stored Step Shape

Advanced control-flow steps continue using `RPAStep.action = "ai_script"`.

Required fields:

```json
{
  "action": "ai_script",
  "source": "ai",
  "description": "Wait for the first item to complete, then download it",
  "prompt": "If the first list item is not complete, refresh every 500 ms until it is complete, then click the first item's name to download it.",
  "value": "async def run(page): ...",
  "assistant_diagnostics": {
    "execution_mode": "code",
    "upgrade_reason": "polling_loop",
    "template": "poll_until_text_then_download",
    "interval_ms": 500,
    "timeout_ms": 60000,
    "condition": {
      "scope": "first_collection_item",
      "target": "status_text",
      "operator": "contains",
      "expected": "完成"
    },
    "locators": {
      "collection": {},
      "item": {},
      "status": {},
      "refresh": {},
      "download_target": {}
    }
  }
}
```

The `value` field stores a normalized full function body:

```python
async def run(page):
    ...
```

The generator should not need to guess whether it received a raw body or a
complete function.

## Locator Strategy

The advanced step must still reuse Playwright locator behavior. The generated
script should prefer:

1. Existing browser-captured locator candidates for refresh and download
   targets.
2. Existing collection detection for list/table/card containers.
3. Relative locators inside the selected collection item.
4. Playwright role, label, text, placeholder, and test id APIs before raw CSS.
5. CSS only as a fallback.

For the motivating scenario, locator ownership should be:

- `collection`: the list, table, or card group containing items.
- `item`: the repeated row/card selector, usually with `first`.
- `status`: a relative locator inside the first item, preferably derived from a
  status column, label, or stable text region.
- `refresh`: the refresh button or icon button.
- `download_target`: the first item's name link or download control.

The generated code must use relative locators for status and download target
inside the first item whenever possible. It must not use a global "first link"
unless no item scope can be inferred.

## Script Template

The first control-flow template should be `poll_until_text_then_download`.

Template behavior:

1. Resolve the item scope at runtime.
2. Read status text.
3. If it already satisfies the condition, skip refresh.
4. Otherwise click refresh, wait `interval_ms`, and re-resolve the item scope.
5. Repeat until satisfied or `timeout_ms` is reached.
6. Wrap the final click with `expect_download()`.
7. Save the downloaded file through the existing `_downloads_dir` behavior when
   exported or tested through the current generator/executor path.
8. Return structured download metadata.

Representative output:

```python
async def run(page):
    interval_ms = 500
    timeout_ms = 60000
    elapsed_ms = 0

    while elapsed_ms <= timeout_ms:
        first_item = page.locator("table tbody tr").first
        status_text = (await first_item.locator("[data-col='status']").inner_text()).strip()
        if "完成" in status_text:
            break
        await page.get_by_role("button", name="刷新").click()
        await page.wait_for_timeout(interval_ms)
        elapsed_ms += interval_ms
    else:
        raise TimeoutError("The first item did not reach the expected status before timeout.")

    async with page.expect_download() as download_info:
        await first_item.get_by_role("link").first.click()

    download = await download_info.value
    return {"download_filename": download.suggested_filename}
```

The actual implementation should fill the locator expressions from resolved
metadata and candidate locators, not from this illustrative CSS.

## Assistant Planning Changes

The assistant should choose an execution mode before returning a step:

```json
{
  "thought": "The task requires polling page state before the final download.",
  "action": "execute",
  "execution_mode": "code",
  "upgrade_reason": "polling_loop",
  "template": "poll_until_text_then_download",
  "description": "Wait for the first item to complete, then download it",
  "code": "async def run(page): ..."
}
```

Rules:

- Use `execution_mode = "structured"` for atomic actions.
- Use `execution_mode = "code"` only for the smallest subtask that needs
  runtime logic.
- Do not collapse an entire multi-page workflow into one script if only one
  segment requires polling or branching.
- Do not commit failed exploratory actions as recording steps.
- Keep failed attempts in assistant/debug trace only.

## Snapshot Enhancements

To generate reliable control-flow code, page snapshots need more than
interactive elements. The snapshot should expose:

- Repeated collections: table rows, list items, cards.
- Visible text for each item.
- Column headers when a table-like layout is detected.
- Relative interactive controls inside each item.
- Candidate status fields inside each item.
- Frame path for each collection and item.

This can build on `assistant_runtime.py` collection detection. The first
implementation should keep heuristics conservative and only create advanced
script steps when a plausible collection, status text, refresh control, and
download target are available.

## Test And Retry Behavior

The test flow should report advanced-step failures at the logical locator level:

- `collection_not_found`
- `status_not_found`
- `refresh_not_found`
- `download_target_not_found`
- `condition_timeout`
- `download_not_triggered`

When possible, the test page should show the same locator candidate replacement
UI already used for ordinary failed steps. Candidate replacement should update
the relevant entry under `assistant_diagnostics.locators`.

The loop must always have a bounded timeout. A default of 60 seconds is
reasonable for the first implementation, with `interval_ms` defaulting to 500
when the user explicitly says every 500 ms.

## Configure UI Behavior

The first version does not need a full visual branch/loop editor.

The configure page should show advanced steps as a single item with:

- Step type: advanced script.
- Upgrade reason: polling loop, condition, branch, or dynamic selection.
- Summary: "wait for first item status to contain 完成, refreshing every 500 ms,
  then download from that item."
- Inspectable script.
- Locator diagnostics when available.

Ordinary steps should remain unchanged.

## Export Behavior

`PlaywrightGenerator` already embeds `ai_script` steps. It should strengthen the
contract:

- Require normalized `async def run(page): ...` code.
- Convert sync Playwright calls to async as it does today.
- Preserve download results in `_results`.
- Add a short generated-code comment that the step was upgraded due to
  control-flow requirements.
- Avoid decomposing the advanced step into ordinary click or wait steps.

## Acceptance Criteria

The enhancement is complete when:

- Explicit conditional or polling instructions upgrade to one advanced script
  step.
- Ordinary static workflows remain structured steps.
- The motivating scenario works when the first item is already complete.
- The motivating scenario works when refresh must be clicked repeatedly.
- The loop stops with a clear timeout error when the status never becomes
  complete.
- The final download click is wrapped in Playwright `expect_download()`.
- The exported skill keeps dynamic runtime checks instead of hard-coding the
  observed item name.
- Failed exploratory attempts are not persisted in `session.steps`.
- Tests cover classification, generation, timeout behavior, and export.

## Risks And Mitigations

Risk: the assistant overuses script mode.

Mitigation: keep explicit trigger rules and default to structured mode for
atomic actions.

Risk: model-generated code becomes too free-form.

Mitigation: introduce named templates such as
`poll_until_text_then_download`; let the assistant fill structured fields and
use backend code to render the final Playwright code where practical.

Risk: status or download target locators are brittle.

Mitigation: use item-scoped relative locators and keep candidate replacement
available in the test UI.

Risk: polling loops hang.

Mitigation: require timeout and interval fields for every loop template.

Risk: existing exports break.

Mitigation: preserve `ai_script` as the storage action and only add optional
diagnostics fields.

