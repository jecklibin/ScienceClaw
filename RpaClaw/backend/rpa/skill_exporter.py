import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any

from backend.storage import get_repository
from backend.config import settings

logger = logging.getLogger(__name__)


class SkillExporter:
    """Export recorded RPA skills to MongoDB or local filesystem."""

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")

    @classmethod
    def _json_dumps(cls, value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=cls._json_default,
        )

    @staticmethod
    def _build_skill_meta(
        skill_name: str,
        description: str,
        params: Dict[str, Any],
        recording_meta: Dict[str, Any],
        projected_steps: list[Dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        legacy_steps = recording_meta.get("legacy_steps", [])
        mcp_steps = projected_steps if projected_steps is not None else recording_meta.get("mcp_steps", legacy_steps)
        return {
            "version": 2,
            "kind": "rpa-recording",
            "name": skill_name,
            "description": description,
            "entry_script": "skill.py",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "params": params,
            "recording_source": recording_meta.get("recording_source", "trace"),
            "recording": recording_meta,
            "steps": legacy_steps,
            "mcp_steps": mcp_steps,
            "artifacts": ["SKILL.md", "params.json", "skill.py"],
        }

    async def export_skill(
        self,
        user_id: str,
        skill_name: str,
        description: str,
        script: str,
        params: Dict[str, Any],
        recording_meta: Dict[str, Any] | None = None,
        steps: list[Dict[str, Any]] | None = None,
    ) -> str:
        """Export skill to MongoDB or local filesystem based on storage_backend.

        Returns the skill name on success.
        """
        # Generate input schema (exclude auto-injected params)
        input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        has_auto_injected = False
        for param_name, param_info in params.items():
            # Sensitive params with credential_id are auto-injected — exclude from schema
            if param_info.get("sensitive") and param_info.get("credential_id"):
                has_auto_injected = True
                continue
            prop = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", ""),
            }
            original = param_info.get("original_value", "")
            if original and original != "{{credential}}":
                prop["default"] = original
                has_auto_injected = True
            input_schema["properties"][param_name] = prop
            # Only required if no default value available
            if param_info.get("required", False) and not original:
                input_schema["required"].append(param_name)

        auto_inject_note = ""
        if has_auto_injected:
            # Build example from actual parameters with defaults
            examples = []
            for param_name, param_info in params.items():
                original = param_info.get("original_value", "")
                if original and original != "{{credential}}":
                    examples.append(f"`--{param_name}={original}`")
            example_text = ""
            if examples:
                example_text = f" For example: {', '.join(examples[:3])}"
            
            auto_inject_note = (
                "\nNote: Some parameters (credentials and defaults) are automatically "
                "injected at runtime. You can run this skill without providing them. "
                f"Pass `--param=value` only to override the pre-configured defaults.{example_text}\n"
            )

        skill_md = f"""---
name: {skill_name}
description: {description}
---

# {skill_name}

{description}

## Usage

To execute this skill, run:

```bash
python3 skill.py
```

The skill uses Playwright to automate browser interactions based on the recorded steps.
{auto_inject_note}
## Input Schema

```json
{json.dumps(input_schema, indent=2)}
```

## Implementation

The skill is implemented in `skill.py` using Playwright for browser automation.
"""
        skill_meta = self._build_skill_meta(
            skill_name=skill_name,
            description=description,
            params=params,
            recording_meta=recording_meta
            or {
                "recording_source": "legacy_step",
                "traces": [],
                "recorded_actions": [],
                "legacy_steps": steps or [],
                "runtime_results": {},
                "trace_diagnostics": [],
                "recording_diagnostics": [],
            },
            projected_steps=steps,
        )

        if settings.storage_backend == "local":
            # Save to filesystem
            skill_dir = Path(settings.external_skills_dir) / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)

            (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
            (skill_dir / "skill.py").write_text(script, encoding="utf-8")
            # Save params config (includes credential_id for sensitive params)
            (skill_dir / "params.json").write_text(
                self._json_dumps(params), encoding="utf-8"
            )
            (skill_dir / "skill.meta.json").write_text(
                self._json_dumps(skill_meta), encoding="utf-8"
            )

            logger.info(f"Skill '{skill_name}' exported to {skill_dir}")
        else:
            # Save to MongoDB
            now = datetime.now(timezone.utc)
            col = get_repository("skills")
            await col.update_one(
                {"user_id": user_id, "name": skill_name},
                {
                    "$set": {
                        "files": {
                            "SKILL.md": skill_md,
                            "skill.py": script,
                            "skill.meta.json": self._json_dumps(skill_meta),
                        },
                        "description": description,
                        "params": params,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "user_id": user_id,
                        "name": skill_name,
                        "source": "rpa",
                        "blocked": False,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
            logger.info(f"Skill '{skill_name}' exported to MongoDB for user {user_id}")

        return skill_name
