import asyncio
import tempfile
from pathlib import Path

from backend.recording.models import RecordingRun
from backend.recording.publishing import build_publish_artifacts


def test_publish_skill_builds_staging_output_and_prompt():
    with tempfile.TemporaryDirectory() as tmp_dir:
        run = RecordingRun(id="run-1", session_id="session-1", user_id="u1", type="rpa")
        run.publish_target = "skill"

        result = asyncio.run(build_publish_artifacts(run, workspace_dir=tmp_dir))

        assert result.prompt_kind == "skill"
        assert result.staging_paths
        staged_dir = Path(result.staging_paths[0])
        assert staged_dir.name == "run-1"
        assert staged_dir.parent.name == "skills_staging"
        assert (staged_dir / "SKILL.md").is_file()
        save_ready_dir = Path(tmp_dir) / "session-1" / ".agents" / "skills" / result.summary["name"]
        assert (save_ready_dir / "SKILL.md").is_file()


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
