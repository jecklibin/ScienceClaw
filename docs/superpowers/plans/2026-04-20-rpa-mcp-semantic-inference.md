# RPA MCP Semantic Inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AI-assisted semantic inference for RPA MCP tool names, descriptions, and input schemas while keeping login stripping and sensitive data removal deterministic.

**Architecture:** Add a focused semantic inferer that consumes sanitized RPA steps and returns validated recommendations. Keep `RpaMcpConverter.preview()` as the offline rule-based path, add an async semantic preview path for routes, and persist schema provenance for the editor. `_PARAM_NAME_HINTS` remains only a fallback when semantic inference is unavailable or invalid.

**Tech Stack:** FastAPI, Python 3.13, Pydantic v2, LangChain `ChatOpenAI`, existing RPA converter/generator models, Vue 3 + TypeScript, pytest, Vite.

---

## File Map

- Modify: `RpaClaw/backend/config.py` - semantic inference feature flag and timeout.
- Modify: `RpaClaw/backend/rpa/mcp_models.py` - semantic report/provenance fields.
- Create: `RpaClaw/backend/rpa/mcp_semantic_inferer.py` - sanitized context builder, model call, JSON validation, fallback.
- Modify: `RpaClaw/backend/rpa/mcp_converter.py` - async semantic preview and merge logic.
- Modify: `RpaClaw/backend/route/rpa_mcp.py` - use semantic preview and preserve user-edited schema.
- Create: `RpaClaw/backend/tests/test_rpa_mcp_semantic_inferer.py` - inferer unit tests.
- Modify: `RpaClaw/backend/tests/test_rpa_mcp_converter.py` - converter integration tests.
- Modify: `RpaClaw/backend/tests/test_rpa_mcp_route.py` - route save/update tests.
- Modify: `RpaClaw/frontend/src/api/rpaMcp.ts` - semantic type fields.
- Modify: `RpaClaw/frontend/src/pages/tools/McpToolEditorPage.vue` - schema source and warnings display.
- Modify: `RpaClaw/frontend/src/locales/en.ts`, `RpaClaw/frontend/src/locales/zh.ts` - labels.

---

## Task 1: Semantic Model Fields and Settings

**Files:**
- Modify: `RpaClaw/backend/config.py`
- Modify: `RpaClaw/backend/rpa/mcp_models.py`
- Test: `RpaClaw/backend/tests/test_rpa_mcp_converter.py`

- [ ] **Step 1: Write failing defaults test**

Add to `test_rpa_mcp_tool_definition_defaults`:

```python
assert tool.semantic_inference["source"] == "rule_inferred"
assert tool.schema_source == "rule_inferred"
```

Run:

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_converter.py::test_rpa_mcp_tool_definition_defaults -v
```

Expected: fails because semantic fields do not exist.

- [ ] **Step 2: Add settings**

In `Settings` in `RpaClaw/backend/config.py`, add:

```python
rpa_mcp_semantic_inference: bool = os.environ.get("RPA_MCP_SEMANTIC_INFERENCE", "true").lower() == "true"
rpa_mcp_semantic_timeout_seconds: int = int(os.environ.get("RPA_MCP_SEMANTIC_TIMEOUT_SECONDS", "20"))
```

- [ ] **Step 3: Add model defaults**

In `RpaClaw/backend/rpa/mcp_models.py`, add:

```python
def build_rpa_mcp_semantic_report(source: str = "rule_inferred") -> dict[str, Any]:
    return {
        "source": source,
        "confidence": None,
        "warnings": [],
        "model": "",
        "generated_at": "preview",
    }
```

Add to `RpaMcpToolDefinition`:

```python
schema_source: str = "rule_inferred"
semantic_inference: dict[str, Any] = Field(default_factory=build_rpa_mcp_semantic_report)
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_converter.py::test_rpa_mcp_tool_definition_defaults -v
git add RpaClaw/backend/config.py RpaClaw/backend/rpa/mcp_models.py RpaClaw/backend/tests/test_rpa_mcp_converter.py
git commit -m "feat: add rpa mcp semantic metadata"
```

Expected: test passes and commit succeeds.

---

## Task 2: Semantic Inferer

**Files:**
- Create: `RpaClaw/backend/rpa/mcp_semantic_inferer.py`
- Create: `RpaClaw/backend/tests/test_rpa_mcp_semantic_inferer.py`

- [ ] **Step 1: Write failing inferer tests**

Create `RpaClaw/backend/tests/test_rpa_mcp_semantic_inferer.py`:

```python
import pytest

from backend.rpa.mcp_semantic_inferer import RpaMcpSemanticInferer


class FakeModelClient:
    def __init__(self, content: str):
        self.content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages

        class Response:
            content = self.content

        return Response()


@pytest.mark.asyncio
async def test_semantic_inferer_accepts_valid_recommendation():
    client = FakeModelClient('{"tool":{"tool_name":"search_reports","display_name":"Search reports","description":"Search reports by keyword."},"input_schema":{"type":"object","properties":{"report_keyword":{"type":"string","description":"Keyword used to search reports."}},"required":["report_keyword"]},"params":{"report_keyword":{"source_step_index":1,"original_value":"cancer","description":"Keyword used to search reports.","required":true,"confidence":0.86}},"warnings":[]}')
    recommendation = await RpaMcpSemanticInferer(model_client=client).infer(
        requested_name="rpa_tool",
        requested_description="",
        steps=[{"action": "fill", "description": "填写搜索关键词", "target": '{"method":"placeholder","value":"搜索关键词"}', "value": "cancer", "url": "https://example.com/search"}],
        removed_step_details=[],
        fallback_params={},
    )
    assert recommendation.source == "ai_inferred"
    assert recommendation.tool_name == "search_reports"
    assert recommendation.params["report_keyword"]["original_value"] == "cancer"


@pytest.mark.asyncio
async def test_semantic_inferer_drops_sensitive_recommendations():
    client = FakeModelClient('{"tool":{"tool_name":"login_then_search","display_name":"Login then search","description":"Search."},"input_schema":{"type":"object","properties":{"password":{"type":"string","description":"Password"}},"required":["password"]},"params":{"password":{"source_step_index":0,"original_value":"secret","description":"Password","required":true,"confidence":0.9}},"warnings":[]}')
    recommendation = await RpaMcpSemanticInferer(model_client=client).infer(
        requested_name="login_then_search",
        requested_description="",
        steps=[],
        removed_step_details=[{"index": 0, "description": "填写密码"}],
        fallback_params={},
    )
    assert recommendation.source == "ai_inferred"
    assert "password" not in recommendation.params
    assert "password" not in recommendation.input_schema["properties"]
    assert any("sensitive" in warning.lower() for warning in recommendation.warnings)


@pytest.mark.asyncio
async def test_semantic_inferer_falls_back_on_invalid_json():
    client = FakeModelClient("not json")
    recommendation = await RpaMcpSemanticInferer(model_client=client).infer(
        requested_name="Search Reports",
        requested_description="Search reports by keyword",
        steps=[],
        removed_step_details=[],
        fallback_params={"keyword": {"original_value": "cancer", "type": "string", "description": "Search keyword"}},
    )
    assert recommendation.source == "rule_inferred"
    assert recommendation.tool_name == "search_reports"
    assert recommendation.input_schema["properties"]["keyword"]["default"] == "cancer"
    assert recommendation.warnings
```

Run:

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_semantic_inferer.py -v
```

Expected: import error because the inferer file does not exist.

- [ ] **Step 2: Implement inferer**

Create `RpaClaw/backend/rpa/mcp_semantic_inferer.py` with these public interfaces:

```python
@dataclass
class RpaMcpSemanticRecommendation:
    source: str
    tool_name: str
    display_name: str
    description: str
    input_schema: dict[str, Any]
    params: dict[str, Any]
    confidence: float | None = None
    warnings: list[str] = field(default_factory=list)
    model: str = ""


class RpaMcpSemanticInferer:
    def __init__(self, model_client: Any | None = None) -> None: ...

    async def infer(
        self,
        *,
        requested_name: str,
        requested_description: str,
        steps: list[dict[str, Any]],
        removed_step_details: list[dict[str, Any]],
        fallback_params: dict[str, Any],
    ) -> RpaMcpSemanticRecommendation: ...
```

Implementation requirements:

- Use `ChatOpenAI(model=settings.model_ds_name, base_url=settings.model_ds_base_url, api_key=settings.model_ds_api_key, temperature=0, max_tokens=2000)` only when `settings.rpa_mcp_semantic_inference` and `settings.model_ds_api_key` are set, unless a `model_client` was injected by tests.
- Build context from retained steps only; include `action`, `description`, `url`, safe locator fields, and non-sensitive example values.
- Include removed login step summaries without credential values.
- Parse strict JSON from plain or fenced model output.
- Normalize `tool_name` and parameter names to snake_case.
- Drop recommended params matching `password|passcode|token|secret|cookie|credential|验证码|动态码|短信码|密码|口令|账号|帐号|账户|用户名|邮箱|手机号|手机`.
- Return `source="rule_inferred"` with fallback params and warnings when model call, timeout, parsing, or validation fails.

- [ ] **Step 3: Verify and commit**

Run:

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_semantic_inferer.py -v
git add RpaClaw/backend/rpa/mcp_semantic_inferer.py RpaClaw/backend/tests/test_rpa_mcp_semantic_inferer.py
git commit -m "feat: infer rpa mcp semantics"
```

Expected: tests pass and commit succeeds.

---

## Task 3: Converter Integration

**Files:**
- Modify: `RpaClaw/backend/rpa/mcp_converter.py`
- Modify: `RpaClaw/backend/tests/test_rpa_mcp_converter.py`

- [ ] **Step 1: Write failing async converter test**

Add:

```python
import pytest


class FakeSemanticInferer:
    async def infer(self, **_kwargs):
        from backend.rpa.mcp_semantic_inferer import RpaMcpSemanticRecommendation
        return RpaMcpSemanticRecommendation(
            source="ai_inferred",
            tool_name="search_reports",
            display_name="Search reports",
            description="Search reports by keyword.",
            input_schema={"type": "object", "properties": {"report_keyword": {"type": "string", "description": "Keyword used to search reports.", "default": "cancer"}}, "required": ["report_keyword"]},
            params={"report_keyword": {"original_value": "cancer", "type": "string", "description": "Keyword used to search reports.", "required": True, "confidence": 0.9}},
            confidence=0.9,
            warnings=[],
            model="fake-model",
        )


@pytest.mark.asyncio
async def test_preview_with_semantics_uses_ai_recommendation_after_login_strip():
    steps = [
        {"action": "fill", "description": "填写密码", "target": '{"method":"label","value":"密码"}', "value": "{{credential}}", "url": "https://example.com/login"},
        {"action": "fill", "description": "填写搜索关键词", "target": '{"method":"placeholder","value":"搜索关键词"}', "value": "cancer", "url": "https://example.com/search"},
    ]
    preview = await RpaMcpConverter(semantic_inferer=FakeSemanticInferer()).preview_with_semantics(
        user_id="user-1",
        session_id="session-1",
        skill_name="search_skill",
        name="rpa_tool",
        description="",
        steps=steps,
        params={},
    )
    assert preview.tool_name == "search_reports"
    assert preview.name == "Search reports"
    assert preview.description == "Search reports by keyword."
    assert preview.schema_source == "ai_inferred"
    assert "report_keyword" in preview.input_schema["properties"]
    assert "password" not in preview.input_schema["properties"]
```

Run:

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_converter.py::test_preview_with_semantics_uses_ai_recommendation_after_login_strip -v
```

Expected: fails because `preview_with_semantics` does not exist.

- [ ] **Step 2: Add async converter path**

In `mcp_converter.py`:

```python
from backend.rpa.mcp_semantic_inferer import RpaMcpSemanticInferer
```

Change constructor:

```python
def __init__(self, semantic_inferer: RpaMcpSemanticInferer | None = None) -> None:
    self._generator = PlaywrightGenerator()
    self._semantic_inferer = semantic_inferer
```

Add `preview_with_semantics(...)` that:

- Runs the same normalize, login detection, `_strip_login_steps`, `_strip_login_params`, and `_infer_step_params` sequence as `preview()`.
- Calls `(self._semantic_inferer or RpaMcpSemanticInferer()).infer(...)` after sanitization.
- Builds `RpaMcpToolDefinition` with recommended `name`, `tool_name`, `description`, `params`, and `input_schema`.
- Re-adds `cookies` to `input_schema` when `requires_cookies` is true, even if the recommendation omitted it.
- Sets `schema_source` and `semantic_inference`.
- Appends semantic warnings into `sanitize_report.warnings`.

Extract common return-building into `_build_preview_from_parts(...)` so existing `preview()` continues to return deterministic rule-inferred previews.

- [ ] **Step 3: Verify and commit**

Run:

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_converter.py -v
git add RpaClaw/backend/rpa/mcp_converter.py RpaClaw/backend/tests/test_rpa_mcp_converter.py
git commit -m "feat: apply semantic rpa mcp previews"
```

Expected: all converter tests pass.

---

## Task 4: Route Save and Update Support

**Files:**
- Modify: `RpaClaw/backend/route/rpa_mcp.py`
- Modify: `RpaClaw/backend/tests/test_rpa_mcp_route.py`

- [ ] **Step 1: Write failing route test**

Add a route test that monkeypatches `_preview_payload`, `RpaMcpPreviewDraftRegistry`, and `RpaMcpToolRegistry`, then posts:

```python
{
    "name": "Search reports",
    "description": "Search reports.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "User-edited search query."}},
        "required": ["query"],
    },
    "params": {
        "query": {"original_value": "cancer", "type": "string", "description": "User-edited search query.", "required": True}
    },
    "schema_source": "user_edited",
    "output_schema": {"type": "object", "properties": {}, "required": []},
}
```

Assert saved tool has:

```python
assert saved["tool"].schema_source == "user_edited"
assert "query" in saved["tool"].input_schema["properties"]
assert "report_keyword" not in saved["tool"].input_schema["properties"]
```

Run:

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_route.py::test_create_rpa_mcp_tool_preserves_user_edited_input_schema -v
```

Expected: fails because save request does not accept input schema provenance.

- [ ] **Step 2: Extend request models and route logic**

In `SaveToolRequest`, add:

```python
input_schema: dict[str, Any] = Field(default_factory=dict)
params: dict[str, Any] = Field(default_factory=dict)
schema_source: str = ""
output_schema: dict[str, Any] = Field(default_factory=dict)
```

In `UpdateToolRequest`, add:

```python
input_schema: dict[str, Any] = Field(default_factory=dict)
params: dict[str, Any] = Field(default_factory=dict)
schema_source: str = ""
```

In `_preview_payload`, call:

```python
preview = await RpaMcpConverter().preview_with_semantics(...)
```

In `create_rpa_mcp_tool`, before save:

```python
if body.input_schema:
    preview.input_schema = body.input_schema
if body.params:
    preview.params = body.params
if body.schema_source:
    preview.schema_source = body.schema_source
```

In `update_rpa_mcp_tool`, preserve those same fields when present in `body.model_fields_set`.

- [ ] **Step 3: Verify and commit**

Run:

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_route.py -v
git add RpaClaw/backend/route/rpa_mcp.py RpaClaw/backend/tests/test_rpa_mcp_route.py
git commit -m "feat: persist rpa mcp semantic schemas"
```

Expected: route tests pass.

---

## Task 5: Editor Display

**Files:**
- Modify: `RpaClaw/frontend/src/api/rpaMcp.ts`
- Modify: `RpaClaw/frontend/src/pages/tools/McpToolEditorPage.vue`
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: Extend TypeScript types**

In `rpaMcp.ts`, add:

```ts
export type RpaMcpSchemaSource = 'ai_inferred' | 'rule_inferred' | 'user_edited' | string;

export interface RpaMcpSemanticInference {
  source: RpaMcpSchemaSource;
  confidence?: number | null;
  warnings?: string[];
  model?: string;
  generated_at?: string;
}
```

Add to `RpaMcpPreview`:

```ts
schema_source?: RpaMcpSchemaSource;
semantic_inference?: RpaMcpSemanticInference;
```

- [ ] **Step 2: Show source and warnings in editor**

In `McpToolEditorPage.vue`, add computed values:

```ts
const schemaSourceLabel = computed(() => {
  const source = preview.value?.schema_source || preview.value?.semantic_inference?.source || 'rule_inferred';
  if (source === 'ai_inferred') return t('MCP Editor AI inferred');
  if (source === 'user_edited') return t('MCP Editor User edited');
  return t('MCP Editor Rule inferred');
});

const semanticWarnings = computed(() => preview.value?.semantic_inference?.warnings || []);
```

Render above the input schema:

```vue
<div class="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
  <span class="rounded-full bg-slate-100 px-2.5 py-1 font-bold text-slate-700 dark:bg-white/10 dark:text-slate-200">
    {{ schemaSourceLabel }}
  </span>
  <span v-if="preview?.semantic_inference?.confidence !== null && preview?.semantic_inference?.confidence !== undefined">
    {{ t('MCP Editor Confidence') }} {{ Math.round(Number(preview.semantic_inference.confidence) * 100) }}%
  </span>
</div>
<ul v-if="semanticWarnings.length" class="mt-2 list-disc pl-5 text-xs text-amber-700 dark:text-amber-200">
  <li v-for="warning in semanticWarnings" :key="warning">{{ warning }}</li>
</ul>
```

When saving with edited `input_schema` or `params`, include:

```ts
schema_source: 'user_edited',
```

- [ ] **Step 3: Add i18n and verify**

In `en.ts`:

```ts
'MCP Editor AI inferred': 'AI inferred',
'MCP Editor Rule inferred': 'Rule inferred',
'MCP Editor User edited': 'User edited',
'MCP Editor Confidence': 'Confidence',
```

In `zh.ts`:

```ts
'MCP Editor AI inferred': 'AI 推荐',
'MCP Editor Rule inferred': '规则推断',
'MCP Editor User edited': '用户编辑',
'MCP Editor Confidence': '置信度',
```

Run:

```bash
cd RpaClaw/frontend
npm run build
git add RpaClaw/frontend/src/api/rpaMcp.ts RpaClaw/frontend/src/pages/tools/McpToolEditorPage.vue RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "feat: show rpa mcp semantic inference"
```

Expected: frontend build succeeds.

---

## Task 6: Final Verification

**Files:**
- Review all modified files.

- [ ] **Step 1: Run affected backend tests**

```bash
cd RpaClaw
uv run pytest backend/tests/test_rpa_mcp_semantic_inferer.py backend/tests/test_rpa_mcp_converter.py backend/tests/test_rpa_mcp_route.py backend/tests/test_mcp_route.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend build**

```bash
cd RpaClaw/frontend
npm run build
```

Expected: build succeeds. Existing Browserslist and chunk-size warnings are acceptable.

- [ ] **Step 3: Check whitespace and encoding**

```bash
git diff --check
$mojibakeChars = @([char]0xFFFD, [char]0x9427, [char]0x93BC, [char]0x93CC, [char]0x934F, [char]0x7EDB, [char]0x6D93)
$encodingPattern = '[\\]u[0-9a-fA-F]{4}|' + (($mojibakeChars | ForEach-Object { [regex]::Escape([string]$_) }) -join '|')
git diff origin/master --name-only | ForEach-Object {
  if (Test-Path $_) {
    Select-String -Path $_ -Pattern $encodingPattern -ErrorAction SilentlyContinue
  }
}
```

Expected: `git diff --check` exits 0 and the encoding scan prints no modified-source matches.

- [ ] **Step 4: Inspect final diff**

```bash
git status --short
git diff --stat origin/codex/rpa-mcp-gateway...HEAD
```

Expected: only semantic inference, route, tests, editor, and i18n files changed.

- [ ] **Step 5: Cleanup commit only if needed**

If cleanup changed files:

```bash
git add <changed-files>
git commit -m "fix: clean rpa mcp semantic inference"
```

If no cleanup was needed, do not create an empty commit.
