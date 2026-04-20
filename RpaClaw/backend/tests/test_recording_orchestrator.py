from backend.recording.intent import detect_recording_intent
from backend.recording.orchestrator import RecordingOrchestrator
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
