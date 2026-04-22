from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import RecordingRun


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


async def execute_workflow_test(skill_dir: Path) -> dict[str, Any]:
    command = [sys.executable, "skill.py"]
    completed = subprocess.run(
        command,
        cwd=str(skill_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
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

    success = completed.returncode == 0 and "SKILL_SUCCESS" in stdout
    return {
        "success": success,
        "returncode": completed.returncode,
        "logs": logs,
        "stdout": stdout,
        "stderr": stderr,
        "result": output_payload or {},
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
    execution_result = await execute_workflow_test(Path(write_result["skill_dir"]))
    return {
        "skill_dir": write_result["skill_dir"],
        "workflow_path": write_result["workflow_path"],
        "segments": [segment.model_dump(mode="json") for segment in workflow_run.segments],
        "result": execution_result,
    }


def map_failed_step_to_repair_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "failed_step_index": result.get("failed_step_index"),
        "failed_step_candidates": result.get("failed_step_candidates", []),
        "error": result.get("error", ""),
    }
