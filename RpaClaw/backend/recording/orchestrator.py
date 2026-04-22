from __future__ import annotations

import uuid
from datetime import datetime
import re
from typing import Any

from .lifecycle import move_run_status
from .models import RecordingRun, RecordingSegment


class RecordingOrchestrator:
    def __init__(self):
        self._runs: dict[str, RecordingRun] = {}

    def create_run(self, session_id: str, user_id: str, kind: str) -> RecordingRun:
        run = RecordingRun(
            id=str(uuid.uuid4()),
            session_id=session_id,
            user_id=user_id,
            type=kind,
            status="draft",
        )
        self._runs[run.id] = run
        return run

    def get_run(self, run_id: str) -> RecordingRun:
        return self._runs[run_id]

    def list_runs(self, session_id: str, user_id: str, include_saved: bool = False) -> list[RecordingRun]:
        runs = [
            run
            for run in self._runs.values()
            if run.session_id == session_id and run.user_id == user_id and (include_saved or run.status != "saved")
        ]
        return sorted(runs, key=lambda run: run.updated_at, reverse=True)

    def latest_run(self, session_id: str, user_id: str, include_saved: bool = False) -> RecordingRun | None:
        runs = self.list_runs(session_id=session_id, user_id=user_id, include_saved=include_saved)
        return runs[0] if runs else None

    def start_segment(
        self,
        run: RecordingRun,
        kind: str,
        intent: str,
        requires_workbench: bool,
    ) -> RecordingSegment:
        segment = RecordingSegment(
            id=str(uuid.uuid4()),
            run_id=run.id,
            kind=kind,
            intent=intent,
            status="recording",
        )
        run.segments.append(segment)
        run.active_segment_id = segment.id
        move_run_status(run, "recording" if requires_workbench else "waiting_user")
        run.updated_at = datetime.now()
        return segment

    def complete_segment(self, run: RecordingRun, segment: RecordingSegment) -> None:
        if run.status in {"recording", "waiting_user"}:
            move_run_status(run, "processing_artifacts")
        segment.status = "completed"
        segment.ended_at = datetime.now()
        run.active_segment_id = None
        move_run_status(run, "ready_for_next_segment")
        run.updated_at = datetime.now()

    def begin_testing(self, run: RecordingRun) -> None:
        if run.status != "testing":
            move_run_status(run, "testing")
        else:
            run.updated_at = datetime.now()
        run.testing = {"status": "running"}

    def mark_needs_repair(self, run: RecordingRun, error: str = "") -> None:
        move_run_status(run, "needs_repair")
        run.testing = {"status": "failed", "error": error}

    def mark_ready_to_publish(self, run: RecordingRun, publish_target: str) -> None:
        if publish_target not in {"skill", "tool"}:
            raise ValueError("publish_target must be skill or tool")
        run.publish_target = publish_target
        if run.status == "ready_for_next_segment":
            move_run_status(run, "testing")
        if run.status == "testing":
            run.testing = {"status": "passed"}
        if run.status != "ready_to_publish":
            move_run_status(run, "ready_to_publish")
        else:
            run.updated_at = datetime.now()

    def build_segment_summary(self, segment: RecordingSegment) -> dict[str, object]:
        summary = {
            "segment_id": segment.id,
            "intent": segment.intent,
            "kind": segment.kind,
            "status": segment.status,
            "artifacts": [artifact.model_dump(mode="json") for artifact in segment.artifacts],
            "steps": _normalize_summary_steps(segment.steps),
        }
        for key in ("params", "auth_config", "title", "description", "testing_status", "inputs", "outputs", "rpa_session_id"):
            if key in segment.exports:
                summary[key] = segment.exports[key]
        summary.setdefault("inputs", _infer_segment_inputs(segment))
        summary.setdefault("outputs", _infer_segment_outputs(segment))
        return summary

    def build_segment_mapping_sources(
        self,
        run: RecordingRun,
        segment: RecordingSegment,
    ) -> dict[str, list[dict[str, object]]]:
        segment_outputs: list[dict[str, object]] = []
        artifacts: list[dict[str, object]] = []

        for candidate in run.segments:
            if candidate.id == segment.id:
                continue
            summary = self.build_segment_summary(candidate)
            artifact_names = {
                str(artifact.get("name") or "")
                for artifact in summary.get("artifacts", [])
                if isinstance(artifact, dict)
            }
            segment_title = _segment_title(candidate, summary)

            for output in summary.get("outputs", []):
                if not isinstance(output, dict):
                    continue
                name = str(output.get("name") or "")
                if not name or name in artifact_names:
                    continue
                segment_outputs.append(
                    {
                        "id": f"{candidate.id}:{name}",
                        "source_type": "segment_output",
                        "source_ref": f"{candidate.id}.outputs.{name}",
                        "segment_id": candidate.id,
                        "segment_title": segment_title,
                        "name": name,
                        "value_type": str(output.get("type") or "string"),
                        "preview": str(output.get("description") or ""),
                    }
                )

            for artifact in summary.get("artifacts", []):
                if not isinstance(artifact, dict):
                    continue
                artifact_name = str(artifact.get("name") or "")
                artifact_id = str(artifact.get("id") or artifact_name)
                if not artifact_name or not artifact_id:
                    continue
                artifact_type = str(artifact.get("type") or "json")
                artifacts.append(
                    {
                        "id": artifact_id,
                        "source_type": "artifact",
                        "source_ref": f"artifact:{artifact_id}",
                        "segment_id": candidate.id,
                        "segment_title": segment_title,
                        "name": artifact_name,
                        "value_type": "file" if artifact_type == "file" else ("string" if artifact_type == "text" else artifact_type),
                        "preview": _artifact_preview(artifact),
                    }
                )

        recommended = list(reversed((segment_outputs[-3:] + artifacts[-3:])[-4:]))
        return {
            "recommended": recommended,
            "segment_outputs": segment_outputs,
            "artifacts": artifacts,
            "workflow_params": [],
        }

    def update_segment_bindings(
        self,
        run: RecordingRun,
        segment: RecordingSegment,
        inputs: list[dict[str, Any]],
    ) -> None:
        normalized_inputs = [
            dict(item)
            for item in inputs
            if isinstance(item, dict) and str(item.get("name") or "")
        ]
        segment.exports["inputs"] = normalized_inputs

        for item in normalized_inputs:
            input_name = str(item.get("name") or "")
            source_type = str(item.get("source") or "")
            source_ref = str(item.get("source_ref") or "")
            input_type = str(item.get("type") or "string")

            if source_type == "segment_output":
                binding = _parse_segment_output_ref(source_ref)
                if binding is not None:
                    source_segment_id, output_name = binding
                    source_segment = next((candidate for candidate in run.segments if candidate.id == source_segment_id), None)
                    if source_segment is not None:
                        source_outputs = [
                            dict(output)
                            for output in source_segment.exports.get("outputs", [])
                            if isinstance(output, dict) and str(output.get("name") or "") != output_name
                        ]
                        source_outputs.append(
                            {
                                "name": output_name,
                                "type": input_type,
                                "description": f"{source_segment.intent} 的输出变量 {output_name}",
                            }
                        )
                        source_segment.exports["outputs"] = source_outputs
                        _bind_rpa_output_result_key(source_segment, output_name)

            if source_type in {"segment_output", "artifact", "workflow_param"}:
                _seed_rpa_input_param(segment, input_name, str(item.get("description") or ""))

        run.updated_at = datetime.now()

    def should_open_workbench(self, kind: str, requires_user_interaction: bool) -> bool:
        return kind in {"rpa", "mcp"} and requires_user_interaction


def _infer_segment_inputs(segment: RecordingSegment) -> list[dict[str, Any]]:
    params = segment.exports.get("params") if isinstance(segment.exports, dict) else {}
    if not isinstance(params, dict):
        return []

    inputs: list[dict[str, Any]] = []
    for name, config in params.items():
        if not isinstance(name, str) or not name:
            continue
        config_dict = config if isinstance(config, dict) else {}
        sensitive = bool(config_dict.get("sensitive"))
        inputs.append(
            {
                "name": name,
                "type": "secret" if sensitive else "string",
                "required": False,
                "source": "credential" if sensitive else "user",
                "description": str(config_dict.get("description") or f"参数 {name}"),
                "default": None if sensitive else config_dict.get("original_value"),
            }
        )
    return inputs


def _normalize_summary_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        step_copy = dict(step)
        step_copy.setdefault("step_index", index)
        normalized_steps.append(step_copy)
    return normalized_steps


def _infer_segment_outputs(segment: RecordingSegment) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for step in segment.steps:
        if not isinstance(step, dict):
            continue
        result_key = step.get("result_key")
        if step.get("action") != "extract_text" or not isinstance(result_key, str) or not result_key:
            continue
        if result_key in seen_names:
            continue
        seen_names.add(result_key)
        outputs.append(
            {
                "name": result_key,
                "type": "string",
                "description": step.get("description") or f"{segment.intent} 的提取结果 {result_key}",
            }
        )

    for artifact in segment.artifacts:
        name = artifact.name or "artifact"
        if name in seen_names:
            continue
        seen_names.add(name)
        outputs.append(
            {
                "name": name,
                "type": "file" if artifact.type == "file" else ("string" if artifact.type == "text" else "json"),
                "description": f"{segment.intent} 产生的产物 {name}",
                "artifact_ref": artifact.id,
            }
        )

    return outputs


def _segment_title(segment: RecordingSegment, summary: dict[str, object]) -> str:
    title = summary.get("title") if isinstance(summary, dict) else None
    if isinstance(title, str) and title:
        return title
    if segment.intent:
        return segment.intent
    return segment.id


def _artifact_preview(artifact: dict[str, Any]) -> str:
    path = artifact.get("path")
    if isinstance(path, str) and path:
        return path
    value = artifact.get("value")
    if value in (None, ""):
        return ""
    return str(value)


def _parse_segment_output_ref(source_ref: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(?P<segment_id>[^.]+)\.outputs\.(?P<output_name>.+)", source_ref or "")
    if match is None:
        return None
    return match.group("segment_id"), match.group("output_name")


def _bind_rpa_output_result_key(segment: RecordingSegment, output_name: str) -> None:
    if segment.kind != "rpa":
        return
    extract_steps = [
        step for step in segment.steps
        if isinstance(step, dict) and step.get("action") == "extract_text"
    ]
    if not extract_steps:
        return
    if any(step.get("result_key") == output_name for step in extract_steps):
        return
    candidate = next((step for step in reversed(extract_steps) if not step.get("result_key")), extract_steps[-1])
    candidate["result_key"] = output_name


def _seed_rpa_input_param(segment: RecordingSegment, input_name: str, description: str = "") -> None:
    if segment.kind != "rpa":
        return
    params = dict(segment.exports.get("params") or {})
    if input_name in params and isinstance(params[input_name], dict):
        segment.exports["params"] = params
        return

    original_value = None
    for step in segment.steps:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        if action == "fill":
            original_value = step.get("value") or ""
            break
        if action == "set_input_files":
            signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
            payload = signals.get("set_input_files") if isinstance(signals, dict) else None
            files = payload.get("files") if isinstance(payload, dict) else None
            if isinstance(files, list) and files:
                original_value = files[0]
                break
            if step.get("value") not in (None, ""):
                original_value = step.get("value")
                break

    if original_value is None:
        return
    params[input_name] = {
        "original_value": original_value,
        "sensitive": False,
        "description": description or f"参数 {input_name}",
    }
    segment.exports["params"] = params
