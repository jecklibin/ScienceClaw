# RPA Control-Flow Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-version control-flow recording support for conditional, polling, and delayed RPA tasks while preserving ordinary structured Playwright steps.

**Architecture:** Add a small backend control-flow helper module, teach the assistant to choose code mode for runtime logic, store upgraded script steps with structured diagnostics, and strengthen generator/export handling for full `async def run(page)` script steps. The frontend displays upgraded steps as advanced script steps without introducing a full visual workflow DSL.

**Tech Stack:** Python 3.13, FastAPI backend modules, Playwright async API, unittest/pytest, Vue 3 + TypeScript.

---

## File Structure

- Create: `RpaClaw/backend/rpa/control_flow.py`
  - Owns control-flow keyword detection, upgrade reason normalization, `ai_script` step construction, and normalized script function storage.
- Modify: `RpaClaw/backend/rpa/assistant.py`
  - Updates prompts, execution-mode routing, code-mode step persistence, and snapshot text emitted to the LLM.
- Modify: `RpaClaw/backend/rpa/generator.py`
  - Embeds full `async def run(page)` script steps by calling them safely and merging returned dictionaries into `_results`.
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`
  - Displays advanced script diagnostics in the configure step list.
- Modify: `RpaClaw/frontend/src/pages/rpa/TestPage.vue`
  - Displays advanced script diagnostics and control-flow failure hints in the test step list.
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
  - Adds tests for upgrade detection, code-mode routing, diagnostics, and richer snapshot observations.
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`
  - Adds tests for full-function `ai_script` embedding and `_results` merge.

Do not alter existing unrelated dirty files. Stage and commit only files listed in each task.

---

### Task 1: Control-Flow Helper Module

**Files:**
- Create: `RpaClaw/backend/rpa/control_flow.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Write failing tests for control-flow detection and script step construction**

Append these tests near the existing `RPAReActAgentTests` class in `RpaClaw/backend/tests/test_rpa_assistant.py`:

```python
class RPAControlFlowHelperTests(unittest.TestCase):
    def test_detect_upgrade_reason_identifies_polling_loop(self):
        from backend.rpa.control_flow import detect_upgrade_reason

        text = "如果第一个条目状态不是完成，就每隔500毫秒点击刷新，直到状态变为完成"

        self.assertEqual(detect_upgrade_reason(text), "polling_loop")

    def test_detect_upgrade_reason_keeps_atomic_click_structured(self):
        from backend.rpa.control_flow import detect_upgrade_reason

        text = "点击列表中的第一个条目"

        self.assertEqual(detect_upgrade_reason(text), "none")

    def test_build_ai_script_step_stores_normalized_function_and_diagnostics(self):
        from backend.rpa.control_flow import build_ai_script_step

        parsed = {
            "execution_mode": "code",
            "upgrade_reason": "polling_loop",
            "template": "poll_until_text_then_download",
            "interval_ms": 500,
            "timeout_ms": 60000,
        }
        step = build_ai_script_step(
            prompt="如果状态不是完成就刷新直到完成并下载",
            description="等待完成后下载",
            code='await page.get_by_role("button", name="刷新").click()',
            parsed=parsed,
        )

        self.assertEqual(step["action"], "ai_script")
        self.assertEqual(step["source"], "ai")
        self.assertTrue(step["value"].startswith("async def run(page):"))
        self.assertIn('await page.get_by_role("button", name="刷新").click()', step["value"])
        self.assertEqual(step["assistant_diagnostics"]["execution_mode"], "code")
        self.assertEqual(step["assistant_diagnostics"]["upgrade_reason"], "polling_loop")
        self.assertEqual(step["assistant_diagnostics"]["template"], "poll_until_text_then_download")
        self.assertEqual(step["assistant_diagnostics"]["interval_ms"], 500)
        self.assertEqual(step["assistant_diagnostics"]["timeout_ms"], 60000)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py::RPAControlFlowHelperTests -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.control_flow'`.

- [ ] **Step 3: Create `control_flow.py` with detection and step construction**

Create `RpaClaw/backend/rpa/control_flow.py`:

```python
import re
from typing import Any, Dict


POLLING_PATTERNS = ("until", "wait until", "repeat", "retry", "poll", "every", "直到", "每隔", "重复", "轮询")
CONDITIONAL_PATTERNS = (" if ", " else ", "unless", "otherwise", "如果", "否则", "不然")
COMPARISON_PATTERNS = ("highest", "lowest", "latest", "earliest", "most", "least", "最高", "最低", "最新", "最早", "最多", "最少")
DEFAULT_CONTROL_FLOW_TEMPLATE = "custom_playwright"


def detect_upgrade_reason(text: str) -> str:
    normalized = f" {str(text or '').strip().lower()} "
    if any(pattern in normalized for pattern in POLLING_PATTERNS):
        return "polling_loop"
    if re.search(r"\bevery\s+\d+\s*(ms|millisecond|milliseconds|s|sec|second|seconds)\b", normalized):
        return "polling_loop"
    if any(pattern in normalized for pattern in CONDITIONAL_PATTERNS):
        return "conditional_branch"
    if any(pattern in normalized for pattern in COMPARISON_PATTERNS):
        return "dynamic_selection"
    return "none"


def normalize_ai_script_function(code: str) -> str:
    stripped = str(code or "").strip()
    if not stripped:
        return "async def run(page):\n    return None"
    if stripped.startswith("async def run(") or stripped.startswith("def run("):
        return stripped
    indented = "\n".join(f"    {line}" if line.strip() else "" for line in stripped.splitlines())
    return f"async def run(page):\n{indented}"


def build_ai_script_step(*, prompt: str, description: str, code: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    upgrade_reason = str(parsed.get("upgrade_reason") or "").strip() or detect_upgrade_reason(
        " ".join([prompt or "", description or "", str(parsed.get("thought") or "")])
    )
    if upgrade_reason == "none":
        upgrade_reason = "custom_logic"

    diagnostics = {
        "execution_mode": "code",
        "upgrade_reason": upgrade_reason,
        "template": str(parsed.get("template") or DEFAULT_CONTROL_FLOW_TEMPLATE),
    }
    for key in ("interval_ms", "timeout_ms", "condition", "locators"):
        if key in parsed:
            diagnostics[key] = parsed[key]

    return {
        "action": "ai_script",
        "source": "ai",
        "value": normalize_ai_script_function(code),
        "description": description,
        "prompt": prompt,
        "assistant_diagnostics": diagnostics,
    }
```

- [ ] **Step 4: Run helper tests and verify they pass**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py::RPAControlFlowHelperTests -v
```

Expected: PASS for all three tests.

- [ ] **Step 5: Commit Task 1**

Run:

```powershell
git add RpaClaw/backend/rpa/control_flow.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: add rpa control flow helpers"
```

---

### Task 2: Assistant Execution-Mode Routing And Diagnostics

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Write failing tests for code mode and control-flow diagnostics**

Append these tests to `RPAReActAgentTests` in `RpaClaw/backend/tests/test_rpa_assistant.py`:

```python
    async def test_react_agent_code_execution_mode_does_not_become_structured_intent(self):
        parsed = {
            "action": "execute",
            "execution_mode": "code",
            "operation": "click",
            "description": "等待完成后下载",
            "code": "async def run(page):\n    return {'ok': True}",
            "upgrade_reason": "polling_loop",
        }

        intent = ASSISTANT_MODULE.RPAReActAgent._extract_structured_execute_intent(parsed, "等待完成后下载")

        self.assertIsNone(intent)

    async def test_react_agent_commits_code_mode_step_with_control_flow_diagnostics(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        responses = [
            json.dumps(
                {
                    "thought": "This needs polling page state.",
                    "action": "execute",
                    "execution_mode": "code",
                    "operation": "custom",
                    "description": "等待第一条完成后下载",
                    "upgrade_reason": "polling_loop",
                    "template": "poll_until_text_then_download",
                    "interval_ms": 500,
                    "timeout_ms": 60000,
                    "code": "async def run(page):\n    return {'download_filename': 'report.xlsx'}",
                    "risk": "none",
                    "risk_reason": "",
                },
                ensure_ascii=False,
            ),
            json.dumps({"thought": "done", "action": "done", "description": "done"}, ensure_ascii=False),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=_FakePage(),
                goal="如果第一条不是完成就每隔500毫秒刷新直到完成并下载",
                existing_steps=[],
            ):
                events.append(event)

        step_done = next(event for event in events if event["event"] == "agent_step_done")
        step = step_done["data"]["step"]
        self.assertEqual(step["action"], "ai_script")
        self.assertTrue(step["value"].startswith("async def run(page):"))
        self.assertEqual(step["assistant_diagnostics"]["execution_mode"], "code")
        self.assertEqual(step["assistant_diagnostics"]["upgrade_reason"], "polling_loop")
        self.assertEqual(step["assistant_diagnostics"]["template"], "poll_until_text_then_download")
        self.assertEqual(step["assistant_diagnostics"]["interval_ms"], 500)
```

- [ ] **Step 2: Run the assistant tests and verify they fail**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py::RPAReActAgentTests::test_react_agent_code_execution_mode_does_not_become_structured_intent tests/test_rpa_assistant.py::RPAReActAgentTests::test_react_agent_commits_code_mode_step_with_control_flow_diagnostics -v
```

Expected: at least one FAIL because `_extract_structured_execute_intent()` currently ignores `execution_mode`, and successful code-mode fallback stores `value` without structured diagnostics.

- [ ] **Step 3: Import control-flow helpers and update prompts**

In `RpaClaw/backend/rpa/assistant.py`, update imports near the existing `assistant_runtime` imports:

```python
from backend.rpa.control_flow import (
    build_ai_script_step,
    detect_upgrade_reason,
)
```

Update `REACT_SYSTEM_PROMPT` preferred format to include:

```text
  "execution_mode": "structured|code",
  "upgrade_reason": "none|polling_loop|conditional_branch|dynamic_selection|custom_logic",
  "template": "poll_until_text_then_download|custom_playwright",
  "code": "async def run(page): ...",
```

Add these rules after the existing rule 10:

```text
11. Set execution_mode=structured for one atomic browser action.
12. Set execution_mode=code when the subtask requires if/else logic, polling, retry loops, explicit wait-until behavior, runtime comparison, or dynamic selection based on page text.
13. For code mode, output a complete async def run(page): function using Playwright async APIs.
14. For polling loops, include upgrade_reason=polling_loop, interval_ms, timeout_ms, and template=poll_until_text_then_download when the task waits for text then downloads.
15. Code mode should solve only the current runtime-dependent subtask, not the entire workflow.
```

Update the non-ReAct `SYSTEM_PROMPT` rules with:

```text
8. If the user asks for conditions, loops, polling, wait-until behavior, or runtime comparisons, output Python code instead of a structured atomic action.
```

- [ ] **Step 4: Update structured intent extraction**

At the start of `RPAReActAgent._extract_structured_execute_intent()`, insert:

```python
        execution_mode = str(parsed.get("execution_mode", "") or "").strip().lower()
        if execution_mode == "code":
            return None

        control_flow_text = " ".join(
            str(parsed.get(key) or "")
            for key in ("thought", "description", "prompt", "operation", "value")
        )
        if parsed.get("code") and detect_upgrade_reason(f"{prompt} {control_flow_text}") != "none":
            return None
```

Keep the rest of the method unchanged.

- [ ] **Step 5: Store code-mode steps through `build_ai_script_step()`**

In `RPAReActAgent.run()`, replace the fallback `step_data` block after successful execution:

```python
                step_data = result.get("step") or {
                    "action": "ai_script",
                    "source": "ai",
                    "value": code,
                    "description": description,
                    "prompt": goal,
                }
```

with:

```python
                step_data = result.get("step") or build_ai_script_step(
                    prompt=goal,
                    description=description,
                    code=executable,
                    parsed=parsed,
                )
```

- [ ] **Step 6: Run the targeted assistant tests**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py::RPAReActAgentTests::test_react_agent_code_execution_mode_does_not_become_structured_intent tests/test_rpa_assistant.py::RPAReActAgentTests::test_react_agent_commits_code_mode_step_with_control_flow_diagnostics -v
```

Expected: PASS for both tests.

- [ ] **Step 7: Run the full assistant test file**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

Run:

```powershell
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: route rpa control flow tasks to script mode"
```

---

### Task 3: Snapshot Observation Detail For Runtime Logic

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Write a failing test for content-node and collection-item observation**

Append this test to `RPAReActAgentTests`:

```python
    async def test_react_agent_observation_includes_content_nodes_for_control_flow(self):
        snapshot = {
            "url": "https://example.com/downloads",
            "title": "Downloads",
            "containers": [
                {
                    "container_id": "table-1",
                    "container_kind": "table",
                    "name": "下载列表",
                    "child_actionable_ids": ["act-1"],
                    "child_content_ids": ["content-1"],
                }
            ],
            "content_nodes": [
                {
                    "node_id": "content-1",
                    "container_id": "table-1",
                    "semantic_kind": "cell",
                    "text": "处理中",
                    "frame_path": [],
                }
            ],
            "actionable_nodes": [
                {
                    "node_id": "act-1",
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260418",
                    "frame_path": [],
                }
            ],
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "item_count": 2,
                            "items": [
                                {"index": 1, "name": "ContractList20260418", "role": "link"},
                                {"index": 2, "name": "ContractList20260417", "role": "link"},
                            ],
                        }
                    ],
                }
            ],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(snapshot, 0)

        self.assertIn("Content: cell", content)
        self.assertIn("处理中", content)
        self.assertIn("Actionable: link", content)
        self.assertIn("ContractList20260418", content)
        self.assertIn("Item 1: ContractList20260418", content)
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py::RPAReActAgentTests::test_react_agent_observation_includes_content_nodes_for_control_flow -v
```

Expected: FAIL because `_snapshot_frame_lines()` does not list root-level `content_nodes`, root-level `actionable_nodes`, or collection item names.

- [ ] **Step 3: Enhance `_snapshot_frame_lines()`**

In `RpaClaw/backend/rpa/assistant.py`, update `_snapshot_frame_lines()` after the container loop and before the frame loop:

```python
    for content_node in (snapshot.get("content_nodes") or [])[:30]:
        text = str(content_node.get("text") or "").strip()
        if not text:
            continue
        lines.append(
            "Content: "
            f"{content_node.get('semantic_kind', 'text')} "
            f"{text[:120]} "
            f"(container={content_node.get('container_id', '')})"
        )
    for actionable_node in (snapshot.get("actionable_nodes") or [])[:30]:
        name = str(actionable_node.get("name") or actionable_node.get("text") or "").strip()
        if not name:
            continue
        lines.append(
            "Actionable: "
            f"{actionable_node.get('role', actionable_node.get('tag', 'element'))} "
            f"{name[:120]} "
            f"(container={actionable_node.get('container_id', '')})"
        )
```

Inside the existing collection loop in `_snapshot_frame_lines()`, after the `Collection:` line, add:

```python
            for item in (collection.get("items") or [])[:5]:
                item_name = str(item.get("name") or item.get("text") or "").strip()
                if item_name:
                    lines.append(f"    Item {item.get('index', '?')}: {item_name[:120]}")
```

- [ ] **Step 4: Run the targeted observation test**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py::RPAReActAgentTests::test_react_agent_observation_includes_content_nodes_for_control_flow -v
```

Expected: PASS.

- [ ] **Step 5: Run assistant tests**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

Run:

```powershell
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: expose rpa snapshot content for control flow"
```

---

### Task 4: Generator Support For Full Function `ai_script` Steps

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Write failing generator tests**

Append these tests to `PlaywrightGeneratorTests` in `RpaClaw/backend/tests/test_rpa_generator.py`:

```python
    def test_generate_script_calls_full_ai_script_function_and_merges_results(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "ai_script",
                "source": "ai",
                "description": "Wait for completion and download",
                "value": "\n".join(["async def run(page):", "    return {'download_filename': 'report.xlsx'}"]),
                "assistant_diagnostics": {
                    "execution_mode": "code",
                    "upgrade_reason": "polling_loop",
                    "template": "poll_until_text_then_download",
                },
                "url": "https://example.com",
                "tab_id": "tab-1",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn("# Advanced AI script step: polling_loop", script)
        self.assertIn("async def _rpa_ai_step_1(page):", script)
        self.assertIn("_rpa_ai_step_1_result = await _rpa_ai_step_1(current_page)", script)
        self.assertIn("if isinstance(_rpa_ai_step_1_result, dict):", script)
        self.assertIn("_results.update(_rpa_ai_step_1_result)", script)
        self.assertNotIn("async def run(page):", script)

    def test_generate_script_keeps_body_only_ai_script_compatibility(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "ai_script",
                "source": "ai",
                "description": "Click refresh",
                "value": 'await page.get_by_role("button", name="Refresh").click()',
                "url": "https://example.com",
                "tab_id": "tab-1",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('await page.get_by_role("button", name="Refresh").click()', script)
```

- [ ] **Step 2: Run the generator tests and verify the full-function test fails**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_generator.py::PlaywrightGeneratorTests::test_generate_script_calls_full_ai_script_function_and_merges_results tests/test_rpa_generator.py::PlaywrightGeneratorTests::test_generate_script_keeps_body_only_ai_script_compatibility -v
```

Expected: the full-function test FAILS because the current generator embeds `async def run(page):` directly and does not call it.

- [ ] **Step 3: Add helper methods in `generator.py`**

Add these methods inside `PlaywrightGenerator` near the existing `ai_script` helper methods:

```python
    @staticmethod
    def _extract_run_function_body(code: str) -> Optional[str]:
        lines = str(code or "").splitlines()
        if not lines:
            return None
        first = lines[0].strip()
        if not (first.startswith("async def run(") or first.startswith("def run(")):
            return None
        body_lines = []
        for line in lines[1:]:
            if line.startswith("    "):
                body_lines.append(line[4:])
            elif line.startswith("\t"):
                body_lines.append(line[1:])
            elif not line.strip():
                body_lines.append("")
            else:
                body_lines.append(line)
        return "\n".join(body_lines).strip("\n")

    def _build_ai_script_step_lines(self, step: Dict[str, Any], step_index: int) -> List[str]:
        ai_code = step.get("value", "")
        diagnostics = step.get("assistant_diagnostics") or {}
        upgrade_reason = diagnostics.get("upgrade_reason")
        body = self._extract_run_function_body(ai_code)
        if body is None:
            converted = self._sync_to_async(ai_code)
            converted = self._inject_result_capture(converted)
            converted = self._strip_locator_result_capture(converted)
            return [f"    {code_line}" if code_line.strip() else "" for code_line in converted.split("\n")]

        converted = self._sync_to_async(body)
        converted = self._inject_result_capture(converted)
        converted = self._strip_locator_result_capture(converted)
        func_name = f"_rpa_ai_step_{step_index + 1}"
        lines: List[str] = []
        if upgrade_reason:
            lines.append(f"    # Advanced AI script step: {upgrade_reason}")
        lines.append(f"    async def {func_name}(page):")
        for code_line in converted.split("\n"):
            lines.append(f"        {code_line}" if code_line.strip() else "")
        result_var = f"{func_name}_result"
        lines.append(f"    {result_var} = await {func_name}(current_page)")
        lines.append(f"    if isinstance({result_var}, dict):")
        lines.append(f"        _results.update({result_var})")
        lines.append(f"    elif {result_var} is not None:")
        lines.append(f'        _results["{func_name}"] = {result_var}')
        return lines
```

- [ ] **Step 4: Use the helper in the `ai_script` branch**

In `generate_script()`, replace the existing `if action == "ai_script":` body with:

```python
                step_lines.extend(self._build_ai_script_step_lines(step, step_index))
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                continue
```

- [ ] **Step 5: Run targeted generator tests**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_generator.py::PlaywrightGeneratorTests::test_generate_script_calls_full_ai_script_function_and_merges_results tests/test_rpa_generator.py::PlaywrightGeneratorTests::test_generate_script_keeps_body_only_ai_script_compatibility -v
```

Expected: PASS.

- [ ] **Step 6: Run full generator tests**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_generator.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

Run:

```powershell
git add RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "feat: export full rpa ai script steps"
```

---

### Task 5: Configure And Test Page Advanced-Step Display

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`
- Modify: `RpaClaw/frontend/src/pages/rpa/TestPage.vue`

- [ ] **Step 1: Add TypeScript helpers in `ConfigurePage.vue`**

In the `<script setup>` section of `ConfigurePage.vue`, add these helpers near `getActionLabel()`:

```ts
const isAdvancedScriptStep = (step: StepItem): boolean => (
  step.action === 'ai_script'
  && step.assistant_diagnostics?.execution_mode === 'code'
);

const getAdvancedReasonLabel = (step: StepItem): string => {
  const reason = step.assistant_diagnostics?.upgrade_reason || '';
  const labels: Record<string, string> = {
    polling_loop: '轮询等待',
    conditional_branch: '条件分支',
    dynamic_selection: '动态选择',
    custom_logic: '自定义逻辑',
  };
  return labels[reason] || '高级脚本';
};

const getAdvancedSummary = (step: StepItem): string => {
  const diagnostics = step.assistant_diagnostics || {};
  const parts: string[] = [];
  if (diagnostics.template) parts.push(`模板 ${diagnostics.template}`);
  if (diagnostics.interval_ms) parts.push(`间隔 ${diagnostics.interval_ms}ms`);
  if (diagnostics.timeout_ms) parts.push(`超时 ${diagnostics.timeout_ms}ms`);
  return parts.join(' · ') || '运行时读取页面状态并执行 Playwright 脚本';
};
```

If `StepItem` does not allow `assistant_diagnostics`, extend it:

```ts
  assistant_diagnostics?: Record<string, any>;
```

- [ ] **Step 2: Render advanced-step diagnostics in `ConfigurePage.vue`**

Inside the expanded step details block, above the primary locator row, add:

```vue
                    <div
                      v-if="isAdvancedScriptStep(step)"
                      class="rounded-2xl border border-purple-100 bg-purple-50/70 p-3 text-xs text-purple-800 dark:border-purple-900/50 dark:bg-purple-950/20 dark:text-purple-200"
                    >
                      <div class="flex flex-wrap items-center gap-2">
                        <span class="rounded-full bg-purple-600 px-2 py-0.5 text-[10px] font-bold text-white">
                          {{ getAdvancedReasonLabel(step) }}
                        </span>
                        <span>{{ getAdvancedSummary(step) }}</span>
                      </div>
                      <pre
                        v-if="step.value"
                        class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-white/80 p-3 font-mono text-[11px] text-gray-700 dark:bg-black/20 dark:text-gray-200"
                      >{{ step.value }}</pre>
                    </div>
```

- [ ] **Step 3: Add helpers in `TestPage.vue`**

In `TestPage.vue`, add equivalent helper functions near the existing formatter helpers:

```ts
const isAdvancedScriptStep = (step: any): boolean => (
  step?.action === 'ai_script'
  && step?.assistant_diagnostics?.execution_mode === 'code'
);

const getAdvancedReasonLabel = (step: any): string => {
  const labels: Record<string, string> = {
    polling_loop: '轮询等待',
    conditional_branch: '条件分支',
    dynamic_selection: '动态选择',
    custom_logic: '自定义逻辑',
  };
  return labels[step?.assistant_diagnostics?.upgrade_reason || ''] || '高级脚本';
};

const getAdvancedSummary = (step: any): string => {
  const diagnostics = step?.assistant_diagnostics || {};
  const parts: string[] = [];
  if (diagnostics.template) parts.push(`模板 ${diagnostics.template}`);
  if (diagnostics.interval_ms) parts.push(`间隔 ${diagnostics.interval_ms}ms`);
  if (diagnostics.timeout_ms) parts.push(`超时 ${diagnostics.timeout_ms}ms`);
  return parts.join(' · ') || '运行时逻辑步骤';
};
```

- [ ] **Step 4: Render advanced-step diagnostics in `TestPage.vue`**

In the recorded step card, after the description heading and before the locator paragraph, add:

```vue
            <div
              v-if="isAdvancedScriptStep(step)"
              class="mt-2 rounded-lg border border-purple-100 bg-purple-50 p-2 text-[11px] text-purple-800 dark:border-purple-900/50 dark:bg-purple-950/20 dark:text-purple-200"
            >
              <span class="font-semibold">{{ getAdvancedReasonLabel(step) }}:</span>
              <span class="ml-1">{{ getAdvancedSummary(step) }}</span>
            </div>
```

When `failedStepError` contains a control-flow error, keep the existing red failure card. The backend task leaves `condition_timeout`, `download_not_triggered`, and raw Playwright messages in `failedStepError`, so the current failure display is enough for first version.

- [ ] **Step 5: Run frontend type check**

Run:

```powershell
cd RpaClaw\frontend
npm run type-check
```

Expected: PASS.

- [ ] **Step 6: Build frontend**

Run:

```powershell
cd RpaClaw\frontend
npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

Run:

```powershell
git add RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue RpaClaw/frontend/src/pages/rpa/TestPage.vue
git commit -m "feat: show rpa advanced script steps"
```

---

### Task 6: End-To-End Regression Coverage And Final Verification

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Add an assistant regression for the motivating scenario prompt**

Add this test to `RPAControlFlowHelperTests`:

```python
    def test_motivating_prompt_is_classified_as_polling_loop(self):
        from backend.rpa.control_flow import detect_upgrade_reason

        prompt = "如果列表中的一个条目状态不是完成状态，就每隔500毫秒点击下刷新，直到状态变为完成，然后点击下第一个条目的名称进行下载"

        self.assertEqual(detect_upgrade_reason(prompt), "polling_loop")
```

- [ ] **Step 2: Add a generator regression for control-flow timeout code preservation**

Add this test to `PlaywrightGeneratorTests`:

```python
    def test_generate_script_preserves_polling_timeout_and_download_wrapper_in_ai_script(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "ai_script",
                "source": "ai",
                "description": "Wait until first item completes and download",
                "value": "\n".join(
                    [
                        "async def run(page):",
                        "    interval_ms = 500",
                        "    timeout_ms = 60000",
                        "    elapsed_ms = 0",
                        "    while elapsed_ms <= timeout_ms:",
                        "        status_text = '完成'",
                        "        if '完成' in status_text:",
                        "            break",
                        "        await page.get_by_role('button', name='刷新').click()",
                        "        await page.wait_for_timeout(interval_ms)",
                        "        elapsed_ms += interval_ms",
                        "    else:",
                        "        raise TimeoutError('condition_timeout')",
                        "    async with page.expect_download() as download_info:",
                        "        await page.get_by_role('link').first.click()",
                        "    download = await download_info.value",
                        "    return {'download_filename': download.suggested_filename}",
                    ]
                ),
                "assistant_diagnostics": {
                    "execution_mode": "code",
                    "upgrade_reason": "polling_loop",
                    "template": "poll_until_text_then_download",
                },
                "url": "https://example.com",
                "tab_id": "tab-1",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn("interval_ms = 500", script)
        self.assertIn("timeout_ms = 60000", script)
        self.assertIn("raise TimeoutError('condition_timeout')", script)
        self.assertIn("async with page.expect_download() as download_info:", script)
        self.assertIn("_results.update(_rpa_ai_step_1_result)", script)
```

- [ ] **Step 3: Run backend regression tests**

Run:

```powershell
cd RpaClaw\backend
uv run pytest tests/test_rpa_assistant.py tests/test_rpa_generator.py -v
```

Expected: PASS.

- [ ] **Step 4: Run frontend checks**

Run:

```powershell
cd RpaClaw\frontend
npm run type-check
npm run build
```

Expected: both commands PASS.

- [ ] **Step 5: Inspect changed files**

Run:

```powershell
git status --short
git diff --stat
```

Expected: only files from this implementation plan appear as changed, plus any pre-existing unrelated dirty files that were present before implementation.

- [ ] **Step 6: Commit final regression coverage**

Run:

```powershell
git add RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "test: cover rpa control flow recording"
```

---

## Self-Review

Spec coverage:

- Control-flow trigger rules are covered by Task 1 and Task 6.
- Assistant execution-mode routing and `ai_script` diagnostics are covered by Task 2.
- Snapshot detail for status/list reasoning is covered by Task 3.
- Full function export and result merging are covered by Task 4.
- Configure and test page display for advanced steps is covered by Task 5.
- Motivating polling/download scenario is covered by Task 6.

Placeholder scan:

- The plan contains no unfinished marker words, incomplete file names, or unspecified test commands.

Type consistency:

- `assistant_diagnostics`, `execution_mode`, `upgrade_reason`, `template`, `interval_ms`, and `timeout_ms` use the same names in backend tests, assistant persistence, generator behavior, and frontend display.

Scope check:

- This plan implements the first hybrid advanced-step version. It does not introduce a full visual workflow DSL, route-level locator replacement for nested advanced locators, or a generic workflow graph. Those remain separate future projects.
