import json
import tempfile
from pathlib import Path

from backend.workflow.models import SegmentInput, SkillPublishDraft, WorkflowRun, WorkflowSegment
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


def test_build_publish_draft_warns_for_unbound_segment_input():
    run = _sample_run()
    run.segments[1].inputs = [
        SegmentInput(
            name="source_file",
            type="file",
            required=True,
            source="segment_output",
            description="第一段下载的文件",
        )
    ]

    draft = build_publish_draft(run, publish_target="skill")

    assert draft.warnings
    assert draft.warnings[0].code == "segment_input_unbound"
    assert draft.warnings[0].segment_id == "segment_2"


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
        assert (skill_dir / "params.json").is_file()
        assert not (skill_dir / "params.schema.json").exists()
        assert not (skill_dir / "credentials.example.json").exists()
        assert (skill_dir / "segments" / "segment_1_rpa.py").is_file()
        assert (skill_dir / "segments" / "segment_2_transform.py").is_file()

        skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "name: download_and_convert_report" in skill_md
        assert "python skill.py" in skill_md
        assert "自动下载业务报表并转换为 CSV。" in skill_md
        assert "下载报表" in skill_md
        assert "转换报表" in skill_md

        workflow = json.loads((skill_dir / "workflow.json").read_text(encoding="utf-8"))
        assert [segment["id"] for segment in workflow["segments"]] == ["segment_1", "segment_2"]
        assert workflow["segments"][0]["entry"] == "segments/segment_1_rpa.py"
        assert workflow["segments"][1]["entry"] == "segments/segment_2_transform.py"

        runner = (skill_dir / "skill.py").read_text(encoding="utf-8")
        assert "async def execute_skill(page, **kwargs):" in runner
        assert "await run_rpa_segment(" in runner
        assert "await _run_workflow(" in runner
        assert "def main()" in runner
        assert "run_script_segment" in runner
        assert "subprocess.run" not in runner

        rpa_segment = (skill_dir / "segments" / "segment_1_rpa.py").read_text(encoding="utf-8")
        assert "async def execute_segment(page, workflow_context=None, **kwargs):" in rpa_segment
        assert "workflow_context['current_page'] = current_page" in rpa_segment
        assert "async def main()" not in rpa_segment


def test_write_skill_artifacts_generates_params_json_for_defaults_and_credentials():
    run = WorkflowRun(
        id="run_params",
        session_id="session_1",
        user_id="user_1",
        intent="登录并下载",
        segments=[
            WorkflowSegment(
                id="segment_login",
                run_id="run_params",
                kind="rpa",
                order=1,
                title="登录系统",
                purpose="使用已保存凭证登录系统",
                status="tested",
                inputs=[
                    {
                        "name": "username",
                        "type": "string",
                        "required": False,
                        "source": "user",
                        "description": "登录用户名",
                        "default": "demo-user",
                    },
                    {
                        "name": "password",
                        "type": "secret",
                        "required": False,
                        "source": "credential",
                        "description": "登录密码",
                    },
                ],
                config={
                    "steps": [],
                    "params": {
                        "username": {
                            "type": "string",
                            "description": "登录用户名",
                            "original_value": "demo-user",
                            "sensitive": False,
                            "required": False,
                        },
                        "password": {
                            "type": "string",
                            "description": "登录密码",
                            "original_value": "{{credential}}",
                            "sensitive": True,
                            "credential_id": "cred-login",
                        },
                    },
                },
            )
        ],
    )
    draft = build_publish_draft(run)
    assert [credential.name for credential in draft.credentials] == ["password"]

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        skill_dir = Path(result["skill_dir"])

        params = json.loads((skill_dir / "params.json").read_text(encoding="utf-8"))
        assert params["username"]["original_value"] == "demo-user"
        assert params["username"]["required"] is False
        assert params["password"]["sensitive"] is True
        assert params["password"]["credential_id"] == "cred-login"
        assert params["username"]["description"] == "登录用户名"
        assert params["password"]["description"] == "登录密码"
        assert not (skill_dir / "params.schema.json").exists()


def test_script_workflow_runner_loads_defaults_from_params_json_without_schema():
    run = WorkflowRun(
        id="run_script_params",
        session_id="session_1",
        user_id="user_1",
        intent="转换报表",
        segments=[
            WorkflowSegment(
                id="segment_transform",
                run_id="run_script_params",
                kind="script",
                order=1,
                title="转换报表",
                purpose="使用报表日期转换文件",
                status="tested",
                inputs=[
                    {
                        "name": "report_date",
                        "type": "string",
                        "required": False,
                        "source": "user",
                        "description": "报表日期",
                        "default": "2026-04-21",
                    }
                ],
                outputs=[
                    {
                        "name": "used_date",
                        "type": "string",
                        "description": "实际使用的报表日期",
                    }
                ],
                config={
                    "language": "python",
                    "entry": "segments/segment_transform.py",
                    "source": "def run(context):\n    return {'used_date': context.params.get('report_date')}\n",
                },
            )
        ],
    )
    draft = build_publish_draft(run)

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        skill_dir = Path(result["skill_dir"])

        assert not (skill_dir / "params.schema.json").exists()
        namespace: dict[str, object] = {"__file__": str(skill_dir / "skill.py")}
        exec((skill_dir / "skill.py").read_text(encoding="utf-8"), namespace)

        run_skill = namespace["run"]
        default_result = run_skill()
        override_result = run_skill(report_date="2026-04-22")

        assert default_result["outputs"]["segment_transform"]["used_date"] == "2026-04-21"
        assert override_result["outputs"]["segment_transform"]["used_date"] == "2026-04-22"
