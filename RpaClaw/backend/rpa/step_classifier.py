from __future__ import annotations

from typing import Any, Dict, Optional


SCRIPT_STEP = "script_step"
AGENT_STEP = "agent_step"

_BRANCHING_KEYWORDS = (
    "if ",
    "elif ",
    "else:",
    "for ",
    "while ",
    "判断",
    "如果",
    "否则",
    "根据",
    "逐个",
    "每个",
    "循环",
)


def classify_candidate_step(
    prompt: str,
    structured_intent: Optional[Dict[str, Any]] = None,
    code: Optional[str] = None,
) -> str:
    """Classify candidate as script_step or agent_step with script-first bias."""
    normalized_prompt = (prompt or "").strip().lower()
    normalized_code = (code or "").strip().lower()
    action = str((structured_intent or {}).get("action", "")).strip().lower()

    if _contains_branching_signal(normalized_prompt) or _contains_branching_signal(normalized_code):
        return AGENT_STEP

    if action in {"navigate", "click", "fill", "extract_text", "press"}:
        return SCRIPT_STEP

    return SCRIPT_STEP


def _contains_branching_signal(text: str) -> bool:
    return any(keyword in text for keyword in _BRANCHING_KEYWORDS)
