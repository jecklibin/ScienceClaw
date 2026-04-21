from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

WorkflowSegmentKind = Literal["rpa", "script", "mcp", "llm", "mixed"]
WorkflowRunStatus = Literal[
    "draft",
    "recording",
    "configuring",
    "testing",
    "ready_to_publish",
    "published",
    "failed",
]
WorkflowSegmentStatus = Literal["draft", "configured", "testing", "tested", "failed"]
ValueType = Literal["string", "number", "boolean", "file", "json", "secret"]


class ArtifactRef(BaseModel):
    id: str
    name: str
    type: Literal["file", "text", "json", "table"]
    path: Optional[str] = None
    value: Optional[Any] = None
    mime_type: Optional[str] = None
    labels: list[str] = Field(default_factory=list)
    producer_segment_id: Optional[str] = None


class SegmentInput(BaseModel):
    name: str
    type: ValueType
    required: bool = True
    source: Literal["user", "workflow_param", "segment_output", "artifact", "credential"] = "user"
    source_ref: Optional[str] = None
    description: str = ""
    default: Optional[Any] = None


class SegmentOutput(BaseModel):
    name: str
    type: Literal["string", "number", "boolean", "file", "json"]
    description: str = ""
    artifact_ref: Optional[str] = None


class SegmentTestResult(BaseModel):
    status: Literal["idle", "running", "passed", "failed"] = "idle"
    error: Optional[str] = None
    logs: list[str] = Field(default_factory=list)


class WorkflowSegment(BaseModel):
    id: str
    run_id: str
    kind: WorkflowSegmentKind
    order: int
    title: str
    purpose: str
    status: WorkflowSegmentStatus = "draft"
    inputs: list[SegmentInput] = Field(default_factory=list)
    outputs: list[SegmentOutput] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    test_result: SegmentTestResult = Field(default_factory=SegmentTestResult)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class WorkflowRun(BaseModel):
    id: str
    session_id: str
    user_id: str
    intent: str
    status: WorkflowRunStatus = "draft"
    segments: list[WorkflowSegment] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def ordered_segments(self) -> list[WorkflowSegment]:
        return sorted(self.segments, key=lambda segment: segment.order)


class PublishInput(BaseModel):
    name: str
    type: ValueType
    required: bool = True
    description: str = ""
    default: Optional[Any] = None


class PublishOutput(BaseModel):
    name: str
    type: Literal["string", "number", "boolean", "file", "json"]
    description: str = ""


class CredentialRequirement(BaseModel):
    name: str
    type: Literal["browser_session", "api_key", "username_password", "oauth", "secret"]
    description: str


class PublishSegmentSummary(BaseModel):
    id: str
    kind: WorkflowSegmentKind
    title: str
    purpose: str
    status: WorkflowSegmentStatus
    input_count: int = 0
    output_count: int = 0


class PublishWarning(BaseModel):
    code: str
    message: str
    segment_id: Optional[str] = None


class SkillPublishDraft(BaseModel):
    id: str
    run_id: str
    publish_target: Literal["skill", "tool", "mcp"] = "skill"
    skill_name: str
    display_title: str
    description: str
    trigger_examples: list[str] = Field(default_factory=list)
    inputs: list[PublishInput] = Field(default_factory=list)
    outputs: list[PublishOutput] = Field(default_factory=list)
    credentials: list[CredentialRequirement] = Field(default_factory=list)
    segments: list[PublishSegmentSummary] = Field(default_factory=list)
    warnings: list[PublishWarning] = Field(default_factory=list)
