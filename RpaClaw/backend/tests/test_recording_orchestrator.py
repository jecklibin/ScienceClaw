from backend.recording.intent import detect_recording_intent
from backend.recording.models import RecordingArtifact, RecordingSegment
from backend.recording.orchestrator import RecordingOrchestrator
from backend.recording.publishing import PublishPreparation
from backend.recording.service import recording_orchestrator
from backend.deepagent.tools import create_recording_lifecycle_tools
from pathlib import Path
import json
import tempfile
from unittest.mock import AsyncMock, patch


def test_recording_creator_skill_mentions_full_lifecycle_triggers():
    skill_md = (Path(__file__).resolve().parents[1] / "builtin_skills" / "recording-creator" / "SKILL.md").read_text(encoding="utf-8")
    assert "MCP 工具" in skill_md
    assert "多段" in skill_md
    assert "测试" in skill_md
    assert "发布" in skill_md
    assert "修复失败步骤" in skill_md
    assert "propose_skill_save" in skill_md
    assert "propose_tool_save" in skill_md
    assert "inspect_recording_runs" in skill_md
    assert "continue_recording_run" in skill_md
    assert "add_script_recording_segment" in skill_md
    assert "bind_recording_segment_io" in skill_md
    assert "input_path" in skill_md
    assert "artifact" in skill_md
    assert "next_segment_context" in skill_md
    assert "_downloads_dir" in skill_md
    assert "sample 文件" in skill_md
    assert "实际文件路径" in skill_md
    assert "run(context, **kwargs)" in skill_md
    assert "begin_recording_test" in skill_md
    assert "完整测试必须" in skill_md
    assert "不要为了验证脚本" in skill_md
    assert "不要用 `write_file`" in skill_md
    assert "不要先补测试样例" in skill_md


def test_recording_creator_skill_stays_execution_focused():
    skill_md = (Path(__file__).resolve().parents[1] / "builtin_skills" / "recording-creator" / "SKILL.md").read_text(encoding="utf-8")

    assert 'description: "Use when' in skill_md
    assert "sessions.py" not in skill_md
    assert "keyword matching" not in skill_md
    assert "姝ｅ垯" not in skill_md
    assert "params.schema.json" not in skill_md
    assert "credentials.example.json" not in skill_md
    assert "staging" not in skill_md


def test_segment_summary_marks_configured_credentials_and_defaults_as_bound_inputs():
    orchestrator = RecordingOrchestrator()
    segment = RecordingSegment(
        id="segment-login",
        run_id="run-1",
        kind="rpa",
        intent="login and download",
        exports={
            "params": {
                "username": {
                    "original_value": "admin",
                    "description": "Login username",
                    "sensitive": False,
                },
                "password": {
                    "original_value": "{{credential}}",
                    "description": "Login password",
                    "sensitive": True,
                    "credential_id": "cred-login",
                },
            }
        },
    )

    inputs = {item["name"]: item for item in orchestrator.build_segment_summary(segment)["inputs"]}

    assert inputs["username"]["source"] == "workflow_param"
    assert inputs["username"]["source_ref"] == "params.username"
    assert inputs["password"]["source"] == "credential"
    assert inputs["password"]["source_ref"] == "params.password"


def test_detect_recording_intent_for_explicit_rpa_request():
    intent = detect_recording_intent("帮我录一个下载论坛 PDF 的流程")
    assert intent is not None
    assert intent.kind == "rpa"
    assert intent.requires_workbench is True


def test_agent_recording_tool_starts_run_event():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    result = tools["start_recording_run"].invoke({
        "message": "record a business workflow skill",
        "kind": "rpa",
        "publish_target": "skill",
    })
    payload = json.loads(result)

    assert payload["recording_event"] == "recording_run_started"
    assert payload["run"]["session_id"] == "session-tool-test"
    assert payload["run"]["save_intent"] == "skill"
    assert payload["open_workbench"] is True


def test_agent_recording_tool_does_not_reparse_intent_message():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-exec",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    payload = json.loads(tools["start_recording_run"].invoke({
        "message": "execute multi-segment skill",
        "kind": "mixed",
        "publish_target": "skill",
    }))

    assert payload["recording_event"] == "recording_run_started"
    assert payload["run"]["type"] == "mixed"
    assert payload["segment"]["intent"] == "execute multi-segment skill"


def test_agent_recording_tool_can_continue_existing_run():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-continue",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a business workflow skill",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    recording_orchestrator.complete_segment(run, run.segments[-1])

    continued = json.loads(
        tools["continue_recording_run"].invoke({
            "run_id": run_id,
            "message": "缁х画褰曞埗绗簩娈碉紝鎼滅储 issue 鏍囬",
            "kind": "rpa",
            "requires_workbench": True,
        })
    )

    assert continued["recording_event"] == "recording_run_started"
    assert continued["run"]["id"] == run_id
    assert continued["segment"]["run_id"] == run_id


def test_continue_recording_run_script_kind_does_not_open_workbench():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-continue-script",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a skill that downloads a file",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    recording_orchestrator.complete_segment(run, run.segments[-1])

    continued = json.loads(
        tools["continue_recording_run"].invoke({
            "run_id": run_id,
            "message": "鎶婂垰鎵嶄笅杞界殑鏂囦欢杞崲鎴?Markdown",
            "kind": "script",
        })
    )

    assert continued["recording_event"] == "recording_run_started"
    assert continued["run"]["id"] == run_id
    assert continued["run"]["status"] == "waiting_user"
    assert continued["segment"]["kind"] == "script"
    assert continued["open_workbench"] is False


def test_add_script_segment_reuses_waiting_script_placeholder():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-script-placeholder",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a skill that downloads a file",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    recording_orchestrator.complete_segment(run, run.segments[-1])

    placeholder = json.loads(
        tools["continue_recording_run"].invoke({
            "run_id": run_id,
            "message": "鎶婂垰鎵嶄笅杞界殑鏂囦欢杞崲鎴?Markdown",
            "kind": "script",
        })
    )
    placeholder_id = placeholder["segment"]["id"]

    saved = json.loads(
        tools["add_script_recording_segment"].invoke({
            "run_id": run_id,
            "title": "杞崲涓嬭浇鏂囦欢",
            "purpose": "read the downloaded xlsx and return a markdown string",
            "script": "def run(context):\n    return {'markdown': '# ok'}\n",
            "outputs_json": json.dumps([
                {"name": "markdown", "type": "string", "description": "markdown string"},
            ], ensure_ascii=False),
        })
    )

    run = recording_orchestrator.get_run(run_id)
    assert saved["recording_event"] == "recording_segment_completed"
    assert saved["segment"]["id"] == placeholder_id
    assert saved["run"]["status"] == "ready_for_next_segment"
    assert len(run.segments) == 2
    assert run.segments[-1].status == "completed"
    assert run.segments[-1].exports["outputs"][0]["name"] == "markdown"


def test_add_script_segment_starts_as_untested():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-script-untested",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a workflow that downloads and converts a file",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    recording_orchestrator.complete_segment(run, run.segments[-1])

    saved = json.loads(
        tools["add_script_recording_segment"].invoke({
            "run_id": run_id,
            "title": "convert file",
            "purpose": "read the downloaded file and return converted output",
            "script": "def run(context, **kwargs):\n    return {'markdown_text': '# ok'}\n",
            "outputs_json": json.dumps([
                {"name": "markdown_text", "type": "string", "description": "markdown text"},
            ], ensure_ascii=False),
        })
    )

    updated_run = recording_orchestrator.get_run(run_id)
    assert updated_run.segments[-1].exports["testing_status"] == "idle"
    assert saved["summary"]["testing_status"] == "idle"

def test_add_script_segment_preserves_agent_declared_inputs_without_backend_guessing():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-script-bind-latest-artifact",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a skill that downloads a contract file",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    source_segment = run.segments[-1]
    source_segment.artifacts.append(
        RecordingArtifact(
            id="artifact-xlsx",
            run_id=run_id,
            segment_id=source_segment.id,
            name="contracts.xlsx",
            type="file",
            value={"filename": "contracts.xlsx", "runtime": "downloads_dir"},
        )
    )
    run.artifact_index.extend(source_segment.artifacts)
    recording_orchestrator.complete_segment(run, source_segment)

    saved = json.loads(
        tools["add_script_recording_segment"].invoke({
            "run_id": run_id,
            "title": "涓嬭浇鏂囦欢杞?Markdown",
            "purpose": "璇诲彇涓婁竴娈典笅杞界殑 Excel 鏂囦欢骞惰浆鎹负 Markdown",
            "script": "def run(context):\n    return {'markdown_content': '# ok'}\n",
            "inputs_json": json.dumps([
                {"name": "input_path", "type": "string", "description": "input file path"},
                {"name": "file_name", "type": "string", "description": "寰呭鐞嗘枃浠跺悕"},
                {"name": "preferred_encoding", "type": "string", "description": "鏂囨湰缂栫爜"},
                {"name": "max_pages", "type": "integer", "description": "max page count"},
            ], ensure_ascii=False),
            "outputs_json": json.dumps([
                {"name": "markdown_content", "type": "string", "description": "Markdown 鍐呭"},
            ], ensure_ascii=False),
        })
    )

    summary = saved["summary"]
    assert len(summary["inputs"]) == 4
    assert summary["inputs"][0]["name"] == "input_path"
    assert not summary["inputs"][0].get("source_ref")
    assert "file_name" not in summary.get("params", {})


def test_add_script_segment_does_not_inject_input_path_when_agent_did_not_plan_it():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-script-infer-input",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a skill that downloads a contract file",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    source_segment = run.segments[-1]
    source_segment.artifacts.append(
        RecordingArtifact(
            id="artifact-auto-input",
            run_id=run_id,
            segment_id=source_segment.id,
            name="contracts.xlsx",
            type="file",
            value={"filename": "contracts.xlsx", "runtime": "downloads_dir"},
        )
    )
    run.artifact_index.extend(source_segment.artifacts)
    recording_orchestrator.complete_segment(run, source_segment)

    saved = json.loads(
        tools["add_script_recording_segment"].invoke({
            "run_id": run_id,
            "title": "涓嬭浇鏂囦欢杞?Markdown",
            "purpose": "璇诲彇涓婁竴娈典笅杞界殑 Excel 鏂囦欢骞惰浆鎹负 Markdown",
            "script": "def run(context):\n    return {'markdown_content': '# ok'}\n",
            "outputs_json": json.dumps([
                {"name": "markdown_content", "type": "string", "description": "Markdown 鍐呭"},
            ], ensure_ascii=False),
        })
    )

    summary = saved["summary"]
    assert summary["inputs"] == []


def test_add_script_segment_normalizes_invalid_entry_to_default_script_path():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-script-invalid-entry",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a skill that downloads a contract file",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    recording_orchestrator.complete_segment(run, run.segments[-1])

    saved = json.loads(
        tools["add_script_recording_segment"].invoke({
            "run_id": run_id,
            "title": "Excel杞琈arkdown鑴氭湰",
            "purpose": "process the downloaded Excel file and return Markdown",
            "entry": "process the downloaded Excel file and return Markdown",
            "script": "def run(context):\n    return {'markdown': '# ok'}\n",
            "outputs_json": json.dumps([
                {"name": "markdown", "type": "string", "description": "Markdown 鍐呭"},
            ], ensure_ascii=False),
        })
    )

    run = recording_orchestrator.get_run(run_id)
    segment = run.segments[-1]
    assert saved["recording_event"] == "recording_segment_completed"
    assert segment.exports["entry"] == f"segments/{segment.id}_script.py"


def test_inspect_recording_runs_exposes_next_segment_context_for_agent_planning():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-next-context",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "create a workflow that downloads a file first",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    source_segment = run.segments[-1]
    source_segment.artifacts.append(
        RecordingArtifact(
            id="artifact-auto-input",
            run_id=run_id,
            segment_id=source_segment.id,
            name="contracts.xlsx",
            type="file",
            value={"filename": "contracts.xlsx", "runtime": "downloads_dir"},
        )
    )
    run.artifact_index.extend(source_segment.artifacts)
    recording_orchestrator.complete_segment(run, source_segment)

    inspected = json.loads(tools["inspect_recording_runs"].invoke({"run_id": run_id}))

    run_payload = inspected["runs"][0]
    next_context = run_payload["next_segment_context"]
    assert next_context["latest_segment"]["segment_id"] == source_segment.id
    assert next_context["runtime_path_policy"]["kind"] == "artifact-ref-runtime-resolution"
    assert "_downloads_dir" in next_context["runtime_path_policy"]["message"]
    assert any(
        item["source_ref"] == "artifact:artifact-auto-input"
        for item in next_context["available_sources"]["artifacts"]
    )


def test_continue_recording_run_returns_compact_lifecycle_payload_after_large_segment():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-continue-large",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a business workflow skill",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    segment = run.segments[-1]
    segment.steps = [
        {
            "id": f"step-{index}",
            "action": "click",
            "target": '{"method":"css","value":"article:nth-child(%d) a.long-selector"}' % index,
            "description": "long recorded step " + ("x" * 120),
            "locator_candidates": [
                {
                    "kind": "css",
                    "status": "ok",
                    "locator": {"method": "css", "value": ".candidate-" + ("y" * 80)},
                }
            ],
        }
        for index in range(40)
    ]
    recording_orchestrator.complete_segment(run, segment)

    result = tools["continue_recording_run"].invoke({
        "run_id": run_id,
        "message": "record second segment",
        "kind": "rpa",
        "requires_workbench": True,
    })

    assert len(result) < 3000
    continued = json.loads(result)
    assert continued["recording_event"] == "recording_run_started"
    assert continued["run"]["id"] == run_id
    assert "segments" not in continued["run"]
    assert "steps" not in continued["segment"]
    assert continued["open_workbench"] is True


def test_agent_recording_tool_can_append_script_segment_and_bind_io():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-bind",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a business workflow skill",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    source_segment_id = started["segment"]["id"]
    run = recording_orchestrator.get_run(run_id)
    recording_orchestrator.complete_segment(run, run.segments[-1])

    script_payload = json.loads(
        tools["add_script_recording_segment"].invoke({
            "run_id": run_id,
            "title": "鎼滅储 issue",
            "purpose": "鎶婄涓€娈垫彁鍙栫殑鏍囬浣滀负鎼滅储杈撳叆",
            "script": "def run(context):\n    return {'search_result': 'ok'}\n",
            "inputs_json": json.dumps([
                {"name": "query", "type": "string", "description": "search query"},
            ], ensure_ascii=False),
            "outputs_json": json.dumps([
                {"name": "search_result", "type": "string", "description": "鎼滅储缁撴灉"},
            ], ensure_ascii=False),
        })
    )
    target_segment_id = script_payload["segment"]["id"]

    bound = json.loads(
        tools["bind_recording_segment_io"].invoke({
            "run_id": run_id,
            "source_segment_id": source_segment_id,
            "output_name": "issue_title",
            "output_type": "string",
            "target_segment_id": target_segment_id,
            "input_name": "query",
            "input_type": "string",
        })
    )

    assert bound["recording_event"] == "recording_segment_updated"
    summaries = bound["summaries"]
    assert summaries[0]["outputs"][0]["name"] == "issue_title"
    assert summaries[1]["inputs"][0]["source_ref"] == f"{source_segment_id}.outputs.issue_title"


def test_bind_recording_segment_io_updates_rpa_step_contracts():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-rpa-bind",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a business workflow skill",
            "kind": "rpa",
            "publish_target": "skill",
        })
    )
    run_id = started["run"]["id"]
    source_segment_id = started["segment"]["id"]
    run = recording_orchestrator.get_run(run_id)
    source_segment = run.segments[-1]
    source_segment.steps = [
        {
            "id": "step-1",
            "action": "extract_text",
            "target": '{"method":"role","role":"heading","name":"Issues"}',
            "description": "鎻愬彇 issue 鏍囬",
        }
    ]
    recording_orchestrator.complete_segment(run, source_segment)

    continued = json.loads(
        tools["continue_recording_run"].invoke({
            "run_id": run_id,
            "message": "缁х画褰曞埗绗簩娈碉紝鎼滅储 issue 鏍囬",
            "kind": "rpa",
            "requires_workbench": True,
        })
    )
    target_segment_id = continued["segment"]["id"]
    target_segment = run.segments[-1]
    target_segment.steps = [
        {
            "id": "step-2",
            "action": "fill",
            "target": "{\"method\":\"label\",\"value\":\"search box\"}",
            "value": "test",
            "description": "enter the search query",
        }
    ]
    target_segment.exports["params"] = {}
    recording_orchestrator.complete_segment(run, target_segment)

    bound = json.loads(
        tools["bind_recording_segment_io"].invoke({
            "run_id": run_id,
            "source_segment_id": source_segment_id,
            "output_name": "issue_title",
            "output_type": "string",
            "target_segment_id": target_segment_id,
            "input_name": "search",
            "input_type": "string",
        })
    )

    assert bound["recording_event"] == "recording_segment_updated"
    assert source_segment.steps[0]["result_key"] == "issue_title"
    assert target_segment.exports["params"]["search"]["original_value"] == "test"
    assert target_segment.exports["params"]["search"]["sensitive"] is False
    assert "issue_title" in target_segment.exports["params"]["search"]["description"]


def test_build_segment_mapping_sources_groups_historical_outputs_and_artifacts():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")

    first = orchestrator.start_segment(run, kind="rpa", intent="鑾峰彇椤圭洰鍚嶇О", requires_workbench=True)
    first.steps = [
        {
            "id": "step-1",
            "action": "extract_text",
            "description": "鎻愬彇椤圭洰鍚嶇О",
            "result_key": "project_name",
        }
    ]
    first.exports["title"] = "鑾峰彇椤圭洰鍚嶇О"
    orchestrator.complete_segment(run, first)

    second = orchestrator.start_segment(run, kind="script", intent="杞崲鏂囦欢", requires_workbench=False)
    second.exports["outputs"] = [
        {"name": "normalized_csv", "type": "file", "description": "鏍囧噯鍖栧悗鐨?CSV"}
    ]
    second.artifacts.append(
        RecordingArtifact(
            id="artifact-1",
            run_id=run.id,
            segment_id=second.id,
            name="normalized_csv",
            type="file",
            path="/tmp/normalized.csv",
        )
    )
    orchestrator.complete_segment(run, second)

    current = orchestrator.start_segment(run, kind="rpa", intent="鎼滅储椤圭洰", requires_workbench=True)
    pool = orchestrator.build_segment_mapping_sources(run, current)

    assert pool["segment_outputs"][0]["source_ref"] == f"{first.id}.outputs.project_name"
    assert pool["segment_outputs"][0]["segment_title"] == "鑾峰彇椤圭洰鍚嶇О"
    assert pool["artifacts"][0]["source_ref"] == "artifact:artifact-1"
    assert pool["recommended"][0]["source_type"] in {"segment_output", "artifact"}
    assert pool["workflow_params"] == []


def test_update_segment_bindings_sets_inputs_and_seeds_rpa_params():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")

    source = orchestrator.start_segment(run, kind="rpa", intent="鎻愬彇鏍囬", requires_workbench=True)
    source.steps = [
        {
            "id": "step-1",
            "action": "extract_text",
            "description": "鎻愬彇鏍囬",
            "result_key": "issue_title",
        }
    ]
    orchestrator.complete_segment(run, source)

    target = orchestrator.start_segment(run, kind="rpa", intent="鎼滅储鏍囬", requires_workbench=True)
    target.steps = [
        {
            "id": "step-2",
            "action": "fill",
            "description": "enter the search query",
            "value": "test",
        }
    ]
    target.exports["params"] = {}
    orchestrator.complete_segment(run, target)

    orchestrator.update_segment_bindings(
        run,
        target,
        inputs=[
            {
                "name": "search",
                "type": "string",
                "required": True,
                "source": "segment_output",
                "source_ref": f"{source.id}.outputs.issue_title",
                "description": "鏉ヨ嚜涓婁竴娈电殑鏍囬",
            }
        ],
    )

    assert target.exports["inputs"][0]["source_ref"] == f"{source.id}.outputs.issue_title"
    assert target.exports["params"]["search"]["original_value"] == "test"
    assert target.exports["params"]["search"]["sensitive"] is False
    assert target.exports["params"]["search"]["description"] == "鏉ヨ嚜涓婁竴娈电殑鏍囬"

def test_detect_recording_intent_for_business_workflow_skill_phrase():
    intent = detect_recording_intent("我要录制个业务流程技能")
    assert intent is not None
    assert intent.kind == "rpa"
    assert intent.requires_workbench is True
    assert intent.save_intent == "skill"


def test_detect_recording_intent_for_mcp_tool_phrase():
    intent = detect_recording_intent("帮我录制一个MCP工具")
    assert intent is not None
    assert intent.kind == "mcp"
    assert intent.requires_workbench is True
    assert intent.save_intent == "tool"


def test_detect_recording_intent_returns_none_for_skill_execution_phrase():
    intent = detect_recording_intent("执行 multi-segment 技能")
    assert intent is None


def test_complete_segment_moves_run_to_ready_for_next_segment():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
    segment = orchestrator.start_segment(run, kind="rpa", intent="涓嬭浇 PDF", requires_workbench=True)

    orchestrator.complete_segment(run, segment)

    assert run.status == "ready_for_next_segment"
    assert run.active_segment_id is None


def test_mark_ready_to_publish_is_idempotent_for_already_ready_run():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
    segment = orchestrator.start_segment(run, kind="rpa", intent="涓嬭浇 PDF", requires_workbench=True)
    orchestrator.complete_segment(run, segment)
    orchestrator.mark_ready_to_publish(run, "skill")

    orchestrator.mark_ready_to_publish(run, "skill")

    assert run.status == "ready_to_publish"
    assert run.publish_target == "skill"
    assert run.testing["status"] == "passed"


def test_ready_to_publish_run_can_continue_with_new_segment_and_invalidates_test_result():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
    segment = orchestrator.start_segment(run, kind="rpa", intent="鑾峰彇椤圭洰鍚嶇О", requires_workbench=True)
    orchestrator.complete_segment(run, segment)
    orchestrator.mark_ready_to_publish(run, "tool")

    next_segment = orchestrator.start_segment(
        run,
        kind="rpa",
        intent="鎼滅储椤圭洰鍚嶇О",
        requires_workbench=True,
    )

    assert run.status == "recording"
    assert run.active_segment_id == next_segment.id
    assert run.testing == {"status": "idle"}
    assert run.publish_target == "tool"


def test_begin_recording_test_tool_marks_workflow_ready_to_publish_on_success():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-workflow-success",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a tool",
            "kind": "rpa",
            "publish_target": "tool",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    first = run.segments[-1]
    first.steps = [{"id": "step-1", "action": "goto", "url": "https://github.com/trending"}]
    first.exports["testing_status"] = "passed"
    recording_orchestrator.complete_segment(run, first)
    second = recording_orchestrator.start_segment(run, kind="script", intent="澶勭悊缁撴灉", requires_workbench=False)
    second.exports["testing_status"] = "passed"
    second.exports["script"] = "def run(context):\n    return {'ok': True}\n"
    second.exports["entry"] = "segments/seg2.py"
    recording_orchestrator.complete_segment(run, second)

    with patch(
        "backend.deepagent.tools.execute_recording_workflow_test",
        new=AsyncMock(return_value={
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": True, "logs": ["workflow ok"], "result": {}},
        }),
        create=True,
    ), patch(
        "backend.recording.testing.execute_recording_workflow_test",
        new=AsyncMock(return_value={
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": True, "logs": ["workflow ok"], "result": {}},
        }),
    ):
        payload = json.loads(tools["begin_recording_test"].invoke({"run_id": run_id}))

    assert payload["run"]["status"] == "ready_to_publish"
    assert payload["run"]["testing"]["status"] == "passed"


def test_begin_recording_test_tool_executes_single_segment_run():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-segment-success",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a tool",
            "kind": "rpa",
            "publish_target": "tool",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    first = run.segments[-1]
    first.steps = [{"id": "step-1", "action": "goto", "url": "https://github.com/trending"}]
    first.exports["rpa_session_id"] = "rpa-1"
    first.exports["title"] = "鑾峰彇椤圭洰鍚嶇О"
    recording_orchestrator.complete_segment(run, first)

    with patch(
        "backend.deepagent.tools.execute_recording_workflow_test",
        new=AsyncMock(return_value={
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": True, "logs": ["segment ok"], "result": {}},
        }),
        create=True,
    ), patch(
        "backend.recording.testing.execute_recording_workflow_test",
        new=AsyncMock(return_value={
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": True, "logs": ["segment ok"], "result": {}},
        }),
    ):
        payload = json.loads(tools["begin_recording_test"].invoke({"run_id": run_id}))

    assert payload["test_payload"]["mode"] == "segment"
    assert payload["test_payload"]["execution"]["result"]["logs"] == ["segment ok"]
    assert payload["run"]["status"] == "ready_to_publish"
    assert payload["run"]["testing"]["status"] == "passed"


def test_begin_recording_test_tool_marks_workflow_needs_repair_on_failure():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-workflow-failure",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a tool",
            "kind": "rpa",
            "publish_target": "tool",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    first = run.segments[-1]
    first.steps = [{"id": "step-1", "action": "goto", "url": "https://github.com/trending"}]
    first.exports["testing_status"] = "passed"
    recording_orchestrator.complete_segment(run, first)
    second = recording_orchestrator.start_segment(run, kind="script", intent="澶勭悊缁撴灉", requires_workbench=False)
    second.exports["testing_status"] = "passed"
    second.exports["script"] = "def run(context):\n    return {'ok': True}\n"
    second.exports["entry"] = "segments/seg2.py"
    recording_orchestrator.complete_segment(run, second)

    with patch(
        "backend.deepagent.tools.execute_recording_workflow_test",
        new=AsyncMock(return_value={
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": False, "logs": ["workflow failed"], "stderr": "boom", "result": {}},
        }),
        create=True,
    ), patch(
        "backend.recording.testing.execute_recording_workflow_test",
        new=AsyncMock(return_value={
            "skill_dir": "/tmp/skill",
            "workflow_path": "/tmp/skill/workflow.json",
            "segments": [],
            "result": {"success": False, "logs": ["workflow failed"], "stderr": "boom", "result": {}},
        }),
    ):
        payload = json.loads(tools["begin_recording_test"].invoke({"run_id": run_id}))

    assert payload["run"]["status"] == "needs_repair"
    assert payload["run"]["testing"]["status"] == "failed"


def test_begin_recording_test_tool_marks_missing_contract_output_as_needs_repair():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-workflow-contract-failure",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }

    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a tool",
            "kind": "rpa",
            "publish_target": "tool",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    first = run.segments[-1]
    first.steps = [{"id": "step-1", "action": "goto", "url": "https://github.com/trending"}]
    first.exports["testing_status"] = "passed"
    recording_orchestrator.complete_segment(run, first)
    second = recording_orchestrator.start_segment(run, kind="script", intent="process result", requires_workbench=False)
    second.exports["script"] = "def run(context, **kwargs):\n    return {}\n"
    second.exports["entry"] = "segments/seg2.py"
    second.exports["outputs"] = [{"name": "markdown_text", "type": "string"}]
    recording_orchestrator.complete_segment(run, second)

    return_value = {
        "skill_dir": "/tmp/skill",
        "workflow_path": "/tmp/skill/workflow.json",
        "segments": [],
        "repair_context": {
            "context_path": "/tmp/skill/recording_test_context.json",
            "missing_outputs": ["markdown_text"],
        },
        "result": {
            "success": False,
            "logs": ["workflow ok", "missing markdown_text"],
            "stderr": "",
            "contract": {
                "success": False,
                "segment_results": [
                    {
                        "segment_id": second.id,
                        "title": "Excel to Markdown",
                        "kind": "script",
                        "missing_outputs": ["markdown_text"],
                        "missing_artifacts": [],
                        "status": "failed",
                    }
                ],
            },
            "result": {"outputs": {second.id: {}}},
        },
    }

    with patch(
        "backend.deepagent.tools.execute_recording_workflow_test",
        new=AsyncMock(return_value=return_value),
        create=True,
    ), patch(
        "backend.recording.testing.execute_recording_workflow_test",
        new=AsyncMock(return_value=return_value),
    ):
        payload = json.loads(tools["begin_recording_test"].invoke({"run_id": run_id}))

    assert payload["run"]["status"] == "needs_repair"
    assert payload["run"]["testing"]["status"] == "failed"
    assert payload["summary"]["repair_context"]["missing_outputs"] == ["markdown_text"]

def test_apply_recording_test_repairs_syncs_script_segment_back_to_run():
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_dir = Path(tmp_dir)
        tools = {
            tool.name: tool
            for tool in create_recording_lifecycle_tools(
                session_id="session-tool-test-apply-repair",
                user_id="u1",
                workspace_dir=str(workspace_dir),
            )
        }

        started = json.loads(
            tools["start_recording_run"].invoke({
                "message": "record a skill",
                "kind": "rpa",
                "publish_target": "skill",
            })
        )
        run_id = started["run"]["id"]
        run = recording_orchestrator.get_run(run_id)
        recording_orchestrator.complete_segment(run, run.segments[-1])

        saved = json.loads(
            tools["add_script_recording_segment"].invoke({
                "run_id": run_id,
                "title": "Excel to Markdown",
                "purpose": "read Excel and return Markdown output",
                "script": "def run(context, **kwargs):\n    return {}\n",
                "outputs_json": json.dumps([
                    {"name": "markdown_text", "type": "string", "description": "markdown text"},
                ], ensure_ascii=False),
            })
        )
        segment_id = saved["segment"]["id"]
        skill_dir = workspace_dir / "session-tool-test-apply-repair" / "workflow_test_runs" / "repair-skill"
        script_path = skill_dir / "segments" / f"{segment_id}_script.py"
        script_path.parent.mkdir(parents=True)
        script_path.write_text(
            "def run(context, **kwargs):\n    return {'markdown_text': '# repaired'}\n",
            encoding="utf-8",
        )
        (skill_dir / "workflow.json").write_text(
            json.dumps(
                {
                    "segments": [
                        {
                            "id": run.segments[0].id,
                            "kind": "rpa",
                            "title": "download file",
                            "purpose": "download file",
                            "inputs": [],
                            "outputs": [],
                        },
                        {
                            "id": segment_id,
                            "kind": "script",
                            "title": "Excel to Markdown",
                            "purpose": "read Excel and return Markdown output",
                            "entry": f"segments/{segment_id}_script.py",
                            "inputs": [{"name": "excel_path", "type": "file", "source": "artifact"}],
                            "outputs": [{"name": "markdown_text", "type": "string"}],
                        },
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (skill_dir / "params.json").write_text("{}", encoding="utf-8")
        run.testing = {
            "status": "failed",
            "repair_context": {
                "skill_dir": str(skill_dir),
                "workflow_path": str(skill_dir / "workflow.json"),
                "context_path": str(skill_dir / "recording_test_context.json"),
            },
        }

        payload = json.loads(tools["apply_recording_test_repairs"].invoke({"run_id": run_id}))

        repaired_segment = recording_orchestrator.get_run(run_id).segments[-1]
        assert payload["recording_event"] == "recording_segment_updated"
        assert repaired_segment.exports["script"] == "def run(context, **kwargs):\n    return {'markdown_text': '# repaired'}\n"
        assert repaired_segment.exports["outputs"] == [{"name": "markdown_text", "type": "string"}]
        assert repaired_segment.exports["inputs"] == [{"name": "excel_path", "type": "file", "source": "artifact"}]
        assert repaired_segment.exports["testing_status"] == "idle"

def test_prepare_recording_publish_tool_preserves_existing_tool_target_when_argument_omitted():
    tools = {
        tool.name: tool
        for tool in create_recording_lifecycle_tools(
            session_id="session-tool-test-publish-target",
            user_id="u1",
            workspace_dir="/tmp/workspace",
        )
    }
    started = json.loads(
        tools["start_recording_run"].invoke({
            "message": "record a tool",
            "kind": "rpa",
            "publish_target": "tool",
        })
    )
    run_id = started["run"]["id"]
    run = recording_orchestrator.get_run(run_id)
    segment = run.segments[-1]
    segment.steps = [{"id": "step-1", "action": "goto", "url": "https://github.com/trending"}]
    recording_orchestrator.complete_segment(run, segment)

    async def _fake_build_publish_artifacts(candidate_run, workspace_dir):
        assert candidate_run.publish_target == "tool"
        return PublishPreparation(
            prompt_kind="tool",
            staging_paths=["rpa-mcp:tool-1"],
            summary={
                "name": "tool_1",
                "saved": True,
                "draft": {"skill_name": "tool_1", "publish_target": "tool"},
            },
        )

    with patch(
        "backend.recording.publishing.build_publish_artifacts",
        new=AsyncMock(side_effect=_fake_build_publish_artifacts),
    ):
        payload = json.loads(tools["prepare_recording_publish"].invoke({"run_id": run_id}))

    assert payload["prompt_kind"] == "tool"
    assert payload["run"]["publish_target"] == "tool"
    assert run.publish_target == "tool"


