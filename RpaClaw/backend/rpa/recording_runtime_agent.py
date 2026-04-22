from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from pydantic import BaseModel, Field

from .assistant_runtime import build_page_snapshot
from .frame_selectors import build_frame_path
from .snapshot_compression import compact_recording_snapshot
from .trace_models import RPAAcceptedTrace, RPAAIExecution, RPAPageState, RPATraceDiagnostic, RPATraceType


RECORDING_RUNTIME_SYSTEM_PROMPT = """You operate exactly one RPA recording command.
Return JSON only.
Schema:
{
  "description": "short user-facing action summary",
  "action_type": "run_python",
  "expected_effect": "extract|navigate|click|fill|mixed",
  "allow_empty_output": false,
  "output_key": "optional_ascii_snake_case_result_key",
  "code": "async def run(page, results): ..."
}
Rules:
- Complete only the current user command, not the full SOP.
- Return action_type="run_python" unless a simple goto/click/fill action is clearly enough.
- expected_effect describes the browser-visible outcome required by the user's current command.
- Use expected_effect="navigate" when the user asks to open, go to, enter, visit, or navigate to a target.
- Use expected_effect="extract" when the user only asks to find, collect, summarize, or return data without opening it.
- If code is returned, it must define async def run(page, results).
- Use Python Playwright async APIs.
- Prefer Playwright locators and page.locator/query_selector_all over page.evaluate.
- Avoid page.evaluate unless the snippet is short, read-only, and necessary.
- Do not include shell, filesystem, network requests outside the current browser page, or infinite loops.
- For search-engine tasks, if the user's goal is to search/open results, prefer navigating to the results URL with an encoded query. If the user explicitly asks to fill a search box, first target visible, enabled, editable input candidates instead of filling hidden DOM matches.
- Do not leave the browser on API, JSON, raw, or other machine endpoints after an extract-only command.
- For extract-only commands, prefer user-facing pages and restore the most recent user-facing page after any temporary helper navigation.
- For extract-only commands, prefer snapshot.expanded_regions and snapshot.sampled_regions before broad DOM scans.
- Use the region title, heading, or catalogue summary as context when it matches the requested area.
- If an expanded region is a label_value_group and the user asks for field names or values, keep extraction focused on that region or supporting locator evidence instead of scanning every table.
- Avoid treating tables as the default fallback for field extraction when a more relevant label_value_group is present.
- snapshot.region_catalogue is page context only.
- Do not include a separate done-check.
- If extracting data, return structured JSON-serializable Python values.
- For extract-only commands, do not return null/empty output unless the user explicitly allows empty results.
- Set allow_empty_output=true only when the user explicitly says no result, empty list, or empty output is acceptable.
- During repair, treat raw error logs and current page facts as authoritative. Any failure_analysis.hint is advisory only.
- During repair after a fill/click actionability failure, inspect the page after failure and visible candidates before retrying the selector.
"""


class RecordingAgentResult(BaseModel):
    success: bool
    trace: Optional[RPAAcceptedTrace] = None
    diagnostics: List[RPATraceDiagnostic] = Field(default_factory=list)
    output_key: Optional[str] = None
    output: Any = None
    message: str = ""


Planner = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
Executor = Callable[[Any, Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]


class RecordingRuntimeAgent:
    def __init__(
        self,
        planner: Optional[Planner] = None,
        executor: Optional[Executor] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ):
        self.planner = planner or self._default_planner
        self.executor = executor or self._default_executor
        self.model_config = model_config

    async def run(
        self,
        *,
        page: Any,
        instruction: str,
        runtime_results: Optional[Dict[str, Any]] = None,
    ) -> RecordingAgentResult:
        runtime_results = runtime_results if runtime_results is not None else {}
        before = await _page_state(page)
        snapshot = await _safe_page_snapshot(page)
        compact_snapshot = _compact_snapshot(snapshot, instruction)
        payload = {
            "instruction": instruction,
            "page": before.model_dump(mode="json"),
            "snapshot": compact_snapshot,
            "runtime_results": runtime_results,
        }
        _write_recording_snapshot_debug(
            "initial",
            instruction=instruction,
            page_state=before.model_dump(mode="json"),
            raw_snapshot=snapshot,
            compact_snapshot=compact_snapshot,
            runtime_results=runtime_results,
        )

        first_plan = await self.planner(payload)
        first_result = await self.executor(page, first_plan, runtime_results)
        first_result = await _ensure_expected_effect(
            page=page,
            instruction=instruction,
            plan=first_plan,
            result=first_result,
            before=before,
        )
        if first_result.get("success"):
            trace = await self._accepted_trace(
                page,
                instruction,
                first_plan,
                first_result,
                before,
                repair_attempted=False,
            )
            return RecordingAgentResult(
                success=True,
                trace=trace,
                output_key=trace.output_key,
                output=trace.output,
                message="Recording command completed.",
            )

        failed_page = await _page_state(page)
        failed_snapshot = await _safe_page_snapshot(page)
        compact_failed_snapshot = _compact_snapshot(failed_snapshot, instruction)
        first_error = str(first_result.get("error") or "recording command failed")
        first_failure_analysis = _classify_recording_failure(first_error)
        _write_recording_snapshot_debug(
            "repair",
            instruction=instruction,
            page_state=failed_page.model_dump(mode="json"),
            raw_snapshot=failed_snapshot,
            compact_snapshot=compact_failed_snapshot,
            runtime_results=runtime_results,
            extra={
                "failed_plan": _safe_jsonable(first_plan),
                "error": first_error,
                "failure_analysis": first_failure_analysis,
            },
        )
        diagnostics = [
            RPATraceDiagnostic(
                source="ai",
                message=first_error,
                raw={
                    "plan": _safe_jsonable(first_plan),
                    "result": _safe_jsonable(first_result),
                    "page_after_failure": failed_page.model_dump(mode="json"),
                    "snapshot_after_failure": _safe_jsonable(compact_failed_snapshot),
                    "failure_analysis": first_failure_analysis,
                },
            )
        ]

        repair_payload = {
            **payload,
            "repair": {
                "error": first_error,
                "failed_plan": first_plan,
                "page_after_failure": failed_page.model_dump(mode="json"),
                "snapshot_after_failure": compact_failed_snapshot,
                "failure_analysis": first_failure_analysis,
            },
        }
        repair_plan = await self.planner(repair_payload)
        repair_result = await self.executor(page, repair_plan, runtime_results)
        repair_result = await _ensure_expected_effect(
            page=page,
            instruction=instruction,
            plan=repair_plan,
            result=repair_result,
            before=before,
        )
        if repair_result.get("success"):
            trace = await self._accepted_trace(
                page,
                instruction,
                repair_plan,
                repair_result,
                before,
                repair_attempted=True,
            )
            return RecordingAgentResult(
                success=True,
                trace=trace,
                diagnostics=diagnostics,
                output_key=trace.output_key,
                output=trace.output,
                message="Recording command completed after one repair.",
            )

        repair_error = str(repair_result.get("error") or "recording command repair failed")
        diagnostics.append(
            RPATraceDiagnostic(
                source="ai",
                message=repair_error,
                raw={
                    "plan": _safe_jsonable(repair_plan),
                    "result": _safe_jsonable(repair_result),
                    "failure_analysis": _classify_recording_failure(repair_error),
                },
            )
        )
        return RecordingAgentResult(
            success=False,
            diagnostics=diagnostics,
            message="Recording command failed after one repair.",
        )

    async def _accepted_trace(
        self,
        page: Any,
        instruction: str,
        plan: Dict[str, Any],
        result: Dict[str, Any],
        before: RPAPageState,
        *,
        repair_attempted: bool,
    ) -> RPAAcceptedTrace:
        after = await _page_state(page)
        output = result.get("output")
        output_key = _normalize_result_key(plan.get("output_key"))
        return RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction=instruction,
            description=str(plan.get("description") or instruction),
            before_page=before,
            after_page=after,
            output_key=output_key,
            output=output,
            ai_execution=RPAAIExecution(
                language="python",
                code=str(plan.get("code") or ""),
                output=output,
                error=result.get("error"),
                repair_attempted=repair_attempted,
            ),
        )

    async def _default_planner(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from backend.deepagent.engine import get_llm_model
        from langchain_core.messages import HumanMessage, SystemMessage

        model = get_llm_model(config=self.model_config, streaming=False)
        response = await model.ainvoke(
            [
                SystemMessage(content=RECORDING_RUNTIME_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
            ]
        )
        return _parse_json_object(_extract_text(response))

    async def _default_executor(self, page: Any, plan: Dict[str, Any], runtime_results: Dict[str, Any]) -> Dict[str, Any]:
        action_type = str(plan.get("action_type") or "run_python").strip()
        try:
            if action_type == "goto":
                url = str(plan.get("url") or plan.get("target_url") or "")
                if not url:
                    return {"success": False, "error": "goto plan missing url", "output": ""}
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_load_state("domcontentloaded")
                return {
                    "success": True,
                    "output": {"url": getattr(page, "url", url)},
                    "effect": {"type": "navigate", "url": getattr(page, "url", url)},
                }

            if action_type == "click":
                selector = str(plan.get("selector") or "")
                if not selector:
                    return {"success": False, "error": "click plan missing selector", "output": ""}
                await page.locator(selector).first.click()
                return {"success": True, "output": "clicked", "effect": {"type": "click", "action_performed": True}}

            if action_type == "fill":
                selector = str(plan.get("selector") or "")
                value = plan.get("value", "")
                if not selector:
                    return {"success": False, "error": "fill plan missing selector", "output": ""}
                await page.locator(selector).first.fill(str(value))
                return {
                    "success": True,
                    "output": value,
                    "effect": {"type": "fill", "action_performed": True},
                }

            code = str(plan.get("code") or "")
            if "async def run(page, results)" not in code:
                return {"success": False, "error": "plan missing async def run(page, results)", "output": ""}
            namespace: Dict[str, Any] = {}
            exec(compile(code, "<recording_runtime_agent>", "exec"), namespace, namespace)
            runner = namespace.get("run")
            if not callable(runner):
                return {"success": False, "error": "No run(page, results) function defined", "output": ""}
            navigation_history: List[str] = []
            original_goto = getattr(page, "goto", None)
            goto_wrapped = False

            if callable(original_goto):
                async def tracked_goto(url: str, *args: Any, **kwargs: Any) -> Any:
                    response = original_goto(url, *args, **kwargs)
                    if inspect.isawaitable(response):
                        response = await response
                    navigation_history.append(str(getattr(page, "url", "") or url or ""))
                    return response

                try:
                    setattr(page, "goto", tracked_goto)
                    goto_wrapped = True
                except Exception:
                    goto_wrapped = False

            try:
                output = runner(page, runtime_results)
                if inspect.isawaitable(output):
                    output = await output
            finally:
                if goto_wrapped:
                    try:
                        setattr(page, "goto", original_goto)
                    except Exception:
                        pass

            response = {"success": True, "error": None, "output": output}
            if navigation_history:
                response["navigation_history"] = navigation_history
            return response
        except Exception as exc:
            return {"success": False, "error": str(exc), "output": ""}


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        if content:
            return content
        reasoning = getattr(response, "additional_kwargs", {}).get("reasoning_content") if hasattr(response, "additional_kwargs") else ""
        return str(reasoning or "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or item.get("thinking") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _parse_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Recording planner must return a JSON object")
    parsed.setdefault("action_type", "run_python")
    parsed["expected_effect"] = _normalize_expected_effect(parsed.get("expected_effect"))
    parsed["allow_empty_output"] = _normalize_bool(parsed.get("allow_empty_output"))
    if parsed.get("action_type") == "run_python" and "async def run(page, results)" not in str(parsed.get("code") or ""):
        raise ValueError("Recording planner must return Python code defining async def run(page, results)")
    return parsed


def _classify_recording_failure(error: Any) -> Dict[str, str]:
    text = str(error or "").strip()
    normalized = text.lower()
    if not normalized:
        return {"type": "unknown"}

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
        return {
            "type": "selector_timeout",
            "hint": (
                "The previous attempt timed out waiting for a specific selector. In repair, re-check the current "
                "page state first and consider resilient extraction through candidate link/row scanning instead "
                "of only replacing one brittle selector with another."
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


def _normalize_result_key(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return None
    if text[0].isdigit():
        text = f"result_{text}"
    return text[:64]


async def _page_state(page: Any) -> RPAPageState:
    title = ""
    title_fn = getattr(page, "title", None)
    if callable(title_fn):
        value = title_fn()
        if inspect.isawaitable(value):
            value = await value
        title = str(value or "")
    return RPAPageState(url=str(getattr(page, "url", "") or ""), title=title)


async def _ensure_expected_effect(
    *,
    page: Any,
    instruction: str,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    before: RPAPageState,
) -> Dict[str, Any]:
    if not result.get("success"):
        return result

    expected_effect = _expected_effect(plan, instruction)
    if expected_effect in {"none", "extract"}:
        result = await _restore_extract_surface_if_needed(page=page, before=before, result=result)
        return result

    if expected_effect in {"navigate", "mixed"}:
        after = await _page_state(page)
        if _url_changed(before.url, after.url):
            effect = dict(result.get("effect") or {})
            effect.update({"type": "navigate", "url": after.url, "observed_url_change": True})
            return {**result, "effect": effect}

        target_url = _extract_target_url(result.get("output"), base_url=before.url) or _extract_target_url(
            plan,
            base_url=before.url,
        )
        if target_url:
            await page.goto(target_url, wait_until="domcontentloaded")
            wait_for_load_state = getattr(page, "wait_for_load_state", None)
            if callable(wait_for_load_state):
                wait_result = wait_for_load_state("domcontentloaded")
                if inspect.isawaitable(wait_result):
                    await wait_result
            after = await _page_state(page)
            if _url_changed(before.url, after.url):
                effect = dict(result.get("effect") or {})
                effect.update(
                    {
                        "type": "navigate",
                        "url": after.url,
                        "auto_completed": True,
                        "source": "output_url",
                    }
                )
                return {**result, "effect": effect}

        return {
            **result,
            "success": False,
            "error": "Expected navigation effect, but the page URL did not change and no target URL was available.",
        }

    if expected_effect in {"click", "fill"}:
        effect = result.get("effect")
        if isinstance(effect, dict) and effect.get("action_performed"):
            return result
        action_type = str(plan.get("action_type") or "").strip().lower()
        if action_type == expected_effect:
            return {**result, "effect": {"type": expected_effect, "action_performed": True}}
        return {
            **result,
            "success": False,
            "error": f"Expected {expected_effect} effect, but no browser action evidence was produced.",
        }

    return result


def _expected_effect(plan: Dict[str, Any], instruction: str) -> str:
    explicit = _normalize_expected_effect(plan.get("expected_effect") or plan.get("effect"))
    if explicit != "extract":
        return explicit

    action_type = str(plan.get("action_type") or "").strip().lower()
    if action_type == "goto":
        return "navigate"
    if action_type in {"click", "fill"}:
        return action_type

    text = str(instruction or "").strip().lower()
    if _contains_any(text, ("打开", "进入", "跳转", "访问", "open", "go to", "goto", "navigate", "visit")):
        return "navigate"
    if _contains_any(text, ("点击", "click", "press")):
        return "click"
    if _contains_any(text, ("填写", "填入", "输入", "fill", "type into", "enter ")):
        return "fill"
    return explicit


def _normalize_expected_effect(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"extract", "navigate", "click", "fill", "mixed", "none"} else "extract"


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


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
    if host in {"api.github.com"}:
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
        for key in ("target_url", "url", "href", "repo_url", "value"):
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


async def _safe_page_snapshot(page: Any) -> Dict[str, Any]:
    try:
        return await build_page_snapshot(page, build_frame_path)
    except Exception:
        return {"url": getattr(page, "url", ""), "title": "", "frames": []}


def _compact_snapshot(snapshot: Dict[str, Any], instruction: str, limit: int = 80) -> Dict[str, Any]:
    try:
        compact_snapshot = compact_recording_snapshot(snapshot, instruction)
        if isinstance(compact_snapshot, dict):
            return compact_snapshot
    except Exception:
        pass

    compact_frames = []
    for frame in list(snapshot.get("frames") or [])[:5]:
        nodes = []
        for node in list(frame.get("elements") or [])[:limit]:
            nodes.append(
                {
                    "index": node.get("index"),
                    "tag": node.get("tag"),
                    "role": node.get("role"),
                    "name": node.get("name"),
                    "text": node.get("text"),
                    "href": node.get("href"),
                }
            )
        compact_frames.append(
            {
                "frame_hint": frame.get("frame_hint"),
                "url": frame.get("url"),
                "elements": nodes,
                "collections": frame.get("collections", [])[:10],
            }
        )
    return {
        "url": snapshot.get("url"),
        "title": snapshot.get("title"),
        "frames": compact_frames,
    }


def _write_recording_snapshot_debug(
    stage: str,
    *,
    instruction: str,
    page_state: Dict[str, Any],
    raw_snapshot: Dict[str, Any],
    compact_snapshot: Dict[str, Any],
    runtime_results: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    debug_dir = _resolve_recording_snapshot_debug_dir()
    if not debug_dir:
        return

    try:
        target_dir = _resolve_recording_snapshot_debug_path(debug_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"recording-snapshot-{timestamp}-{stage}-{uuid4().hex[:8]}.json"
        payload: Dict[str, Any] = {
            "stage": stage,
            "instruction": instruction,
            "page": page_state,
            "raw_snapshot": raw_snapshot,
            "compact_snapshot": compact_snapshot,
            "runtime_results": runtime_results,
        }
        if extra:
            payload.update(extra)
        (target_dir / filename).write_text(
            json.dumps(_safe_jsonable(payload), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        return


def _resolve_recording_snapshot_debug_dir() -> str:
    debug_dir = str(os.environ.get("RPA_RECORDING_DEBUG_SNAPSHOT_DIR") or "").strip()
    if debug_dir:
        return debug_dir

    try:
        from backend.config import settings

        return str(getattr(settings, "rpa_recording_debug_snapshot_dir", "") or "").strip()
    except Exception:
        return ""


def _resolve_recording_snapshot_debug_path(debug_dir: str) -> Path:
    path = Path(str(debug_dir or "").strip()).expanduser()
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[3] / path


def _safe_jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)

