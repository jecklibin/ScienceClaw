# Jalor grid snapshot and recording regression notes

## Background

Two reverted commits explored support for an internal Jalor grid workflow:

- `1ecaf7e fix: support Jalor grid snapshot tables`
- `afd0693 fix: preserve hover trigger across body clicks`

They were reverted because the end-to-end recording flow still generated an invalid `click body` trace after a table row-selection step, causing a later menu item click to fail during replay. The partial solution remains useful and should be revisited with better recording-event evidence.

## Working part

The Jalor table snapshot idea solved a real problem: Jalor renders header and body as separate tables under one `.jalor-igrid` container. A natural-language command such as "click the file name in the first row of the table" needs a single structured `table_view` built from that outer grid, not from page-level `tbody tr` selectors.

The useful direction was:

- Detect only the generic Jalor grid container: `.jalor-igrid`.
- Read headers from `.jalor-igrid-head tbody.igrid-head td`.
- Read body rows from `.jalor-igrid-body tbody.igrid-data tr.grid-row`.
- Skip grouping rows such as `tr.grid-row-group`.
- Associate columns by `field` first, `_col` second, and visual index last.
- Generate scoped row hints such as:

```python
page.locator('#taskExportGridTable tbody.igrid-data tr.grid-row').nth(0)
```

- Generate row-relative cell actions such as:

```css
td[field="tmpName"] a
```

This is a framework-level adaptation, not a page-specific rule. It should not depend on page title, menu text, URL, export page names, or observed file names.

## Regression observed

The failing workflow was:

1. Click a hover/dropdown trigger button.
2. Click a menu item such as "export all columns", opening a new page.
3. On the new page, run the natural-language Jalor table command; this part worked after the snapshot adaptation.
4. Finish recording and generate the script.
5. Replay failed because an earlier dropdown trigger step was generated as:

```python
await current_page.locator('body').first.click()
```

Further observation: clicking the hover/dropdown trigger directly recorded normally. The bad `click body` appeared after selecting all rows in the table below the button and then clicking the hover/dropdown trigger.

## Current hypothesis

The root problem is likely in the recording-event chain, not in trace compilation.

After Jalor row selection, the UI may change focus, rebuild toolbar DOM, create an overlay, or delegate the actual action through `mousedown` while the later `click` event target falls back to `body`. The recorder currently treats `body` as a valid CSS locator, so the trace can be polluted with a non-business click.

The attempted guard in `afd0693` only handled one case: `click body` occurring between a pending hover trigger and a later menu item. It did not solve the reported end-to-end case, so the real event sequence likely differs from that simplified test.

## Evidence needed before the next fix

Before implementing another repair, capture raw recording events for this exact sequence:

1. Select all rows in the Jalor table.
2. Move to or click the hover/dropdown trigger.
3. Click the menu item such as "export all columns".

For each event, preserve:

- `action`
- `tag`
- `locator`
- `locator_candidates`
- `validation`
- `element_snapshot`
- `signals`
- `timestamp`
- `sequence`
- `tab_id`
- `frame_path`

The key question is where `body` first appears:

- Capture JS `retarget()` already returned `body`.
- Playwright recorder produced only a weak body locator for the real button.
- Manager normalized a better candidate into `body`.
- A non-menu event cleared the pending hover candidate before the menu item click.

## Safer future approach

Do not add a page-specific rule for this export page. The next implementation should probably combine:

- Jalor grid snapshot support, as above.
- A recording-layer rule that treats `body`/`html` click as weak evidence when a stronger nearby hover/click target exists.
- Diagnostics that record body-click event context instead of silently converting it into a final trace.

Any future fix should include a regression test based on the real raw event sequence, not only the simplified hover -> body click -> menu item sequence.
