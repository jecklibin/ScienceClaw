from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


SEMANTIC_SELECTION_TERMS = (
    "most related",
    "best match",
    "highest",
    "lowest",
    "most relevant",
    "compare",
    "summarize",
    "summary",
    "最相关",
    "最高",
    "最低",
    "最大",
    "最小",
    "最佳",
    "比较",
    "总结",
)


def build_preplanned_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build deterministic replay plans from ordinal structure in the snapshot.

    Ordinal instructions such as "click the first item" are structural: replay
    must target the current first item, not the text observed while recording.
    """

    for builder in (
        _build_table_entity_action_plan,
        _build_visible_entity_action_plan,
        _build_table_ordinal_overlay_plan,
        _build_ordinal_overlay_plan,
    ):
        plan = builder(instruction, snapshot)
        if plan:
            plan = dict(plan)
            plan.setdefault("preplanned_source", "ordinal_snapshot")
            return plan
    return None


def _build_table_entity_action_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action = _detect_ordinal_action(instruction)
    if action != "click_primary":
        return None
    target_tokens = _instruction_entity_tokens(instruction)
    if len(target_tokens) != 1:
        return None
    table = _select_table_view_for_entity(snapshot, instruction, target_tokens)
    if not table:
        return None
    row_index = _table_row_index_matching_tokens(table, target_tokens)
    if row_index is None:
        return None
    action_selector = _table_row_action_selector(table, row_index)
    if not action_selector:
        return None
    token = target_tokens[0]
    rows_setup = _table_rows_setup_code(table)
    code = (
        "async def run(page, results):\n"
        f"{rows_setup}"
        f"    _target_text = {token!r}\n"
        "    _row = _rows.filter(has_text=_target_text).first\n"
        "    await _row.wait_for(state='visible', timeout=10000)\n"
        "    _before_url = page.url\n"
        f"    await _row.locator({action_selector!r}).click()\n"
        "    try:\n"
        "        await page.wait_for_load_state('domcontentloaded', timeout=5000)\n"
        "    except Exception:\n"
        "        pass\n"
        "    return {'action_performed': True, 'action_type': 'click', 'target_text': _target_text, 'from_url': _before_url, 'url': page.url}\n"
    )
    return {
        "description": "Click table row action by visible row identifier",
        "action_type": "run_python",
        "expected_effect": "click",
        "output_key": "table_row_opened",
        "code": code,
        "input_bindings": {
            "target_text": {
                "source": "user_param",
                "default": token,
                "classification": "user_param",
            }
        },
        "table_entity_action": True,
    }


def _build_visible_entity_action_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action = _detect_ordinal_action(instruction)
    if action != "click_primary":
        return None
    target_tokens = _instruction_entity_tokens(instruction)
    if len(target_tokens) != 1:
        return None
    token = next((item for item in target_tokens if _snapshot_contains_text(snapshot, item)), "")
    if not token:
        return None

    code = (
        "async def run(page, results):\n"
        f"    _target_text = {token!r}\n"
        "    _row = None\n"
        "    _row_scopes = ['tbody tr', '[role=row]', 'li', '[data-row-key]', '[data-testid]']\n"
        "    for _selector in _row_scopes:\n"
        "        _candidate = page.locator(_selector).filter(has_text=_target_text).first\n"
        "        try:\n"
        "            if await _candidate.count():\n"
        "                await _candidate.wait_for(state='visible', timeout=3000)\n"
        "                _row = _candidate\n"
        "                break\n"
        "        except Exception:\n"
        "            continue\n"
        "    if _row is None:\n"
        "        _text = page.get_by_text(_target_text, exact=True).first\n"
        "        if not await _text.count():\n"
        "            _text = page.get_by_text(_target_text, exact=False).first\n"
        "        await _text.wait_for(state='visible', timeout=10000)\n"
        "        _row = _text.locator(\"xpath=ancestor::*[self::tr or @role='row' or self::li or @data-row-key][1]\")\n"
        "        if not await _row.count():\n"
        "            _row = _text.locator(\"xpath=ancestor::*[.//a or .//button or .//*[@role='button' or @role='link']][1]\")\n"
        "    _before_url = page.url\n"
        "    _actions = _row.locator('a, button, [role=link], [role=button]').filter(has_not_text=_target_text)\n"
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
        "    return {'action_performed': True, 'action_type': 'click', 'target_text': _target_text, 'from_url': _before_url, 'url': page.url}\n"
    )
    return {
        "description": "Click visible entity row action",
        "action_type": "run_python",
        "expected_effect": "click",
        "output_key": "entity_row_opened",
        "code": code,
        "input_bindings": {
            "target_text": {
                "source": "user_param",
                "default": token,
                "classification": "user_param",
            }
        },
        "visible_entity_action": True,
    }


def _build_table_ordinal_overlay_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    intent = _detect_ordinal_intent(instruction)
    if not intent:
        return None
    action = _detect_ordinal_action(instruction)
    if action not in {"click_primary", "extract_title"}:
        return None

    table = _select_table_view(snapshot, instruction)
    if not table:
        return None
    rows = list(table.get("rows") or [])
    if not rows:
        return None
    if str(intent.get("kind") or "") == "first_n":
        if action != "extract_title":
            return None
        limit = int(intent.get("limit") or 0)
        if limit <= 0:
            return None
        return _table_first_n_rows_plan(table, limit)
    index = _ordinal_index_from_intent(intent, len(rows))
    if index is None:
        return None
    column = _select_table_column(table, instruction)
    if not column:
        return None

    rows_setup = _table_rows_setup_code(table)
    column_id = str(column.get("column_id") or "")
    if column_id:
        cell_selector = f"td[data-colid={column_id!r}]"
    else:
        col_index = int(column.get("index") or 0) + 1
        cell_selector = f"td:nth-child({col_index})"

    if action == "click_primary":
        action_selector = _table_click_action_selector(table, index, column)
        if not action_selector:
            return None
        code = (
            "async def run(page, results):\n"
            f"{rows_setup}"
            f"    _row = _rows.nth({index})\n"
            "    _label = (await _row.inner_text()).strip()\n"
            "    _before_url = page.url\n"
            f"    await _row.locator({action_selector!r}).click()\n"
            "    try:\n"
            "        await page.wait_for_load_state('domcontentloaded', timeout=5000)\n"
            "    except Exception:\n"
            "        pass\n"
            "    await page.wait_for_timeout(500)\n"
            "    return {'action_performed': True, 'action_type': 'click', "
            f"'ordinal_index': {index}, 'clicked_label': _label, 'from_url': _before_url, 'url': page.url}}"
        )
        return {
            "description": "Click table row column action",
            "action_type": "run_python",
            "expected_effect": "click",
            "output_key": "table_row_action",
            "code": code,
            "table_ordinal_overlay": True,
            "terminal_contract": _click_terminal_contract(),
        }

    code = (
        "async def run(page, results):\n"
        f"{rows_setup}"
        f"    _row = _rows.nth({index})\n"
        f"    return (await _row.locator({cell_selector!r}).inner_text()).strip()"
    )
    return {
        "description": "Extract table row column value",
        "action_type": "run_python",
        "expected_effect": "extract",
        "output_key": "table_row_value",
        "code": code,
        "table_ordinal_overlay": True,
    }


def _build_ordinal_overlay_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    intent = _detect_ordinal_intent(instruction)
    if not intent:
        return None

    action = _detect_ordinal_action(instruction)
    if not action:
        return None

    # If the user names a concrete region, a page-global repeated-item fallback
    # is not safe. Let table/region evidence or the planner handle it instead of
    # clicking the first matching button/link in the whole page.
    if _instruction_region_anchors(instruction):
        return None

    collection_snapshot = dict(snapshot)
    collection_snapshot["_instruction"] = instruction
    collection = _extract_repeated_candidate_collection(collection_snapshot)
    if not collection:
        return None

    items = list(collection.get("items") or [])
    selector = str(collection.get("primary_selector") or "")
    if not selector or not items:
        return None
    if _is_page_chrome_collection(selector, instruction):
        return None

    kind = intent["kind"]
    index = int(intent.get("index") or 0)
    if kind == "last":
        index = len(items) - 1
    if kind in {"nth", "last"} and (index < 0 or index >= len(items)):
        return None

    if kind == "first_n":
        limit = int(intent.get("limit") or 0)
        if limit <= 0:
            return None
        return _ordinal_first_n_titles_plan(selector, limit)

    if action == "extract_title":
        return _ordinal_extract_title_plan(selector, index)

    if action == "click_secondary":
        secondary_selector = _select_secondary_action_selector(collection, instruction)
        if not secondary_selector:
            return None
        return _ordinal_click_plan(
            secondary_selector,
            index,
            description="Click ordinal item action",
            expected_effect="none",
        )

    if action == "click_primary":
        return _ordinal_click_plan(selector, index, description="Click ordinal item")

    return None


def _detect_ordinal_intent(instruction: str) -> Optional[Dict[str, int | str]]:
    text = _normalize_instruction_text(instruction)
    if not text:
        return None
    real_chinese_intent = _detect_real_chinese_ordinal_intent(text)
    if real_chinese_intent:
        return real_chinese_intent

    first_n = re.search(r"\bfirst\s+(\d+)\b", text) or re.search(
        r"前\s*([0-9一二三四五六七八九十两]+)", text
    )
    if first_n:
        limit = _parse_ordinal_number(first_n.group(1))
        if limit is not None:
            return {"kind": "first_n", "limit": limit}

    nth = re.search(r"\b(?:number|item|row)\s+(\d+)\b", text) or re.search(
        r"第\s*([0-9一二三四五六七八九十两]+)\s*(?:个|项|条|行)?", text
    )
    if nth:
        number = _parse_ordinal_number(nth.group(1))
        if number is not None:
            return {"kind": "nth", "index": max(number - 1, 0)}

    if any(token in text for token in ("第一个", "第一项", "第一条", "第一行", "first")):
        return {"kind": "nth", "index": 0}
    if any(token in text for token in ("第二个", "第二项", "第二条", "第二行", "second")):
        return {"kind": "nth", "index": 1}
    if any(token in text for token in ("最后一个", "最后一项", "最后一条", "最后一行", "last")):
        return {"kind": "last", "index": -1}
    return None


def _detect_real_chinese_ordinal_intent(text: str) -> Optional[Dict[str, int | str]]:
    first_n = re.search(r"前\s*([0-9一二两三四五六七八九十]+)\s*(?:个|项|条|行)?", text)
    if first_n:
        limit = _parse_real_chinese_ordinal_number(first_n.group(1))
        if limit is not None:
            return {"kind": "first_n", "limit": limit}

    nth = re.search(r"第\s*([0-9一二两三四五六七八九十]+)\s*(?:个|项|条|行)", text)
    if nth:
        number = _parse_real_chinese_ordinal_number(nth.group(1))
        if number is not None:
            return {"kind": "nth", "index": max(number - 1, 0)}

    if any(token in text for token in ("第一个", "第一项", "第一条", "第一行")):
        return {"kind": "nth", "index": 0}
    if any(token in text for token in ("第二个", "第二项", "第二条", "第二行")):
        return {"kind": "nth", "index": 1}
    if any(token in text for token in ("最后一个", "最后一项", "最后一条", "最后一行")):
        return {"kind": "last", "index": -1}
    return None


def _parse_real_chinese_ordinal_number(text: str) -> Optional[int]:
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2 and text[1] in digits:
        return 10 + digits[text[1]]
    if text.endswith("十") and len(text) == 2 and text[0] in digits:
        return digits[text[0]] * 10
    if "十" in text and len(text) == 3 and text[0] in digits and text[2] in digits:
        return digits[text[0]] * 10 + digits[text[2]]
    if text.isdigit():
        return int(text)
    return None


def _parse_ordinal_number(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    real_chinese_number = _parse_real_chinese_ordinal_number(text)
    if real_chinese_number is not None:
        return real_chinese_number
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2 and text[1] in digits:
        return 10 + digits[text[1]]
    if text.endswith("十") and len(text) == 2 and text[0] in digits:
        return digits[text[0]] * 10
    if "十" in text and len(text) == 3 and text[0] in digits and text[2] in digits:
        return digits[text[0]] * 10 + digits[text[2]]
    return None


def _detect_ordinal_action(instruction: str) -> str:
    text = _normalize_instruction_text(instruction)
    if any(term in text for term in SEMANTIC_SELECTION_TERMS):
        return ""
    if _contains_positive_action_term(text, ("下载",)):
        return "click_secondary"
    if _contains_positive_action_term(text, ("点击", "打开", "进入")):
        return "click_primary"
    if any(term in text for term in ("名称", "名字", "标题", "获取", "读取", "提取")):
        return "extract_title"
    if _contains_positive_action_term(text, ("download", "下载")):
        return "click_secondary"
    if _contains_positive_action_term(text, ("click", "open", "visit", "go to", "点击", "打开", "进入")):
        return "click_primary"
    if any(term in text for term in ("name", "title", "text", "名称", "名字", "标题", "获取", "读取", "提取")):
        return "extract_title"
    return ""


def _contains_positive_action_term(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            if not _action_term_is_negated(text, index):
                return True
            start = index + len(term)
    return False


def _action_term_is_negated(text: str, index: int) -> bool:
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
            "do not",
            "don't",
            "dont",
            "should not",
            "must not",
            "not",
        )
    )


def _normalize_instruction_text(instruction: str) -> str:
    text = str(instruction or "").strip().lower()
    if not text:
        return ""
    repaired_candidates = [text]
    try:
        repaired = text.encode("gb18030").decode("utf-8")
        if repaired and repaired != text:
            repaired_candidates.append(repaired.lower())
    except UnicodeError:
        pass
    real_hints = {
        "项目": "project",
        "文件": "file",
        "名称": "name",
        "名字": "name",
        "状态": "status",
        "操作": "action",
        "按钮": "button",
        "勾选": "checkbox select",
        "选择": "select",
        "列表": "list",
        "表格": "table",
        "行": "row",
    }
    for source, hint in real_hints.items():
        if source in text:
            repaired_candidates.append(hint)
    bilingual_hints = {
        "项目": "project",
        "文件": "file",
        "名称": "name",
        "名字": "name",
        "状态": "status",
        "操作": "action",
        "按钮": "button",
        "勾选": "checkbox select",
        "选择": "select",
        "列表": "list",
        "表格": "table",
        "行": "row",
    }
    for source, hint in bilingual_hints.items():
        if source in text:
            repaired_candidates.append(hint)
    return " ".join(repaired_candidates)


def _ordinal_index_from_intent(intent: Dict[str, int | str], row_count: int) -> Optional[int]:
    kind = str(intent.get("kind") or "")
    if kind == "last":
        return row_count - 1 if row_count else None
    if kind == "first_n":
        return None
    index = int(intent.get("index") or 0)
    return index if 0 <= index < row_count else None


def _select_table_view(snapshot: Dict[str, Any], instruction: str) -> Optional[Dict[str, Any]]:
    tables = [table for table in list(snapshot.get("table_views") or []) if table.get("rows")]
    if not tables:
        return None
    anchored_tables = _tables_matching_explicit_region(tables, instruction)
    if anchored_tables:
        tables = anchored_tables
    elif _instruction_region_anchors(instruction):
        return None
    scored = [(_score_table_view_for_instruction(table, instruction), table) for table in tables]
    score, table = max(scored, key=lambda item: item[0])
    # A table ordinal plan is only safe when the instruction anchors a concrete
    # table/region/header. Otherwise a page-level "first item" instruction should
    # fall through to repeated-item collection evidence instead of the first table
    # that happens to appear in DOM order.
    if score <= len(table.get("rows") or []):
        return None
    return table


def _select_table_view_for_entity(
    snapshot: Dict[str, Any],
    instruction: str,
    target_tokens: List[str],
) -> Optional[Dict[str, Any]]:
    tables = [table for table in list(snapshot.get("table_views") or []) if table.get("rows")]
    if not tables:
        return None
    anchored_tables = _tables_matching_explicit_region(tables, instruction)
    if anchored_tables:
        tables = anchored_tables
    elif _instruction_region_anchors(instruction):
        return None
    scored: List[tuple[int, Dict[str, Any]]] = []
    for table in tables:
        row_index = _table_row_index_matching_tokens(table, target_tokens)
        if row_index is None:
            continue
        action_selector = _table_row_action_selector(table, row_index)
        if not action_selector:
            continue
        scored.append((_score_table_view_for_instruction(table, instruction), table))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _tables_matching_explicit_region(tables: List[Dict[str, Any]], instruction: str) -> List[Dict[str, Any]]:
    anchors = _instruction_region_anchors(instruction)
    if not anchors:
        return []
    return [
        table
        for table in tables
        if _table_matches_any_anchor(table, anchors)
    ]


def _table_matches_any_anchor(table: Dict[str, Any], anchors: List[str]) -> bool:
    candidates = [str(table.get("title") or "")]
    candidates.extend(str(item or "") for item in table.get("nearby_headings") or [])
    candidates.extend(str(table.get("section_title") or ""))
    return any(_text_matches_any_anchor(candidate, anchors) for candidate in candidates)


def _instruction_region_anchors(instruction: str) -> List[str]:
    text = _normalize_instruction_text(instruction)
    anchors: List[str] = []
    for match in re.finditer(r"在\s*(.{2,80}?)\s*(?:区域|区|部分|面板)", text, flags=re.IGNORECASE):
        anchor = re.sub(r"^[：:，,。、“”\"'\s]+|[：:，,。、“”\"'\s]+$", "", match.group(1)).strip()
        if anchor:
            anchors.append(anchor)
    patterns = [
        r"(?:in|inside|within)\s+(.{2,80}?)\s+(?:area|section|region)\b",
        r"在\s*(.{2,80}?)\s*(?:区域|区|部分)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            anchor = re.sub(r"^[，,。.:：\\s]+|[，,。.:：\\s]+$", "", match.group(1)).strip()
            if anchor:
                anchors.append(anchor)
    return anchors


def _text_matches_any_anchor(value: str, anchors: List[str]) -> bool:
    normalized = _normalize_instruction_text(value)
    if not normalized:
        return False
    for anchor in anchors:
        if anchor in normalized or normalized in anchor:
            return True
    return False


def _score_table_view_for_instruction(table: Dict[str, Any], instruction: str) -> int:
    text = _normalize_instruction_text(instruction)
    score = len(table.get("rows") or [])
    title_parts = [str(table.get("title") or "")]
    title_parts.extend(str(item or "") for item in table.get("nearby_headings") or [])
    for title in title_parts:
        normalized = title.strip().lower()
        if not normalized:
            continue
        if normalized in text:
            score += 100
        elif all(token in text for token in normalized.split()):
            score += 40
    for column in table.get("columns") or []:
        header = str(column.get("header") or "").strip().lower()
        if header and header in text:
            score += 20
            continue
        overlap = _semantic_token_overlap(header, text)
        if overlap:
            score += 8 * overlap
    for header in _table_view_headers(table):
        normalized_header = header.lower()
        if normalized_header and normalized_header in text:
            score += 16
            continue
        overlap = _semantic_token_overlap(normalized_header, text)
        if overlap:
            score += 6 * overlap
    return score


def _select_table_column(table: Dict[str, Any], instruction: str) -> Optional[Dict[str, Any]]:
    text = _normalize_instruction_text(instruction)
    columns = list(table.get("columns") or [])
    scored: List[tuple[int, Dict[str, Any]]] = []
    for column in columns:
        header = str(column.get("header") or "").lower()
        role = str(column.get("role") or "").lower()
        score = 0
        if header and header in text:
            score += 6
        score += 3 * _semantic_token_overlap(header, text)
        if role and role in text:
            score += 3
        if role == "action" and any(term in text for term in ("action", "operation", "操作", "按钮")):
            score += 5
        if role == "file_link" and any(term in text for term in ("file", "文件", "名称", "名字")):
            score += 5
        if role == "status" and any(term in text for term in ("status", "状态")):
            score += 5
        if role == "selection" and any(term in text for term in ("checkbox", "勾选", "选择")):
            score += 5
        if score:
            scored.append((score, column))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _semantic_token_overlap(value: str, normalized_instruction: str) -> int:
    tokens = {
        token
        for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", str(value or "").lower())
        if len(token) >= 3 and token not in {"the", "and", "row", "col", "id"}
    }
    if not tokens:
        return 0
    return sum(1 for token in tokens if token in normalized_instruction)


def _table_row_selector(table: Dict[str, Any]) -> str:
    for row in table.get("rows") or []:
        for hint in row.get("locator_hints") or []:
            expression = str(hint.get("expression") or "")
            match = re.search(r"page\.locator\((['\"])(.*?)\1\)\.nth\(\d+\)", expression)
            if match:
                return match.group(2)
    return "tbody tr"


def _table_rows_setup_code(table: Dict[str, Any]) -> str:
    title = str(table.get("title") or "").strip()
    fallback_rows = _table_rows_by_headers_setup_code(table)
    if title:
        headers = _table_view_headers(table)
        header_literals = repr(headers)
        first_row_markers = repr(_table_first_row_markers(table))
        return (
            "    _rows = None\n"
            "    for _attempt in range(40):\n"
            f"        _title = {title!r}\n"
            "        _named_table_candidates = []\n"
            "        try:\n"
            "            _named_table_candidates.append(page.get_by_role('table', name=_title))\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            "            _named_table_candidates.append(page.locator('table').filter(has_text=_title))\n"
            "        except Exception:\n"
            "            pass\n"
            "        for _named_tables in _named_table_candidates:\n"
            "            try:\n"
            "                _named_limit = min(await _named_tables.count(), 5)\n"
            "                for _named_index in range(_named_limit):\n"
            "                    _candidate_rows = _named_tables.nth(_named_index).locator('tbody tr, [role=row]')\n"
            "                    if await _candidate_rows.count():\n"
            "                        _rows = _candidate_rows\n"
            "                        break\n"
            "                if _rows is not None:\n"
            "                    break\n"
            "            except Exception:\n"
            "                continue\n"
            "        if _rows is not None:\n"
            "            break\n"
            f"        _heading = page.get_by_text({title!r}, exact=True).first\n"
            "        if not await _heading.count():\n"
            f"            _heading = page.get_by_text({title!r}, exact=False).first\n"
            "        if await _heading.count():\n"
            f"            _headers = {header_literals}\n"
            f"            _first_row_markers = {first_row_markers}\n"
            "            _tables_after_heading = _heading.locator(\"xpath=following::table[.//tbody/tr]\")\n"
            "            _table_limit = min(await _tables_after_heading.count(), 8)\n"
            "            for _table_index in range(_table_limit):\n"
            "                _candidate_table = _tables_after_heading.nth(_table_index)\n"
            "                _header_text = ''\n"
            "                _first_row_text = ''\n"
            "                try:\n"
            "                    _header_text = (await _candidate_table.locator('thead, tr').first.inner_text()).lower()\n"
            "                except Exception:\n"
            "                    pass\n"
            "                try:\n"
            "                    _first_row_text = (await _candidate_table.locator('tbody tr').first.inner_text()).lower()\n"
            "                except Exception:\n"
            "                    pass\n"
            "                _headers_match = bool(_headers) and all(str(_header).lower() in _header_text for _header in _headers)\n"
            "                _row_marker_hits = sum(1 for _marker in _first_row_markers if str(_marker).lower() in _first_row_text)\n"
            "                _markers_match = bool(_first_row_markers) and _row_marker_hits >= min(2, len(_first_row_markers))\n"
            "                if not (_headers_match or _markers_match):\n"
            "                    continue\n"
            "                _candidate_rows = _candidate_table.locator('tbody tr')\n"
            "                if await _candidate_rows.count():\n"
            "                    _rows = _candidate_rows\n"
            "                    break\n"
            "            if _rows is not None:\n"
            "                break\n"
            "        await page.wait_for_timeout(250)\n"
            "    if _rows is None:\n"
            f"{_indent_code(fallback_rows, 8)}"
        )
    return fallback_rows


def _table_rows_by_headers_setup_code(table: Dict[str, Any]) -> str:
    row_selector = _table_row_selector(table)
    headers = _table_view_headers(table)
    if headers:
        header_literals = repr(headers)
        return (
            f"    _headers = {header_literals}\n"
            "    _table = None\n"
            "    for _attempt in range(40):\n"
            "        _tables = page.locator('table')\n"
            "        for _table_index in range(await _tables.count()):\n"
            "            _candidate = _tables.nth(_table_index)\n"
            "            _header_text = (await _candidate.locator('thead, tr').first.inner_text()).lower()\n"
            "            if all(str(_header).lower() in _header_text for _header in _headers):\n"
            "                _candidate_rows = _candidate.locator('tbody tr')\n"
            "                if await _candidate_rows.count():\n"
            "                    _table = _candidate\n"
            "                    break\n"
            "        if _table is not None:\n"
            "            break\n"
            "        await page.wait_for_timeout(250)\n"
            "    if _table is None:\n"
            f"        _rows = page.locator({row_selector!r})\n"
            "    else:\n"
            "        _rows = _table.locator('tbody tr')\n"
        )
    return f"    _rows = page.locator({row_selector!r})\n"


def _table_view_headers(table: Dict[str, Any]) -> List[str]:
    headers: List[str] = []
    for column in table.get("columns") or []:
        header = str(column.get("header") or "").strip()
        if header and header not in headers:
            headers.append(header)
    for row in table.get("rows") or []:
        for cell in row.get("cells") or []:
            header = str(cell.get("column_header") or "").strip()
            if header and header not in headers:
                headers.append(header)
    return headers


def _table_first_row_markers(table: Dict[str, Any]) -> List[str]:
    rows = list(table.get("rows") or [])
    if not rows:
        return []
    markers: List[str] = []
    for cell in list(rows[0].get("cells") or []):
        text = str(cell.get("text") or cell.get("value") or "").strip()
        if text and text not in markers:
            markers.append(text)
        if len(markers) >= 3:
            break
    return markers


def _indent_code(code: str, spaces: int) -> str:
    prefix = " " * spaces
    normalized_lines = []
    for line in code.splitlines(keepends=True):
        if line.startswith("    "):
            line = line[4:]
        normalized_lines.append(prefix + line if line.strip() else line)
    return "".join(normalized_lines)


def _table_first_n_rows_plan(table: Dict[str, Any], limit: int) -> Optional[Dict[str, Any]]:
    columns = []
    for column in table.get("columns") or []:
        header = str(column.get("header") or "").strip()
        if not header:
            continue
        column_id = str(column.get("column_id") or "").strip()
        if column_id:
            selector = f"td[data-colid={column_id!r}]"
        else:
            index = int(column.get("index") or 0) + 1
            selector = f"td:nth-child({index})"
        columns.append((header, selector))
    if not columns:
        return None

    rows_setup = _table_rows_setup_code(table)
    column_specs = repr(columns)
    code = (
        "async def run(page, results):\n"
        f"{rows_setup}"
        f"    _limit = min({limit}, await _rows.count())\n"
        f"    _columns = {column_specs}\n"
        "    _records = []\n"
        "    for _i in range(_limit):\n"
        "        _row = _rows.nth(_i)\n"
        "        _record = {}\n"
        "        for _header, _selector in _columns:\n"
        "            _cell = _row.locator(_selector)\n"
        "            _record[_header] = (await _cell.inner_text()).strip() if await _cell.count() else ''\n"
        "        _records.append(_record)\n"
        "    return _records"
    )
    return {
        "description": "Extract first table rows",
        "action_type": "run_python",
        "expected_effect": "extract",
        "output_key": "table_rows",
        "code": code,
        "table_ordinal_overlay": True,
    }


def _table_column_action_selector(table: Dict[str, Any], index: int, column: Dict[str, Any]) -> str:
    column_id = str(column.get("column_id") or "")
    column_index = int(column.get("index") or 0) + 1
    rows = list(table.get("rows") or [])
    if index >= len(rows):
        return ""
    for cell in rows[index].get("cells") or []:
        if column_id and str(cell.get("column_id") or "") != column_id:
            continue
        cell_selector = f"td[data-colid={column_id!r}]" if column_id else f"td:nth-child({column_index})"
        actions = list(cell.get("actions") or cell.get("row_local_actions") or [])
        for action in actions:
            locator = action.get("locator") if isinstance(action, dict) else {}
            if isinstance(locator, dict) and locator.get("scope") == "row" and locator.get("value"):
                value = str(locator.get("value") or "").strip()
                if _is_cell_scoped_single_action_selector(value, cell_selector):
                    return _cell_action_selector(cell_selector)
                return value
    if column_id:
        return _cell_action_selector(f"td[data-colid={column_id!r}]")
    return _cell_action_selector(f"td:nth-child({column_index})")


def _table_click_action_selector(table: Dict[str, Any], index: int, preferred_column: Dict[str, Any]) -> str:
    """Choose a row-local action without depending on recorded row text."""

    selector = _table_column_action_selector_if_present(table, index, preferred_column)
    if selector:
        return selector
    if _column_can_contain_primary_action(preferred_column):
        return _table_column_action_selector(table, index, preferred_column)

    columns = list(table.get("columns") or [])
    for column in columns:
        if _column_can_contain_primary_action(column):
            selector = _table_column_action_selector_if_present(table, index, column)
            if selector:
                return selector
            return _table_column_action_selector(table, index, column)

    row = list(table.get("rows") or [])[index]
    for cell_index, cell in enumerate(list(row.get("cells") or [])):
        if list(cell.get("actions") or cell.get("row_local_actions") or []):
            column_id = str(cell.get("column_id") or "")
            if column_id:
                return _cell_action_selector(f"td[data-colid={column_id!r}]")
            return _cell_action_selector(f"td:nth-child({cell_index + 1})")
    return ""


def _column_can_contain_primary_action(column: Dict[str, Any]) -> bool:
    role = str(column.get("role") or "").strip().lower()
    header = _normalize_instruction_text(str(column.get("header") or ""))
    return role in {"action", "file_link", "link"} or header in {
        "action",
        "actions",
        "operation",
        "operations",
        "操作",
    }


def _table_column_action_selector_if_present(table: Dict[str, Any], index: int, column: Dict[str, Any]) -> str:
    rows = list(table.get("rows") or [])
    if index >= len(rows):
        return ""
    column_id = str(column.get("column_id") or "")
    column_index = int(column.get("index") or 0)
    for cell in rows[index].get("cells") or []:
        same_column = (
            bool(column_id) and str(cell.get("column_id") or "") == column_id
        ) or int(cell.get("column_index") or 0) == column_index
        if not same_column:
            continue
        actions = list(cell.get("actions") or cell.get("row_local_actions") or [])
        if not actions:
            return ""
        cell_selector = f"td[data-colid={column_id!r}]" if column_id else f"td:nth-child({column_index + 1})"
        for action in actions:
            locator = action.get("locator") if isinstance(action, dict) else {}
            if isinstance(locator, dict) and locator.get("scope") == "row" and locator.get("value"):
                value = str(locator.get("value") or "").strip()
                if _is_cell_scoped_single_action_selector(value, cell_selector):
                    return _cell_action_selector(cell_selector)
                return value
        return _cell_action_selector(cell_selector)
    return ""


def _table_row_action_selector(table: Dict[str, Any], row_index: int) -> str:
    columns = list(table.get("columns") or [])
    action_columns = [
        column
        for column in columns
        if str(column.get("role") or "").lower() == "action"
        or _normalize_instruction_text(str(column.get("header") or "")) in {"action", "actions", "operation", "operations", "操作"}
    ]
    for column in action_columns:
        selector = _table_column_action_selector(table, row_index, column)
        if selector:
            return selector
    row = list(table.get("rows") or [])[row_index]
    for cell_index, cell in enumerate(list(row.get("cells") or [])):
        actions = list(cell.get("actions") or cell.get("row_local_actions") or [])
        if not actions:
            continue
        column_id = str(cell.get("column_id") or "")
        if column_id:
            return _cell_action_selector(f"td[data-colid={column_id!r}]")
        return _cell_action_selector(f"td:nth-child({cell_index + 1})")
    return ""


def _table_row_index_matching_tokens(table: Dict[str, Any], target_tokens: List[str]) -> Optional[int]:
    rows = list(table.get("rows") or [])
    for index, row in enumerate(rows):
        text = _normalize_instruction_text(_row_visible_text(row))
        if any(_normalize_instruction_text(token) in text for token in target_tokens):
            return index
    return None


def _row_visible_text(row: Dict[str, Any]) -> str:
    values: List[str] = []
    for cell in list(row.get("cells") or []):
        if not isinstance(cell, dict):
            continue
        text = str(cell.get("text") or cell.get("value") or "").strip()
        if text:
            values.append(text)
    return " ".join(values)


def _instruction_entity_tokens(instruction: str) -> List[str]:
    tokens: List[str] = []
    for match in re.finditer(
        r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9][A-Za-z0-9_-]{3,}(?![A-Za-z0-9_])",
        str(instruction or ""),
    ):
        token = match.group(0).strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens[:5]


def _snapshot_contains_text(snapshot: Dict[str, Any], needle: str) -> bool:
    target = _normalize_instruction_text(needle)
    if not target:
        return False

    allowed_text_keys = {
        "text",
        "name",
        "title",
        "value",
        "header",
        "column_header",
        "label",
        "description",
    }
    stack: List[Any] = [snapshot]
    visited = 0
    while stack and visited < 2000:
        current = stack.pop()
        visited += 1
        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(value, (dict, list)):
                    stack.append(value)
                elif str(key) in allowed_text_keys:
                    text = _normalize_instruction_text(str(value or ""))
                    if target and target in text:
                        return True
        elif isinstance(current, list):
            stack.extend(current)
    return False


def _is_cell_scoped_single_action_selector(selector: str, cell_selector: str) -> bool:
    text = str(selector or "").strip()
    if not text.startswith(cell_selector):
        return False
    suffix = text[len(cell_selector) :].strip()
    return suffix in {"a", "button", "[role=button]", "[role=link]"}


def _cell_action_selector(cell_selector: str) -> str:
    return (
        f"{cell_selector} a, "
        f"{cell_selector} button, "
        f"{cell_selector} [role=button], "
        f"{cell_selector} [role=link]"
    )


def _extract_repeated_candidate_collection(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    instruction = str(snapshot.get("_instruction") or "")
    for node in snapshot.get("actionable_nodes") or []:
        selector = str(node.get("collection_item_selector") or "").strip()
        count = int(node.get("collection_item_count") or 0)
        label = _node_label(node)
        if not selector or count < 2 or not label:
            continue
        if _is_page_chrome_collection(selector, instruction):
            continue
        if _looks_like_secondary_action_label(label):
            continue
        if str(node.get("role") or "").strip().lower() not in {"link", "button"}:
            continue
        grouped.setdefault(selector, []).append(node)

    if not grouped:
        return _extract_repeated_candidate_collection_from_frames(snapshot)

    grouped = {
        selector: nodes
        for selector, nodes in grouped.items()
        if len({_node_label(node).lower() for node in nodes}) >= 2
        and any(_looks_like_primary_item_label(_node_label(node)) for node in nodes)
    }
    if not grouped:
        return _extract_repeated_candidate_collection_from_frames(snapshot)

    selector, nodes = max(
        grouped.items(),
        key=lambda item: _score_ordinal_primary_collection(
            item[0],
            [_node_label(node) for node in item[1]],
            len(item[1]),
        ),
    )
    items = []
    for index, node in enumerate(_sort_snapshot_nodes(nodes)):
        label = _node_label(node)
        if not label:
            continue
        items.append(
            {
                "index": index,
                "title": label,
                "container_id": str(node.get("container_id") or ""),
                "primary_selector": selector,
            }
        )
    if len(items) < 2:
        return None

    secondary = _extract_secondary_action_selectors(snapshot, items)
    return {
        "kind": "repeated_candidates",
        "source": "raw_snapshot",
        "primary_selector": selector,
        "items": items,
        "secondary_selectors": secondary,
    }


def _extract_repeated_candidate_collection_from_frames(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    instruction = str(snapshot.get("_instruction") or "")
    for frame in snapshot.get("frames") or []:
        collections = list(frame.get("collections") or [])
        for collection in collections:
            if str(collection.get("kind") or "") != "repeated_items":
                continue
            selector = _collection_item_css_selector(collection)
            if not selector:
                continue
            container_selector = _collection_container_css_selector(collection)
            if _is_page_chrome_collection(" ".join([selector, container_selector]), instruction):
                continue
            role = str((collection.get("item_hint") or {}).get("role") or "").strip().lower()
            if role and role not in {"link", "button"}:
                continue

            items: List[Dict[str, Any]] = []
            labels: List[str] = []
            for item in collection.get("items") or []:
                label = _node_label(item)
                if not _looks_like_primary_item_label(label):
                    continue
                labels.append(label)
                items.append(
                    {
                        "index": len(items),
                        "title": label,
                        "container_id": "",
                        "primary_selector": selector,
                    }
                )

            if len(items) < 2 or len({label.lower() for label in labels}) < 2:
                continue

            candidates.append(
                {
                    "kind": "repeated_candidates",
                    "source": "raw_snapshot.frames.collections",
                    "primary_selector": selector,
                    "items": items,
                    "secondary_selectors": _extract_frame_secondary_action_selectors(collections, collection),
                    "_score": _score_ordinal_primary_collection(
                        selector,
                        labels,
                        int(collection.get("item_count") or len(items)),
                    ),
                }
            )

    if not candidates:
        return None

    selected = max(candidates, key=lambda item: item["_score"])
    selected.pop("_score", None)
    return selected


def _is_page_chrome_collection(selector: str, instruction: str) -> bool:
    normalized = _normalize_instruction_text(instruction)
    if any(term in normalized for term in ("menu", "navigation", "nav", "toolbar", "sidebar", "header", "菜单", "导航", "工具栏")):
        return False
    selector_text = str(selector or "").lower()
    return bool(re.search(r"(?:^|[.#\s_-])(toolbar|nav|navbar|menu|menubar|sidebar|header)(?:$|[.#\s_-])", selector_text))


def _collection_item_css_selector(collection: Dict[str, Any]) -> str:
    item_hint = collection.get("item_hint") if isinstance(collection, dict) else {}
    locator = item_hint.get("locator") if isinstance(item_hint, dict) else {}
    if not isinstance(locator, dict) or locator.get("method") != "css":
        return ""
    return str(locator.get("value") or "").strip()


def _extract_frame_secondary_action_selectors(
    collections: List[Dict[str, Any]],
    primary_collection: Dict[str, Any],
) -> Dict[str, str]:
    primary_container = _collection_container_css_selector(primary_collection)
    if not primary_container:
        return {}

    selectors: Dict[str, str] = {}
    for collection in collections:
        if collection is primary_collection:
            continue
        if _collection_container_css_selector(collection) != primary_container:
            continue
        selector = _collection_item_css_selector(collection)
        if not selector:
            continue
        labels = [_node_label(item) for item in collection.get("items") or []]
        if sum(1 for label in labels if "download" in label.lower() or "下载" in label) >= 2:
            selectors["download"] = selector
    return selectors


def _collection_container_css_selector(collection: Dict[str, Any]) -> str:
    container_hint = collection.get("container_hint") if isinstance(collection, dict) else {}
    locator = container_hint.get("locator") if isinstance(container_hint, dict) else {}
    if not isinstance(locator, dict) or locator.get("method") != "css":
        return ""
    return str(locator.get("value") or "").strip()


def _extract_secondary_action_selectors(
    snapshot: Dict[str, Any],
    items: List[Dict[str, Any]],
) -> Dict[str, str]:
    item_container_ids = {str(item.get("container_id") or "") for item in items if item.get("container_id")}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for node in snapshot.get("actionable_nodes") or []:
        container_id = str(node.get("container_id") or "")
        if container_id not in item_container_ids:
            continue
        label = _node_label(node).lower()
        selector = str(node.get("collection_item_selector") or "").strip()
        if not selector:
            continue
        if "download" in label or "下载" in label:
            grouped.setdefault("download", []).append(node)

    selectors: Dict[str, str] = {}
    for action, nodes in grouped.items():
        by_selector: Dict[str, int] = {}
        for node in nodes:
            selector = str(node.get("collection_item_selector") or "").strip()
            by_selector[selector] = by_selector.get(selector, 0) + 1
        selector, count = max(by_selector.items(), key=lambda item: item[1])
        if count >= min(2, len(items)):
            selectors[action] = selector
    return selectors


def _select_secondary_action_selector(collection: Dict[str, Any], instruction: str) -> str:
    text = str(instruction or "").lower()
    secondary = collection.get("secondary_selectors") if isinstance(collection, dict) else {}
    if ("download" in text or "下载" in text) and isinstance(secondary, dict):
        return str(secondary.get("download") or "")
    return ""


def _ordinal_extract_title_plan(selector: str, index: int) -> Dict[str, Any]:
    code = (
        "async def run(page, results):\n"
        f"    _item = page.locator({selector!r}).nth({index})\n"
        "    return (await _item.inner_text()).strip()"
    )
    return {
        "description": "Extract ordinal item title",
        "action_type": "run_python",
        "expected_effect": "extract",
        "output_key": "ordinal_item_name",
        "code": code,
        "ordinal_overlay": True,
    }


def _ordinal_first_n_titles_plan(selector: str, limit: int) -> Dict[str, Any]:
    code = (
        "async def run(page, results):\n"
        f"    _items = page.locator({selector!r})\n"
        f"    _limit = min({limit}, await _items.count())\n"
        "    _result = []\n"
        "    for _index in range(_limit):\n"
        "        _result.append((await _items.nth(_index).inner_text()).strip())\n"
        "    return _result"
    )
    return {
        "description": "Extract first ordinal item titles",
        "action_type": "run_python",
        "expected_effect": "extract",
        "output_key": "ordinal_item_names",
        "code": code,
        "ordinal_overlay": True,
    }


def _ordinal_click_plan(
    selector: str,
    index: int,
    *,
    description: str,
    expected_effect: str = "click",
) -> Dict[str, Any]:
    code = (
        "async def run(page, results):\n"
        f"    _label = (await page.locator({selector!r}).nth({index}).inner_text()).strip()\n"
        "    _before_url = page.url\n"
        f"    await page.locator({selector!r}).nth({index}).click()\n"
        "    try:\n"
        "        await page.wait_for_load_state('domcontentloaded', timeout=5000)\n"
        "    except Exception:\n"
        "        pass\n"
        "    await page.wait_for_timeout(500)\n"
        "    return {'action_performed': True, 'action_type': 'click', "
        f"'ordinal_index': {index}, 'clicked_label': _label, 'from_url': _before_url, 'url': page.url}}"
    )
    return {
        "description": description,
        "action_type": "run_python",
        "expected_effect": expected_effect,
        "output_key": "ordinal_item_action",
        "code": code,
        "ordinal_overlay": True,
        "terminal_contract": _click_terminal_contract() if expected_effect == "click" else {},
    }


def _click_terminal_contract() -> Dict[str, Any]:
    return {
        "required": True,
        "kind": "state_change",
        "success_evidence": [{"type": "feedback_visible"}, {"type": "url_changed"}],
        "allow_semantic_judge": True,
    }


def _node_label(node: Dict[str, Any]) -> str:
    return " ".join(str(node.get(key) or "").strip() for key in ("name", "text") if str(node.get(key) or "").strip()).strip()


def _looks_like_primary_item_label(label: str) -> bool:
    text = str(label or "").strip()
    if not text or _looks_like_secondary_action_label(text):
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", text))


def _score_ordinal_primary_collection(selector: str, labels: List[str], item_count: int) -> tuple[int, int, int, int, int, int]:
    meaningful_labels = [label for label in labels if _looks_like_primary_item_label(label)]
    distinct_count = len({label.lower() for label in meaningful_labels})
    heading_selector = 1 if re.search(r"(^|\s)h[1-6](\.|\s|$)", selector) else 0
    slash_pair_count = sum(1 for label in meaningful_labels if re.search(r"\S+\s*/\s*\S+", label))
    average_length = int(sum(len(label) for label in meaningful_labels) / max(len(meaningful_labels), 1))
    return (
        heading_selector,
        slash_pair_count,
        min(int(item_count or 0), 25),
        distinct_count,
        min(average_length, 80),
        len(meaningful_labels),
    )


def _sort_snapshot_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        nodes,
        key=lambda node: (
            int((node.get("bbox") or {}).get("y", 0) or 0),
            int((node.get("bbox") or {}).get("x", 0) or 0),
            int(node.get("index") or 0),
            str(node.get("node_id") or ""),
        ),
    )


def _looks_like_secondary_action_label(label: str) -> bool:
    text = str(label or "").strip().lower()
    if not text:
        return True
    return any(token in text for token in ("download", "下载"))
