from __future__ import annotations

from typing import Any

from .models import RecordingRun


async def build_test_payload(run: RecordingRun) -> dict[str, Any]:
    latest_segment = run.segments[-1] if run.segments else None
    return {
        "run_id": run.id,
        "session_id": run.session_id,
        "segment_id": latest_segment.id if latest_segment else None,
        "type": run.type,
        "steps": latest_segment.steps if latest_segment else [],
        "artifacts": [
            artifact.model_dump(mode="json")
            for artifact in (latest_segment.artifacts if latest_segment else run.artifact_index)
        ],
    }


def map_failed_step_to_repair_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "failed_step_index": result.get("failed_step_index"),
        "failed_step_candidates": result.get("failed_step_candidates", []),
        "error": result.get("error", ""),
    }
