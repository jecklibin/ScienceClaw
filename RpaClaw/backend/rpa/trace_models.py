from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class RPATraceType(str, Enum):
    MANUAL_ACTION = "manual_action"
    AI_OPERATION = "ai_operation"
    DATA_CAPTURE = "data_capture"
    DATAFLOW_FILL = "dataflow_fill"
    NAVIGATION = "navigation"


class RPAPageState(BaseModel):
    url: str = ""
    title: str = ""
    snapshot_summary: Dict[str, Any] = Field(default_factory=dict)


class RPAAIExecution(BaseModel):
    language: str = "python"
    code: str = ""
    output: Any = None
    error: Optional[str] = None
    repair_attempted: bool = False


class RPALocatorStabilityCandidate(BaseModel):
    locator: Dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    confidence: str = ""


class RPALocatorStabilityMetadata(BaseModel):
    primary_locator: Dict[str, Any] = Field(default_factory=dict)
    stable_self_signals: Dict[str, Any] = Field(default_factory=dict)
    stable_anchor_signals: Dict[str, Any] = Field(default_factory=dict)
    unstable_signals: List[Dict[str, Any]] = Field(default_factory=list)
    alternate_locators: List[RPALocatorStabilityCandidate] = Field(default_factory=list)


class RPATargetField(BaseModel):
    label: str = ""
    role: str = ""
    placeholder: str = ""
    locator_candidates: List[Dict[str, Any]] = Field(default_factory=list)


class RPADataflowMapping(BaseModel):
    target_field: RPATargetField = Field(default_factory=RPATargetField)
    value: Any = None
    source_ref_candidates: List[str] = Field(default_factory=list)
    selected_source_ref: Optional[str] = None
    confidence: str = ""


class RPAAcceptedTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: f"trace-{uuid4().hex}")
    trace_type: RPATraceType
    source: str = "record"
    user_instruction: Optional[str] = None
    action: Optional[str] = None
    description: str = ""
    before_page: RPAPageState = Field(default_factory=RPAPageState)
    after_page: RPAPageState = Field(default_factory=RPAPageState)
    frame_path: List[str] = Field(default_factory=list)
    locator_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    signals: Dict[str, Any] = Field(default_factory=dict)
    value: Any = None
    output_key: Optional[str] = None
    output: Any = None
    ai_execution: Optional[RPAAIExecution] = None
    locator_stability: Optional[RPALocatorStabilityMetadata] = None
    dataflow: Optional[RPADataflowMapping] = None
    diagnostics_ref: Optional[str] = None
    accepted: bool = True
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime = Field(default_factory=datetime.now)


class RPATraceDiagnostic(BaseModel):
    diagnostic_id: str = Field(default_factory=lambda: f"diag-{uuid4().hex}")
    trace_id: Optional[str] = None
    source: str = "ai"
    message: str = ""
    raw: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


class RPARuntimeResults(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)

    def write(self, key: Optional[str], value: Any) -> None:
        if isinstance(key, str) and key.strip():
            self.values[key.strip()] = value

    def resolve_ref(self, ref: str) -> Any:
        current: Any = self.values
        for segment in str(ref or "").split("."):
            if isinstance(current, dict) and segment in current:
                current = current[segment]
                continue
            if isinstance(current, list) and segment.isdigit():
                index = int(segment)
                if 0 <= index < len(current):
                    current = current[index]
                    continue
            raise KeyError(ref)
        return current

    def find_value_refs(self, value: Any) -> List[str]:
        refs: List[str] = []

        def visit(node: Any, path: List[str]) -> None:
            if isinstance(node, dict):
                for key, item in node.items():
                    visit(item, path + [str(key)])
                return
            if isinstance(node, list):
                for index, item in enumerate(node):
                    visit(item, path + [str(index)])
                return
            if node == value and path:
                refs.append(".".join(path))

        visit(self.values, [])
        return refs

