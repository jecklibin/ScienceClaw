from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .recording_contracts import normalize_terminal_contract


def snapshot_diff_terminal_postcondition(
    *,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    recovery = _updated_row_recovery(
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        plan=plan,
        result=result,
    )
    if not recovery:
        recovery = _new_row_recovery(
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            plan=plan,
            result=result,
        )
    if not recovery:
        recovery = _disappeared_row_recovery(
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            plan=plan,
            result=result,
        )
    return recovery


def recover_failed_side_effect_from_snapshot_diff(
    *,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Recover a failed side-effect attempt only when DOM table facts prove it.

    This is intentionally narrow: it does not interpret business words or error
    strings. It only accepts a failed attempt when a terminal contract required a
    durable change and the snapshot diff has a replayable table fact correlated
    with the plan, bindings, postcondition, or failure/output facts.
    """

    if result.get("success"):
        return None
    contract = normalize_terminal_contract(plan)
    if not contract.get("required") or contract.get("kind") not in {
        "record_created",
        "record_updated",
        "record_removed",
        "state_change",
    }:
        return None

    recovery = snapshot_diff_terminal_postcondition(
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        plan=plan,
        result=result,
    )
    if not recovery:
        return None

    postcondition = recovery["postcondition"]
    evidence = recovery["evidence"]

    signals = dict(result.get("signals") or {})
    signals["recovered_attempt"] = {
        "ignore_errors": True,
        "reason": "failed attempt produced a correlated snapshot-diff postcondition",
    }
    signals["terminal_evidence"] = evidence

    recovered_result = {
        **result,
        "success": True,
        "error": None,
        "signals": signals,
        "output": {
            "recovered_attempt": True,
            "row": recovery["row_values"],
        },
        "effect": {
            "type": "mixed",
            "terminal_evidence": evidence[0]["type"],
            "terminal_evidence_items": evidence,
            "recovered_from_failed_attempt": True,
        },
    }
    recovered_plan = {
        **plan,
        "postcondition": postcondition,
    }
    return recovered_plan, recovered_result


def _new_row_recovery(
    *,
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
    plan: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    diff = _find_table_row_matching_plan_values(
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        plan=plan,
        result=result,
    )
    if not diff:
        return {}
    postcondition = _postcondition_from_row(diff, kind="table_row_exists")
    if not postcondition:
        return {}
    evidence = [
        {
            "type": "row_exists",
            "source": "snapshot",
            "summary": "A correlated table row appeared after the attempted browser action.",
        }
    ]
    if postcondition.get("expect"):
        evidence.append(
            {
                "type": "field_value_equals",
                "source": "snapshot",
                "summary": "The new table row includes a visible terminal status/value.",
            }
        )
    return {"postcondition": postcondition, "evidence": evidence, "row_values": diff["row_values"]}


def _disappeared_row_recovery(
    *,
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
    plan: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    diff = _find_disappeared_table_row_matching_plan_values(
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        plan=plan,
        result=result,
    )
    if not diff:
        return {}
    postcondition = _postcondition_from_row(diff, kind="table_row_absent")
    if not postcondition:
        return {}
    evidence = [
        {
            "type": "row_absent",
            "source": "snapshot",
            "summary": "A correlated table row disappeared after the attempted browser action.",
        }
    ]
    return {"postcondition": postcondition, "evidence": evidence, "row_values": diff["row_values"]}


def _updated_row_recovery(
    *,
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
    plan: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    diff = _find_updated_table_row_matching_plan_values(
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        plan=plan,
        result=result,
    )
    if not diff:
        return {}
    postcondition = _postcondition_from_row(diff, kind="table_row_exists")
    if not postcondition:
        return {}
    evidence = [
        {
            "type": "row_status_changed",
            "source": "snapshot",
            "summary": "A correlated table row changed after the browser action.",
        },
        {
            "type": "field_value_equals",
            "source": "snapshot",
            "summary": "The changed table row includes the expected terminal values.",
        },
    ]
    return {"postcondition": postcondition, "evidence": evidence, "row_values": diff["row_values"]}


def _find_table_row_matching_plan_values(
    *,
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
    plan: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_values = _plan_candidate_values(plan, result)
    if not candidate_values:
        return {}
    min_score = min(2, len(candidate_values))

    before_signatures = {row["signature"] for row in _snapshot_rows(before_snapshot)}
    scored_rows: List[Tuple[int, Dict[str, Any]]] = []
    for row in _snapshot_rows(after_snapshot):
        if row["signature"] in before_signatures:
            continue
        row_values = {_clean_text(value) for value in row.get("row_values", {}).values() if _clean_text(value)}
        matched_values = row_values & candidate_values
        score = len(matched_values)
        if score >= min_score:
            scored_row = dict(row)
            scored_row["matched_values"] = matched_values
            scored_rows.append((score, scored_row))
    if not scored_rows:
        return {}
    scored_rows.sort(key=lambda item: item[0], reverse=True)
    if len(scored_rows) > 1 and scored_rows[0][0] == scored_rows[1][0]:
        return {}
    return scored_rows[0][1]


def _find_updated_table_row_matching_plan_values(
    *,
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
    plan: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_values = _plan_candidate_values(plan, result)
    if not candidate_values:
        return {}
    after_rows = _snapshot_rows(after_snapshot)
    scored_rows: List[Tuple[int, Dict[str, Any]]] = []
    for before_row in _snapshot_rows(before_snapshot):
        before_values = _data_cell_values(before_row.get("row_values", {}))
        key_header = _select_key_header(before_values)
        key_value = before_values.get(key_header, "")
        if not key_header or not key_value:
            continue
        for after_row in after_rows:
            after_values = _data_cell_values(after_row.get("row_values", {}))
            if after_values.get(key_header) != key_value:
                continue
            if before_values == after_values:
                continue
            changed_headers = {
                header
                for header, value in after_values.items()
                if header != key_header and before_values.get(header) != value
            }
            if not changed_headers:
                continue
            row_values = {_clean_text(value) for value in after_values.values() if _clean_text(value)}
            matched_values = row_values & candidate_values
            if not matched_values:
                continue
            score = len(matched_values) + len(changed_headers)
            scored_row = dict(after_row)
            scored_row["matched_values"] = matched_values
            scored_row["changed_headers"] = changed_headers
            scored_rows.append((score, scored_row))
    if not scored_rows:
        return {}
    scored_rows.sort(key=lambda item: item[0], reverse=True)
    if len(scored_rows) > 1 and scored_rows[0][0] == scored_rows[1][0]:
        return {}
    return scored_rows[0][1]


def _find_disappeared_table_row_matching_plan_values(
    *,
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
    plan: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_values = _plan_candidate_values(plan, result)
    if not candidate_values:
        return {}
    min_score = min(2, len(candidate_values))
    after_rows = _snapshot_rows(after_snapshot)
    after_signatures = {row["signature"] for row in after_rows}
    after_value_sets = [
        {_clean_text(value) for value in row.get("row_values", {}).values() if _clean_text(value)}
        for row in after_rows
    ]
    scored_rows: List[Tuple[int, Dict[str, Any]]] = []
    for row in _snapshot_rows(before_snapshot):
        if row["signature"] in after_signatures:
            continue
        if not _has_comparable_table_context(row, after_snapshot):
            continue
        row_values = {_clean_text(value) for value in row.get("row_values", {}).values() if _clean_text(value)}
        matched_values = row_values & candidate_values
        score = len(matched_values)
        if score < min_score:
            continue
        if any(matched_values and matched_values.issubset(values) for values in after_value_sets):
            continue
        scored_rows.append((score, row))
    if not scored_rows:
        return {}
    scored_rows.sort(key=lambda item: item[0], reverse=True)
    if len(scored_rows) > 1 and scored_rows[0][0] == scored_rows[1][0]:
        return {}
    return scored_rows[0][1]


def _has_comparable_table_context(row: Dict[str, Any], after_snapshot: Dict[str, Any]) -> bool:
    headers = {_clean_text(header) for header in list(row.get("headers") or []) if _clean_text(header)}
    if not headers:
        return False
    min_overlap = min(2, len(headers))
    for table in list(after_snapshot.get("table_views") or []):
        if not isinstance(table, dict):
            continue
        after_headers = {_clean_text(header) for header in _table_headers(table) if _clean_text(header)}
        if len(headers & after_headers) >= min_overlap:
            return True
    return False


_ENTITY_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9][A-Za-z0-9_-]{3,}(?![A-Za-z0-9_])"
)


def _plan_candidate_values(plan: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> set[str]:
    values: set[str] = set()
    for binding in (plan.get("input_bindings") or {}).values() if isinstance(plan.get("input_bindings"), dict) else []:
        if not isinstance(binding, dict):
            continue
        for key in ("default", "value", "example"):
            text = _clean_text(binding.get(key))
            if len(text) >= 2:
                values.add(text)
    postcondition = plan.get("postcondition") if isinstance(plan.get("postcondition"), dict) else {}
    for group_name in ("key", "expect"):
        group = postcondition.get(group_name) if isinstance(postcondition.get(group_name), dict) else {}
        for value in group.values():
            text = _clean_text(value)
            if len(text) >= 2 and not _is_template_ref(text):
                values.add(text)
    structured_output = result.get("output") if isinstance(result, dict) else None
    for source in (structured_output,):
        for token in _entity_tokens(source):
            values.add(token)
    return values


def _entity_tokens(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    tokens: List[str] = []
    for match in _ENTITY_TOKEN_RE.finditer(text):
        token = _clean_text(match.group(0))
        if token and token not in tokens:
            tokens.append(token)
    return tokens[:20]


def _is_template_ref(value: str) -> bool:
    text = str(value or "").strip()
    return text.startswith("{{") and text.endswith("}}")


def _snapshot_rows(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for table_index, table in enumerate(snapshot.get("table_views") or []):
        if not isinstance(table, dict):
            continue
        headers = _table_headers(table)
        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            values = _row_values(row, headers)
            if not values:
                continue
            signature = "\u241f".join(f"{key}={value}" for key, value in sorted(values.items()))
            rows.append(
                {
                    "table_index": table_index,
                    "headers": headers,
                    "row_values": values,
                    "signature": signature,
                }
            )
    return rows


def _table_headers(table: Dict[str, Any]) -> List[str]:
    headers: List[str] = []
    for column in table.get("columns") or []:
        if not isinstance(column, dict):
            continue
        text = _clean_text(column.get("header") or column.get("name") or column.get("column_id"))
        if text:
            headers.append(text)
    return headers


def _row_values(row: Dict[str, Any], headers: List[str]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for index, cell in enumerate(row.get("cells") or []):
        if not isinstance(cell, dict):
            continue
        header = _clean_text(cell.get("column_header"))
        if not header and index < len(headers):
            header = headers[index]
        if not header:
            header = f"index:{index}"
        text = _clean_text(cell.get("text") or cell.get("value"))
        if text:
            values[header] = text
    return values


def _postcondition_from_row(diff: Dict[str, Any], *, kind: str) -> Dict[str, Any]:
    values = diff.get("row_values") if isinstance(diff.get("row_values"), dict) else {}
    values = _data_cell_values(values)
    if not values:
        return {}
    key_header = _select_key_header(values)
    if not key_header:
        return {}
    expect_values = _select_expect_values(
        values,
        key_header=key_header,
        matched_values=diff.get("matched_values"),
        changed_headers=diff.get("changed_headers"),
    )
    postcondition = {
        "kind": kind,
        "source": "snapshot",
        "table_headers": [header for header in values.keys() if not str(header).startswith("index:")][:8],
        "key": {key_header: values[key_header]},
        "expect": {},
    }
    if expect_values:
        postcondition["expect"] = expect_values
    return postcondition


def _select_expect_values(
    values: Dict[str, str],
    *,
    key_header: str,
    matched_values: Any = None,
    changed_headers: Any = None,
) -> Dict[str, str]:
    matched = {_clean_text(value) for value in matched_values or [] if _clean_text(value)}
    changed = {str(header) for header in changed_headers or [] if str(header)}
    expect: Dict[str, str] = {}
    for header, value in values.items():
        if header == key_header or not value:
            continue
        if _looks_volatile_value(value):
            continue
        if header in changed or _clean_text(value) in matched:
            expect[header] = value
        if len(expect) >= 4:
            break
    if expect:
        return expect
    status_header = _select_status_header(values, key_header)
    return {status_header: values[status_header]} if status_header else {}


def _looks_volatile_value(value: Any) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    return bool(
        re.search(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?", text)
        or re.fullmatch(r"\d{10,}", text)
    )


def _select_key_header(values: Dict[str, str]) -> str:
    generic_identifier_markers = ("id", "no", "number", "code", "key", "编号", "单号", "代码")
    for header, value in values.items():
        lowered = header.lower()
        if value and any(marker in lowered for marker in generic_identifier_markers):
            return header
    for header, value in values.items():
        if value:
            return header
    return ""


def _select_status_header(values: Dict[str, str], key_header: str) -> str:
    generic_status_markers = ("status", "state", "阶段", "状态")
    for header, value in values.items():
        if header == key_header or not value:
            continue
        lowered = header.lower()
        if any(marker in lowered for marker in generic_status_markers):
            return header
    return ""


def _data_cell_values(values: Dict[str, str]) -> Dict[str, str]:
    return {
        header: value
        for header, value in values.items()
        if value and not _is_action_header(header)
    }


def _is_action_header(header: Any) -> bool:
    normalized = _clean_text(header).lower()
    if not normalized:
        return False
    return normalized in {"action", "actions", "operation", "operations", "operate", "操作"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()
