from __future__ import annotations

from dataclasses import dataclass

_RECORDING_VERBS = (
    "录制",
    "录个",
    "录一个",
    "录一段",
    "帮我录",
    "我要录",
    "想录",
)

_RECORDING_TARGET_HINTS = (
    "流程",
    "业务流程",
    "技能",
    "工具",
    "网页",
    "下载",
    "操作",
    "步骤",
    "自动化",
    "mcp",
    "rpa",
)


@dataclass(slots=True)
class RecordingIntent:
    kind: str
    requires_workbench: bool
    save_intent: str | None = None


def _normalize_text(message: str) -> str:
    return "".join(message.strip().lower().split())


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def detect_recording_intent(message: str) -> RecordingIntent | None:
    text = _normalize_text(message)
    if not text:
        return None

    if not _contains_any(text, _RECORDING_VERBS):
        return None

    if not _contains_any(text, _RECORDING_TARGET_HINTS):
        return None

    is_mcp = "mcp" in text
    if "技能" in text:
        save_intent = "skill"
    elif "工具" in text or is_mcp:
        save_intent = "tool"
    else:
        save_intent = None

    return RecordingIntent(
        kind="mcp" if is_mcp else "rpa",
        requires_workbench=True,
        save_intent=save_intent,
    )
