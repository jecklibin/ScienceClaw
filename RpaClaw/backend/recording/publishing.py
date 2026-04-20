from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.config import settings
from backend.rpa.generator import PlaywrightGenerator

from .models import RecordingRun


@dataclass
class PublishPreparation:
    prompt_kind: Literal["skill", "tool"]
    staging_paths: list[str]
    summary: dict[str, Any]


def _safe_name(value: str, fallback: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value).strip("_").lower()
    return candidate or fallback


def _run_display_name(run: RecordingRun) -> str:
    if run.segments:
        return run.segments[0].intent.strip() or "recorded_workflow"
    return "recorded_workflow"


def _collect_rpa_steps(run: RecordingRun) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for segment in run.segments:
        if segment.kind in {"rpa", "mixed"}:
            steps.extend(segment.steps)
    return steps


def _build_skill_script(run: RecordingRun) -> str:
    steps = _collect_rpa_steps(run)
    if not steps:
        return "\n".join(
            [
                '"""Generated recorded workflow skill."""',
                "",
                "def run(*args, **kwargs):",
                "    return {",
                f'        "run_id": "{run.id}",',
                '        "status": "no_rpa_steps",',
                "    }",
                "",
            ]
        )
    return PlaywrightGenerator().generate_script(
        steps,
        params={},
        is_local=(settings.storage_backend == "local"),
    )


def _skill_staging_dir(workspace_dir: str, session_id: str, run_id: str) -> Path:
    return Path(workspace_dir) / session_id / "skills_staging" / run_id


def _tool_staging_dir(workspace_dir: str, session_id: str) -> Path:
    return Path(workspace_dir) / session_id / "tools_staging"


async def build_publish_artifacts(run: RecordingRun, workspace_dir: str) -> PublishPreparation:
    target = run.publish_target or run.save_intent or "skill"
    if target not in {"skill", "tool"}:
        target = "skill"

    display_name = _run_display_name(run)
    safe_name = _safe_name(display_name, f"recording_{run.id[:8]}")
    summary = {
        "name": safe_name,
        "title": display_name,
        "run_id": run.id,
        "session_id": run.session_id,
        "segments": [segment.model_dump(mode="json") for segment in run.segments],
        "artifacts": [artifact.model_dump(mode="json") for artifact in run.artifact_index],
    }

    if target == "tool":
        target_dir = _tool_staging_dir(workspace_dir, run.session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        tool_path = target_dir / f"{safe_name}.py"
        tool_path.write_text(
            "\n".join(
                [
                    '"""Generated from a recorded RPA/MCP workflow."""',
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
        return PublishPreparation(prompt_kind="tool", staging_paths=[str(tool_path)], summary=summary)

    target_dir = _skill_staging_dir(workspace_dir, run.session_id, run.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    save_ready_dir = Path(workspace_dir) / run.session_id / ".agents" / "skills" / safe_name
    save_ready_dir.mkdir(parents=True, exist_ok=True)
    skill_md = "\n".join(
        [
            "---",
            f"name: {safe_name}",
            f'description: "Recorded workflow generated from conversation run {run.id}."',
            "---",
            "",
            f"# {display_name}",
            "",
            "This skill was generated from a recorded RPA/MCP workflow.",
            "",
            "## Segments",
            *[f"- {segment.intent}" for segment in run.segments],
            "",
        ]
    )
    skill_py = _build_skill_script(run)
    (target_dir / "SKILL.md").write_text(
        skill_md,
        encoding="utf-8",
    )
    (target_dir / "skill.py").write_text(
        skill_py,
        encoding="utf-8",
    )
    (save_ready_dir / "SKILL.md").write_text(
        skill_md,
        encoding="utf-8",
    )
    (save_ready_dir / "skill.py").write_text(
        skill_py,
        encoding="utf-8",
    )
    return PublishPreparation(
        prompt_kind="skill",
        staging_paths=[str(target_dir), str(save_ready_dir)],
        summary=summary,
    )
