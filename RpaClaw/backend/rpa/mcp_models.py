from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


def build_rpa_mcp_output_schema(data_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "message": {"type": "string"},
            "data": data_schema or {"type": "object", "properties": {}, "additionalProperties": True},
            "downloads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["filename", "path"],
                    "additionalProperties": True,
                },
            },
            "artifacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
            "error": {"type": ["object", "null"]},
        },
        "required": ["success", "data", "downloads", "artifacts"],
    }


def build_rpa_mcp_semantic_report(source: str = "rule_inferred") -> dict[str, Any]:
    return {
        "source": source,
        "confidence": None,
        "warnings": [],
        "model": "",
        "generated_at": "preview",
    }


class RpaMcpSource(BaseModel):
    type: str = "rpa_skill"
    session_id: str
    skill_name: str = ""


class RpaMcpSanitizeReport(BaseModel):
    removed_steps: list[int] = Field(default_factory=list)
    removed_step_details: list[dict[str, Any]] = Field(default_factory=list)
    removed_params: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RpaMcpToolDefinition(BaseModel):
    id: str
    user_id: str
    name: str
    tool_name: str
    description: str = ""
    enabled: bool = True
    requires_cookies: bool = False
    source: RpaMcpSource
    allowed_domains: list[str] = Field(default_factory=list)
    post_auth_start_url: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    schema_source: str = "rule_inferred"
    output_schema: dict[str, Any] = Field(default_factory=build_rpa_mcp_output_schema)
    recommended_output_schema: dict[str, Any] = Field(default_factory=build_rpa_mcp_output_schema)
    output_schema_confirmed: bool = False
    output_examples: list[dict[str, Any]] = Field(default_factory=list)
    output_inference_report: dict[str, Any] = Field(default_factory=dict)
    semantic_inference: dict[str, Any] = Field(default_factory=build_rpa_mcp_semantic_report)
    sanitize_report: RpaMcpSanitizeReport = Field(default_factory=RpaMcpSanitizeReport)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
