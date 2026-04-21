import asyncio
import json
import tempfile
from pathlib import Path

from backend.recording.models import RecordingRun, RecordingSegment
from backend.recording.publishing import build_publish_artifacts


def test_publish_skill_builds_complete_multi_segment_skill():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="mixed")
        run.publish_target = "skill"
        run.segments = [
            RecordingSegment(
                id="segment-1",
                run_id="run-1",
                kind="rpa",
                intent="下载报表",
                status="completed",
                steps=[
                    {"id": "step-1", "action": "goto", "target": "https://example.com"},
                    {"id": "step-2", "action": "click", "target": "Download"},
                ],
                exports={
                    "title": "下载报表",
                    "description": "从网页下载 Excel 报表",
                    "testing_status": "passed",
                },
            ),
            RecordingSegment(
                id="segment-2",
                run_id="run-1",
                kind="chat_process",
                intent="转换下载文件",
                status="completed",
                exports={
                    "title": "转换报表",
                    "description": "将下载文件转换为 CSV",
                    "testing_status": "passed",
                    "script": "def run(context):\n    return {'converted_csv': 'output.csv'}\n",
                    "entry": "segments/segment-2_transform.py",
                    "params": {
                        "report_date": {
                            "original_value": "2026-04-21",
                            "sensitive": False,
                        }
                    },
                },
            ),
        ]

        result = asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))

        assert result.prompt_kind == "skill"
        staged_dir = Path(result.staging_paths[0])
        assert (staged_dir / "SKILL.md").is_file()
        assert (staged_dir / "workflow.json").is_file()
        assert (staged_dir / "params.schema.json").is_file()
        assert (staged_dir / "credentials.example.json").is_file()
        assert (staged_dir / "segments" / "segment-1_rpa.json").is_file()
        assert (staged_dir / "segments" / "segment-2_transform.py").is_file()

        workflow = json.loads((staged_dir / "workflow.json").read_text(encoding="utf-8"))
        assert [segment["id"] for segment in workflow["segments"]] == ["segment-1", "segment-2"]
        assert workflow["segments"][0]["kind"] == "rpa"
        assert workflow["segments"][1]["kind"] == "script"

        params_schema = json.loads((staged_dir / "params.schema.json").read_text(encoding="utf-8"))
        assert "report_date" in params_schema["properties"]

        skill_md = (staged_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "下载报表" in skill_md
        assert "转换报表" in skill_md


def test_publish_tool_builds_tool_staging_output():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="mcp")
        run.publish_target = "tool"

        result = asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))

        assert result.prompt_kind == "tool"
        assert result.staging_paths
        staged_file = Path(result.staging_paths[0])
        assert staged_file.parent.name == "tools_staging"
        assert staged_file.suffix == ".py"
