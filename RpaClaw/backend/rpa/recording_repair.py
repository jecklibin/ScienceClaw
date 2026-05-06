from __future__ import annotations

from typing import Any, Dict, List, Optional


def _classify_recording_failure(error: Any) -> Dict[str, str]:
    text = str(error or "").strip()
    normalized = text.lower()
    if not normalized:
        return {"type": "unknown"}

    if "locator' object is not subscriptable" in normalized or "locator object is not subscriptable" in normalized:
        return {
            "type": "locator_not_subscriptable",
            "hint": (
                "Playwright Python Locator objects are lazy and are not list-like. In repair, iterate with "
                "`count()` and `nth(index)`, or use `locator.first`, instead of slicing or direct iteration."
            ),
        }

    if "input[type=number]" in normalized or "role=\"spinbutton\"" in normalized or "role='spinbutton'" in normalized:
        return {
            "type": "numeric_input_text_mismatch",
            "hint": (
                "A number input or spinbutton was treated as the wrong field or was filled with non-numeric text. "
                "In repair, map each value to labels, column headers, row-local controls, aria names, placeholders, "
                "or nearby text before filling; only numeric strings should be filled into number inputs."
            ),
        }

    if "intercepts pointer events" in normalized or "subtree intercepts pointer" in normalized:
        return {
            "type": "active_overlay_intercepted_click",
            "hint": (
                "A visible overlay or dialog intercepted the click. In repair, do not click the same background "
                "trigger again; scope actions to the visible dialog, overlay, focused form, or its buttons."
            ),
        }

    if (
        ("locator.fill" in normalized or "locator.click" in normalized or "fill action" in normalized or "click action" in normalized)
        and (
            "element is not visible" in normalized
            or "not visible" in normalized
            or "not editable" in normalized
            or "not enabled" in normalized
            or "visible, enabled and editable" in normalized
        )
    ):
        return {
            "type": "element_not_visible_or_not_editable",
            "hint": (
                "The locator matched or was attempted, but Playwright could not act on a visible/enabled/editable "
                "element. In repair, inspect the page after failure and choose a truly visible interactive candidate; "
                "for search goals, consider a direct encoded results URL unless the user explicitly needs UI typing."
            ),
        }

    if "strict mode violation" in normalized:
        return {
            "type": "strict_locator_violation",
            "hint": (
                "The attempted locator matched multiple elements. In repair, prefer a more scoped Playwright "
                "locator, role/name combination, or DOM scan that selects the intended element from candidates."
            ),
        }

    if (
        ("wait_for_selector" in normalized or "locator" in normalized)
        and "timeout" in normalized
        and ("waiting for" in normalized or "to be visible" in normalized)
    ):
        if "intercepts pointer events" in normalized or "subtree intercepts pointer" in normalized:
            return {
                "type": "active_overlay_intercepted_click",
                "hint": (
                    "A visible overlay or dialog intercepted the click. In repair, do not click the same background "
                    "trigger again; scope actions to the visible dialog, overlay, focused form, or its buttons."
                ),
            }
        return {
            "type": "selector_timeout",
            "hint": (
                "The previous attempt timed out waiting for a specific selector. In repair, re-check the current "
                "page state first and consider resilient extraction through candidate link/row scanning instead "
                "of only replacing one brittle selector with another."
            ),
        }

    if "element is not an <input>" in normalized or "does not have a role allowing" in normalized:
        return {
            "type": "non_editable_fill_target",
            "hint": (
                "The fill target was not editable. In repair, first locate visible editable controls by role, tag, "
                "placeholder, label, or proximity, and keep submit/search buttons for clicking only."
            ),
        }

    if "function' object has no attribute 'replace" in normalized or 'function" object has no attribute "replace' in normalized:
        return {
            "type": "invalid_callable_locator_filter",
            "hint": (
                "The generated Playwright Python passed a callable where Playwright expects a string or regex. "
                "In repair, replace callable locator filters with explicit locator chains, text filters, or loops."
            ),
        }

    output_looks_empty = "output" in normalized and "empty" in normalized
    if "returned no meaningful output" in normalized or "empty record" in normalized or output_looks_empty:
        return {
            "type": "empty_extract_output",
            "hint": (
                "The browser action ran but produced empty data. In repair, verify the page is the expected page, "
                "then broaden extraction candidates or add field-level validation before accepting the result."
            ),
        }

    if "net::" in normalized or "err_connection" in normalized or ("page.goto" in normalized and "timeout" in normalized):
        return {
            "type": "navigation_timeout_or_network",
            "hint": (
                "The failure happened during navigation or page loading. In repair, keep the raw network error in "
                "mind, avoid assuming selector failure, and use the current browser state if navigation partially succeeded."
            ),
        }

    if "syntaxerror" in normalized or "indentationerror" in normalized or "nameerror" in normalized:
        return {
            "type": "syntax_or_runtime_code_error",
            "hint": (
                "The generated Python failed before completing the browser task. In repair, fix the code shape first "
                "while preserving the original user goal and current page context."
            ),
        }

    if "expected navigation effect" in normalized or "url did not change" in normalized:
        return {
            "type": "wrong_page_or_no_goal_progress",
            "hint": (
                "The code did not produce the browser-visible effect requested by the user. In repair, distinguish "
                "between extraction-only and action/navigation goals, then provide observable evidence for the intended effect."
            ),
        }

    return {"type": "unknown"}


def _known_failure_analysis(error: Any) -> Optional[Dict[str, str]]:
    analysis = _classify_recording_failure(error)
    return analysis if analysis.get("type") != "unknown" else None


def _repair_guidance_for_failure(
    *,
    error: Any,
    instruction: str,
    failure_analysis: Optional[Dict[str, str]] = None,
    previous_failures: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    guidance: List[Dict[str, str]] = []
    error_text = str(error or "").lower()
    analysis_type = str((failure_analysis or {}).get("type") or "")

    if _is_terminal_contract_failure(error_text):
        guidance.append(
            {
                "kind": "terminal_effect",
                "message": (
                    "The previous attempt lacked the terminal evidence required by its contract. After the action, "
                    "return only browser-observed evidence such as a success/status region, changed row/status, "
                    "created identifier, final URL, or download event. Do not use a bare action acknowledgement."
                ),
            }
        )

    if analysis_type in {
        "selector_timeout",
        "strict_locator_violation",
        "element_not_visible_or_not_editable",
        "non_editable_fill_target",
        "active_overlay_intercepted_click",
    }:
        guidance.append(
            {
                "kind": "selector_retarget",
                "message": (
                    "Retarget from the current page facts instead of repeating the failed selector. Prefer visible "
                    "role/name, label, placeholder, form_views control locators, table/grid headers with row-local "
                    "controls, and dialog/form-scoped locators before raw CSS or positional input order."
                ),
            }
        )

    if analysis_type == "locator_not_subscriptable":
        guidance.append(
            {
                "kind": "playwright_locator_api",
                "message": (
                    "Playwright Python Locator is lazy and cannot be sliced or iterated directly. Use "
                    "`count()` plus `nth(index)`, `first`, or a DOM evaluate only when read-only and necessary."
                ),
            }
        )

    if _is_async_or_download_failure(error_text):
        guidance.append(
            {
                "kind": "bounded_polling",
                "message": (
                    "For async or download flows, use short bounded polling against visible status/action candidates. "
                    "Return success only after a ready/completed state, actionable download control, or Playwright "
                    "download event is observed; otherwise raise with the last visible state."
                ),
            }
        )

    if previous_failures:
        guidance.append(
            {
                "kind": "avoid_repeat",
                "message": (
                    "A previous repair also failed. Do not only retry the same locator or wait. Re-plan from the "
                    "current page snapshot and preserve the original business goal."
                ),
            }
        )

    return guidance


def _is_terminal_contract_failure(error_text: str) -> bool:
    return _contains_any(
        error_text,
        (
            "required terminal evidence",
            "terminal_evidence",
            "missing_terminal_evidence",
            "validation_error_visible",
        ),
    )


def _is_async_or_download_failure(error_text: str) -> bool:
    return _contains_any(
        error_text,
        (
            "download",
            "expect_download",
            "not ready",
            "timeout",
            "pending",
            "processing",
        ),
    )


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)
