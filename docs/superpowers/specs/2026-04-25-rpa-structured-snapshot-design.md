# RPA Structured Snapshot Design

> Status: Core implementation landed.
>
> Snapshot v2 currently collects `table_views` and `detail_views` in `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`; `snapshot_compression.py` preserves compact structured facts; `recording_runtime_agent.py` uses them for prompt context and guarded table/ordinal overlay plans. Treat future-branch wording below as historical.

## Goal

Improve natural-language browser operation accuracy for internal enterprise pages by upgrading recording-time page evidence from compressed DOM regions to structured page facts.

The first target scenarios are:

- Table and grid operations such as "click the file name in the first row" or "click the download action in row 2".
- Detail panel extraction such as "extract fields from the purchase information section".
- Cross-step data flow where extracted structured data is later used to fill another page.

The design must protect the current trace-first recording and script compilation path. Existing accepted traces and deterministic replay should continue to work while the new snapshot facts are introduced.

## Current Problem

The current snapshot path is better than raw DOM truncation, but it still presents mostly region summaries:

- `expanded_regions`
- `sampled_regions`
- `region_catalogue`

That shape works for simple pages but loses critical structure on complex internal systems.

For tables, current evidence contains headers and sampled rows, but does not preserve row/column/cell/action relationships. A command like "click the first row's file name" needs:

```text
table -> row[0] -> column "file_name" -> link action
```

not:

```text
visible text "File_189.xlsx"
```

For detail panels, current label-value detection depends heavily on labels, classes, data attributes, and visual position. Internal pages often express fields through nested UI framework containers:

```text
collapse section -> field panel -> form item -> label area + content area
```

The LLM needs that field structure directly instead of a general text section or broad summary.

## Design Principles

### Page Facts, Not Site Rules

The new layer should expose page facts:

- rows
- columns
- cells
- row-local actions
- sections
- fields
- labels
- values
- editable controls

Framework-specific signals such as `data-colid`, `aui-grid__body`, or `aui-form-item` are only evidence sources. They must not become the architecture.

### Trace-First Remains The Main Architecture

Recording still means:

```text
operate the browser -> record factual trace -> compile accepted traces later
```

The structured snapshot should improve the evidence given to the planner and deterministic runtime lanes. It should not reintroduce contract-first planning during recording.

### Additive First, Replacement Later

The first implementation should add structured views beside existing region output:

- keep `expanded_regions`
- keep `sampled_regions`
- keep `region_catalogue`
- add `table_views`
- add `detail_views`

Once the new views are validated, old region compression can be simplified or reduced. The long-term target is one structured snapshot model, not two permanent systems.

### Deterministic Lanes Must Be Guarded

High-confidence table or ordinal actions may bypass the LLM planner and generate deterministic Playwright code. That takeover is allowed only when the page facts are clear. Otherwise the system must fall back to the existing planner path.

## External Reference Alignment

This direction matches mainstream browser automation practice.

Playwright recommends locators based on user-facing semantics such as role, label, text, and test id instead of fragile DOM paths. Structured snapshots should similarly surface user-facing semantics while preserving executable locator hints.

Playwright aria snapshots show the same principle from another angle: the useful representation for reasoning is a structured semantic tree, not full HTML text.

Browser-agent tools such as OpenClaw expose browser snapshots plus action references, then execute through controlled browser operations. The model should choose from page facts; execution should ground that choice through stable locators or references.

## Proposed Snapshot Shape

### Top-Level Payload

```json
{
  "mode": "structured_snapshot",
  "url": "https://example.internal/page",
  "title": "Example Page",
  "table_views": [],
  "detail_views": [],
  "expanded_regions": [],
  "sampled_regions": [],
  "region_catalogue": []
}
```

`expanded_regions`, `sampled_regions`, and `region_catalogue` remain during rollout. The prompt should prefer `table_views` and `detail_views` for matching tasks.

### Table View

A table view represents one logical table or grid, even when the UI renders header and body as separate tables.

```json
{
  "kind": "table_view",
  "title": "",
  "framework_hint": "aui-grid",
  "row_count_observed": 10,
  "columns": [
    {
      "index": 0,
      "column_id": "col_23",
      "header": "",
      "role": "row_index"
    },
    {
      "index": 1,
      "column_id": "col_24",
      "header": "",
      "role": "selection"
    },
    {
      "index": 2,
      "column_id": "col_25",
      "header": "file_name",
      "role": "file_link"
    }
  ],
  "rows": [
    {
      "index": 0,
      "cells": [
        {
          "column_id": "col_25",
          "column_header": "file_name",
          "text": "File_189.xlsx",
          "actions": [
            {
              "kind": "link",
              "label": "File_189.xlsx",
              "locator": {
                "method": "relative_css",
                "scope": "row",
                "value": "td[data-colid='col_25'] a"
              }
            }
          ]
        }
      ],
      "locator_hints": [
        {
          "kind": "playwright",
          "expression": "page.locator('table.aui-grid__body tbody tr').nth(0)"
        }
      ]
    }
  ],
  "auxiliary_text": [
    {
      "kind": "empty_state",
      "text": "no_data",
      "outside_rows": true
    }
  ]
}
```

#### Table Detection

Detection should prefer general evidence:

1. Native `table`, `thead`, `tbody`, `tr`, `th`, `td`.
2. ARIA grid/table roles.
3. Repeated row-like div structures.
4. Framework hints when they improve confidence.

#### Header And Body Pairing

Header/body pairing should use evidence in this order:

1. Shared `data-colid`.
2. Shared column classes such as `col_25`.
3. `aria-colindex` or similar column index attributes.
4. Visual x-position alignment.
5. Plain column order as a final fallback.

The supplied table sample uses `data-colid`, so the first implementation can support it cleanly while keeping fallback hooks for other frameworks.

#### Row-Local Actions

Actions inside a cell must be scoped to the row and column. The planner should be guided to generate relative code:

```python
row = page.locator("table.aui-grid__body tbody tr").nth(0)
await row.locator("td[data-colid='col_25'] a").click()
```

It should avoid page-global text selectors for ordinal table tasks:

```python
await page.get_by_text("File_189.xlsx").click()
```

### Detail View

A detail view represents one visible business section, panel, card, or collapse item.

```json
{
  "kind": "detail_view",
  "section_title": "purchase_info",
  "section_locator": {
    "method": "text",
    "value": "purchase_info"
  },
  "fields": [
    {
      "label": "estimated_total_amount",
      "value": "100.00",
      "data_prop": "2652409177955720363",
      "required": true,
      "visible": true,
      "value_kind": "number",
      "locator_hints": [
        {
          "kind": "field_container",
          "expression": "page.locator('[data-prop=\"2652409177955720363\"]')"
        }
      ]
    },
    {
      "label": "currency",
      "value": "USD",
      "data_prop": "8893190997723929912",
      "required": true,
      "visible": true,
      "value_kind": "text"
    }
  ]
}
```

#### Detail Detection

Detection should prefer:

1. Semantic sections: `section`, `article`, `form`, `fieldset`, role `region` or `group`.
2. Framework field containers: classes like `form-item`, stable attributes like `data-prop`.
3. Definition lists: `dt` and `dd`.
4. Label-control relationships: `label[for]`, `aria-labelledby`, `aria-label`.
5. Visual fallback: nearby label/value text by layout position.

The supplied detail sample exposes strong field containers through `aui-form-item` and `data-prop`. These should be treated as evidence for the general "field container" concept.

#### Hidden Fields

Hidden fields should be recorded as facts but not expanded by default for planner decisions.

```json
{
  "label": "request_title",
  "value": "REQ-20260425-001",
  "visible": false,
  "hidden_reason": "display_none"
}
```

This prevents hidden data from polluting visible operations while preserving diagnostic evidence.

#### Non-Business Buttons Inside Fields

Display-only widgets may contain internal buttons such as numeric increase/decrease controls. These should not be promoted as primary business actions unless the user explicitly asks for that widget operation.

## Prompt Contract

The recording runtime prompt should add these rules:

- For table/list tasks, inspect `table_views` before `expanded_regions`.
- For ordinal table tasks, prefer row-relative and column-relative locators.
- For detail extraction, inspect `detail_views` before scanning generic text or tables.
- Treat hidden fields as diagnostic unless the user explicitly asks for hidden/default/internal values.
- Return structured dict/list values for extraction steps that may feed later form filling.
- Do not use observed row text as the primary selector when the instruction is ordinal.

## Runtime Deterministic Lanes

### Table Ordinal Lane

The existing ordinal overlay handles some repeated item cases. It should be extended or refactored into a table-aware lane that can handle:

- first row file name
- second row status
- first row checkbox
- first row link in a named column
- first N row values from a named column

Takeover is allowed only when:

1. A table view exists.
2. Rows are ordered.
3. The target column is identified by header, role, or explicit column index.
4. The requested action is row-local.

Otherwise, fall back to the planner.

### Detail Extraction Lane

The first version should not bypass the planner for all detail extraction. Instead, it should provide strong `detail_views` to the planner and add tests that verify the planner payload contains correct fields.

Simple deterministic extraction for commands such as "extract all fields from purchase information" can be introduced after the evidence model is validated.

## Budget Policy

Increase the default `char_budget` from `20000` to `60000`.

This is justified because the target internal models have at least 128k context, and accuracy is prioritized over extreme compression.

The budget increase is not the main fix. It only gives the structured views enough room during rollout.

The raw snapshot collection caps should also be reviewed. If a page has many rows and fields, collecting only 120 actionable nodes and 160 content nodes may lose evidence before compression begins.

## Compatibility And Rollout

### Phase 1

- Add `table_views` and `detail_views`.
- Keep existing region payload.
- Raise compact budget to 60000.
- Update prompt to prefer structured views.
- Add debug metrics for structured view counts.
- Add unit tests using internal sample-shaped fixtures.

### Phase 2

- Add table ordinal deterministic lane.
- Keep strict fallback to current planner.
- Record table grounding evidence in accepted trace metadata if needed.

### Phase 3

- Reduce reliance on older region compression where structured views cover the same page facts.
- Keep old region output only as fallback or diagnostics if it still adds value.

## Non-Goals

- Do not build a site-specific AUI rule engine.
- Do not replace `TraceSkillCompiler`.
- Do not add multi-round repair loops.
- Do not introduce embedding or vector search in the first version.
- Do not rewrite generated skills around a new replay architecture.
- Do not block non-dangerous recording-time actions because selectors look weak.

## Files Likely To Change

- `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
  - collect additional structured table/detail facts in the browser context.
- `RpaClaw/backend/rpa/snapshot_compression.py`
  - include `table_views` and `detail_views` in compact snapshot output.
  - raise default char budget.
- `RpaClaw/backend/rpa/recording_runtime_agent.py`
  - update prompt.
  - optionally extend ordinal overlay into table-aware deterministic lane.
  - include structured view diagnostics.
- `RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py`
  - verify snapshot JS includes table/detail extraction logic.
- `RpaClaw/backend/tests/test_rpa_snapshot_compression.py`
  - verify structured views are preserved and budget behavior remains stable.
- `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`
  - verify planner payload contains structured views.
  - verify table ordinal lane only takes over under high confidence.

## Test Fixtures

Use sample-shaped fixtures based on the sanitized table and detail HTML examples provided outside the repository:

- table sample: split header/body grid, `data-colid`, row-local file links, checkbox cells, empty-state text, tooltip text.
- detail sample: collapse sections, `aui-form-item`, `data-prop`, label/content separation, display-only values, hidden fields.

Fixtures should be checked into tests as minimal sanitized structures rather than reading from the external sample directory.

## Acceptance Criteria

1. A split-header/body table with matching `data-colid` produces one logical `table_view`.
2. `table_view.columns` binds headers to body cells by `data-colid`.
3. `table_view.rows[0]` exposes the file-name link as a row-local action under the file-name column.
4. Empty-state text is marked as auxiliary text outside table rows.
5. Tooltip text outside the table is not mixed into table rows.
6. A collapse-panel detail page produces separate `detail_views` for sections such as basic information and purchase information.
7. Detail fields preserve label, value, data-prop, required flag, visible flag, and value kind.
8. Hidden fields are represented but not prioritized in planner instructions.
9. The compact snapshot default budget is 60000.
10. Existing `expanded_regions`, `sampled_regions`, and `region_catalogue` remain available during Phase 1.
11. Current trace-first accepted trace compilation tests continue to pass.

## Risks

### Overfitting To AUI

Mitigation: keep AUI class handling inside generic evidence functions. Tests should name the business behavior, not the framework.

### Snapshot Payload Growth

Mitigation: cap rows and fields per view, summarize overflow explicitly, and rely on the 60000 budget during rollout.

### Incorrect Deterministic Takeover

Mitigation: table ordinal lane must require clear table, row, column, and row-local action evidence. Otherwise it falls back.

### Compiler Regression

Mitigation: avoid large compiler changes in Phase 1. New snapshot facts should improve recording-time planning without changing skill replay semantics.

## Decision

Proceed with a structured snapshot layer on the `codex/trace-first-structured-snapshot` branch.

Implement `table_views` and `detail_views` first, keep the existing region payload as rollout compatibility, raise the budget to 60000, then add guarded table ordinal handling.
