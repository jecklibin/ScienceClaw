from __future__ import annotations

import uuid
from datetime import datetime

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
        move_run_status(run, "testing")
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
        move_run_status(run, "ready_to_publish")

    def build_segment_summary(self, segment: RecordingSegment) -> dict[str, object]:
        return {
            "segment_id": segment.id,
            "intent": segment.intent,
            "kind": segment.kind,
            "status": segment.status,
            "artifacts": [artifact.model_dump(mode="json") for artifact in segment.artifacts],
            "steps": segment.steps,
        }

    def should_open_workbench(self, kind: str, requires_user_interaction: bool) -> bool:
        return kind in {"rpa", "mcp"} and requires_user_interaction
