from __future__ import annotations

import inspect
import re
from typing import Any, Dict
from urllib.parse import urljoin, urlparse

from .recording_contracts import normalize_terminal_contract
from .recording_verifier import (
    output_has_nonterminal_marker,
    output_looks_unsuccessful,
    verify_terminal_contract,
)
from .trace_models import RPAPageState


async def _ensure_expected_effect(
    *,
    page: Any,
    instruction: str,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    before: RPAPageState,
) -> Dict[str, Any]:
    if not result.get("success"):
        recovered = await _recover_failed_result_with_terminal_evidence(
            page=page,
            plan=plan,
            result=result,
            before=before,
            expected_effect=_expected_effect(plan, instruction),
        )
        if recovered:
            return recovered
        return result

    expected_effect = _expected_effect(plan, instruction)
    if expected_effect in {"none", "extract"}:
        result = await _restore_extract_surface_if_needed(page=page, before=before, result=result)
        return result

    after = await _page_state(page)
    if not _normalize_bool(plan.get("allow_empty_output")) and _looks_like_unsuccessful_output(result.get("output")):
        if _terminal_contract_kind(plan) == "empty_result" and _looks_like_empty_result_output(result.get("output")):
            effect = dict(result.get("effect") or {})
            effect.setdefault("type", expected_effect)
            effect["terminal_evidence"] = "empty_result"
            return {**result, "effect": effect}
        return {
            **result,
            "success": False,
            "error": "Generated command returned visible error or validation output instead of terminal success evidence.",
        }

    if expected_effect in {"navigate", "mixed"}:
        if _url_changed(before.url, after.url):
            terminal = _verify_terminal_if_required(plan=plan, result=result, before=before, after=after)
            if terminal["required"] and not terminal["passed"]:
                return _terminal_failure(result, terminal)
            effect = dict(result.get("effect") or {})
            effect.update({"type": "navigate", "url": after.url, "observed_url_change": True})
            if terminal["required"]:
                return _with_terminal_verification(result, effect, terminal)
            return {**result, "effect": effect}

        if expected_effect == "mixed":
            generic_evidence = _generic_effect_evidence(result)
            if generic_evidence:
                terminal = _verify_terminal_if_required(plan=plan, result=result, before=before, after=after)
                if terminal["required"] and not terminal["passed"]:
                    return _terminal_failure(result, terminal)
                effect = dict(result.get("effect") or {})
                effect.setdefault("type", "mixed")
                effect["generic_evidence"] = generic_evidence
                if terminal["required"]:
                    return _with_terminal_verification(result, effect, terminal)
                return {**result, "effect": effect}

        target_url = _extract_target_url(result.get("output"), base_url=before.url) or _extract_target_url(
            plan,
            base_url=before.url,
        )
        if target_url:
            await page.goto(target_url, wait_until="domcontentloaded")
            await _wait_for_load_state(page, "domcontentloaded")
            after = await _page_state(page)
            if _url_changed(before.url, after.url):
                terminal = _verify_terminal_if_required(plan=plan, result=result, before=before, after=after)
                if terminal["required"] and not terminal["passed"]:
                    return _terminal_failure(result, terminal)
                effect = dict(result.get("effect") or {})
                effect.update(
                    {
                        "type": "navigate",
                        "url": after.url,
                        "auto_completed": True,
                        "source": "output_url",
                    }
                )
                if terminal["required"]:
                    return _with_terminal_verification(result, effect, terminal)
                return {**result, "effect": effect}

        terminal = _verify_terminal_if_required(plan=plan, result=result, before=before, after=after)
        if terminal["required"] and terminal["passed"]:
            effect = dict(result.get("effect") or {})
            effect.update({"type": "state_change", "action_performed": True})
            return _with_terminal_verification(result, effect, terminal)

        return {
            **result,
            "success": False,
            "error": "Expected navigation effect, but the page URL did not change and no target URL was available.",
        }

    if expected_effect in {"click", "fill"}:
        terminal = _verify_terminal_if_required(plan=plan, result=result, before=before, after=after)

        effect = result.get("effect")
        if isinstance(effect, dict) and effect.get("action_performed"):
            if terminal["required"] and not terminal["passed"]:
                return _terminal_failure(result, terminal)
            if terminal["required"]:
                return _with_terminal_verification(result, dict(effect), terminal)
            return result

        output = result.get("output")
        if isinstance(output, dict) and output.get("action_performed"):
            output_action_type = str(output.get("action_type") or output.get("type") or "").strip().lower()
            has_fill_value = expected_effect != "fill" or "filled_value" in output or "value" in output
            if has_fill_value and (not output_action_type or output_action_type == expected_effect):
                if terminal["required"] and not terminal["passed"]:
                    return _terminal_failure(result, terminal)
                effect = dict(effect or {})
                effect.update(
                    {
                        "type": expected_effect,
                        "action_performed": True,
                        "source": "output_evidence",
                    }
                )
                if terminal["required"]:
                    return _with_terminal_verification(result, effect, terminal)
                return {**result, "effect": effect}

        action_type = str(plan.get("action_type") or "").strip().lower()
        if action_type == expected_effect:
            if terminal["required"] and not terminal["passed"]:
                return _terminal_failure(result, terminal)
            return {**result, "effect": {"type": expected_effect, "action_performed": True}}

        if expected_effect == "click" and action_type == "run_python":
            if _url_changed(before.url, after.url):
                effect = dict(result.get("effect") or {})
                effect.update(
                    {
                        "type": "click",
                        "action_performed": True,
                        "observed_url_change": True,
                        "url": after.url,
                    }
                )
                return {**result, "effect": effect}

        if action_type == "run_python" and _run_python_code_contains_effect(plan, expected_effect):
            generic_evidence = _generic_effect_evidence(result)
            if generic_evidence:
                if terminal["required"] and not terminal["passed"]:
                    return _terminal_failure(result, terminal)
                effect = dict(result.get("effect") or {})
                effect.setdefault("type", expected_effect)
                effect["action_performed"] = True
                effect["generic_evidence"] = generic_evidence
                if terminal["required"]:
                    return _with_terminal_verification(result, effect, terminal)
                return {**result, "effect": effect}

        return {
            **result,
            "success": False,
            "error": f"Expected {expected_effect} effect, but no browser action evidence was produced.",
        }

    return result


async def _recover_failed_result_with_terminal_evidence(
    *,
    page: Any,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    before: RPAPageState,
    expected_effect: str,
) -> Dict[str, Any]:
    if not normalize_terminal_contract(plan).get("required"):
        return {}
    if result.get("traceback") or output_looks_unsuccessful(result.get("output")):
        return {}
    if not isinstance(result.get("output"), (dict, list)):
        return {}
    after = await _page_state(page)
    terminal = _verify_terminal_if_required(plan=plan, result=result, before=before, after=after)
    if not terminal["required"] or not terminal["passed"]:
        return {}
    effect = dict(result.get("effect") or {})
    effect.setdefault("type", expected_effect)
    effect["terminal_evidence"] = _terminal_evidence_name(terminal)
    effect["terminal_evidence_items"] = terminal.get("evidence", [])
    effect["recovered_from_structured_failure"] = True
    return {
        **result,
        "success": True,
        "error": None,
        "effect": effect,
        "structured_failure_recovered": True,
    }


def _run_python_code_contains_effect(plan: Dict[str, Any], expected_effect: str) -> bool:
    code = str(plan.get("code") or "")
    if expected_effect == "click":
        return any(token in code for token in (".click(", ".press(", ".check(", ".uncheck(", ".select_option("))
    if expected_effect == "fill":
        return any(token in code for token in (".fill(", ".type(", ".press_sequentially(", ".select_option("))
    return False


def _generic_effect_evidence(result: Dict[str, Any]) -> str:
    effect = result.get("effect")
    if isinstance(effect, dict) and _normalize_bool(effect.get("action_performed")):
        return "action_performed"

    signals = result.get("signals")
    if isinstance(signals, dict) and signals.get("download"):
        return "download"
    if isinstance(signals, dict) and signals.get("extract_snapshot"):
        return "extract_snapshot"

    if _has_non_empty_structured_output(result.get("output")):
        return "structured_output"

    return ""


def _verify_terminal_if_required(
    *,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    before: RPAPageState,
    after: RPAPageState,
) -> Dict[str, Any]:
    return verify_terminal_contract(plan=plan, result=result, before=before, after=after)


def _terminal_failure(result: Dict[str, Any], terminal: Dict[str, Any]) -> Dict[str, Any]:
    missing = terminal.get("missing_evidence") or ["terminal_evidence"]
    return {
        **result,
        "success": False,
        "error": f"Generated command stopped without required terminal evidence: {missing}.",
        "terminal_verification": terminal,
    }


def _with_terminal_verification(result: Dict[str, Any], effect: Dict[str, Any], terminal: Dict[str, Any]) -> Dict[str, Any]:
    effect["terminal_evidence"] = _terminal_evidence_name(terminal)
    effect["terminal_evidence_items"] = terminal.get("evidence", [])
    return {**result, "effect": effect, "terminal_verification": terminal}


def _terminal_evidence_name(terminal: Dict[str, Any]) -> str:
    evidence = terminal.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        return str(evidence[0].get("type") or "terminal_evidence")
    return "terminal_evidence"


def _terminal_contract_kind(plan: Dict[str, Any]) -> str:
    return str(normalize_terminal_contract(plan).get("kind") or "none")


def _looks_like_empty_result_output(value: Any) -> bool:
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value).strip().lower()
        return bool(
            re.search(
                r"\b(?:no matching|no match|no result|zero results?|not found|empty)\b"
                r"|没有匹配|无匹配|没有结果|空结果|未找到|暂无数据|无数据",
                text,
            )
        )
    if isinstance(value, dict):
        keys = {str(key).strip().lower() for key in value.keys()}
        if keys & {"error", "errors", "exception", "traceback"}:
            return False
        return any(_looks_like_empty_result_output(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_looks_like_empty_result_output(item) for item in value)
    return False


def _has_non_empty_structured_output(value: Any) -> bool:
    if _looks_like_unsuccessful_output(value):
        return False
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return False


def _looks_like_unsuccessful_output(value: Any) -> bool:
    return output_looks_unsuccessful(value) or output_has_nonterminal_marker(value)


def _expected_effect(plan: Dict[str, Any], instruction: str) -> str:
    action_type = str(plan.get("action_type") or "").strip().lower()
    if action_type == "extract_snapshot":
        return "extract"

    explicit = _normalize_expected_effect(plan.get("expected_effect") or plan.get("effect"))
    if explicit != "extract":
        return explicit

    if action_type == "goto":
        return "navigate"
    if action_type in {"click", "fill"}:
        return action_type
    return explicit


def _normalize_expected_effect(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"extract", "navigate", "click", "fill", "mixed", "none"} else "extract"


def _should_drain_download_events(plan: Dict[str, Any], code: str) -> bool:
    action_type = str(plan.get("action_type") or "").strip().lower()
    if action_type in {"click", "press"}:
        return True
    if action_type != "run_python":
        return False
    return any(
        token in code
        for token in (
            ".click(",
            ".press(",
            ".check(",
            ".uncheck(",
            ".select_option(",
            ".set_input_files(",
        )
    )


def _merge_runtime_ai_signal(signals: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    if not _normalize_bool(plan.get("preserve_runtime_ai")):
        return signals
    if not _should_keep_runtime_ai_preserve_signal(plan):
        return signals
    runtime_ai = signals.get("runtime_ai") if isinstance(signals.get("runtime_ai"), dict) else {}
    reason = str(plan.get("semantic_intent") or runtime_ai.get("reason") or "semantic_candidate_selection").strip()
    signals["runtime_ai"] = {
        **runtime_ai,
        "preserve": True,
        "reason": reason or "semantic_candidate_selection",
    }
    return signals


def _should_keep_runtime_ai_preserve_signal(plan: Dict[str, Any]) -> bool:
    intent = str(plan.get("semantic_intent") or "").strip().lower()
    allowed_intents = {
        "semantic_candidate_selection",
        "select_best_matching_candidate",
    }
    if intent not in allowed_intents:
        return False

    code = str(plan.get("code") or "").lower()
    browser_mutation_markers = (
        ".click(",
        ".dblclick(",
        ".fill(",
        ".press(",
        ".check(",
        ".uncheck(",
        ".select_option(",
        ".set_input_files(",
        ".goto(",
        ".evaluate(",
    )
    if _contains_any(code, browser_mutation_markers):
        return False

    deterministic_form_markers = (
        ".fill(",
        ".select_option(",
        ".set_input_files(",
        "get_by_label(",
        "get_by_placeholder(",
    )
    if _contains_any(code, deterministic_form_markers):
        return False

    return True


def _normalize_visible_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


async def _page_state(page: Any) -> RPAPageState:
    title = ""
    title_fn = getattr(page, "title", None)
    if callable(title_fn):
        value = title_fn()
        if inspect.isawaitable(value):
            value = await value
        title = str(value or "")
    return RPAPageState(url=str(getattr(page, "url", "") or ""), title=title)


async def _restore_extract_surface_if_needed(
    *,
    page: Any,
    before: RPAPageState,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    after = await _page_state(page)
    if not before.url or not _url_changed(before.url, after.url):
        return result
    if not _is_machine_endpoint_url(after.url, before_url=before.url):
        return result

    restore_url = _last_user_facing_url(result.get("navigation_history"), before_url=before.url) or before.url
    await page.goto(restore_url, wait_until="domcontentloaded")
    await _wait_for_load_state(page, "domcontentloaded")
    restored = await _page_state(page)
    effect = dict(result.get("effect") or {})
    effect.update(
        {
            "type": "extract",
            "restored_after_transient_endpoint": True,
            "transient_url": after.url,
            "url": restored.url,
        }
    )
    return {**result, "effect": effect}


async def _wait_for_load_state(page: Any, state: str) -> None:
    wait_for_load_state = getattr(page, "wait_for_load_state", None)
    if not callable(wait_for_load_state):
        return
    wait_result = wait_for_load_state(state)
    if inspect.isawaitable(wait_result):
        await wait_result


def _url_changed(before_url: str, after_url: str) -> bool:
    before = str(before_url or "").rstrip("/")
    after = str(after_url or "").rstrip("/")
    return bool(after) and before != after


def _is_machine_endpoint_url(url: str, *, before_url: str = "") -> bool:
    parsed = urlparse(str(url or ""))
    if not parsed.scheme or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host.startswith("api.") or ".api." in host:
        return True
    if "/api/" in path or path.startswith("/api/"):
        return True
    if path.endswith((".json", ".xml")):
        return True

    before_host = urlparse(str(before_url or "")).netloc.lower()
    return bool(before_host and host != before_host and host.startswith(("raw.", "gist.")))


def _last_user_facing_url(history: Any, *, before_url: str = "") -> str:
    if not isinstance(history, list):
        return ""
    for item in reversed(history):
        url = str(item or "").strip()
        if url and not _is_machine_endpoint_url(url, before_url=before_url):
            return url
    return ""


def _extract_target_url(value: Any, *, base_url: str = "") -> str:
    if isinstance(value, str):
        return _normalize_target_url(value, base_url=base_url)
    if isinstance(value, dict):
        prioritized_keys = ["target_url", "url", "href", "value"]
        url_like_keys = [
            str(key)
            for key in value.keys()
            if str(key).strip().lower().endswith("_url") and str(key) not in prioritized_keys
        ]
        for key in [*prioritized_keys, *url_like_keys]:
            target_url = _extract_target_url(value.get(key), base_url=base_url)
            if target_url:
                return target_url
        output_url = _extract_target_url(value.get("output"), base_url=base_url)
        if output_url:
            return output_url
    return ""


def _normalize_target_url(value: str, *, base_url: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("/") and base_url:
        return urljoin(base_url, text)
    return ""
