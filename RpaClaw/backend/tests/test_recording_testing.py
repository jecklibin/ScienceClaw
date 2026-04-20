import asyncio

import pytest

from backend.recording.lifecycle import move_run_status
from backend.recording.models import RecordingRun, RecordingSegment
from backend.recording.testing import build_test_payload, map_failed_step_to_repair_payload


def test_run_moves_from_ready_for_next_segment_to_testing():
    run = RecordingRun(id="run-1", session_id="session-1", user_id="u1")
    run.status = "ready_for_next_segment"

    move_run_status(run, "testing")

    assert run.status == "testing"


def test_invalid_lifecycle_transition_raises():
    run = RecordingRun(id="run-1", session_id="session-1", user_id="u1")

    with pytest.raises(ValueError):
        move_run_status(run, "ready_to_publish")


def test_build_test_payload_uses_latest_segment():
    run = RecordingRun(id="run-1", session_id="session-1", user_id="u1")
    run.segments.append(
        RecordingSegment(
            id="seg-1",
            run_id="run-1",
            kind="rpa",
            intent="下载 PDF",
            steps=[{"id": "step-1", "action": "click"}],
        )
    )

    payload = asyncio.run(build_test_payload(run))

    assert payload["run_id"] == "run-1"
    assert payload["segment_id"] == "seg-1"
    assert payload["steps"] == [{"id": "step-1", "action": "click"}]


def test_failed_step_result_maps_to_repair_payload():
    payload = map_failed_step_to_repair_payload(
        {
            "failed_step_index": 2,
            "failed_step_candidates": [{"kind": "css"}],
            "error": "locator failed",
        }
    )

    assert payload == {
        "failed_step_index": 2,
        "failed_step_candidates": [{"kind": "css"}],
        "error": "locator failed",
    }
