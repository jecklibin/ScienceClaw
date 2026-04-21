from __future__ import annotations

from typing import Any

from backend.recording.models import RecordingArtifact, RecordingRun, RecordingSegment

from .models import (
    ArtifactRef,
    SegmentInput,
    SegmentOutput,
    SegmentTestResult,
    WorkflowRun,
    WorkflowSegment,
    WorkflowSegmentKind,
)


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
        inputs=_params_to_inputs(exports.get("params", {}), exports.get("inputs", [])),
        outputs=_infer_outputs(segment, kind, exports.get("outputs", [])),
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


def _segment_kind(segment: RecordingSegment) -> WorkflowSegmentKind:
    if segment.kind == "chat_process":
        return "script"
    if segment.kind in {"rpa", "mcp", "mixed"}:
        return segment.kind
    return "mixed"


def _params_to_inputs(params: dict[str, Any], explicit_inputs: Any) -> list[SegmentInput]:
    inputs: list[SegmentInput] = []
    if isinstance(explicit_inputs, list):
        for item in explicit_inputs:
            if isinstance(item, dict) and item.get("name") and item.get("type"):
                inputs.append(SegmentInput.model_validate(item))

    for name, config in params.items():
        if not isinstance(config, dict):
            continue
        if any(item.name == name for item in inputs):
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


def _infer_outputs(segment: RecordingSegment, kind: WorkflowSegmentKind, explicit_outputs: Any) -> list[SegmentOutput]:
    if isinstance(explicit_outputs, list) and explicit_outputs:
        return [
            SegmentOutput.model_validate(item)
            for item in explicit_outputs
            if isinstance(item, dict) and item.get("name") and item.get("type")
        ]
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
