import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from backend.mongodb.db import db

logger = logging.getLogger(__name__)


class SkillExporter:
    """Export recorded RPA skills to MongoDB."""

    async def export_skill(
        self,
        user_id: str,
        skill_name: str,
        description: str,
        script: str,
        params: Dict[str, Any],
    ) -> str:
        """Export skill to MongoDB skills collection.

        Returns the skill name on success.
        """
        # Generate input schema
        input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        for param_name, param_info in params.items():
            input_schema["properties"][param_name] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", ""),
            }
            if param_info.get("required", False):
                input_schema["required"].append(param_name)

        skill_md = f"""---
name: {skill_name}
description: {description}
---

# {skill_name}

{description}

## Input Schema

```json
{json.dumps(input_schema, indent=2)}
```

## Implementation

See `skill.py` for the Playwright implementation.
"""

        now = datetime.now(timezone.utc)
        col = db.get_collection("skills")
        await col.update_one(
            {"user_id": user_id, "name": skill_name},
            {
                "$set": {
                    "files": {
                        "SKILL.md": skill_md,
                        "skill.py": script,
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
