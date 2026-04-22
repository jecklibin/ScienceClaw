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


def test_begin_testing_is_idempotent_when_run_is_already_testing():
    from backend.recording.orchestrator import RecordingOrchestrator

    orchestrator = RecordingOrchestrator()
    run = RecordingRun(id="run-1", session_id="session-1", user_id="u1")
    run.status = "testing"
    run.testing = {"status": "passed"}

    orchestrator.begin_testing(run)

    assert run.status == "testing"
    assert run.testing == {"status": "running"}


def test_begin_testing_can_restart_after_publish_is_prepared():
    from backend.recording.orchestrator import RecordingOrchestrator

    orchestrator = RecordingOrchestrator()
    run = RecordingRun(id="run-1", session_id="session-1", user_id="u1")
    run.status = "ready_to_publish"
    run.testing = {"status": "passed"}

    orchestrator.begin_testing(run)

    assert run.status == "testing"
    assert run.testing == {"status": "running"}


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


def test_build_test_payload_switches_to_workflow_mode_for_multi_segment_run():
    run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="mixed")
    first = RecordingSegment(
        id="seg-1",
        run_id="run-1",
        kind="rpa",
        intent="download report",
        steps=[{"id": "step-1", "action": "click"}],
    )
    first.exports.update(
        {
            "rpa_session_id": "rpa-1",
            "title": "下载报表",
            "description": "下载业务报表",
            "params": {"report_name": {"original_value": "orders.xlsx"}},
            "testing_status": "passed",
        }
    )
    second = RecordingSegment(
        id="seg-2",
        run_id="run-1",
        kind="script",
        intent="convert report",
    )
    second.exports.update(
        {
            "title": "转换报表",
            "description": "把 Excel 转成 CSV",
            "script": "def run(context):\n    return {'converted_csv': 'out.csv'}\n",
            "entry": "segments/seg-2_script.py",
            "inputs": [{"name": "source_file", "type": "file"}],
            "outputs": [{"name": "converted_csv", "type": "file"}],
            "testing_status": "passed",
        }
    )
    run.segments.extend([first, second])

    payload = asyncio.run(build_test_payload(run))

    assert payload["mode"] == "workflow"
    assert payload["run_id"] == "run-1"
    assert [segment["segment_id"] for segment in payload["segments"]] == ["seg-1", "seg-2"]
    assert payload["segments"][0]["rpa_session_id"] == "rpa-1"
    assert payload["segments"][1]["kind"] == "script"


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
