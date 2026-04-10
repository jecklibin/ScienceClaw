# RPA Locator Fallback Design

## Summary

Recorder V2 already captures locator diagnostics for each recorded action:

- a selected primary locator in `target`
- a ranked `locator_candidates` list
- validation metadata including whether the selected locator is uniquely resolvable

However, replay generation still treats `target` as the single source of truth. This causes failures on pages where the recorded primary locator is semantically good but not unique at replay time.

The current failure mode is visible in strict Playwright errors such as:

- `get_by_title(".dockerignore", exact=True)` resolves to multiple elements
- replay aborts before the action can execute

This design upgrades replay generation so it consumes the recorded diagnostics instead of ignoring them. The generator should prefer unique candidates, attempt narrower scoped locators when available, and only fall back to `.first()` as a last resort.

## Goals

- Prevent replay from failing on locator strictness when the recording already contains better alternatives
- Reuse existing recorder metadata instead of inventing a second locator-resolution system
- Keep fallback behavior explicit and ordered
- Improve replay success on repeated-content pages such as GitHub file trees and result lists
- Preserve current recorder payload compatibility

## Non-Goals

- Rewriting the browser-side locator generation heuristics
- Guaranteeing perfect replay when every recorded candidate is ambiguous
- Hiding all ambiguity by silently clicking the first match by default
- Introducing a second persistence format for recorded steps

## Current Problem

Today the system does two different things:

1. Recorder side:
   - builds multiple candidates
   - computes `strict_match_count`
   - marks validation as `ok` or `fallback`
2. Replay generation side:
   - directly converts `target` into Playwright code
   - does not use `locator_candidates`
   - does not react to non-unique validation

As a result, the system can know that a locator is ambiguous and still generate code that fails under Playwright strict mode.

## Design Principles

- Replay should consume recorder diagnostics, not discard them
- Unique candidates are always preferred over ambiguous candidates
- Structure-aware narrowing is better than positional fallback
- `.first()` is allowed only as the final explicit fallback
- Generated code should make fallback intent understandable during debugging

## Chosen Approach

### Replay Candidate Selection Pipeline

For each replayable action that uses a locator:

1. Inspect the selected locator in `target`
2. Inspect `validation`
3. Inspect `locator_candidates`
4. Choose the final replay locator using ordered fallback rules

### Ordered Fallback Rules

The generator should use this order:

1. Keep the selected primary locator when it is validated as uniquely resolvable
2. Otherwise, choose the best candidate from `locator_candidates` with `strict_match_count == 1`
3. If no unique plain candidate exists, prefer a narrower structured candidate such as:
   - `nested`
   - parent-child scoped locator
   - unique CSS fallback already captured by the recorder
4. If all candidates remain ambiguous, generate an explicit `.first()` fallback on the best available locator

This implements the agreed behavior:

- prefer scoped narrowing first
- fall back to `.first()` only when narrowing cannot produce uniqueness

## Selection Heuristics

When multiple unique candidates exist, prefer candidates in this order:

1. selected candidate if it is unique
2. nested/scoped candidate
3. `role + name`
4. `testid`
5. `label`
6. `placeholder`
7. `alt`
8. `text`
9. `title`
10. CSS fallback

This ordering intentionally prefers structure-preserving locators over content-only locators when both are available.

## Generator Changes

### New Internal Resolver

Add an internal generator helper that receives the recorded step and produces:

- the chosen locator payload
- whether the locator used a fallback path
- which fallback phase was used

Suggested return shape:

```python
{
    "locator": {...},
    "source": "target|candidate|scoped|first_fallback",
    "used_first_fallback": False,
    "details": "selected unique candidate"
}
```

This helper stays internal to `generator.py`.

### Script Emission

The emitted Playwright expression should reflect the chosen locator:

- normal unique candidate:
  - `page.get_by_role(...)`
- explicit `.first()` fallback:
  - `page.get_by_title(..., exact=True).first`

When `.first()` is used, generated code should include a short comment noting that strict uniqueness was unavailable and positional fallback was used.

Example:

```python
    # Fallback: recorder candidates were ambiguous, using first matching locator
    await current_page.get_by_title(".dockerignore", exact=True).first.click()
```

## Data Contract Usage

No API schema changes are required.

The generator will consume these existing step fields:

- `target`
- `locator_candidates`
- `validation`

If a step lacks candidates or validation, replay should preserve current behavior and use `target` directly.

## Error Handling

If a step has malformed locator candidate data:

- log the issue
- fall back to the current `target` path

If a step has only ambiguous candidates:

- emit `.first()` fallback
- keep the replay executable

This keeps replay robust while preserving observability.

## Testing Strategy

Add generator-focused tests covering at least:

1. Primary locator ambiguous, unique alternative candidate exists
   - expected: generator chooses the unique candidate
2. Primary locator ambiguous, nested/scoped candidate exists
   - expected: generator chooses the scoped candidate
3. All candidates ambiguous
   - expected: generator emits `.first()` fallback on the best candidate
4. Step has no candidate metadata
   - expected: generator preserves current behavior

Tests should assert generated script text, not only helper return values, so replay behavior is locked in at the final output layer.

## Risks

- Over-preferencing CSS can make replay brittle if ordering is wrong
- Automatically using `.first()` can hide real ambiguity if applied too early
- Inconsistent candidate shapes across old recordings may force fallback more often than expected

These risks are why the fallback order must remain:

- unique candidate first
- scoped narrowing second
- `.first()` last

## Implementation Scope

This is intentionally scoped to one implementation cycle:

- update generator selection logic
- add focused tests
- do not modify recorder payload format
- do not redesign configure UI in the same change

## Acceptance Criteria

- Replays no longer fail in cases where the selected primary locator is ambiguous but a unique candidate was already recorded
- Replays prefer scoped/nested candidates before positional fallback
- `.first()` appears only when all available candidates are ambiguous
- Existing steps without candidate metadata still generate usable scripts
