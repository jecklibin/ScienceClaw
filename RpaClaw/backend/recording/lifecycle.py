from __future__ import annotations

from datetime import datetime

from .models import RecordingRun


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"recording", "waiting_user", "failed"},
    "waiting_user": {"recording", "processing_artifacts", "ready_for_next_segment", "failed"},
    "recording": {"processing_artifacts", "failed"},
    "processing_artifacts": {"ready_for_next_segment", "testing", "failed"},
    "ready_for_next_segment": {"recording", "waiting_user", "testing", "ready_to_publish", "failed"},
    "testing": {"needs_repair", "ready_to_publish", "failed"},
    "needs_repair": {"testing", "failed"},
    "ready_to_publish": {"testing", "saved", "failed"},
    "failed": {"recording", "testing"},
}


def move_run_status(run: RecordingRun, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(run.status, set())
    if target not in allowed:
        raise ValueError(f"invalid transition: {run.status} -> {target}")
    run.status = target
    run.updated_at = datetime.now()
