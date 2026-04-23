import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.recording.lifecycle import move_run_status
from backend.recording.models import RecordingRun, RecordingSegment
from backend.recording.testing import build_test_payload, execute_workflow_test, map_failed_step_to_repair_payload


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
            intent="涓嬭浇 PDF",
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
            "title": "涓嬭浇鎶ヨ〃",
            "description": "涓嬭浇涓氬姟鎶ヨ〃",
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
            "title": "杞崲鎶ヨ〃",
            "description": "鎶?Excel 杞垚 CSV",
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


@pytest.mark.anyio
async def test_execute_workflow_test_injects_credentials_and_downloads_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        skill_dir = Path(tmp_dir) / "skill"
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text("print('SKILL_SUCCESS')\n", encoding="utf-8")
        (skill_dir / "params.json").write_text(
            json.dumps(
                {
                    "password": {
                        "sensitive": True,
                        "credential_id": "cred-login",
                        "original_value": "{{credential}}",
                    }
                }
            ),
            encoding="utf-8",
        )

        captured: dict[str, object] = {}

        async def fake_inject_credentials(user_id, params, kwargs):
            captured["user_id"] = user_id
            captured["params"] = params
            captured["incoming_kwargs"] = dict(kwargs)
            return {**kwargs, "password": "secret-value"}

        def fake_run(command, cwd, capture_output, text):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["text"] = text
            return subprocess.CompletedProcess(command, 0, "SKILL_SUCCESS\n", "")

        monkeypatch.setattr("backend.credential.vault.inject_credentials", fake_inject_credentials)
        monkeypatch.setattr(subprocess, "run", fake_run)

        result = await execute_workflow_test(skill_dir, user_id="user-1")

        command = captured["command"]
        assert result["success"] is True
        assert captured["user_id"] == "user-1"
        assert captured["incoming_kwargs"] == {
            "_downloads_dir": str(skill_dir / "downloads"),
            "_skill_dir": str(skill_dir),
            "_workspace_dir": str(skill_dir),
        }
        assert isinstance(command, list)
        assert captured["cwd"] == str(skill_dir)
        assert captured["text"] is False
        assert f"--_downloads_dir={skill_dir / 'downloads'}" in command
        assert f"--_skill_dir={skill_dir}" in command
        assert f"--_workspace_dir={skill_dir}" in command
        assert "--password=secret-value" in command


@pytest.mark.anyio
async def test_execute_workflow_test_tolerates_non_utf8_subprocess_output(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        skill_dir = Path(tmp_dir) / "skill"
        skill_dir.mkdir()
        (skill_dir / "skill.py").write_text("print('SKILL_SUCCESS')\n", encoding="utf-8")

        captured: dict[str, object] = {}

        def fake_run(command, cwd, capture_output, text):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["text"] = text
            return SimpleNamespace(
                returncode=1,
                stdout="SKILL_ERROR: failed\n".encode("utf-8"),
                stderr="閿欒锛氬瘑鐮佺己澶盶n".encode("gbk", errors="replace"),
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = await execute_workflow_test(skill_dir)

        assert result["success"] is False
        assert captured["text"] is False
        assert isinstance(result["stderr"], str)
        assert result["stderr"].strip()
        assert any(result["stderr"].strip() in line for line in result["logs"])


@pytest.mark.anyio
async def test_execute_workflow_test_fails_when_declared_output_is_missing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        skill_dir = Path(tmp_dir) / "skill"
        segments_dir = skill_dir / "segments"
        segments_dir.mkdir(parents=True)

        (skill_dir / "workflow.json").write_text(
            json.dumps(
                {
                    "segments": [
                        {
                            "id": "segment-script",
                            "kind": "script",
                            "entry": "segments/segment-script.py",
                            "outputs": [
                                {
                                    "name": "markdown_text",
                                    "type": "string",
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (skill_dir / "params.json").write_text("{}", encoding="utf-8")
        (skill_dir / "skill.py").write_text(
            "\n".join(
                [
                    "import json",
                    "print('SKILL_DATA:' + json.dumps({",
                    "    'status': 'success',",
                    "    'outputs': {'segment-script': {}},",
                    "    'artifacts': {},",
                    "}, ensure_ascii=False))",
                    "print('SKILL_SUCCESS')",
                ]
            ),
            encoding="utf-8",
        )

        result = await execute_workflow_test(skill_dir)

        assert result["success"] is False
        assert result["contract"]["success"] is False
        assert result["contract"]["segment_results"] == [
            {
                "segment_id": "segment-script",
                "title": "segment-script",
                "kind": "script",
                "missing_outputs": ["markdown_text"],
                "missing_artifacts": [],
                "status": "failed",
            }
        ]
