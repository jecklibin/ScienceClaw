# RPA AI 录制统一分段 ai_script 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AI 录制统一为按页面状态边界分段的 `ai_script` 主链，删除旧的非 Agent 模式与 AI 录制中的 structured 主路径。

**Architecture:** 后端以 `segment_planner -> segment_runner -> segment_validator -> recording_committer` 为主链，将原来的“structured / code 双轨”改为单一 `segment ai_script` 协议。前端统一消费 segment 事件流，最终步骤面板只展示通过校验的 `segment_committed` 结果。

**Tech Stack:** FastAPI, Python 3.13, Playwright async API, pytest, Vue 3, TypeScript, Vitest

---

## 文件结构

### 新建文件

- `RpaClaw/backend/rpa/segment_models.py`
  统一定义 segment 规格、执行结果、校验结果的数据结构。
- `RpaClaw/backend/rpa/segment_runner.py`
  负责执行单段 `ai_script`，采集输出、异常、执行前后快照和页面变化信息。
- `RpaClaw/backend/rpa/segment_validator.py`
  负责根据 `expected_outcome` 与 `completion_check` 校验段是否达标。
- `RpaClaw/frontend/src/pages/rpa/segmentChatEvents.ts`
  将 segment 事件转换为聊天流和最终步骤流可消费的数据。
- `RpaClaw/frontend/src/pages/rpa/segmentChatEvents.test.ts`
  覆盖新的 segment 事件映射规则。

### 重点修改文件

- `RpaClaw/backend/rpa/assistant.py`
  从 ReAct 原子动作执行器重构为统一的 segment orchestrator。
- `RpaClaw/backend/route/rpa.py`
  删除 `chat`/`react` 双模式入口，统一改为新的 segment 主链，并只持久化 `segment_committed`。
- `RpaClaw/backend/rpa/generator.py`
  保持 `ai_script` 导出能力，同时删除旧 structured 回退假设。
- `RpaClaw/backend/tests/test_rpa_assistant.py`
  删除旧 structured/code 双轨思路的测试，新增统一 segment 主链测试。
- `RpaClaw/backend/tests/test_rpa_generator.py`
  校验统一后的 `ai_script` 步骤仍能稳定导出。
- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
  删除非 Agent UI 分支，统一消费 segment 事件。

### 待删除或下线的旧实现

- `RpaClaw/backend/rpa/assistant_runtime.py` 中仅服务于 AI 录制 structured 主链的接线和测试依赖
- `RpaClaw/backend/rpa/assistant.py` 中 `execution_mode=structured|code`、`_extract_structured_execute_intent()`、`_normalize_execution_mode()` 等旧协议逻辑
- `RpaClaw/frontend/src/pages/rpa/agentChatEvents.ts`
- 前端录制页中 `mode: 'chat' | 'react'` 的旧切换与 UI 文案

---

### Task 1: 建立统一 segment 协议与基础测试

**Files:**
- Create: `RpaClaw/backend/rpa/segment_models.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，固定统一 segment 协议**

```python
def test_segment_spec_requires_ai_script_only():
    from backend.rpa.segment_models import SegmentSpec

    spec = SegmentSpec(
        segment_goal="在当前列表中动态比较 stars 并点击最高项",
        segment_kind="state_changing",
        stop_reason="after_state_change",
        expected_outcome={"type": "page_state_changed", "summary": "进入目标仓库页"},
        completion_check={"url_not_same": True},
        code="async def run(page):\n    return 'ok'",
    )

    assert spec.segment_kind == "state_changing"
    assert spec.code.startswith("async def run(page):")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py -k segment_spec_requires_ai_script_only -q`

Expected: `ImportError` 或 `AttributeError`，说明 `SegmentSpec` 尚未定义。

- [ ] **Step 3: 以最小实现新增统一数据结构**

```python
# RpaClaw/backend/rpa/segment_models.py
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


SegmentKind = Literal["read_only", "state_changing"]
StopReason = Literal["goal_reached", "before_state_change", "after_state_change"]


@dataclass(slots=True)
class SegmentSpec:
    segment_goal: str
    segment_kind: SegmentKind
    stop_reason: StopReason
    expected_outcome: Dict[str, Any]
    completion_check: Dict[str, Any]
    code: str
    notes: str = ""


@dataclass(slots=True)
class SegmentRunResult:
    success: bool
    output: str = ""
    error: str = ""
    page_changed: bool = False
    selected_artifacts: Dict[str, Any] = field(default_factory=dict)
    before_snapshot: Optional[Dict[str, Any]] = None
    after_snapshot: Optional[Dict[str, Any]] = None


@dataclass(slots=True)
class SegmentValidationResult:
    passed: bool
    goal_completed: bool
    reason: str = ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py -k segment_spec_requires_ai_script_only -q`

Expected: `1 passed`

- [ ] **Step 5: 提交本任务**

```bash
git add RpaClaw/backend/rpa/segment_models.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: define unified rpa segment models"
```

### Task 2: 将 assistant 主链改成 segment orchestrator

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，禁止“提取后固定点击”的退化路径**

```python
async def test_react_agent_commits_single_segment_for_dynamic_click_goal(self):
    agent = ASSISTANT_MODULE.RPAReActAgent()
    page = _FakeActionPage()
    snapshots = [
        {"url": "https://example.com/trending", "title": "Trending", "frames": []},
        {"url": "https://example.com/microsoft/markitdown", "title": "markitdown", "frames": []},
    ]
    responses = [
        json.dumps(
            {
                "segment_goal": "在当前趋势列表中动态比较 stars 并点击最高项",
                "segment_kind": "state_changing",
                "stop_reason": "after_state_change",
                "expected_outcome": {"type": "page_state_changed", "summary": "进入目标仓库页"},
                "completion_check": {"url_not_same": True, "page_contains_selected_target": True},
                "code": "async def run(page):\n    return 'clicked'",
            }
        ),
        json.dumps({"action": "done"}),
    ]

    async def fake_stream(_history, _model_config=None):
        yield responses.pop(0)

    async def fake_execute(_page, code):
        return {"success": True, "output": "clicked", "page_changed": True, "selected_artifacts": {"selected_repo_name": "microsoft / markitdown"}}

    agent._stream_llm = fake_stream

    with patch.object(ASSISTANT_MODULE, "build_page_snapshot", new=AsyncMock(side_effect=snapshots)), patch.object(
        ASSISTANT_MODULE, "_execute_on_page", new=fake_execute
    ):
        events = [event async for event in agent.run(session_id="s1", page=page, goal="点击 stars 最多的项目", existing_steps=[])]

    committed = [event for event in events if event["event"] == "segment_committed"]
    assert len(committed) == 1
    assert committed[0]["data"]["step"]["action"] == "ai_script"
    assert "microsoft / markitdown" not in committed[0]["data"]["step"]["description"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py -k single_segment_for_dynamic_click_goal -q`

Expected: FAIL，当前代码仍返回 `agent_step_committed` 或旧协议事件。

- [ ] **Step 3: 用最小实现替换旧的 structured/code 双轨编排**

```python
# RpaClaw/backend/rpa/assistant.py
from backend.rpa.segment_models import SegmentSpec
from backend.rpa.segment_runner import run_segment
from backend.rpa.segment_validator import validate_segment_result


SEGMENT_SYSTEM_PROMPT = """You are an RPA recording planner.
Return exactly one JSON object describing the next ai_script segment.
Never return structured browser actions.
If the segment will trigger any state-changing action such as click, press, submit,
or opening a link, stop the segment immediately after the first such action.
"""


def _parse_segment_spec(text: str) -> SegmentSpec | None:
    parsed = json.loads(text)
    if parsed.get("action") == "done":
        return None
    return SegmentSpec(
        segment_goal=parsed["segment_goal"],
        segment_kind=parsed["segment_kind"],
        stop_reason=parsed["stop_reason"],
        expected_outcome=parsed["expected_outcome"],
        completion_check=parsed["completion_check"],
        code=RPAReActAgent._normalize_run_function(parsed["code"]),
        notes=str(parsed.get("notes", "") or ""),
    )
```

- [ ] **Step 4: 删除旧协议分支并改写事件名称**

```python
# RpaClaw/backend/rpa/assistant.py
yield {"event": "segment_planned", "data": {"segment_goal": spec.segment_goal, "code": spec.code}}
yield {"event": "segment_started", "data": {"segment_goal": spec.segment_goal}}
run_result = await run_segment(current_page, spec)
validation = await validate_segment_result(
    goal=goal,
    spec=spec,
    run_result=run_result,
)
if validation.passed:
    yield {"event": "segment_committed", "data": {"step": self._build_ai_script_step(goal=goal, description=spec.segment_goal, code=spec.code, upgrade_reason="segment", recovery_attempts=0)}}
else:
    yield {"event": "segment_validation_failed", "data": {"segment_goal": spec.segment_goal, "reason": validation.reason}}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py -k "single_segment_for_dynamic_click_goal or normalize_run_function" -q`

Expected: 目标测试通过，已有 `normalize_run_function` 相关测试保持通过。

- [ ] **Step 6: 提交本任务**

```bash
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "refactor: switch ai recording to segment orchestrator"
```

### Task 3: 新增 segment runner 与 validator，并接入状态变化校验

**Files:**
- Create: `RpaClaw/backend/rpa/segment_runner.py`
- Create: `RpaClaw/backend/rpa/segment_validator.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，要求状态变化后必须重观察并校验**

```python
async def test_segment_runner_reobserves_after_state_change(self):
    spec = SegmentSpec(
        segment_goal="点击 stars 最高项并进入详情页",
        segment_kind="state_changing",
        stop_reason="after_state_change",
        expected_outcome={"type": "page_state_changed", "summary": "进入详情页"},
        completion_check={"url_not_same": True},
        code="async def run(page):\n    return 'clicked'",
    )
    page = _FakeActionPage()
    snapshots = [
        {"url": "https://example.com/trending", "title": "Trending", "frames": []},
        {"url": "https://example.com/repo", "title": "Repo", "frames": []},
    ]

    with patch.object(ASSISTANT_MODULE, "build_page_snapshot", new=AsyncMock(side_effect=snapshots)):
        result = await run_segment(page, spec)

    assert result.before_snapshot["url"].endswith("/trending")
    assert result.after_snapshot["url"].endswith("/repo")
    assert result.page_changed is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py -k segment_runner_reobserves_after_state_change -q`

Expected: FAIL，`run_segment` 尚未实现。

- [ ] **Step 3: 实现 segment runner**

```python
# RpaClaw/backend/rpa/segment_runner.py
from backend.rpa.assistant import _execute_on_page
from backend.rpa.assistant_runtime import build_frame_path_from_frame, build_page_snapshot
from backend.rpa.segment_models import SegmentRunResult, SegmentSpec


async def run_segment(page, spec: SegmentSpec) -> SegmentRunResult:
    before_snapshot = await build_page_snapshot(page, build_frame_path_from_frame)
    result = await _execute_on_page(page, spec.code)
    after_snapshot = await build_page_snapshot(page, build_frame_path_from_frame)
    before_url = str(before_snapshot.get("url", ""))
    after_url = str(after_snapshot.get("url", ""))
    return SegmentRunResult(
        success=bool(result.get("success")),
        output=str(result.get("output", "") or ""),
        error=str(result.get("error", "") or ""),
        page_changed=(before_url != after_url) or spec.segment_kind == "state_changing",
        selected_artifacts=dict(result.get("selected_artifacts") or {}),
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
    )
```

- [ ] **Step 4: 实现 validator，禁止动作型目标被“只读提取”误判成功**

```python
# RpaClaw/backend/rpa/segment_validator.py
from backend.rpa.segment_models import SegmentRunResult, SegmentSpec, SegmentValidationResult


async def validate_segment_result(*, goal: str, spec: SegmentSpec, run_result: SegmentRunResult) -> SegmentValidationResult:
    if not run_result.success:
        return SegmentValidationResult(passed=False, goal_completed=False, reason=run_result.error or "segment_failed")
    if spec.segment_kind == "state_changing" and not run_result.page_changed:
        return SegmentValidationResult(passed=False, goal_completed=False, reason="expected_page_change_not_observed")
    lowered_goal = goal.lower()
    if any(word in lowered_goal for word in ("点击", "打开", "下载", "click", "open", "download")):
        if spec.segment_kind == "read_only":
            return SegmentValidationResult(passed=False, goal_completed=False, reason="action_goal_cannot_finish_with_read_only_segment")
    return SegmentValidationResult(passed=True, goal_completed=bool(run_result.page_changed or spec.segment_kind == "read_only"))
```

- [ ] **Step 5: 运行测试确认通过**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py -k "segment_runner_reobserves_after_state_change or action_goal_cannot_finish_with_read_only_segment" -q`

Expected: `2 passed`

- [ ] **Step 6: 提交本任务**

```bash
git add RpaClaw/backend/rpa/segment_runner.py RpaClaw/backend/rpa/segment_validator.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: add segment runner and validator"
```

### Task 4: 统一后端入口并删除旧 chat/react 双模式

**Files:**
- Modify: `RpaClaw/backend/route/rpa.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，要求路由只持久化 `segment_committed`**

```python
async def test_route_persists_only_segment_committed(self):
    request = SimpleNamespace(mode="segment", message="点击 stars 最多的项目")
    session = SimpleNamespace(steps=[], user_id="user-1")
    fake_events = [
        {"event": "segment_planned", "data": {"segment_goal": "动态比较 stars"}},
        {"event": "segment_validation_failed", "data": {"reason": "timeout"}},
        {"event": "segment_committed", "data": {"step": {"action": "ai_script", "description": "动态比较 stars 并点击最高项"}}},
        {"event": "recording_done", "data": {"total_steps": 1}},
    ]

    class _FakeAgent:
        async def run(self, **_kwargs):
            for event in fake_events:
                yield event

    add_step = AsyncMock()
    with patch.object(RPA_ROUTE_MODULE.rpa_manager, "get_session", new=AsyncMock(return_value=session)), patch.object(
        RPA_ROUTE_MODULE.rpa_manager, "add_step", new=add_step
    ), patch.dict(RPA_ROUTE_MODULE._active_agents, {"session-1": _FakeAgent()}):
        response = await RPA_ROUTE_MODULE.chat_with_assistant("session-1", request, SimpleNamespace(id="user-1"))
        async for _ in response.body_iterator:
            pass

    add_step.assert_awaited_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py -k route_persists_only_segment_committed -q`

Expected: FAIL，当前路由仍以 `agent_step_committed` 为提交触发条件。

- [ ] **Step 3: 修改路由，删除旧模式分支**

```python
# RpaClaw/backend/route/rpa.py
@router.post("/session/{session_id}/assistant/chat")
async def chat_with_assistant(session_id: str, request: AssistantChatRequest, current_user=Depends(get_current_user)):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    agent = _active_agents.get(session_id)
    if agent is None:
        agent = RPAReActAgent()
        _active_agents[session_id] = agent

    async def event_stream():
        async for event in agent.run(session_id=session_id, page=rpa_manager.get_page(session_id), goal=request.message, existing_steps=session.steps):
            if event["event"] == "segment_committed" and event["data"].get("step"):
                await rpa_manager.add_step(session_id, event["data"]["step"])
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 4: 删除旧 UI 请求参数语义**

```python
# RpaClaw/backend/route/rpa.py
class AssistantChatRequest(BaseModel):
    message: str
    mode: Literal["segment"] = "segment"
```

- [ ] **Step 5: 运行测试确认通过**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py -k route_persists_only_segment_committed -q`

Expected: `1 passed`

- [ ] **Step 6: 提交本任务**

```bash
git add RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "refactor: unify assistant route on segment mode"
```

### Task 5: 保持导出能力并清理旧 structured 假设

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py`
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: 写失败测试，要求导出后的多段 `ai_script` 仍可执行**

```python
def test_generator_exports_multiple_segment_ai_scripts():
    generator = PlaywrightGenerator()
    steps = [
        {
            "action": "ai_script",
            "description": "动态比较 stars 并点击最高项",
            "value": "async def run(page):\n    selected_repo_name = 'microsoft / markitdown'\n    await page.wait_for_timeout(10)",
        },
        {
            "action": "ai_script",
            "description": "在详情页点击下载",
            "value": "async def run(page):\n    await page.wait_for_timeout(10)",
        },
    ]

    script = generator.generate_script(steps, is_local=True)
    assert "动态比较 stars 并点击最高项" in script
    assert "在详情页点击下载" in script
    assert script.count("await current_page.wait_for_timeout") >= 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_generator.py -k multiple_segment_ai_scripts -q`

Expected: FAIL，如果生成器仍依赖旧 structured 回退或没有正确内联多段 `ai_script`。

- [ ] **Step 3: 清理生成器中旧的 structured 兼容假设**

```python
# RpaClaw/backend/rpa/generator.py
if action == "ai_script":
    ai_code = step.get("value", "")
    if not ai_code:
        continue
    step_lines.append(f"    # {desc}")
    step_lines.append("    page = current_page")
    converted = self._extract_ai_script_body(ai_code)
    converted = self._sync_to_async(converted)
    converted = self._inject_result_capture(converted)
    for code_line in converted.splitlines():
        step_lines.append(f"    {code_line}" if code_line.strip() else "")
    lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
    lines.append("")
    continue
```

- [ ] **Step 4: 运行测试确认通过**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_generator.py -k multiple_segment_ai_scripts -q`

Expected: `1 passed`

- [ ] **Step 5: 提交本任务**

```bash
git add RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "refactor: keep generator focused on ai_script segments"
```

### Task 6: 前端统一为单一 AI 录制入口并切换到 segment 事件

**Files:**
- Create: `RpaClaw/frontend/src/pages/rpa/segmentChatEvents.ts`
- Create: `RpaClaw/frontend/src/pages/rpa/segmentChatEvents.test.ts`
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- Delete: `RpaClaw/frontend/src/pages/rpa/agentChatEvents.ts`
- Delete: `RpaClaw/frontend/src/pages/rpa/agentChatEvents.test.ts`
- Test: `RpaClaw/frontend/src/pages/rpa/segmentChatEvents.test.ts`

- [ ] **Step 1: 写失败测试，要求最终步骤列表只消费 `segment_committed`**

```ts
import { applySegmentEvent } from './segmentChatEvents';

it('only appends committed segments into final steps', () => {
  const state = { messages: [], finalSteps: [] };

  applySegmentEvent(state, { event: 'segment_planned', data: { segment_goal: '动态比较 stars' } });
  applySegmentEvent(state, { event: 'segment_validation_failed', data: { reason: 'timeout' } });
  applySegmentEvent(state, {
    event: 'segment_committed',
    data: { step: { action: 'ai_script', description: '动态比较 stars 并点击最高项' } },
  });

  expect(state.finalSteps).toHaveLength(1);
  expect(state.finalSteps[0].description).toContain('动态比较 stars');
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd RpaClaw/frontend && npm run test -- src/pages/rpa/segmentChatEvents.test.ts`

Expected: FAIL，`segmentChatEvents.ts` 尚不存在。

- [ ] **Step 3: 新增 segment 事件映射器**

```ts
// RpaClaw/frontend/src/pages/rpa/segmentChatEvents.ts
export function applySegmentEvent(state: any, payload: { event: string; data: any }) {
  switch (payload.event) {
    case 'segment_planned':
      state.messages.push({ role: 'assistant', kind: 'plan', text: payload.data.segment_goal });
      return;
    case 'segment_recovering':
    case 'segment_validation_failed':
      state.messages.push({ role: 'assistant', kind: 'recovery', text: payload.data.reason || payload.data.segment_goal });
      return;
    case 'segment_committed':
      state.finalSteps.push(payload.data.step);
      state.messages.push({ role: 'assistant', kind: 'commit', text: payload.data.step.description });
      return;
    default:
      return;
  }
}
```

- [ ] **Step 4: 修改录制页，删除模式切换与旧事件分支**

```ts
// RpaClaw/frontend/src/pages/rpa/RecorderPage.vue
body: JSON.stringify({ message: userText, mode: 'segment' })
```

```ts
// RpaClaw/frontend/src/pages/rpa/RecorderPage.vue
import { applySegmentEvent } from './segmentChatEvents';

if (eventType.startsWith('segment_') || eventType === 'recording_done' || eventType === 'recording_aborted') {
  applySegmentEvent({ messages: chatMessages.value, finalSteps: steps.value }, payload);
}
```

- [ ] **Step 5: 删除旧文件并运行前端测试**

Run: `cd RpaClaw/frontend && npm run test -- src/pages/rpa/segmentChatEvents.test.ts`

Expected: `PASS`

- [ ] **Step 6: 提交本任务**

```bash
git add RpaClaw/frontend/src/pages/rpa/segmentChatEvents.ts RpaClaw/frontend/src/pages/rpa/segmentChatEvents.test.ts RpaClaw/frontend/src/pages/rpa/RecorderPage.vue
git rm RpaClaw/frontend/src/pages/rpa/agentChatEvents.ts RpaClaw/frontend/src/pages/rpa/agentChatEvents.test.ts
git commit -m "refactor: switch recorder ui to segment events"
```

### Task 7: 删除旧主路径并做最终回归

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`
- Test: `RpaClaw/frontend/src/pages/rpa/segmentChatEvents.test.ts`

- [ ] **Step 1: 删除旧 structured/code 协议与无效测试**

```python
# 删除 assistant.py 中以下旧逻辑
# - _extract_structured_execute_intent
# - _normalize_execution_mode
# - CONTROL_FLOW_HINT_RE / DYNAMIC_SELECTION_HINT_RE
# - agent_* 旧事件分支
```

- [ ] **Step 2: 删除非 Agent 模式 UI 与接口文案**

```vue
<!-- RpaClaw/frontend/src/pages/rpa/RecorderPage.vue -->
<!-- 删除 agentMode 开关、旧的 mode=chat 分支、旧成功态文案 -->
```

- [ ] **Step 3: 运行后端回归**

Run: `set PYTHONPATH=D:\code\MyScienceClaw\.worktrees\codex-rpa-ai-recording-reliability\RpaClaw && pytest backend/tests/test_rpa_assistant.py backend/tests/test_rpa_generator.py -q`

Expected: 全部通过，无旧 `agent_*` 事件断言残留。

- [ ] **Step 4: 运行前端回归**

Run: `cd RpaClaw/frontend && npm run test -- src/pages/rpa/segmentChatEvents.test.ts`

Expected: `PASS`

- [ ] **Step 5: 运行类型检查或记录现有遗留问题**

Run: `cd RpaClaw/frontend && npm run -s type-check`

Expected: 若有遗留失败，仅允许出现在本任务无关的既有文件；如 Recorder 相关文件报错，必须先修复。

- [ ] **Step 6: 提交本任务**

```bash
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/frontend/src/pages/rpa/RecorderPage.vue
git commit -m "refactor: remove legacy ai recording paths"
```

## 自检

### Spec 覆盖检查

- 统一入口：Task 4、Task 6、Task 7 覆盖
- 按页面状态边界分段：Task 2、Task 3 覆盖
- 最终只保留 `ai_script`：Task 1、Task 2、Task 5 覆盖
- 页面变化后必须重观察：Task 3 覆盖
- 失败尝试不进入最终步骤：Task 4、Task 6 覆盖
- 清理旧实现：Task 7 覆盖

### 占位符检查

本计划未使用 `TODO`、`TBD`、`后续实现`、`类似 Task N` 等占位表达。每个任务都给出了明确文件、测试命令和最小代码落点。

### 类型与命名一致性检查

- 后端统一使用 `SegmentSpec`、`SegmentRunResult`、`SegmentValidationResult`
- 事件统一使用 `segment_*`
- 最终持久化事件统一为 `segment_committed`

