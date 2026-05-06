from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .recording_ordinal_preplans import (
    _instruction_entity_tokens,
    _instruction_region_anchors,
    _normalize_instruction_text,
)


def build_search_preplanned_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    _ = snapshot
    token = _first_entity_token(instruction)
    if not token:
        return None
    if _is_empty_result_search_instruction(instruction):
        return _build_search_empty_result_plan(instruction, token)
    if _is_search_then_open_instruction(instruction):
        return _build_search_then_open_plan(instruction, token)
    return None


def _first_entity_token(instruction: str) -> str:
    tokens = _instruction_entity_tokens(instruction)
    if tokens:
        return tokens[0]
    return _first_filter_value_token(instruction)


def _first_filter_value_token(instruction: str) -> str:
    text = _normalize_search_text(instruction)
    patterns = (
        r"按\s*([A-Za-z0-9_-]{2,})\s*(?:状态|status|筛选|过滤)",
        r"(?:status|state)\s*[:=]?\s*([A-Za-z0-9_-]{2,})",
        r"(?:filter|search|query|lookup)\s+(?:for\s+)?(?:[A-Za-z]+\s+){0,3}?([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)",
    )
    ignored = {
        "search",
        "filter",
        "query",
        "lookup",
        "status",
        "state",
        "empty",
        "result",
        "results",
        "record",
        "records",
    }
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        token = match.group(1).strip()
        if token and token.lower() not in ignored:
            return token
    return ""


def _is_empty_result_search_instruction(instruction: str) -> bool:
    text = _normalize_search_text(instruction)
    if not _contains_search_intent(text):
        return False
    empty_markers = (
        "empty",
        "no match",
        "no matching",
        "not found",
        "no result",
        "zero result",
        "不存在",
        "为空",
        "没有匹配",
        "未找到",
        "无匹配",
        "无结果",
        "没有结果",
        "暂无",
    )
    return any(marker in text for marker in empty_markers)


def _is_search_then_open_instruction(instruction: str) -> bool:
    text = _normalize_search_text(instruction)
    return _contains_search_intent(text) and _contains_positive_open_intent(text)


def _normalize_search_text(instruction: str) -> str:
    text = _normalize_instruction_text(instruction)
    try:
        repaired = text.encode("gbk").decode("utf-8")
    except UnicodeError:
        repaired = ""
    if repaired and repaired != text:
        return f"{text}\n{_normalize_instruction_text(repaired)}"
    return text


def _contains_search_intent(text: str) -> bool:
    return any(marker in text for marker in ("search", "filter", "query", "lookup", "搜索", "筛选", "查询", "查找", "过滤"))


def _contains_positive_open_intent(text: str) -> bool:
    return _contains_positive_term(text, ("open", "click", "visit", "go to", "打开", "点击", "进入"))


def _contains_positive_term(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            if not _term_is_negated(text, index):
                return True
            start = index + len(term)
    return False


def _term_is_negated(text: str, index: int) -> bool:
    prefix = text[max(0, index - 14) : index]
    compact = "".join(prefix.split())
    return any(
        compact.endswith(marker)
        for marker in (
            "不要",
            "不能",
            "不得",
            "请勿",
            "勿",
            "别",
            "不要误",
            "do not",
            "don't",
            "dont",
            "should not",
            "must not",
            "not",
        )
    )


def _build_search_empty_result_plan(instruction: str, token: str) -> Dict[str, Any]:
    code = _search_helpers_code(instruction, token) + (
        "    _empty_text = await _visible_empty_text(_scope)\n"
        "    _matched_rows = await _count_visible_rows_matching(_scope, _query)\n"
        "    _visible_rows = await _count_visible_data_rows(_scope)\n"
        "    _empty_confirmed = bool(_empty_text) or (_matched_rows == 0 and _visible_rows == 0)\n"
        "    if not _empty_confirmed:\n"
        "        raise RuntimeError(f'Empty result was not confirmed for {_query!r}; matched_rows={_matched_rows}, visible_rows={_visible_rows}')\n"
        "    return {\n"
        "        'search_value': _query,\n"
        "        'matched_rows': _matched_rows,\n"
        "        'visible_rows': _visible_rows,\n"
        "        'empty_result_confirmed': True,\n"
        "        'empty_state_text': _empty_text,\n"
        "    }\n"
    )
    return {
        "description": "Search visible collection and confirm empty result",
        "action_type": "run_python",
        "expected_effect": "state_change",
        "output_key": "search_empty_result",
        "code": code,
        "input_bindings": {
            "query": {"source": "user_param", "default": token, "classification": "user_param"}
        },
        "terminal_contract": {
            "required": True,
            "kind": "empty_result",
            "success_evidence": [{"type": "empty_result"}],
            "allow_semantic_judge": False,
        },
        "search_empty_result": True,
    }


def _build_search_then_open_plan(instruction: str, token: str) -> Dict[str, Any]:
    code = _search_helpers_code(instruction, token) + (
        "    _row = await _find_row_containing(_scope, _query)\n"
        "    _before_url = page.url\n"
        "    _actions = _row.locator('a, button, [role=link], [role=button]').filter(has_not_text=_query)\n"
        "    _clicked = False\n"
        "    for _index in range(min(await _actions.count(), 8)):\n"
        "        _action = _actions.nth(_index)\n"
        "        try:\n"
        "            if await _action.is_visible() and await _action.is_enabled():\n"
        "                await _action.click(timeout=5000)\n"
        "                _clicked = True\n"
        "                break\n"
        "        except Exception:\n"
        "            continue\n"
        "    if not _clicked:\n"
        "        await _row.click(timeout=5000)\n"
        "    try:\n"
        "        await page.wait_for_load_state('domcontentloaded', timeout=5000)\n"
        "    except Exception:\n"
        "        pass\n"
        "    await page.wait_for_timeout(500)\n"
        "    return {'action_performed': True, 'action_type': 'search_then_open', 'target_text': _query, 'from_url': _before_url, 'url': page.url}\n"
    )
    return {
        "description": "Search visible collection and open matching row",
        "action_type": "run_python",
        "expected_effect": "state_change",
        "output_key": "search_opened_row",
        "code": code,
        "input_bindings": {
            "query": {"source": "user_param", "default": token, "classification": "user_param"}
        },
        "terminal_contract": {
            "required": True,
            "kind": "state_change",
            "success_evidence": [{"type": "feedback_visible"}, {"type": "url_changed"}],
            "allow_semantic_judge": True,
        },
        "search_then_open": True,
    }


def _search_helpers_code(instruction: str, token: str) -> str:
    anchors = _instruction_region_anchors(instruction)
    return (
        "async def run(page, results):\n"
        "    import re as _re\n"
        f"    _query = {token!r}\n"
        f"    _region_anchors = {anchors!r}\n"
        "    async def _first_visible(locator, limit=20):\n"
        "        count = min(await locator.count(), limit)\n"
        "        for index in range(count):\n"
        "            item = locator.nth(index)\n"
        "            try:\n"
        "                if await item.is_visible():\n"
        "                    return item\n"
        "            except Exception:\n"
        "                continue\n"
        "        return None\n"
        "    async def _region_scope():\n"
        "        for anchor in _region_anchors:\n"
        "            heading = page.get_by_text(anchor, exact=True).first\n"
        "            try:\n"
        "                if await heading.count():\n"
        "                    scope = heading.locator(\"xpath=ancestor-or-self::*[self::section or self::article or self::div][1]\")\n"
        "                    if await scope.count():\n"
        "                        return scope\n"
        "            except Exception:\n"
        "                continue\n"
        "        return page.locator('body')\n"
        "    async def _fill_search(scope):\n"
        "        candidates = [\n"
        "            scope.get_by_role('searchbox'),\n"
        "            scope.locator(\"input[type='search']\"),\n"
        "            scope.locator(\"input[placeholder*='search' i], input[aria-label*='search' i], input[placeholder*='filter' i], input[aria-label*='filter' i]\"),\n"
        "            scope.locator(\"input[placeholder*='搜索'], input[aria-label*='搜索'], input[placeholder*='查询'], input[aria-label*='查询'], input[placeholder*='筛选'], input[aria-label*='筛选']\"),\n"
        "            scope.locator(\"input:not([type='hidden']):not([type='password']):not([type='checkbox']):not([type='radio']), textarea\"),\n"
        "        ]\n"
        "        for locator in candidates:\n"
        "            control = await _first_visible(locator)\n"
        "            if control is None:\n"
        "                continue\n"
        "            try:\n"
        "                if hasattr(control, 'is_enabled') and not await control.is_enabled():\n"
        "                    continue\n"
        "                await control.fill(_query)\n"
        "                return control\n"
        "            except Exception:\n"
        "                continue\n"
        "        action = await _find_filter_action(scope)\n"
        "        if action is not None:\n"
        "            await action.click(timeout=5000)\n"
        "            return None\n"
        "        raise RuntimeError('No visible search input or query-specific filter action found')\n"
        "    async def _find_filter_action(scope):\n"
        "        controls = scope.locator('button, a, [role=button], [role=link]')\n"
        "        query_lower = _query.lower()\n"
        "        count = min(await controls.count(), 80)\n"
        "        for index in range(count):\n"
        "            control = controls.nth(index)\n"
        "            try:\n"
        "                if not await control.is_visible():\n"
        "                    continue\n"
        "                if hasattr(control, 'is_enabled') and not await control.is_enabled():\n"
        "                    continue\n"
        "                label = await control.evaluate(\"\"\"el => [\n"
        "                    el.innerText,\n"
        "                    el.textContent,\n"
        "                    el.getAttribute('aria-label'),\n"
        "                    el.getAttribute('title'),\n"
        "                    el.getAttribute('data-testid'),\n"
        "                    el.getAttribute('data-test'),\n"
        "                    el.getAttribute('value')\n"
        "                ].filter(Boolean).join(' ')\"\"\")\n"
        "                label_lower = str(label or '').lower()\n"
        "                if query_lower and query_lower in label_lower:\n"
        "                    return control\n"
        "            except Exception:\n"
        "                continue\n"
        "        return None\n"
        "    async def _submit_search(scope, control):\n"
        "        if control is None:\n"
        "            return\n"
        "        buttons = scope.get_by_role('button', name=_re.compile(r'(search|filter|query|lookup|搜索|筛选|查询|查找|过滤)', _re.I))\n"
        "        button = await _first_visible(buttons)\n"
        "        if button is not None:\n"
        "            try:\n"
        "                if await button.is_enabled():\n"
        "                    await button.click(timeout=5000)\n"
        "                    return\n"
        "            except Exception:\n"
        "                pass\n"
        "        try:\n"
        "            await control.press('Enter')\n"
        "        except Exception:\n"
        "            pass\n"
        "    async def _visible_empty_text(scope):\n"
        "        pattern = _re.compile(r'(no\\s+(matching\\s+)?(result|results|record|records|data)|no\\s+[^\\n]{0,60}\\s+found|not\\s+found|没有匹配|未找到|暂无|无数据|为空)', _re.I)\n"
        "        locator = scope.get_by_text(pattern)\n"
        "        node = await _first_visible(locator, limit=30)\n"
        "        if node is None:\n"
        "            return ''\n"
        "        try:\n"
        "            return (await node.inner_text()).strip()\n"
        "        except Exception:\n"
        "            return ''\n"
        "    async def _count_visible_data_rows(scope):\n"
        "        rows = scope.locator('tbody tr, [role=row], li[data-row-key], [data-row-key]')\n"
        "        total = 0\n"
        "        for index in range(min(await rows.count(), 200)):\n"
        "            row = rows.nth(index)\n"
        "            try:\n"
        "                if await row.is_visible() and (await row.inner_text()).strip():\n"
        "                    total += 1\n"
        "            except Exception:\n"
        "                continue\n"
        "        return total\n"
        "    async def _count_visible_rows_matching(scope, text):\n"
        "        rows = scope.locator('tbody tr, [role=row], li[data-row-key], [data-row-key]').filter(has_text=text)\n"
        "        total = 0\n"
        "        for index in range(min(await rows.count(), 50)):\n"
        "            row = rows.nth(index)\n"
        "            try:\n"
        "                if await row.is_visible():\n"
        "                    total += 1\n"
        "            except Exception:\n"
        "                continue\n"
        "        return total\n"
        "    async def _find_row_containing(scope, text):\n"
        "        for selector in ('tbody tr', '[role=row]', 'li[data-row-key]', '[data-row-key]'):\n"
        "            rows = scope.locator(selector).filter(has_text=text)\n"
        "            row = await _first_visible(rows, limit=50)\n"
        "            if row is not None:\n"
        "                return row\n"
        "        raise RuntimeError(f'No visible row found for {text!r}')\n"
        "    _scope = await _region_scope()\n"
        "    _control = await _fill_search(_scope)\n"
        "    await _submit_search(_scope, _control)\n"
        "    await page.wait_for_timeout(700)\n"
    )
