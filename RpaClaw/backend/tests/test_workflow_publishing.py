import json
import tempfile
from pathlib import Path

from backend.recording.models import RecordingArtifact, RecordingSegment
from backend.workflow.models import SegmentInput, SkillPublishDraft, WorkflowRun, WorkflowSegment
from backend.workflow.publishing import build_publish_draft, write_skill_artifacts
from backend.workflow.recording_adapter import recording_segment_to_workflow


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
        assert "source_ref.startswith(\"artifact:\")" in runner
        assert "def build_runtime_context(page, kwargs: dict[str, Any]) -> dict[str, Any]:" in runner
        assert "\"downloads_dir\": str(downloads_dir)" in runner
        assert "\"workspace_dir\": str(workspace_dir)" in runner
        assert "\"skill_dir\": str(skill_dir)" in runner
        assert "def main()" in runner
        assert "run_script_segment" in runner
        assert "subprocess.run" not in runner

        rpa_segment = (skill_dir / "segments" / "segment_1_rpa.py").read_text(encoding="utf-8")
        assert "async def execute_segment(page, workflow_context=None, **kwargs):" in rpa_segment
        assert "workflow_context['current_page'] = current_page" in rpa_segment
        assert "async def main()" not in rpa_segment


def test_write_skill_artifacts_sanitizes_runtime_download_artifact_paths():
    run = WorkflowRun(
        id="run_download_artifact",
        session_id="session_1",
        user_id="user_1",
        intent="download and process",
        segments=[
            WorkflowSegment(
                id="segment_download",
                run_id="run_download_artifact",
                kind="rpa",
                order=1,
                title="download file",
                purpose="download a file",
                status="tested",
                outputs=[
                    {
                        "name": "contracts.xlsx",
                        "type": "file",
                        "description": "downloaded file",
                        "artifact_ref": "artifact-download-1",
                    }
                ],
                artifacts=[
                    {
                        "id": "artifact-download-1",
                        "name": "contracts.xlsx",
                        "type": "file",
                        "path": "C:\\Users\\HUAWEI\\AppData\\Local\\Temp\\playwright-artifacts-KfLnXX\\raw-download",
                        "value": {
                            "filename": "contracts.xlsx",
                            "recorded_path": "C:\\Users\\HUAWEI\\AppData\\Local\\Temp\\playwright-artifacts-KfLnXX\\raw-download",
                            "runtime": "downloads_dir",
                        },
                        "labels": ["recording", "download", "runtime-download"],
                    }
                ],
                config={"steps": []},
            )
        ],
    )
    draft = build_publish_draft(run)

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        workflow = json.loads((Path(result["skill_dir"]) / "workflow.json").read_text(encoding="utf-8"))

    artifact = workflow["segments"][0]["artifacts"][0]
    assert artifact["path"] is None
    assert artifact["value"] == {
        "filename": "contracts.xlsx",
        "runtime": "downloads_dir",
    }
    workflow_text = json.dumps(workflow, ensure_ascii=False)
    assert "playwright-artifacts" not in workflow_text
    assert "recorded_path" not in workflow_text


def test_recording_segment_to_workflow_normalizes_invalid_script_entry():
    segment = RecordingSegment(
        id="segment_bad_entry",
        run_id="run_1",
        kind="script",
        intent="处理下载文件",
        exports={
            "title": "Excel转Markdown脚本",
            "description": "处理下载的Excel文件，将其内容读取并转换为Markdown后返回",
            "entry": "处理下载的Excel文件，将其内容读取并转换为Markdown后返回",
            "script": "def run(context):\n    return {'markdown': '# ok'}\n",
            "testing_status": "passed",
        },
    )

    workflow_segment = recording_segment_to_workflow(segment, order=2)

    assert workflow_segment.kind == "script"
    assert workflow_segment.config["entry"] == "segments/segment_bad_entry_script.py"


def test_recording_segment_to_workflow_aligns_explicit_output_with_artifact_ref():
    segment = RecordingSegment(
        id="segment_download",
        run_id="run_1",
        kind="rpa",
        intent="下载文件",
        artifacts=[
            {
                "id": "artifact-download-1",
                "run_id": "run_1",
                "segment_id": "segment_download",
                "name": "contracts.xlsx",
                "type": "file",
            }
        ],
        exports={
            "title": "下载文件",
            "description": "下载 Excel 文件",
            "outputs": [
                {
                    "name": "contracts.xlsx",
                    "type": "string",
                    "description": "下载得到的文件",
                }
            ],
            "testing_status": "passed",
        },
    )

    workflow_segment = recording_segment_to_workflow(segment, order=1)

    assert workflow_segment.outputs[0].artifact_ref == "artifact-download-1"
    assert workflow_segment.outputs[0].type == "file"


def test_recording_adapter_sanitizes_runtime_download_artifact_paths():
    segment = RecordingSegment(
        id="segment_download",
        run_id="run_1",
        kind="rpa",
        intent="下载文件",
        artifacts=[
            RecordingArtifact(
                id="artifact-download-1",
                run_id="run_1",
                segment_id="segment_download",
                name="contracts.xlsx",
                type="file",
                path="C:\\Users\\HUAWEI\\AppData\\Local\\Temp\\playwright-artifacts-KfLnXX\\raw-download",
                value={
                    "filename": "contracts.xlsx",
                    "recorded_path": "C:\\Users\\HUAWEI\\AppData\\Local\\Temp\\playwright-artifacts-KfLnXX\\raw-download",
                    "runtime": "downloads_dir",
                },
                labels=["recording", "download", "runtime-download"],
            )
        ],
        exports={
            "title": "下载文件",
            "description": "下载 Excel 文件",
            "testing_status": "passed",
        },
    )

    workflow_segment = recording_segment_to_workflow(segment, order=1)
    artifact = workflow_segment.artifacts[0]

    assert artifact.path is None
    assert artifact.value == {
        "filename": "contracts.xlsx",
        "runtime": "downloads_dir",
    }
    serialized = json.dumps(workflow_segment.model_dump(mode="json"), ensure_ascii=False)
    assert "playwright-artifacts" not in serialized
    assert "recorded_path" not in serialized


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


def test_script_workflow_runner_supports_main_function_with_resolved_inputs():
    run = WorkflowRun(
        id="run_script_main",
        session_id="session_1",
        user_id="user_1",
        intent="处理下载文件",
        segments=[
            WorkflowSegment(
                id="segment_transform",
                run_id="run_script_main",
                kind="script",
                order=1,
                title="处理下载文件",
                purpose="读取输入文件并返回使用到的路径",
                status="tested",
                inputs=[
                    {
                        "name": "excel_path",
                        "type": "string",
                        "required": True,
                        "source": "user",
                        "description": "待处理文件路径",
                    }
                ],
                outputs=[
                    {
                        "name": "used_path",
                        "type": "string",
                        "description": "实际使用的文件路径",
                    }
                ],
                config={
                    "language": "python",
                    "entry": "segments/segment_transform.py",
                    "source": "def main(excel_path):\n    return {'used_path': excel_path}\n",
                },
            )
        ],
    )
    draft = build_publish_draft(run)

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        skill_dir = Path(result["skill_dir"])
        namespace: dict[str, object] = {"__file__": str(skill_dir / "skill.py")}
        exec((skill_dir / "skill.py").read_text(encoding="utf-8"), namespace)

        run_skill = namespace["run"]
        output = run_skill(excel_path="downloads/contracts.xlsx")

        assert output["outputs"]["segment_transform"]["used_path"] == "downloads/contracts.xlsx"


def test_script_workflow_runner_supports_legacy_params_inputs_outputs_style():
    run = WorkflowRun(
        id="run_script_legacy",
        session_id="session_1",
        user_id="user_1",
        intent="处理下载文件",
        segments=[
            WorkflowSegment(
                id="segment_transform",
                run_id="run_script_legacy",
                kind="script",
                order=1,
                title="处理下载文件",
                purpose="兼容旧式脚本源码",
                status="tested",
                inputs=[
                    {
                        "name": "input_path",
                        "type": "string",
                        "required": True,
                        "source": "user",
                        "description": "待处理文件路径",
                    }
                ],
                outputs=[
                    {
                        "name": "used_path",
                        "type": "string",
                        "description": "实际使用的文件路径",
                    }
                ],
                config={
                    "language": "python",
                    "entry": "segments/segment_transform.py",
                    "source": "selected = params['input_path']\noutputs['used_path'] = inputs['input_path']\n",
                },
            )
        ],
    )
    draft = build_publish_draft(run)

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        skill_dir = Path(result["skill_dir"])
        namespace: dict[str, object] = {"__file__": str(skill_dir / "skill.py")}
        exec((skill_dir / "skill.py").read_text(encoding="utf-8"), namespace)

        run_skill = namespace["run"]
        output = run_skill(input_path="downloads/contracts.xlsx")

        assert output["outputs"]["segment_transform"]["used_path"] == "downloads/contracts.xlsx"


def test_script_workflow_runner_resolves_artifact_backed_segment_output_to_file_path():
    run = WorkflowRun(
        id="run_script_artifact",
        session_id="session_1",
        user_id="user_1",
        intent="处理下载文件",
        segments=[
            WorkflowSegment(
                id="segment_download",
                run_id="run_script_artifact",
                kind="script",
                order=1,
                title="生成文件产物",
                purpose="输出一个文件产物",
                status="tested",
                outputs=[
                    {
                        "name": "downloaded_file",
                        "type": "file",
                        "description": "生成的文件",
                        "artifact_ref": "artifact-download-1",
                    }
                ],
                config={
                    "language": "python",
                    "entry": "segments/segment_download.py",
                    "source": "def run(context):\n    return {'download_result': {'path': 'downloads/contracts.xlsx', 'filename': 'contracts.xlsx'}}\n",
                },
            ),
            WorkflowSegment(
                id="segment_transform",
                run_id="run_script_artifact",
                kind="script",
                order=2,
                title="处理下载文件",
                purpose="消费上游文件路径",
                status="tested",
                inputs=[
                    {
                        "name": "input_path",
                        "type": "string",
                        "required": True,
                        "source": "segment_output",
                        "source_ref": "segment_download.outputs.downloaded_file",
                        "description": "上游文件路径",
                    }
                ],
                outputs=[
                    {
                        "name": "used_path",
                        "type": "string",
                        "description": "实际使用的文件路径",
                    }
                ],
                config={
                    "language": "python",
                    "entry": "segments/segment_transform.py",
                    "source": "def main(input_path):\n    return {'used_path': input_path}\n",
                },
            ),
        ],
    )
    draft = build_publish_draft(run)

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        skill_dir = Path(result["skill_dir"])
        namespace: dict[str, object] = {"__file__": str(skill_dir / "skill.py")}
        exec((skill_dir / "skill.py").read_text(encoding="utf-8"), namespace)

        run_skill = namespace["run"]
        output = run_skill()

        assert output["outputs"]["segment_transform"]["used_path"] == "downloads/contracts.xlsx"


def test_script_workflow_runner_exposes_runtime_workspace_and_download_paths():
    run = WorkflowRun(
        id="run_script_runtime",
        session_id="session_1",
        user_id="user_1",
        intent="处理下载文件",
        segments=[
            WorkflowSegment(
                id="segment_transform",
                run_id="run_script_runtime",
                kind="script",
                order=1,
                title="处理下载文件",
                purpose="读取运行时路径上下文",
                status="tested",
                outputs=[
                    {
                        "name": "runtime_info",
                        "type": "json",
                        "description": "运行时路径信息",
                    }
                ],
                config={
                    "language": "python",
                    "entry": "segments/segment_transform.py",
                    "source": (
                        "def run(context, **kwargs):\n"
                        "    return {\n"
                        "        'runtime_info': {\n"
                        "            'downloads_dir': context.runtime['downloads_dir'],\n"
                        "            'workspace_dir': context.runtime['workspace_dir'],\n"
                        "            'skill_dir': context.runtime['skill_dir'],\n"
                        "        }\n"
                        "    }\n"
                    ),
                },
            )
        ],
    )
    draft = build_publish_draft(run)

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = write_skill_artifacts(run, draft, Path(tmp_dir))
        skill_dir = Path(result["skill_dir"])
        namespace: dict[str, object] = {"__file__": str(skill_dir / "skill.py")}
        exec((skill_dir / "skill.py").read_text(encoding="utf-8"), namespace)

        run_skill = namespace["run"]
        output = run_skill(
            _downloads_dir="D:/tmp/downloads",
            _workspace_dir="D:/tmp/workspace",
            _skill_dir="D:/tmp/skill",
        )

        runtime_info = output["outputs"]["segment_transform"]["runtime_info"]
        assert runtime_info["downloads_dir"] == "D:\\tmp\\downloads"
        assert runtime_info["workspace_dir"] == "D:\\tmp\\workspace"
        assert runtime_info["skill_dir"] == "D:\\tmp\\skill"


def test_recording_adapter_binds_configured_params_to_runtime_params():
    segment = RecordingSegment(
        id="segment_login",
        run_id="run_params",
        kind="rpa",
        intent="login and download",
        status="completed",
        steps=[
            {"id": "step_1", "action": "fill", "value": "demo-user"},
            {"id": "step_2", "action": "fill", "value": "{{credential}}", "sensitive": True},
        ],
        exports={
            "params": {
                "username": {
                    "type": "string",
                    "description": "Login username",
                    "original_value": "demo-user",
                    "sensitive": False,
                    "required": False,
                },
                "password": {
                    "type": "string",
                    "description": "Login password",
                    "original_value": "{{credential}}",
                    "sensitive": True,
                    "credential_id": "cred-login",
                    "required": True,
                },
            },
            "auth_config": {"credential_ids": ["cred-login"]},
        },
    )

    workflow_segment = recording_segment_to_workflow(segment, order=1)
    inputs = {item.name: item for item in workflow_segment.inputs}

    assert inputs["username"].source == "workflow_param"
    assert inputs["username"].source_ref == "params.username"
    assert inputs["username"].default == "demo-user"
    assert inputs["password"].source == "credential"
    assert inputs["password"].source_ref == "params.password"
    assert inputs["password"].default is None
    assert workflow_segment.config["params"]["password"]["credential_id"] == "cred-login"
