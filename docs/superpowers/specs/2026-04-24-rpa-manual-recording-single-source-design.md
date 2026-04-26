# RPA Manual Recording Single Source Design

> Status: Implemented with compatibility layers.
>
> The current code has `ManualRecordedAction` and `ManualRecordingDiagnostic` in `RpaClaw/backend/rpa/manual_recording_models.py`, normalizes manual actions in `manual_recording_normalizer.py`, and derives traces for compilation. Legacy `steps` and `traces` still exist for UI/API/compiler compatibility, so this document's "replace" language should be read as the target invariant, not a complete removal of old fields.

Branch: `codex/rpa-trace-first-recording`

Date: `2026-04-24`

## 1. Summary

This design restructures the **manual recording -> generate skill** path so that the system keeps **one persisted source of truth** for accepted recorded actions instead of maintaining both `steps` and `traces` as parallel durable models.

The redesign is motivated by a real failure in the current trace-first implementation:

- the recorder captured manual business-page actions after login
- the persisted `steps` still contained partially useful candidate data such as `selector` and `playwright_locator`
- the derived `traces` lost those candidates because they lacked canonical structured locators
- skill generation consumed `traces`, not `steps`
- replay failed with `Recorded click/fill action is missing a valid target locator`

This is not a one-off Huawei login special case. It exposed a deeper architecture flaw: the current manual recording path persists two "truth-like" models that can drift.

The recommended solution is:

- replace the current durable `steps + traces` split for manual recording with:
  - `RecordedAction`: the only persisted accepted action model
  - `RecordingDiagnostic`: persisted non-accepted diagnostic evidence
- derive any compiler-specific trace view from `RecordedAction` at generation time
- stop allowing malformed or under-specified manual actions to enter the accepted timeline

This design **does not** cover runtime AI recording or AI-originated traces. It only restructures the manual recording path.

## 2. Background

### 2.1 Existing Intent

The current system evolved toward two durable models:

- `steps`: recorder-facing step data used for UI display, editing, and manual action persistence
- `traces`: trace-first accepted facts intended for post-hoc compilation

The original intent was understandable:

- keep rawer recording-time state for UI and user editing
- keep accepted facts for compiler-stage generalization

Conceptually this looked like a view split, but the implementation became a **state duplication split**:

- both models are persisted
- both models can change
- both models can be consumed
- conversion between them is lossy

### 2.2 Current Generation Behavior

`route/rpa.py` currently generates scripts from `session.traces` whenever traces are present, and only falls back to the legacy `steps -> generator` path when traces are absent.

That means trace-first did not simply add a new internal abstraction. It changed the effective source of truth for manual-skill generation.

### 2.3 Real Failure That Triggered This Design

In the reproduced intranet scenario:

- login and auth redirect worked correctly
- post-login business-page actions were recorded with:
  - `target = ""`
  - descriptions equivalent to `click None` and `fill ... into None`
  - `locator_candidates` containing only `selector` / `playwright_locator`
  - `validation.status = ok`
- the step-to-trace conversion dropped those candidates because they were not canonical structured locators
- generated replay then failed fast because the trace had no valid target locator

This revealed a deeper issue than replay flakiness:

1. malformed manual actions were accepted into the durable primary timeline
2. `validation` was treated like display metadata instead of an enforced invariant
3. `step -> trace` was assumed to be cheap and safe, but it was actually a lossy translation
4. the compiler was asked to consume a second data model that no longer faithfully represented recorder reality

## 3. Problem Statement

The current manual recording architecture is not stable enough for long-term evolution because:

1. `steps` and `traces` are both truth-like durable models
2. conversion from `steps` to `traces` is lossy
3. malformed manual actions can be accepted as if they are replayable facts
4. product UI can imply "recording succeeded" even when the system lacks a canonical replay target
5. compiler behavior depends on a transformed model rather than the recorder's canonical accepted action model

This causes three categories of systemic risk:

- **truth drift**: UI and compiler operate on different durable views of the same action
- **late failure**: users first learn a step is invalid during test/generate instead of near recording time
- **patch pressure**: each new failure encourages adding more synchronization logic or more compiler heuristics

## 4. Goals

- Make manual recording more stable and easier to debug.
- Remove parallel durable truth models from the manual recording path.
- Ensure the accepted timeline contains only replayable actions.
- Preserve enough diagnostic evidence for the user to repair failed or under-specified actions.
- Keep trace-first post-hoc compilation as a compiler-phase concept, not a second persisted truth model for manual actions.
- Make future evolution easier for:
  - better locator parsing
  - better diagnostics UI
  - improved recorder vendor integration
  - stronger post-hoc generalization

## 5. Non-goals

- Do not redesign AI recording or runtime AI traces in this work.
- Do not solve every possible locator challenge across all future sites in this first refactor.
- Do not reintroduce Contract-first live recording complexity.
- Do not depend on site-specific heuristics such as Huawei login labels or page titles as architectural primitives.
- Do not preserve backward compatibility for pre-launch manual session data. The product is not yet launched, so clean architecture takes priority over historical compatibility.

## 6. What We Learned From Existing Versions

### 6.1 Why The Older Version "Seemed Fine"

The older version appeared more stable in some cases for reasons that are not strong architectural evidence:

1. skill generation was closer to directly consuming `steps`
2. older flows were more tolerant of incomplete targets and looser fallbacks
3. malformed recorded actions were often allowed to look like successful steps
4. some failures were delayed until replay and therefore less visible during architecture evaluation

The old behavior did not prove the architecture was sound. It only meant the system was more permissive about ambiguity.

### 6.2 What Trace-first Exposed

Trace-first did one useful thing here: it exposed latent recorder truth problems earlier and more honestly.

However, the current manual trace-first implementation also introduced an architectural weakness:

- it added a second durable truth-like model for manual actions
- it assumed manual step data could be converted into accepted traces without semantic loss

The intranet failure proved that assumption wrong.

## 7. Pitfalls We Hit

This section must stay in the final design because these are not abstract possibilities. They already happened.

### 7.1 Mistaking "an event happened" for "a replayable action was recorded"

The user really did click and type. But the system did not capture a stable canonical target. Recording the event is not the same as recording a replayable fact.

### 7.2 Treating validation as presentation metadata instead of a hard invariant

We observed impossible states such as:

- `validation.status = ok`
- `target = ""`
- selected candidate index points to a candidate that has no canonical locator

That should be impossible if validation is authoritative.

### 7.3 Assuming canonical locators are naturally available

Real recorder output may contain:

- vendor `selector`
- `playwright_locator`
- partial role information
- unnamed elements
- nth-like semantics
- dynamic or framework-generated elements

Canonical locator IR must be explicitly constructed and verified. It does not simply exist.

### 7.4 Allowing invalid actions into the main timeline

The product let bad actions look like valid recorded steps, which caused users to discover the truth only during generation or replay.

### 7.5 Asking the compiler to compensate for recorder truth loss

Post-hoc generalization is valid compiler work. Reconstructing what the user probably clicked when the recorder did not preserve the target is not.

## 8. Alternatives Considered

### 8.1 Keep `steps + traces`, tighten synchronization

#### Summary

Continue current architecture, but add stricter synchronization and more conversion logic.

#### Pros

- smaller code movement
- lower short-term migration effort

#### Cons

- keeps two durable truth-like models
- keeps long-term drift risk
- pushes more complexity into synchronization code
- encourages future bug fixes to remain local patches

#### Decision

Rejected. This treats the symptom, not the structural cause.

### 8.2 Accept bad steps into the timeline, but fail fast later

#### Summary

Keep recording almost everything into the main timeline, but improve generation-time rejection.

#### Pros

- easier to implement than a deeper refactor
- more honest than silent `body` fallbacks

#### Cons

- still too late in the flow
- timeline remains polluted by non-replayable actions
- users still perceive recording as successful until generate/test

#### Decision

Rejected. Better than old behavior, but still architecturally weak.

### 8.3 Hard-stop recording immediately on every under-specified step

#### Summary

Interrupt the user as soon as any step cannot be canonicalized.

#### Pros

- very strong data purity
- easy accepted timeline semantics

#### Cons

- poor recorder UX on dynamic pages
- risks false interruptions from transient page states
- too strict for the current product stage

#### Decision

Rejected. Overcorrects toward rigidity.

### 8.4 Persist only raw browser events and infer everything later

#### Summary

Store only low-level event logs and derive steps/actions entirely post-hoc.

#### Pros

- maximum raw evidence retention
- no early action model commitment

#### Cons

- much harder UI
- harder user editing
- greater system complexity
- overdesigned for the current need

#### Decision

Rejected. Too much scope and complexity.

### 8.5 Unify AI recording and manual recording now

#### Summary

Solve manual recording and runtime AI trace unification in one redesign.

#### Pros

- cleaner long-term conceptual model

#### Cons

- much broader scope
- delays solving the current manual recording reliability problem
- increases risk substantially

#### Decision

Rejected for this phase. AI trace unification belongs in a later phase.

## 9. Recommended Architecture

### 9.1 Core Decision

For the manual recording path, persist only one accepted-action truth model:

- `RecordedAction`: accepted and replayable

Persist non-accepted evidence separately:

- `RecordingDiagnostic`: informative, repairable, not replayable

Any compiler-stage trace abstraction becomes a **derived view**, not a persisted parallel truth model.

### 9.2 High-level Data Flow

```text
Browser capture
    ->
Recorder normalization
    ->
Acceptance gate
    -> accepted -> RecordedAction timeline
    -> rejected -> RecordingDiagnostic lane
    ->
Skill generation consumes only RecordedAction
```

### 9.3 Source of Truth Policy

- The accepted manual timeline is the only durable compiler input.
- Diagnostics are durable evidence, not generation inputs.
- Compiler trace views are temporary derivations.

## 10. Data Model

### 10.1 RecordedAction

This is the new primary persisted action model for manual recording.

Suggested fields:

- `id`
- `action_kind`
- `target`
- `value`
- `page_state`
- `signals`
- `validation`
- `element_snapshot`
- `raw_candidates`
- `sequence`
- `timestamp`
- `source = manual`

Required property:

- for interactive actions such as `click`, `fill`, `press`, `select`, `check`, `uncheck`, `target` must be a canonical locator IR

### 10.2 RecordingDiagnostic

Suggested fields:

- `diagnostic_id`
- `raw_event`
- `related_action_kind`
- `page_state`
- `element_snapshot`
- `raw_candidates`
- `failure_reason`
- `repair_options`
- `timestamp`

### 10.3 Canonical Locator IR

The manual recording path must normalize to one canonical locator model capable of representing:

- `role`
- `placeholder`
- `label`
- `text`
- `title`
- `alt`
- `css`
- `nested`
- `nth`

The system must support parsing canonical locator IR from:

- already structured locator payloads
- vendor selector metadata
- Playwright locator string forms when they map cleanly to supported IR

## 11. Acceptance Gate

The acceptance gate is the most important new boundary in the design.

For interactive manual actions:

- if canonical target resolution succeeds:
  - persist `RecordedAction`
- if canonical target resolution fails:
  - do not persist accepted action
  - persist `RecordingDiagnostic`

### 11.1 Mandatory Invariants

These must be enforced, not merely displayed:

1. `validation.status = ok` implies canonical `target` exists.
2. Accepted interactive actions must be compiler-consumable.
3. Diagnostics never enter the accepted timeline.
4. Display descriptions such as `click None` cannot be treated as truth.
5. `selector` or `playwright_locator` strings do not count as canonical targets until successfully normalized.

## 12. Product Behavior

### 12.1 Default Product Behavior

Recommended default:

- **diagnostic-first plus generation blocking**

That means:

- recording does not hard-stop on every failed normalization
- bad steps do not enter the accepted timeline
- bad steps are visible in diagnostics
- skill generation is blocked until unresolved diagnostics are addressed

### 12.2 Why This Default Is Recommended

It balances three goals:

- avoid pretending failure is success
- avoid over-interrupting users during recording
- keep the accepted timeline pure

### 12.3 UI Model

Accepted timeline:

- only `RecordedAction`

Diagnostic lane:

- unresolved recording diagnostics
- each diagnostic shows:
  - why the action was not accepted
  - raw candidates
  - element snapshot
  - user repair choices

Generation screen:

- block generation if unresolved diagnostics exist
- explain how many diagnostics remain and why

## 13. Responsibilities By Component

### 13.1 Recorder

Recorder should:

- capture real user interaction facts
- capture candidate metadata
- capture page context and element snapshot
- attempt canonical normalization

Recorder should not:

- fabricate success
- mark actions `ok` without canonical targets
- assume vendor string candidates are already acceptable final targets

### 13.2 Manager

Manager should:

- own ordering
- own signal binding
- own action acceptance vs. diagnostic routing
- enforce invariants

Manager should not:

- tolerate contradictory accepted states
- delay truth decisions until compiler time

### 13.3 Compiler

Compiler should:

- read accepted actions
- generalize
- generate replayable skill code

Compiler should not:

- recover missing truth from malformed actions
- decide whether a manual action was valid in the first place

## 14. Why `steps` And `traces` Need To Change

The old split is understandable in intent but flawed in implementation.

If the product still wants two concepts in the future, the relationship must change:

- one durable truth model
- one derived compiler view

The future equivalent of `trace` may still exist conceptually, but it cannot remain a second independently persisted truth model for manual actions.

## 15. Migration Direction

Because the product has not launched, we do not need to preserve old manual session compatibility.

Recommended migration direction:

- stop writing new manual sessions into dual `steps + traces`
- introduce the new accepted-action and diagnostic structures
- update generation to read only the new accepted manual action model
- treat legacy manual sessions as non-goals for this refactor

## 16. Implementation Phases

### Phase 1: Introduce the new manual recording truth model

- add `RecordedAction`
- add `RecordingDiagnostic`
- stop treating current manual `trace` as durable primary state

### Phase 2: Build the canonical locator normalization layer

- support structured locators
- support vendor selector metadata
- support recoverable Playwright locator strings
- add explicit nth support

### Phase 3: Add acceptance gating

- route malformed actions to diagnostics
- enforce validation invariants
- keep accepted timeline clean

### Phase 4: Update generation path

- compiler reads only accepted manual actions
- trace-like compiler view is derived transiently if needed

### Phase 5: Update UI

- accepted timeline
- diagnostic lane
- generation blocking on unresolved diagnostics

### Phase 6: Verification

- recorder integration tests
- route tests
- generator/compiler tests
- intranet acceptance checklist scenarios

## 17. Test Strategy

### 17.1 Unit Tests

- canonical locator parsing from:
  - structured locator
  - selector-only candidate
  - `playwright_locator` string
  - nth candidate
- acceptance gate rejects contradictory states
- diagnostics created when target cannot be canonicalized

### 17.2 Integration Tests

- login -> redirect -> business page -> unnamed textbox recording
- business page textboxes resolved to canonical nth/role target
- generation blocked when unresolved diagnostics remain
- repaired diagnostic can be promoted into accepted action

### 17.3 Product Acceptance Tests

- manual search after auth redirect
- unnamed textbox recording and replay
- list selection and export flows
- dynamic components with late-rendered inputs

## 18. Risks

### 18.1 Temporary UI complexity increase

Introducing diagnostics adds another visible concept. This is worthwhile because it prevents ambiguity from being disguised as success.

### 18.2 Canonical locator parser incompleteness

The parser will initially support only the target forms we can safely normalize. This is acceptable as long as unresolved forms become diagnostics instead of corrupted accepted actions.

### 18.3 Scope creep toward AI trace unification

This design must stay focused on manual recording. AI trace unification is explicitly deferred.

## 19. Final Recommendation

Do not continue patching the current manual `steps + traces` design.

For manual recording, move to:

- one durable accepted truth model
- one durable diagnostics model
- one transient compiler view

This is the cleanest path to:

- higher stability
- clearer debugging
- better user trust
- less patch-driven architecture
- stronger long-term evolution
