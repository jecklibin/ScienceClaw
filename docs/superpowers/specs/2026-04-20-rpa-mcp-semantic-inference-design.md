# RPA MCP Semantic Inference Design

## Goal

Improve RPA-to-MCP conversion so the generated MCP tool looks like a stable API instead of a thin wrapper around recorded browser steps.

The system should infer a useful tool name, description, input schema, parameter names, and parameter descriptions from the sanitized recording context. The inference must not weaken the existing safety model: login stripping, sensitive-field removal, cookie requirements, and allowed-domain controls remain deterministic backend decisions.

## Problem

The current converter uses deterministic heuristics such as `_PARAM_NAME_HINTS` to infer parameter names from locator text and step descriptions. This is useful as a fallback, but it does not generalize well:

- Business-specific fields often do not match the built-in keyword list.
- A single locator does not explain the intent of the whole workflow.
- Generated parameter names can be generic, such as `keyword`, `title`, or `name`, when the MCP caller needs names such as `repo_url`, `issue_title`, `patient_id`, or `paper_query`.
- Tool names and descriptions inherit too much from the recording flow and too little from the actual task semantics.

## Non-Goals

- Do not let an LLM decide whether login or credential steps are safe to keep.
- Do not require a model call for basic RPA MCP conversion to function.
- Do not generate per-tool MCP servers. The centralized RPA MCP Gateway remains the runtime.
- Do not auto-save inferred schemas without user review.

## Recommended Approach

Use a hybrid pipeline:

1. Normalize recorded steps with the existing Playwright generator logic.
2. Deterministically detect and remove login, credential, and authentication steps.
3. Build a compact semantic context from the remaining steps and locator metadata.
4. Ask an agent/model to recommend MCP-facing metadata and input schema.
5. Validate the model response against a strict Pydantic schema.
6. Merge accepted recommendations into the preview draft.
7. Show the recommendation in the MCP editor for user confirmation and editing.
8. Run preview test before saving.
9. Infer output schema from the successful test result.
10. Save the final tool definition with schema provenance metadata.

## Architecture

Add a backend component:

`backend/rpa/mcp_semantic_inferer.py`

Responsibilities:

- Build a sanitized inference prompt from recording context.
- Call the configured project model through the existing model/provider abstraction where possible.
- Parse and validate structured JSON output.
- Return a recommendation object with confidence and warnings.
- Fall back to deterministic inference when no model is configured or the model response is invalid.

The existing `RpaMcpConverter` remains the orchestrator:

- It keeps login stripping and sensitive parameter removal.
- It calls the semantic inferer after sanitization.
- It keeps `_PARAM_NAME_HINTS` as fallback only.
- It builds the final preview object from deterministic safety output plus semantic recommendations.

## Data Flow

```text
recorded session
  -> normalize / deduplicate steps
  -> deterministic login range detection
  -> remove login and sensitive steps
  -> build semantic context from retained steps
  -> model recommends tool metadata and input schema
  -> validate recommendation
  -> user reviews / edits in MCP editor
  -> preview test
  -> infer output schema from real execution result
  -> save MCP tool
```

## Semantic Context

The model should receive only sanitized data:

- Retained step index and action.
- Step description.
- Current URL host and path.
- Locator role, name, placeholder, label, text, alt, title, and selected candidate metadata.
- Recorded non-sensitive example values for editable business inputs.
- A list of removed login step summaries without credential values.
- Optional successful test result summary when inferring output schema.

The model must not receive:

- Password values.
- Credential vault IDs.
- Raw cookies.
- Full request headers.
- Any value already classified as sensitive.

## Recommendation Schema

The model returns this shape:

```json
{
  "tool": {
    "tool_name": "get_first_github_issue",
    "display_name": "Get first GitHub issue",
    "description": "Open GitHub Trending, choose the first repository, and return the first issue title."
  },
  "input_schema": {
    "type": "object",
    "properties": {
      "topic": {
        "type": "string",
        "description": "Optional GitHub trending topic or search keyword."
      }
    },
    "required": []
  },
  "params": {
    "topic": {
      "source_step_index": 2,
      "original_value": "ai",
      "description": "Optional GitHub trending topic or search keyword.",
      "required": false,
      "confidence": 0.78
    }
  },
  "warnings": []
}
```

Validation rules:

- `tool_name` must be snake_case and must not collide with existing tool names.
- Parameter names must be snake_case.
- Parameter types must be JSON Schema primitive or array/object shapes supported by the current UI.
- Parameter names must not match authentication patterns.
- The recommendation cannot add `cookies`; cookie requirements remain converter-owned.
- The recommendation cannot reintroduce removed steps or sensitive values.

## Fallback Behavior

If semantic inference cannot run:

- Keep the existing deterministic converter path.
- Use locator-derived names and `_PARAM_NAME_HINTS` only as fallback.
- Mark schema provenance as `rule_inferred`.
- Surface a non-blocking warning in the editor.

If the model returns invalid JSON:

- Store a compact parse/validation warning in the preview report.
- Do not expose raw model output in the saved tool.
- Continue with deterministic fallback.

## UI Behavior

The MCP editor should show the recommendation source:

- `AI inferred`
- `Rule inferred`
- `User edited`

For each recommended parameter, show:

- Name.
- Type.
- Description.
- Required/optional state.
- Example value.
- Source step.
- Confidence when available.

The user can confirm, rename, edit descriptions, mark a value as fixed, or remove a parameter before testing. Saving still requires a successful preview test.

Removed login steps continue to be shown separately and highlighted in the recorded step list, so users can verify what will not be part of the shared MCP tool.

## Backend API Changes

Extend preview responses with optional semantic inference metadata:

```json
{
  "semantic_inference": {
    "source": "ai_inferred",
    "confidence": 0.82,
    "warnings": [],
    "model": "configured-model-name",
    "generated_at": "preview"
  }
}
```

Extend save payloads to allow user-edited schema provenance:

```json
{
  "schema_source": "user_edited",
  "input_schema": {},
  "params": {}
}
```

Existing clients can ignore these fields.

## Error Handling

- Model unavailable: fallback to rule inference and show warning.
- Model timeout: fallback to rule inference and show warning.
- Invalid model output: fallback to rule inference and show warning.
- Suspected sensitive parameter in model output: drop that parameter and warn.
- Name collision: append a deterministic suffix or ask the user to edit in UI.

## Testing

Backend tests:

- Semantic inferer validates good model output.
- Invalid JSON falls back to deterministic inference.
- Model output cannot add password, username, cookie, or credential parameters.
- Tool name and parameter names are normalized to snake_case.
- Login stripping happens before semantic inference.
- Existing converter tests continue to pass with no model configured.

Frontend tests or build checks:

- MCP editor renders recommendation source.
- User edits schema without losing preview test state unless executable fields changed.
- Removed login steps remain visible and highlighted.

Manual verification:

- Record a no-login flow and confirm no cookie parameter is required.
- Record a login flow and confirm login inputs are removed before inference.
- Preview a flow with business inputs and verify generated parameter names/descriptions are meaningful.
- Save and call the resulting MCP tool through the gateway.

## Rollout

Implement behind a backend feature flag:

`RPA_MCP_SEMANTIC_INFERENCE=true`

Default behavior can be conservative:

- Enabled when a model is configured.
- Falls back silently to rule inference when unavailable.
- The UI always allows manual editing.

This keeps local/offline RPA MCP conversion working while improving schema quality in configured environments.
