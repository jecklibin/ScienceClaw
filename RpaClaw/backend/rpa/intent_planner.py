import re
from typing import Any, Dict, Optional

from backend.rpa.intent_models import IntentPlan, IntentSelection, IntentTarget


FLOW_HINT_RE = re.compile(r"\b(if|until|retry|repeat|otherwise)\b|如果|直到|重试|重复|否则", re.IGNORECASE)
READ_HINT_RE = re.compile(r"获取|提取|读取|返回|输出|title|标题|name|名称|text|文本|内容|issue 标题|issues 的标题", re.IGNORECASE)
CLICK_HINT_RE = re.compile(r"点击|打开|进入|click|open", re.IGNORECASE)
DOWNLOAD_HINT_RE = re.compile(r"下载|download", re.IGNORECASE)
FIRST_HINT_RE = re.compile(r"第一个|首个|first\b|1st\b", re.IGNORECASE)
LAST_HINT_RE = re.compile(r"最后一个|last\b", re.IGNORECASE)
MAX_HINT_RE = re.compile(r"最多|最大|最高|most\b|max\b|highest\b", re.IGNORECASE)
MIN_HINT_RE = re.compile(r"最少|最小|最低|least\b|min\b|lowest\b", re.IGNORECASE)
LATEST_HINT_RE = re.compile(r"最新|最近|latest\b|newest\b|recent\b", re.IGNORECASE)
EARLIEST_HINT_RE = re.compile(r"最早|earliest\b|oldest\b", re.IGNORECASE)
ISSUE_HINT_RE = re.compile(r"issue|issues", re.IGNORECASE)
PROJECT_HINT_RE = re.compile(r"项目|仓库|repo|repository|project", re.IGNORECASE)
STAR_HINT_RE = re.compile(r"stars?\b|star 数|star数|星标", re.IGNORECASE)
NTH_HINT_RE = re.compile(r"第\s*(\d+)\s*个|\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)


def _has_structured_collection(snapshot: Dict[str, Any]) -> bool:
    for frame in snapshot.get("frames", []) or []:
        for collection in frame.get("collections", []) or []:
            container_locator = (collection.get("container_hint") or {}).get("locator")
            item_locator = (collection.get("item_hint") or {}).get("locator")
            if container_locator and item_locator:
                return True
    return False


def plan_compilable_intent(goal: str, snapshot: Dict[str, Any]) -> Optional[IntentPlan]:
    text = str(goal or "").strip()
    if not text:
        return None
    if FLOW_HINT_RE.search(text):
        return None

    is_read = bool(READ_HINT_RE.search(text))
    action = "read" if is_read else "click"
    if DOWNLOAD_HINT_RE.search(text):
        action = "download"
    elif CLICK_HINT_RE.search(text):
        action = "click"

    selection = IntentSelection(mode="direct")
    nth_match = NTH_HINT_RE.search(text)
    if FIRST_HINT_RE.search(text):
        selection.mode = "first"
    elif LAST_HINT_RE.search(text):
        selection.mode = "last"
    elif nth_match:
        selection.mode = "nth"
        selection.ordinal_index = int(nth_match.group(1) or nth_match.group(2) or 1)
    elif MAX_HINT_RE.search(text):
        selection.mode = "max"
    elif MIN_HINT_RE.search(text):
        selection.mode = "min"
    elif LATEST_HINT_RE.search(text):
        selection.mode = "latest"
    elif EARLIEST_HINT_RE.search(text):
        selection.mode = "earliest"

    target = IntentTarget(scope="page", role="link", semantic="")
    if ISSUE_HINT_RE.search(text):
        target.semantic = "issue"
    elif PROJECT_HINT_RE.search(text):
        target.semantic = "project"

    if STAR_HINT_RE.search(text):
        selection.metric = "stars"
        selection.value_source = "stargazers"

    result_key = ""
    if is_read and target.semantic == "issue":
        result_key = "selected_issue_title"

    if selection.mode == "direct" and not is_read:
        return None
    if not _has_structured_collection(snapshot) and selection.mode in {"first", "last", "nth", "max", "min"}:
        return None

    return IntentPlan(
        goal_type="read" if is_read else "action",
        action=action,  # type: ignore[arg-type]
        route="selection",
        target=target,
        selection=selection,
        state_change=not is_read,
        requires_reobserve=not is_read,
        source_hint="deterministic_selection",
        result_key=result_key,
    )
