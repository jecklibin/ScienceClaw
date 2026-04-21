from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.workflow.publishing import build_publish_draft, safe_skill_name, write_skill_artifacts
from backend.workflow.recording_adapter import recording_run_to_workflow

from .models import RecordingRun


@dataclass
class PublishPreparation:
    prompt_kind: Literal["skill", "tool"]
    staging_paths: list[str]
    summary: dict[str, Any]


def _safe_name(value: str, fallback: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value).strip("_").lower()
    return candidate or fallback


def _tool_staging_dir(workspace_dir: str, session_id: str) -> Path:
    return Path(workspace_dir) / session_id / "tools_staging"


async def build_publish_artifacts(run: RecordingRun, workspace_dir: str) -> PublishPreparation:
    target = run.publish_target or run.save_intent or "skill"
    if target not in {"skill", "tool"}:
        target = "skill"

    workflow_run = recording_run_to_workflow(run)
    draft = build_publish_draft(workflow_run, publish_target="skill" if target == "skill" else "tool")

    if target == "tool":
        safe_name = safe_skill_name(draft.skill_name, f"recording_{run.id[:8]}")
        target_dir = _tool_staging_dir(workspace_dir, run.session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        tool_path = target_dir / f"{safe_name}.py"
        tool_path.write_text(
            "\n".join(
                [
                    '"""Generated from a recorded workflow."""',
                    "",
                    "def run(*args, **kwargs):",
                    "    return {",
                    f'        "run_id": "{run.id}",',
                    f'        "segments": {len(run.segments)},',
                    "    }",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return PublishPreparation(
            prompt_kind="tool",
            staging_paths=[str(tool_path)],
            summary={
                "name": safe_name,
                "title": draft.display_title,
                "run_id": run.id,
                "draft": draft.model_dump(mode="json"),
            },
        )

    staging_root = Path(workspace_dir) / run.session_id / "skills_staging"
    save_ready_root = Path(workspace_dir) / run.session_id / ".agents" / "skills"
    staging_result = write_skill_artifacts(workflow_run, draft, staging_root)
    save_ready_result = write_skill_artifacts(workflow_run, draft, save_ready_root)

    return PublishPreparation(
        prompt_kind="skill",
        staging_paths=[staging_result["skill_dir"], save_ready_result["skill_dir"]],
        summary={
            "name": staging_result["name"],
            "title": draft.display_title,
            "run_id": run.id,
            "session_id": run.session_id,
            "draft": draft.model_dump(mode="json"),
            "segments": [segment.model_dump(mode="json") for segment in workflow_run.segments],
            "artifacts": [artifact.model_dump(mode="json") for artifact in workflow_run.artifacts],
        },
    )
