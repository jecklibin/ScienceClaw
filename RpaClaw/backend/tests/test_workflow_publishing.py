import json
import tempfile
from pathlib import Path

from backend.workflow.models import SkillPublishDraft, WorkflowRun, WorkflowSegment
from backend.workflow.publishing import build_publish_draft, write_skill_artifacts


def _sample_run() -> WorkflowRun:
    return WorkflowRun(
        id="run_1",
        session_id="session_1",
        user_id="user_1",
        intent="下载并转换文件",
        segments=[
            WorkflowSegment(
                id="segment_1",
                run_id="run_1",
                kind="rpa",
                order=1,
                title="下载报表",
                purpose="从网页下载 Excel 报表",
                status="tested",
                outputs=[
                    {
                        "name": "downloaded_file",
                        "type": "file",
                        "description": "下载得到的 Excel 文件",
                    }
                ],
                config={
                    "steps": [
                        {"id": "step_1", "action": "goto", "target": "https://example.com"},
                        {"id": "step_2", "action": "click", "target": "Download"},
                    ]
                },
            ),
            WorkflowSegment(
                id="segment_2",
                run_id="run_1",
                kind="script",
                order=2,
                title="转换报表",
                purpose="将下载文件转换为 CSV",
                status="tested",
                inputs=[
                    {
                        "name": "source_file",
                        "type": "file",
                        "required": True,
                        "source": "segment_output",
                        "source_ref": "segment_1.outputs.downloaded_file",
                        "description": "第一段下载的文件",
                    }
                ],
                outputs=[
                    {
                        "name": "converted_csv",
                        "type": "file",
                        "description": "转换后的 CSV 文件",
                    }
                ],
                config={
                    "language": "python",
                    "entry": "segments/segment_2_transform.py",
                    "source": "def run(context):\n    return {'converted_csv': 'output.csv'}\n",
                },
            ),
        ],
    )


def test_build_publish_draft_uses_workflow_metadata():
    draft = build_publish_draft(_sample_run(), publish_target="skill")

    assert draft.skill_name == "下载并转换文件"
    assert draft.display_title == "下载并转换文件"
    assert "下载报表" in draft.description
    assert [segment.id for segment in draft.segments] == ["segment_1", "segment_2"]
    assert not draft.warnings


def test_write_skill_artifacts_generates_complete_skill_directory():
    run = _sample_run()
    draft = SkillPublishDraft(
        id="draft_run_1",
        run_id="run_1",
        publish_target="skill",
        skill_name="download_and_convert_report",
        display_title="下载并转换业务报表",
        description="自动下载业务报表并转换为 CSV。",
        trigger_examples=["帮我下载并转换业务报表"],
        inputs=[],
        outputs=[],
        credentials=[],
        segments=build_publish_draft(run).segments,
        warnings=[],
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        skill_dir = Path(result["skill_dir"])

        assert (skill_dir / "SKILL.md").is_file()
        assert (skill_dir / "skill.py").is_file()
        assert (skill_dir / "workflow.json").is_file()
        assert (skill_dir / "params.schema.json").is_file()
        assert (skill_dir / "credentials.example.json").is_file()
        assert (skill_dir / "segments" / "segment_1_rpa.json").is_file()
        assert (skill_dir / "segments" / "segment_2_transform.py").is_file()

        skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "name: download_and_convert_report" in skill_md
        assert "自动下载业务报表并转换为 CSV。" in skill_md
        assert "下载报表" in skill_md
        assert "转换报表" in skill_md

        workflow = json.loads((skill_dir / "workflow.json").read_text(encoding="utf-8"))
        assert [segment["id"] for segment in workflow["segments"]] == ["segment_1", "segment_2"]
        assert workflow["segments"][1]["entry"] == "segments/segment_2_transform.py"

        runner = (skill_dir / "skill.py").read_text(encoding="utf-8")
        assert "def run(**kwargs):" in runner
        assert "run_rpa_segment" in runner
        assert "run_script_segment" in runner
