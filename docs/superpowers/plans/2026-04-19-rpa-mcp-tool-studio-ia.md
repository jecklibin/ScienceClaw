# RPA Tool Studio IA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the RPA-to-MCP publishing flow so RPA recording supports two release targets, while MCP Tool creation and editing live in a dedicated Tool Studio surface instead of staying embedded in the recording configure page.

**Architecture:** Keep RPA recording as the automation authoring flow, keep Skill publishing in the RPA/Skill domain, and move MCP Tool publishing into a dedicated Tool Editor route under the Tools domain. The RPA configure page remains the fast handoff entry, while Tools becomes the long-term management surface for MCP tools. Reuse the existing preview-test, save, schema, and gateway APIs rather than redesigning backend behavior.

**Tech Stack:** Vue 3, TypeScript, Vue Router, existing `apiClient` wrappers, existing RPA MCP backend routes, Vitest, FastAPI route tests only where API contract needs small adjustments.

---

## File Map

### Frontend

- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\main.ts`
  - Add dedicated Tool Editor route(s) under the Tools domain for MCP tool creation from RPA.
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\pages\rpa\ConfigurePage.vue`
  - Keep the dual publish actions, but change MCP publish to hand off into the Tool Studio route instead of treating the RPA page as the permanent editing surface.
- Move or replace: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\pages\rpa\McpConvertPage.vue`
  - Either reduce this page to a redirect shell or replace it with a thin handoff screen.
- Create: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\pages\tools\McpToolEditorPage.vue`
  - Dedicated editor for RPA-backed MCP tool creation and editing.
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\pages\ToolsPage.vue`
  - Add a formal MCP Tools area/entry that leads into the Tool Editor and treats saved tools as the system of record.
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\api\rpaMcp.ts`
  - Ensure API helpers cleanly support both “preview from session” and “manage saved tool” usage from the Tool Studio route.
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\utils\rpaMcpConvert.ts`
  - Keep execution-relevant preview test validity logic aligned with the Tool Editor UX.
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\utils\rpaMcpConvert.test.ts`
  - Add tests for the Tool Studio handoff assumptions where they are covered by pure helpers.

### Backend

- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\backend\route\rpa_mcp.py`
  - Only if needed to support cleaner draft/tool editor hydration from Tools domain routes. Avoid backend churn unless the UI split exposes a real contract gap.
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\backend\tests\test_rpa_mcp_route.py`
  - Add route tests only for any new or adjusted contract.

### Docs

- Existing spec: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\docs\superpowers\specs\2026-04-19-rpa-mcp-tool-studio-ia-design.md`

## Task 1: Lock route and handoff requirements with a failing frontend helper test

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\utils\rpaMcpConvert.test.ts`
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\utils\rpaMcpConvert.ts`

- [ ] **Step 1: Write the failing helper tests for Tool Studio handoff state**

```ts
import { describe, expect, it } from 'vitest';

import {
  buildRpaToolEditorLocation,
  buildPreviewDraftSignature,
} from './rpaMcpConvert';

describe('buildRpaToolEditorLocation', () => {
  it('routes MCP publishing into the Tools domain with session context', () => {
    expect(buildRpaToolEditorLocation({
      sessionId: 'session-1',
      skillName: 'github-project-issue',
      skillDescription: 'Fetch the first issue',
    })).toEqual({
      path: '/chat/tools/mcp/new',
      query: {
        source: 'rpa-session',
        sessionId: 'session-1',
        skillName: 'github-project-issue',
        skillDescription: 'Fetch the first issue',
      },
    });
  });
});

describe('buildPreviewDraftSignature', () => {
  it('ignores name and description because they do not affect execution validity', () => {
    const a = buildPreviewDraftSignature({
      sessionId: 'session-1',
      name: 'tool-a',
      description: 'desc-a',
      allowedDomains: ['github.com'],
      postAuthStartUrl: 'https://github.com/trending',
    });
    const b = buildPreviewDraftSignature({
      sessionId: 'session-1',
      name: 'tool-b',
      description: 'desc-b',
      allowedDomains: ['github.com'],
      postAuthStartUrl: 'https://github.com/trending',
    });

    expect(a).toBe(b);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run test -- src/utils/rpaMcpConvert.test.ts
```

Expected: FAIL because `buildRpaToolEditorLocation` does not exist yet.

- [ ] **Step 3: Add the minimal helper implementation**

```ts
export function buildRpaToolEditorLocation(input: {
  sessionId: string;
  skillName?: string;
  skillDescription?: string;
}) {
  return {
    path: '/chat/tools/mcp/new',
    query: {
      source: 'rpa-session',
      sessionId: input.sessionId,
      skillName: input.skillName || '',
      skillDescription: input.skillDescription || '',
    },
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run test -- src/utils/rpaMcpConvert.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway add RpaClaw/frontend/src/utils/rpaMcpConvert.ts RpaClaw/frontend/src/utils/rpaMcpConvert.test.ts
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway commit -m "feat: add tool studio handoff helpers"
```

## Task 2: Move the MCP publish entry from RPA Configure into Tool Studio routing

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\pages\rpa\ConfigurePage.vue`
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\main.ts`

- [ ] **Step 1: Write the failing route/build check**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run build
```

Expected: PASS before changes; use the build plus manual route grep as the safety check after wiring.

- [ ] **Step 2: Change ConfigurePage MCP publish button to use Tool Studio handoff**

Update the MCP publish action to use the helper instead of routing to `/rpa/convert-mcp`.

```ts
import { buildRpaToolEditorLocation } from '@/utils/rpaMcpConvert';

const goToMcpToolEditor = () => {
  router.push(buildRpaToolEditorLocation({
    sessionId: sessionId.value,
    skillName: skillName.value,
    skillDescription: skillDescription.value,
  }));
};
```

Template target:

```vue
<button
  type="button"
  @click="goToMcpToolEditor"
  class="inline-flex items-center gap-2 rounded-full border border-violet-200 bg-violet-50 px-4 py-2 text-sm font-semibold text-violet-700"
>
  <ChevronRight :size="16" />
  发布为 MCP Tool
</button>
```

- [ ] **Step 3: Register the Tool Studio route under Tools**

Add a child route under `/chat/tools`.

```ts
{
  path: 'tools/mcp/new',
  component: McpToolEditorPage,
  meta: { requiresAuth: true },
}
```

If the codebase prefers grouped children under `tools`, use the closest established route pattern rather than inventing a nested router abstraction.

- [ ] **Step 4: Run the build to verify routing compiles**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway add RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue RpaClaw/frontend/src/main.ts
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway commit -m "feat: route rpa mcp publishing into tool studio"
```

## Task 3: Build the dedicated MCP Tool Editor page

**Files:**
- Create: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\pages\tools\McpToolEditorPage.vue`
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\api\rpaMcp.ts`

- [ ] **Step 1: Write the failing build-level contract**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run build
```

Expected: FAIL after the new route is added but before `McpToolEditorPage.vue` exists.

- [ ] **Step 2: Create the editor page with RPA session hydration**

Minimum page responsibilities:

- read `source=rpa-session` and `sessionId` from route query
- call `previewRpaMcpTool(sessionId, ...)`
- show:
  - tool metadata
  - sanitize report
  - input schema
  - output schema
  - preview test section
- save using `createRpaMcpTool(sessionId, ...)`

Skeleton:

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { createRpaMcpTool, previewRpaMcpTool, testPreviewRpaMcpTool } from '@/api/rpaMcp';

const route = useRoute();
const router = useRouter();
const sessionId = computed(() => typeof route.query.sessionId === 'string' ? route.query.sessionId : '');
const preview = ref(null);

onMounted(async () => {
  if (!sessionId.value) return;
  preview.value = await previewRpaMcpTool(sessionId.value, {
    name: typeof route.query.skillName === 'string' ? route.query.skillName : 'rpa_tool',
    description: typeof route.query.skillDescription === 'string' ? route.query.skillDescription : '',
  });
});
</script>
```

The page should reuse the existing conversion/test logic from the current `McpConvertPage.vue` rather than fork it.

- [ ] **Step 3: Extend `rpaMcp.ts` only where the new page needs clearer types**

Add or refine request/response types only if the new Tool Studio page cannot cleanly reuse the current API wrapper.

- [ ] **Step 4: Run the build to verify the new page compiles**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway add RpaClaw/frontend/src/pages/tools/McpToolEditorPage.vue RpaClaw/frontend/src/api/rpaMcp.ts
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway commit -m "feat: add dedicated mcp tool editor page"
```

## Task 4: Reduce the old RPA conversion page to a compatibility handoff

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\pages\rpa\McpConvertPage.vue`

- [ ] **Step 1: Write the failing build-level check**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run build
```

Expected: PASS before changes.

- [ ] **Step 2: Replace the old page with a redirect or thin compatibility wrapper**

Use it only to preserve existing links/bookmarks.

```vue
<script setup lang="ts">
import { onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { buildRpaToolEditorLocation } from '@/utils/rpaMcpConvert';

const route = useRoute();
const router = useRouter();

onMounted(() => {
  router.replace(buildRpaToolEditorLocation({
    sessionId: typeof route.query.sessionId === 'string' ? route.query.sessionId : '',
    skillName: typeof route.query.skillName === 'string' ? route.query.skillName : '',
    skillDescription: typeof route.query.skillDescription === 'string' ? route.query.skillDescription : '',
  }));
});
</script>

<template>
  <div class="min-h-screen flex items-center justify-center text-sm text-slate-500">
    Redirecting to MCP Tool Editor...
  </div>
</template>
```

- [ ] **Step 3: Run the build to verify compatibility remains intact**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run build
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway add RpaClaw/frontend/src/pages/rpa/McpConvertPage.vue
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway commit -m "refactor: redirect legacy mcp convert page to tool studio"
```

## Task 5: Reframe ToolsPage as the main MCP Tool management surface

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend\src\pages\ToolsPage.vue`

- [ ] **Step 1: Write the failing build-level check**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run build
```

Expected: PASS before changes.

- [ ] **Step 2: Add a clear MCP Tools primary entry action**

Add a visible action near the RPA MCP section header:

```vue
<button
  type="button"
  class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold"
  @click="router.push('/chat/tools/mcp/new')"
>
  New MCP Tool
</button>
```

If a source picker is not built yet, the route can open the editor in “expecting RPA source” mode and later grow into a source-selection wizard.

- [ ] **Step 3: Make the page language consistent with the new model**

Use copy that treats ToolsPage as the management surface:

- “MCP Tools”
- “Manage published RPA-backed tools”
- “Create from recording”

Do not describe this area as an extension of the RPA configure page.

- [ ] **Step 4: Run the build to verify it passes**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway add RpaClaw/frontend/src/pages/ToolsPage.vue
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway commit -m "feat: make tools page the primary mcp tool surface"
```

## Task 6: Adjust backend route tests only if Tool Studio hydration exposes API contract gaps

**Files:**
- Modify only if needed: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\backend\route\rpa_mcp.py`
- Modify only if needed: `D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\backend\tests\test_rpa_mcp_route.py`

- [ ] **Step 1: Add the failing backend route test only if the new Tool Studio route reveals a contract gap**

Candidate test:

```python
def test_preview_route_supports_tools_domain_editor_hydration(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: _FakeConverter())

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/preview",
        json={
            "name": "download_invoice",
            "description": "Download invoice",
            "allowed_domains": ["example.com"],
            "post_auth_start_url": "https://example.com/dashboard",
        },
    )

    assert response.status_code == 200
```

- [ ] **Step 2: Run the targeted route test to verify the failure**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_route.py -k tools_domain_editor_hydration -v
```

Expected: FAIL only if a real API contract gap exists.

- [ ] **Step 3: Make the minimal backend fix**

Only adjust backend if the editor split needs a clearer route contract. Avoid speculative backend redesign.

- [ ] **Step 4: Re-run the targeted route test**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_route.py -k tools_domain_editor_hydration -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway add RpaClaw/backend/route/rpa_mcp.py RpaClaw/backend/tests/test_rpa_mcp_route.py
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway commit -m "fix: align rpa mcp routes with tool studio editor"
```

## Task 7: Full verification

**Files:**
- Modify only if verification reveals real defects in files already touched above.

- [ ] **Step 1: Run backend verification**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway
uv run pytest \
  RpaClaw/backend/tests/test_rpa_mcp_converter.py \
  RpaClaw/backend/tests/test_rpa_mcp_executor.py \
  RpaClaw/backend/tests/test_rpa_mcp_route.py \
  RpaClaw/backend/tests/test_mcp_route.py \
  -q
```

Expected: PASS with 0 failures.

- [ ] **Step 2: Run frontend verification**

Run:

```bash
cd D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway\RpaClaw\frontend
npm run test -- src/utils/rpaMcpConvert.test.ts src/utils/rpaMcpTest.test.ts
npm run build
```

Expected: PASS. Existing non-blocking warnings may remain, but no new build failures.

- [ ] **Step 3: Do a manual smoke pass on the new user flow**

Manual flow:

1. Open an RPA session with recorded steps
2. Click `发布为 MCP Tool`
3. Confirm navigation goes to `/chat/tools/mcp/new`
4. Run preview test from the Tool Editor
5. Change only name/description and confirm save still stays valid
6. Change `allowed_domains` or `post_auth_start_url` and confirm preview test becomes stale

- [ ] **Step 4: Commit any final polish**

```bash
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway add RpaClaw/frontend RpaClaw/backend
git -C D:\code\MyScienceClaw\.worktrees\rpa-mcp-gateway commit -m "fix: polish rpa tool studio ia flow"
```

## Self-Review

### Spec coverage

- RPA recording remains the authoring flow: Tasks 2 and 4.
- Skill / MCP Tool dual release model: Task 2 plus Task 5 copy/entry changes.
- Tools domain becomes the MCP Tool management surface: Tasks 3 and 5.
- Legacy RPA MCP convert path remains compatible: Task 4.
- Execution validity remains tied to runtime-relevant fields only: Task 1 and Task 7 smoke flow.

### Placeholder scan

- No `TODO` or `TBD`.
- Each task names exact files and commands.
- Where code is required, concrete snippets are provided.
- Backend changes are explicitly optional rather than hand-waved.

### Type consistency

- New helper name: `buildRpaToolEditorLocation`.
- Primary route target: `/chat/tools/mcp/new`.
- Editor page name: `McpToolEditorPage.vue`.
- Existing preview signature helper remains `buildPreviewDraftSignature`.

Plan complete and saved to `docs/superpowers/plans/2026-04-19-rpa-mcp-tool-studio-ia.md`. Based on your earlier preference, the default next step is inline execution in this worktree without subagents. If you want that, I’ll start with Task 1. 
