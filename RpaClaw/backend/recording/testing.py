from __future__ import annotations

import json
import locale
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import RecordingRun


def _decode_subprocess_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value

    encodings: list[str] = []
    for encoding in ("utf-8", locale.getpreferredencoding(False), "gb18030", "cp936"):
        if encoding and encoding not in encodings:
            encodings.append(encoding)

    for encoding in encodings:
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue

    return value.decode(encodings[0] if encodings else "utf-8", errors="replace")


async def build_test_payload(run: RecordingRun) -> dict[str, Any]:
    if len(run.segments) > 1:
        return {
            "mode": "workflow",
            "run_id": run.id,
            "chat_session_id": run.session_id,
            "type": run.type,
            "segments": [
                {
                    "segment_id": segment.id,
                    "kind": segment.kind,
                    "title": exports.get("title") or segment.intent,
                    "description": exports.get("description") or "",
                    "rpa_session_id": exports.get("rpa_session_id"),
                    "params": exports.get("params") or {},
                    "inputs": exports.get("inputs") or [],
                    "outputs": exports.get("outputs") or [],
                    "testing_status": exports.get("testing_status") or "idle",
                }
                for segment in run.segments
                for exports in [segment.exports or {}]
            ],
            "artifacts": [
                artifact.model_dump(mode="json")
                for artifact in run.artifact_index
            ],
        }

    latest_segment = run.segments[-1] if run.segments else None
    exports = latest_segment.exports if latest_segment else {}
    return {
        "mode": "segment",
        "run_id": run.id,
        "chat_session_id": run.session_id,
        "rpa_session_id": exports.get("rpa_session_id"),
        "segment_id": latest_segment.id if latest_segment else None,
        "type": run.type,
        "steps": latest_segment.steps if latest_segment else [],
        "title": exports.get("title") or (latest_segment.intent if latest_segment else ""),
        "description": exports.get("description") or "",
        "params": exports.get("params") or {},
        "artifacts": [
            artifact.model_dump(mode="json")
            for artifact in (latest_segment.artifacts if latest_segment else run.artifact_index)
        ],
    }


async def execute_workflow_test(
    skill_dir: Path,
    *,
    user_id: str | None = None,
    extra_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "_downloads_dir": str(skill_dir / "downloads"),
        "_skill_dir": str(skill_dir),
        "_workspace_dir": str(skill_dir),
    }
    kwargs.update(extra_args or {})
    Path(str(kwargs["_downloads_dir"])).mkdir(parents=True, exist_ok=True)
    if user_id:
        params_path = skill_dir / "params.json"
        if params_path.is_file():
            from backend.credential.vault import inject_credentials

            params_config = json.loads(params_path.read_text(encoding="utf-8"))
            kwargs = await inject_credentials(user_id, params_config, kwargs)

    command = [sys.executable, "skill.py"]
    command.extend(f"--{key}={value}" for key, value in kwargs.items() if value is not None)
    completed = subprocess.run(
        command,
        cwd=str(skill_dir),
        capture_output=True,
        text=False,
    )
    stdout = _decode_subprocess_output(completed.stdout)
    stderr = _decode_subprocess_output(completed.stderr)
    logs = [line for line in stdout.splitlines() if line.strip()]
    if stderr.strip():
        logs.extend(line for line in stderr.splitlines() if line.strip())

    output_payload: dict[str, Any] | None = None
    for line in stdout.splitlines():
        if line.startswith("SKILL_DATA:"):
            raw = line.removeprefix("SKILL_DATA:")
            try:
                output_payload = json.loads(raw)
            except json.JSONDecodeError:
                output_payload = {"raw": raw}

    contract = validate_workflow_contract(skill_dir, output_payload or {})
    success = completed.returncode == 0 and "SKILL_SUCCESS" in stdout and contract["success"]
    return {
        "success": success,
        "returncode": completed.returncode,
        "logs": logs,
        "stdout": stdout,
        "stderr": stderr,
        "result": output_payload or {},
        "contract": contract,
        "command": command,
    }


async def execute_recording_workflow_test(run: RecordingRun, workspace_dir: str | Path) -> dict[str, Any]:
    from backend.workflow.publishing import build_publish_draft, write_skill_artifacts
    from backend.workflow.recording_adapter import recording_run_to_workflow

    workflow_run = recording_run_to_workflow(run)
    draft = build_publish_draft(
        workflow_run,
        publish_target=(run.publish_target or run.save_intent or "skill"),
    )
    test_root = Path(workspace_dir) / run.session_id / "workflow_test_runs"
    write_result = write_skill_artifacts(workflow_run, draft, test_root)
    execution_result = await execute_workflow_test(
        Path(write_result["skill_dir"]),
        user_id=run.user_id,
        extra_args={
            "_workspace_dir": str(Path(workspace_dir) / run.session_id),
        },
    )
    repair_context = build_recording_test_repair_context(
        run,
        Path(write_result["skill_dir"]),
        Path(write_result["workflow_path"]),
        execution_result,
    )
    return {
        "skill_dir": write_result["skill_dir"],
        "workflow_path": write_result["workflow_path"],
        "segments": [segment.model_dump(mode="json") for segment in workflow_run.segments],
        "result": execution_result,
        "repair_context": repair_context,
    }


def map_failed_step_to_repair_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "failed_step_index": result.get("failed_step_index"),
        "failed_step_candidates": result.get("failed_step_candidates", []),
        "error": result.get("error", ""),
    }


def validate_workflow_contract(skill_dir: Path, result_payload: dict[str, Any]) -> dict[str, Any]:
    workflow_path = skill_dir / "workflow.json"
    if not workflow_path.is_file():
        return {"success": True, "segment_results": []}

    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"success": True, "segment_results": []}

    outputs_by_segment = result_payload.get("outputs")
    if not isinstance(outputs_by_segment, dict):
        outputs_by_segment = {}
    artifacts = result_payload.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}

    segment_results: list[dict[str, Any]] = []
    for segment in workflow.get("segments", []):
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("id") or "")
        if not segment_id:
            continue
        declared_outputs = segment.get("outputs")
        if not isinstance(declared_outputs, list) or not declared_outputs:
            continue

        actual_outputs = outputs_by_segment.get(segment_id)
        if not isinstance(actual_outputs, dict):
            actual_outputs = {}
        missing_outputs: list[str] = []
        missing_artifacts: list[str] = []

        for output_spec in declared_outputs:
            if not isinstance(output_spec, dict):
                continue
            output_name = str(output_spec.get("name") or "")
            if not output_name:
                continue
            artifact_ref = str(output_spec.get("artifact_ref") or "")
            if artifact_ref:
                artifact_value = artifacts.get(artifact_ref)
                if artifact_value is None and actual_outputs.get(output_name) is None:
                    missing_artifacts.append(artifact_ref)
                continue
            if output_name not in actual_outputs or actual_outputs.get(output_name) is None:
                missing_outputs.append(output_name)

        if missing_outputs or missing_artifacts:
            segment_results.append(
                {
                    "segment_id": segment_id,
                    "title": str(segment.get("title") or segment_id),
                    "kind": str(segment.get("kind") or ""),
                    "missing_outputs": missing_outputs,
                    "missing_artifacts": missing_artifacts,
                    "status": "failed",
                }
            )

    return {
        "success": not segment_results,
        "segment_results": segment_results,
    }


def build_recording_test_repair_context(
    run: RecordingRun,
    skill_dir: Path,
    workflow_path: Path,
    execution_result: dict[str, Any],
) -> dict[str, Any]:
    contract = execution_result.get("contract")
    if not isinstance(contract, dict):
        contract = {"success": True, "segment_results": []}
    segment_results = contract.get("segment_results")
    if not isinstance(segment_results, list):
        segment_results = []

    missing_outputs = [
        str(name)
        for item in segment_results
        if isinstance(item, dict)
        for name in item.get("missing_outputs", [])
        if str(name or "")
    ]
    missing_artifacts = [
        str(name)
        for item in segment_results
        if isinstance(item, dict)
        for name in item.get("missing_artifacts", [])
        if str(name or "")
    ]
    context_path = skill_dir / "recording_test_context.json"
    payload = {
        "run_id": run.id,
        "session_id": run.session_id,
        "skill_dir": str(skill_dir),
        "workflow_path": str(workflow_path),
        "segments": [segment.model_dump(mode="json") for segment in run.segments],
        "execution_result": execution_result,
        "missing_outputs": missing_outputs,
        "missing_artifacts": missing_artifacts,
    }
    context_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "skill_dir": str(skill_dir),
        "workflow_path": str(workflow_path),
        "context_path": str(context_path),
        "missing_outputs": missing_outputs,
        "missing_artifacts": missing_artifacts,
    }
