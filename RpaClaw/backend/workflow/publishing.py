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
