from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from backend.config import settings
from backend.rpa.mcp_script_compiler import generate_mcp_script


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def build_rpa_mcp_execution_plan(tool: Any) -> dict[str, Any]:
    compiled_script = generate_mcp_script(
        tool.steps,
        tool.params,
        is_local=(settings.storage_backend == "local"),
    )
    source_hash = hashlib.sha256(
        json.dumps(
            {
                "steps": tool.steps,
                "params": tool.params,
                "input_schema": tool.input_schema,
                "post_auth_start_url": tool.post_auth_start_url,
                "allowed_domains": tool.allowed_domains,
                "requires_cookies": tool.requires_cookies,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=_json_default,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "tool_id": tool.id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requires_cookies": tool.requires_cookies,
        "compiled_steps": tool.steps,
        "compiled_script": compiled_script,
        "input_schema": tool.input_schema,
        "output_schema": tool.output_schema,
        "source_hash": source_hash,
    }
