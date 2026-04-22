from backend.recording.intent import detect_recording_intent
from backend.recording.models import RecordingArtifact
from backend.recording.orchestrator import RecordingOrchestrator
from backend.recording.service import recording_orchestrator
from backend.deepagent.tools import create_recording_lifecycle_tools
from pathlib import Path
import json


def test_recording_creator_skill_mentions_full_lifecycle_triggers():
    skill_md = (Path(__file__).resolve().parents[1] / "builtin_skills" / "recording-creator" / "SKILL.md").read_text(encoding="utf-8")
    assert "业务流程技能" in skill_md
    assert "MCP 工具" in skill_md
    assert "多段" in skill_md
    assert "测试" in skill_md
    assert "发布" in skill_md
    assert "propose_skill_save" in skill_md
    assert "propose_tool_save" in skill_md
    assert "inspect_recording_runs" in skill_md
    assert "continue_recording_run" in skill_md
    assert "add_script_recording_segment" in skill_md
    assert "bind_recording_segment_io" in skill_md


def test_detect_recording_intent_for_explicit_rpa_request():
    intent = detect_recording_intent("帮我录一个下载论文 PDF 的流程")
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
        "message": "我要录制个业务流程技能",
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
        "message": "执行 multi-segment 技能",
        "kind": "mixed",
        "publish_target": "skill",
    }))

    assert payload["recording_event"] == "recording_run_started"
    assert payload["run"]["type"] == "mixed"
    assert payload["segment"]["intent"] == "执行 multi-segment 技能"


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
            "message": "我要录制个业务流程技能",
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
            "message": "继续录制第二段，搜索 issue 标题",
            "kind": "rpa",
            "requires_workbench": True,
        })
    )

    assert continued["recording_event"] == "recording_run_started"
    assert continued["run"]["id"] == run_id
    assert continued["segment"]["run_id"] == run_id


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
            "message": "我要录制一个业务流程技能",
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
            "message": "我要录制个业务流程技能",
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
            "title": "搜索 issue",
            "purpose": "把第一段提取的标题作为搜索输入",
            "script": "def run(context):\n    return {'search_result': 'ok'}\n",
            "inputs_json": json.dumps([
                {"name": "query", "type": "string", "description": "搜索词"},
            ], ensure_ascii=False),
            "outputs_json": json.dumps([
                {"name": "search_result", "type": "string", "description": "搜索结果"},
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
            "message": "我要录制个业务流程技能",
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
            "description": "提取 issue 标题",
        }
    ]
    recording_orchestrator.complete_segment(run, source_segment)

    continued = json.loads(
        tools["continue_recording_run"].invoke({
            "run_id": run_id,
            "message": "继续录制第二段，搜索 issue 标题",
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
            "target": '{"method":"label","value":"搜索框"}',
            "value": "test",
            "description": "输入搜索词",
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

    first = orchestrator.start_segment(run, kind="rpa", intent="获取项目名称", requires_workbench=True)
    first.steps = [
        {
            "id": "step-1",
            "action": "extract_text",
            "description": "提取项目名称",
            "result_key": "project_name",
        }
    ]
    first.exports["title"] = "获取项目名称"
    orchestrator.complete_segment(run, first)

    second = orchestrator.start_segment(run, kind="script", intent="转换文件", requires_workbench=False)
    second.exports["outputs"] = [
        {"name": "normalized_csv", "type": "file", "description": "标准化后的 CSV"}
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

    current = orchestrator.start_segment(run, kind="rpa", intent="搜索项目", requires_workbench=True)
    pool = orchestrator.build_segment_mapping_sources(run, current)

    assert pool["segment_outputs"][0]["source_ref"] == f"{first.id}.outputs.project_name"
    assert pool["segment_outputs"][0]["segment_title"] == "获取项目名称"
    assert pool["artifacts"][0]["source_ref"] == "artifact:artifact-1"
    assert pool["recommended"][0]["source_type"] in {"segment_output", "artifact"}
    assert pool["workflow_params"] == []


def test_update_segment_bindings_sets_inputs_and_seeds_rpa_params():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="mixed")

    source = orchestrator.start_segment(run, kind="rpa", intent="提取标题", requires_workbench=True)
    source.steps = [
        {
            "id": "step-1",
            "action": "extract_text",
            "description": "提取标题",
            "result_key": "issue_title",
        }
    ]
    orchestrator.complete_segment(run, source)

    target = orchestrator.start_segment(run, kind="rpa", intent="搜索标题", requires_workbench=True)
    target.steps = [
        {
            "id": "step-2",
            "action": "fill",
            "description": "输入搜索词",
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
                "description": "来自上一段的标题",
            }
        ],
    )

    assert target.exports["inputs"][0]["source_ref"] == f"{source.id}.outputs.issue_title"
    assert target.exports["params"]["search"]["original_value"] == "test"
    assert target.exports["params"]["search"]["sensitive"] is False
    assert target.exports["params"]["search"]["description"] == "来自上一段的标题"


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
    segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)

    orchestrator.complete_segment(run, segment)

    assert run.status == "ready_for_next_segment"
    assert run.active_segment_id is None


def test_mark_ready_to_publish_is_idempotent_for_already_ready_run():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
    segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)
    orchestrator.complete_segment(run, segment)
    orchestrator.mark_ready_to_publish(run, "skill")

    orchestrator.mark_ready_to_publish(run, "skill")

    assert run.status == "ready_to_publish"
    assert run.publish_target == "skill"
    assert run.testing["status"] == "passed"
