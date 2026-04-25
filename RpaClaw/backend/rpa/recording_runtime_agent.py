from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import json
import linecache
import logging
import os
import re
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from pydantic import BaseModel, Field

from .assistant_runtime import build_page_snapshot
from .frame_selectors import build_frame_path
from .snapshot_compression import compact_recording_snapshot
from .trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPALocatorStabilityCandidate,
    RPALocatorStabilityMetadata,
    RPAPageState,
    RPATraceDiagnostic,
    RPATraceType,
)


logger = logging.getLogger(__name__)


_GENERATED_CODE_FILENAME = "<recording_runtime_agent>"
_RANDOM_LIKE_ATTR_RE = re.compile(r"(?i)(?:[a-z]+[-_])?[a-z0-9]{6,}[a-z][a-z0-9]*")
_DOWNLOAD_EVENT_DRAIN_TIMEOUT_S = 0.5


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
- 结果返回规则：
  - `results` 是普通 Python dict，只包含之前已成功步骤的输出结果。
  - 可以从 `results` 读取历史结果，用于跨步骤引用、整合、过滤、改写或汇总。
  - 不要在 `run()` 内原地修改 `results`，也不要把当前步骤输出直接写入 `results`。
  - 如果需要基于已有结果产生新结果，应读取 `results`，使用局部变量构造新的 Python 值，并通过 `return` 返回该新值。
  - 禁止调用 `results.set(...)`、`results.write(...)`、`results.update(...)` 来保存当前步骤结果。
  - 禁止通过 `results[...] = ...` 保存当前步骤结果。
  - 当前步骤产生的数据只能通过 `return` 从 `run(page, results)` 返回。
  - `output_key` 只是给后置 trace compiler 使用的元数据，不要在生成代码中根据 `output_key` 实现结果存储。
  - 最终 `_results[output_key] = _result` 由 skill 编译阶段自动生成，录制阶段代码不要实现这件事。
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
- Structured snapshot views:
  - For table/list/grid tasks, inspect `snapshot.table_views` before generic `expanded_regions`.
  - `table_views[].columns` describes column ids, headers, and inferred roles.
  - `table_views[].rows[].cells` describes row-local cell text and row-local actions.
  - For ordinal table tasks, prefer row-relative and column-relative Playwright locators.
  - Do not use observed row text as the primary selector when the instruction is ordinal.
  - For detail extraction, inspect `snapshot.detail_views` before scanning generic text or tables.
  - `detail_views[].fields` preserves label, value, data_prop, required, visible, and value_kind.
  - Treat hidden fields as diagnostic unless the user explicitly asks for hidden/default/internal values.
- Snapshot 结构契约：
  - `evidence` 是页面事实，用于理解当前区域的文本、字段、表头、样例行或可操作项。
  - `locator_hints`、`locator`、`label_locator`、`value_locator`、`actions[].locator` 是可执行定位线索，生成 Playwright 代码时应优先使用这些字段。
  - `ref`、`internal_ref`、`region_id`、`container_id`、`node_id` 是系统内部引用，只用于诊断和回溯 snapshot，不是 DOM id、CSS selector 或 Playwright locator。
  - 不要把内部引用改写成 `#...`、`[id=...]` 或其他 selector。
  - 对表格提取任务，优先使用 `locator_hints`、可见表头、标题文本或角色语义来定位表格，不要使用内部引用作为 selector。
- Do not include a separate done-check.
- If extracting data, return structured JSON-serializable Python values.
- For extract-only commands, do not return null/empty output unless the user explicitly allows empty results.
- Set allow_empty_output=true only when the user explicitly says no result, empty list, or empty output is acceptable.
- During repair, treat raw error logs and current page facts as authoritative. Any failure_analysis.hint is advisory only.
- 修复规则：
  - 修复时必须优先参考原始错误日志、异常类型、traceback 行号和当前页面事实。
  - 修复前先判断失败类型：如果失败来自 Python 代码错误，应优先修复对应代码行；如果失败来自页面状态、定位器、空数据或目标区域选择错误，再调整 selector 或取数策略。
  - 修复时应保持用户原始目标不变，不要把一次局部代码错误扩展成无关的页面流程重写。
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
        debug_context: Optional[Dict[str, Any]] = None,
    ) -> RecordingAgentResult:
        runtime_results = runtime_results if runtime_results is not None else {}
        debug_context = dict(debug_context or {})
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
            debug_context=debug_context,
        )

        first_plan = _build_table_ordinal_overlay_plan(instruction, snapshot)
        if not first_plan:
            first_plan = _build_ordinal_overlay_plan(instruction, snapshot)
        if not first_plan:
            first_plan = await self.planner(payload)
        first_result = await self.executor(page, first_plan, runtime_results)
        first_result = await _ensure_expected_effect(
            page=page,
            instruction=instruction,
            plan=first_plan,
            result=first_result,
            before=before,
        )
        _write_recording_attempt_debug(
            "initial_attempt",
            instruction=instruction,
            page_state=before.model_dump(mode="json"),
            plan=first_plan,
            execution_result=first_result,
            failure_analysis=None if first_result.get("success") else _known_failure_analysis(first_result.get("error")),
            debug_context=debug_context,
        )
        if first_result.get("success"):
            trace = await self._accepted_trace(
                page,
                instruction,
                first_plan,
                first_result,
                before,
                repair_attempted=False,
                snapshot=snapshot,
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
        first_error_type = str(first_result.get("error_type") or "").strip()
        first_traceback = str(first_result.get("traceback") or "").strip()
        first_failure_analysis = _classify_recording_failure(first_error)
        first_known_failure_analysis = _known_failure_analysis(first_error)
        logger.warning(
            "[RPA] recording command first attempt failed type=%s error=%s",
            first_failure_analysis.get("type", "unknown"),
            first_error[:300],
        )
        repair_snapshot_extra = {
            "failed_plan": _safe_jsonable(first_plan),
            "error": first_error,
        }
        if first_error_type:
            repair_snapshot_extra["error_type"] = first_error_type
        if first_traceback:
            repair_snapshot_extra["traceback"] = first_traceback
        if first_known_failure_analysis:
            repair_snapshot_extra["failure_analysis"] = first_known_failure_analysis
        _write_recording_snapshot_debug(
            "repair",
            instruction=instruction,
            page_state=failed_page.model_dump(mode="json"),
            raw_snapshot=failed_snapshot,
            compact_snapshot=compact_failed_snapshot,
            runtime_results=runtime_results,
            debug_context=debug_context,
            extra=repair_snapshot_extra,
        )
        diagnostic_raw = {
            "plan": _safe_jsonable(first_plan),
            "result": _safe_jsonable(first_result),
            "page_after_failure": failed_page.model_dump(mode="json"),
            "snapshot_after_failure": _safe_jsonable(compact_failed_snapshot),
        }
        if first_error_type:
            diagnostic_raw["error_type"] = first_error_type
        if first_traceback:
            diagnostic_raw["traceback"] = first_traceback
        if first_known_failure_analysis:
            diagnostic_raw["failure_analysis"] = first_known_failure_analysis
        diagnostics = [
            RPATraceDiagnostic(
                source="ai",
                message=first_error,
                raw=diagnostic_raw,
            )
        ]

        repair_context = {
            "error": first_error,
            "failed_plan": first_plan,
            "page_after_failure": failed_page.model_dump(mode="json"),
            "snapshot_after_failure": compact_failed_snapshot,
        }
        if first_error_type:
            repair_context["error_type"] = first_error_type
        if first_traceback:
            repair_context["traceback"] = first_traceback
        if first_known_failure_analysis:
            repair_context["failure_analysis"] = first_known_failure_analysis
        repair_payload = {
            **payload,
            "repair": repair_context,
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
        _write_recording_attempt_debug(
            "repair_attempt",
            instruction=instruction,
            page_state=failed_page.model_dump(mode="json"),
            plan=repair_plan,
            execution_result=repair_result,
            failure_analysis=None if repair_result.get("success") else _known_failure_analysis(repair_result.get("error")),
            debug_context=debug_context,
        )
        if repair_result.get("success"):
            trace = await self._accepted_trace(
                page,
                instruction,
                repair_plan,
                repair_result,
                before,
                repair_attempted=True,
                snapshot=failed_snapshot,
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
        repair_error_type = str(repair_result.get("error_type") or "").strip()
        repair_traceback = str(repair_result.get("traceback") or "").strip()
        repair_failure_analysis = _classify_recording_failure(repair_error)
        repair_known_failure_analysis = _known_failure_analysis(repair_error)
        logger.warning(
            "[RPA] recording command repair failed type=%s error=%s",
            repair_failure_analysis.get("type", "unknown"),
            repair_error[:300],
        )
        repair_diagnostic_raw = {
            "plan": _safe_jsonable(repair_plan),
            "result": _safe_jsonable(repair_result),
        }
        if repair_error_type:
            repair_diagnostic_raw["error_type"] = repair_error_type
        if repair_traceback:
            repair_diagnostic_raw["traceback"] = repair_traceback
        if repair_known_failure_analysis:
            repair_diagnostic_raw["failure_analysis"] = repair_known_failure_analysis
        diagnostics.append(
            RPATraceDiagnostic(
                source="ai",
                message=repair_error,
                raw=repair_diagnostic_raw,
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
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> RPAAcceptedTrace:
        after = await _page_state(page)
        output = result.get("output")
        output_key = _normalize_result_key(plan.get("output_key"))
        locator_stability = _build_locator_stability_metadata(plan, snapshot or {})
        return RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction=instruction,
            description=str(plan.get("description") or instruction),
            before_page=before,
            after_page=after,
            signals=dict(result.get("signals") or {}),
            output_key=output_key,
            output=output,
            ai_execution=RPAAIExecution(
                language="python",
                code=str(plan.get("code") or ""),
                output=output,
                error=result.get("error"),
                repair_attempted=repair_attempted,
            ),
            locator_stability=locator_stability,
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
            _cache_generated_code_for_traceback(code)
            exec(compile(code, _GENERATED_CODE_FILENAME, "exec"), namespace, namespace)
            runner = namespace.get("run")
            if not callable(runner):
                return {"success": False, "error": "No run(page, results) function defined", "output": ""}
            navigation_history: List[str] = []
            download_events: List[Dict[str, Any]] = []
            download_observed = asyncio.get_running_loop().create_future()
            original_goto = getattr(page, "goto", None)
            goto_wrapped = False
            download_handler_attached = False

            def on_download(download: Any) -> None:
                download_events.append(
                    {
                        "filename": str(getattr(download, "suggested_filename", "") or ""),
                        "url": str(getattr(page, "url", "") or ""),
                    }
                )
                if not download_observed.done():
                    download_observed.set_result(True)

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

            page_on = getattr(page, "on", None)
            if callable(page_on):
                try:
                    page_on("download", on_download)
                    download_handler_attached = True
                except Exception:
                    download_handler_attached = False

            try:
                output = runner(page, runtime_results)
                if inspect.isawaitable(output):
                    output = await output
                if download_handler_attached:
                    await asyncio.sleep(0)
                    if not download_events and _should_drain_download_events(plan, code):
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(download_observed),
                                timeout=_DOWNLOAD_EVENT_DRAIN_TIMEOUT_S,
                            )
                        except asyncio.TimeoutError:
                            pass
            finally:
                if download_handler_attached:
                    remover = getattr(page, "remove_listener", None) or getattr(page, "off", None)
                    if callable(remover):
                        try:
                            remover("download", on_download)
                        except Exception:
                            pass
                if goto_wrapped:
                    try:
                        setattr(page, "goto", original_goto)
                    except Exception:
                        pass

            response = {"success": True, "error": None, "output": output}
            if navigation_history:
                response["navigation_history"] = navigation_history
            if download_events:
                download_signal = dict(download_events[0])
                download_signal["count"] = len(download_events)
                if len(download_events) > 1:
                    download_signal["files"] = list(download_events)
                response["signals"] = {"download": download_signal}
                response["effect"] = {"type": "download", "action_performed": True}
            return response
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": _format_exception_for_repair(exc),
                "output": "",
            }


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
        action_selector = _table_column_action_selector(table, index, column)
        if not action_selector:
            return None
        code = (
            "async def run(page, results):\n"
            f"{rows_setup}"
            f"    _row = _rows.nth({index})\n"
            f"    await _row.locator({action_selector!r}).click()\n"
            "    return {'action_performed': True}"
        )
        return {
            "description": "Click table row column action",
            "action_type": "run_python",
            "expected_effect": "none",
            "output_key": "table_row_action",
            "code": code,
            "table_ordinal_overlay": True,
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
    return max(tables, key=lambda table: _score_table_view_for_instruction(table, instruction))


def _score_table_view_for_instruction(table: Dict[str, Any], instruction: str) -> int:
    text = str(instruction or "").lower()
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
    return score


def _select_table_column(table: Dict[str, Any], instruction: str) -> Optional[Dict[str, Any]]:
    text = str(instruction or "").lower()
    columns = list(table.get("columns") or [])
    scored: List[tuple[int, Dict[str, Any]]] = []
    for column in columns:
        header = str(column.get("header") or "").lower()
        role = str(column.get("role") or "").lower()
        score = 0
        if header and header in text:
            score += 6
        if any(token and token in text for token in header.replace("_", " ").split()):
            score += 3
        if role and role in text:
            score += 3
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
    row_selector = _table_row_selector(table)
    if title:
        return (
            f"    _heading = page.get_by_text({title!r}, exact=True).first\n"
            "    if await _heading.count():\n"
            "        _rows = _heading.locator(\"xpath=following::table[.//tbody/tr][1]//tbody/tr\")\n"
            "    else:\n"
            f"        _rows = page.locator({row_selector!r})\n"
        )
    return f"    _rows = page.locator({row_selector!r})\n"


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
    rows = list(table.get("rows") or [])
    if index >= len(rows):
        return ""
    for cell in rows[index].get("cells") or []:
        if column_id and str(cell.get("column_id") or "") != column_id:
            continue
        actions = list(cell.get("actions") or cell.get("row_local_actions") or [])
        for action in actions:
            locator = action.get("locator") if isinstance(action, dict) else {}
            if isinstance(locator, dict) and locator.get("scope") == "row" and locator.get("value"):
                return str(locator.get("value"))
    if column_id:
        return f"td[data-colid={column_id!r}] a, td[data-colid={column_id!r}] button"
    return ""


def _build_ordinal_overlay_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    intent = _detect_ordinal_intent(instruction)
    if not intent:
        return None

    action = _detect_ordinal_action(instruction)
    if not action:
        return None

    collection = _extract_repeated_candidate_collection(snapshot)
    if not collection:
        return None

    items = list(collection.get("items") or [])
    selector = str(collection.get("primary_selector") or "")
    if not selector or not items:
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
        return _ordinal_click_plan(secondary_selector, index, description="Click ordinal item action")

    if action == "click_primary":
        return _ordinal_click_plan(selector, index, description="Click ordinal item")

    return None


def _detect_ordinal_intent(instruction: str) -> Optional[Dict[str, int | str]]:
    text = str(instruction or "").strip().lower()
    if not text:
        return None

    first_n = re.search(r"\bfirst\s+(\d+)\b", text) or re.search(r"前\s*([0-9一二三四五六七八九十两]+)", text)
    if first_n:
        limit = _parse_ordinal_number(first_n.group(1))
        if limit is not None:
            return {"kind": "first_n", "limit": limit}

    nth = re.search(r"\b(?:number|item|row)\s+(\d+)\b", text) or re.search(r"第\s*([0-9一二三四五六七八九十两]+)\s*(?:个|项|条|行)?", text)
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


def _parse_ordinal_number(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
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
    text = str(instruction or "").strip().lower()
    semantic_terms = (
        "most related",
        "best match",
        "highest",
        "most relevant",
        "compare",
        "summarize",
        "summary",
        "最相关",
        "最高",
        "最多",
        "最佳",
        "比较",
        "总结",
    )
    if any(term in text for term in semantic_terms):
        return ""
    if any(term in text for term in ("download", "下载")):
        return "click_secondary"
    if any(term in text for term in ("click", "open", "visit", "go to", "点击", "打开", "进入")):
        return "click_primary"
    if any(term in text for term in ("name", "title", "text", "名称", "名字", "标题", "获取", "抓取", "提取")):
        return "extract_title"
    return ""


def _extract_repeated_candidate_collection(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for node in snapshot.get("actionable_nodes") or []:
        selector = str(node.get("collection_item_selector") or "").strip()
        count = int(node.get("collection_item_count") or 0)
        label = _node_label(node)
        if not selector or count < 2 or not label:
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
    for frame in snapshot.get("frames") or []:
        collections = list(frame.get("collections") or [])
        for collection in collections:
            if str(collection.get("kind") or "") != "repeated_items":
                continue
            selector = _collection_item_css_selector(collection)
            if not selector:
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


def _ordinal_click_plan(selector: str, index: int, *, description: str) -> Dict[str, Any]:
    code = (
        "async def run(page, results):\n"
        f"    await page.locator({selector!r}).nth({index}).click()\n"
        "    return {'action_performed': True}"
    )
    return {
        "description": description,
        "action_type": "run_python",
        "expected_effect": "none",
        "output_key": "ordinal_item_action",
        "code": code,
        "ordinal_overlay": True,
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
    return any(token in text for token in ("download", "下载", "star", "fork", "signed in"))


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


def _known_failure_analysis(error: Any) -> Optional[Dict[str, str]]:
    analysis = _classify_recording_failure(error)
    return analysis if analysis.get("type") != "unknown" else None


def _cache_generated_code_for_traceback(code: str) -> None:
    lines = [line if line.endswith("\n") else f"{line}\n" for line in code.splitlines()]
    linecache.cache[_GENERATED_CODE_FILENAME] = (len(code), None, lines, _GENERATED_CODE_FILENAME)


def _format_exception_for_repair(exc: BaseException) -> str:
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    return formatted or str(exc)


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
        if expected_effect == "click" and action_type == "run_python":
            after = await _page_state(page)
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


def _extract_primary_locator_from_code(code: str) -> Dict[str, Any]:
    match = re.search(r"page\.locator\((?P<quote>['\"])(?P<selector>.+?)(?P=quote)\)", code or "")
    if not match:
        return {}
    return {"method": "css", "value": match.group("selector")}


def _extract_unstable_signals(locator: Dict[str, Any]) -> List[Dict[str, Any]]:
    if locator.get("method") != "css":
        return []
    selector = str(locator.get("value") or "")
    signals: List[Dict[str, Any]] = []
    patterns = {
        "data-testid": re.compile(r"""\[\s*data-testid\s*=\s*["']([^"']+)["']\s*\]"""),
        "data-test": re.compile(r"""\[\s*data-test\s*=\s*["']([^"']+)["']\s*\]"""),
        "id": re.compile(r"""#([A-Za-z0-9_-]+)"""),
        "class": re.compile(r"""\.([A-Za-z0-9_-]+)"""),
    }
    for attribute, pattern in patterns.items():
        for match in pattern.finditer(selector):
            value = match.group(1)
            if _RANDOM_LIKE_ATTR_RE.search(value):
                signals.append({"attribute": attribute, "value": value})
    return signals


def _build_anchor_candidate(anchor_title: str, role: str, name: str) -> RPALocatorStabilityCandidate:
    return RPALocatorStabilityCandidate(
        locator={
            "method": "nested",
            "parent": {"method": "text", "value": anchor_title},
            "child": {"method": "role", "role": role, "name": name},
        },
        source="snapshot_anchor_scope",
        confidence="high",
    )


def _build_locator_stability_metadata(
    plan: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Optional[RPALocatorStabilityMetadata]:
    primary_locator = _extract_primary_locator_from_code(str(plan.get("code") or ""))
    if not primary_locator:
        return None

    unstable_signals = _extract_unstable_signals(primary_locator)
    if not unstable_signals:
        return None

    fallback_metadata = RPALocatorStabilityMetadata(
        primary_locator=primary_locator,
        unstable_signals=unstable_signals,
    )

    for node in snapshot.get("actionable_nodes") or []:
        locator = node.get("locator") or {}
        role = str(node.get("role") or locator.get("role") or "").strip()
        name = str(node.get("name") or locator.get("name") or node.get("text") or "").strip()
        if not role or not name:
            continue
        anchor = str((node.get("container") or {}).get("title") or "").strip()
        alternate_locators = [
            RPALocatorStabilityCandidate(
                locator={"method": "role", "role": role, "name": name},
                source="snapshot_actionable_node",
                confidence="high",
            )
        ]
        if anchor:
            alternate_locators.append(_build_anchor_candidate(anchor, role, name))
        return RPALocatorStabilityMetadata(
            primary_locator=primary_locator,
            stable_self_signals={"role": role, "name": name},
            stable_anchor_signals={"title": anchor} if anchor else {},
            unstable_signals=unstable_signals,
            alternate_locators=alternate_locators,
        )
    return fallback_metadata


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
    debug_context: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    debug_dir = _resolve_recording_snapshot_debug_dir()
    if not debug_dir:
        return

    try:
        debug_context = dict(debug_context or {})
        target_dir = _resolve_recording_snapshot_debug_path(debug_dir, debug_context=debug_context)
        target_dir.mkdir(parents=True, exist_ok=True)
        sequence = _next_debug_sequence(target_dir)
        filename = _debug_filename(
            sequence=sequence,
            stage=stage,
            kind="snapshot",
            label=instruction,
            extension="json",
        )
        payload: Dict[str, Any] = {
            "stage": stage,
            "debug_context": debug_context,
            "instruction": instruction,
            "page": page_state,
            "raw_snapshot": raw_snapshot,
            "compact_snapshot": compact_snapshot,
            "snapshot_metrics": _build_snapshot_debug_metrics(raw_snapshot, compact_snapshot),
            "snapshot_comparison": _compare_instruction_snapshot_presence(instruction, raw_snapshot, compact_snapshot),
            "runtime_results": runtime_results,
        }
        if extra:
            payload.update(extra)
        (target_dir / filename).write_text(
            json.dumps(_safe_jsonable(payload), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("[RPA-DIAG] snapshot dump written stage=%s path=%s", stage, target_dir / filename)
    except Exception:
        logger.warning("[RPA-DIAG] snapshot dump failed stage=%s", stage, exc_info=True)
        return


def _write_recording_attempt_debug(
    stage: str,
    *,
    instruction: str,
    page_state: Dict[str, Any],
    plan: Dict[str, Any],
    execution_result: Dict[str, Any],
    failure_analysis: Optional[Dict[str, Any]] = None,
    debug_context: Optional[Dict[str, Any]] = None,
) -> None:
    debug_dir = _resolve_recording_snapshot_debug_dir()
    if not debug_dir:
        return

    try:
        debug_context = dict(debug_context or {})
        target_dir = _resolve_recording_snapshot_debug_path(debug_dir, debug_context=debug_context)
        target_dir.mkdir(parents=True, exist_ok=True)
        sequence = _next_debug_sequence(target_dir)
        label = str(plan.get("description") or instruction or stage)
        json_path = target_dir / _debug_filename(
            sequence=sequence,
            stage=stage,
            kind="attempt",
            label=label,
            extension="json",
        )
        code = str(plan.get("code") or "")
        payload: Dict[str, Any] = {
            "stage": stage,
            "debug_context": debug_context,
            "instruction": instruction,
            "page": page_state,
            "plan": _safe_jsonable(plan),
            "generated_code": code,
            "execution_result": _safe_jsonable(execution_result),
        }
        if failure_analysis:
            payload["failure_analysis"] = failure_analysis
        if code:
            code_path = target_dir / _debug_filename(
                sequence=sequence,
                stage=stage,
                kind="code",
                label=label,
                extension="py",
            )
            code_path.write_text(code, encoding="utf-8")
            payload["generated_code_path"] = str(code_path)
        json_path.write_text(
            json.dumps(_safe_jsonable(payload), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("[RPA-DIAG] attempt dump written stage=%s path=%s", stage, json_path)
    except Exception:
        logger.warning("[RPA-DIAG] attempt dump failed stage=%s", stage, exc_info=True)
        return


def _build_snapshot_debug_metrics(raw_snapshot: Dict[str, Any], compact_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    content_nodes = list(raw_snapshot.get("content_nodes") or [])
    actionable_nodes = list(raw_snapshot.get("actionable_nodes") or [])
    containers = list(raw_snapshot.get("containers") or [])
    expanded_regions = list(compact_snapshot.get("expanded_regions") or [])
    sampled_regions = list(compact_snapshot.get("sampled_regions") or [])
    catalogue = list(compact_snapshot.get("region_catalogue") or [])
    table_views = list(compact_snapshot.get("table_views") or [])
    detail_views = list(compact_snapshot.get("detail_views") or [])
    return {
        "raw_snapshot": {
            "frame_count": len(raw_snapshot.get("frames") or []),
            "content_node_count": len(content_nodes),
            "actionable_node_count": len(actionable_nodes),
            "container_count": len(containers),
            "content_node_limit_hit": len(content_nodes) >= 160,
            "actionable_node_limit_hit": len(actionable_nodes) >= 120,
            "semantic_kind_counts": _count_by_key(content_nodes, "semantic_kind"),
            "container_kind_counts": _count_by_key(containers, "container_kind"),
        },
        "compact_snapshot": {
            "mode": compact_snapshot.get("mode", ""),
            "char_size": len(json.dumps(_safe_jsonable(compact_snapshot), ensure_ascii=False, sort_keys=True, default=str)),
            "expanded_region_count": len(expanded_regions),
            "sampled_region_count": len(sampled_regions),
            "catalogue_region_count": len(catalogue),
            "table_view_count": len(table_views),
            "detail_view_count": len(detail_views),
            "expanded_region_titles": _region_titles(expanded_regions),
            "sampled_region_titles": _region_titles(sampled_regions),
            "table_view_titles": _region_titles(table_views),
            "detail_view_titles": [
                str(view.get("section_title") or view.get("title") or "").strip()[:120]
                for view in detail_views[:20]
                if str(view.get("section_title") or view.get("title") or "").strip()
            ],
            "region_kind_counts": _count_by_key(expanded_regions + sampled_regions + catalogue, "kind"),
        },
    }


def _compare_instruction_snapshot_presence(
    instruction: str,
    raw_snapshot: Dict[str, Any],
    compact_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    terms = _diagnostic_instruction_terms(instruction)
    if not terms:
        return {"classification": "no_instruction_terms", "terms": []}

    raw_text = _diagnostic_text_blob(raw_snapshot)
    compact_text = _diagnostic_text_blob(compact_snapshot)
    raw_hits = [term for term in terms if term in raw_text]
    compact_hits = [term for term in terms if term in compact_text]
    if raw_hits and compact_hits:
        classification = "present_in_both"
    elif raw_hits and not compact_hits:
        classification = "missing_in_compact"
    elif not raw_hits:
        classification = "missing_in_raw"
    else:
        classification = "present_in_compact_only"
    return {
        "classification": classification,
        "terms": terms,
        "raw_hits": raw_hits,
        "compact_hits": compact_hits,
    }


def _count_by_key(items: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _region_titles(regions: List[Dict[str, Any]]) -> List[str]:
    titles: List[str] = []
    for region in regions[:20]:
        title = str(region.get("title") or region.get("summary") or region.get("region_id") or "").strip()
        if title:
            titles.append(title[:120])
    return titles


def _diagnostic_instruction_terms(instruction: str) -> List[str]:
    text = _normalize_debug_text(instruction)
    terms: List[str] = []
    for match in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text):
        terms.append(match)
    compact_cjk = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    if len(compact_cjk) >= 4:
        terms.append(compact_cjk)
    for index in range(max(len(compact_cjk) - 1, 0)):
        gram = compact_cjk[index : index + 2]
        if gram:
            terms.append(gram)
    seen: set[str] = set()
    deduped: List[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped[:30]


def _diagnostic_text_blob(value: Any) -> str:
    return _normalize_debug_text(json.dumps(_safe_jsonable(value), ensure_ascii=False, default=str))


def _normalize_debug_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _resolve_recording_snapshot_debug_dir() -> str:
    debug_dir = str(os.environ.get("RPA_RECORDING_DEBUG_SNAPSHOT_DIR") or "").strip()
    if debug_dir:
        return debug_dir

    try:
        from backend.config import settings

        return str(getattr(settings, "rpa_recording_debug_snapshot_dir", "") or "").strip()
    except Exception:
        return ""


def _resolve_recording_snapshot_debug_path(debug_dir: str, *, debug_context: Optional[Dict[str, Any]] = None) -> Path:
    path = Path(str(debug_dir or "").strip()).expanduser()
    resolved = path if path.is_absolute() else Path(__file__).resolve().parents[3] / path
    session_id = str((debug_context or {}).get("session_id") or "").strip()
    if not session_id:
        return resolved
    return resolved / _safe_debug_path_segment(session_id)


def _next_debug_sequence(target_dir: Path) -> int:
    max_seen = 0
    for pattern in ("*-snapshot-*.json", "*-attempt-*.json", "*-code-*.py", "snapshot-*.json", "attempt-*.json", "code-*.py"):
        for path in target_dir.glob(pattern):
            match = re.match(r"^(?:snapshot|attempt|code)-(\d+)-|^(\d+)-", path.name)
            if match:
                max_seen = max(max_seen, int(match.group(1) or match.group(2)))
    return max_seen + 1


def _debug_filename(*, sequence: int, stage: str, kind: str, label: str, extension: str) -> str:
    stage_segment = _safe_debug_path_segment(stage, max_length=40, allow_unicode=False)
    label_segment = _safe_debug_path_segment(label, max_length=48, allow_unicode=True)
    return f"{sequence:03d}-{stage_segment}-{kind}-{label_segment}.{extension}"


def _safe_debug_path_segment(value: str, *, max_length: int = 120, allow_unicode: bool = False) -> str:
    pattern = r"[^\w\u4e00-\u9fff_.-]+" if allow_unicode else r"[^a-zA-Z0-9_.-]+"
    segment = re.sub(pattern, "_", str(value or "").strip(), flags=re.UNICODE)
    segment = segment.strip("._")
    return segment[:max_length] or "unknown"


def _safe_jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)

