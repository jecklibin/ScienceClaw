# Snapshot Candidate Collection Analysis

## Background

This note summarizes a postponed design discussion for improving RPA recording
snapshots. The issue appeared in a GitHub Trending scenario:

```text
Open the project most related to claudecode.
```

The current compressed snapshot worked better than the old flat DOM snapshot in
terms of size, but it exposed a weakness for semantic selection tasks. The LLM
needs to compare multiple candidates on the page, while the current tiered
snapshot tends to fully expand only the highest-scoring region and summarize the
rest.

This is not an implementation plan. The feature is intentionally deferred until
the same class of problem appears again.

## Current Snapshot Model

The current recording snapshot has two layers in the debug dump:

```json
{
  "raw_snapshot": {},
  "compact_snapshot": {}
}
```

Only `compact_snapshot` is sent to the recording planner. `raw_snapshot` is
diagnostic evidence used to inspect what was lost during compression.

The current compact snapshot can output:

- `clean_snapshot`: structured regions fit within the size budget.
- `tiered_snapshot`: top relevant regions are expanded, secondary regions are
  sampled, remaining regions are summarized in `region_catalogue`.

The tiered form is good for field extraction and detail pages, because those
tasks usually target one region. It is less stable for candidate selection,
because the LLM must compare many similar items.

## Observed Size Comparison

The following numbers were calculated from real debug snapshots in:

```text
data/rpa_recording_snapshots
```

Size means minified UTF-8 JSON size of the snapshot object.

| Scenario | Raw Snapshot | Old Flat DOM Filter | Current Structured Filter | Proposed Candidate Collection |
| --- | ---: | ---: | ---: | ---: |
| Open project most related to `claudecode` | 198.28 KB | 22.95 KB | 5.66 KB | about 6.9 to 7.1 KB |
| Extract project name / current repository page info | 291.64 KB | 28.83 KB | 6.53 KB | about 6.53 KB |

For the Trending scenario, an additive variant that appends the candidate
collection without replacing existing expanded regions was estimated at about
8.6 KB.

The important point is that the proposed structure would not return to the old
20 to 30 KB flat DOM payload. It would add roughly 1.5 KB in the Trending case
while restoring the key comparison fields.

## Why Current Filtering Is Weak For Candidate Selection

In the Trending snapshot, the current compact output was:

```text
expanded_regions: 1
sampled_regions: 1
region_catalogue: 15
```

The fully expanded candidate was:

```text
zilliztech / claude-context
```

One sampled candidate was:

```text
ruvnet / RuView
```

Other repositories were mostly present only as catalogue summaries. In the raw
snapshot, each repository card had a useful description, for example:

```text
zilliztech / claude-context
Code search MCP for Claude Code. Make entire codebase the context for any coding agent.
```

and:

```text
VoltAgent / awesome-agent-skills
A curated collection of 1000+ agent skills ... compatible with Claude Code, Codex, Gemini CLI...
```

Those descriptions are essential for "most related" style instructions. If the
descriptions are missing from `compact_snapshot`, the LLM may fall back to a
less grounded strategy such as searching the site or selecting based on a weak
name match.

## Proposed Deferred Design

The proposed structure is `candidate_collection`.

It is a page structure expression, not an execution strategy. The LLM would not
operate on `candidate_collection` itself. It would read the candidate list and,
if needed, generate Playwright code using each candidate's
`primary_action.locator`.

Example:

```json
{
  "kind": "candidate_collection",
  "title": "Candidate cards",
  "candidates": [
    {
      "source_region_id": "container-5",
      "title": "zilliztech / claude-context",
      "description": "Code search MCP for Claude Code. Make entire codebase the context for any coding agent.",
      "metadata": ["TypeScript", "169 stars today"],
      "primary_action": {
        "label": "zilliztech / claude-context",
        "locator": {
          "method": "role",
          "role": "link",
          "name": "zilliztech / claude-context"
        }
      }
    }
  ]
}
```

If a candidate collection matches the instruction, the collection itself would
enter `expanded_regions`. The original card regions would not all be fully
expanded. This avoids reintroducing noisy DOM such as star buttons, fork links,
sponsor links, and avatar links.

## Structural Detection Idea

Candidate collection detection should be based on UI structure, not a specific
site such as GitHub.

Candidate inputs:

```text
1. Multiple adjacent card_group or list-like regions.
2. Each region has a title or heading.
3. Each region has a likely primary clickable action.
4. The regions have similar structure.
5. Candidate count is at least 3.
```

This keeps the abstraction general enough for:

- repository cards
- search results
- product lists
- PR cards
- article lists
- task cards

It should not use rules like:

```text
if url contains github.com/trending
```

## Relevance Change

Current relevance is region-oriented:

```text
region title / summary / actions / fields
vs
user instruction
```

Then the top regions are expanded.

With candidate collection, relevance would add a collection-level path:

```text
candidate titles / descriptions / metadata / primary action labels
vs
user instruction
```

If the collection is relevant, the whole collection is exposed as a lightweight
candidate list. The LLM still decides which candidate is most relevant. The
compression layer does not choose the final target.

## Why The Feature Is Deferred

The design adds another abstraction to snapshot compression. It appears useful,
but the latest runs did not consistently reproduce the original failure. Given
the project rules, adding a new abstraction should require repeated evidence
that the current model fails for candidate selection.

For now, the right action is to keep the diagnostic snapshot dump available and
revisit this design if the same class of failure appears again.

## Future Reopen Criteria

Reopen this design if any of the following happens:

- A list/card selection task fails because the compact snapshot lacks candidate
  descriptions or primary links.
- The planner chooses a search box or unrelated navigation even though the
  current page contains visible candidate cards.
- `raw_snapshot` contains the needed candidate information, but
  `compact_snapshot` only keeps it in weak catalogue summaries.

When reopened, compare the failing `raw_snapshot` and `compact_snapshot` first.
If the problem is confirmed at the compression layer, implement
`candidate_collection` with focused tests.
