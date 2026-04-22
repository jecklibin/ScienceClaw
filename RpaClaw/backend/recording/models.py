from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

RecordingRunStatus = Literal[
    "draft",
    "recording",
    "waiting_user",
    "processing_artifacts",
    "ready_for_next_segment",
    "testing",
    "needs_repair",
    "ready_to_publish",
    "blocked",
    "failed",
    "completed",
    "saved",
]

RecordingSegmentStatus = Literal[
    "draft",
    "recording",
    "running",
    "validating",
    "ready",
    "blocked",
    "failed",
    "completed",
    "aborted",
]


class RecordingArtifact(BaseModel):
    id: str
    run_id: str
    segment_id: str
    name: str
    type: Literal["file", "text", "json", "table"]
    path: Optional[str] = None
    value: Optional[Any] = None
    mime_type: Optional[str] = None
    labels: list[str] = Field(default_factory=list)
    producer_step_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


class RecordingSegment(BaseModel):
    id: str
    run_id: str
    kind: Literal["rpa", "mcp", "script", "mixed"]
    intent: str
    status: RecordingSegmentStatus = "draft"
    steps: list[dict[str, Any]] = Field(default_factory=list)
    imports: dict[str, Any] = Field(default_factory=dict)
    exports: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[RecordingArtifact] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None


class RecordingRun(BaseModel):
    id: str
    session_id: str
    user_id: str
    type: Literal["rpa", "mcp", "mixed"] = "rpa"
    status: RecordingRunStatus = "draft"
    active_segment_id: Optional[str] = None
    segments: list[RecordingSegment] = Field(default_factory=list)
    artifact_index: list[RecordingArtifact] = Field(default_factory=list)
    save_intent: Optional[str] = None
    publish_target: Optional[Literal["skill", "tool"]] = None
    testing: dict[str, Any] = Field(default_factory=lambda: {"status": "idle"})
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
