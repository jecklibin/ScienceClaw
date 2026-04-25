from __future__ import annotations

from typing import Any

from .manual_recording_models import ManualRecordedAction
from .trace_models import RPAAcceptedTrace, RPATraceType
from .trace_recorder import recorded_action_to_trace


def _first_locator_candidate(trace: RPAAcceptedTrace) -> dict[str, Any]:
    for candidate in trace.locator_candidates or []:
        if candidate.get("selected"):
            return candidate.get("locator") or candidate
    if trace.locator_candidates:
        candidate = trace.locator_candidates[0]
        return candidate.get("locator") or candidate
    return {}


def _step_by_id(session: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for step in getattr(session, "steps", None) or []:
        step_id = str(getattr(step, "id", "") or "")
        if step_id:
            result[step_id] = step
    return result


def _merge_step_metadata(trace: RPAAcceptedTrace, step: Any | None) -> RPAAcceptedTrace:
    if step is None:
        return trace
    signals = getattr(step, "signals", None)
    if isinstance(signals, dict) and signals:
        trace.signals = {**(trace.signals or {}), **signals}
    return trace


def recorded_action_to_mcp_step(action: ManualRecordedAction, *, step: Any | None = None) -> dict[str, Any]:
    trace = _merge_step_metadata(recorded_action_to_trace(action), step)
    page_state = action.page_state if isinstance(action.page_state, dict) else {}
    return {
        "id": action.step_id or trace.trace_id,
        "action": action.action_kind.value,
        "target": action.target or {},
        "frame_path": list(action.frame_path or trace.frame_path or []),
        "locator_candidates": action.raw_candidates or trace.locator_candidates,
        "validation": dict(action.validation or {}),
        "signals": dict(trace.signals or {}),
        "value": action.value,
        "description": action.description or action.action_kind.value,
        "label": action.action_kind.value,
        "url": str(page_state.get("url", "") or trace.after_page.url or ""),
        "source": "record",
        "configurable": True,
        "rpa_trace": trace.model_dump(mode="json"),
    }


def trace_to_mcp_step(trace: RPAAcceptedTrace) -> dict[str, Any]:
    locator = _first_locator_candidate(trace)
    if trace.trace_type == RPATraceType.AI_OPERATION:
        action = "ai_script"
        value = trace.ai_execution.code if trace.ai_execution else ""
    elif trace.trace_type == RPATraceType.NAVIGATION:
        action = "navigate"
        value = trace.value
    else:
        action = trace.action or trace.trace_type.value
        value = trace.value

    return {
        "id": trace.trace_id,
        "action": action,
        "target": locator,
        "frame_path": list(trace.frame_path or []),
        "locator_candidates": list(trace.locator_candidates or []),
        "validation": dict(trace.validation or {}),
        "signals": dict(trace.signals or {}),
        "value": value,
        "description": trace.description or trace.user_instruction or trace.trace_type.value,
        "label": trace.user_instruction or trace.action or trace.trace_type.value,
        "url": trace.after_page.url or "",
        "source": "ai" if trace.source == "ai" or trace.trace_type == RPATraceType.AI_OPERATION else "record",
        "prompt": trace.user_instruction,
        "result_key": trace.output_key,
        "configurable": False,
        "rpa_trace": trace.model_dump(mode="json"),
    }


def session_to_mcp_steps(session: Any) -> list[dict[str, Any]]:
    recorded_actions = list(getattr(session, "recorded_actions", None) or [])
    traces = list(getattr(session, "traces", None) or [])
    legacy_steps = list(getattr(session, "steps", None) or [])

    if not recorded_actions and not traces:
        return [step.model_dump(mode="json") for step in legacy_steps]

    steps_by_id = _step_by_id(session)
    actions_by_trace_id = {
        f"trace-{action.step_id}": action
        for action in recorded_actions
        if action.step_id
    }
    emitted_action_ids: set[str] = set()
    projected: list[dict[str, Any]] = []

    for trace in traces:
        replacement = actions_by_trace_id.get(trace.trace_id)
        if replacement is not None:
            projected.append(recorded_action_to_mcp_step(replacement, step=steps_by_id.get(replacement.step_id)))
            emitted_action_ids.add(replacement.step_id)
            continue
        projected.append(trace_to_mcp_step(trace))

    for action in recorded_actions:
        if action.step_id in emitted_action_ids:
            continue
        projected.append(recorded_action_to_mcp_step(action, step=steps_by_id.get(action.step_id)))

    return projected
