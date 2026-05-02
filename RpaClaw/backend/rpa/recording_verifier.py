from __future__ import annotations

import inspect
from typing import Any, Dict, List

from .recording_contracts import STRONG_EVIDENCE_TYPES, normalize_terminal_contract
from .trace_models import RPAPageState


async def capture_browser_evidence(page: Any) -> Dict[str, Any]:
    dialogs = await _visible_locator_texts(page, "[role='dialog'],[aria-modal='true']", limit=4)
    feedback = await _visible_locator_texts(
        page,
        (
            "[role='status'],[aria-live],[role='alert'],"
            "[data-rpa-feedback],[data-feedback],[data-toast],[data-notification],"
            ".toast,.snackbar"
        ),
        limit=6,
    )
    validation = await _visible_locator_texts(
        page,
        "[aria-invalid='true'],input:invalid,textarea:invalid,select:invalid",
        limit=6,
    )
    return {
        "visible_dialog_count": len(dialogs),
        "visible_dialogs": dialogs,
        "feedback_texts": feedback,
        "validation_texts": validation,
    }


def verify_terminal_contract(
    *,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    before: RPAPageState,
    after: RPAPageState,
) -> Dict[str, Any]:
    contract = normalize_terminal_contract(plan)
    if not contract["required"]:
        return {"required": False, "passed": True, "evidence": [], "missing_evidence": []}

    observed = collect_observed_evidence(plan=plan, result=result, before=before, after=after)
    desired_types = {
        str(item.get("type") or "").strip().lower()
        for item in contract.get("success_evidence") or []
        if str(item.get("type") or "").strip()
    }
    strong_observed = [
        item
        for item in observed
        if (
            item.get("type") in STRONG_EVIDENCE_TYPES
            and (item.get("type") != "empty_result" or contract.get("kind") == "empty_result")
        )
        or (item.get("type") == "dialog_closed" and contract.get("kind") == "dialog_dismissed")
    ]
    if desired_types:
        matched = [item for item in strong_observed if item.get("type") in desired_types]
    else:
        matched = strong_observed

    if matched:
        return {"required": True, "passed": True, "evidence": matched, "missing_evidence": []}
    if contract.get("kind") == "state_change" and strong_observed:
        return {"required": True, "passed": True, "evidence": strong_observed, "missing_evidence": []}

    if any(item.get("type") == "validation_error_visible" for item in observed):
        return {
            "required": True,
            "passed": False,
            "evidence": observed,
            "missing_evidence": ["validation_error_absent"],
            "reason": "validation_error_visible",
        }

    missing = sorted(desired_types) if desired_types else [str(contract.get("kind") or "terminal_evidence")]
    return {
        "required": True,
        "passed": False,
        "evidence": observed,
        "missing_evidence": missing,
        "reason": "missing_terminal_evidence",
    }


def collect_observed_evidence(
    *,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    before: RPAPageState,
    after: RPAPageState,
) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    if _url_changed(before.url, after.url):
        evidence.append({"type": "url_changed", "before_url": before.url, "after_url": after.url})

    signals = result.get("signals") if isinstance(result.get("signals"), dict) else {}
    if isinstance(signals.get("download"), dict):
        evidence.append({"type": "download_created", **signals["download"]})
    evidence.extend(_normalize_output_evidence(signals.get("terminal_evidence")))
    if _output_has_download_evidence(result.get("output")):
        evidence.append({"type": "download_created", "source": "observed"})

    browser = result.get("browser_evidence") if isinstance(result.get("browser_evidence"), dict) else {}
    before_browser = browser.get("before") if isinstance(browser.get("before"), dict) else {}
    after_browser = browser.get("after") if isinstance(browser.get("after"), dict) else {}
    if int(before_browser.get("visible_dialog_count") or 0) > 0 and int(after_browser.get("visible_dialog_count") or 0) == 0:
        evidence.append({"type": "dialog_closed"})
    before_feedback = {
        _normalize_feedback_text(text)
        for text in list(before_browser.get("feedback_texts") or [])
        if _normalize_feedback_text(text)
    }
    for text in list(after_browser.get("feedback_texts") or [])[:3]:
        normalized = _normalize_feedback_text(text)
        if normalized and normalized not in before_feedback:
            if _feedback_looks_negative(normalized):
                evidence.append({"type": "validation_error_visible", "text": normalized, "source": "browser"})
                continue
            evidence.append({"type": "feedback_visible", "text": normalized, "source": "browser"})
    for text in list(after_browser.get("validation_texts") or [])[:3]:
        if str(text or "").strip():
            evidence.append({"type": "validation_error_visible", "text": str(text).strip()})

    evidence.extend(_observed_terminal_output_evidence(result.get("output")))
    if _output_has_empty_result_evidence(result.get("output")):
        evidence.append({"type": "empty_result", "source": "observed"})
    if _output_has_row_absent_evidence(result.get("output")):
        evidence.append({"type": "row_absent", "source": "observed"})
    return _dedupe_evidence(evidence)


def output_looks_unsuccessful(value: Any) -> bool:
    if isinstance(value, dict):
        keys = {str(key).strip().lower() for key in value.keys()}
        if keys & {"error", "errors", "exception", "traceback"}:
            return True
        return any(output_looks_unsuccessful(item) for item in value.values())
    if isinstance(value, list):
        return any(output_looks_unsuccessful(item) for item in value)
    return False


def output_has_nonterminal_marker(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "").strip().lower()
            if isinstance(item, bool) and item is False and key_text in {
                "downloaded",
                "completed",
                "confirmed",
                "saved",
                "submitted",
                "success",
            }:
                return True
            if output_has_nonterminal_marker(item):
                return True
        return False
    if isinstance(value, list):
        return any(output_has_nonterminal_marker(item) for item in value)
    return False


def _observed_terminal_output_evidence(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        found: List[Dict[str, Any]] = []
        for key in ("terminal_evidence", "terminal_observation", "observed_evidence"):
            found.extend(_normalize_output_evidence(value.get(key)))
        for item in value.values():
            found.extend(_observed_terminal_output_evidence(item))
        return found
    if isinstance(value, list):
        found: List[Dict[str, Any]] = []
        for item in value:
            found.extend(_observed_terminal_output_evidence(item))
        return found
    return []


def _normalize_output_evidence(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    result: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence_type = str(item.get("type") or "").strip().lower()
        source = str(item.get("source") or "").strip().lower()
        if evidence_type not in STRONG_EVIDENCE_TYPES:
            continue
        if source not in {"browser", "page", "dom", "download", "observed", "snapshot"}:
            continue
        if item.get("observed") is False:
            continue
        normalized = dict(item)
        normalized["type"] = evidence_type
        result.append(normalized)
    return result


def _normalize_feedback_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _feedback_looks_negative(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in ("invalid", "failed", "failure", "error", "rejected", "denied", "失败", "错误", "无效", "拒绝"))


def _output_has_empty_result_evidence(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "").strip().lower()
            if key_text in {
                "empty_result",
                "empty_result_confirmed",
                "empty_state",
                "empty_state_confirmed",
                "no_results",
                "no_matching_results",
                "no_matches",
            } and item is True:
                return True
            if key_text in {
                "row_count",
                "row_count_after",
                "result_count",
                "record_count",
                "matched_count",
                "match_count",
                "matched_rows",
                "matching_rows",
                "filtered_rows",
                "visible_rows",
                "total",
                "count",
            } or key_text.endswith(("_count", "count")):
                try:
                    if int(item) == 0:
                        return True
                except (TypeError, ValueError):
                    pass
            if key_text in {"rows", "items", "results", "records", "data"} and isinstance(item, list) and not item:
                return True
            if _output_has_empty_result_evidence(item):
                return True
        return False
    if isinstance(value, list):
        return len(value) == 0
    return False


def _output_has_row_absent_evidence(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "").strip().lower()
            if key_text in {"match_found", "found", "exists", "present", "matched"} and item is False:
                return True
            if key_text in {
                "row_absent",
                "record_absent",
                "not_found",
                "no_results",
                "no_matching_results",
                "no_matches",
            } and item is True:
                return True
            if _output_has_row_absent_evidence(item):
                return True
        return False
    if isinstance(value, list):
        return any(_output_has_row_absent_evidence(item) for item in value)
    return False


def _output_has_download_evidence(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "").strip().lower()
            if key_text in {"download_created", "downloaded", "download_complete", "file_downloaded"} and item is True:
                return True
            if key_text in {
                "downloaded_file",
                "download_filename",
                "download_suggested_filename",
                "suggested_filename",
                "filename",
            } and str(item or "").strip():
                return True
            if key_text in {"download", "downloaded_file"} and isinstance(item, dict):
                if item.get("filename") or item.get("path") or item.get("suggested_filename"):
                    return True
            if _output_has_download_evidence(item):
                return True
        return False
    if isinstance(value, list):
        return any(_output_has_download_evidence(item) for item in value)
    return False


async def _visible_locator_texts(page: Any, selector: str, *, limit: int) -> List[str]:
    locator = _safe_locator(page, selector)
    if locator is None:
        return []
    try:
        count = await locator.count()
    except Exception:
        return []
    texts: List[str] = []
    for index in range(min(int(count or 0), limit)):
        item = locator.nth(index)
        try:
            visible = item.is_visible()
            if inspect.isawaitable(visible):
                visible = await visible
            if not visible:
                continue
        except Exception:
            continue
        text = await _locator_text(item)
        if text:
            texts.append(text)
    return texts


async def _locator_text(locator: Any) -> str:
    for name in ("inner_text", "text_content"):
        fn = getattr(locator, name, None)
        if not callable(fn):
            continue
        try:
            value = fn()
            if inspect.isawaitable(value):
                value = await value
            text = " ".join(str(value or "").split())
            if text:
                return text[:300]
        except Exception:
            continue
    return ""


def _safe_locator(page: Any, selector: str) -> Any:
    locator_fn = getattr(page, "locator", None)
    if not callable(locator_fn):
        return None
    try:
        return locator_fn(selector)
    except Exception:
        return None


def _dedupe_evidence(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = tuple(sorted((str(k), str(v)) for k, v in item.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _url_changed(before_url: str, after_url: str) -> bool:
    before = str(before_url or "").rstrip("/")
    after = str(after_url or "").rstrip("/")
    return bool(after) and before != after
