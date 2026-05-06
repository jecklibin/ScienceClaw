from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .recording_ordinal_preplans import _instruction_entity_tokens, _normalize_instruction_text
from .recording_ordinal_preplans import _snapshot_contains_text


def build_modal_form_preplanned_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entity = _first_entity_token(instruction)
    comment = _quoted_text(instruction)
    if not entity or not comment:
        return None
    if not _is_modal_form_intent(instruction):
        return None
    if not _snapshot_contains_text(snapshot, entity):
        return None
    if not _snapshot_has_row_action_for_entity(snapshot, entity):
        return None
    text = _normalize_instruction_text(instruction)
    if not any(marker in text for marker in ("submit", "save", "complete", "confirm", "提交", "保存", "完成", "确认", "同意")):
        return None
    return _build_row_modal_comment_submit_plan(entity, comment)


def _first_entity_token(instruction: str) -> str:
    tokens = _instruction_entity_tokens(instruction)
    return tokens[0] if tokens else ""


def _instruction_text_variants(instruction: str) -> list[str]:
    text = str(instruction or "")
    variants = [text]
    try:
        repaired = text.encode("gbk").decode("utf-8")
        if repaired and repaired != text:
            variants.append(repaired)
    except Exception:
        pass
    return variants


def _quoted_text(instruction: str) -> str:
    for text in _instruction_text_variants(instruction):
        for pattern in (
            r"“([^”]{1,200})”",
            r"‘([^’]{1,200})’",
            r"\"([^\"]{1,200})\"",
            r"'([^']{1,200})'",
        ):
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
    return ""


def _is_modal_form_intent(instruction: str) -> bool:
    text = _normalize_instruction_text(instruction)
    modal_markers = (
        "modal",
        "dialog",
        "popup",
        "comment",
        "remark",
        "note",
        "reason",
        "opinion",
        "review",
        "approve",
        "reject",
        "弹窗",
        "对话框",
        "评论",
        "备注",
        "原因",
        "意见",
        "审核",
        "审批",
        "批准",
        "驳回",
    )
    create_form_markers = (
        "create",
        "new",
        "add request",
        "fill request",
        "based on",
        "新建",
        "创建",
        "新增",
        "采购申请",
        "基于",
    )
    comment_or_review_markers = (
        "comment",
        "remark",
        "opinion",
        "review",
        "approve",
        "reject",
        "评论",
        "备注",
        "意见",
        "审核",
        "审批",
        "批准",
        "驳回",
    )
    if any(marker in text for marker in create_form_markers) and not any(
        marker in text for marker in comment_or_review_markers
    ):
        return False
    return any(marker in text for marker in modal_markers)


def _snapshot_has_row_action_for_entity(snapshot: Dict[str, Any], entity: str) -> bool:
    entity_text = str(entity or "").strip()
    if not entity_text:
        return False
    for table in snapshot.get("table_views") or []:
        if not isinstance(table, dict):
            continue
        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            cells = row.get("cells") or []
            row_text = " ".join(str(cell.get("text") or "") for cell in cells if isinstance(cell, dict))
            if entity_text not in row_text:
                continue
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                if cell.get("actions"):
                    return True
            if row.get("actions"):
                return True
    return False


def _build_row_modal_comment_submit_plan(entity: str, comment: str) -> Dict[str, Any]:
    code = (
        "async def run(page, results):\n"
        "    import re as _re\n"
        f"    _entity = {entity!r}\n"
        f"    _comment = {comment!r}\n"
        "    async def _first_visible(locator, limit=30):\n"
        "        count = min(await locator.count(), limit)\n"
        "        for index in range(count):\n"
        "            item = locator.nth(index)\n"
        "            try:\n"
        "                if await item.is_visible() and (not hasattr(item, 'is_enabled') or await item.is_enabled()):\n"
        "                    return item\n"
        "            except Exception:\n"
        "                continue\n"
        "        return None\n"
        "    _row = None\n"
        "    for selector in ('tbody tr', '[role=row]', 'li[data-row-key]', '[data-row-key]'):\n"
        "        _rows = page.locator(selector).filter(has_text=_entity)\n"
        "        try:\n"
        "            await _rows.first.wait_for(state='visible', timeout=10000)\n"
        "        except Exception:\n"
        "            pass\n"
        "        _row = await _first_visible(_rows)\n"
        "        if _row is not None:\n"
        "            break\n"
        "    if _row is None:\n"
        "        raise RuntimeError(f'No visible row found for {_entity!r}')\n"
        "    _action = await _first_visible(_row.locator('button, a, [role=button], [role=link]').filter(has_not_text=_entity))\n"
        "    if _action is None:\n"
        "        raise RuntimeError(f'No row action found for {_entity!r}')\n"
        "    await _action.click(timeout=8000)\n"
        "    _dialog = page.locator(\"[role='dialog'], [aria-modal='true']\").last\n"
        "    await _dialog.wait_for(state='visible', timeout=10000)\n"
        "    _input = await _first_visible(_dialog.locator('textarea, input:not([type=hidden]), [contenteditable=true], [role=textbox]'))\n"
        "    if _input is None:\n"
        "        raise RuntimeError('No editable field found in dialog')\n"
        "    await _input.fill(_comment)\n"
        "    _submit_pattern = _re.compile(r'(submit|save|confirm|ok|yes|完成|提交|保存|确认|同意)', _re.I)\n"
        "    _submit = await _first_visible(_dialog.get_by_role('button', name=_submit_pattern))\n"
        "    if _submit is None:\n"
        "        _submit = await _first_visible(_dialog.locator('button[type=submit], input[type=submit], [data-testid*=submit], [data-test*=submit], [aria-label*=submit i], [title*=submit i]'))\n"
        "    if _submit is None:\n"
        "        raise RuntimeError('No enabled submit button found in dialog')\n"
        "    await _submit.click(timeout=8000)\n"
        "    await page.wait_for_timeout(700)\n"
        "    return {'action_performed': True, 'action_type': 'modal_form_submit', 'target_text': _entity, 'filled_value': _comment, 'submitted': True}\n"
    )
    return {
        "description": "Open matching row dialog, fill quoted text, and submit",
        "action_type": "run_python",
        "expected_effect": "state_change",
        "output_key": "modal_form_submission",
        "code": code,
        "terminal_contract": {
            "required": True,
            "kind": "record_updated",
            "success_evidence": [{"type": "feedback_visible"}, {"type": "row_status_changed"}, {"type": "field_value_equals"}],
            "allow_semantic_judge": True,
        },
        "modal_form_submit": True,
    }
