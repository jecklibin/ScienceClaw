from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.workflow.publishing import (
    build_publish_draft,
    build_workflow_artifact_payload,
    safe_skill_name,
    write_skill_artifacts,
)
from backend.workflow.recording_adapter import recording_run_to_workflow
from backend.workflow.models import PublishInput, SkillPublishDraft

from .models import RecordingRun


@dataclass
class PublishPreparation:
    prompt_kind: Literal["skill", "tool"]
    staging_paths: list[str]
    summary: dict[str, Any]


def _collect_rpa_mcp_source(run: RecordingRun) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    steps: list[dict[str, Any]] = []
    params: dict[str, Any] = {}
    source_session_id = ""
    for segment in run.segments:
        if segment.kind not in {"rpa", "mcp", "mixed"}:
            continue
        if not source_session_id:
            source_session_id = str(segment.exports.get("rpa_session_id") or "")
        for step in segment.steps:
            if isinstance(step, dict):
                steps.append(dict(step))
        segment_params = segment.exports.get("params")
        if isinstance(segment_params, dict):
            params.update(segment_params)
    if not steps:
        raise ValueError("Cannot publish recording as MCP tool without recorded RPA/MCP steps")
    return steps, params, source_session_id or run.id


def _json_schema_type(input_type: str) -> str:
    if input_type == "file":
        return "string"
    if input_type == "secret":
        return "string"
    if input_type == "json":
        return "object"
    if input_type in {"string", "number", "boolean", "integer"}:
        return input_type
    return "string"


def _build_public_mcp_input_schema(
    inputs: list[PublishInput],
    workflow_params: dict[str, Any],
    existing_schema: dict[str, Any],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    existing_properties = existing_schema.get("properties") if isinstance(existing_schema, dict) else {}
    if isinstance(existing_properties, dict) and "cookies" in existing_properties:
        properties["cookies"] = existing_properties["cookies"]
        if "cookies" in (existing_schema.get("required") or []):
            required.append("cookies")

    for item in inputs:
        if item.type == "secret":
            continue
        param_config = workflow_params.get(item.name) if isinstance(workflow_params, dict) else None
        if not isinstance(param_config, dict):
            param_config = {}
        prop: dict[str, Any] = {
            "type": _json_schema_type(item.type),
            "description": item.description or str(param_config.get("description") or ""),
        }
        default = item.default if item.default is not None else param_config.get("original_value")
        if default not in (None, "", "{{credential}}"):
            prop["default"] = default
        properties[item.name] = prop
        if item.required:
            required.append(item.name)
    return {"type": "object", "properties": properties, "required": required}


def _build_public_mcp_params(inputs: list[PublishInput], workflow_params: dict[str, Any]) -> dict[str, Any]:
    public_params: dict[str, Any] = {}
    for item in inputs:
        if item.type == "secret":
            continue
        param_config = workflow_params.get(item.name) if isinstance(workflow_params, dict) else None
        if isinstance(param_config, dict):
            public_params[item.name] = dict(param_config)
            continue
        public_params[item.name] = {
            "type": _json_schema_type(item.type),
            "description": item.description,
            "original_value": item.default if item.default is not None else "",
            "sensitive": False,
            "required": item.required,
        }
    return public_params


async def build_mcp_tool_artifacts(
    run: RecordingRun,
    draft: SkillPublishDraft,
    *,
    registry=None,
    converter=None,
) -> PublishPreparation:
    from backend.rpa.mcp_converter import RpaMcpConverter
    from backend.rpa.mcp_registry import RpaMcpToolRegistry

    workflow_run = recording_run_to_workflow(run)
    contract_draft = build_publish_draft(workflow_run, publish_target="tool")
    workflow_package = build_workflow_artifact_payload(workflow_run, draft)
    # The converter still owns MCP metadata/schema sanitization. Execution of
    # multi-segment conversational tools uses workflow_package so bindings,
    # script segments, and generated RPA modules remain the source of truth.
    steps, params, source_session_id = _collect_rpa_mcp_source(run)
    converter = converter or RpaMcpConverter()
    registry = registry or RpaMcpToolRegistry()
    tool_name = safe_skill_name(draft.skill_name, f"recording_{run.id[:8]}")
    preview = converter.preview(
        user_id=run.user_id,
        session_id=source_session_id,
        skill_name=draft.skill_name,
        name=draft.display_title or draft.skill_name,
        description=draft.description,
        steps=steps,
        params=params,
    )
    preview.id = f"rpa_mcp_{uuid.uuid4().hex[:12]}"
    preview.name = draft.display_title or draft.skill_name
    preview.tool_name = tool_name
    preview.description = draft.description
    preview.workflow_package = workflow_package
    preview.params = _build_public_mcp_params(contract_draft.inputs, workflow_package.get("params") or {})
    preview.input_schema = _build_public_mcp_input_schema(
        contract_draft.inputs,
        workflow_package.get("params") or {},
        preview.input_schema,
    )
    preview.output_schema_confirmed = True
    saved = await registry.save(preview)
    return PublishPreparation(
        prompt_kind="tool",
        staging_paths=[f"rpa-mcp:{saved.id}"],
        summary={
            "name": saved.tool_name,
            "title": saved.name,
            "run_id": run.id,
            "session_id": run.session_id,
            "tool_id": saved.id,
            "target": "mcp_tool",
            "saved": True,
            "draft": draft.model_dump(mode="json"),
        },
    )


async def build_publish_artifacts(run: RecordingRun, workspace_dir: str) -> PublishPreparation:
    target = run.publish_target or run.save_intent or "skill"
    if target not in {"skill", "tool"}:
        target = "skill"

    workflow_run = recording_run_to_workflow(run)
    draft = build_publish_draft(workflow_run, publish_target="skill" if target == "skill" else "tool")

    if target == "tool":
        return await build_mcp_tool_artifacts(run, draft)

    staging_root = Path(workspace_dir) / run.session_id / "skills_staging"
    save_ready_root = Path(workspace_dir) / run.session_id / ".agents" / "skills"
    staging_result = write_skill_artifacts(workflow_run, draft, staging_root)
    save_ready_result = write_skill_artifacts(workflow_run, draft, save_ready_root)

    return PublishPreparation(
        prompt_kind="skill",
        staging_paths=[staging_result["skill_dir"], save_ready_result["skill_dir"]],
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
