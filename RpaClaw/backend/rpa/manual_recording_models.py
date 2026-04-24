from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field, model_validator


class ManualActionKind(str, Enum):
    HOVER = "hover"
    CLICK = "click"
    FILL = "fill"
    PRESS = "press"
    SELECT = "select"
    CHECK = "check"
    UNCHECK = "uncheck"
    NAVIGATE = "navigate"
    NAVIGATE_CLICK = "navigate_click"
    NAVIGATE_PRESS = "navigate_press"


INTERACTIVE_ACTIONS = {
    ManualActionKind.HOVER,
    ManualActionKind.CLICK,
    ManualActionKind.FILL,
    ManualActionKind.PRESS,
    ManualActionKind.SELECT,
    ManualActionKind.CHECK,
    ManualActionKind.UNCHECK,
    ManualActionKind.NAVIGATE_CLICK,
    ManualActionKind.NAVIGATE_PRESS,
}

CANONICAL_TARGET_METHODS = {
    "role",
    "placeholder",
    "label",
    "text",
    "title",
    "alt",
    "css",
    "nested",
    "nth",
}


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _canonicalize_target(target: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(target, dict):
        return target

    canonical_target = dict(target)
    method = canonical_target.get("method")
    if method == "nested":
        if isinstance(canonical_target.get("parent"), dict):
            canonical_target["parent"] = _canonicalize_target(canonical_target["parent"])
        if isinstance(canonical_target.get("child"), dict):
            canonical_target["child"] = _canonicalize_target(canonical_target["child"])
        return canonical_target

    if method == "nth":
        if "locator" not in canonical_target and "base" in canonical_target:
            canonical_target["locator"] = canonical_target.pop("base")
        if isinstance(canonical_target.get("locator"), dict):
            canonical_target["locator"] = _canonicalize_target(canonical_target["locator"])
        return canonical_target

    return canonical_target


def _is_canonical_target(target: Dict[str, Any]) -> bool:
    if not isinstance(target, dict) or not target:
        return False
    method = target.get("method")
    if not isinstance(method, str) or method not in CANONICAL_TARGET_METHODS:
        return False

    if method == "role":
        return _is_non_empty_string(target.get("role"))
    if method in {"placeholder", "label", "text", "title", "alt", "css"}:
        return _is_non_empty_string(target.get("value"))
    if method == "nested":
        parent = target.get("parent")
        child = target.get("child")
        return (
            isinstance(parent, dict)
            and isinstance(child, dict)
            and _is_canonical_target(parent)
            and _is_canonical_target(child)
        )
    if method == "nth":
        base = target.get("locator")
        index = target.get("index")
        return (
            isinstance(base, dict)
            and _is_canonical_target(base)
            and isinstance(index, int)
            and index >= 0
        )
    return False


class ManualRecordedAction(BaseModel):
    step_id: str = ""
    action_kind: ManualActionKind
    description: str = ""
    target: Dict[str, Any] = Field(default_factory=dict)
    validation: Dict[str, Any] = Field(default_factory=dict)
    raw_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    page_state: Dict[str, Any] = Field(default_factory=dict)
    value: Any = None

    @model_validator(mode="after")
    def validate_target_invariants(self) -> "ManualRecordedAction":
        self.target = _canonicalize_target(self.target)
        if self.action_kind in INTERACTIVE_ACTIONS and not _is_canonical_target(self.target):
            raise ValueError("accepted interactive action requires canonical target")
        return self


class ManualRecordingDiagnostic(BaseModel):
    related_step_id: str = ""
    related_action_kind: ManualActionKind
    failure_reason: str
    raw_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    element_snapshot: Dict[str, Any] = Field(default_factory=dict)
    page_state: Dict[str, Any] = Field(default_factory=dict)
