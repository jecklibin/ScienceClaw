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
                kind="script",
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
                    "inputs": [
                        {"name": "report_date", "type": "string", "description": "报表日期"},
                    ],
                },
            ),
        ]

        result = asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))

        assert result.prompt_kind == "skill"
        staged_dir = Path(result.staging_paths[0])
        assert (staged_dir / "SKILL.md").is_file()
        assert (staged_dir / "workflow.json").is_file()
        assert (staged_dir / "params.json").is_file()
        assert not (staged_dir / "params.schema.json").exists()
        assert not (staged_dir / "credentials.example.json").exists()
        assert (staged_dir / "segments" / "segment-1_rpa.py").is_file()
        assert (staged_dir / "segments" / "segment-2_transform.py").is_file()

        workflow = json.loads((staged_dir / "workflow.json").read_text(encoding="utf-8"))
        assert [segment["id"] for segment in workflow["segments"]] == ["segment-1", "segment-2"]
        assert workflow["segments"][0]["kind"] == "rpa"
        assert workflow["segments"][1]["kind"] == "script"

        params = json.loads((staged_dir / "params.json").read_text(encoding="utf-8"))
        assert params["report_date"]["original_value"] == "2026-04-21"
        assert params["report_date"]["description"] == "报表日期"

        skill_md = (staged_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "下载报表" in skill_md
        assert "转换报表" in skill_md

        runner = (staged_dir / "skill.py").read_text(encoding="utf-8")
        assert "async def execute_skill(page, **kwargs):" in runner


def test_publish_skill_parameterizes_bound_rpa_segment_input():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-2", session_id="session-1", user_id="u1", type="mixed")
        run.publish_target = "skill"
        run.segments = [
            RecordingSegment(
                id="segment-a",
                run_id="run-2",
                kind="rpa",
                intent="获取项目名称",
                status="completed",
                steps=[
                    {
                        "id": "step-a1",
                        "action": "navigate",
                        "target": "https://github.com/trending",
                        "url": "",
                    },
                    {
                        "id": "step-a2",
                        "action": "extract_text",
                        "target": '{"method":"css","value":"h2 a"}',
                    },
                ],
                exports={
                    "title": "获取项目名称",
                    "testing_status": "passed",
                    "outputs": [
                        {"name": "project_name", "type": "string", "description": "项目名称"},
                    ],
                },
            ),
            RecordingSegment(
                id="segment-b",
                run_id="run-2",
                kind="rpa",
                intent="搜索项目名称",
                status="completed",
                steps=[
                    {
                        "id": "step-b1",
                        "action": "goto",
                        "target": "https://www.runoob.com",
                        "url": "",
                    },
                    {
                        "id": "step-b2",
                        "action": "fill",
                        "target": '{"method":"role","role":"textbox","name":"搜索"}',
                        "value": "",
                    },
                ],
                exports={
                    "title": "搜索项目名称",
                    "testing_status": "passed",
                    "inputs": [
                        {
                            "name": "search",
                            "type": "string",
                            "required": True,
                            "source": "segment_output",
                            "source_ref": "segment-a.outputs.project_name",
                            "description": "上一段提取的项目名称",
                        },
                    ],
                },
            ),
        ]

        result = asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))

        staged_dir = Path(result.staging_paths[0])
        first_segment = (staged_dir / "segments" / "segment-a_rpa.py").read_text(encoding="utf-8")
        second_segment = (staged_dir / "segments" / "segment-b_rpa.py").read_text(encoding="utf-8")
        assert 'await current_page.goto("https://github.com/trending")' in first_segment
        assert '_results["project_name"]' in first_segment
        assert 'await current_page.goto("https://www.runoob.com")' in second_segment
        assert "kwargs.get('search', '')" in second_segment


def test_publish_skill_uses_structured_navigation_url_and_named_output():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-3", session_id="session-1", user_id="u1", type="rpa")
        run.publish_target = "skill"
        run.segments = [
            RecordingSegment(
                id="segment-desc-url",
                run_id="run-3",
                kind="rpa",
                intent="获取项目名称",
                status="completed",
                steps=[
                    {
                        "id": "step-1",
                        "action": "navigate",
                        "target": "",
                        "url": "https://github.com/trending",
                        "description": "导航到 https://github.com/trending",
                    },
                    {
                        "id": "step-2",
                        "action": "extract_text",
                        "target": '{"method":"css","value":"h2 a"}',
                        "description": "提取趋势列表第一个项目名称",
                    },
                ],
                exports={
                    "title": "获取项目名称",
                    "testing_status": "passed",
                    "outputs": [
                        {"name": "project_name", "type": "string", "description": "项目名称"},
                    ],
                },
            )
        ]

        result = asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))

        staged_dir = Path(result.staging_paths[0])
        segment_script = (staged_dir / "segments" / "segment-desc-url_rpa.py").read_text(encoding="utf-8")
        assert 'await current_page.goto("https://github.com/trending")' in segment_script
        assert '_results["project_name"]' in segment_script
        assert '_results["extract_text_1"]' not in segment_script


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
