from __future__ import annotations

from typing import Any, Dict, List


TERMINAL_KINDS = {
    "none",
    "state_change",
    "record_created",
    "record_updated",
    "record_removed",
    "download_created",
    "dialog_dismissed",
    "empty_result",
}

EVIDENCE_TYPES = {
    "url_changed",
    "download_created",
    "toast_visible",
    "feedback_visible",
    "dialog_closed",
    "row_exists",
    "row_absent",
    "row_status_changed",
    "field_value_equals",
    "postcondition",
    "empty_result",
}

STRONG_EVIDENCE_TYPES = {
    "url_changed",
    "download_created",
    "toast_visible",
    "feedback_visible",
    "row_exists",
    "row_absent",
    "row_status_changed",
    "field_value_equals",
    "postcondition",
    "empty_result",
}


def normalize_terminal_contract(plan: Dict[str, Any]) -> Dict[str, Any]:
    raw = plan.get("terminal_contract")
    if not isinstance(raw, dict):
        raw = {}

    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in TERMINAL_KINDS:
        kind = "none"

    evidence = _normalize_evidence_list(raw.get("success_evidence") or raw.get("evidence"))
    required = _normalize_bool(raw.get("required")) or bool(evidence)
    if kind == "none" and required:
        kind = "state_change"

    return {
        "required": required,
        "kind": kind,
        "success_evidence": evidence,
        "allow_semantic_judge": _normalize_bool(raw.get("allow_semantic_judge")),
    }


def terminal_contract_required(plan: Dict[str, Any]) -> bool:
    return bool(normalize_terminal_contract(plan).get("required"))


def _normalize_evidence_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []

    result: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence_type = str(item.get("type") or "").strip().lower()
        if evidence_type not in EVIDENCE_TYPES:
            continue
        normalized = dict(item)
        normalized["type"] = evidence_type
        result.append(normalized)
    return result


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)
