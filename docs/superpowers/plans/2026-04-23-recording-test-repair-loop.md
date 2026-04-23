# Recording Test Repair Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make conversational recording script generation and full workflow testing validate declared contracts, fail fast on missing outputs, and support repairing test workspace changes back into the recording run before publish.

**Architecture:** Tighten the workflow-test success criteria around declared segment outputs and artifacts, then expose a repair-oriented recording test workspace that the `recording-creator` skill can inspect, edit, sync back, and retest. Keep the implementation generic: the backend provides contract-aware execution metadata and sync primitives, while the built-in skill drives the write-execute-fix-retest loop.

**Tech Stack:** Python 3.13, FastAPI backend helpers, Pydantic models, DeepAgent built-in tools, pytest

---

### Task 1: Lock the contract-validation regression with failing tests

**Files:**
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_testing.py`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py`

- [ ] **Step 1: Write the failing workflow-test contract regression test**

```python
@pytest.mark.anyio
async def test_execute_workflow_test_fails_when_declared_output_is_missing():
    ...
    assert result["success"] is False
    assert result["contract"]["success"] is False
    assert result["contract"]["segment_results"][0]["missing_outputs"] == ["markdown_text"]
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_testing.py::test_execute_workflow_test_fails_when_declared_output_is_missing -q`
Expected: FAIL because `execute_workflow_test()` still reports success when the process exits cleanly but the declared output is absent.

- [ ] **Step 3: Write the failing recording tool test for repair context + failed status**

```python
def test_begin_recording_test_tool_marks_missing_contract_output_as_needs_repair():
    ...
    assert payload["run"]["status"] == "needs_repair"
    assert payload["summary"]["repair_context"]["missing_outputs"] == ["markdown_text"]
```

- [ ] **Step 4: Run the focused orchestrator test to verify it fails**

Run: `pytest D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py::test_begin_recording_test_tool_marks_missing_contract_output_as_needs_repair -q`
Expected: FAIL because the tool still promotes the run to `ready_to_publish`.

- [ ] **Step 5: Write the failing repair-sync test**

```python
def test_apply_recording_test_repairs_syncs_script_segment_back_to_run(tmp_path):
    ...
    assert repaired_segment.exports["script"] == updated_source
    assert repaired_segment.exports["outputs"][0]["name"] == "markdown_text"
```

- [ ] **Step 6: Run the focused repair-sync test to verify it fails**

Run: `pytest D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py::test_apply_recording_test_repairs_syncs_script_segment_back_to_run -q`
Expected: FAIL because no repair-sync tool exists yet.

### Task 2: Tighten workflow test success around segment contracts

**Files:**
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/recording/testing.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_testing.py`

- [ ] **Step 1: Implement contract evaluation helpers**

```python
def validate_workflow_contract(workflow: dict[str, Any], result_payload: dict[str, Any]) -> dict[str, Any]:
    ...
    return {
        "success": overall_success,
        "segment_results": segment_results,
        "missing_outputs": aggregate_missing_outputs,
        "missing_artifacts": aggregate_missing_artifacts,
    }
```

- [ ] **Step 2: Merge contract validation into `execute_workflow_test()`**

```python
workflow = _load_workflow_json_if_present(skill_dir)
contract = validate_workflow_contract(workflow, output_payload or {})
success = completed.returncode == 0 and "SKILL_SUCCESS" in stdout and contract["success"]
```

- [ ] **Step 3: Return command, contract, and raw result details**

```python
return {
    "success": success,
    "command": command,
    "contract": contract,
    ...
}
```

- [ ] **Step 4: Run the focused recording-testing tests**

Run: `pytest D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_testing.py -q`
Expected: PASS

### Task 3: Build repair context and sync repaired files back into the recording run

**Files:**
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/recording/testing.py`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/deepagent/tools.py`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/recording/orchestrator.py`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/workflow/recording_adapter.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py`

- [ ] **Step 1: Write a repair-context file into the workflow test workspace**

```python
repair_context = build_recording_repair_context(run, workflow_run, write_result, execution_result)
(skill_dir / "recording_test_context.json").write_text(...)
```

- [ ] **Step 2: Persist per-segment testing results back onto the recording run**

```python
recording_orchestrator.apply_test_results(run, execution_result["contract"]["segment_results"])
```

- [ ] **Step 3: Add a session-bound tool to sync repaired workflow files back into the run**

```python
@tool
def apply_recording_test_repairs(run_id: str = "", skill_dir: str = "") -> str:
    ...
```

- [ ] **Step 4: Make `begin_recording_test` expose repair context in its response**

```python
"summary": {
    ...
    "repair_context": payload["execution"]["repair_context"],
}
```

- [ ] **Step 5: Run the focused orchestrator tests**

Run: `pytest D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py -q`
Expected: PASS

### Task 4: Update `recording-creator` to use the same repair loop mindset as normal skill execution

**Files:**
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/builtin_skills/recording-creator/SKILL.md`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py`
- Modify: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/docs/superpowers/specs/2026-04-21-workflow-segment-creator-design.md`

- [ ] **Step 1: Add repair-loop instructions to the skill**

```md
- 完整测试失败后，不要停在总结；读取 repair context、检查脚本文件、必要时编辑测试工作区文件并调用 `apply_recording_test_repairs`，然后重新测试。
- 输出缺失也算测试失败，必须修复契约或脚本后再重测。
```

- [ ] **Step 2: Keep the wording implementation-focused, not history-focused**

```md
- 不要提“旧式兼容”或历史实现细节。
- 只描述 agent 应如何利用测试工作区、输入输出契约与真实 artifact 完成修复。
```

- [ ] **Step 3: Run the focused skill/orchestrator assertions**

Run: `pytest D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py::test_recording_creator_skill_stays_execution_focused -q`
Expected: PASS

### Task 5: Run end-to-end verification for the touched backend areas

**Files:**
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_testing.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_workflow_publishing.py`
- Test: `D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_sessions.py`

- [ ] **Step 1: Run the backend regression suite**

Run: `pytest D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_testing.py D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_recording_orchestrator.py D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_workflow_publishing.py D:/code/MyScienceClaw/.worktrees/codex-conversational-recording/RpaClaw/backend/tests/test_sessions.py -q`
Expected: PASS

- [ ] **Step 2: Review that recording test pass/fail semantics now match the design**

```text
- Missing declared outputs => full test fails and run enters needs_repair.
- Repair context points to the real workflow test workspace.
- Recording-creator can edit that workspace, sync fixes back, and retest before publish.
```
