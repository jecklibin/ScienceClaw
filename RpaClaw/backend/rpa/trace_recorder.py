from __future__ import annotations

import json
from typing import Any, Dict, List

from .manual_recording_models import ManualRecordedAction
from .trace_locator_utils import normalize_locator, normalize_locator_candidates
from .trace_models import (
    RPAAcceptedTrace,
    RPADataflowMapping,
    RPAPageState,
    RPATargetField,
    RPATraceType,
    RPARuntimeResults,
)


def _step_get(step: Dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(step, dict):
        return step.get(key, default)
    return getattr(step, key, default)


def _parse_target_locator(raw_target: Any) -> Dict[str, Any]:
    if isinstance(raw_target, dict):
        return raw_target
    if not isinstance(raw_target, str) or not raw_target.strip():
        return {}
    try:
        parsed = json.loads(raw_target)
    except Exception:
        return {"method": "css", "value": raw_target}
    return parsed if isinstance(parsed, dict) else {}


def _locator_candidates(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = _step_get(step, "locator_candidates", []) or []
    target = _parse_target_locator(_step_get(step, "target", ""))
    normalized = normalize_locator_candidates(candidates, target=target)
    if normalized:
        return normalized
    target = normalize_locator(target)
    if target:
        return [{"kind": target.get("method", "locator"), "locator": target, "selected": True}]
    return []


def _page_state_from_step(step: Dict[str, Any], *, prefer_after: bool = True) -> RPAPageState:
    url = _step_get(step, "url", "") or _step_get(step, "page_url", "") or ""
    title = _step_get(step, "title", "") or _step_get(step, "page_title", "") or ""
    if prefer_after and _step_get(step, "action", "") in {"navigate", "goto"}:
        url = _step_get(step, "url", "") or _step_get(step, "target", "") or url
    return RPAPageState(url=str(url or ""), title=str(title or ""))


def manual_step_to_trace(step: Dict[str, Any]) -> RPAAcceptedTrace:
    action = str(_step_get(step, "action", "") or "").strip()
    trace_type = RPATraceType.NAVIGATION if action in {"navigate", "goto"} else RPATraceType.MANUAL_ACTION
    if action == "extract_text":
        trace_type = RPATraceType.DATA_CAPTURE

    trace_id = f"trace-{_step_get(step, 'id', '') or action or 'manual'}"
    after_page = _page_state_from_step(step, prefer_after=True)
    if trace_type == RPATraceType.NAVIGATION and not after_page.url:
        after_page.url = str(_step_get(step, "target", "") or "")

    return RPAAcceptedTrace(
        trace_id=trace_id,
        trace_type=trace_type,
        source="manual" if _step_get(step, "source", "record") == "record" else str(_step_get(step, "source", "record")),
        action=action,
        description=str(_step_get(step, "description", "") or action or "Manual action"),
        before_page=RPAPageState(url=str(_step_get(step, "before_url", "") or "")),
        after_page=after_page,
        locator_candidates=_locator_candidates(step),
        validation=dict(_step_get(step, "validation", {}) or {}),
        signals=dict(_step_get(step, "signals", {}) or {}),
        value=_step_get(step, "value"),
        output_key=_step_get(step, "result_key"),
        output=_step_get(step, "output"),
    )


def recorded_action_to_trace(action: ManualRecordedAction) -> RPAAcceptedTrace:
    page_state = action.page_state if isinstance(action.page_state, dict) else {}
    locator = normalize_locator(action.target or {})
    locator_candidates = [{"locator": locator, "selected": True}] if locator else []
    trace_type = (
        RPATraceType.NAVIGATION
        if action.action_kind.value == "navigate"
        else RPATraceType.MANUAL_ACTION
    )
    after_page = RPAPageState(
        url=str(page_state.get("url", "") or ""),
        title=str(page_state.get("title", "") or ""),
    )
    return RPAAcceptedTrace(
        trace_id=f"trace-{action.step_id or action.action_kind.value}",
        trace_type=trace_type,
        source="manual",
        action=action.action_kind.value,
        description=action.description,
        after_page=after_page,
        locator_candidates=locator_candidates,
        validation=dict(action.validation or {}),
        value=action.value,
    )


def infer_dataflow_for_fill(trace: RPAAcceptedTrace, runtime_results: RPARuntimeResults) -> RPAAcceptedTrace:
    if trace.action != "fill":
        return trace
    refs = runtime_results.find_value_refs(trace.value)
    if not refs:
        return trace

    selected_locator = {}
    if trace.locator_candidates:
        selected = next((item for item in trace.locator_candidates if item.get("selected")), trace.locator_candidates[0])
        selected_locator = normalize_locator(selected.get("locator") or selected)

    trace.dataflow = RPADataflowMapping(
        target_field=RPATargetField(locator_candidates=list(trace.locator_candidates or [])),
        value=trace.value,
        source_ref_candidates=refs,
        selected_source_ref=refs[0],
        confidence="exact_value_match",
    )
    if selected_locator:
        trace.dataflow.target_field.locator_candidates = [{"locator": selected_locator, "selected": True}]
    trace.trace_type = RPATraceType.DATAFLOW_FILL
    return trace

