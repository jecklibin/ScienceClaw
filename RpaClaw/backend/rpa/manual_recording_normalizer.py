from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .manual_recording_models import (
    ManualActionKind,
    ManualRecordedAction,
    ManualRecordingDiagnostic,
)
from .trace_locator_utils import normalize_locator


_ROLE_FIRST_RE = re.compile(r'^page\.get_by_role\("(?P<role>[^"]+)"\)\.first$')

_INTERACTIVE_ACTIONS = {
    ManualActionKind.HOVER.value,
    ManualActionKind.CLICK.value,
    ManualActionKind.FILL.value,
    ManualActionKind.PRESS.value,
    ManualActionKind.SELECT.value,
    ManualActionKind.CHECK.value,
    ManualActionKind.UNCHECK.value,
    ManualActionKind.NAVIGATE_CLICK.value,
    ManualActionKind.NAVIGATE_PRESS.value,
}


@dataclass
class ManualRecordingOutcome:
    accepted_action: Optional[ManualRecordedAction]
    diagnostic: Optional[ManualRecordingDiagnostic]


def parse_playwright_locator_string(value: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    match = _ROLE_FIRST_RE.match(text)
    if match:
        return {
            "method": "nth",
            "locator": {"method": "role", "role": match.group("role")},
            "index": 0,
        }
    return {}


def normalize_manual_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(candidate or {})
    locator = normalize_locator(normalized.get("locator") if "locator" in normalized else normalized)
    if locator:
        normalized["locator"] = locator
        return normalized

    playwright_locator = normalized.get("playwright_locator")
    if isinstance(playwright_locator, str):
        parsed = parse_playwright_locator_string(playwright_locator)
        if parsed:
            normalized["locator"] = parsed
    return normalized


def _coerce_locator_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def resolve_canonical_target(*, target: Any, locator_candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    normalized_target = normalize_locator(_coerce_locator_payload(target))
    if normalized_target:
        return normalized_target

    selected_candidate = next(
        (candidate for candidate in locator_candidates if candidate.get("selected") is True),
        locator_candidates[0] if locator_candidates else None,
    )
    if isinstance(selected_candidate, dict):
        locator = normalize_locator(
            selected_candidate.get("locator") if "locator" in selected_candidate else selected_candidate
        )
        if locator:
            return locator
    return {}


def build_manual_recording_outcome(
    *,
    step_id: str = "",
    action: str,
    description: str,
    target: Any,
    frame_path: Optional[List[str]] = None,
    locator_candidates: List[Dict[str, Any]],
    validation: Dict[str, Any],
    value: Any = None,
    element_snapshot: Optional[Dict[str, Any]] = None,
    page_state: Optional[Dict[str, Any]] = None,
    signals: Optional[Dict[str, Any]] = None,
) -> ManualRecordingOutcome:
    try:
        action_kind = ManualActionKind(action)
    except ValueError:
        return ManualRecordingOutcome(accepted_action=None, diagnostic=None)

    normalized_candidates = [
        normalize_manual_candidate(candidate)
        for candidate in (locator_candidates or [])
        if isinstance(candidate, dict)
    ]
    normalized_validation = dict(validation or {})
    canonical_target = resolve_canonical_target(
        target=target,
        locator_candidates=normalized_candidates,
    )

    if action in _INTERACTIVE_ACTIONS and not canonical_target:
        return ManualRecordingOutcome(
            accepted_action=None,
            diagnostic=ManualRecordingDiagnostic(
                related_step_id=step_id,
                related_action_kind=action_kind,
                failure_reason="canonical_target_missing",
                raw_candidates=normalized_candidates,
                element_snapshot=dict(element_snapshot or {}),
                page_state=dict(page_state or {}),
            ),
        )

    accepted_action = ManualRecordedAction(
        step_id=step_id,
        action_kind=action_kind,
        description=description,
        target=canonical_target,
        frame_path=list(frame_path or []),
        validation=normalized_validation,
        raw_candidates=normalized_candidates,
        page_state=dict(page_state or {}),
        value=value,
    )
    return ManualRecordingOutcome(accepted_action=accepted_action, diagnostic=None)
