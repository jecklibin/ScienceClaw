from backend.recording.intent import detect_recording_intent
from backend.recording.orchestrator import RecordingOrchestrator
from backend.recording.service import recording_orchestrator
from backend.deepagent.tools import create_recording_lifecycle_tools
from pathlib import Path
import json


def test_recording_creator_skill_mentions_full_lifecycle_triggers():
    skill_md = Path("builtin_skills/recording-creator/SKILL.md").read_text(encoding="utf-8")
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


def test_complete_segment_moves_run_to_ready_for_next_segment():
    orchestrator = RecordingOrchestrator()
    run = orchestrator.create_run(session_id="session-1", user_id="u1", kind="rpa")
    segment = orchestrator.start_segment(run, kind="rpa", intent="下载 PDF", requires_workbench=True)

    orchestrator.complete_segment(run, segment)

    assert run.status == "ready_for_next_segment"
    assert run.active_segment_id is None
