import asyncio
import json
import tempfile
from pathlib import Path
import pytest

from backend.recording.models import RecordingRun, RecordingSegment
from backend.recording.publishing import build_mcp_tool_artifacts, build_publish_artifacts
from backend.workflow.models import PublishInput
from backend.workflow.publishing import build_publish_draft
from backend.workflow.recording_adapter import recording_run_to_workflow


class _FakeMcpRegistry:
    def __init__(self):
        self.saved = []

    async def save(self, tool):
        self.saved.append(tool)
        return tool


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


def test_recording_run_to_workflow_normalizes_mixed_segment_with_steps_to_rpa():
    run = RecordingRun(id="run-mixed", session_id="session-1", user_id="u1", type="mixed")
    run.publish_target = "tool"
    run.segments = [
        RecordingSegment(
            id="segment-mixed",
            run_id="run-mixed",
            kind="mixed",
            intent="获取趋势项目",
            status="completed",
            steps=[
                {
                    "id": "step-1",
                    "action": "goto",
                    "url": "https://github.com/trending",
                    "description": "导航到 GitHub Trending",
                }
            ],
            exports={
                "title": "获取趋势项目",
                "description": "打开页面并继续后续提取",
                "testing_status": "passed",
            },
        )
    ]

    workflow = recording_run_to_workflow(run)

    assert workflow.segments[0].kind == "rpa"
    assert workflow.segments[0].config["source_recording_kind"] == "mixed"
    assert workflow.segments[0].config["steps"][0]["url"] == "https://github.com/trending"


def test_publish_tool_requires_recorded_steps():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="mcp")
        run.publish_target = "tool"

        with pytest.raises(ValueError, match="without recorded RPA/MCP steps"):
            asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))


def test_confirmed_tool_draft_saves_rpa_mcp_tool():
    run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="rpa")
    run.publish_target = "tool"
    run.segments = [
        RecordingSegment(
            id="segment-1",
            run_id=run.id,
            kind="rpa",
            intent="搜索项目",
            status="completed",
            steps=[
                {"action": "navigate", "url": "https://github.com/trending", "description": "打开 GitHub Trending"},
                {
                    "action": "extract_text",
                    "description": "提取第一个项目名称",
                    "target": "h2 a",
                    "locator": {"method": "css", "value": "h2 a"},
                    "result_key": "project_name",
                },
            ],
            exports={
                "rpa_session_id": "rpa-session-1",
                "title": "搜索项目",
                "description": "搜索并提取项目名称",
                "testing_status": "passed",
            },
        )
    ]
    draft = build_publish_draft(recording_run_to_workflow(run), publish_target="tool")
    draft.skill_name = "github_project_search"
    draft.display_title = "GitHub project search"
    registry = _FakeMcpRegistry()

    result = asyncio.run(build_mcp_tool_artifacts(run, draft, registry=registry))

    assert result.prompt_kind == "tool"
    assert result.summary["target"] == "mcp_tool"
    assert result.summary["saved"] is True
    assert result.summary["name"] == "github_project_search"
    assert result.staging_paths == [f"rpa-mcp:{result.summary['tool_id']}"]
    assert len(registry.saved) == 1
    assert registry.saved[0].source.session_id == "rpa-session-1"
    assert registry.saved[0].tool_name == "github_project_search"


def test_publish_tool_keeps_segment_output_bindings_in_workflow_package():
    run = RecordingRun(id="run-bound-tool", session_id="session-1", user_id="u1", type="mixed")
    run.publish_target = "tool"
    run.segments = [
        RecordingSegment(
            id="segment-a",
            run_id=run.id,
            kind="rpa",
            intent="Get project name",
            status="completed",
            steps=[
                {"id": "step-a1", "action": "navigate", "url": "https://github.com/trending"},
                {
                    "id": "step-a2",
                    "action": "extract_text",
                    "target": '{"method":"css","value":"h2 a"}',
                    "result_key": "project_name",
                },
            ],
            exports={
                "rpa_session_id": "rpa-session-1",
                "title": "Get project name",
                "description": "Extract the first project name.",
                "outputs": [
                    {"name": "project_name", "type": "string", "description": "Project name"},
                ],
                "testing_status": "passed",
            },
        ),
        RecordingSegment(
            id="segment-b",
            run_id=run.id,
            kind="rpa",
            intent="Search project",
            status="completed",
            steps=[
                {"id": "step-b1", "action": "navigate", "url": "https://www.runoob.com"},
                {
                    "id": "step-b2",
                    "action": "fill",
                    "target": '{"method":"role","role":"textbox","name":"search"}',
                    "value": "test",
                },
            ],
            exports={
                "rpa_session_id": "rpa-session-2",
                "title": "Search project",
                "description": "Search with the previous segment output.",
                "params": {
                    "keyword": {
                        "original_value": "test",
                        "type": "string",
                        "description": "Search keyword",
                        "required": True,
                        "sensitive": False,
                    }
                },
                "inputs": [
                    {
                        "name": "keyword",
                        "type": "string",
                        "required": True,
                        "source": "segment_output",
                        "source_ref": "segment-a.outputs.project_name",
                        "description": "Search keyword",
                    },
                ],
                "testing_status": "passed",
            },
        ),
    ]
    draft = build_publish_draft(recording_run_to_workflow(run), publish_target="tool")
    draft.skill_name = "bound_project_search"
    draft.inputs.append(
        PublishInput(
            name="keyword",
            type="string",
            required=True,
            description="Stale public input from before binding",
            default="test",
        )
    )
    registry = _FakeMcpRegistry()

    asyncio.run(build_mcp_tool_artifacts(run, draft, registry=registry))

    saved = registry.saved[0]
    second_segment = saved.workflow_package["workflow"]["segments"][1]
    assert second_segment["inputs"][0]["source_ref"] == "segment-a.outputs.project_name"
    assert "keyword" not in saved.input_schema["properties"]
    assert "keyword" not in saved.params
