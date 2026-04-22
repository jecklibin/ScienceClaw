# Segment I/O Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visible, editable segment input/output binding UX to the chat experience so users can inspect, create, change, and validate workflow mappings without relying on natural-language commands.

**Architecture:** Keep the chat page as the primary surface, but split the interaction into two layers: inline quick binding in `RecordingSegmentCard` for the common case, and a dedicated `I/O Mapping Drawer` for deep editing. Reuse one shared frontend source-pool model and one backend mapping API so chat cards, drawer, publish draft warnings, and workflow execution all consume the same binding data.

**Tech Stack:** Vue 3 + TypeScript, FastAPI, Pydantic v2, Vitest, pytest

---

## File Map

### Frontend

- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/types/recording.ts`
  - Add mapping-source view types and binding health metadata
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/api/recording.ts`
  - Add fetch/update APIs for segment mapping sources and bindings
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/utils/recording.ts`
  - Add source-pool builders and binding summary helpers
- Create: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/RecordingIoSourcePicker.vue`
  - Lightweight source selector used from cards
- Create: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/RecordingIoMappingDrawer.vue`
  - Full mapping editor for complex cases
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/RecordingSegmentCard.vue`
  - Show mapping summary, quick bind actions, and emit edit events
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/pages/ChatPage.vue`
  - Wire the drawer, source picker, optimistic updates, and refresh logic
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/utils/recording.test.ts`
- Create: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/__tests__/RecordingIoMappingDrawer.spec.ts`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/__tests__/useRecordingRun.spec.ts`

### Backend

- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/recording/orchestrator.py`
  - Add source-pool generation and binding mutation helpers
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/route/sessions.py`
  - Add read/update endpoints for segment mapping
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/workflow/publishing.py`
  - Surface warnings for unbound or invalid inputs in publish draft
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_sessions.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_workflow_publishing.py`

### Docs

- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/docs/superpowers/specs/2026-04-21-segment-io-mapping-design.md`
  - Mark implemented API names and any design drift discovered during implementation

---

### Task 1: Define Mapping Types And Source-Pool Helpers

**Files:**
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/types/recording.ts`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/utils/recording.ts`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/utils/recording.test.ts`

- [ ] **Step 1: Write the failing tests for source-pool grouping and binding summaries**

```ts
import { describe, expect, it } from 'vitest'
import { buildMappingSourcePool, summarizeInputBindings } from '@/utils/recording'
import type { RecordingSegmentSummary } from '@/types/recording'

describe('buildMappingSourcePool', () => {
  it('groups outputs and artifacts from any historical segment', () => {
    const summaries = [
      {
        segment_id: 'seg-1',
        title: '获取项目名称',
        outputs: [{ name: 'project_name', type: 'string', description: '项目名' }],
        artifacts: [],
      },
      {
        segment_id: 'seg-2',
        title: '下载报表',
        outputs: [],
        artifacts: [{ id: 'artifact-1', name: 'report.xlsx', type: 'file', path: '/tmp/report.xlsx' }],
      },
    ] as RecordingSegmentSummary[]

    const pool = buildMappingSourcePool({
      currentSegmentId: 'seg-3',
      summaries,
      workflowParams: [],
    })

    expect(pool.segmentOutputs).toHaveLength(1)
    expect(pool.artifacts).toHaveLength(1)
    expect(pool.segmentOutputs[0].sourceRef).toBe('seg-1.outputs.project_name')
    expect(pool.artifacts[0].sourceRef).toBe('artifact:artifact-1')
  })
})

describe('summarizeInputBindings', () => {
  it('returns bound and unbound counts with readable summary lines', () => {
    const summary = summarizeInputBindings([
      { name: 'search', type: 'string', source: 'segment_output', source_ref: 'seg-1.outputs.project_name' },
      { name: 'report_file', type: 'file' },
    ])

    expect(summary.boundCount).toBe(1)
    expect(summary.unboundCount).toBe(1)
    expect(summary.lines[0]).toContain('search')
    expect(summary.lines[0]).toContain('seg-1.outputs.project_name')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/utils/recording.test.ts`

Expected: FAIL with missing `buildMappingSourcePool` / `summarizeInputBindings`

- [ ] **Step 3: Add shared mapping types**

```ts
export type MappingSourceType = 'segment_output' | 'artifact' | 'workflow_param'

export interface MappingSourceOption {
  id: string
  sourceType: MappingSourceType
  sourceRef: string
  segmentId?: string
  segmentTitle?: string
  name: string
  valueType: WorkflowValueType
  preview?: string
  recommended?: boolean
}

export interface MappingSourcePool {
  recommended: MappingSourceOption[]
  segmentOutputs: MappingSourceOption[]
  artifacts: MappingSourceOption[]
  workflowParams: MappingSourceOption[]
}

export interface InputBindingSummary {
  boundCount: number
  unboundCount: number
  lines: string[]
}
```

- [ ] **Step 4: Implement source-pool and summary helpers**

```ts
export function buildMappingSourcePool(args: {
  currentSegmentId: string
  summaries: RecordingSegmentSummary[]
  workflowParams: WorkflowIO[]
}): MappingSourcePool {
  const historical = args.summaries.filter((item) => item.segment_id !== args.currentSegmentId)
  const segmentOutputs = historical.flatMap((summary) =>
    deriveSummaryOutputs(summary).map((output) => ({
      id: `${summary.segment_id}:${output.name}`,
      sourceType: 'segment_output' as const,
      sourceRef: `${summary.segment_id}.outputs.${output.name}`,
      segmentId: summary.segment_id,
      segmentTitle: summary.title || summary.intent || summary.segment_id,
      name: output.name,
      valueType: output.type,
      preview: output.description || '',
    })),
  )
  const artifacts = historical.flatMap((summary) =>
    (summary.artifacts || []).map((artifact) => ({
      id: artifact.id || `${summary.segment_id}:${artifact.name}`,
      sourceType: 'artifact' as const,
      sourceRef: artifact.id ? `artifact:${artifact.id}` : `artifact:${artifact.name}`,
      segmentId: summary.segment_id,
      segmentTitle: summary.title || summary.intent || summary.segment_id,
      name: artifact.name,
      valueType: artifact.type === 'file' ? 'file' : artifact.type === 'text' ? 'string' : 'json',
      preview: artifact.path || String(artifact.value || ''),
    })),
  )
  return {
    recommended: [...segmentOutputs.slice(-3), ...artifacts.slice(-3)].slice(-4).reverse(),
    segmentOutputs,
    artifacts,
    workflowParams: args.workflowParams.map((item) => ({
      id: `workflow:${item.name}`,
      sourceType: 'workflow_param' as const,
      sourceRef: `workflow.params.${item.name}`,
      name: item.name,
      valueType: item.type,
      preview: item.description || '',
    })),
  }
}

export function summarizeInputBindings(inputs: WorkflowIO[]): InputBindingSummary {
  const bound = inputs.filter((item) => !!item.source && !!item.source_ref)
  const unbound = inputs.filter((item) => !item.source_ref)
  return {
    boundCount: bound.length,
    unboundCount: unbound.length,
    lines: bound.slice(0, 2).map((item) => `${item.name} <- ${item.source_ref}`),
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- src/utils/recording.test.ts`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add \
  RpaClaw/frontend/src/types/recording.ts \
  RpaClaw/frontend/src/utils/recording.ts \
  RpaClaw/frontend/src/utils/recording.test.ts
git commit -m "feat: add segment io mapping helpers"
```

### Task 2: Add Backend APIs For Reading And Updating Segment Bindings

**Files:**
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/recording/orchestrator.py`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/route/sessions.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_sessions.py`

- [ ] **Step 1: Write the failing backend tests**

```python
def test_orchestrator_builds_mapping_sources_for_historical_segments():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="s1", user_id="u1", kind="mixed")
    first = orchestrator.start_segment(run, kind="rpa", intent="获取项目名称", requires_workbench=True)
    first.exports["outputs"] = [{"name": "project_name", "type": "string", "description": "项目名"}]
    orchestrator.complete_segment(run, first)
    second = orchestrator.start_segment(run, kind="script", intent="搜索项目", requires_workbench=False)

    sources = orchestrator.build_segment_mapping_sources(run, second)

    assert sources["segment_outputs"][0]["source_ref"] == f"{first.id}.outputs.project_name"

async def test_update_segment_binding_route_persists_input_source_ref():
    request = UpdateRecordingSegmentBindingsRequest(
        inputs=[
            {
                "name": "search",
                "type": "string",
                "source": "segment_output",
                "source_ref": "seg-1.outputs.project_name",
                "description": "搜索词",
            }
        ]
    )
    response = await update_recording_segment_bindings("session-1", "run-1", "seg-2", request, current_user=user)
    assert response.data["summary"]["inputs"][0]["source_ref"] == "seg-1.outputs.project_name"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest RpaClaw/backend/tests/test_recording_orchestrator.py RpaClaw/backend/tests/test_sessions.py -q`

Expected: FAIL with missing route/helper symbols

- [ ] **Step 3: Add orchestrator helpers and request models**

```python
class UpdateRecordingSegmentBindingsRequest(BaseModel):
    inputs: list[dict[str, Any]] = Field(default_factory=list)

def build_segment_mapping_sources(self, run: RecordingRun, segment: RecordingSegment) -> dict[str, list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    for candidate in run.segments:
        if candidate.id == segment.id:
            continue
        summary = self.build_segment_summary(candidate)
        for output in summary.get("outputs", []):
            sources.append({
                "id": f"{candidate.id}:{output['name']}",
                "source_type": "segment_output",
                "source_ref": f"{candidate.id}.outputs.{output['name']}",
                "segment_id": candidate.id,
                "segment_title": summary.get("title") or summary.get("intent") or candidate.id,
                "name": output["name"],
                "type": output["type"],
                "preview": output.get("description", ""),
            })
    return {"segment_outputs": sources}

def update_segment_inputs(self, segment: RecordingSegment, inputs: list[dict[str, Any]]) -> dict[str, object]:
    segment.exports["inputs"] = inputs
    return self.build_segment_summary(segment)
```

- [ ] **Step 4: Add read/update routes**

```python
@router.get("/{session_id}/recordings/{run_id}/segments/{segment_id}/mapping-sources", response_model=ApiResponse)
async def list_recording_segment_mapping_sources(...):
    ...
    return ApiResponse(data=recording_orchestrator.build_segment_mapping_sources(run, segment))

@router.put("/{session_id}/recordings/{run_id}/segments/{segment_id}/bindings", response_model=ApiResponse)
async def update_recording_segment_bindings(...):
    ...
    summary = recording_orchestrator.update_segment_inputs(segment, body.inputs)
    _append_session_event(session, _wrap_event("recording_segment_updated", {
        "event_id": _new_event_id(),
        "timestamp": _now_ts(),
        "run": _serialize_recording_obj(run),
        "summaries": [summary],
    }))
    await session.save()
    return ApiResponse(data={"summary": summary, "run": _serialize_recording_obj(run)})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest RpaClaw/backend/tests/test_recording_orchestrator.py RpaClaw/backend/tests/test_sessions.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add \
  RpaClaw/backend/recording/orchestrator.py \
  RpaClaw/backend/route/sessions.py \
  RpaClaw/backend/tests/test_recording_orchestrator.py \
  RpaClaw/backend/tests/test_sessions.py
git commit -m "feat: add recording segment binding apis"
```

### Task 3: Add Quick Binding UI To Segment Cards

**Files:**
- Create: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/RecordingIoSourcePicker.vue`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/RecordingSegmentCard.vue`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/pages/ChatPage.vue`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/api/recording.ts`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/__tests__/useRecordingRun.spec.ts`

- [ ] **Step 1: Write the failing UI test**

```ts
it('shows binding summary and emits quick-bind updates from the card', async () => {
  const summary = {
    segment_id: 'seg-2',
    title: '搜索项目',
    inputs: [{ name: 'search', type: 'string', description: '搜索词' }],
    outputs: [],
    artifacts: [],
  }
  const wrapper = mount(RecordingSegmentCard, {
    props: {
      summary,
      sourcePool: {
        recommended: [
          {
            id: 'seg-1:project_name',
            sourceType: 'segment_output',
            sourceRef: 'seg-1.outputs.project_name',
            segmentTitle: '获取项目名称',
            name: 'project_name',
            valueType: 'string',
          },
        ],
        segmentOutputs: [],
        artifacts: [],
        workflowParams: [],
      },
    },
  })
  await wrapper.find('button[data-testid="bind-search"]').trigger('click')
  expect(wrapper.text()).toContain('待绑定')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/components/__tests__/useRecordingRun.spec.ts`

Expected: FAIL because `sourcePool` / quick-bind UI is missing

- [ ] **Step 3: Create the picker component**

```vue
<template>
  <div class="rounded-2xl border border-gray-200 bg-white p-3 shadow-lg">
    <div v-if="groups.recommended.length" class="space-y-2">
      <p class="text-[11px] font-black uppercase tracking-[0.16em] text-violet-500">推荐来源</p>
      <button
        v-for="item in groups.recommended"
        :key="item.id"
        type="button"
        class="flex w-full items-start justify-between rounded-xl border border-gray-200 px-3 py-2 text-left hover:bg-violet-50"
        @click="$emit('select', item)"
      >
        <span>{{ item.segmentTitle }}.{{ item.name }}</span>
        <span class="text-xs text-gray-400">{{ item.valueType }}</span>
      </button>
    </div>
  </div>
</template>
```

- [ ] **Step 4: Extend the card and chat page**

```vue
<!-- RecordingSegmentCard.vue -->
<button
  :data-testid="`bind-${item.name}`"
  class="rounded-lg border border-amber-200 px-2.5 py-1 text-[11px] font-bold text-amber-700"
  @click.stop="openPickerFor(item.name)"
>
  {{ item.source_ref ? '更换来源' : '选择来源' }}
</button>

<RecordingIoSourcePicker
  v-if="pickerInputName === item.name"
  :groups="groupedSourcePool"
  @select="(source) => $emit('quick-bind', { inputName: item.name, source })"
  @close="pickerInputName = null"
/>
```

```ts
// ChatPage.vue
const handleQuickBind = async (summary: RecordingSegmentSummary, inputName: string, source: MappingSourceOption) => {
  const nextInputs = deriveSummaryInputs(summary).map((item) =>
    item.name === inputName
      ? { ...item, source: source.sourceType, source_ref: source.sourceRef }
      : item,
  )
  await updateRecordingSegmentBindings(sessionId.value!, recordingStore.run.value!.id, summary.segment_id, { inputs: nextInputs })
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- src/components/__tests__/useRecordingRun.spec.ts`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add \
  RpaClaw/frontend/src/api/recording.ts \
  RpaClaw/frontend/src/components/RecordingIoSourcePicker.vue \
  RpaClaw/frontend/src/components/RecordingSegmentCard.vue \
  RpaClaw/frontend/src/pages/ChatPage.vue \
  RpaClaw/frontend/src/components/__tests__/useRecordingRun.spec.ts
git commit -m "feat: add quick segment io binding ui"
```

### Task 4: Build The I/O Mapping Drawer For Deep Editing

**Files:**
- Create: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/RecordingIoMappingDrawer.vue`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/pages/ChatPage.vue`
- Create: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/frontend/src/components/__tests__/RecordingIoMappingDrawer.spec.ts`

- [ ] **Step 1: Write the failing drawer test**

```ts
it('loads current inputs and updates the selected input binding', async () => {
  const wrapper = mount(RecordingIoMappingDrawer, {
    props: {
      open: true,
      summary: {
        segment_id: 'seg-2',
        title: '搜索项目',
        inputs: [{ name: 'search', type: 'string', description: '搜索词' }],
        outputs: [],
        artifacts: [],
      },
      sourcePool: {
        recommended: [],
        segmentOutputs: [
          {
            id: 'seg-1:project_name',
            sourceType: 'segment_output',
            sourceRef: 'seg-1.outputs.project_name',
            segmentTitle: '获取项目名称',
            name: 'project_name',
            valueType: 'string',
          },
        ],
        artifacts: [],
        workflowParams: [],
      },
    },
  })
  await wrapper.find('[data-testid="input-search"]').trigger('click')
  await wrapper.find('[data-testid="source-seg-1:project_name"]').trigger('click')
  expect(wrapper.emitted('save')?.[0][0].inputs[0].source_ref).toBe('seg-1.outputs.project_name')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/components/__tests__/RecordingIoMappingDrawer.spec.ts`

Expected: FAIL because the drawer does not exist

- [ ] **Step 3: Implement the drawer**

```vue
<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex justify-end bg-black/20">
      <aside class="flex h-full w-[720px] flex-col bg-white shadow-2xl">
        <header class="border-b border-gray-200 px-5 py-4">
          <h2 class="text-sm font-black text-gray-950">输入输出映射</h2>
        </header>
        <div class="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)_280px]">
          <div class="border-r border-gray-200 p-3">
            <button
              v-for="item in draftInputs"
              :key="item.name"
              :data-testid="`input-${item.name}`"
              class="mb-2 w-full rounded-xl border px-3 py-2 text-left"
              @click="selectedInputName = item.name"
            >
              {{ item.name }}
            </button>
          </div>
          <div class="border-r border-gray-200 p-3">
            <button
              v-for="item in sourcePool.segmentOutputs"
              :key="item.id"
              :data-testid="`source-${item.id}`"
              class="mb-2 w-full rounded-xl border px-3 py-2 text-left"
              @click="applySource(item)"
            >
              {{ item.segmentTitle }}.{{ item.name }}
            </button>
          </div>
          <div class="p-3">
            <pre class="rounded-xl bg-gray-50 p-3 text-xs">{{ selectedInputPreview }}</pre>
            <button class="mt-3 rounded-xl bg-violet-600 px-3 py-2 text-sm font-bold text-white" @click="$emit('save', { inputs: draftInputs })">
              保存映射
            </button>
          </div>
        </div>
      </aside>
    </div>
  </Teleport>
</template>
```

- [ ] **Step 4: Wire it into the chat page**

```ts
const mappingDrawerState = ref<{
  open: boolean
  summary: RecordingSegmentSummary | null
}>({ open: false, summary: null })

const handleOpenMappingDrawer = (summary: RecordingSegmentSummary) => {
  mappingDrawerState.value = { open: true, summary }
}

const handleSaveDrawerBindings = async ({ inputs }: { inputs: WorkflowIO[] }) => {
  const summary = mappingDrawerState.value.summary
  if (!summary || !sessionId.value || !recordingStore.run.value) return
  await updateRecordingSegmentBindings(sessionId.value, recordingStore.run.value.id, summary.segment_id, { inputs })
  mappingDrawerState.value = { open: false, summary: null }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- src/components/__tests__/RecordingIoMappingDrawer.spec.ts src/components/__tests__/useRecordingRun.spec.ts`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add \
  RpaClaw/frontend/src/components/RecordingIoMappingDrawer.vue \
  RpaClaw/frontend/src/components/__tests__/RecordingIoMappingDrawer.spec.ts \
  RpaClaw/frontend/src/pages/ChatPage.vue
git commit -m "feat: add segment io mapping drawer"
```

### Task 5: Surface Binding Health In Publish Draft And Final Verification

**Files:**
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/workflow/publishing.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_workflow_publishing.py`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/docs/superpowers/specs/2026-04-21-segment-io-mapping-design.md`

- [ ] **Step 1: Write the failing publish-warning test**

```python
def test_build_publish_draft_warns_when_segment_inputs_are_unbound():
    run = _sample_run()
    run.segments[1].inputs = [
        {
            "name": "search",
            "type": "string",
            "required": True,
            "description": "搜索词",
        }
    ]

    draft = build_publish_draft(run, publish_target="skill")

    assert any(item.code == "unbound_segment_input" for item in draft.warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest RpaClaw/backend/tests/test_workflow_publishing.py -q`

Expected: FAIL because no unbound-input warning exists

- [ ] **Step 3: Add binding-health warnings**

```python
for segment in ordered:
    for item in segment.inputs:
        if item.required and item.source not in {"segment_output", "artifact", "workflow_param", "credential"}:
            warnings.append(
                PublishWarning(
                    code="unbound_segment_input",
                    message=f"片段「{segment.title}」的输入「{item.name}」尚未绑定来源。",
                    segment_id=segment.id,
                )
            )
        elif item.source in {"segment_output", "artifact"} and not item.source_ref:
            warnings.append(
                PublishWarning(
                    code="invalid_segment_binding",
                    message=f"片段「{segment.title}」的输入「{item.name}」缺少 source_ref。",
                    segment_id=segment.id,
                )
            )
```

- [ ] **Step 4: Update the spec with implementation decisions**

```md
## 实现补充

- 后端 API 使用 `GET /mapping-sources` 和 `PUT /bindings`
- 快速绑定使用轻量选择器
- 抽屉复用同一份来源池视图模型
```

- [ ] **Step 5: Run verification**

Run: `python -m pytest RpaClaw/backend/tests/test_workflow_publishing.py -q`

Expected: PASS

Run: `python -m pytest RpaClaw/backend/tests/test_recording_orchestrator.py RpaClaw/backend/tests/test_sessions.py RpaClaw/backend/tests/test_recording_testing.py RpaClaw/backend/tests/test_recording_publishing.py RpaClaw/backend/tests/test_workflow_publishing.py -q`

Expected: PASS

Run: `npm run test -- src/utils/recording.test.ts src/components/__tests__/useRecordingRun.spec.ts src/components/__tests__/RecordingIoMappingDrawer.spec.ts src/utils/recordingEvents.test.ts src/utils/__tests__/recordingEvents.spec.ts src/components/__tests__/recordingLifecycle.spec.ts`

Expected: PASS

Run: `npm run build`

Expected: PASS with only existing non-blocking warnings

- [ ] **Step 6: Commit**

```bash
git add \
  RpaClaw/backend/workflow/publishing.py \
  RpaClaw/backend/tests/test_workflow_publishing.py \
  docs/superpowers/specs/2026-04-21-segment-io-mapping-design.md
git commit -m "feat: validate segment io mapping before publish"
```

---

## Self-Review

### Spec coverage

- 聊天页摘要显示：Task 3
- 快速绑定：Task 3
- 抽屉完整编辑：Task 4
- 任意历史 segment / artifact 来源池：Task 1 + Task 2
- 测试/发布复用最新映射：Task 2 + Task 5
- warning 与发布联动：Task 5

### Placeholder scan

- No `TODO`, `TBD`, “similar to previous task”, or unspecified commands remain
- Every code-changing step includes concrete code blocks
- Every verification step includes exact commands

### Type consistency

- Frontend source model uses `MappingSourceOption` / `MappingSourcePool` consistently
- Backend binding payload uses `inputs: list[dict[str, Any]]` consistently
- `source_ref` remains the contract across card, drawer, backend, publish draft, and workflow execution
