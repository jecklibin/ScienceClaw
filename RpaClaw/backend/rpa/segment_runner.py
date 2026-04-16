from typing import Any, Awaitable, Callable, Dict

from playwright.async_api import Page

from backend.rpa.segment_models import SegmentRunResult, SegmentSpec


def _snapshot_visible_texts(snapshot: Dict[str, Any]) -> list[str]:
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


def _snapshot_signature(snapshot: Dict[str, Any]) -> tuple[str, ...]:
    normalized = []
    for text in _snapshot_visible_texts(snapshot):
        compact = " ".join(str(text).split())
        if compact:
            normalized.append(compact)
    return tuple(normalized)


def _snapshot_changed(before_snapshot: Dict[str, Any], after_snapshot: Dict[str, Any]) -> bool:
    return _snapshot_signature(before_snapshot) != _snapshot_signature(after_snapshot)


async def run_segment(
    *,
    page: Page,
    spec: SegmentSpec,
    executor: Callable[[Page, str], Awaitable[Dict[str, Any]]],
    snapshot_builder: Callable[[Page], Awaitable[Dict[str, Any]]],
) -> SegmentRunResult:
    before_snapshot = await snapshot_builder(page)
    result = await executor(page, spec.code)
    after_snapshot = await snapshot_builder(page)

    page_changed = _snapshot_changed(before_snapshot, after_snapshot)
    if not page_changed:
        page_changed = bool(result.get("page_changed"))
    if not page_changed and spec.segment_kind == "state_changing" and bool(result.get("success")):
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        for _ in range(3):
            if callable(wait_for_timeout):
                await wait_for_timeout(250)
            try:
                reobserved_snapshot = await snapshot_builder(page)
            except (StopAsyncIteration, StopIteration):
                break
            if _snapshot_changed(before_snapshot, reobserved_snapshot):
                after_snapshot = reobserved_snapshot
                page_changed = True
                break
            after_snapshot = reobserved_snapshot

    return SegmentRunResult(
        success=bool(result.get("success")),
        output=str(result.get("output", "") or ""),
        error=str(result.get("error", "") or ""),
        page_changed=page_changed,
        selected_artifacts=dict(result.get("selected_artifacts") or {}),
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        error_code=str(result.get("error_code", "") or ""),
    )
