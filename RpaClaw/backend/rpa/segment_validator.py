import re

from backend.rpa.segment_models import SegmentRunResult, SegmentSpec, SegmentValidationResult


ACTION_GOAL_HINTS = (
    "\u70b9\u51fb",
    "\u6253\u5f00",
    "\u8fdb\u5165",
    "\u4e0b\u8f7d",
    "\u63d0\u4ea4",
    "\u786e\u8ba4",
    "click",
    "open",
    "enter",
    "download",
    "submit",
)
READ_GOAL_VERB_RE = re.compile(
    r"获取|提取|读取|返回|输出|告诉我|extract|read|get\b|return\b|output\b|tell me|show\b",
    re.IGNORECASE,
)
READ_GOAL_OBJECT_RE = re.compile(
    r"标题|名称|文本|内容|结果|链接|地址|title|name|text|content|value|url",
    re.IGNORECASE,
)


def _text_variants(value: str) -> set[str]:
    normalized = " ".join(str(value or "").strip().lower().split())
    if not normalized:
        return set()

    variants = {normalized}
    variants.add(re.sub(r"\s+", "", normalized))

    slash_compact = re.sub(r"\s*/\s*", "/", normalized)
    variants.add(slash_compact)
    variants.add(re.sub(r"\s+", "", slash_compact))
    return {variant for variant in variants if variant}


def _goal_requires_observable_output(goal: str) -> bool:
    text = str(goal or "")
    return bool(READ_GOAL_VERB_RE.search(text) and READ_GOAL_OBJECT_RE.search(text))


def _goal_is_action(goal: str) -> bool:
    text = str(goal or "").lower()
    return any(hint in text for hint in ACTION_GOAL_HINTS)


def _has_meaningful_output(output: str) -> bool:
    text = str(output or "").strip()
    return bool(text and text not in {"ok", "None"})


def _snapshot_texts(snapshot: dict) -> list[str]:
    texts = [
        str(snapshot.get("url", "") or ""),
        str(snapshot.get("title", "") or ""),
    ]

    for frame in snapshot.get("frames", []) or []:
        texts.append(str(frame.get("frame_hint", "") or ""))
        for element in frame.get("elements", []) or []:
            texts.append(str(element.get("name", "") or ""))
            texts.append(str(element.get("href", "") or ""))
        for collection in frame.get("collections", []) or []:
            for item in collection.get("items", []) or []:
                texts.append(str(item.get("name", "") or ""))
                texts.append(str(item.get("href", "") or ""))

    for node in snapshot.get("actionable_nodes", []) or []:
        texts.append(str(node.get("name", "") or ""))

    for node in snapshot.get("content_nodes", []) or []:
        texts.append(str(node.get("text", "") or ""))

    for container in snapshot.get("containers", []) or []:
        texts.append(str(container.get("name", "") or ""))
        texts.append(str(container.get("summary", "") or ""))

    return [text for text in texts if text]


async def validate_segment_result(
    *,
    goal: str,
    spec: SegmentSpec,
    run_result: SegmentRunResult,
) -> SegmentValidationResult:
    if not run_result.success:
        return SegmentValidationResult(passed=False, goal_completed=False, reason=run_result.error or "segment_failed")

    if spec.segment_kind == "state_changing" and not run_result.page_changed:
        return SegmentValidationResult(
            passed=False,
            goal_completed=False,
            reason="expected_page_change_not_observed",
        )

    lowered_goal = goal.lower()
    if any(hint in lowered_goal for hint in ACTION_GOAL_HINTS) and spec.segment_kind == "read_only":
        return SegmentValidationResult(
            passed=False,
            goal_completed=False,
            reason="action_goal_cannot_finish_with_read_only_segment",
        )

    if _goal_requires_observable_output(goal) and spec.segment_kind == "read_only" and not _has_meaningful_output(run_result.output):
        return SegmentValidationResult(
            passed=False,
            goal_completed=False,
            reason="read_goal_requires_output",
        )

    completion_check = spec.completion_check or {}
    selected_target_key = str(completion_check.get("selected_target_key", "") or "").strip()
    page_contains_selected_target = bool(completion_check.get("page_contains_selected_target"))
    if selected_target_key and page_contains_selected_target:
        selected_artifacts = run_result.selected_artifacts or {}
        candidate_values: list[str] = []

        primary_value = selected_artifacts.get(selected_target_key)
        if isinstance(primary_value, str) and primary_value.strip():
            candidate_values.append(primary_value)

        if spec.segment_kind == "read_only" and _has_meaningful_output(run_result.output):
            candidate_values.append(str(run_result.output).strip())

        for key, value in selected_artifacts.items():
            if key == selected_target_key:
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            if spec.segment_kind == "read_only" or str(key).startswith("selected_"):
                candidate_values.append(value)

        after_snapshot = run_result.after_snapshot or {}
        haystack_variants = set()
        for haystack in _snapshot_texts(after_snapshot):
            haystack_variants.update(_text_variants(haystack))

        matched = False
        for candidate in candidate_values:
            candidate_variants = _text_variants(candidate)
            if any(candidate_variant and candidate_variant in haystack_variant for candidate_variant in candidate_variants for haystack_variant in haystack_variants):
                matched = True
                break

        if candidate_values and not matched:
            return SegmentValidationResult(
                passed=False,
                goal_completed=False,
                reason="selected_target_not_observed_after_segment",
            )

    goal_is_read = _goal_requires_observable_output(goal)
    goal_is_action = _goal_is_action(goal)
    if goal_is_read:
        goal_completed = spec.segment_kind == "read_only" and _has_meaningful_output(run_result.output)
    elif goal_is_action:
        goal_completed = spec.segment_kind == "state_changing" and run_result.page_changed
    else:
        goal_completed = bool(run_result.page_changed or (spec.segment_kind == "read_only" and _has_meaningful_output(run_result.output)))

    return SegmentValidationResult(
        passed=True,
        goal_completed=goal_completed,
    )
