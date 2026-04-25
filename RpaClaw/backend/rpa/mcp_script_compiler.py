from __future__ import annotations

from typing import Any

from .generator import PlaywrightGenerator
from .trace_models import RPAAcceptedTrace
from .trace_recorder import manual_step_to_trace
from .trace_skill_compiler import TraceSkillCompiler


def has_trace_backed_steps(steps: list[dict[str, Any]]) -> bool:
    return any(isinstance(step.get("rpa_trace"), dict) for step in steps)


def _step_to_trace(step: dict[str, Any]) -> RPAAcceptedTrace:
    trace_payload = step.get("rpa_trace")
    if isinstance(trace_payload, dict):
        return RPAAcceptedTrace.model_validate(trace_payload)
    return manual_step_to_trace(step)


def generate_mcp_script(
    steps: list[dict[str, Any]],
    params: dict[str, Any] | None = None,
    *,
    is_local: bool = False,
    test_mode: bool = False,
) -> str:
    if has_trace_backed_steps(steps):
        traces = [_step_to_trace(step) for step in steps]
        return TraceSkillCompiler().generate_script(
            traces,
            params or {},
            is_local=is_local,
            test_mode=test_mode,
        )
    return PlaywrightGenerator().generate_script(
        steps,
        params or {},
        is_local=is_local,
        test_mode=test_mode,
    )
