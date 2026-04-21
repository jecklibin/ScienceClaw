# Workflow Segment Creator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a publishable multi-segment workflow creator where RPA recording, generated scripts, MCP calls, and LLM processing are unified as workflow segments and saved as complete skills.

**Architecture:** Add a focused `backend.workflow` domain that owns segment models, publish drafts, workflow metadata, and artifact generation. Keep existing RPA recording routes working by adapting `RecordingRun` into `WorkflowRun`, then update frontend chat UI to configure segment metadata separately from final skill publish metadata.

**Tech Stack:** FastAPI, Pydantic v2, pytest, Vue 3, TypeScript, Vitest, existing RPA recorder/configure/test pages.

---

## Scope

This plan implements the first shippable slice of the approved design:

- Multi-type workflow segment data model.
- RPA and script segment publish support.
- Complete skill artifact generation: `SKILL.md`, `skill.py`, `workflow.json`, `params.schema.json`, `credentials.example.json`, and `segments/`.
- Publish draft API and frontend publish modal.
- Current RPA recording compatibility through an adapter.

MCP and LLM segment execution adapters are modeled but only published as metadata in this slice. Their runtime execution can be added after the RPA + script path is stable.

## File Map

### Backend

- Create `RpaClaw/backend/workflow/__init__.py`: package exports.
- Create `RpaClaw/backend/workflow/models.py`: Pydantic workflow, segment, publish draft, parameter, credential, and artifact models.
- Create `RpaClaw/backend/workflow/recording_adapter.py`: convert existing `RecordingRun` and `RecordingSegment` objects to workflow models.
- Create `RpaClaw/backend/workflow/publishing.py`: build publish drafts and write final skill artifacts.
- Create `RpaClaw/backend/workflow/runner_template.py`: source template for generated `skill.py`.
- Modify `RpaClaw/backend/recording/publishing.py`: compatibility wrapper delegating to workflow publishing.
- Modify `RpaClaw/backend/route/sessions.py`: add publish draft endpoint and accept final draft on publish.
- Modify `RpaClaw/backend/deepagent/tools.py`: route `prepare_recording_publish` through publish draft aware publishing.
- Add `RpaClaw/backend/tests/test_workflow_models.py`: model validation tests.
- Add `RpaClaw/backend/tests/test_workflow_publishing.py`: artifact generation tests.
- Modify `RpaClaw/backend/tests/test_recording_publishing.py`: compatibility tests for existing recording publishing entrypoint.

### Frontend

- Modify `RpaClaw/frontend/src/types/recording.ts`: add workflow segment, publish draft, warnings, inputs, outputs, and credentials types.
- Modify `RpaClaw/frontend/src/api/recording.ts`: add `prepareRecordingPublishDraft` and change `publishRecordingRun` to submit a final draft.
- Create `RpaClaw/frontend/src/components/RecordingPublishDraftModal.vue`: final skill publish confirmation modal.
- Modify `RpaClaw/frontend/src/composables/useRecordingRun.ts`: store publish draft separately from old save prompt.
- Modify `RpaClaw/frontend/src/components/RecordingSegmentCard.vue`: support non-RPA segment kinds and default-collapsed detail view with inputs and outputs.
- Modify `RpaClaw/frontend/src/pages/ChatPage.vue`: open publish modal after prepare publish, not the old save prompt.
- Modify `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`: rename skill fields to segment title and segment purpose.
- Modify `RpaClaw/frontend/src/pages/rpa/TestPage.vue`: send segment metadata names and continue returning to chat after segment completion.
- Add `RpaClaw/frontend/src/components/__tests__/RecordingPublishDraftModal.spec.ts`: modal behavior tests.
- Modify `RpaClaw/frontend/src/components/__tests__/RecordingSegmentCard.spec.ts`: multi-kind and collapsed card tests.
- Modify `RpaClaw/frontend/src/components/__tests__/useRecordingRun.spec.ts`: publish draft store tests.

---

## Task 1: Backend Workflow Models

**Files:**
- Create: `RpaClaw/backend/workflow/__init__.py`
- Create: `RpaClaw/backend/workflow/models.py`
- Test: `RpaClaw/backend/tests/test_workflow_models.py`

- [ ] **Step 1: Write failing model tests**

Create `RpaClaw/backend/tests/test_workflow_models.py`:

```python
from backend.workflow.models import (
    CredentialRequirement,
    PublishInput,
    SkillPublishDraft,
    WorkflowRun,
    WorkflowSegment,
)


def test_workflow_segment_accepts_script_artifact_input():
    segment = WorkflowSegment(
        id="segment_2",
        run_id="run_1",
        kind="script",
        order=2,
        title="转换下载文件",
        purpose="将第一段下载的文件转换为 CSV",
        inputs=[
            {
                "name": "source_file",
                "type": "file",
                "required": True,
                "source": "segment_output",
                "source_ref": "segment_1.outputs.downloaded_file",
                "description": "第一段下载得到的文件",
            }
        ],
        outputs=[
            {
                "name": "converted_csv",
                "type": "file",
                "description": "转换后的 CSV 文件",
            }
        ],
        config={
            "language": "python",
            "entry": "segments/segment_2_transform.py",
        },
    )

    assert segment.kind == "script"
    assert segment.inputs[0].source_ref == "segment_1.outputs.downloaded_file"
    assert segment.config["entry"] == "segments/segment_2_transform.py"


def test_publish_draft_requires_final_skill_name_and_description():
    draft = SkillPublishDraft(
        id="draft_1",
        run_id="run_1",
        publish_target="skill",
        skill_name="download_and_convert_report",
        display_title="下载并转换业务报表",
        description="自动下载业务报表并转换为 CSV。",
        trigger_examples=["帮我下载并转换业务报表"],
        inputs=[
            PublishInput(
                name="report_date",
                type="string",
                required=False,
                description="报表日期",
            )
        ],
        outputs=[],
        credentials=[
            CredentialRequirement(
                name="business_system",
                type="browser_session",
                description="业务系统登录态",
            )
        ],
        segments=[],
        warnings=[],
    )

    assert draft.skill_name == "download_and_convert_report"
    assert draft.description.startswith("自动下载")
    assert draft.credentials[0].type == "browser_session"


def test_workflow_run_keeps_segments_in_order():
    run = WorkflowRun(
        id="run_1",
        session_id="session_1",
        user_id="user_1",
        intent="下载并转换文件",
        segments=[
            WorkflowSegment(
                id="segment_1",
                run_id="run_1",
                kind="rpa",
                order=1,
                title="下载文件",
                purpose="从网页下载源文件",
            ),
            WorkflowSegment(
                id="segment_2",
                run_id="run_1",
                kind="script",
                order=2,
                title="转换文件",
                purpose="将下载文件转换为 CSV",
            ),
        ],
    )

    assert [segment.id for segment in run.ordered_segments()] == ["segment_1", "segment_2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_workflow_models.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'backend.workflow'`.

- [ ] **Step 3: Create workflow package exports**

Create `RpaClaw/backend/workflow/__init__.py`:

```python
"""Workflow segment domain for conversational workflow creation."""

from .models import (
    ArtifactRef,
    CredentialRequirement,
    PublishInput,
    PublishOutput,
    PublishSegmentSummary,
    PublishWarning,
    SegmentInput,
    SegmentOutput,
    SegmentTestResult,
    SkillPublishDraft,
    WorkflowRun,
    WorkflowSegment,
)

__all__ = [
    "ArtifactRef",
    "CredentialRequirement",
    "PublishInput",
    "PublishOutput",
    "PublishSegmentSummary",
    "PublishWarning",
    "SegmentInput",
    "SegmentOutput",
    "SegmentTestResult",
    "SkillPublishDraft",
    "WorkflowRun",
    "WorkflowSegment",
]
```

- [ ] **Step 4: Implement workflow models**

Create `RpaClaw/backend/workflow/models.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

WorkflowSegmentKind = Literal["rpa", "script", "mcp", "llm", "mixed"]
WorkflowRunStatus = Literal[
    "draft",
    "recording",
    "configuring",
    "testing",
    "ready_to_publish",
    "published",
    "failed",
]
WorkflowSegmentStatus = Literal["draft", "configured", "testing", "tested", "failed"]
ValueType = Literal["string", "number", "boolean", "file", "json", "secret"]


class ArtifactRef(BaseModel):
    id: str
    name: str
    type: Literal["file", "text", "json", "table"]
    path: Optional[str] = None
    value: Optional[Any] = None
    mime_type: Optional[str] = None
    labels: list[str] = Field(default_factory=list)
    producer_segment_id: Optional[str] = None


class SegmentInput(BaseModel):
    name: str
    type: ValueType
    required: bool = True
    source: Literal["user", "workflow_param", "segment_output", "artifact", "credential"] = "user"
    source_ref: Optional[str] = None
    description: str = ""
    default: Optional[Any] = None


class SegmentOutput(BaseModel):
    name: str
    type: Literal["string", "number", "boolean", "file", "json"]
    description: str = ""
    artifact_ref: Optional[str] = None


class SegmentTestResult(BaseModel):
    status: Literal["idle", "running", "passed", "failed"] = "idle"
    error: Optional[str] = None
    logs: list[str] = Field(default_factory=list)


class WorkflowSegment(BaseModel):
    id: str
    run_id: str
    kind: WorkflowSegmentKind
    order: int
    title: str
    purpose: str
    status: WorkflowSegmentStatus = "draft"
    inputs: list[SegmentInput] = Field(default_factory=list)
    outputs: list[SegmentOutput] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    test_result: SegmentTestResult = Field(default_factory=SegmentTestResult)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class WorkflowRun(BaseModel):
    id: str
    session_id: str
    user_id: str
    intent: str
    status: WorkflowRunStatus = "draft"
    segments: list[WorkflowSegment] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def ordered_segments(self) -> list[WorkflowSegment]:
        return sorted(self.segments, key=lambda segment: segment.order)


class PublishInput(BaseModel):
    name: str
    type: ValueType
    required: bool = True
    description: str = ""
    default: Optional[Any] = None


class PublishOutput(BaseModel):
    name: str
    type: Literal["string", "number", "boolean", "file", "json"]
    description: str = ""


class CredentialRequirement(BaseModel):
    name: str
    type: Literal["browser_session", "api_key", "username_password", "oauth", "secret"]
    description: str


class PublishSegmentSummary(BaseModel):
    id: str
    kind: WorkflowSegmentKind
    title: str
    purpose: str
    status: WorkflowSegmentStatus
    input_count: int = 0
    output_count: int = 0


class PublishWarning(BaseModel):
    code: str
    message: str
    segment_id: Optional[str] = None


class SkillPublishDraft(BaseModel):
    id: str
    run_id: str
    publish_target: Literal["skill", "tool", "mcp"] = "skill"
    skill_name: str
    display_title: str
    description: str
    trigger_examples: list[str] = Field(default_factory=list)
    inputs: list[PublishInput] = Field(default_factory=list)
    outputs: list[PublishOutput] = Field(default_factory=list)
    credentials: list[CredentialRequirement] = Field(default_factory=list)
    segments: list[PublishSegmentSummary] = Field(default_factory=list)
    warnings: list[PublishWarning] = Field(default_factory=list)
```

- [ ] **Step 5: Run model tests**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_workflow_models.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/workflow/__init__.py RpaClaw/backend/workflow/models.py RpaClaw/backend/tests/test_workflow_models.py
git commit -m "feat: add workflow segment models"
```

---

## Task 2: Workflow Publishing Artifacts

**Files:**
- Create: `RpaClaw/backend/workflow/runner_template.py`
- Create: `RpaClaw/backend/workflow/publishing.py`
- Test: `RpaClaw/backend/tests/test_workflow_publishing.py`

- [ ] **Step 1: Write failing publishing tests**

Create `RpaClaw/backend/tests/test_workflow_publishing.py`:

```python
import json
import tempfile
from pathlib import Path

from backend.workflow.models import SkillPublishDraft, WorkflowRun, WorkflowSegment
from backend.workflow.publishing import build_publish_draft, write_skill_artifacts


def _sample_run() -> WorkflowRun:
    return WorkflowRun(
        id="run_1",
        session_id="session_1",
        user_id="user_1",
        intent="下载并转换文件",
        segments=[
            WorkflowSegment(
                id="segment_1",
                run_id="run_1",
                kind="rpa",
                order=1,
                title="下载报表",
                purpose="从网页下载 Excel 报表",
                status="tested",
                outputs=[
                    {
                        "name": "downloaded_file",
                        "type": "file",
                        "description": "下载得到的 Excel 文件",
                    }
                ],
                config={
                    "steps": [
                        {"id": "step_1", "action": "goto", "target": "https://example.com"},
                        {"id": "step_2", "action": "click", "target": "Download"},
                    ]
                },
            ),
            WorkflowSegment(
                id="segment_2",
                run_id="run_1",
                kind="script",
                order=2,
                title="转换报表",
                purpose="将下载文件转换为 CSV",
                status="tested",
                inputs=[
                    {
                        "name": "source_file",
                        "type": "file",
                        "required": True,
                        "source": "segment_output",
                        "source_ref": "segment_1.outputs.downloaded_file",
                        "description": "第一段下载的文件",
                    }
                ],
                outputs=[
                    {
                        "name": "converted_csv",
                        "type": "file",
                        "description": "转换后的 CSV 文件",
                    }
                ],
                config={
                    "language": "python",
                    "entry": "segments/segment_2_transform.py",
                    "source": "def run(context):\n    return {'converted_csv': 'output.csv'}\n",
                },
            ),
        ],
    )


def test_build_publish_draft_uses_workflow_metadata():
    draft = build_publish_draft(_sample_run(), publish_target="skill")

    assert draft.skill_name == "下载并转换文件"
    assert draft.display_title == "下载并转换文件"
    assert "下载报表" in draft.description
    assert [segment.id for segment in draft.segments] == ["segment_1", "segment_2"]
    assert not draft.warnings


def test_write_skill_artifacts_generates_complete_skill_directory():
    run = _sample_run()
    draft = SkillPublishDraft(
        id="draft_run_1",
        run_id="run_1",
        publish_target="skill",
        skill_name="download_and_convert_report",
        display_title="下载并转换业务报表",
        description="自动下载业务报表并转换为 CSV。",
        trigger_examples=["帮我下载并转换业务报表"],
        inputs=[],
        outputs=[],
        credentials=[],
        segments=build_publish_draft(run).segments,
        warnings=[],
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        skill_dir = Path(result["skill_dir"])

        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / "skill.py").is_file()
        assert (skill_dir / "workflow.json").is_file()
        assert (skill_dir / "params.schema.json").is_file()
        assert (skill_dir / "credentials.example.json").is_file()
        assert (skill_dir / "segments" / "segment_1_rpa.json").is_file()
        assert (skill_dir / "segments" / "segment_2_transform.py").is_file()

        skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "name: download_and_convert_report" in skill_md
        assert "自动下载业务报表并转换为 CSV。" in skill_md
        assert "下载报表" in skill_md
        assert "转换报表" in skill_md

        workflow = json.loads((skill_dir / "workflow.json").read_text(encoding="utf-8"))
        assert [segment["id"] for segment in workflow["segments"]] == ["segment_1", "segment_2"]
        assert workflow["segments"][1]["entry"] == "segments/segment_2_transform.py"

        runner = (skill_dir / "skill.py").read_text(encoding="utf-8")
        assert "def run(**kwargs):" in runner
        assert "run_rpa_segment" in runner
        assert "run_script_segment" in runner
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_workflow_publishing.py -q
```

Expected: fail with missing `backend.workflow.publishing`.

- [ ] **Step 3: Add generated runner template**

Create `RpaClaw/backend/workflow/runner_template.py`:

```python
WORKFLOW_RUNNER_TEMPLATE = '''"""Generated workflow skill runner."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent


class WorkflowContext:
    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.outputs: dict[str, dict[str, Any]] = {}

    def resolve(self, source_ref: str | None) -> Any:
        if not source_ref:
            return None
        if source_ref.startswith("params."):
            return self.params.get(source_ref.removeprefix("params."))
        if ".outputs." in source_ref:
            segment_id, output_name = source_ref.split(".outputs.", 1)
            return self.outputs.get(segment_id, {}).get(output_name)
        return None

    def store_segment_outputs(self, segment_id: str, values: dict[str, Any]) -> None:
        self.outputs[segment_id] = values

    def final_outputs(self) -> dict[str, Any]:
        return {
            "status": "success",
            "outputs": self.outputs,
        }


def load_json(name: str) -> dict[str, Any]:
    return json.loads((BASE_DIR / name).read_text(encoding="utf-8"))


def load_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    schema = load_json("params.schema.json")
    params: dict[str, Any] = {}
    for name, spec in schema.get("properties", {}).items():
        if "default" in spec:
            params[name] = spec["default"]
    params.update(kwargs)
    return params


def run_rpa_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    config_path = segment.get("config_path")
    config = load_json(config_path) if config_path else segment.get("config", {})
    return {
        output.get("name"): output.get("artifact_ref") or config.get("last_output")
        for output in segment.get("outputs", [])
        if output.get("name")
    }


def run_script_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    entry = segment["entry"]
    script_path = BASE_DIR / entry
    spec = importlib.util.spec_from_file_location(f"workflow_segment_{segment['id']}", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load script segment: {entry}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise RuntimeError(f"Script segment {entry} must define run(context)")
    return module.run(context)


def run_mcp_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    return {
        "status": "metadata_only",
        "tool": segment.get("tool"),
    }


def run_llm_segment(segment: dict[str, Any], context: WorkflowContext) -> dict[str, Any]:
    return {
        "status": "metadata_only",
        "schema": segment.get("schema"),
    }


def run(**kwargs):
    workflow = load_json("workflow.json")
    params = load_params(kwargs)
    context = WorkflowContext(params=params)

    for segment in workflow.get("segments", []):
        kind = segment.get("kind")
        if kind == "rpa":
            result = run_rpa_segment(segment, context)
        elif kind == "script":
            result = run_script_segment(segment, context)
        elif kind == "mcp":
            result = run_mcp_segment(segment, context)
        elif kind == "llm":
            result = run_llm_segment(segment, context)
        else:
            raise ValueError(f"Unsupported segment kind: {kind}")
        context.store_segment_outputs(segment["id"], result)

    return context.final_outputs()
'''
```

- [ ] **Step 4: Implement workflow publishing**

Create `RpaClaw/backend/workflow/publishing.py` with these public functions:

```python
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from .models import (
    CredentialRequirement,
    PublishInput,
    PublishOutput,
    PublishSegmentSummary,
    PublishWarning,
    SkillPublishDraft,
    WorkflowRun,
    WorkflowSegment,
)
from .runner_template import WORKFLOW_RUNNER_TEMPLATE


def safe_skill_name(value: str, fallback: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value).strip("_").lower()
    return candidate or fallback


def build_publish_draft(
    run: WorkflowRun,
    publish_target: Literal["skill", "tool", "mcp"] = "skill",
) -> SkillPublishDraft:
    ordered = run.ordered_segments()
    title = run.intent.strip() or "recorded_workflow"
    segment_titles = "、".join(segment.title for segment in ordered[:3]) or title
    warnings: list[PublishWarning] = []
    for segment in ordered:
        if segment.status not in {"tested", "configured"}:
            warnings.append(
                PublishWarning(
                    code="segment_not_tested",
                    message=f"片段「{segment.title}」尚未测试通过。",
                    segment_id=segment.id,
                )
            )

    return SkillPublishDraft(
        id=f"draft_{run.id}",
        run_id=run.id,
        publish_target=publish_target,
        skill_name=title,
        display_title=title,
        description=f"自动执行以下工作流片段：{segment_titles}。",
        trigger_examples=[title],
        inputs=_collect_publish_inputs(ordered),
        outputs=_collect_publish_outputs(ordered),
        credentials=_collect_credentials(ordered),
        segments=[
            PublishSegmentSummary(
                id=segment.id,
                kind=segment.kind,
                title=segment.title,
                purpose=segment.purpose,
                status=segment.status,
                input_count=len(segment.inputs),
                output_count=len(segment.outputs),
            )
            for segment in ordered
        ],
        warnings=warnings,
    )


def write_skill_artifacts(run: WorkflowRun, draft: SkillPublishDraft, base_dir: Path) -> dict[str, Any]:
    safe_name = safe_skill_name(draft.skill_name, f"workflow_{run.id[:8]}")
    skill_dir = base_dir / safe_name
    segments_dir = skill_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    workflow = _build_workflow_json(run, draft)
    (skill_dir / "workflow.json").write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (skill_dir / "params.schema.json").write_text(
        json.dumps(_build_params_schema(draft), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (skill_dir / "credentials.example.json").write_text(
        json.dumps(_build_credentials_example(draft), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(_build_skill_md(draft, safe_name), encoding="utf-8")
    (skill_dir / "skill.py").write_text(WORKFLOW_RUNNER_TEMPLATE, encoding="utf-8")

    for segment in run.ordered_segments():
        if segment.kind == "rpa":
            _write_rpa_segment(segments_dir, segment)
        elif segment.kind == "script":
            _write_script_segment(segments_dir, segment)
        else:
            _write_metadata_segment(segments_dir, segment)

    return {
        "name": safe_name,
        "skill_dir": str(skill_dir),
        "workflow_path": str(skill_dir / "workflow.json"),
    }
```

Add private helpers in the same file:

```python
def _collect_publish_inputs(segments: list[WorkflowSegment]) -> list[PublishInput]:
    collected: dict[str, PublishInput] = {}
    for segment in segments:
        for item in segment.inputs:
            if item.source != "user":
                continue
            if item.type == "secret":
                continue
            collected.setdefault(
                item.name,
                PublishInput(
                    name=item.name,
                    type=item.type,
                    required=item.required,
                    description=item.description,
                    default=item.default,
                ),
            )
    return list(collected.values())


def _collect_publish_outputs(segments: list[WorkflowSegment]) -> list[PublishOutput]:
    outputs: dict[str, PublishOutput] = {}
    for segment in segments:
        for item in segment.outputs:
            outputs[item.name] = PublishOutput(
                name=item.name,
                type=item.type,
                description=item.description,
            )
    return list(outputs.values())


def _collect_credentials(segments: list[WorkflowSegment]) -> list[CredentialRequirement]:
    credentials: dict[str, CredentialRequirement] = {}
    for segment in segments:
        auth_config = segment.config.get("auth_config")
        if not isinstance(auth_config, dict):
            continue
        for name in auth_config.get("credential_ids", []):
            credentials[str(name)] = CredentialRequirement(
                name=str(name),
                type="browser_session",
                description=f"片段「{segment.title}」需要的浏览器登录态。",
            )
    return list(credentials.values())


def _build_skill_md(draft: SkillPublishDraft, safe_name: str) -> str:
    segment_lines = [
        f"{index}. {segment.title}: {segment.purpose}"
        for index, segment in enumerate(draft.segments, start=1)
    ]
    input_lines = [
        f"- `{item.name}`: {item.description or item.type}"
        for item in draft.inputs
    ] or ["- 无需用户显式输入。"]
    credential_lines = [
        f"- `{item.name}`: {item.description}"
        for item in draft.credentials
    ] or ["- 无需额外认证，或使用当前浏览器登录态。"]
    output_lines = [
        f"- `{item.name}`: {item.description or item.type}"
        for item in draft.outputs
    ] or ["- 返回每个片段的执行状态和产物。"]
    trigger_lines = [f"- {example}" for example in draft.trigger_examples] or [f"- {draft.display_title}"]

    return "\n".join(
        [
            "---",
            f"name: {safe_name}",
            f'description: "{draft.description}"',
            "---",
            "",
            f"# {draft.display_title}",
            "",
            "## 何时使用",
            "",
            draft.description,
            "",
            "## 触发示例",
            "",
            *trigger_lines,
            "",
            "## 输入参数",
            "",
            *input_lines,
            "",
            "## 认证要求",
            "",
            *credential_lines,
            "",
            "## 工作流片段",
            "",
            *segment_lines,
            "",
            "## 输出",
            "",
            *output_lines,
            "",
            "## 失败处理",
            "",
            "如果片段执行失败，技能应返回失败片段、错误信息和已生成产物，便于用户重新录制或修复该片段。",
            "",
        ]
    )


def _build_workflow_json(run: WorkflowRun, draft: SkillPublishDraft) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "name": safe_skill_name(draft.skill_name, f"workflow_{run.id[:8]}"),
        "title": draft.display_title,
        "description": draft.description,
        "segments": [_segment_to_workflow_json(segment) for segment in run.ordered_segments()],
    }


def _segment_to_workflow_json(segment: WorkflowSegment) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": segment.id,
        "kind": segment.kind,
        "title": segment.title,
        "purpose": segment.purpose,
        "inputs": [item.model_dump(mode="json") for item in segment.inputs],
        "outputs": [item.model_dump(mode="json") for item in segment.outputs],
    }
    if segment.kind == "rpa":
        base["config_path"] = f"segments/{segment.id}_rpa.json"
    elif segment.kind == "script":
        base["entry"] = segment.config.get("entry") or f"segments/{segment.id}_script.py"
    else:
        base["config_path"] = f"segments/{segment.id}_{segment.kind}.json"
    return base


def _build_params_schema(draft: SkillPublishDraft) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in draft.inputs:
        if item.type == "secret":
            continue
        json_type = "string" if item.type == "file" else item.type
        properties[item.name] = {
            "type": json_type,
            "description": item.description,
        }
        if item.default is not None:
            properties[item.name]["default"] = item.default
        if item.required:
            required.append(item.name)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _build_credentials_example(draft: SkillPublishDraft) -> dict[str, Any]:
    return {
        item.name: {
            "type": item.type,
            "description": item.description,
        }
        for item in draft.credentials
    }


def _write_rpa_segment(segments_dir: Path, segment: WorkflowSegment) -> None:
    payload = {
        "id": segment.id,
        "title": segment.title,
        "purpose": segment.purpose,
        "steps": segment.config.get("steps", []),
        "browser": segment.config.get("browser", {}),
    }
    (segments_dir / f"{segment.id}_rpa.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_script_segment(segments_dir: Path, segment: WorkflowSegment) -> None:
    entry = segment.config.get("entry") or f"segments/{segment.id}_script.py"
    source = segment.config.get("source") or "def run(context):\n    return {}\n"
    script_name = Path(str(entry)).name
    (segments_dir / script_name).write_text(str(source), encoding="utf-8")


def _write_metadata_segment(segments_dir: Path, segment: WorkflowSegment) -> None:
    payload = segment.model_dump(mode="json")
    (segments_dir / f"{segment.id}_{segment.kind}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
```

- [ ] **Step 5: Run publishing tests**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_workflow_publishing.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/workflow/runner_template.py RpaClaw/backend/workflow/publishing.py RpaClaw/backend/tests/test_workflow_publishing.py
git commit -m "feat: generate workflow skill artifacts"
```

---

## Task 3: Recording Compatibility Adapter

**Files:**
- Create: `RpaClaw/backend/workflow/recording_adapter.py`
- Modify: `RpaClaw/backend/recording/publishing.py`
- Modify: `RpaClaw/backend/tests/test_recording_publishing.py`

- [ ] **Step 1: Extend recording publishing tests for multi-segment artifacts**

Replace `RpaClaw/backend/tests/test_recording_publishing.py` with:

```python
import asyncio
import json
import tempfile
from pathlib import Path

from backend.recording.models import RecordingRun, RecordingSegment
from backend.recording.publishing import build_publish_artifacts


def test_publish_skill_builds_complete_multi_segment_skill():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="mixed")
        run.publish_target = "skill"
        run.segments = [
            RecordingSegment(
                id="segment-1",
                run_id="run-1",
                kind="rpa",
                intent="下载报表",
                status="completed",
                steps=[
                    {"id": "step-1", "action": "goto", "target": "https://example.com"},
                    {"id": "step-2", "action": "click", "target": "Download"},
                ],
                exports={
                    "title": "下载报表",
                    "description": "从网页下载 Excel 报表",
                    "testing_status": "passed",
                },
            ),
            RecordingSegment(
                id="segment-2",
                run_id="run-1",
                kind="chat_process",
                intent="转换下载文件",
                status="completed",
                exports={
                    "title": "转换报表",
                    "description": "将下载文件转换为 CSV",
                    "testing_status": "passed",
                    "script": "def run(context):\n    return {'converted_csv': 'output.csv'}\n",
                    "entry": "segments/segment-2_transform.py",
                    "params": {
                        "report_date": {
                            "original_value": "2026-04-21",
                            "sensitive": False,
                        }
                    },
                },
            ),
        ]

        result = asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))

        assert result.prompt_kind == "skill"
        staged_dir = Path(result.staging_paths[0])
        assert (staged_dir / "SKILL.md").is_file()
        assert (staged_dir / "workflow.json").is_file()
        assert (staged_dir / "params.schema.json").is_file()
        assert (staged_dir / "credentials.example.json").is_file()
        assert (staged_dir / "segments" / "segment-1_rpa.json").is_file()
        assert (staged_dir / "segments" / "segment-2_transform.py").is_file()

        workflow = json.loads((staged_dir / "workflow.json").read_text(encoding="utf-8"))
        assert [segment["id"] for segment in workflow["segments"]] == ["segment-1", "segment-2"]
        assert workflow["segments"][0]["kind"] == "rpa"
        assert workflow["segments"][1]["kind"] == "script"

        params_schema = json.loads((staged_dir / "params.schema.json").read_text(encoding="utf-8"))
        assert "report_date" in params_schema["properties"]

        skill_md = (staged_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "下载报表" in skill_md
        assert "转换报表" in skill_md


def test_publish_tool_builds_tool_staging_output():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="mcp")
        run.publish_target = "tool"

        result = asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))

        assert result.prompt_kind == "tool"
        assert result.staging_paths
        staged_file = Path(result.staging_paths[0])
        assert staged_file.parent.name == "tools_staging"
        assert staged_file.suffix == ".py"
```

- [ ] **Step 2: Run compatibility tests to verify they fail**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_recording_publishing.py -q
```

Expected: fail because old publishing does not write `workflow.json`, `params.schema.json`, or script segment files.

- [ ] **Step 3: Implement recording adapter**

Create `RpaClaw/backend/workflow/recording_adapter.py`:

```python
from __future__ import annotations

from typing import Any

from backend.recording.models import RecordingArtifact, RecordingRun, RecordingSegment

from .models import ArtifactRef, SegmentInput, SegmentOutput, SegmentTestResult, WorkflowRun, WorkflowSegment


def recording_run_to_workflow(run: RecordingRun) -> WorkflowRun:
    return WorkflowRun(
        id=run.id,
        session_id=run.session_id,
        user_id=run.user_id,
        intent=_run_intent(run),
        status="ready_to_publish" if run.status in {"ready_to_publish", "completed", "saved"} else "draft",
        segments=[
            recording_segment_to_workflow(segment, order=index + 1)
            for index, segment in enumerate(run.segments)
        ],
        artifacts=[recording_artifact_to_ref(artifact) for artifact in run.artifact_index],
    )


def recording_segment_to_workflow(segment: RecordingSegment, order: int) -> WorkflowSegment:
    exports = segment.exports or {}
    kind = _segment_kind(segment)
    title = str(exports.get("title") or segment.intent or f"片段 {order}")
    purpose = str(exports.get("description") or segment.intent or title)
    config: dict[str, Any] = {
        "source_recording_kind": segment.kind,
        "auth_config": exports.get("auth_config", {}),
    }

    if kind == "rpa":
        config["steps"] = segment.steps
    elif kind == "script":
        config["language"] = exports.get("language", "python")
        config["entry"] = exports.get("entry") or f"segments/{segment.id}_script.py"
        config["source"] = exports.get("script") or "def run(context):\n    return {}\n"
    else:
        config.update(exports)

    return WorkflowSegment(
        id=segment.id,
        run_id=segment.run_id,
        kind=kind,
        order=order,
        title=title,
        purpose=purpose,
        status="tested" if exports.get("testing_status") == "passed" else "configured",
        inputs=_params_to_inputs(exports.get("params", {})),
        outputs=_infer_outputs(segment, kind),
        artifacts=[recording_artifact_to_ref(artifact) for artifact in segment.artifacts],
        config=config,
        test_result=SegmentTestResult(
            status="passed" if exports.get("testing_status") == "passed" else "idle",
        ),
    )


def recording_artifact_to_ref(artifact: RecordingArtifact) -> ArtifactRef:
    return ArtifactRef(
        id=artifact.id,
        name=artifact.name,
        type=artifact.type,
        path=artifact.path,
        value=artifact.value,
        mime_type=artifact.mime_type,
        labels=artifact.labels,
        producer_segment_id=artifact.segment_id,
    )


def _run_intent(run: RecordingRun) -> str:
    if run.save_intent:
        return run.save_intent
    if run.segments:
        return run.segments[0].intent or "recorded_workflow"
    return "recorded_workflow"


def _segment_kind(segment: RecordingSegment) -> str:
    if segment.kind == "chat_process":
        return "script"
    if segment.kind in {"rpa", "mcp", "mixed"}:
        return segment.kind
    return "mixed"


def _params_to_inputs(params: dict[str, Any]) -> list[SegmentInput]:
    inputs: list[SegmentInput] = []
    for name, config in params.items():
        if not isinstance(config, dict):
            continue
        sensitive = bool(config.get("sensitive"))
        inputs.append(
            SegmentInput(
                name=name,
                type="secret" if sensitive else "string",
                required=False,
                source="credential" if sensitive else "user",
                description=f"片段参数 {name}",
                default=None if sensitive else config.get("original_value"),
            )
        )
    return inputs


def _infer_outputs(segment: RecordingSegment, kind: str) -> list[SegmentOutput]:
    if kind == "rpa" and segment.artifacts:
        return [
            SegmentOutput(
                name=artifact.name or "artifact",
                type="file" if artifact.type == "file" else "json",
                description=f"片段「{segment.intent}」生成的产物",
                artifact_ref=artifact.id,
            )
            for artifact in segment.artifacts
        ]
    if kind == "script":
        return [
            SegmentOutput(
                name="result",
                type="json",
                description="脚本处理结果",
            )
        ]
    return []
```

- [ ] **Step 4: Replace recording publisher with workflow-backed implementation**

Modify `RpaClaw/backend/recording/publishing.py` so the skill path delegates to workflow publishing while the tool path stays compatible:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.workflow.publishing import build_publish_draft, safe_skill_name, write_skill_artifacts
from backend.workflow.recording_adapter import recording_run_to_workflow

from .models import RecordingRun


@dataclass
class PublishPreparation:
    prompt_kind: Literal["skill", "tool"]
    staging_paths: list[str]
    summary: dict[str, Any]


def _safe_name(value: str, fallback: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value).strip("_").lower()
    return candidate or fallback


def _tool_staging_dir(workspace_dir: str, session_id: str) -> Path:
    return Path(workspace_dir) / session_id / "tools_staging"


async def build_publish_artifacts(run: RecordingRun, workspace_dir: str) -> PublishPreparation:
    target = run.publish_target or run.save_intent or "skill"
    if target not in {"skill", "tool"}:
        target = "skill"

    workflow_run = recording_run_to_workflow(run)
    draft = build_publish_draft(workflow_run, publish_target="skill" if target == "skill" else "tool")

    if target == "tool":
        safe_name = safe_skill_name(draft.skill_name, f"recording_{run.id[:8]}")
        target_dir = _tool_staging_dir(workspace_dir, run.session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        tool_path = target_dir / f"{safe_name}.py"
        tool_path.write_text(
            "\n".join(
                [
                    '"""Generated from a recorded workflow."""',
                    "",
                    "def run(*args, **kwargs):",
                    "    return {",
                    f'        "run_id": "{run.id}",',
                    f'        "segments": {len(run.segments)},',
                    "    }",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return PublishPreparation(
            prompt_kind="tool",
            staging_paths=[str(tool_path)],
            summary={
                "name": safe_name,
                "title": draft.display_title,
                "run_id": run.id,
                "draft": draft.model_dump(mode="json"),
            },
        )

    staging_dir = Path(workspace_dir) / run.session_id / "skills_staging" / run.id
    save_ready_root = Path(workspace_dir) / run.session_id / ".agents" / "skills"
    staging_result = write_skill_artifacts(workflow_run, draft, staging_dir.parent)
    staged_skill_dir = Path(staging_result["skill_dir"])
    final_result = write_skill_artifacts(workflow_run, draft, save_ready_root)

    return PublishPreparation(
        prompt_kind="skill",
        staging_paths=[str(staged_skill_dir), final_result["skill_dir"]],
        summary={
            "name": staging_result["name"],
            "title": draft.display_title,
            "run_id": run.id,
            "session_id": run.session_id,
            "draft": draft.model_dump(mode="json"),
            "segments": [segment.model_dump(mode="json") for segment in workflow_run.segments],
            "artifacts": [artifact.model_dump(mode="json") for artifact in workflow_run.artifacts],
        },
    )
```

- [ ] **Step 5: Run backend publishing tests**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_recording_publishing.py tests/test_workflow_publishing.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/workflow/recording_adapter.py RpaClaw/backend/recording/publishing.py RpaClaw/backend/tests/test_recording_publishing.py
git commit -m "feat: publish recordings as workflow skills"
```

---

## Task 4: Publish Draft API

**Files:**
- Modify: `RpaClaw/backend/route/sessions.py`
- Modify: `RpaClaw/backend/deepagent/tools.py`
- Test: `RpaClaw/backend/tests/test_recording_publishing.py`

- [ ] **Step 1: Add route request models**

Modify the request model section in `RpaClaw/backend/route/sessions.py` near `PublishRecordingRunRequest`:

```python
class PrepareRecordingPublishDraftRequest(BaseModel):
    publish_target: str = Field(default="skill", description="Publish target: skill, tool or mcp")


class PublishRecordingRunRequest(BaseModel):
    publish_target: str = Field(default="skill", description="Publish target: skill, tool or mcp")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="Final user-confirmed publish draft")
```

- [ ] **Step 2: Add publish draft endpoint**

Add this route before the existing `publish_recording_run` route:

```python
@router.post("/{session_id}/recordings/{run_id}/publish-draft", response_model=ApiResponse)
async def prepare_recording_publish_draft(
    session_id: str,
    run_id: str,
    body: PrepareRecordingPublishDraftRequest,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    try:
        session = await async_get_science_session(session_id)
    except Exception as exc:
        logger.error("Failed to load session %s: %s", session_id, exc)
        raise HTTPException(status_code=404, detail="Session not found") from exc

    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    run = recording_orchestrator.get_run(run_id)
    if not run or run.session_id != session_id or run.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording run not found")

    from backend.workflow.publishing import build_publish_draft
    from backend.workflow.recording_adapter import recording_run_to_workflow

    workflow_run = recording_run_to_workflow(run)
    draft = build_publish_draft(workflow_run, publish_target=body.publish_target if body.publish_target in {"skill", "tool", "mcp"} else "skill")
    return ApiResponse(data={
        "run": _serialize_recording_obj(run),
        "draft": draft.model_dump(mode="json"),
    })
```

- [ ] **Step 3: Make publish route accept final draft**

In `publish_recording_run`, replace the direct `recording_publishing.build_publish_artifacts(...)` call with:

```python
        recording_orchestrator.mark_ready_to_publish(run, body.publish_target)
        if body.draft:
            from backend.workflow.models import SkillPublishDraft
            from backend.workflow.publishing import write_skill_artifacts
            from backend.workflow.recording_adapter import recording_run_to_workflow

            workflow_run = recording_run_to_workflow(run)
            draft = SkillPublishDraft.model_validate(body.draft)
            base_dir = _WORKSPACE_DIR / run.session_id / "skills_staging"
            result = write_skill_artifacts(workflow_run, draft, base_dir)
            save_ready_root = _WORKSPACE_DIR / run.session_id / ".agents" / "skills"
            save_ready = write_skill_artifacts(workflow_run, draft, save_ready_root)
            prepared = recording_publishing.PublishPreparation(
                prompt_kind="skill",
                staging_paths=[result["skill_dir"], save_ready["skill_dir"]],
                summary={
                    "name": result["name"],
                    "title": draft.display_title,
                    "run_id": run.id,
                    "session_id": run.session_id,
                    "draft": draft.model_dump(mode="json"),
                },
            )
        else:
            prepared = await recording_publishing.build_publish_artifacts(run, workspace_dir=str(_WORKSPACE_DIR))
```

Keep the existing event append and response structure after this block.

- [ ] **Step 4: Update deepagent publishing tool**

Modify `RpaClaw/backend/deepagent/tools.py` in `prepare_recording_publish` so it still works with the compatibility wrapper but returns draft metadata if present:

```python
prepared = await recording_publishing.build_publish_artifacts(run, workspace_dir=workspace_dir)
summary = prepared.summary
return {
    "ok": True,
    "prompt_kind": prepared.prompt_kind,
    "staging_paths": prepared.staging_paths,
    "summary": summary,
    "draft": summary.get("draft"),
}
```

- [ ] **Step 5: Run API-adjacent tests**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_recording_publishing.py tests/test_sessions.py -q
```

Expected: existing session tests pass. If `tests/test_sessions.py` requires services not available locally, record the failure and run the narrower publishing tests before committing.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/route/sessions.py RpaClaw/backend/deepagent/tools.py
git commit -m "feat: add recording publish draft api"
```

---

## Task 5: Frontend Types and Recording API

**Files:**
- Modify: `RpaClaw/frontend/src/types/recording.ts`
- Modify: `RpaClaw/frontend/src/api/recording.ts`
- Test: `RpaClaw/frontend/src/components/__tests__/useRecordingRun.spec.ts`

- [ ] **Step 1: Add frontend workflow publish types**

Append to `RpaClaw/frontend/src/types/recording.ts`:

```ts
export type WorkflowSegmentKind = 'rpa' | 'script' | 'mcp' | 'llm' | 'mixed'
export type WorkflowValueType = 'string' | 'number' | 'boolean' | 'file' | 'json' | 'secret'

export interface WorkflowIO {
  name: string
  type: WorkflowValueType
  required?: boolean
  source?: 'user' | 'workflow_param' | 'segment_output' | 'artifact' | 'credential'
  source_ref?: string | null
  description?: string
  default?: unknown
}

export interface WorkflowPublishSegmentSummary {
  id: string
  kind: WorkflowSegmentKind
  title: string
  purpose: string
  status: string
  input_count: number
  output_count: number
}

export interface WorkflowCredentialRequirement {
  name: string
  type: 'browser_session' | 'api_key' | 'username_password' | 'oauth' | 'secret'
  description: string
}

export interface WorkflowPublishWarning {
  code: string
  message: string
  segment_id?: string | null
}

export interface SkillPublishDraft {
  id: string
  run_id: string
  publish_target: 'skill' | 'tool' | 'mcp'
  skill_name: string
  display_title: string
  description: string
  trigger_examples: string[]
  inputs: WorkflowIO[]
  outputs: WorkflowIO[]
  credentials: WorkflowCredentialRequirement[]
  segments: WorkflowPublishSegmentSummary[]
  warnings: WorkflowPublishWarning[]
}
```

Extend `RecordingPublishPreparedPayload.summary`:

```ts
    draft?: SkillPublishDraft
```

- [ ] **Step 2: Update frontend API functions**

Modify `RpaClaw/frontend/src/api/recording.ts`:

```ts
import type {
  RecordingArtifact,
  RecordingParamConfig,
  RecordingStep,
  SkillPublishDraft,
} from '@/types/recording'
```

Add:

```ts
export async function prepareRecordingPublishDraft(
  sessionId: string,
  runId: string,
  publishTarget: 'skill' | 'tool' | 'mcp',
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/publish-draft`,
    { publish_target: publishTarget },
  )
  return response.data.data as { draft: SkillPublishDraft }
}
```

Replace `publishRecordingRun` with:

```ts
export async function publishRecordingRun(
  sessionId: string,
  runId: string,
  publishTarget: 'skill' | 'tool' | 'mcp',
  draft?: SkillPublishDraft,
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/publish`,
    { publish_target: publishTarget, draft },
  )
  return response.data.data
}
```

- [ ] **Step 3: Run type check**

Run:

```powershell
cd RpaClaw/frontend
npm run type-check
```

Expected: no TypeScript errors after later tasks update callers. If this task is run alone, the only acceptable failures are caller type mismatches in `ChatPage.vue`; fix them in Task 7.

- [ ] **Step 4: Commit**

```powershell
git add RpaClaw/frontend/src/types/recording.ts RpaClaw/frontend/src/api/recording.ts
git commit -m "feat: add workflow publish draft client types"
```

---

## Task 6: Publish Draft Modal

**Files:**
- Create: `RpaClaw/frontend/src/components/RecordingPublishDraftModal.vue`
- Test: `RpaClaw/frontend/src/components/__tests__/RecordingPublishDraftModal.spec.ts`

- [ ] **Step 1: Write modal tests**

Create `RpaClaw/frontend/src/components/__tests__/RecordingPublishDraftModal.spec.ts`:

```ts
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RecordingPublishDraftModal from '../RecordingPublishDraftModal.vue'
import type { SkillPublishDraft } from '@/types/recording'

const draft: SkillPublishDraft = {
  id: 'draft_run_1',
  run_id: 'run_1',
  publish_target: 'skill',
  skill_name: 'download_and_convert_report',
  display_title: '下载并转换业务报表',
  description: '自动下载业务报表并转换为 CSV。',
  trigger_examples: ['帮我下载并转换业务报表'],
  inputs: [],
  outputs: [],
  credentials: [],
  segments: [
    {
      id: 'segment_1',
      kind: 'rpa',
      title: '下载报表',
      purpose: '从网页下载报表',
      status: 'tested',
      input_count: 0,
      output_count: 1,
    },
    {
      id: 'segment_2',
      kind: 'script',
      title: '转换报表',
      purpose: '将下载文件转换为 CSV',
      status: 'tested',
      input_count: 1,
      output_count: 1,
    },
  ],
  warnings: [],
}

describe('RecordingPublishDraftModal', () => {
  it('renders editable final skill metadata and segment list', () => {
    const wrapper = mount(RecordingPublishDraftModal, {
      props: {
        visible: true,
        draft,
        saving: false,
      },
    })

    expect(wrapper.text()).toContain('发布为技能')
    expect((wrapper.get('[data-testid="publish-skill-name"]').element as HTMLInputElement).value).toBe('download_and_convert_report')
    expect(wrapper.text()).toContain('下载报表')
    expect(wrapper.text()).toContain('转换报表')
  })

  it('emits save with edited draft', async () => {
    const wrapper = mount(RecordingPublishDraftModal, {
      props: {
        visible: true,
        draft,
        saving: false,
      },
    })

    await wrapper.get('[data-testid="publish-skill-name"]').setValue('business_report_flow')
    await wrapper.get('[data-testid="publish-save"]').trigger('click')

    const emitted = wrapper.emitted('save')?.[0]?.[0] as SkillPublishDraft
    expect(emitted.skill_name).toBe('business_report_flow')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd RpaClaw/frontend
npm run test -- RecordingPublishDraftModal.spec.ts
```

Expected: fail because the component does not exist.

- [ ] **Step 3: Implement modal component**

Create `RpaClaw/frontend/src/components/RecordingPublishDraftModal.vue`:

```vue
<template>
  <div
    v-if="visible && localDraft"
    class="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-sm"
  >
    <div class="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl dark:bg-gray-950">
      <header class="flex items-center justify-between border-b border-gray-100 px-6 py-4 dark:border-gray-800">
        <div>
          <p class="text-xs font-bold uppercase tracking-[0.22em] text-blue-500">Publish Draft</p>
          <h2 class="mt-1 text-lg font-extrabold text-gray-950 dark:text-gray-50">发布为技能</h2>
        </div>
        <button class="rounded-full px-3 py-1 text-sm text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900" type="button" @click="$emit('close')">
          关闭
        </button>
      </header>

      <main class="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
        <section class="grid gap-4 sm:grid-cols-2">
          <label class="space-y-1">
            <span class="text-xs font-bold text-gray-500">技能名称</span>
            <input
              v-model="localDraft.skill_name"
              data-testid="publish-skill-name"
              class="w-full rounded-2xl border border-gray-200 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-900"
            />
          </label>
          <label class="space-y-1">
            <span class="text-xs font-bold text-gray-500">显示标题</span>
            <input
              v-model="localDraft.display_title"
              class="w-full rounded-2xl border border-gray-200 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-900"
            />
          </label>
        </section>

        <label class="block space-y-1">
          <span class="text-xs font-bold text-gray-500">技能描述</span>
          <textarea
            v-model="localDraft.description"
            rows="3"
            class="w-full rounded-2xl border border-gray-200 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-900"
          />
        </label>

        <section>
          <h3 class="text-sm font-extrabold text-gray-950 dark:text-gray-50">工作流片段</h3>
          <div class="mt-3 space-y-2">
            <div
              v-for="(segment, index) in localDraft.segments"
              :key="segment.id"
              class="rounded-2xl border border-gray-100 bg-gray-50 px-4 py-3 dark:border-gray-800 dark:bg-gray-900"
            >
              <div class="flex items-start justify-between gap-3">
                <div>
                  <div class="text-sm font-bold text-gray-950 dark:text-gray-50">
                    {{ index + 1 }}. {{ segment.title }}
                  </div>
                  <div class="mt-1 text-xs text-gray-500">{{ segment.purpose }}</div>
                </div>
                <span class="rounded-full bg-white px-2 py-1 text-[11px] font-bold uppercase text-blue-600 dark:bg-gray-950">
                  {{ segment.kind }}
                </span>
              </div>
            </div>
          </div>
        </section>

        <section v-if="localDraft.warnings.length" class="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          <div class="font-bold">发布前提示</div>
          <ul class="mt-2 list-disc space-y-1 pl-5">
            <li v-for="warning in localDraft.warnings" :key="`${warning.code}-${warning.segment_id || 'run'}`">
              {{ warning.message }}
            </li>
          </ul>
        </section>
      </main>

      <footer class="flex items-center justify-end gap-2 border-t border-gray-100 px-6 py-4 dark:border-gray-800">
        <button class="rounded-xl px-4 py-2 text-sm font-bold text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900" type="button" @click="$emit('close')">
          取消
        </button>
        <button
          data-testid="publish-save"
          class="rounded-xl bg-blue-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
          type="button"
          :disabled="saving || !localDraft.skill_name.trim() || !localDraft.description.trim()"
          @click="$emit('save', localDraft)"
        >
          {{ saving ? '保存中...' : '保存技能' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import type { SkillPublishDraft } from '@/types/recording'

const props = defineProps<{
  visible: boolean
  draft: SkillPublishDraft | null
  saving: boolean
}>()

defineEmits<{
  close: []
  save: [draft: SkillPublishDraft]
}>()

const localDraft = ref<SkillPublishDraft | null>(null)

watch(
  () => props.draft,
  (draft) => {
    localDraft.value = draft ? JSON.parse(JSON.stringify(draft)) as SkillPublishDraft : null
  },
  { immediate: true },
)
</script>
```

- [ ] **Step 4: Run modal tests**

Run:

```powershell
cd RpaClaw/frontend
npm run test -- RecordingPublishDraftModal.spec.ts
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```powershell
git add RpaClaw/frontend/src/components/RecordingPublishDraftModal.vue RpaClaw/frontend/src/components/__tests__/RecordingPublishDraftModal.spec.ts
git commit -m "feat: add recording publish draft modal"
```

---

## Task 7: Chat Publish Flow Integration

**Files:**
- Modify: `RpaClaw/frontend/src/composables/useRecordingRun.ts`
- Modify: `RpaClaw/frontend/src/pages/ChatPage.vue`
- Test: `RpaClaw/frontend/src/components/__tests__/useRecordingRun.spec.ts`

- [ ] **Step 1: Update recording store state**

Modify imports in `RpaClaw/frontend/src/composables/useRecordingRun.ts`:

```ts
  SkillPublishDraft,
```

Replace `publishPrompt` with:

```ts
  const publishDraft = ref<SkillPublishDraft | null>(null)
```

Add:

```ts
  const setPublishDraft = (draft: SkillPublishDraft | null) => {
    publishDraft.value = draft
  }
```

Update `onPublishPrepared`:

```ts
  const onPublishPrepared = (payload: RecordingPublishPreparedPayload) => {
    run.value = payload.run
    actionPrompt.value = null
    publishDraft.value = payload.summary.draft || null
  }
```

Return `publishDraft` and `setPublishDraft`, and remove returned `publishPrompt`.

- [ ] **Step 2: Update ChatPage imports**

Modify imports in `RpaClaw/frontend/src/pages/ChatPage.vue`:

```ts
import RecordingPublishDraftModal from '@/components/RecordingPublishDraftModal.vue';
import { prepareRecordingPublishDraft, publishRecordingRun } from '@/api/recording';
import type { SkillPublishDraft } from '@/types/recording';
```

- [ ] **Step 3: Add modal to ChatPage template**

Place this beside `RecordingRecorderModal`:

```vue
<RecordingPublishDraftModal
  :visible="!!recordingStore.publishDraft.value"
  :draft="recordingStore.publishDraft.value"
  :saving="recordingActionBusy === 'save'"
  @close="recordingStore.setPublishDraft(null)"
  @save="handleSaveRecordingPublishDraft"
/>
```

- [ ] **Step 4: Change prepare publish handler**

Replace `handlePrepareRecordingPublish` body with:

```ts
const handlePrepareRecordingPublish = async () => {
  const prompt = recordingStore.actionPrompt.value;
  if (!sessionId.value || !prompt) return;

  recordingActionBusy.value = 'publish';
  try {
    const payload = await prepareRecordingPublishDraft(sessionId.value, prompt.runId, prompt.publishTarget);
    recordingStore.setPublishDraft(payload.draft);
    recordingStore.dismissActionPrompt();
  } catch (error) {
    console.error('Failed to prepare recording publish draft:', error);
  } finally {
    recordingActionBusy.value = null;
  }
};
```

Add save handler:

```ts
const handleSaveRecordingPublishDraft = async (draft: SkillPublishDraft) => {
  if (!sessionId.value) return;

  recordingActionBusy.value = 'save';
  try {
    await publishRecordingRun(sessionId.value, draft.run_id, draft.publish_target, draft);
    recordingStore.setPublishDraft(null);
    pendingSkillSave.value = draft.skill_name;
  } catch (error) {
    console.error('Failed to save recording publish draft:', error);
  } finally {
    recordingActionBusy.value = null;
  }
};
```

- [ ] **Step 5: Keep event compatibility**

Update `handleRecordingPublishPrepared`:

```ts
const handleRecordingPublishPrepared = (payload: RecordingPublishPreparedPayload) => {
  recordingStore.onPublishPrepared(payload);
  if (!payload.summary.draft) {
    if (payload.prompt_kind === 'skill') {
      pendingSkillSave.value = payload.summary.name || payload.summary.title || 'recorded_workflow';
    } else {
      pendingToolSave.value = payload.summary.name || payload.summary.title || 'recorded_workflow';
    }
  }
};
```

- [ ] **Step 6: Run frontend tests and type check**

Run:

```powershell
cd RpaClaw/frontend
npm run test -- useRecordingRun.spec.ts RecordingPublishDraftModal.spec.ts
npm run type-check
```

Expected: tests and type check pass.

- [ ] **Step 7: Commit**

```powershell
git add RpaClaw/frontend/src/composables/useRecordingRun.ts RpaClaw/frontend/src/pages/ChatPage.vue
git commit -m "feat: confirm workflow publish draft in chat"
```

---

## Task 8: Segment Configuration Semantics and Cards

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`
- Modify: `RpaClaw/frontend/src/pages/rpa/TestPage.vue`
- Modify: `RpaClaw/frontend/src/components/RecordingSegmentCard.vue`
- Modify: `RpaClaw/frontend/src/components/__tests__/RecordingSegmentCard.spec.ts`

- [ ] **Step 1: Update ConfigurePage labels**

In `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`, replace user-visible labels:

```text
技能信息 -> 片段信息
技能名称 -> 片段名称
描述 -> 片段用途
保存技能 -> 完成片段
```

Keep query names compatible for this slice, but rename local variables if low risk:

```ts
const segmentTitle = ref(route.query.skillName?.toString() || '')
const segmentPurpose = ref(route.query.skillDescription?.toString() || '')
```

When navigating to test, continue passing the existing query keys plus explicit segment keys:

```ts
skillName: segmentTitle.value,
skillDescription: segmentPurpose.value,
segmentTitle: segmentTitle.value,
segmentPurpose: segmentPurpose.value,
```

- [ ] **Step 2: Update TestPage payload names**

In `RpaClaw/frontend/src/pages/rpa/TestPage.vue`, update `finishConversationalSegment()` to prefer segment query names:

```ts
const segmentTitle = computed(() => route.query.segmentTitle?.toString() || route.query.skillName?.toString() || '未命名片段')
const segmentPurpose = computed(() => route.query.segmentPurpose?.toString() || route.query.skillDescription?.toString() || '')
```

Send:

```ts
title: segmentTitle.value,
description: segmentPurpose.value,
```

- [ ] **Step 3: Extend segment card for non-RPA**

Modify `RecordingSegmentCard.vue`:

```vue
<p class="text-[11px] font-bold uppercase tracking-[0.22em] text-violet-500">
  {{ kindLabel }}
</p>
```

Add computed label:

```ts
const kindLabel = computed(() => {
  if (props.summary.kind === 'script') return 'Script segment'
  if (props.summary.kind === 'mcp') return 'MCP segment'
  if (props.summary.kind === 'llm') return 'LLM segment'
  return 'Workflow segment'
})
```

Show inputs and outputs when present:

```vue
<div v-if="expanded && (inputEntries.length || outputEntries.length)" class="border-t border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-950">
  <div v-if="inputEntries.length" class="text-xs text-gray-500">
    输入：{{ inputEntries.join('、') }}
  </div>
  <div v-if="outputEntries.length" class="mt-1 text-xs text-gray-500">
    输出：{{ outputEntries.join('、') }}
  </div>
</div>
```

Add computed entries:

```ts
const inputEntries = computed(() => {
  const inputs = (props.summary as any).inputs
  return Array.isArray(inputs) ? inputs.map((item) => item.name).filter(Boolean) : []
})

const outputEntries = computed(() => {
  const outputs = (props.summary as any).outputs
  return Array.isArray(outputs) ? outputs.map((item) => item.name).filter(Boolean) : []
})
```

- [ ] **Step 4: Update card tests**

Add a test to `RecordingSegmentCard.spec.ts`:

```ts
it('renders a script segment as a workflow segment card', async () => {
  const wrapper = mount(RecordingSegmentCard, {
    props: {
      summary: {
        segment_id: 'segment_2',
        kind: 'script',
        title: '转换报表',
        description: '将下载文件转换为 CSV',
        artifacts: [],
        steps: [],
        params: {},
        testing_status: 'passed',
        inputs: [{ name: 'source_file' }],
        outputs: [{ name: 'converted_csv' }],
      } as any,
    },
  })

  expect(wrapper.text()).toContain('Script segment')
  expect(wrapper.text()).toContain('转换报表')
  await wrapper.get('[data-testid="recording-segment-toggle"]').trigger('click')
  expect(wrapper.text()).toContain('source_file')
  expect(wrapper.text()).toContain('converted_csv')
})
```

- [ ] **Step 5: Run frontend tests**

Run:

```powershell
cd RpaClaw/frontend
npm run test -- RecordingSegmentCard.spec.ts
npm run type-check
```

Expected: tests and type check pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue RpaClaw/frontend/src/pages/rpa/TestPage.vue RpaClaw/frontend/src/components/RecordingSegmentCard.vue RpaClaw/frontend/src/components/__tests__/RecordingSegmentCard.spec.ts
git commit -m "fix: separate segment metadata from skill metadata"
```

---

## Task 9: Script Segment Minimum API

**Files:**
- Modify: `RpaClaw/backend/route/sessions.py`
- Modify: `RpaClaw/frontend/src/api/recording.ts`
- Modify: `RpaClaw/frontend/src/types/recording.ts`
- Test: `RpaClaw/backend/tests/test_recording_publishing.py`

- [ ] **Step 1: Add backend request model for script segment**

In `RpaClaw/backend/route/sessions.py`, add:

```python
class CreateScriptSegmentRequest(BaseModel):
    title: str = Field(..., description="Segment title")
    purpose: str = Field(..., description="Segment purpose")
    script: str = Field(..., description="Python script source defining run(context)")
    entry: str = Field(default="", description="Relative segment script path")
    params: Dict[str, Any] = Field(default_factory=dict)
    inputs: List[Dict[str, Any]] = Field(default_factory=list)
    outputs: List[Dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 2: Add endpoint that appends a chat_process segment**

Add:

```python
@router.post("/{session_id}/recordings/{run_id}/script-segments", response_model=ApiResponse)
async def create_recording_script_segment(
    session_id: str,
    run_id: str,
    body: CreateScriptSegmentRequest,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    session = await async_get_science_session(session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    run = recording_orchestrator.get_run(run_id)
    if not run or run.session_id != session_id or run.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording run not found")

    segment = recording_orchestrator.start_segment(run, "chat_process", body.purpose)
    segment.status = "completed"
    segment.exports = {
        "title": body.title,
        "description": body.purpose,
        "script": body.script,
        "entry": body.entry or f"segments/{segment.id}_script.py",
        "params": body.params,
        "inputs": body.inputs,
        "outputs": body.outputs,
        "testing_status": "passed",
    }
    recording_orchestrator.complete_segment(run, segment.id)

    summary = {
        "segment_id": segment.id,
        "intent": segment.intent,
        "title": body.title,
        "description": body.purpose,
        "kind": "script",
        "status": segment.status,
        "params": body.params,
        "artifacts": [],
        "steps": [],
        "inputs": body.inputs,
        "outputs": body.outputs,
        "testing_status": "passed",
    }
    _append_session_event(session, _wrap_event("recording_segment_completed", {
        "timestamp": _now_ts(),
        "segment": _serialize_recording_obj(segment),
        "summary": summary,
    }))
    await session.save()
    return ApiResponse(data={"segment": _serialize_recording_obj(segment), "summary": summary})
```

If `recording_orchestrator.start_segment` has a different signature, inspect `RpaClaw/backend/recording/orchestrator.py` and adapt to the existing method names rather than adding a parallel run store.

- [ ] **Step 3: Add frontend API function**

In `RpaClaw/frontend/src/api/recording.ts`, add:

```ts
export async function createScriptRecordingSegment(
  sessionId: string,
  runId: string,
  payload: {
    title: string
    purpose: string
    script: string
    entry?: string
    params?: Record<string, unknown>
    inputs?: Array<Record<string, unknown>>
    outputs?: Array<Record<string, unknown>>
  },
) {
  const response = await apiClient.post(
    `/sessions/${sessionId}/recordings/${runId}/script-segments`,
    payload,
  )
  return response.data.data
}
```

- [ ] **Step 4: Run backend tests**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_recording_publishing.py -q
```

Expected: publishing tests pass, proving script segments are represented in final artifacts.

- [ ] **Step 5: Commit**

```powershell
git add RpaClaw/backend/route/sessions.py RpaClaw/frontend/src/api/recording.ts RpaClaw/frontend/src/types/recording.ts
git commit -m "feat: add script workflow segment endpoint"
```

---

## Task 10: End-to-End Verification

**Files:**
- Verify changed backend and frontend files.

- [ ] **Step 1: Run backend workflow test suite**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_workflow_models.py tests/test_workflow_publishing.py tests/test_recording_publishing.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run broader backend regression slice**

Run:

```powershell
cd RpaClaw/backend
uv run pytest tests/test_recording_models.py tests/test_recording_orchestrator.py tests/test_recording_testing.py tests/test_recording_step_repair.py tests/test_rpa_generator.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run frontend tests**

Run:

```powershell
cd RpaClaw/frontend
npm run test -- RecordingPublishDraftModal.spec.ts RecordingSegmentCard.spec.ts useRecordingRun.spec.ts
```

Expected: all selected tests pass.

- [ ] **Step 4: Run frontend type check**

Run:

```powershell
cd RpaClaw/frontend
npm run type-check
```

Expected: no TypeScript errors.

- [ ] **Step 5: Build frontend**

Run:

```powershell
cd RpaClaw/frontend
npm run build
```

Expected: Vite build succeeds.

- [ ] **Step 6: Manual smoke path**

Run the app with existing local dev commands:

```powershell
cd D:\code\MyScienceClaw\.worktrees\codex-conversational-recording
docker compose up -d --build
```

Manual checks:

- Start a chat and ask to record a business workflow skill.
- Record one RPA segment that opens a page or downloads a file.
- Complete the segment and confirm the card appears collapsed in chat.
- Add a script segment through the new script segment API or chat tool path.
- Click prepare publish.
- Confirm the publish modal appears and allows editing final skill name and description.
- Save.
- Inspect the generated skill directory under the session workspace.
- Confirm `workflow.json` contains both segments in order.
- Confirm `SKILL.md` has valid front matter and both segment descriptions.
- Confirm `segments/` includes the RPA JSON and script file.

- [ ] **Step 7: Commit final verification fixes**

If verification required small fixes:

```powershell
git add RpaClaw/backend RpaClaw/frontend
git commit -m "fix: stabilize workflow segment creator"
```

If no fixes were required, do not create an empty commit.

---

## Implementation Notes

- Keep existing `/sessions/{session_id}/recordings/...` endpoints working during this slice. The new `workflow` domain is internal until the frontend and built-in skill are fully switched.
- Do not hardcode real credentials or write secret defaults into `params.schema.json`.
- Do not flatten all RPA steps into a single generated script. `workflow.json` is the source of truth, and `skill.py` is a runner.
- Keep `chat_process` as the compatibility kind for script segments in the current `RecordingSegment` model. Convert it to `script` in `recording_adapter.py`.
- The publish modal is the only place final skill name and final skill description are edited.
- RPA configure/test pages should use “片段名称 / 片段用途” language.

## Self-Review

- Spec coverage: the plan covers multi-type segment models, RPA + script segment workflow publishing, final publish draft confirmation, complete skill artifacts, params schema, credential example, segment card UX, and compatibility with current recording routes.
- Scope limit: MCP and LLM execution adapters are intentionally metadata-only in this slice; the data model and runner have extension points for them.
- Placeholder scan: this plan contains no unresolved implementation placeholders.
- Type consistency: backend uses `SkillPublishDraft`, `WorkflowRun`, and `WorkflowSegment`; frontend uses `SkillPublishDraft` and `WorkflowPublishSegmentSummary` with matching snake_case API fields.
