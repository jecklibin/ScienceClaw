import json
import re
from typing import Any, Dict, Optional

from backend.rpa.intent_models import IntentPlan
from backend.rpa.segment_models import SegmentSpec


def _render_locator_expr(payload: Dict[str, Any], scope_expr: str) -> str:
    method = payload.get("method")
    if method == "css":
        return f'{scope_expr}.locator({json.dumps(payload.get("value", ""))})'
    if method == "role":
        role = json.dumps(payload.get("role", "link"))
        name = payload.get("name")
        if name:
            return f'{scope_expr}.get_by_role({role}, name={json.dumps(name)})'
        return f"{scope_expr}.get_by_role({role})"
    if method == "text":
        return f'{scope_expr}.get_by_text({json.dumps(payload.get("value", ""))})'
    if method == "placeholder":
        return f'{scope_expr}.get_by_placeholder({json.dumps(payload.get("value", ""))})'
    return f'{scope_expr}.locator({json.dumps(payload.get("value", ""))})'


def _iter_collections(snapshot: Dict[str, Any]):
    for frame in snapshot.get("frames", []) or []:
        for collection in frame.get("collections", []) or []:
            container_locator = (collection.get("container_hint") or {}).get("locator")
            item_locator = (collection.get("item_hint") or {}).get("locator")
            if container_locator and item_locator:
                yield frame, collection


def _tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(value or "").lower()) if len(token) >= 2}


def _collection_score(collection: Dict[str, Any], intent: IntentPlan) -> int:
    score = int(collection.get("item_count", 0) or 0)
    selector_text = " ".join(
        [
            str(((collection.get("container_hint") or {}).get("locator") or {}).get("value") or ""),
            str(((collection.get("item_hint") or {}).get("locator") or {}).get("value") or ""),
        ]
    ).lower()
    if intent.target.semantic == "issue" and "/issues/" in selector_text:
        score += 8
    if intent.target.semantic == "project" and any(token in selector_text for token in ["article", "repo", "h2 a", "box-row"]):
        score += 8
    sample_names = " ".join(str(item.get("name", "")) for item in (collection.get("items") or [])[:3]).lower()
    if intent.target.semantic == "issue" and "issue" in sample_names:
        score += 2
    if intent.target.semantic == "project" and "/" in sample_names:
        score += 2
    return score


def _best_collection(snapshot: Dict[str, Any], intent: IntentPlan) -> Optional[Dict[str, Any]]:
    candidates = [collection for _frame, collection in _iter_collections(snapshot)]
    if not candidates:
        return None
    candidates.sort(key=lambda collection: _collection_score(collection, intent), reverse=True)
    return candidates[0]


def _ordinal_index(intent: IntentPlan) -> str:
    mode = intent.selection.mode
    if mode == "first":
        return "0"
    if mode == "last":
        return "count - 1"
    if mode == "nth":
        index = max((intent.selection.ordinal_index or 1) - 1, 0)
        return str(index)
    return "0"


def _build_state_changing_ordinal_code(intent: IntentPlan, collection: Dict[str, Any]) -> str:
    container_locator = ((collection.get("container_hint") or {}).get("locator") or {})
    item_locator = ((collection.get("item_hint") or {}).get("locator") or {})
    container_expr = _render_locator_expr(container_locator, "page")
    item_expr = _render_locator_expr(item_locator, "container")
    target_name_key = "selected_issue_title" if intent.target.semantic == "issue" else "selected_repo_name"
    target_href_key = "selected_issue_href" if intent.target.semantic == "issue" else "selected_repo_href"

    return "\n".join(
        [
            "async def run(page):",
            "    from urllib.parse import urljoin",
            f"    containers = {container_expr}",
            "    count = await containers.count()",
            "    if count == 0:",
            "        raise Exception('No candidate containers found for deterministic selection')",
            f"    index = {_ordinal_index(intent)}",
            "    if index < 0:",
            "        index = 0",
            "    if index >= count:",
            "        index = count - 1",
            "    container = containers.nth(index)",
            f"    link = ({item_expr}).first",
            "    href = await link.get_attribute('href')",
            "    name = ' '.join((await link.inner_text()).split())",
            "    if not href:",
            "        raise Exception('Deterministic selection found no href on chosen link')",
            "    await page.goto(urljoin(page.url, href), wait_until='domcontentloaded')",
            "    return {",
            f"        {json.dumps(target_name_key)}: name,",
            f"        {json.dumps(target_href_key)}: href,",
            "        'page_changed': True,",
            "    }",
        ]
    )


def _build_read_only_ordinal_code(intent: IntentPlan, collection: Dict[str, Any]) -> str:
    container_locator = ((collection.get("container_hint") or {}).get("locator") or {})
    item_locator = ((collection.get("item_hint") or {}).get("locator") or {})
    container_expr = _render_locator_expr(container_locator, "page")
    item_expr = _render_locator_expr(item_locator, "container")
    result_key = intent.result_key or "selected_target_text"

    return "\n".join(
        [
            "async def run(page):",
            f"    containers = {container_expr}",
            "    count = await containers.count()",
            "    if count == 0:",
            "        raise Exception('No candidate containers found for deterministic read selection')",
            f"    index = {_ordinal_index(intent)}",
            "    if index < 0:",
            "        index = 0",
            "    if index >= count:",
            "        index = count - 1",
            "    container = containers.nth(index)",
            f"    link = ({item_expr}).first",
            "    text = ' '.join((await link.inner_text()).split())",
            "    href = await link.get_attribute('href')",
            "    if not text:",
            "        raise Exception('Deterministic read selection found empty text')",
            "    return {",
            "        'output': text,",
            f"        {json.dumps(result_key)}: text,",
            "        'selected_target_href': href or '',",
            "    }",
        ]
    )


def _build_max_stars_code(collection: Dict[str, Any]) -> str:
    container_locator = ((collection.get("container_hint") or {}).get("locator") or {})
    item_locator = ((collection.get("item_hint") or {}).get("locator") or {})
    container_expr = _render_locator_expr(container_locator, "page")
    item_expr = _render_locator_expr(item_locator, "container")

    return "\n".join(
        [
            "async def run(page):",
            "    import re",
            "    from urllib.parse import urljoin",
            f"    containers = {container_expr}",
            "    count = await containers.count()",
            "    if count == 0:",
            "        raise Exception('No candidate containers found for deterministic aggregate selection')",
            "    best = None",
            "    for index in range(count):",
            "        container = containers.nth(index)",
            f"        link = ({item_expr}).first",
            "        href = await link.get_attribute('href')",
            "        name = ' '.join((await link.inner_text()).split())",
            "        if not href:",
            "            continue",
            "        star_link = container.locator('a[href$=\"/stargazers\"]').first",
            "        if await star_link.count() == 0:",
            "            continue",
            "        star_text = await star_link.inner_text()",
            "        match = re.search(r'[\\d,]+', star_text or '')",
            "        if not match:",
            "            continue",
            "        stars = int(match.group(0).replace(',', ''))",
            "        if best is None or stars > best['stars']:",
            "            best = {'stars': stars, 'href': href, 'name': name}",
            "    if best is None:",
            "        raise Exception('Could not determine the max-valued item in the visible collection')",
            "    await page.goto(urljoin(page.url, best['href']), wait_until='domcontentloaded')",
            "    return {",
            "        'selected_repo_name': best['name'],",
            "        'selected_repo_href': best['href'],",
            "        'selected_repo_stars': best['stars'],",
            "        'page_changed': True,",
            "    }",
        ]
    )


def compile_selection_segment(intent: IntentPlan, goal: str, snapshot: Dict[str, Any]) -> Optional[SegmentSpec]:
    collection = _best_collection(snapshot, intent)
    if collection is None:
        return None

    if intent.goal_type == "read":
        code = _build_read_only_ordinal_code(intent, collection)
        return SegmentSpec(
            segment_goal=goal,
            segment_kind="read_only",
            stop_reason="goal_reached",
            expected_outcome={"type": "observation", "summary": goal},
            completion_check={},
            code=code,
            notes="compiled:selection",
        )

    if intent.selection.mode in {"first", "last", "nth"}:
        code = _build_state_changing_ordinal_code(intent, collection)
    elif intent.selection.mode == "max" and intent.selection.metric == "stars":
        code = _build_max_stars_code(collection)
    else:
        return None

    return SegmentSpec(
        segment_goal=goal,
        segment_kind="state_changing",
        stop_reason="after_state_change",
        expected_outcome={"type": "page_state_changed", "summary": goal},
        completion_check={"url_not_same": True},
        code=code,
        notes="compiled:selection",
    )
