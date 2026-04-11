# RPA 录制助手统一技能与运行时 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前 RPA 录制助手改造成统一录制入口、脚本优先、录制态验收、失败自动恢复、统一技能导出的实现。

**Architecture:** 后端新增录制编排层、步骤分类器、步骤校验器、恢复 Agent 和统一技能运行时，复用现有 `assistant.py` 的页面观察与结构化动作能力。前端移除 `agentMode` 开关，统一展示步骤类型、录制态验收结果和恢复状态；技能导出从纯 `skill.py` 升级为 `manifest.json + skill.py`。

**Tech Stack:** FastAPI, Python 3.13, Playwright async API, unittest, Vue 3 + TypeScript, SSE

---

## 文件结构

### 新增文件

- `RpaClaw/backend/rpa/recording_orchestrator.py`
  - 统一录制入口，串起候选步骤生成、分类、执行、录制态验收与落库
- `RpaClaw/backend/rpa/step_classifier.py`
  - 根据用户指令和候选步骤判定 `script_step` / `agent_step`
- `RpaClaw/backend/rpa/step_validator.py`
  - 负责录制态验收与运行时轻量校验
- `RpaClaw/backend/rpa/recovery_agent.py`
  - 负责脚本失败后的环境恢复
- `RpaClaw/backend/rpa/skill_manifest.py`
  - 定义统一技能 manifest 结构与序列化
- `RpaClaw/backend/rpa/skill_runtime.py`
  - 统一运行 `script -> validate -> recover -> retry` 状态机
- `RpaClaw/backend/tests/test_rpa_recording_orchestrator.py`
- `RpaClaw/backend/tests/test_rpa_step_classifier.py`
- `RpaClaw/backend/tests/test_rpa_step_validator.py`
- `RpaClaw/backend/tests/test_rpa_recovery_agent.py`
- `RpaClaw/backend/tests/test_rpa_skill_manifest.py`
- `RpaClaw/backend/tests/test_rpa_skill_runtime.py`

### 修改文件

- `RpaClaw/backend/rpa/assistant.py`
  - 抽出单步候选生成接口，保留结构化动作与 Agent 基础能力
- `RpaClaw/backend/route/rpa.py`
  - 统一 `/session/{session_id}/chat` 入口，移除前端依赖的 `mode` 分叉
- `RpaClaw/backend/rpa/skill_exporter.py`
  - 导出 `manifest.json + skill.py + SKILL.md`
- `RpaClaw/backend/rpa/generator.py`
  - 补充生成脚本片段和 runtime entry 需要的辅助能力
- `RpaClaw/backend/tests/test_rpa_assistant.py`
  - 调整为单步候选生成和编排协作测试
- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
  - 去掉 `agentMode`，改用统一录制事件和恢复态 UI

### 不改动文件

- `RpaClaw/backend/rpa/manager.py`
  - 继续负责录制 session、page 获取、step 持久化
- `RpaClaw/backend/rpa/assistant_runtime.py`
  - 继续负责页面快照、结构化动作解析与执行

## Task 1: 定义统一步骤模型与 Manifest

**Files:**
- Create: `RpaClaw/backend/rpa/skill_manifest.py`
- Create: `RpaClaw/backend/tests/test_rpa_skill_manifest.py`
- Modify: `RpaClaw/backend/rpa/skill_exporter.py`

- [ ] **Step 1: 写失败测试，固定 manifest 结构**

```python
import unittest

from backend.rpa.skill_manifest import build_manifest


class SkillManifestTests(unittest.TestCase):
    def test_build_manifest_includes_script_and_agent_steps(self):
        manifest = build_manifest(
            skill_name="review_processor",
            description="处理评论",
            params={"keyword": {"type": "string", "description": "关键词"}},
            steps=[
                {
                    "id": "step_1",
                    "type": "script",
                    "action": "click",
                    "description": "打开评论列表",
                    "script_fragment": "await current_page.get_by_role('button', name='评论').click()",
                    "validator": {"kind": "dom_change", "acceptance_hint": "评论列表出现"},
                    "recovery": {"enabled": True, "max_attempts": 1},
                },
                {
                    "id": "step_2",
                    "type": "agent",
                    "description": "判断评论情绪并执行分支",
                    "goal": "如果积极执行 X，否则执行 Y",
                },
            ],
        )

        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["steps"][0]["type"], "script")
        self.assertEqual(manifest["steps"][1]["type"], "agent")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_rpa_skill_manifest -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.skill_manifest'`

- [ ] **Step 3: 写最小实现，定义 manifest builder**

```python
from __future__ import annotations

from typing import Any, Dict, List


def build_manifest(
    *,
    skill_name: str,
    description: str,
    params: Dict[str, Any],
    steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "version": 2,
        "name": skill_name,
        "description": description,
        "goal": {"summary": description},
        "params": params,
        "steps": steps,
    }
```

- [ ] **Step 4: 升级 skill_exporter，写入 `manifest.json`**

```python
from backend.rpa.skill_manifest import build_manifest

manifest = build_manifest(
    skill_name=skill_name,
    description=description,
    params=params,
    steps=steps,
)

(skill_dir / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m unittest tests.test_rpa_skill_manifest -v`

Expected: PASS with `OK`

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/skill_manifest.py RpaClaw/backend/rpa/skill_exporter.py RpaClaw/backend/tests/test_rpa_skill_manifest.py
git commit -m "feat: add rpa skill manifest"
```

## Task 2: 实现步骤分类器，统一 `script_step` / `agent_step`

**Files:**
- Create: `RpaClaw/backend/rpa/step_classifier.py`
- Create: `RpaClaw/backend/tests/test_rpa_step_classifier.py`
- Modify: `RpaClaw/backend/rpa/assistant.py`

- [ ] **Step 1: 写失败测试，覆盖脚本优先和 Agent 降级**

```python
import unittest

from backend.rpa.step_classifier import classify_candidate_step


class StepClassifierTests(unittest.TestCase):
    def test_prefers_script_for_extract_text(self):
        candidate = {
            "action": "extract_text",
            "description": "提取最新评论标题",
            "result_key": "latest_review_title",
        }
        result = classify_candidate_step(
            prompt="提取最新评论标题",
            candidate_step=candidate,
            execution_failed=False,
            validation_failed=False,
        )
        self.assertEqual(result["type"], "script")

    def test_marks_agent_for_semantic_branching_prompt(self):
        candidate = {"action": "ai_script", "description": "判断评论情绪"}
        result = classify_candidate_step(
            prompt="判断评论情绪，如果积极就回复，否则归档",
            candidate_step=candidate,
            execution_failed=False,
            validation_failed=False,
        )
        self.assertEqual(result["type"], "agent")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_rpa_step_classifier -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.step_classifier'`

- [ ] **Step 3: 写最小实现，先用规则分类**

```python
from __future__ import annotations

from typing import Any, Dict


BRANCH_KEYWORDS = ("判断", "如果", "否则", "情绪", "分类")


def classify_candidate_step(
    *,
    prompt: str,
    candidate_step: Dict[str, Any],
    execution_failed: bool,
    validation_failed: bool,
) -> Dict[str, Any]:
    if any(keyword in prompt for keyword in BRANCH_KEYWORDS):
        return {"type": "agent", "reason": "semantic_branch"}
    if execution_failed and validation_failed:
        return {"type": "agent", "reason": "script_cannot_stabilize"}
    return {"type": "script", "reason": "default_script_first"}
```

- [ ] **Step 4: 在 `assistant.py` 暴露候选步骤生成函数**

```python
class RPAAssistant:
    async def generate_candidate_step(
        self,
        *,
        session_id: str,
        page: Page,
        message: str,
        steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> Dict[str, Any]:
        snapshot = await build_page_snapshot(page_provider() if page_provider else page, build_frame_path_from_frame)
        messages = self._build_messages(message, steps, snapshot, self._get_history(session_id))
        full_response = ""
        async for chunk_text in self._stream_llm(messages, model_config):
            full_response += chunk_text
        structured_intent = self._extract_structured_intent(full_response)
        return {
            "raw_response": full_response,
            "structured_intent": structured_intent,
            "code": self._extract_code(full_response),
            "snapshot": snapshot,
        }
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m unittest tests.test_rpa_step_classifier -v`

Expected: PASS with `OK`

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/step_classifier.py RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_step_classifier.py
git commit -m "feat: add rpa step classifier"
```

## Task 3: 实现录制态验收与统一录制编排

**Files:**
- Create: `RpaClaw/backend/rpa/step_validator.py`
- Create: `RpaClaw/backend/rpa/recording_orchestrator.py`
- Create: `RpaClaw/backend/tests/test_rpa_step_validator.py`
- Create: `RpaClaw/backend/tests/test_rpa_recording_orchestrator.py`
- Modify: `RpaClaw/backend/route/rpa.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，提取错值时不得入库**

```python
import unittest
from unittest.mock import AsyncMock

from backend.rpa.recording_orchestrator import RecordingOrchestrator


class RecordingOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_step_is_not_saved_when_validation_fails(self):
        assistant = AsyncMock()
        assistant.generate_candidate_step.return_value = {
            "structured_intent": {"action": "extract_text", "result_key": "latest_review_title"},
            "snapshot": {"frames": []},
        }
        validator = AsyncMock()
        validator.validate_recording_step.return_value = {
            "accepted": False,
            "reason": "value came from wrong section",
        }
        manager = AsyncMock()

        orchestrator = RecordingOrchestrator(assistant=assistant, validator=validator, manager=manager)
        result = await orchestrator.record_step(
            session_id="session-1",
            page=object(),
            message="提取最新评论标题",
            existing_steps=[],
        )

        self.assertFalse(result["saved"])
        manager.add_step.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_rpa_recording_orchestrator -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.recording_orchestrator'`

- [ ] **Step 3: 写最小实现，先支持录制态验收阻断落库**

```python
class StepValidator:
    async def validate_recording_step(self, *, prompt, candidate_step, snapshot, execution_result):
        output = execution_result.get("output")
        if candidate_step.get("action") == "extract_text" and not output:
            return {"accepted": False, "reason": "empty extraction output"}
        return {"accepted": True, "reason": "recording validation passed"}


class RecordingOrchestrator:
    async def record_step(self, *, session_id, page, message, existing_steps, model_config=None, page_provider=None):
        candidate = await self.assistant.generate_candidate_step(
            session_id=session_id,
            page=page,
            message=message,
            steps=existing_steps,
            model_config=model_config,
            page_provider=page_provider,
        )
        execution_result = await self._execute_candidate(page=page, candidate=candidate)
        validation = await self.validator.validate_recording_step(
            prompt=message,
            candidate_step=candidate,
            snapshot=candidate["snapshot"],
            execution_result=execution_result,
        )
        if not validation["accepted"]:
            return {"saved": False, "validation": validation}
        await self.manager.add_step(session_id, execution_result["step"])
        return {"saved": True, "validation": validation, "step": execution_result["step"]}
```

- [ ] **Step 4: 改造 `/session/{session_id}/chat` 统一走 orchestrator**

```python
orchestrator = RecordingOrchestrator(
    assistant=assistant,
    validator=step_validator,
    manager=rpa_manager,
)

async for event in orchestrator.stream_recording(
    session_id=session_id,
    page=page,
    message=request.message,
    existing_steps=steps,
    model_config=model_config,
    page_provider=lambda: rpa_manager.get_page(session_id),
):
    yield {
        "event": event["event"],
        "data": json.dumps(event["data"], ensure_ascii=False),
    }
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m unittest tests.test_rpa_step_validator tests.test_rpa_recording_orchestrator -v`

Expected: PASS with `OK`

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/step_validator.py RpaClaw/backend/rpa/recording_orchestrator.py RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_rpa_step_validator.py RpaClaw/backend/tests/test_rpa_recording_orchestrator.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: orchestrate rpa recording validation"
```

## Task 4: 实现恢复 Agent 与统一运行时

**Files:**
- Create: `RpaClaw/backend/rpa/recovery_agent.py`
- Create: `RpaClaw/backend/rpa/skill_runtime.py`
- Create: `RpaClaw/backend/tests/test_rpa_recovery_agent.py`
- Create: `RpaClaw/backend/tests/test_rpa_skill_runtime.py`
- Modify: `RpaClaw/backend/rpa/assistant.py`

- [ ] **Step 1: 写失败测试，脚本失败后先恢复再重试原步骤**

```python
import unittest
from unittest.mock import AsyncMock

from backend.rpa.skill_runtime import SkillRuntime


class SkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_retries_same_script_step_after_recovery(self):
        executor = AsyncMock()
        executor.run_script_step.side_effect = [
            {"success": False, "error": "click intercepted"},
            {"success": True, "output": "ok"},
        ]
        validator = AsyncMock()
        validator.validate_runtime_step.return_value = {"accepted": True}
        recovery_agent = AsyncMock()
        recovery_agent.recover.return_value = {"success": True, "actions": ["close modal"]}

        runtime = SkillRuntime(executor=executor, validator=validator, recovery_agent=recovery_agent)
        manifest = {
            "steps": [
                {
                    "id": "step_1",
                    "type": "script",
                    "description": "点击提交按钮",
                    "recovery": {"enabled": True, "max_attempts": 1},
                }
            ]
        }

        result = await runtime.run(manifest=manifest, page=object())

        self.assertTrue(result["success"])
        self.assertEqual(executor.run_script_step.await_count, 2)
        recovery_agent.recover.assert_awaited_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_rpa_skill_runtime -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.skill_runtime'`

- [ ] **Step 3: 写最小恢复 Agent 与运行时实现**

```python
class RecoveryAgent:
    async def recover(self, *, page, failed_step, error, snapshot):
        if "intercepted" in (error or ""):
            return {"success": True, "actions": ["close modal"]}
        return {"success": False, "actions": []}


class SkillRuntime:
    async def run(self, *, manifest, page):
        for step in manifest["steps"]:
            if step["type"] == "agent":
                result = await self.executor.run_agent_step(page=page, step=step)
                if not result["success"]:
                    return {"success": False, "failed_step": step["id"]}
                continue

            attempts = 0
            while True:
                result = await self.executor.run_script_step(page=page, step=step)
                if result["success"]:
                    validation = await self.validator.validate_runtime_step(
                        step=step,
                        page=page,
                        execution_result=result,
                    )
                    if validation["accepted"]:
                        break
                if not step.get("recovery", {}).get("enabled") or attempts >= step.get("recovery", {}).get("max_attempts", 0):
                    return {"success": False, "failed_step": step["id"]}
                attempts += 1
                recovery = await self.recovery_agent.recover(
                    page=page,
                    failed_step=step,
                    error=result.get("error"),
                    snapshot={},
                )
                if not recovery["success"]:
                    return {"success": False, "failed_step": step["id"]}
        return {"success": True}
```

- [ ] **Step 4: 在 `assistant.py` 抽出恢复 Agent 可复用的观察与 Agent 执行入口**

```python
class RPAReActAgent:
    async def recover_environment(
        self,
        *,
        page: Page,
        failed_step: Dict[str, Any],
        error: str,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snapshot = await build_page_snapshot(page, build_frame_path_from_frame)
        goal = f"Recover the page so this script step can be retried safely: {failed_step.get('description', '')}. Error: {error}"
        events = []
        async for event in self.run(
            session_id="recovery",
            page=page,
            goal=goal,
            existing_steps=[],
            model_config=model_config,
        ):
            events.append(event)
        return {"success": any(evt["event"] == "agent_done" for evt in events), "events": events, "snapshot": snapshot}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m unittest tests.test_rpa_recovery_agent tests.test_rpa_skill_runtime -v`

Expected: PASS with `OK`

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/recovery_agent.py RpaClaw/backend/rpa/skill_runtime.py RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_recovery_agent.py RpaClaw/backend/tests/test_rpa_skill_runtime.py
git commit -m "feat: add rpa recovery runtime"
```

## Task 5: 升级导出与执行入口，输出统一技能包

**Files:**
- Modify: `RpaClaw/backend/rpa/skill_exporter.py`
- Modify: `RpaClaw/backend/rpa/generator.py`
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`
- Modify: `RpaClaw/backend/tests/test_rpa_executor.py`

- [ ] **Step 1: 写失败测试，确认导出物包含 runtime entry 和 manifest**

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.rpa.skill_exporter import SkillExporter


class SkillExporterRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_writes_manifest_and_runtime_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exporter = SkillExporter()
            with patch("backend.rpa.skill_exporter.settings.storage_backend", "local"), patch(
                "backend.rpa.skill_exporter.settings.external_skills_dir",
                temp_dir,
            ):
                await exporter.export_skill(
                    user_id="user-1",
                    skill_name="review_skill",
                    description="处理评论",
                    script="print('legacy')",
                    params={},
                    steps=[{"id": "step_1", "type": "script", "description": "打开评论"}],
                )
            skill_dir = Path(temp_dir) / "review_skill"
            self.assertTrue((skill_dir / "manifest.json").exists())
            self.assertIn("SkillRuntime", (skill_dir / "skill.py").read_text(encoding="utf-8"))
            manifest = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], 2)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_rpa_executor -v`

Expected: FAIL with `TypeError: export_skill() got an unexpected keyword argument 'steps'`

- [ ] **Step 3: 修改导出接口，接受 `steps` 并生成 runtime entry**

```python
async def export_skill(
    self,
    user_id: str,
    skill_name: str,
    description: str,
    script: str,
    params: Dict[str, Any],
    steps: list[Dict[str, Any]],
) -> str:
    manifest = build_manifest(
        skill_name=skill_name,
        description=description,
        params=params,
        steps=steps,
    )
    runtime_entry = """\
import json
from pathlib import Path

from backend.rpa.skill_runtime import SkillRuntime


def load_manifest():
    return json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))


async def execute_skill(page, **kwargs):
    runtime = SkillRuntime()
    return await runtime.run(manifest=load_manifest(), page=page)
"""
```

- [ ] **Step 4: 调整生成器为导出阶段保留步骤片段，而不是只生成整段脚本**

```python
def build_script_fragment(self, step: Dict[str, Any], scope_var: str = "current_page") -> str:
    if step.get("action") == "click":
        locator = self._build_adaptive_locator_for_step(step, scope_var) or self._build_locator_for_page(step.get("target", ""), scope_var)
        return f"await {locator}.click()"
    if step.get("action") == "extract_text":
        locator = self._build_adaptive_locator_for_step(step, scope_var) or self._build_locator_for_page(step.get('target', ''), scope_var)
        result_key = step.get("result_key", "value")
        return f'_results["{result_key}"] = await {locator}.inner_text()'
    raise ValueError(f"Unsupported action for fragment build: {step.get('action')}")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m unittest tests.test_rpa_generator tests.test_rpa_executor -v`

Expected: PASS with `OK`

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/skill_exporter.py RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_generator.py RpaClaw/backend/tests/test_rpa_executor.py
git commit -m "feat: export unified rpa skill runtime"
```

## Task 6: 收口前端录制页，移除模式开关并展示验收/恢复状态

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`

- [ ] **Step 1: 写前端最小验收用例说明，锁定 UI 行为**

```ts
// 手工验收目标：页面不再出现 agentMode 切换按钮；发送指令后能显示：步骤类型、录制态验收结果、恢复状态。
const expectedBadges = [
  '已录制为脚本步骤',
  '已录制为 Agent 步骤',
  '录制态验收已通过',
  '正在尝试自动恢复环境',
];
```

- [ ] **Step 2: 删除 `agentMode` 状态与模式切换 UI**

```ts
const agentMode = ref(false);
```

改为：

```ts
const recordingStatus = ref<'idle' | 'executing' | 'validated' | 'recovering'>('idle');
```

并删除：

```vue
@click="agentMode = !agentMode"
```

- [ ] **Step 3: 调整请求体，统一调用后端录制入口**

```ts
body: JSON.stringify({ message: userText }),
```

替换现有：

```ts
body: JSON.stringify({ message: userText, mode: agentMode.value ? 'react' : 'chat' }),
```

- [ ] **Step 4: 处理新的 SSE 事件，展示步骤类型、验收结果和恢复状态**

```ts
} else if (eventType === 'recording_classified') {
  chatMessages.value[msgIdx].text += `\n步骤类型：${data.step_type === 'agent' ? 'Agent 步骤' : '脚本步骤'}`;
} else if (eventType === 'recording_validated') {
  recordingStatus.value = 'validated';
  chatMessages.value[msgIdx].text += '\n录制态验收已通过';
} else if (eventType === 'recovery_started') {
  recordingStatus.value = 'recovering';
  chatMessages.value[msgIdx].text += '\n正在尝试自动恢复环境';
} else if (eventType === 'recovery_finished') {
  chatMessages.value[msgIdx].text += data.success ? '\n恢复成功，正在重试原步骤' : '\n恢复失败';
}
```

- [ ] **Step 5: 手工验证页面行为**

Run:

```bash
cd RpaClaw/frontend
npm run dev
```

Expected:

- 页面不再出现 `Agent 模式` 开关
- 发送普通提取指令时显示 `脚本步骤` 和 `录制态验收已通过`
- 模拟恢复事件时显示 `正在尝试自动恢复环境`

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/frontend/src/pages/rpa/RecorderPage.vue
git commit -m "feat: unify rpa recorder assistant ui"
```

## Task 7: 端到端回归与文档收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-04-11-rpa-ai-assistant-unified-runtime-design.md`
- Create: `docs/test-report-rpa-ai-assistant-unified-runtime.md`

- [ ] **Step 1: 运行后端聚焦测试集**

Run:

```bash
cd RpaClaw/backend
python -m unittest tests.test_rpa_assistant tests.test_rpa_step_classifier tests.test_rpa_step_validator tests.test_rpa_recording_orchestrator tests.test_rpa_recovery_agent tests.test_rpa_skill_manifest tests.test_rpa_skill_runtime -v
```

Expected: all tests PASS with `OK`

- [ ] **Step 2: 运行现有生成器与录制器回归**

Run:

```bash
cd RpaClaw/backend
python -m unittest tests.test_rpa_manager tests.test_rpa_generator tests.test_rpa_executor -v
```

Expected: PASS with `OK`

- [ ] **Step 3: 写测试报告**

```markdown
# RPA 录制助手统一技能与运行时测试报告

- 后端聚焦测试：通过
- 生成器/导出回归：通过
- 前端手工验证：通过
- 已验证场景：
  - 提取类步骤录制态验收
  - 语义分支步骤落为 Agent 步
  - 脚本失败后自动恢复并重试
```

- [ ] **Step 4: 回写 spec 中的实现状态说明**

```markdown
## 实现状态

- 统一录制入口：已完成
- 录制态验收：已完成
- 恢复 Agent：已完成
- 统一技能导出：已完成
```

- [ ] **Step 5: Commit**

```bash
git add docs/test-report-rpa-ai-assistant-unified-runtime.md docs/superpowers/specs/2026-04-11-rpa-ai-assistant-unified-runtime-design.md
git commit -m "chore: document unified rpa assistant rollout"
```
