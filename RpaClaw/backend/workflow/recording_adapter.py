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
    title = str(exports.get("title") or segment.intent or f"Segment {order}")
    purpose = str(exports.get("description") or segment.intent or title)
    inputs = _params_to_inputs(exports.get("params", {}), exports.get("inputs", []))
    outputs = _infer_outputs(segment, kind, exports.get("outputs", []))

    config: dict[str, Any] = {
        "source_recording_kind": segment.kind,
        "auth_config": exports.get("auth_config", {}),
    }

    if kind == "rpa":
        config["steps"] = _align_rpa_steps(segment.steps, outputs)
        config["params"] = _align_rpa_params(exports.get("params", {}), inputs, config["steps"])
        config["browser"] = exports.get("browser", {})
    elif kind == "script":
        config["language"] = exports.get("language", "python")
        config["entry"] = exports.get("entry") or f"segments/{segment.id}_script.py"
        config["source"] = exports.get("script") or "def run(context):\n    return {}\n"
        config["params"] = exports.get("params", {})
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
        inputs=inputs,
        outputs=outputs,
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
    if segment.kind in {"rpa", "script", "mcp", "llm"}:
        return segment.kind
    if segment.kind == "mixed":
        exports = segment.exports or {}
        if segment.steps or exports.get("rpa_session_id") or exports.get("browser") or exports.get("auth_config"):
            return "rpa"
        if exports.get("script") or exports.get("entry") or exports.get("language"):
            return "script"
        if exports.get("tool") or exports.get("tool_name") or exports.get("tool_schema"):
            return "mcp"
    return "rpa" if segment.steps else "script"


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
                description=str(config.get("description") or f"参数 {name}"),
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
                description=f"Artifacts generated by segment {segment.intent}",
                artifact_ref=artifact.id,
            )
            for artifact in segment.artifacts
        ]
    if kind == "script":
        return [
            SegmentOutput(
                name="result",
                type="json",
                description="Script processing result",
            )
        ]
    return []


def _align_rpa_steps(steps: list[dict[str, Any]], outputs: list[SegmentOutput]) -> list[dict[str, Any]]:
    normalized = [dict(step) if isinstance(step, dict) else step for step in steps]
    extract_steps = [
        step for step in normalized
        if isinstance(step, dict) and step.get("action") == "extract_text"
    ]
    if not extract_steps:
        return normalized

    extract_output_names = [
        output.name
        for output in outputs
        if output.type in {"string", "number", "boolean", "json"} and not output.artifact_ref
    ]
    if not extract_output_names:
        return normalized

    for step, output_name in zip(extract_steps, extract_output_names):
        step.setdefault("result_key", output_name)
    return normalized


def _align_rpa_params(
    params: dict[str, Any],
    inputs: list[SegmentInput],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = dict(params or {})
    bound_inputs = [
        item for item in inputs
        if item.source in {"segment_output", "artifact", "workflow_param"} and item.name not in normalized
    ]
    if not bound_inputs:
        return normalized

    fill_steps = [
        step for step in steps
        if isinstance(step, dict) and step.get("action") in {"fill", "set_input_files"}
    ]
    used_step_ids: set[str] = set()
    for item in bound_inputs:
        step = next(
            (
                candidate for candidate in fill_steps
                if str(candidate.get("id") or "") not in used_step_ids
            ),
            None,
        )
        if step is None:
            continue
        used_step_ids.add(str(step.get("id") or id(step)))
        normalized[item.name] = {
            "original_value": _step_input_value(step),
            "sensitive": item.type == "secret",
            "description": item.description or f"参数 {item.name}",
        }
    return normalized


def _step_input_value(step: dict[str, Any]) -> str:
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    payload = signals.get("set_input_files") if isinstance(signals, dict) else None
    files = payload.get("files") if isinstance(payload, dict) else None
    if isinstance(files, list) and files:
        return str(files[0] or "")
    return str(step.get("value") or "")
