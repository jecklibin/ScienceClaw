# Conversational RPA / MCP Recording Final Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild conversational RPA / MCP recording as a first-class built-in skill with a shared recorder core and a full generate/test/publish lifecycle.

**Architecture:** Remove the `sessions.py` recording bypass entirely and let a new `recording-creator` built-in skill drive recording lifecycle through explicit recording APIs. Refactor `RecorderPage.vue` and `TestPage.vue` into a shared recorder core that powers both the standalone recorder routes and the right-side chat workbench, then connect testing, repair, and publish back into the existing `propose_skill_save` / `propose_tool_save` flow.

**Tech Stack:** FastAPI, Pydantic v2, Vue 3, TypeScript, existing RPA manager/generator/test flows, built-in skills, SSE session events.

---

## File Map

### Backend

- Modify: `RpaClaw/backend/deepagent/agent.py`
  - Register the new built-in skill as a first-class resource in the main agent policy.
- Modify: `RpaClaw/backend/route/sessions.py`
  - Remove recording-intent chat short-circuit behavior and keep only generic session save/prompt plumbing.
- Modify: `RpaClaw/backend/route/rpa.py`
  - Reuse test/repair endpoints from conversational publishing flow where needed.
- Modify: `RpaClaw/backend/rpa/manager.py`
  - Preserve download artifacts and any metadata needed for run/test/publish lifecycle.
- Create/modify: `RpaClaw/backend/recording/models.py`
  - Extend run/segment state for testing and publishing.
- Create/modify: `RpaClaw/backend/recording/orchestrator.py`
  - Move run lifecycle forward without chat-route intent matching.
- Create: `RpaClaw/backend/recording/lifecycle.py`
  - Centralize allowed state transitions.
- Create: `RpaClaw/backend/recording/testing.py`
  - Build and execute recording test flow using existing RPA test behavior.
- Create: `RpaClaw/backend/recording/publishing.py`
  - Build staged skill/tool outputs and trigger save prompts.
- Create/modify: `RpaClaw/backend/recording/artifact_registry.py`
  - Manage artifact registration and imports/exports.
- Create/modify: `RpaClaw/backend/recording/step_repair_service.py`
  - Normalize step repair behavior for test failures and summary cards.
- Create: `RpaClaw/backend/recording/adapters/rpa_adapter.py`
  - Translate recorder/test results into recording-domain summaries.
- Create: `RpaClaw/backend/recording/adapters/mcp_adapter.py`
  - Translate MCP-step execution into recording-domain summaries.
- Create: `RpaClaw/backend/builtin_skills/recording-creator/SKILL.md`
  - Define trigger scope and lifecycle instructions for the agent.

### Frontend

- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderSession.ts`
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderScreencast.ts`
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderSteps.ts`
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderAssistant.ts`
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderTesting.ts`
  - Shared recorder core extracted from current recorder/test pages.
- Create: `RpaClaw/frontend/src/components/recorder/RecorderWorkbenchShell.vue`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderSidebar.vue`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderCanvasStage.vue`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderAssistantPanel.vue`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderTestPanel.vue`
  - Shared visual shell for recorder page, test page, and chat workbench.
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
  - Swap page-specific logic to shared recorder core.
- Modify: `RpaClaw/frontend/src/pages/rpa/TestPage.vue`
  - Swap page-specific logic to shared test/repair core.
- Modify: `RpaClaw/frontend/src/pages/ChatPage.vue`
  - Replace simplified right workbench with shared recorder shell.
- Modify: `RpaClaw/frontend/src/api/recording.ts`
  - Add lifecycle APIs for create/segment/test/publish/repair.
- Modify: `RpaClaw/frontend/src/types/recording.ts`
  - Extend types for lifecycle, testing, and publish state.
- Modify: `RpaClaw/frontend/src/composables/useRecordingRun.ts`
  - Manage full run state instead of summary-only state.
- Delete or stop using: `RpaClaw/frontend/src/components/RecordingWorkbench.vue`
  - Remove simplified duplicate implementation after shared shell is live.

### Tests

- Modify: `RpaClaw/backend/tests/test_sessions.py`
  - Prove chat no longer short-circuits recording requests.
- Modify: `RpaClaw/backend/tests/test_recording_orchestrator.py`
  - Cover new lifecycle transitions and publish targets.
- Create: `RpaClaw/backend/tests/test_recording_testing.py`
- Create: `RpaClaw/backend/tests/test_recording_publishing.py`
- Create: `RpaClaw/frontend/src/components/__tests__/recorderShell.spec.ts`
- Create: `RpaClaw/frontend/src/components/__tests__/recordingLifecycle.spec.ts`

---

### Task 1: Remove Chat-Route Recording Bypass And Lock The New Entry Contract

**Files:**
- Modify: `RpaClaw/backend/tests/test_sessions.py`
- Modify: `RpaClaw/backend/route/sessions.py`
- Modify: `RpaClaw/backend/tests/test_recording_orchestrator.py`

- [ ] **Step 1: Write the failing backend tests for “no bypass” behavior**

```python
async def test_chat_with_session_does_not_short_circuit_recording_requests():
    session = SimpleNamespace(
        user_id="u1",
        status=SESSIONS_MODULE.SessionStatus.PENDING,
        events=[],
        save=AsyncMock(),
        model_config=None,
    )

    with unittest.mock.patch.object(
        SESSIONS_MODULE,
        "async_get_science_session",
        AsyncMock(return_value=session),
    ), unittest.mock.patch.object(
        SESSIONS_MODULE,
        "_ensure_agent_worker_running",
        AsyncMock(),
    ) as ensure_worker:
        response = await SESSIONS_MODULE.chat_with_session(
            "session-1",
            SESSIONS_MODULE.ChatRequest(message="我要录制个业务流程技能"),
            SimpleNamespace(),
            current_user=SimpleNamespace(id="u1"),
        )

    assert isinstance(response, SESSIONS_MODULE.EventSourceResponse)
    ensure_worker.assert_awaited_once()
    assert not any(event["event"] == "recording_run_started" for event in session.events)
```

- [ ] **Step 2: Run the targeted backend tests to verify they fail against the current bypass**

Run: `uv run pytest tests/test_sessions.py tests/test_recording_orchestrator.py -q`

Expected: FAIL because `chat_with_session` still emits `recording_run_started` directly from `sessions.py`.

- [ ] **Step 3: Remove the recording intent short-circuit from `sessions.py` and keep only generic session flow**

```python
async def chat_with_session(...):
    ...
    # Remove detect_recording_intent() short-circuit entirely.
    existing_task = _agent_tasks.get(session_id)
    is_reconnect = existing_task is not None and not existing_task.done()
    ...
```

```python
def _serialize_recording_obj(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    ...
```

The route keeps generic recording CRUD endpoints, but `POST /chat` no longer decides that the user is “starting a recording”.

- [ ] **Step 4: Replace the bypass-focused tests with lifecycle-neutral tests**

```python
def test_detect_recording_intent_for_business_workflow_skill_phrase():
    intent = detect_recording_intent("我要录制个业务流程技能")
    assert intent is not None
    assert intent.kind == "rpa"
    assert intent.save_intent == "skill"
```

```python
async def test_create_recording_run_route_requires_explicit_api_call():
    ...
    data = await SESSIONS_MODULE.create_recording_run(
        "session-1",
        SESSIONS_MODULE.CreateRecordingRunRequest(message="我要录制个业务流程技能"),
        current_user=SimpleNamespace(id="u1"),
    )
    assert data.data["open_workbench"] is True
```

- [ ] **Step 5: Run the backend tests to verify the bypass is gone**

Run: `uv run pytest tests/test_sessions.py tests/test_recording_orchestrator.py -q`

Expected: PASS with no `recording_run_started` event emitted from `chat_with_session`.

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/route/sessions.py RpaClaw/backend/tests/test_sessions.py RpaClaw/backend/tests/test_recording_orchestrator.py
git commit -m "refactor: remove chat recording bypass"
```

### Task 2: Promote Recording-Creator To A Real Built-In Skill Entry

**Files:**
- Modify: `RpaClaw/backend/deepagent/agent.py`
- Modify: `RpaClaw/backend/builtin_skills/recording-creator/SKILL.md`
- Test: `RpaClaw/backend/tests/test_recording_orchestrator.py`

- [ ] **Step 1: Write a failing test that locks the skill’s trigger metadata**

```python
def test_recording_creator_skill_mentions_skill_tool_and_multisegment_triggers():
    skill_md = Path("backend/builtin_skills/recording-creator/SKILL.md").read_text(encoding="utf-8")
    assert "业务流程技能" in skill_md
    assert "MCP 工具" in skill_md
    assert "多段" in skill_md
    assert "发布" in skill_md
```

- [ ] **Step 2: Run the single test to verify it fails if the skill body is incomplete**

Run: `uv run pytest tests/test_recording_orchestrator.py -q`

Expected: FAIL until the skill body fully describes generate/test/publish workflow.

- [ ] **Step 3: Expand `recording-creator` to own the full lifecycle**

```md
## 生命周期
1. 创建 recording run
2. 指导录制当前 segment
3. 完成后提取 artifacts
4. 决定继续下一段还是进入测试
5. 测试失败时走修复
6. 测试通过后触发发布
```

```md
## 发布规则
- 目标是 skill 时，准备 staging 结果并调用 propose_skill_save
- 目标是 tool / MCP tool 时，准备 staging 结果并调用 propose_tool_save
```

- [ ] **Step 4: Update the main agent policy so recording is treated like other first-class built-in skills**

```python
## Task Resources
- **Record an RPA skill or MCP workflow** → `read_file("{builtin_path_for_prompt}recording-creator/SKILL.md")`.
- After recording is complete, use the skill workflow to test, repair, and publish.
```

Also remove wording that implies `skill-creator` / `tool-creator` are the only creation entry points.

- [ ] **Step 5: Run the test to verify the built-in skill contract is explicit**

Run: `uv run pytest tests/test_recording_orchestrator.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/deepagent/agent.py RpaClaw/backend/builtin_skills/recording-creator/SKILL.md RpaClaw/backend/tests/test_recording_orchestrator.py
git commit -m "feat: promote recording creator to full lifecycle skill"
```

### Task 3: Implement Recording Lifecycle For Generate, Test, Repair, And Publish

**Files:**
- Create: `RpaClaw/backend/recording/lifecycle.py`
- Create: `RpaClaw/backend/recording/testing.py`
- Create: `RpaClaw/backend/recording/publishing.py`
- Modify: `RpaClaw/backend/recording/models.py`
- Modify: `RpaClaw/backend/recording/orchestrator.py`
- Modify: `RpaClaw/backend/recording/artifact_registry.py`
- Create: `RpaClaw/backend/tests/test_recording_testing.py`
- Create: `RpaClaw/backend/tests/test_recording_publishing.py`

- [ ] **Step 1: Write the failing lifecycle tests**

```python
def test_run_moves_from_ready_for_next_segment_to_testing():
    run = RecordingRun(...)
    run.status = "ready_for_next_segment"
    move_run_status(run, "testing")
    assert run.status == "testing"
```

```python
async def test_publish_skill_builds_staging_output_and_prompt():
    run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="rpa")
    run.publish_target = "skill"
    result = await build_publish_artifacts(run, workspace_dir="D:/tmp/workspace")
    assert result.prompt_kind == "skill"
    assert result.staging_paths
```

- [ ] **Step 2: Run the lifecycle tests to verify they fail**

Run: `uv run pytest tests/test_recording_testing.py tests/test_recording_publishing.py -q`

Expected: FAIL because lifecycle and publish helpers do not exist yet.

- [ ] **Step 3: Add explicit lifecycle helpers and statuses**

```python
ALLOWED_TRANSITIONS = {
    "draft": {"recording"},
    "recording": {"processing_artifacts", "failed"},
    "processing_artifacts": {"ready_for_next_segment", "testing", "failed"},
    "ready_for_next_segment": {"recording", "testing", "ready_to_publish", "failed"},
    "testing": {"needs_repair", "ready_to_publish", "failed"},
    "needs_repair": {"testing", "failed"},
    "ready_to_publish": {"saved", "failed"},
}

def move_run_status(run: RecordingRun, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(run.status, set())
    if target not in allowed:
        raise ValueError(f"invalid transition: {run.status} -> {target}")
    run.status = target
```

- [ ] **Step 4: Implement testing and publish helpers**

```python
@dataclass
class PublishPreparation:
    prompt_kind: Literal["skill", "tool"]
    staging_paths: list[str]
    summary: dict[str, Any]

async def build_publish_artifacts(run: RecordingRun, workspace_dir: str) -> PublishPreparation:
    if run.publish_target == "skill":
        target_dir = Path(workspace_dir) / run.session_id / "skills_staging" / run.id
        ...
        return PublishPreparation(prompt_kind="skill", staging_paths=[str(target_dir)], summary=summary)
    ...
```

```python
async def build_test_payload(run: RecordingRun) -> dict[str, Any]:
    latest_segment = run.segments[-1]
    return {
        "steps": latest_segment.steps,
        "artifacts": [artifact.model_dump(mode="json") for artifact in latest_segment.artifacts],
    }
```

- [ ] **Step 5: Extend orchestrator and artifact registry to use the new lifecycle**

```python
class RecordingOrchestrator:
    def mark_segment_completed(...):
        move_run_status(run, "processing_artifacts")
        ...
        move_run_status(run, "ready_for_next_segment")

    def begin_testing(self, run: RecordingRun) -> None:
        move_run_status(run, "testing")

    def mark_ready_to_publish(self, run: RecordingRun, publish_target: str) -> None:
        run.publish_target = publish_target
        move_run_status(run, "ready_to_publish")
```

- [ ] **Step 6: Run lifecycle tests plus existing recording tests**

Run: `uv run pytest tests/test_recording_models.py tests/test_recording_orchestrator.py tests/test_recording_testing.py tests/test_recording_publishing.py -q`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add RpaClaw/backend/recording RpaClaw/backend/tests/test_recording_models.py RpaClaw/backend/tests/test_recording_orchestrator.py RpaClaw/backend/tests/test_recording_testing.py RpaClaw/backend/tests/test_recording_publishing.py
git commit -m "feat: add recording lifecycle testing and publishing"
```

### Task 4: Wire Lifecycle APIs For Segment Completion, Testing, Repair, And Publish

**Files:**
- Modify: `RpaClaw/backend/route/sessions.py`
- Modify: `RpaClaw/backend/route/rpa.py`
- Modify: `RpaClaw/backend/tests/test_sessions.py`
- Modify: `RpaClaw/backend/tests/test_recording_step_repair.py`

- [ ] **Step 1: Write failing API tests for test/publish lifecycle**

```python
async def test_begin_recording_test_route_sets_testing_status():
    ...
    data = await SESSIONS_MODULE.test_recording_run("session-1", run.id, current_user=user)
    assert data.data["run"]["status"] == "testing"
```

```python
async def test_publish_recording_run_returns_prompt_kind():
    ...
    data = await SESSIONS_MODULE.publish_recording_run("session-1", run.id, current_user=user)
    assert data.data["prompt_kind"] == "skill"
```

- [ ] **Step 2: Run the targeted tests to confirm the routes are missing**

Run: `uv run pytest tests/test_sessions.py tests/test_recording_step_repair.py -q`

Expected: FAIL because the lifecycle routes do not exist.

- [ ] **Step 3: Implement lifecycle endpoints in `sessions.py`**

```python
@router.post("/{session_id}/recordings/{run_id}/test", response_model=ApiResponse)
async def test_recording_run(...):
    run = recording_orchestrator.get_run(run_id)
    recording_orchestrator.begin_testing(run)
    payload = await recording_testing.build_test_payload(run)
    return ApiResponse(data={"run": run.model_dump(mode="json"), "test_payload": payload})
```

```python
@router.post("/{session_id}/recordings/{run_id}/publish", response_model=ApiResponse)
async def publish_recording_run(...):
    run = recording_orchestrator.get_run(run_id)
    recording_orchestrator.mark_ready_to_publish(run, body.publish_target)
    prepared = await recording_publishing.build_publish_artifacts(run, workspace_dir=_WORKSPACE_DIR)
    return ApiResponse(data={"run": run.model_dump(mode="json"), "prompt_kind": prepared.prompt_kind, "staging_paths": prepared.staging_paths})
```

- [ ] **Step 4: Bridge recording test failures back into existing RPA step repair path**

```python
def map_failed_step_to_repair_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "failed_step_index": result.get("failed_step_index"),
        "failed_step_candidates": result.get("failed_step_candidates", []),
        "error": result.get("error", ""),
    }
```

Keep the actual locator-candidate selection on `/rpa/session/{session_id}/step/{step_index}/locator`.

- [ ] **Step 5: Run route-level tests and existing recording tests**

Run: `uv run pytest tests/test_sessions.py tests/test_recording_step_repair.py tests/test_recording_orchestrator.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/route/sessions.py RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_sessions.py RpaClaw/backend/tests/test_recording_step_repair.py
git commit -m "feat: add recording lifecycle session APIs"
```

### Task 5: Extract Shared Recorder Core From RecorderPage And TestPage

**Files:**
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderSession.ts`
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderScreencast.ts`
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderSteps.ts`
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderAssistant.ts`
- Create: `RpaClaw/frontend/src/composables/recorder/useRecorderTesting.ts`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderWorkbenchShell.vue`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderSidebar.vue`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderCanvasStage.vue`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderAssistantPanel.vue`
- Create: `RpaClaw/frontend/src/components/recorder/RecorderTestPanel.vue`
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- Modify: `RpaClaw/frontend/src/pages/rpa/TestPage.vue`
- Create: `RpaClaw/frontend/src/components/__tests__/recorderShell.spec.ts`

- [ ] **Step 1: Write the failing shared-shell test**

```ts
it('renders steps, assistant panel, and canvas from shared shell props', () => {
  const wrapper = mount(RecorderWorkbenchShell, {
    props: {
      title: 'Recording Workbench',
      steps: [{ id: '1', title: '下载按钮', status: 'completed' }],
      messages: [{ role: 'assistant', text: '已准备开始录制' }],
      testingState: { status: 'idle' },
    },
  })

  expect(wrapper.text()).toContain('下载按钮')
  expect(wrapper.text()).toContain('已准备开始录制')
})
```

- [ ] **Step 2: Run the frontend test to verify the shared shell is missing**

Run: `npm run test -- recorderShell.spec.ts`

Expected: FAIL because the shared recorder shell/components do not exist yet.

- [ ] **Step 3: Extract shared composables from `RecorderPage.vue` and `TestPage.vue`**

```ts
export function useRecorderSession() {
  const sessionId = ref<string | null>(null)
  const tabs = ref<BrowserTab[]>([])
  const addressInput = ref('about:blank')
  const activeTabId = ref<string | null>(null)
  return { sessionId, tabs, addressInput, activeTabId }
}
```

```ts
export function useRecorderTesting() {
  const testing = ref(false)
  const testDone = ref(false)
  const failedStepIndex = ref<number | null>(null)
  const failedStepCandidates = ref<LocatorCandidate[]>([])
  return { testing, testDone, failedStepIndex, failedStepCandidates }
}
```

- [ ] **Step 4: Build the shared shell with full recorder affordances**

```vue
<template>
  <div class="flex h-full flex-col">
    <RecorderSidebar :steps="steps" :messages="messages" :testing-state="testingState" />
    <RecorderCanvasStage ... />
    <RecorderAssistantPanel :messages="messages" :pending-confirm="pendingConfirm" />
    <RecorderTestPanel :testing-state="testingState" />
  </div>
</template>
```

Do not regress to a canvas-only layout. The shell must expose steps, assistant flow, and testing state.

- [ ] **Step 5: Rewire `RecorderPage.vue` and `TestPage.vue` to the shared shell without changing behavior**

```vue
<RecorderWorkbenchShell
  :title="'录制技能'"
  :steps="steps"
  :messages="chatMessages"
  :testing-state="testingState"
  ...
/>
```

- [ ] **Step 6: Run the shell test and build**

Run: `npm run test -- recorderShell.spec.ts`

Expected: PASS

Run: `npm run build`

Expected: PASS (allowing existing repository warnings outside this change scope).

- [ ] **Step 7: Commit**

```bash
git add RpaClaw/frontend/src/composables/recorder RpaClaw/frontend/src/components/recorder RpaClaw/frontend/src/pages/rpa/RecorderPage.vue RpaClaw/frontend/src/pages/rpa/TestPage.vue RpaClaw/frontend/src/components/__tests__/recorderShell.spec.ts
git commit -m "refactor: extract shared recorder core"
```

### Task 6: Replace The Simplified Chat Workbench With The Shared Recorder Shell

**Files:**
- Modify: `RpaClaw/frontend/src/pages/ChatPage.vue`
- Modify: `RpaClaw/frontend/src/composables/useRecordingRun.ts`
- Modify: `RpaClaw/frontend/src/api/recording.ts`
- Modify: `RpaClaw/frontend/src/types/recording.ts`
- Modify: `RpaClaw/frontend/src/components/RecordingSegmentCard.vue`
- Modify: `RpaClaw/frontend/src/components/RecordingArtifactList.vue`
- Create: `RpaClaw/frontend/src/components/__tests__/recordingLifecycle.spec.ts`
- Delete or stop importing: `RpaClaw/frontend/src/components/RecordingWorkbench.vue`

- [ ] **Step 1: Write failing frontend lifecycle tests**

```ts
it('opens shared recorder shell for active interactive segment and closes it on completion', async () => {
  const store = createRecordingRunStore()
  store.onRunStarted({
    run: { id: 'run-1', status: 'recording', type: 'rpa' },
    segment: { id: 'seg-1', status: 'recording', kind: 'rpa', intent: '下载 PDF' },
    open_workbench: true,
  })
  expect(store.workbenchOpen.value).toBe(true)
  store.onSegmentCompleted({
    segment: { id: 'seg-1', status: 'completed' },
    summary: { segment_id: 'seg-1', artifacts: [], steps: [] },
  })
  expect(store.workbenchOpen.value).toBe(false)
})
```

- [ ] **Step 2: Run the lifecycle test to verify the chat flow is still summary-only**

Run: `npm run test -- recordingLifecycle.spec.ts useRecordingRun.spec.ts`

Expected: FAIL until the store and chat page manage testing/publish-aware run state.

- [ ] **Step 3: Extend recording API/types/store for full lifecycle**

```ts
export interface RecordingRun {
  id: string
  status: RecordingRunStatus
  type?: 'rpa' | 'mcp' | 'mixed'
  publish_target?: 'skill' | 'tool' | null
  testing?: {
    status: 'idle' | 'running' | 'failed' | 'passed'
    failed_step_index?: number | null
  }
}
```

```ts
export async function testRecordingRun(sessionId: string, runId: string) {
  const response = await apiClient.post(`/sessions/${sessionId}/recordings/${runId}/test`)
  return response.data.data
}
```

- [ ] **Step 4: Replace the simplified right-side component with the shared recorder shell**

```vue
<RecorderWorkbenchShell
  v-if="recordingStore.workbenchOpen.value"
  :title="recordingStore.activeSegment.value?.intent || 'Recording Workbench'"
  :steps="recordingStore.activeSteps.value"
  :messages="recordingStore.assistantMessages.value"
  :testing-state="recordingStore.testingState.value"
  ...
/>
```

Keep `RecordingSegmentCard` focused on completed summaries and step repair, not on live recording canvas.

- [ ] **Step 5: Run the lifecycle tests plus the build**

Run: `npm run test -- recordingLifecycle.spec.ts useRecordingRun.spec.ts recorderShell.spec.ts`

Expected: PASS

Run: `npm run build`

Expected: PASS (allowing pre-existing repository warnings not introduced by these changes).

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/frontend/src/pages/ChatPage.vue RpaClaw/frontend/src/composables/useRecordingRun.ts RpaClaw/frontend/src/api/recording.ts RpaClaw/frontend/src/types/recording.ts RpaClaw/frontend/src/components/RecordingSegmentCard.vue RpaClaw/frontend/src/components/RecordingArtifactList.vue RpaClaw/frontend/src/components/__tests__/recordingLifecycle.spec.ts
git commit -m "feat: embed shared recorder shell in chat"
```

### Task 7: Connect Test, Repair, And Publish Back Into Existing Save Prompts

**Files:**
- Modify: `RpaClaw/backend/recording/publishing.py`
- Modify: `RpaClaw/backend/route/sessions.py`
- Modify: `RpaClaw/frontend/src/pages/ChatPage.vue`
- Modify: `RpaClaw/frontend/src/components/RecordingSegmentCard.vue`
- Modify: `RpaClaw/frontend/src/utils/recording.ts`
- Test: `RpaClaw/backend/tests/test_recording_publishing.py`
- Test: `RpaClaw/frontend/src/components/__tests__/recordingLifecycle.spec.ts`

- [ ] **Step 1: Write the failing tests for prompt reuse**

```python
async def test_publish_recording_run_for_skill_reuses_existing_prompt_flow():
    result = await build_publish_artifacts(run, workspace_dir=temp_dir)
    assert result.prompt_kind == "skill"
    assert "skills_staging" in result.staging_paths[0]
```

```ts
it('keeps publish target in store and surfaces skill save prompt after publish prep', () => {
  ...
  expect(store.run.value?.publish_target).toBe('skill')
})
```

- [ ] **Step 2: Run the targeted tests to verify prompt reuse is missing**

Run: `uv run pytest tests/test_recording_publishing.py -q`

Expected: FAIL if publish preparation does not shape staging output correctly.

Run: `npm run test -- recordingLifecycle.spec.ts`

Expected: FAIL if the store does not track publish target / readiness.

- [ ] **Step 3: Implement staging output that matches existing save endpoints**

```python
def _skill_staging_dir(workspace_dir: str, session_id: str, run_id: str) -> Path:
    return Path(workspace_dir) / session_id / "skills_staging" / run_id

def _tool_staging_dir(workspace_dir: str, session_id: str, run_id: str) -> Path:
    return Path(workspace_dir) / session_id / "tools_staging"
```

For skill publish, stage a skill directory with `SKILL.md` and any helper files. For tool publish, stage a single `.py` file compatible with the existing `save_tool_from_session`.

- [ ] **Step 4: Surface publish readiness in chat UI**

```ts
if (publishResult.prompt_kind === 'skill') {
  pendingSkillSave.value = publishResult.summary.name
} else {
  pendingToolSave.value = publishResult.summary.name
}
```

Also keep the completed run summary visible after publish prep so the user understands what they are saving.

- [ ] **Step 5: Run publish tests, frontend lifecycle tests, and a backend save regression**

Run: `uv run pytest tests/test_recording_publishing.py tests/test_sessions.py -q`

Expected: PASS

Run: `npm run test -- recordingLifecycle.spec.ts useRecordingRun.spec.ts`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/recording/publishing.py RpaClaw/backend/route/sessions.py RpaClaw/frontend/src/pages/ChatPage.vue RpaClaw/frontend/src/components/RecordingSegmentCard.vue RpaClaw/frontend/src/utils/recording.ts RpaClaw/backend/tests/test_recording_publishing.py RpaClaw/frontend/src/components/__tests__/recordingLifecycle.spec.ts
git commit -m "feat: connect recording publish flow to existing save prompts"
```

### Task 8: Final Verification, Cleanup, And Removal Of Transitional Code

**Files:**
- Delete or stop using: `RpaClaw/frontend/src/components/RecordingWorkbench.vue`
- Review: `RpaClaw/backend/route/sessions.py`
- Review: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- Review: `RpaClaw/frontend/src/pages/rpa/TestPage.vue`
- Review: `RpaClaw/frontend/src/pages/ChatPage.vue`

- [ ] **Step 1: Remove the transitional simplified workbench from imports and templates**

```vue
<!-- Remove -->
<RecordingWorkbench ... />

<!-- Keep -->
<RecorderWorkbenchShell ... />
```

- [ ] **Step 2: Run the full scoped verification suite**

Run: `uv run pytest tests/test_rpa_manager.py tests/test_recording_models.py tests/test_recording_orchestrator.py tests/test_recording_step_repair.py tests/test_recording_testing.py tests/test_recording_publishing.py tests/test_sessions.py -q`

Expected: PASS

Run: `npm run test -- useRecordingRun.spec.ts recordingUtils.spec.ts recorderShell.spec.ts recordingLifecycle.spec.ts`

Expected: PASS

Run: `npm run build`

Expected: PASS

- [ ] **Step 3: Run the known-bad full type-check only as a diagnostic**

Run: `npm run type-check`

Expected: FAIL only on existing repository-wide issues unrelated to the new recorder architecture. Record any newly introduced errors and fix them before closing the task.

- [ ] **Step 4: Review the acceptance checklist against the spec**

Checklist:

- [ ] Main chat recording uses built-in skill flow, not chat-route bypass
- [ ] Right workbench uses shared recorder core and shows steps + assistant
- [ ] `/rpa/recorder` and `/rpa/test` still work with shared core
- [ ] Multi-segment artifact handoff still works
- [ ] Testing and repair flow is available before publish
- [ ] Publish reuses `propose_skill_save` / `propose_tool_save`

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend RpaClaw/frontend
git commit -m "feat: finalize conversational recording architecture"
```

---

## Self-Review

### Spec coverage

- Skill-based entry replaces `sessions.py` bypass: Task 1 and Task 2
- Shared recorder core replaces simplified workbench: Task 5 and Task 6
- Generate/test/repair/publish lifecycle: Task 3, Task 4, Task 7
- Reuse existing save prompts: Task 7
- Final acceptance and cleanup: Task 8

### Placeholder scan

- No `TODO` / `TBD`
- Each task lists exact files, test commands, and concrete code shapes
- Each implementation step points to specific modules and data shapes

### Type consistency

- `publish_target` is used consistently in run state, lifecycle APIs, and publish flow
- Shared recorder shell is named `RecorderWorkbenchShell.vue` everywhere
- Lifecycle statuses use the same names across models, store, and routes
