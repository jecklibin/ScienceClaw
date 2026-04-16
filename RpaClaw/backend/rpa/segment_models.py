from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


SegmentKind = Literal["read_only", "state_changing"]
StopReason = Literal["goal_reached", "before_state_change", "after_state_change"]


@dataclass(slots=True)
class SegmentSpec:
    segment_goal: str
    segment_kind: SegmentKind
    stop_reason: StopReason
    expected_outcome: Dict[str, Any]
    completion_check: Dict[str, Any]
    code: str
    notes: str = ""


@dataclass(slots=True)
class SegmentRunResult:
    success: bool
    output: str = ""
    error: str = ""
    page_changed: bool = False
    selected_artifacts: Dict[str, Any] = field(default_factory=dict)
    before_snapshot: Optional[Dict[str, Any]] = None
    after_snapshot: Optional[Dict[str, Any]] = None
    error_code: str = ""


@dataclass(slots=True)
class SegmentValidationResult:
    passed: bool
    goal_completed: bool
    reason: str = ""
