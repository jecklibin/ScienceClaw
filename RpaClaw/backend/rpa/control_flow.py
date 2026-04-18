from __future__ import annotations

import re
from typing import Any, Dict


_POLLING_PATTERNS = [
    r"\buntil\b",
    r"\bwait\s+until\b",
    r"\brepeat\b",
    r"\bretry\b",
    r"\bpoll\b",
    r"\bevery\b",
    r"直到",
    r"每隔",
    r"重复",
    r"轮询",
    r"\bevery\s+\d+\s+(?:ms|millisecond|milliseconds|s|sec|second|seconds|m|min|minute|minutes|h|hour|hours)\b",
]

_CONDITIONAL_PATTERNS = [
    r"\bif\b",
    r"\belse\b",
    r"\bunless\b",
    r"\botherwise\b",
    r"如果",
    r"否则",
    r"不然",
]

_DYNAMIC_SELECTION_PATTERNS = [
    r"\bhighest\b",
    r"\blowest\b",
    r"\blatest\b",
    r"\bearliest\b",
    r"\bmost\b",
    r"(?<!\bat\s)\bleast\b",
    r"最高",
    r"最低",
    r"最新",
    r"最早",
    r"最多",
    r"最少",
]

_VALID_UPGRADE_REASONS = {
    "polling_loop",
    "conditional_branch",
    "dynamic_selection",
    "custom_logic",
}


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def detect_upgrade_reason(text: str) -> str:
    if not text:
        return "none"

    if _matches_any(text, _POLLING_PATTERNS):
        return "polling_loop"

    if _matches_any(text, _CONDITIONAL_PATTERNS):
        return "conditional_branch"

    if _matches_any(text, _DYNAMIC_SELECTION_PATTERNS):
        return "dynamic_selection"

    return "none"


def normalize_ai_script_function(code: str) -> str:
    code = (code or "").strip()
    if not code:
        return "async def run(page):\n    return None"

    if code.startswith("async def run(") or code.startswith("def run("):
        return code

    indented_body = "\n".join(
        f"    {line}" if line.strip() else ""
        for line in code.splitlines()
    )
    return f"async def run(page):\n{indented_body or '    return None'}"


def build_ai_script_step(
    prompt: str,
    description: str,
    code: str,
    parsed: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_code = normalize_ai_script_function(code)
    parsed = parsed or {}
    detection_text = "\n".join(
        str(part).strip()
        for part in (
            prompt,
            description,
            parsed.get("thought"),
            code,
        )
        if part
    )
    detected_upgrade_reason = detect_upgrade_reason(detection_text)
    if detected_upgrade_reason != "none":
        upgrade_reason = detected_upgrade_reason
    else:
        parsed_upgrade_reason = str(parsed.get("upgrade_reason", "")).strip()
        upgrade_reason = parsed_upgrade_reason if parsed_upgrade_reason in _VALID_UPGRADE_REASONS else "custom_logic"

    assistant_diagnostics: Dict[str, Any] = {
        "execution_mode": "code",
        "upgrade_reason": upgrade_reason,
        "template": parsed.get("template"),
    }

    for key in ("interval_ms", "timeout_ms", "condition", "locators"):
        if key in parsed:
            assistant_diagnostics[key] = parsed[key]

    return {
        "action": "ai_script",
        "source": "ai",
        "prompt": prompt,
        "description": description,
        "value": normalized_code,
        "assistant_diagnostics": assistant_diagnostics,
    }
