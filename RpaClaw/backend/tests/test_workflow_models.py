from backend.workflow.models import (
    CredentialRequirement,
    PublishInput,
    SkillPublishDraft,
    WorkflowRun,
    WorkflowSegment,
)


def test_workflow_segment_accepts_script_artifact_input():
    segment = WorkflowSegment(
        id="segment_2",
        run_id="run_1",
        kind="script",
        order=2,
        title="转换下载文件",
        purpose="将第一段下载的文件转换为 CSV",
        inputs=[
            {
                "name": "source_file",
                "type": "file",
                "required": True,
                "source": "segment_output",
                "source_ref": "segment_1.outputs.downloaded_file",
                "description": "第一段下载得到的文件",
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
        },
    )

    assert segment.kind == "script"
    assert segment.inputs[0].source_ref == "segment_1.outputs.downloaded_file"
    assert segment.config["entry"] == "segments/segment_2_transform.py"


def test_publish_draft_requires_final_skill_name_and_description():
    draft = SkillPublishDraft(
        id="draft_1",
        run_id="run_1",
        publish_target="skill",
        skill_name="download_and_convert_report",
        display_title="下载并转换业务报表",
        description="自动下载业务报表并转换为 CSV。",
        trigger_examples=["帮我下载并转换业务报表"],
        inputs=[
            PublishInput(
                name="report_date",
                type="string",
                required=False,
                description="报表日期",
            )
        ],
        outputs=[],
        credentials=[
            CredentialRequirement(
                name="business_system",
                type="browser_session",
                description="业务系统登录态",
            )
        ],
        segments=[],
        warnings=[],
    )

    assert draft.skill_name == "download_and_convert_report"
    assert draft.description.startswith("自动下载")
    assert draft.credentials[0].type == "browser_session"


def test_workflow_run_keeps_segments_in_order():
    run = WorkflowRun(
        id="run_1",
        session_id="session_1",
        user_id="user_1",
        intent="下载并转换文件",
        segments=[
            WorkflowSegment(
                id="segment_2",
                run_id="run_1",
                kind="script",
                order=2,
                title="转换文件",
                purpose="将下载文件转换为 CSV",
            ),
            WorkflowSegment(
                id="segment_1",
                run_id="run_1",
                kind="rpa",
                order=1,
                title="下载文件",
                purpose="从网页下载源文件",
            ),
        ],
    )

    assert [segment.id for segment in run.ordered_segments()] == ["segment_1", "segment_2"]
