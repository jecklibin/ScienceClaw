from __future__ import annotations

import asyncio
import ast
from datetime import datetime, timezone
import inspect
import json
import linecache
import logging
import os
import re
import time
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import unquote, urlparse
from uuid import uuid4

from pydantic import BaseModel, Field

from .assistant_runtime import build_page_snapshot
from .frame_selectors import build_frame_path
from .recording_effects import (
    _ensure_expected_effect,
    _expected_effect,
    _merge_runtime_ai_signal,
    _normalize_expected_effect,
    _should_drain_download_events,
)
from .recording_contracts import normalize_terminal_contract
from .recording_download_preplans import build_download_preplanned_plan
from .recording_modal_preplans import build_modal_form_preplanned_plan
from .recording_ordinal_preplans import (
    build_preplanned_plan as build_ordinal_preplanned_plan,
    _build_ordinal_overlay_plan,
    _build_table_ordinal_overlay_plan,
)
from .recording_search_preplans import build_search_preplanned_plan
from .recording_repair import (
    _classify_recording_failure,
    _known_failure_analysis,
    _repair_guidance_for_failure,
)
from .recording_terminal_recovery import (
    current_snapshot_terminal_postcondition,
    recover_failed_side_effect_from_snapshot_diff,
    snapshot_diff_terminal_postcondition,
)
from .recording_verifier import capture_browser_evidence
from .playwright_code_normalizer import (
    normalize_generated_playwright_code,
)
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
_RECORDING_PLANNER_MIN_OUTPUT_TOKENS = 8192


def _env_positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


_RECORDING_LLM_TIMEOUT_S = _env_positive_float("RPA_RECORDING_LLM_TIMEOUT_SECONDS", 45.0)


async def _ainvoke_model_with_recording_timeout(model: Any, messages: List[Any]) -> Any:
    try:
        return await asyncio.wait_for(model.ainvoke(messages), timeout=_RECORDING_LLM_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"recording LLM call exceeded {_RECORDING_LLM_TIMEOUT_S:g}s") from exc


_SEMANTIC_TERMINAL_JUDGE_PROMPT = """You are a strict verifier for one RPA recording step.
Return JSON only:
{"passed": false, "evidence": [{"type": "url_changed|download_created|toast_visible|feedback_visible|row_exists|row_absent|row_status_changed|field_value_equals|postcondition|empty_result", "source": "snapshot|browser|download|page", "summary": "short observed fact"}], "reason": "short reason"}
Rules:
- Pass only when the current page facts or browser/download signals explicitly show the terminal_contract is satisfied.
- Do not infer success from the intended code, a bare click/fill, or output fields such as action_performed.
- If the page still shows validation/error text, or evidence is ambiguous, return passed=false.
"""

_INSTRUCTION_COMPLETION_JUDGE_PROMPT = """You are a strict completion verifier for one RPA recording command.
Return JSON only:
{"passed": false, "missing_requirements": ["short unmet requirement"], "reason": "short reason"}
Rules:
- Compare the full instruction with the executed plan, output, before page, current page, and current snapshot.
- Pass only when every explicit action or data extraction requested by the instruction is complete.
- Do not require future SOP steps that are not in the instruction.
- If the plan only read or extracted data but the instruction also required a later browser action, state change, submission, download, or navigation, return passed=false.
- Use observed page facts and structured output only; do not infer completion from intended code or domain assumptions.
- Keep missing_requirements concise and structural, not business-specific.
"""


RECORDING_RUNTIME_SYSTEM_PROMPT = """You operate exactly one RPA recording command.
Return JSON only.
Schema:
{
  "description": "short user-facing action summary",
  "action_type": "run_python|extract_snapshot",
  "expected_effect": "extract|navigate|click|fill|mixed",
  "allow_empty_output": false,
  "output_key": "optional_ascii_snake_case_result_key",
  "code": "async def run(page, results): ...",
  "source": "detail_views",
  "section_title": "optional snapshot section title",
  "frame_path": "optional iframe selector chain for extract_snapshot",
  "fields": "optional structured fields for extract_snapshot",
  "input_bindings": {"param_name": {"source": "user_param|previous_result|literal", "default": "recorded sample value", "classification": "user_param|dynamic|literal"}},
  "output_bindings": {"field_name": {"path": "output.path"}},
  "postcondition": {"kind": "table_row_exists", "source": "observed", "table_headers": ["<observed column>"], "key": {"<observed id column>": "{{param_name}}"}, "expect": {"<observed status column>": "<observed terminal value>"}},
  "terminal_contract": {"required": false, "kind": "none|state_change|record_created|record_updated|record_removed|download_created|dialog_dismissed|empty_result", "success_evidence": [{"type": "url_changed|download_created|toast_visible|feedback_visible|row_exists|row_absent|row_status_changed|field_value_equals|postcondition|empty_result"}], "allow_semantic_judge": false},
  "preserve_runtime_ai": false,
  "semantic_intent": "none|semantic_candidate_selection"
}
Rules:
- Complete only the current user command, not the full SOP.
- Return action_type="run_python" unless a simple goto/click/fill action is clearly enough.
- expected_effect describes the browser-visible outcome required by the user's current command.
- Use expected_effect="navigate" when the user asks to open, go to, enter, visit, or navigate to a target.
- Use expected_effect="extract" when the user only asks to find, collect, summarize, or return data without opening it.
- Set preserve_runtime_ai=true only when replay must re-evaluate current page candidates semantically. When set, semantic_intent must be "semantic_candidate_selection".
- Do not set preserve_runtime_ai for a simple deterministic click/fill/goto where the recorded locator or value is the intended reusable behavior.
- If the user asks to filter/search and open a specific record, do not stop after the record is merely visible in a list/table. Click the row-local link/action or stable record locator, then confirm a detail page, detail panel, selected row expansion, or URL/detail-view change.
- If the requested data is already visible in snapshot.detail_views, prefer action_type="extract_snapshot" and expected_effect="extract" even when the instruction mentions opening or entering a detail page.
- When snapshot.modal_dialogs is non-empty, the active dialog is the current interaction scope. Continue inside that dialog instead of clicking background page controls to reopen it.
- When a prior click opens a dialog, do not assume the dialog's terminal button has the same label as the opener. Scope to the visible dialog and prefer dialog-local action/test-id/type evidence before a generic button label.
- If code is returned, it must define async def run(page, results).
- Use action_type="extract_snapshot" only when the requested extract-only data is already present in snapshot.detail_views fields.
- For extract_snapshot, return the relevant observed detail fields in the plan itself, including the detail view frame_path when present; do not generate Python code and do not reference `snapshot` inside `run()`.
- Use input_bindings for values that should vary at replay time. Keep literal UI labels, headers, button names, and fixed workflow labels out of input_bindings.
- Use postcondition only as a candidate replayable structural check that was observed from the current page or returned output; include source="observed". It must be anchored to input_bindings such as "{{param_name}}" and to real table/detail headers visible in snapshot evidence. Do not encode guessed status values, examples, or business-specific recovery rules.
- Use terminal_contract when the command must leave a durable browser-visible terminal state, create/update/remove a record, create a download, dismiss a dialog, or prove an expected empty result. success_evidence must name evidence types, not business-specific words. Bare action acknowledgements are not terminal evidence.
- Use output_bindings only to describe returned output paths; generated Python still returns the current step output normally.
- `snapshot` is planner-only evidence. Generated Python can access only `page` and `results`.
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
- Playwright Python Locator is lazy and not list-like. Do not slice or directly iterate a Locator; use `count()` with `nth(index)`, `first`, `all_inner_texts()`, or a short read-only DOM query when needed.
- Avoid page.evaluate unless the snippet is short, read-only, and necessary.
- Do not include shell, filesystem, network requests outside the current browser page, or infinite loops.
- For search-engine tasks, if the user's goal is to search/open results, prefer navigating to the results URL with an encoded query. If the user explicitly asks to fill a search box, first target visible, enabled, editable input candidates instead of filling hidden DOM matches.
- For in-page filter/search forms, fill only editable controls such as textbox/searchbox/combobox/input/textarea/contenteditable; do not fill buttons or submit controls even if their test id or text contains the query concept.
- Treat same-page filtering, sorting, modal submission, and table/list refreshes as expected_effect="mixed" or "extract" unless the user explicitly requires the browser URL to change.
- Do not leave the browser on API, JSON, raw, or other machine endpoints after an extract-only command.
- For extract-only commands, prefer user-facing pages and restore the most recent user-facing page after any temporary helper navigation.
- For extract-only commands, prefer snapshot.expanded_regions and snapshot.sampled_regions before broad DOM scans.
- When transferring data from one page to another, prefer structured snapshot.detail_views fields as the source of truth. Do not parse the whole body text with broad regular expressions when structured label/value fields are available.
- When a later form value must come from data read earlier in the command, store that source value in a local variable and reuse it for the fill. Do not substitute current user/menu/role text, guessed defaults, or UNKNOWN/placeholder values; if the source value is missing, raise before submitting the form.
- Use the region title, heading, or catalogue summary as context when it matches the requested area.
- If an expanded region is a label_value_group and the user asks for field names or values, keep extraction focused on that region or supporting locator evidence instead of scanning every table.
- Avoid treating tables as the default fallback for field extraction when a more relevant label_value_group is present.
- snapshot.region_catalogue is page context only.
- Structured snapshot views:
  - For table/list/grid tasks, inspect `snapshot.table_views` before generic `expanded_regions`.
  - `table_views[].columns` describes column ids, headers, and inferred roles.
  - `table_views[].rows[].cells` describes row-local cell text and row-local actions.
  - `table_views[].rows[].cells[].controls` describes editable controls inside a cell. For editable tables, map intended values to column headers and use the row-relative control locator before falling back to raw input order.
  - For ordinal table tasks, prefer row-relative and column-relative Playwright locators.
  - Do not use observed row text as the primary selector when the instruction is ordinal.
  - For detail extraction, inspect `snapshot.detail_views` before scanning generic text or tables.
  - `detail_views[].fields` preserves label, value, data_prop, required, visible, and value_kind.
  - Treat hidden fields as diagnostic unless the user explicitly asks for hidden/default/internal values.
  - For form fill/edit tasks, inspect `snapshot.form_views` before generic text, tables, or summary regions.
  - `form_views[].fields[].control.locator` is executable locator evidence for fillable controls.
  - Do not turn summary text into placeholder, label, name, or CSS selectors unless a form/detail/actionable locator explicitly exposes that attribute.
- Snapshot 结构契约：
  - `evidence` 是页面事实，用于理解当前区域的文本、字段、表头、样例行或可操作项。
  - `locator_hints`、`locator`、`label_locator`、`value_locator`、`actions[].locator` 是可执行定位线索，生成 Playwright 代码时应优先使用这些字段。
  - `ref`、`internal_ref`、`region_id`、`container_id`、`node_id` 是系统内部引用，只用于诊断和回溯 snapshot，不是 DOM id、CSS selector 或 Playwright locator。
  - 不要把内部引用改写成 `#...`、`[id=...]` 或其他 selector。
  - 对表格提取任务，优先使用 `locator_hints`、可见表头、标题文本或角色语义来定位表格，不要使用内部引用作为 selector。
- Do not include a separate done-check.
- For run_python click/fill commands, return action evidence such as `{"action_performed": True, "action_type": "fill", "filled_value": value}` after the Playwright action completes.
- If extracting data, return structured JSON-serializable Python values.
- For extract-only commands, do not return null/empty output unless the user explicitly allows empty results.
- Set allow_empty_output=true only when the user explicitly says no result, empty list, or empty output is acceptable.
- During repair, treat raw error logs and current page facts as authoritative. Any failure_analysis.hint is advisory only.
- 修复规则：
  - 修复时必须优先参考原始错误日志、异常类型、traceback 行号和当前页面事实。
  - 修复前先判断失败类型：如果失败来自 Python 代码错误，应优先修复对应代码行；如果失败来自页面状态、定位器、空数据或目标区域选择错误，再调整 selector 或取数策略。
  - 修复时应保持用户原始目标不变，不要把一次局部代码错误扩展成无关的页面流程重写。
- During repair after a fill/click actionability failure, inspect the page after failure and visible candidates before retrying the selector.
- If a click failed because another element or dialog intercepts pointer events, assume the target dialog is already open. Continue inside the visible dialog/overlay/current focused form instead of clicking the background trigger again.
- For state-changing or artifact-producing commands, prefer short bounded waits for a business-visible terminal condition such as a success message, row appearing in a list, status changing out of processing/pending, final URL leaving the edit page, or a download event, then return the observed state.
- For state-changing or artifact-producing commands, return observed state after the action, not just intended constants or an acknowledgement. Re-read the visible row/detail/form, success message, status text, generated file name, or download event before reporting success.
- For dialog/modal submissions, terminal evidence may be a success message, changed status, row removal from a pending list, or the dialog closing with no visible validation error.
- If a required terminal condition is not reached (for example not complete, not ready, no download, validation failed, or saved values do not match the intended values), raise RuntimeError with the observed state instead of returning success.
- Status values may be localized labels or raw enum tokens. Treat exact visible enum/status tokens from the page as authoritative terminal evidence; do not require translated synonyms that are not visible.
- After saving an edit form, list rows may only show summary columns. If some saved fields are not visible in the list, reopen the row detail/edit view or inspect the visible dialog before failing; do not require hidden fields to appear in a summary row.
- For multi-part commands, do not return after an intermediate milestone such as opening an edit dialog, showing a creation form, selecting a row, or making a target visible. Continue until every requested verb in the command has an observed terminal state.
- For asynchronous job/report flows that say to wait until completion or download a file, continue bounded polling until a completed/ready/downloadable state is visible or a browser download event fires. Do not return `downloaded: false`, `not_confirmed_complete`, or similar incomplete states as successful output.
- For asynchronous job/report flows, distinguish label/value description tables from result tables. If a completed state and filename are visible in a description panel, locate the associated row/action or page-level download control and require `page.expect_download()` before returning success.
- Use short bounded waits during recording; do not poll for minutes. If the terminal state is not reached quickly, return the best observed state instead of entering a long loop.
- For editable table or line-item forms, do not unconditionally add a new row. First inspect existing editable rows, reuse an empty/default row when available, fill by column/header/label semantics rather than raw input order, and verify row count or computed totals before submitting when those values are visible. Do not leave blank required line rows behind; fill them, remove them, or fail before submit with the observed blank cells.
- For create/submit forms, after clicking submit/save, verify that the browser left the editable form or that a success message/new record identifier/status is visible. If the page remains on the same form with blank required controls or validation text, raise RuntimeError instead of returning success.
- Do not click unnamed increment/decrement controls repeatedly for numeric fields. Prefer filling the numeric input directly after selecting/clearing it, or read the current value and set the exact target value.
- For input[type=number] or role=spinbutton, fill only numeric strings. If the intended value is not numeric, the target is a different field; re-select by row header, label, placeholder, aria name, or nearby text before filling.
- Avoid broad positional form filling. When a form or editable table has labels, placeholders, aria names, data attributes, column headers, or row-local controls, map values to those semantic anchors first and use raw input order only as a last resort.
- Component libraries may put data-testid on wrapper elements. Before filling a test-id locator, ensure the target is an editable input/textarea/select/contenteditable element; otherwise fill the wrapper-local editable descendant.
- In dialogs and forms, scope field locators to the dialog/form container and prefer stable data-testid/role/placeholder locators. Avoid bare page.get_by_label(...) when the same label can match the dialog title or multiple controls.
- For empty-result filter/search tasks, absence of the searched value in rows is not enough. Verify zero data rows, a visible empty-state message, or row count reduction to zero after the filter; if unrelated rows remain visible, raise RuntimeError with the observed rows.
- Do not use extract_snapshot to return table column headers as data values unless the user asked for table schema. For table row data, use table_views row/cell evidence or executable Playwright code that extracts from a row anchored by a stable row key.
- Do not pass Python lambda or other callables as Playwright locator name/has_text filters; Playwright Python expects strings, regex patterns, or supported options.
"""


class RecordingAgentResult(BaseModel):
    success: bool
    trace: Optional[RPAAcceptedTrace] = None
    traces: List[RPAAcceptedTrace] = Field(default_factory=list)
    diagnostics: List[RPATraceDiagnostic] = Field(default_factory=list)
    output_key: Optional[str] = None
    output: Any = None
    message: str = ""


class RecordingPlannerContractError(ValueError):
    def __init__(self, message: str, *, raw_output: str = "", cause: Optional[BaseException] = None):
        super().__init__(message)
        self.raw_output = raw_output
        self.llm_call: Dict[str, Any] = {}
        self.__cause__ = cause


Planner = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
Executor = Callable[[Any, Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]
CompletionVerifier = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class RecordingRuntimeAgent:
    def __init__(
        self,
        planner: Optional[Planner] = None,
        executor: Optional[Executor] = None,
        completion_verifier: Optional[CompletionVerifier] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ):
        self._uses_default_planner = planner is None
        self.planner = planner or self._default_planner
        self.executor = executor or self._default_executor
        self.completion_verifier = completion_verifier
        self._instruction_completion_check_enabled = completion_verifier is not None or self._uses_default_planner
        self.model_config = model_config
        self._planner_llm_calls: List[Dict[str, Any]] = []

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
        if _instruction_is_detail_extract_only(instruction) and not list(snapshot.get("detail_views") or []):
            try:
                await page.wait_for_timeout(750)
                snapshot = await _safe_page_snapshot(page)
            except Exception:
                pass
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

        first_plan = _build_preplanned_plan(instruction, snapshot)
        if not first_plan:
            first_plan, first_result = await self._plan_and_execute(
                page=page,
                payload=payload,
                runtime_results=runtime_results,
                instruction=instruction,
                before=before,
                before_snapshot=snapshot,
            )
        else:
            first_result = await self.executor(page, first_plan, runtime_results)
            first_result = await _ensure_expected_effect(
                page=page,
                instruction=instruction,
                plan=first_plan,
                result=first_result,
                before=before,
            )
            first_result = await self._verify_instruction_completion_if_needed(
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
                traces=[trace],
                output_key=trace.output_key,
                output=trace.output,
                message="Recording command completed.",
            )

        failed_page = await _page_state(page)
        failed_snapshot = await _safe_page_snapshot(page)
        compact_failed_snapshot = _compact_snapshot(failed_snapshot, instruction)
        recovered = await self._accept_recovered_side_effect(
            page=page,
            instruction=instruction,
            plan=first_plan,
            result=first_result,
            before=before,
            before_snapshot=snapshot,
            after_snapshot=failed_snapshot,
            diagnostics=[],
            repair_attempted=False,
        )
        if recovered:
            return recovered
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

        repair_guidance = _repair_guidance_for_failure(
            error=first_error,
            instruction=instruction,
            failure_analysis=first_known_failure_analysis,
        )
        repair_context = {
            "error": first_error,
            "failed_plan": first_plan,
            "page_after_failure": failed_page.model_dump(mode="json"),
            "snapshot_after_failure": compact_failed_snapshot,
        }
        if repair_guidance:
            repair_context["guidance"] = repair_guidance
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
        repair_plan = _build_preplanned_plan(instruction, failed_snapshot)
        if repair_plan:
            repair_result = await self.executor(page, repair_plan, runtime_results)
            repair_result = await _ensure_expected_effect(
                page=page,
                instruction=instruction,
                plan=repair_plan,
                result=repair_result,
                before=failed_page,
            )
            repair_result = await self._verify_instruction_completion_if_needed(
                page=page,
                instruction=instruction,
                plan=repair_plan,
                result=repair_result,
                before=failed_page,
            )
        else:
            repair_plan, repair_result = await self._plan_and_execute(
                page=page,
                payload=repair_payload,
                runtime_results=runtime_results,
                instruction=instruction,
                before=before,
                before_snapshot=failed_snapshot,
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
            trace = await self._trace_with_replayable_failed_preconditions(
                page=page,
                instruction=instruction,
                failed_plans=[first_plan],
                repair_plan=repair_plan,
                repair_result=repair_result,
                before=before,
                repair_snapshot=failed_snapshot,
                fallback_trace=trace,
            )
            return RecordingAgentResult(
                success=True,
                trace=trace,
                traces=[trace],
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
        repair_failed_page = await _page_state(page)
        repair_failed_snapshot = await _safe_page_snapshot(page)
        compact_repair_failed_snapshot = _compact_snapshot(repair_failed_snapshot, instruction)
        recovered = await self._accept_recovered_side_effect(
            page=page,
            instruction=instruction,
            plan=repair_plan,
            result=repair_result,
            before=before,
            before_snapshot=failed_snapshot,
            after_snapshot=repair_failed_snapshot,
            diagnostics=diagnostics,
            repair_attempted=True,
            precondition_plans=[first_plan],
        )
        if recovered:
            return recovered
        logger.warning(
            "[RPA] recording command repair failed type=%s error=%s",
            repair_failure_analysis.get("type", "unknown"),
            repair_error[:300],
        )
        repair_diagnostic_raw = {
            "plan": _safe_jsonable(repair_plan),
            "result": _safe_jsonable(repair_result),
            "page_after_failure": repair_failed_page.model_dump(mode="json"),
            "snapshot_after_failure": _safe_jsonable(compact_repair_failed_snapshot),
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
        second_repair_guidance = _repair_guidance_for_failure(
            error=repair_error,
            instruction=instruction,
            failure_analysis=repair_known_failure_analysis,
            previous_failures=[diagnostic.message for diagnostic in diagnostics],
        )
        second_repair_context = {
            "error": repair_error,
            "failed_plan": repair_plan,
            "page_after_failure": repair_failed_page.model_dump(mode="json"),
            "snapshot_after_failure": compact_repair_failed_snapshot,
            "previous_failures": [diagnostic.message for diagnostic in diagnostics],
        }
        if second_repair_guidance:
            second_repair_context["guidance"] = second_repair_guidance
        if repair_error_type:
            second_repair_context["error_type"] = repair_error_type
        if repair_traceback:
            second_repair_context["traceback"] = repair_traceback
        if repair_known_failure_analysis:
            second_repair_context["failure_analysis"] = repair_known_failure_analysis
        second_repair_payload = {
            **payload,
            "repair": second_repair_context,
        }
        second_repair_plan, second_repair_result = await self._plan_and_execute(
            page=page,
            payload=second_repair_payload,
            runtime_results=runtime_results,
            instruction=instruction,
            before=before,
            before_snapshot=repair_failed_snapshot,
        )
        _write_recording_attempt_debug(
            "second_repair_attempt",
            instruction=instruction,
            page_state=repair_failed_page.model_dump(mode="json"),
            plan=second_repair_plan,
            execution_result=second_repair_result,
            failure_analysis=None if second_repair_result.get("success") else _known_failure_analysis(second_repair_result.get("error")),
            debug_context=debug_context,
        )
        if second_repair_result.get("success"):
            trace = await self._accepted_trace(
                page,
                instruction,
                second_repair_plan,
                second_repair_result,
                before,
                repair_attempted=True,
                snapshot=repair_failed_snapshot,
            )
            trace = await self._trace_with_replayable_failed_preconditions(
                page=page,
                instruction=instruction,
                failed_plans=[first_plan, repair_plan],
                repair_plan=second_repair_plan,
                repair_result=second_repair_result,
                before=before,
                repair_snapshot=repair_failed_snapshot,
                fallback_trace=trace,
            )
            return RecordingAgentResult(
                success=True,
                trace=trace,
                traces=[trace],
                diagnostics=diagnostics,
                output_key=trace.output_key,
                output=trace.output,
                message="Recording command completed after repair.",
            )

        second_repair_error = str(second_repair_result.get("error") or "recording command repair failed")
        second_repair_error_type = str(second_repair_result.get("error_type") or "").strip()
        second_repair_traceback = str(second_repair_result.get("traceback") or "").strip()
        second_repair_known_failure_analysis = _known_failure_analysis(second_repair_error)
        second_repair_failed_page = await _page_state(page)
        second_repair_failed_snapshot = await _safe_page_snapshot(page)
        compact_second_repair_failed_snapshot = _compact_snapshot(second_repair_failed_snapshot, instruction)
        recovered = await self._accept_recovered_side_effect(
            page=page,
            instruction=instruction,
            plan=second_repair_plan,
            result=second_repair_result,
            before=before,
            before_snapshot=repair_failed_snapshot,
            after_snapshot=second_repair_failed_snapshot,
            diagnostics=diagnostics,
            repair_attempted=True,
            precondition_plans=[first_plan, repair_plan],
        )
        if recovered:
            return recovered
        second_repair_diagnostic_raw = {
            "plan": _safe_jsonable(second_repair_plan),
            "result": _safe_jsonable(second_repair_result),
            "page_after_failure": second_repair_failed_page.model_dump(mode="json"),
            "snapshot_after_failure": _safe_jsonable(compact_second_repair_failed_snapshot),
        }
        if second_repair_error_type:
            second_repair_diagnostic_raw["error_type"] = second_repair_error_type
        if second_repair_traceback:
            second_repair_diagnostic_raw["traceback"] = second_repair_traceback
        if second_repair_known_failure_analysis:
            second_repair_diagnostic_raw["failure_analysis"] = second_repair_known_failure_analysis
        diagnostics.append(
            RPATraceDiagnostic(
                source="ai",
                message=second_repair_error,
                raw=second_repair_diagnostic_raw,
            )
        )
        return RecordingAgentResult(
            success=False,
            diagnostics=diagnostics,
            message="Recording command failed after two repairs.",
        )

    async def _plan_and_execute(
        self,
        *,
        page: Any,
        payload: Dict[str, Any],
        runtime_results: Dict[str, Any],
        instruction: str,
        before: RPAPageState,
        before_snapshot: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        timing_ms: Dict[str, float] = {}
        try:
            started_at = time.perf_counter()
            plan = await self.planner(payload)
            timing_ms["planner"] = round((time.perf_counter() - started_at) * 1000, 1)
        except Exception as exc:
            plan = {
                "description": "Planner output could not be executed",
                "action_type": "planner_error",
                "expected_effect": "none",
            }
            llm_call = getattr(exc, "llm_call", None)
            raw_output = str(getattr(exc, "raw_output", "") or "")
            return plan, {
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": _format_exception_for_repair(exc),
                "output": "",
                "planner_raw_output": raw_output,
                "llm_call": _safe_jsonable(llm_call) if isinstance(llm_call, dict) else {},
                "timing_ms": timing_ms,
            }
        started_at = time.perf_counter()
        executor_result = await self.executor(page, plan, runtime_results)
        result = executor_result
        timing_ms["executor"] = round((time.perf_counter() - started_at) * 1000, 1)
        started_at = time.perf_counter()
        result = await _ensure_expected_effect(
            page=page,
            instruction=instruction,
            plan=plan,
            result=result,
            before=before,
        )
        timing_ms["effect_verifier"] = round((time.perf_counter() - started_at) * 1000, 1)
        if executor_result.get("success") and before_snapshot and not _has_failed_instruction_completion(result):
            recovered = await self._recover_successful_side_effect_from_snapshot_diff(
                page=page,
                plan=plan,
                executor_result=executor_result,
                verifier_result=result,
                before_snapshot=before_snapshot,
            )
            if recovered and (
                _is_terminal_contract_failure_result(result)
                or _terminal_recovery_adds_structural_evidence(result, recovered)
            ):
                result = recovered
        if _should_try_semantic_terminal_judge(plan, result):
            try:
                started_at = time.perf_counter()
                judgement = await self._semantic_terminal_judge(
                    page=page,
                    instruction=instruction,
                    plan=plan,
                    result=result,
                )
                timing_ms["semantic_terminal_judge"] = round((time.perf_counter() - started_at) * 1000, 1)
                if judgement.get("passed"):
                    result = _attach_semantic_terminal_judgement(result, judgement)
            except Exception as exc:
                result = {
                    **result,
                    "semantic_terminal_judge_error": f"{type(exc).__name__}: {exc}",
                }
        started_at = time.perf_counter()
        result = await self._verify_instruction_completion_if_needed(
            page=page,
            instruction=instruction,
            plan=plan,
            result=result,
            before=before,
        )
        timing_ms["completion_verifier"] = round((time.perf_counter() - started_at) * 1000, 1)
        result = {**result, "timing_ms": {**timing_ms, **dict(result.get("timing_ms") or {})}}
        return plan, result

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
        result = _enrich_extract_snapshot_result_with_replay_evidence(result, snapshot or {})
        output = result.get("output")
        output_key = _normalize_result_key(plan.get("output_key"))
        locator_stability = _build_locator_stability_metadata(plan, snapshot or {})
        signals = _merge_runtime_ai_signal(dict(result.get("signals") or {}), plan)
        terminal_contract = normalize_terminal_contract(plan)
        if terminal_contract.get("required"):
            signals["terminal_contract"] = terminal_contract
        input_bindings = _dict_field(plan.get("input_bindings"))
        output_bindings = _dict_field(plan.get("output_bindings"))
        postcondition = await _trusted_replay_postcondition(
            page=page,
            plan=plan,
            result=result,
            input_bindings=input_bindings,
        )
        if not postcondition and snapshot and result.get("success") and _plan_has_browser_side_effect(plan):
            after_snapshot = await _safe_page_snapshot(page)
            inferred_terminal = snapshot_diff_terminal_postcondition(
                plan=plan,
                result=result,
                before_snapshot=snapshot,
                after_snapshot=after_snapshot,
            )
            if inferred_terminal:
                postcondition = _validated_postcondition(
                    inferred_terminal.get("postcondition"),
                    snapshot=after_snapshot,
                    input_bindings=input_bindings,
                    allow_literal_key=True,
                    result=result,
                )
                if postcondition:
                    signals["idempotent_postcondition_replay"] = {
                        "ignore_precondition_errors": True,
                        "reason": "snapshot diff produced a replayable terminal postcondition",
                    }
                    signals["terminal_evidence"] = inferred_terminal.get("evidence") or []
        return RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction=instruction,
            description=str(plan.get("description") or instruction),
            before_page=before,
            after_page=after,
            signals=signals,
            output_key=output_key,
            output=output,
            ai_execution=RPAAIExecution(
                language="snapshot" if str(plan.get("action_type") or "").strip() == "extract_snapshot" else "python",
                code=_extract_snapshot_preview_code(plan) if str(plan.get("action_type") or "").strip() == "extract_snapshot" else str(plan.get("code") or ""),
                output=output,
                error=result.get("error"),
                repair_attempted=repair_attempted,
            ),
            locator_stability=locator_stability,
            input_bindings=input_bindings,
            output_bindings=output_bindings,
            postcondition=postcondition,
        )

    async def _accept_recovered_side_effect(
        self,
        *,
        page: Any,
        instruction: str,
        plan: Dict[str, Any],
        result: Dict[str, Any],
        before: RPAPageState,
        before_snapshot: Dict[str, Any],
        after_snapshot: Dict[str, Any],
        diagnostics: List[RPATraceDiagnostic],
        repair_attempted: bool,
        precondition_plans: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[RecordingAgentResult]:
        if _has_failed_instruction_completion(result):
            return None
        recovered_side_effect = recover_failed_side_effect_from_snapshot_diff(
            plan=plan,
            result=result,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            instruction=instruction,
        )
        if not recovered_side_effect:
            return None
        recovered_plan, recovered_result = recovered_side_effect
        trace = await self._accepted_trace(
            page,
            instruction,
            recovered_plan,
            recovered_result,
            before,
            repair_attempted=repair_attempted,
            snapshot=after_snapshot,
        )
        if precondition_plans:
            trace = await self._trace_with_replayable_failed_preconditions(
                page=page,
                instruction=instruction,
                failed_plans=precondition_plans,
                repair_plan=recovered_plan,
                repair_result=recovered_result,
                before=before,
                repair_snapshot=after_snapshot,
                fallback_trace=trace,
            )
        return RecordingAgentResult(
            success=True,
            trace=trace,
            traces=[trace],
            diagnostics=diagnostics,
            output_key=trace.output_key,
            output=trace.output,
            message="Recording command completed with verified terminal evidence after a failed attempt.",
        )

    async def _recover_successful_side_effect_from_snapshot_diff(
        self,
        *,
        page: Any,
        plan: Dict[str, Any],
        executor_result: Dict[str, Any],
        verifier_result: Dict[str, Any],
        before_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        contract = normalize_terminal_contract(plan)
        if contract.get("kind") == "state_change":
            return {}
        after_snapshot = await _safe_page_snapshot(page)
        recovery = snapshot_diff_terminal_postcondition(
            plan=plan,
            result=executor_result,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
        )
        if not recovery:
            recovery = current_snapshot_terminal_postcondition(
                plan=plan,
                result=executor_result,
                snapshot=after_snapshot,
            )
        if not recovery:
            return {}
        postcondition = recovery.get("postcondition")
        evidence = list(recovery.get("evidence") or [])
        if not isinstance(postcondition, dict) or not evidence:
            return {}
        plan["postcondition"] = postcondition
        signals = dict(executor_result.get("signals") or {})
        signals["terminal_evidence"] = evidence
        signals["terminal_row"] = recovery.get("row_values") or {}
        effect = dict(executor_result.get("effect") or {})
        effect.setdefault("type", str(plan.get("expected_effect") or "mixed"))
        effect["terminal_evidence"] = str(evidence[0].get("type") or "postcondition")
        effect["terminal_evidence_items"] = evidence
        effect["snapshot_diff_terminal_recovered"] = True
        return {
            **executor_result,
            "success": True,
            "error": None,
            "signals": signals,
            "effect": effect,
            "terminal_verification": {
                **dict(verifier_result.get("terminal_verification") or {}),
                "passed": True,
                "evidence": evidence,
                "recovered_from_snapshot_diff": True,
            },
        }

    async def _trace_with_replayable_failed_preconditions(
        self,
        *,
        page: Any,
        instruction: str,
        failed_plans: List[Dict[str, Any]],
        repair_plan: Dict[str, Any],
        repair_result: Dict[str, Any],
        before: RPAPageState,
        repair_snapshot: Dict[str, Any],
        fallback_trace: RPAAcceptedTrace,
    ) -> RPAAcceptedTrace:
        combined_plan = _combine_run_python_attempts(
            [plan for plan in failed_plans if _plan_is_safe_replay_precondition(plan)],
            repair_plan,
        )
        if not combined_plan:
            return fallback_trace
        combined_trace = await self._accepted_trace(
            page,
            instruction,
            combined_plan,
            repair_result,
            before,
            repair_attempted=True,
            snapshot=repair_snapshot,
        )
        if combined_trace.postcondition:
            return combined_trace
        if fallback_trace.postcondition:
            merged_signals = dict(combined_trace.signals or {})
            merged_signals.update(dict(fallback_trace.signals or {}))
            return combined_trace.model_copy(
                update={
                    "signals": merged_signals,
                    "postcondition": fallback_trace.postcondition,
                    "output": fallback_trace.output,
                }
            )
        return fallback_trace

    async def _default_planner(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from backend.config import settings
        from backend.deepagent.engine import get_llm_model
        from langchain_core.messages import HumanMessage, SystemMessage

        planner_max_tokens = max(int(getattr(settings, "max_tokens", 0) or 0), _RECORDING_PLANNER_MIN_OUTPUT_TOKENS)
        model = get_llm_model(
            config=self.model_config,
            max_tokens_override=planner_max_tokens,
            streaming=False,
        )
        messages = [
            SystemMessage(content=RECORDING_RUNTIME_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ]
        llm_request = _build_planner_llm_request_summary(
            model=model,
            messages=messages,
            model_config=self.model_config,
        )
        response = await _ainvoke_model_with_recording_timeout(model, messages)
        response_text = _extract_text(response)
        llm_call = {
            "request": llm_request,
            "response": _text_diagnostic(response_text, limit=8000),
        }
        self._planner_llm_calls.append(llm_call)
        _log_planner_llm_call(llm_call)
        try:
            return _parse_json_object(response_text)
        except Exception as exc:
            try:
                setattr(exc, "llm_call", llm_call)
            except Exception:
                pass
            raise

    async def _semantic_terminal_judge(
        self,
        *,
        page: Any,
        instruction: str,
        plan: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        from backend.deepagent.engine import get_llm_model
        from langchain_core.messages import HumanMessage, SystemMessage

        snapshot = _compact_snapshot(await _safe_page_snapshot(page), instruction)
        payload = {
            "instruction": instruction,
            "terminal_contract": normalize_terminal_contract(plan),
            "terminal_verification": _safe_jsonable(result.get("terminal_verification")),
            "browser_evidence": _safe_jsonable(result.get("browser_evidence")),
            "snapshot": snapshot,
        }
        model = get_llm_model(config=self.model_config, streaming=False)
        response = await _ainvoke_model_with_recording_timeout(
            model,
            [
                SystemMessage(content=_SEMANTIC_TERMINAL_JUDGE_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
            ],
        )
        return _normalize_semantic_terminal_judgement(_parse_raw_json_object(_extract_text(response)), plan)

    async def _verify_instruction_completion_if_needed(
        self,
        *,
        page: Any,
        instruction: str,
        plan: Dict[str, Any],
        result: Dict[str, Any],
        before: RPAPageState,
    ) -> Dict[str, Any]:
        if not self._instruction_completion_check_enabled:
            return result
        if not _should_verify_instruction_completion(plan, result, instruction):
            return result
        if (
            str(plan.get("action_type") or "").strip() == "extract_snapshot"
            and not _is_deterministic_preplanned_extract(plan, result)
        ):
            current = await _page_state(page)
            if _page_url_changed(before.url, current.url):
                return {
                    **result,
                    "success": False,
                    "error": "Instruction completion verification failed: snapshot extraction cannot replay prior browser state changes.",
                    "instruction_completion": {
                        "passed": False,
                        "missing_requirements": ["replayable browser action trace"],
                        "reason": "extract_snapshot plans do not contain the browser actions that changed the page",
                    },
                }
        if self.completion_verifier is None and _is_deterministic_preplanned_extract(plan, result):
            signals = dict(result.get("signals") or {})
            signals["instruction_completion"] = {
                "passed": True,
                "missing_requirements": [],
                "reason": "deterministic read-only snapshot extraction",
                "source": "structural_preplan",
            }
            return {**result, "signals": signals}
        try:
            judgement = await self._instruction_completion_judge(
                page=page,
                instruction=instruction,
                plan=plan,
                result=result,
                before=before,
            )
        except Exception as exc:
            verifier_error = f"{type(exc).__name__}: {exc}"
            signals = dict(result.get("signals") or {})
            signals["instruction_completion_verifier_error"] = verifier_error
            if result.get("success") and _has_strong_terminal_completion_evidence(plan, result):
                return {
                    **result,
                    "signals": signals,
                    "instruction_completion_verifier_error": verifier_error,
                }
            return {
                **result,
                "success": False,
                "error": f"Instruction completion verification failed: {verifier_error}",
                "signals": signals,
                "instruction_completion_verifier_error": verifier_error,
            }
        signals = dict(result.get("signals") or {})
        signals["instruction_completion"] = judgement
        if judgement.get("passed"):
            return {**result, "signals": signals}
        missing = judgement.get("missing_requirements") or []
        reason = str(judgement.get("reason") or "instruction was not fully completed").strip()
        if missing:
            reason = f"{reason}; missing: {', '.join(str(item) for item in missing[:5])}"
        return {
            **result,
            "success": False,
            "error": f"Instruction completion verification failed: {reason}",
            "signals": signals,
            "instruction_completion": judgement,
        }

    async def _instruction_completion_judge(
        self,
        *,
        page: Any,
        instruction: str,
        plan: Dict[str, Any],
        result: Dict[str, Any],
        before: RPAPageState,
    ) -> Dict[str, Any]:
        verifier = self.completion_verifier or self._default_instruction_completion_judge
        payload = {
            "instruction": instruction,
            "before_page": before.model_dump(mode="json"),
            "current_page": (await _page_state(page)).model_dump(mode="json"),
            "plan": _completion_plan_summary(plan),
            "result": _completion_result_summary(result),
            "missing_instruction_identifiers": _missing_instruction_identifier_tokens(instruction, result),
            "snapshot": _compact_snapshot(await _safe_page_snapshot(page), instruction),
        }
        return _normalize_instruction_completion_judgement(await verifier(payload))

    async def _default_instruction_completion_judge(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from backend.deepagent.engine import get_llm_model
        from langchain_core.messages import HumanMessage, SystemMessage

        model = get_llm_model(config=self.model_config, streaming=False)
        response = await _ainvoke_model_with_recording_timeout(
            model,
            [
                SystemMessage(content=_INSTRUCTION_COMPLETION_JUDGE_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
            ],
        )
        return _parse_raw_json_object(_extract_text(response))

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
                browser_before = await capture_browser_evidence(page)
                await page.locator(selector).first.click()
                await _settle_after_browser_action(page)
                browser_after = await capture_browser_evidence(page)
                return {
                    "success": True,
                    "output": "clicked",
                    "effect": {"type": "click", "action_performed": True},
                    "browser_evidence": {"before": browser_before, "after": browser_after},
                }

            if action_type == "fill":
                selector = str(plan.get("selector") or "")
                value = plan.get("value", "")
                if not selector:
                    return {"success": False, "error": "fill plan missing selector", "output": ""}
                browser_before = await capture_browser_evidence(page)
                await page.locator(selector).first.fill(str(value))
                await _settle_after_browser_action(page)
                browser_after = await capture_browser_evidence(page)
                return {
                    "success": True,
                    "output": value,
                    "effect": {"type": "fill", "action_performed": True},
                    "browser_evidence": {"before": browser_before, "after": browser_after},
                }

            if action_type == "extract_snapshot":
                snapshot = await _safe_page_snapshot(page)
                return _execute_extract_snapshot_plan(plan, snapshot=snapshot)

            code = str(plan.get("code") or "")
            code = _normalize_generated_playwright_code(code)
            plan["code"] = code
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

            browser_before = await capture_browser_evidence(page)
            browser_after: Dict[str, Any] = {}
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
                await _settle_after_browser_action(page)
                browser_after = await capture_browser_evidence(page)
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
            if browser_before or browser_after:
                response["browser_evidence"] = {"before": browser_before, "after": browser_after}
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
            structured_output = _structured_exception_output(exc)
            return {
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": _format_exception_for_repair(exc),
                "output": structured_output if structured_output is not None else "",
            }


def _execute_extract_snapshot_plan(plan: Dict[str, Any], snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fields = _resolve_snapshot_plan_fields(plan, snapshot or {})
    if not fields:
        return {"success": False, "error": "extract_snapshot plan missing fields", "output": ""}

    output: Dict[str, Any] = {}
    selected_fields: List[Dict[str, Any]] = []
    include_hidden = _normalize_bool(plan.get("include_hidden"))
    include_empty = _normalize_bool(plan.get("include_empty"))
    for field in fields:
        label = str(field.get("label") or "").strip()
        if not label:
            continue
        visible = bool(field.get("visible", True))
        value_info = _snapshot_field_value_info(field)
        value = value_info["value"]
        if not visible and not include_hidden:
            continue
        if value == "" and not include_empty:
            continue
        output[label] = value
        selected_fields.append(
            {
                "label": label,
                "value": value,
                "observed_label": value_info["observed_label"],
                "data_prop": str(field.get("data_prop") or "").strip(),
                "visible": visible,
                "value_kind": str(field.get("value_kind") or "").strip(),
                "required": bool(field.get("required")),
                "replay_required": bool(field.get("replay_required", True)),
                "field_locator": dict(field.get("field_locator") or {}),
                "label_locator": dict(field.get("label_locator") or {}),
                "value_locator": dict(field.get("value_locator") or {}),
                "locator_hints": list(field.get("locator_hints") or [])[:3],
                "adapter": str(field.get("adapter") or field.get("framework_hint") or "").strip(),
                "value_selector": str(field.get("value_selector") or "").strip(),
                "value_selectors": list(field.get("value_selectors") or [])[:6],
            }
        )

    if not output and not _normalize_bool(plan.get("allow_empty_output")):
        return {
            "success": False,
            "error": "extract_snapshot plan produced no visible non-empty fields",
            "output": "",
        }

    return {
        "success": True,
        "error": None,
        "output": output,
        "signals": {
            "extract_snapshot": {
                "source": str(plan.get("source") or "").strip(),
                "section_title": str(plan.get("section_title") or "").strip(),
                "frame_path": _snapshot_plan_frame_path(plan),
                "fields": selected_fields,
            }
        },
    }


def _structured_exception_output(exc: Exception) -> Any:
    if not getattr(exc, "args", None):
        return None
    payload = exc.args[0]
    if isinstance(payload, (dict, list)):
        return payload
    if not isinstance(payload, str):
        return None
    text = payload.strip()
    if not text.startswith(("{", "[")):
        return None
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _enrich_extract_snapshot_result_with_replay_evidence(
    result: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    signals = result.get("signals") if isinstance(result.get("signals"), dict) else {}
    extract_signal = signals.get("extract_snapshot") if isinstance(signals.get("extract_snapshot"), dict) else {}
    fields = extract_signal.get("fields") if isinstance(extract_signal.get("fields"), list) else []
    if not fields:
        return result

    enriched_fields = [
        _enrich_extract_snapshot_field_with_replay_evidence(dict(field), snapshot)
        for field in fields
        if isinstance(field, dict)
    ]
    enriched_fields = _enrich_extract_snapshot_fields_with_table_cell_evidence(enriched_fields, snapshot)
    enriched_signal = dict(extract_signal)
    enriched_signal["fields"] = enriched_fields
    enriched_signals = dict(signals)
    enriched_signals["extract_snapshot"] = enriched_signal
    enriched_result = dict(result)
    output = enriched_result.get("output")
    if isinstance(output, dict):
        enriched_output = dict(output)
        for field in enriched_fields:
            label = str(field.get("label") or "").strip()
            if label and label in enriched_output:
                enriched_output[label] = field.get("value")
        enriched_result["output"] = enriched_output
    enriched_result["signals"] = enriched_signals
    return enriched_result


def _enrich_extract_snapshot_field_with_replay_evidence(
    field: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    raw_value = str(field.get("value") or "").strip()
    if raw_value:
        observed_field = _observed_detail_field_for_label(snapshot, raw_value)
        if observed_field and not _value_visible_in_table_cell(snapshot, raw_value):
            field["value"] = str(observed_field.get("value") or "").strip()
            field["observed_label"] = str(observed_field.get("label") or "").strip()
    value_info = _snapshot_field_value_info(field)
    if value_info["value"] != field.get("value"):
        field["value"] = value_info["value"]
    observed_label = value_info["observed_label"]
    observed_label_exists = _observed_detail_label_exists(snapshot, observed_label)
    value_matched_label = _observed_detail_label_for_value(snapshot, value_info["value"])
    if value_matched_label and (not observed_label or not observed_label_exists):
        field["observed_label"] = value_matched_label
    elif observed_label and not str(field.get("observed_label") or "").strip():
        field["observed_label"] = observed_label
    value = str(value_info["value"] or "").strip()
    if not value:
        return field

    has_primary_evidence = _snapshot_field_has_replay_evidence(field)

    if not has_primary_evidence:
        url_evidence = _url_path_join_evidence(str(snapshot.get("url") or ""), value)
        if url_evidence:
            field["url_extraction"] = url_evidence
            return field

        text_pattern = _text_pattern_evidence(snapshot, value)
        if text_pattern:
            field["text_pattern"] = text_pattern
            return field

    if not isinstance(field.get("unique_text"), dict):
        unique_text = _unique_visible_text_evidence(snapshot, value)
        if unique_text:
            field["unique_text"] = unique_text
    return field


def _snapshot_field_has_replay_evidence(field: Dict[str, Any]) -> bool:
    if str(field.get("data_prop") or "").strip():
        return True
    if isinstance(field.get("field_locator"), dict) and field["field_locator"]:
        return True
    if isinstance(field.get("value_locator"), dict) and field["value_locator"]:
        return True
    if isinstance(field.get("url_extraction"), dict) and field["url_extraction"]:
        return True
    if isinstance(field.get("text_pattern"), dict) and field["text_pattern"]:
        return True
    if isinstance(field.get("table_cell"), dict) and field["table_cell"]:
        return True
    return False


def _enrich_extract_snapshot_fields_with_table_cell_evidence(
    fields: List[Dict[str, Any]],
    snapshot: Dict[str, Any],
) -> List[Dict[str, Any]]:
    anchors = [
        _normalize_visible_text(field.get("value"))
        for field in fields
        if _snapshot_field_has_replay_evidence(field) and _normalize_visible_text(field.get("value"))
    ]
    min_score = 1
    if not anchors:
        anchors = [
            _normalize_visible_text(field.get("value"))
            for field in fields
            if _normalize_visible_text(field.get("value"))
        ]
        min_score = 2
    if not anchors:
        return fields

    table_match = _best_table_row_match(snapshot, anchors, min_score=min_score)
    if not table_match:
        return fields

    headers = table_match["headers"]
    row_cells = table_match["cells"]
    anchor_cell = table_match["anchor_cell"]
    row_key = {
        str(anchor_cell.get("column_header") or "").strip(): str(anchor_cell.get("text") or "").strip()
    }
    if not next(iter(row_key.keys()), "") or not next(iter(row_key.values()), ""):
        return fields

    enriched: List[Dict[str, Any]] = []
    for field in fields:
        item = dict(field)
        if not isinstance(item.get("table_cell"), dict) or not item["table_cell"]:
            target = _normalize_visible_text(item.get("value"))
            cell = _first_row_cell_with_text(row_cells, target)
            if cell and str(cell.get("column_header") or "").strip():
                item["table_cell"] = {
                    "table_headers": headers,
                    "row_key": row_key,
                    "column_header": str(cell.get("column_header") or "").strip(),
                    "column_index": cell.get("column_index"),
                }
        enriched.append(item)
    return enriched


def _best_table_row_match(snapshot: Dict[str, Any], anchors: List[str], *, min_score: int = 1) -> Dict[str, Any]:
    anchor_set = {_normalize_visible_text(item) for item in anchors if _normalize_visible_text(item)}
    best: Dict[str, Any] = {}
    best_score = 0
    for table in list(snapshot.get("table_views") or []):
        if not isinstance(table, dict):
            continue
        headers = [
            _normalize_visible_text(column.get("header"))
            for column in list(table.get("columns") or [])
            if isinstance(column, dict) and _normalize_visible_text(column.get("header"))
        ]
        if not headers:
            continue
        for row in list(table.get("rows") or []):
            if not isinstance(row, dict):
                continue
            cells = [cell for cell in list(row.get("cells") or []) if isinstance(cell, dict)]
            matched_cells = [
                cell
                for cell in cells
                if _normalize_visible_text(cell.get("text")) in anchor_set
                and _normalize_visible_text(cell.get("column_header"))
            ]
            matched_values = {_normalize_visible_text(cell.get("text")) for cell in matched_cells}
            score = len(matched_values)
            if score >= min_score and score > best_score:
                best_score = score
                best = {
                    "headers": headers,
                    "cells": cells,
                    "anchor_cell": _select_table_anchor_cell(matched_cells, table_rows=list(table.get("rows") or [])),
                }
    return best


def _select_table_anchor_cell(
    cells: List[Dict[str, Any]],
    *,
    table_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    scored = []
    for order, cell in enumerate(cells):
        text = _normalize_visible_text(cell.get("text"))
        if not text:
            continue
        column_index = cell.get("column_index")
        column_header = _normalize_visible_text(cell.get("column_header"))
        score = 0
        if _table_cell_value_is_column_unique(table_rows or [], column_index, text):
            score += 80
        if column_header:
            score += 20
        if cell.get("column_id"):
            score += 20
        if isinstance(column_index, int) and column_index == 0:
            score += 5
        if cell.get("controls") or cell.get("actions"):
            score -= 30
        if len(text) > 80:
            score -= 20
        scored.append((score, -order, cell))
    if not scored:
        return cells[0] if cells else {}
    return max(scored, key=lambda item: (item[0], item[1]))[2]


def _table_cell_value_is_column_unique(rows: List[Dict[str, Any]], column_index: Any, text: str) -> bool:
    if not isinstance(column_index, int):
        return False
    target = _normalize_visible_text(text)
    if not target:
        return False
    occurrences = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        for cell in list(row.get("cells") or []):
            if not isinstance(cell, dict) or cell.get("column_index") != column_index:
                continue
            if _normalize_visible_text(cell.get("text")) == target:
                occurrences += 1
                if occurrences > 1:
                    return False
    return occurrences == 1


def _first_row_cell_with_text(cells: List[Dict[str, Any]], text: str) -> Dict[str, Any]:
    target = _normalize_visible_text(text)
    if not target:
        return {}
    for cell in cells:
        if _normalize_visible_text(cell.get("text")) == target:
            return cell
    return {}


def _value_visible_in_table_cell(snapshot: Dict[str, Any], value: str) -> bool:
    target = _normalize_visible_text(value)
    if not target:
        return False
    for table in list(snapshot.get("table_views") or []):
        if not isinstance(table, dict):
            continue
        for row in list(table.get("rows") or []):
            if not isinstance(row, dict):
                continue
            for cell in list(row.get("cells") or []):
                if isinstance(cell, dict) and _normalize_visible_text(cell.get("text")) == target:
                    return True
    return False


def _url_path_join_evidence(url: str, value: str) -> Dict[str, Any]:
    target = _normalize_slash_joined_text(value)
    if not target:
        return {}

    parsed = urlparse(url)
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    for start in range(len(segments)):
        for count in range(1, len(segments) - start + 1):
            joined = "/".join(segments[start : start + count])
            if _normalize_slash_joined_text(joined) == target:
                return {
                    "kind": "url_path_join",
                    "start": start,
                    "count": count,
                    "separator": "/",
                }
    return {}


def _normalize_slash_joined_text(value: str) -> str:
    text = _normalize_visible_text(value)
    text = re.sub(r"\s*/\s*", "/", text)
    return text.strip("/")


def _text_pattern_evidence(snapshot: Dict[str, Any], value: str) -> Dict[str, Any]:
    target = _normalize_visible_text(value)
    if not target:
        return {}

    for node in _snapshot_text_evidence_nodes(snapshot):
        for text in _node_visible_text_candidates(node):
            pattern = _text_pattern_from_observed_value(text, target)
            if not pattern:
                continue
            role = str(node.get("role") or "").strip()
            tag = str(node.get("tag") or node.get("element_snapshot", {}).get("tag") or "").strip().lower()
            if role:
                pattern["role"] = role
            if tag:
                pattern["tag"] = tag
            pattern["value"] = value
            return pattern
    return {}


def _unique_visible_text_evidence(snapshot: Dict[str, Any], value: str) -> Dict[str, Any]:
    target = _normalize_visible_text(value)
    if not target:
        return {}

    matches: List[Dict[str, Any]] = []
    for node in _snapshot_text_evidence_nodes(snapshot):
        for text in _node_visible_text_candidates(node):
            if _normalize_visible_text(text) != target:
                continue
            role = str(node.get("role") or "").strip()
            tag = str(node.get("tag") or node.get("element_snapshot", {}).get("tag") or "").strip().lower()
            match: Dict[str, Any] = {"text": target}
            if role:
                match["role"] = role
            if tag:
                match["tag"] = tag
            if match not in matches:
                matches.append(match)
    if len(matches) != 1:
        return {}
    return matches[0]


def _snapshot_text_evidence_nodes(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for key in ("content_nodes", "actionable_nodes"):
        for node in list(snapshot.get(key) or []):
            if isinstance(node, dict):
                nodes.append(node)
    return nodes


def _node_visible_text_candidates(node: Dict[str, Any]) -> List[str]:
    element_snapshot = node.get("element_snapshot") if isinstance(node.get("element_snapshot"), dict) else {}
    raw_values = [
        node.get("text"),
        node.get("name"),
        element_snapshot.get("text"),
        element_snapshot.get("title"),
    ]
    candidates: List[str] = []
    for value in raw_values:
        text = _normalize_visible_text(value)
        if text and text not in candidates:
            candidates.append(text)
    return candidates


def _text_pattern_from_observed_value(text: str, value: str) -> Dict[str, Any]:
    normalized_text = _normalize_visible_text(text)
    normalized_value = _normalize_visible_text(value)
    index = normalized_text.find(normalized_value)
    if index < 0:
        return {}
    prefix = normalized_text[:index].strip()
    suffix = normalized_text[index + len(normalized_value) :].strip()
    if not prefix and not suffix:
        return {}
    return {
        "prefix": prefix[-80:],
        "suffix": suffix[:80],
    }


def _normalize_visible_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _snapshot_plan_frame_path(plan: Dict[str, Any]) -> List[str]:
    frame_path = plan.get("frame_path")
    if isinstance(frame_path, list):
        return [str(item) for item in frame_path if str(item or "").strip()]
    extraction = plan.get("extraction")
    if isinstance(extraction, dict) and isinstance(extraction.get("frame_path"), list):
        return [str(item) for item in extraction["frame_path"] if str(item or "").strip()]
    return []


def _snapshot_plan_fields(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    fields = plan.get("fields")
    if isinstance(fields, list):
        return [_normalize_snapshot_plan_field(dict(field)) for field in fields if isinstance(field, dict)]
    if isinstance(fields, dict):
        return _snapshot_field_map_to_list(fields)
    extraction = plan.get("extraction")
    if isinstance(extraction, dict) and isinstance(extraction.get("fields"), list):
        return [_normalize_snapshot_plan_field(dict(field)) for field in extraction["fields"] if isinstance(field, dict)]
    if isinstance(extraction, dict) and isinstance(extraction.get("fields"), dict):
        return _snapshot_field_map_to_list(extraction["fields"])
    return []


def _resolve_snapshot_plan_fields(plan: Dict[str, Any], snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    fields = _snapshot_plan_fields(plan)
    if not snapshot:
        return fields
    return [_resolve_snapshot_plan_field_from_observed_detail(field, snapshot) for field in fields]


def _resolve_snapshot_plan_field_from_observed_detail(
    field: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    value_info = _snapshot_field_value_info(field)
    if value_info["value"]:
        return field

    candidates = [
        value_info["observed_label"],
        str(field.get("observed_label") or "").strip(),
        str(field.get("label") or "").strip(),
    ]
    observed = {}
    for label in candidates:
        if not label:
            continue
        observed = _observed_detail_field_for_label(snapshot, label)
        if observed:
            break
    if not observed:
        return field

    resolved = dict(field)
    observed_info = _snapshot_field_value_info(observed)
    if observed_info["value"]:
        resolved["value"] = observed_info["value"]
    if observed_info["observed_label"] and not str(resolved.get("observed_label") or "").strip():
        resolved["observed_label"] = observed_info["observed_label"]
    for key in (
        "field_locator",
        "label_locator",
        "value_locator",
        "locator_hints",
        "data_prop",
        "value_kind",
        "required",
        "visible",
        "adapter",
        "framework_hint",
        "value_selector",
        "value_selectors",
    ):
        if key not in resolved and key in observed:
            resolved[key] = observed[key]
    return resolved


def _snapshot_field_map_to_list(fields: Dict[str, Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for label, value in fields.items():
        label_text = str(label or "").strip()
        if not label_text:
            continue
        normalized.append(_normalize_snapshot_plan_field({"label": label_text, "value": value}))
    return normalized


def _normalize_snapshot_plan_field(field: Dict[str, Any]) -> Dict[str, Any]:
    value_info = _snapshot_field_value_info(field)
    if value_info["value"] != field.get("value"):
        field["value"] = value_info["value"]
    if value_info["observed_label"] and not str(field.get("observed_label") or "").strip():
        field["observed_label"] = value_info["observed_label"]
    return field


def _snapshot_field_value_info(field: Dict[str, Any]) -> Dict[str, str]:
    raw_value = field.get("value")
    observed_label = str(field.get("observed_label") or "").strip()
    if isinstance(raw_value, dict):
        nested_label = str(raw_value.get("label") or "").strip()
        nested_value = raw_value.get("value")
        return {
            "value": str(nested_value or "").strip(),
            "observed_label": observed_label or nested_label,
        }
    return {"value": str(raw_value or "").strip(), "observed_label": observed_label}


def _observed_detail_label_for_value(snapshot: Dict[str, Any], value: str) -> str:
    target = _normalize_visible_text(value)
    if not target:
        return ""
    for detail in list(snapshot.get("detail_views") or []):
        if not isinstance(detail, dict):
            continue
        for field in list(detail.get("fields") or []):
            if not isinstance(field, dict):
                continue
            field_value = _normalize_visible_text(field.get("value"))
            label = str(field.get("label") or "").strip()
            if label and field_value == target:
                return label
    return ""


def _observed_detail_label_exists(snapshot: Dict[str, Any], label: str) -> bool:
    target = _normalize_visible_text(label)
    if not target:
        return False
    for detail in list(snapshot.get("detail_views") or []):
        if not isinstance(detail, dict):
            continue
        for field in list(detail.get("fields") or []):
            if not isinstance(field, dict):
                continue
            if _normalize_visible_text(field.get("label")) == target:
                return True
    return False


def _observed_detail_field_for_label(snapshot: Dict[str, Any], label: str) -> Dict[str, Any]:
    target = _normalize_visible_text(label)
    if not target:
        return {}
    for detail in list(snapshot.get("detail_views") or []):
        if not isinstance(detail, dict):
            continue
        for field in list(detail.get("fields") or []):
            if not isinstance(field, dict):
                continue
            field_label = _normalize_visible_text(field.get("label"))
            field_value = _normalize_visible_text(field.get("value"))
            if field_label == target and field_value:
                return field
    return {}


def _extract_snapshot_preview_code(plan: Dict[str, Any]) -> str:
    fields = _snapshot_plan_fields(plan)
    labels = [str(field.get("label") or "").strip() for field in fields if str(field.get("label") or "").strip()]
    lines = [
        "# extract_snapshot: values were read from the current compact snapshot during recording",
        "# final skill compilation will generate Playwright extraction code from this evidence",
    ]
    source = str(plan.get("source") or "").strip()
    section_title = str(plan.get("section_title") or "").strip()
    if source:
        lines.append(f"# source: {source}")
    if section_title:
        lines.append(f"# section: {section_title}")
    for label in labels[:20]:
        lines.append(f"# field: {label}")
    return "\n".join(lines)


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
    candidates = _json_object_candidates(raw)
    last_error: Optional[Exception] = None
    validation_error: Optional[ValueError] = None
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for start in (index for index, char in enumerate(candidate) if char == "{"):
            try:
                parsed, _end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(parsed, dict):
                try:
                    return _normalize_planner_object(parsed)
                except ValueError as exc:
                    if _looks_like_planner_object(parsed):
                        raise exc
                    if validation_error is None:
                        validation_error = exc
                    continue
    if validation_error:
        raise RecordingPlannerContractError(
            str(validation_error),
            raw_output=raw,
            cause=validation_error,
        ) from validation_error
    if last_error:
        raise RecordingPlannerContractError(
            f"Recording planner returned invalid JSON: {last_error}",
            raw_output=raw,
            cause=last_error,
        ) from last_error
    raise RecordingPlannerContractError("Recording planner must return a JSON object", raw_output=raw)


def _parse_raw_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    decoder = json.JSONDecoder()
    last_error: Optional[Exception] = None
    for candidate in _json_object_candidates(raw):
        for start in (index for index, char in enumerate(candidate) if char == "{"):
            try:
                parsed, _end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(parsed, dict):
                return parsed
    if last_error:
        raise last_error
    raise ValueError("Expected a JSON object")


def _should_try_semantic_terminal_judge(plan: Dict[str, Any], result: Dict[str, Any]) -> bool:
    if result.get("success"):
        return False
    contract = normalize_terminal_contract(plan)
    if not contract.get("required") or not contract.get("allow_semantic_judge"):
        return False
    verification = result.get("terminal_verification")
    if not isinstance(verification, dict):
        return False
    return verification.get("reason") != "validation_error_visible"


def _should_verify_instruction_completion(plan: Dict[str, Any], result: Dict[str, Any], instruction: str = "") -> bool:
    if not result.get("success"):
        return False
    if str(plan.get("action_type") or "").strip() == "extract_snapshot":
        return True
    if _missing_instruction_identifier_tokens(instruction, result):
        return True
    if _result_has_typed_terminal_evidence(result):
        return not _has_strong_terminal_completion_evidence(plan, result)
    expected = _normalize_expected_effect(plan.get("expected_effect"))
    if expected in {"click", "fill", "mixed"} and _output_is_action_only(result.get("output")):
        return True
    return False


def _plan_has_browser_side_effect(plan: Dict[str, Any]) -> bool:
    expected = _normalize_expected_effect(plan.get("expected_effect"))
    if expected in {"navigate", "click", "fill", "mixed"}:
        return True
    action_type = str(plan.get("action_type") or "").strip()
    return action_type in {"goto", "click", "fill", "run_python"}


def _plan_is_safe_replay_precondition(plan: Dict[str, Any]) -> bool:
    action_type = str(plan.get("action_type") or "").strip()
    if action_type == "goto":
        return True
    if action_type != "run_python":
        return False
    code = str(plan.get("code") or "").lower()
    mutation_markers = (
        ".click(",
        ".dblclick(",
        ".fill(",
        ".press(",
        ".check(",
        ".uncheck(",
        ".select_option(",
        ".set_input_files(",
        ".dispatch_event(",
        ".evaluate(",
    )
    return not any(marker in code for marker in mutation_markers)


_ENTITY_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9][A-Za-z0-9_-]{3,}(?![A-Za-z0-9_])")


def _missing_instruction_identifier_tokens(instruction: str, result: Dict[str, Any]) -> List[str]:
    tokens = _instruction_identifier_tokens(instruction)
    if len(tokens) < 2:
        return []
    observed = _normalize_visible_text(
        json.dumps(
            {
                "output": _safe_jsonable(result.get("output")),
                "effect": _safe_jsonable(result.get("effect")),
                "signals": _safe_jsonable(result.get("signals")),
                "terminal_verification": _safe_jsonable(result.get("terminal_verification")),
            },
            ensure_ascii=False,
            default=str,
        )
    ).lower()
    return [token for token in tokens if token.lower() not in observed]


def _instruction_identifier_tokens(instruction: str) -> List[str]:
    tokens: List[str] = []
    for match in _ENTITY_TOKEN_RE.finditer(str(instruction or "")):
        token = match.group(0).strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens[:12]


def _result_has_typed_terminal_evidence(result: Dict[str, Any]) -> bool:
    effect = result.get("effect")
    if isinstance(effect, dict) and (effect.get("terminal_evidence") or effect.get("terminal_evidence_items")):
        return True
    signals = result.get("signals")
    if isinstance(signals, dict) and (
        signals.get("download")
        or signals.get("terminal_evidence")
        or signals.get("extract_snapshot")
    ):
        return True
    verification = result.get("terminal_verification")
    return isinstance(verification, dict) and bool(verification.get("passed"))


def _has_strong_terminal_completion_evidence(plan: Dict[str, Any], result: Dict[str, Any]) -> bool:
    verification = result.get("terminal_verification")
    if isinstance(verification, dict) and verification.get("passed"):
        return True
    signals = result.get("signals")
    if isinstance(signals, dict) and signals.get("download"):
        return True
    if str(plan.get("action_type") or "").strip() == "extract_snapshot" and _is_deterministic_preplanned_extract(plan, result):
        return True
    contract = normalize_terminal_contract(plan)
    if contract.get("required"):
        return False
    return False


def _page_url_changed(before_url: str, after_url: str) -> bool:
    return _stable_page_url(before_url) != _stable_page_url(after_url)


def _stable_page_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def _output_is_action_only(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    if not _normalize_bool(output.get("action_performed")):
        return False
    evidence_keys = {
        "url",
        "href",
        "download_filename",
        "download_triggered",
        "created",
        "submitted",
        "submission_confirmed",
        "saved",
        "updated",
        "deleted",
        "confirmed",
        "confirmation",
        "visible_confirmation_text",
        "status",
        "state",
        "row",
        "record",
        "records",
        "data",
        "value",
        "values",
        "result",
        "results",
    }
    return not any(key in output for key in evidence_keys)


def _is_deterministic_preplanned_extract(plan: Dict[str, Any], result: Dict[str, Any]) -> bool:
    if str(plan.get("action_type") or "").strip() != "extract_snapshot":
        return False
    if str(plan.get("preplanned_source") or "").strip() not in {"detail_snapshot", "table_snapshot", "ordinal_snapshot"}:
        return False
    signal = (result.get("signals") or {}).get("extract_snapshot") if isinstance(result.get("signals"), dict) else {}
    fields = signal.get("fields") if isinstance(signal, dict) else []
    return bool(fields)


def _completion_plan_summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    summary = {
        "description": str(plan.get("description") or "")[:500],
        "action_type": str(plan.get("action_type") or ""),
        "expected_effect": str(plan.get("expected_effect") or ""),
        "output_key": str(plan.get("output_key") or ""),
        "source": str(plan.get("source") or ""),
        "section_title": str(plan.get("section_title") or ""),
        "fields": _safe_jsonable(plan.get("fields") or [])[:30]
        if isinstance(_safe_jsonable(plan.get("fields") or []), list)
        else [],
    }
    code = str(plan.get("code") or "").strip()
    if code:
        summary["code_excerpt"] = code[:1200]
    return summary


def _completion_result_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": bool(result.get("success")),
        "output": _safe_jsonable(result.get("output")),
        "effect": _safe_jsonable(result.get("effect")),
        "signals": _safe_jsonable(result.get("signals")),
        "error": str(result.get("error") or "")[:500],
    }


def _normalize_instruction_completion_judgement(value: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "passed": False,
            "missing_requirements": ["verifier_output_not_object"],
            "reason": "Completion verifier did not return a JSON object.",
        }
    missing = value.get("missing_requirements")
    if isinstance(missing, str):
        missing_items = [missing]
    elif isinstance(missing, list):
        missing_items = [str(item).strip()[:200] for item in missing if str(item).strip()]
    else:
        missing_items = []
    passed = _normalize_bool(value.get("passed"))
    if passed:
        missing_items = []
    return {
        "passed": passed,
        "missing_requirements": missing_items[:8],
        "reason": str(value.get("reason") or "").strip()[:500],
    }


def _has_failed_instruction_completion(result: Dict[str, Any]) -> bool:
    signals = result.get("signals") if isinstance(result.get("signals"), dict) else {}
    judgement = signals.get("instruction_completion") or result.get("instruction_completion")
    return isinstance(judgement, dict) and judgement.get("passed") is False


def _terminal_recovery_adds_structural_evidence(original: Dict[str, Any], recovered: Dict[str, Any]) -> bool:
    original_types = _terminal_evidence_types(original)
    recovered_types = _terminal_evidence_types(recovered)
    structural_types = {"row_exists", "row_absent", "row_status_changed", "field_value_equals", "postcondition"}
    return bool((recovered_types - original_types) & structural_types)


def _terminal_evidence_types(result: Dict[str, Any]) -> set[str]:
    types: set[str] = set()
    effect = result.get("effect") if isinstance(result.get("effect"), dict) else {}
    if effect.get("terminal_evidence"):
        types.add(str(effect.get("terminal_evidence")).strip().lower())
    for item in effect.get("terminal_evidence_items") or []:
        if isinstance(item, dict) and item.get("type"):
            types.add(str(item.get("type")).strip().lower())
    signals = result.get("signals") if isinstance(result.get("signals"), dict) else {}
    for item in signals.get("terminal_evidence") or []:
        if isinstance(item, dict) and item.get("type"):
            types.add(str(item.get("type")).strip().lower())
    verification = result.get("terminal_verification") if isinstance(result.get("terminal_verification"), dict) else {}
    for item in verification.get("evidence") or []:
        if isinstance(item, dict) and item.get("type"):
            types.add(str(item.get("type")).strip().lower())
    return {item for item in types if item}


def _attach_semantic_terminal_judgement(result: Dict[str, Any], judgement: Dict[str, Any]) -> Dict[str, Any]:
    effect = dict(result.get("effect") or {})
    evidence = judgement.get("evidence") if isinstance(judgement.get("evidence"), list) else []
    first_type = str(evidence[0].get("type") if evidence else "semantic_terminal_judge")
    effect["terminal_evidence"] = first_type
    effect["terminal_evidence_items"] = evidence
    signals = dict(result.get("signals") or {})
    signals["semantic_terminal_judge"] = {
        "passed": True,
        "evidence": evidence,
        "reason": str(judgement.get("reason") or "").strip(),
    }
    return {**result, "effect": effect, "signals": signals}


def _normalize_semantic_terminal_judgement(value: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"passed": False, "evidence": [], "reason": "judge_output_not_object"}
    contract = normalize_terminal_contract(plan)
    desired_types = {
        str(item.get("type") or "").strip().lower()
        for item in contract.get("success_evidence") or []
        if str(item.get("type") or "").strip()
    }
    allowed_types = desired_types or {
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
    evidence = []
    raw_evidence = value.get("evidence")
    if isinstance(raw_evidence, dict):
        raw_evidence = [raw_evidence]
    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            evidence_type = str(item.get("type") or "").strip().lower()
            if evidence_type not in allowed_types:
                continue
            source = str(item.get("source") or "").strip().lower()
            if source not in {"snapshot", "browser", "download", "page"}:
                continue
            evidence.append(
                {
                    "type": evidence_type,
                    "source": source,
                    "summary": str(item.get("summary") or item.get("text") or "").strip()[:300],
                }
            )
    return {
        "passed": _normalize_bool(value.get("passed")) and bool(evidence),
        "evidence": evidence,
        "reason": str(value.get("reason") or "").strip()[:300],
    }


def _normalize_planner_object(parsed: Dict[str, Any]) -> Dict[str, Any]:
    parsed = dict(parsed)
    parsed.setdefault("action_type", "run_python")
    parsed["expected_effect"] = _normalize_expected_effect(parsed.get("expected_effect"))
    parsed["allow_empty_output"] = _normalize_bool(parsed.get("allow_empty_output"))
    parsed["input_bindings"] = _dict_field(parsed.get("input_bindings"))
    parsed["output_bindings"] = _dict_field(parsed.get("output_bindings"))
    parsed["postcondition"] = _dict_field(parsed.get("postcondition"))
    parsed["terminal_contract"] = normalize_terminal_contract(parsed)
    if parsed.get("action_type") == "run_python" and "async def run(page, results)" not in str(parsed.get("code") or ""):
        raise ValueError("Recording planner must return Python code defining async def run(page, results)")
    return parsed


def _looks_like_planner_object(parsed: Dict[str, Any]) -> bool:
    planner_keys = {
        "description",
        "action_type",
        "expected_effect",
        "effect",
        "allow_empty_output",
        "output_key",
        "code",
        "source",
        "section_title",
        "frame_path",
        "fields",
        "extraction",
        "input_bindings",
        "output_bindings",
        "postcondition",
        "terminal_contract",
    }
    return any(key in parsed for key in planner_keys)


def _model_config_summary(model_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(model_config, dict) or not model_config:
        return {}
    summary: Dict[str, Any] = {}
    for key in (
        "provider",
        "model_name",
        "base_url",
        "context_window",
        "id",
        "name",
        "requested_user_id",
        "selected_owner",
        "resolution_reason",
        "user_id",
    ):
        value = model_config.get(key)
        if value not in (None, ""):
            summary[key] = value
    return summary


def _build_planner_llm_request_summary(
    *,
    model: Any,
    messages: List[Any],
    model_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    message_summaries = []
    total_chars = 0
    include_prompt_preview = _planner_prompt_preview_enabled()
    for message in messages:
        content = str(getattr(message, "content", "") or "")
        total_chars += len(content)
        summary = {
            "type": type(message).__name__,
            "chars": len(content),
            "truncated": len(content) > 20000,
        }
        if include_prompt_preview:
            summary["preview"] = _truncate_text(content, 20000)
        message_summaries.append(summary)
    return {
        "configured_model": _model_config_summary(model_config),
        "effective_model": _effective_llm_model_summary(model),
        "message_count": len(messages),
        "total_message_chars": total_chars,
        "messages": message_summaries,
    }


def _planner_prompt_preview_enabled() -> bool:
    return str(os.getenv("RPA_LLM_DIAGNOSTIC_PROMPT_PREVIEW", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _effective_llm_model_summary(model: Any) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    attr_map = {
        "model_name": ("model_name", "model"),
        "base_url": ("openai_api_base", "base_url"),
        "max_tokens": ("max_tokens",),
        "temperature": ("temperature",),
        "streaming": ("streaming",),
        "request_timeout": ("request_timeout", "timeout"),
        "max_retries": ("max_retries",),
        "model_kwargs": ("model_kwargs",),
        "disabled_params": ("disabled_params",),
        "profile": ("profile",),
    }
    for key, candidates in attr_map.items():
        for attr in candidates:
            value = getattr(model, attr, None)
            if value not in (None, ""):
                summary[key] = _safe_jsonable(value)
                break
    return summary


def _text_diagnostic(text: Any, *, limit: int) -> Dict[str, Any]:
    value = str(text or "")
    return {
        "chars": len(value),
        "preview": _truncate_text(value, limit),
        "truncated": len(value) > limit,
    }


def _truncate_text(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit]


def _log_planner_llm_call(llm_call: Dict[str, Any]) -> None:
    request = llm_call.get("request") if isinstance(llm_call, dict) else {}
    response = llm_call.get("response") if isinstance(llm_call, dict) else {}
    effective_model = request.get("effective_model") if isinstance(request, dict) else {}
    logger.info(
        "[RPA-LLM] planner call model=%s base_url=%s max_tokens=%s profile=%s input_chars=%s output_chars=%s",
        effective_model.get("model_name"),
        effective_model.get("base_url"),
        effective_model.get("max_tokens"),
        effective_model.get("profile"),
        request.get("total_message_chars") if isinstance(request, dict) else None,
        response.get("chars") if isinstance(response, dict) else None,
    )


def _dict_field(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def _trusted_replay_postcondition(
    *,
    page: Any,
    plan: Dict[str, Any],
    result: Dict[str, Any],
    input_bindings: Dict[str, Any],
) -> Dict[str, Any]:
    candidate = _postcondition_candidate(plan, result)
    if not candidate:
        return {}
    signals = result.get("signals") if isinstance(result.get("signals"), dict) else {}
    allow_literal_key = bool(signals.get("recovered_attempt"))
    if not _postcondition_has_replay_key(candidate, input_bindings, allow_literal_key=allow_literal_key):
        return {}
    snapshot = await _safe_page_snapshot(page)
    return _validated_postcondition(
        candidate,
        snapshot=snapshot,
        input_bindings=input_bindings,
        allow_literal_key=allow_literal_key,
        result=result,
    )


def _postcondition_candidate(plan: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    signals = result.get("signals")
    if isinstance(signals, dict):
        signaled = _dict_field(signals.get("postcondition"))
        if signaled:
            return signaled
    return _dict_field(plan.get("postcondition"))


def _validated_postcondition(
    value: Any,
    *,
    snapshot: Optional[Dict[str, Any]] = None,
    input_bindings: Optional[Dict[str, Any]] = None,
    allow_literal_key: bool = False,
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    postcondition = _dict_field(value)
    if not postcondition:
        return {}
    source = str(postcondition.get("source") or postcondition.get("evidence_source") or "").strip().lower()
    observed = _normalize_bool(postcondition.get("observed"))
    if source not in {"observed", "snapshot", "structured_snapshot", "page"} and not observed:
        return {}
    kind = str(postcondition.get("kind") or "").strip()
    if kind not in {"table_row_exists", "table_row_absent"}:
        return {}
    input_bindings = input_bindings or {}
    if not _postcondition_has_replay_key(postcondition, input_bindings, allow_literal_key=allow_literal_key):
        return {}
    postcondition = _postcondition_with_supported_expect_values(postcondition, result, input_bindings)
    if snapshot is not None:
        row_exists = _snapshot_contains_postcondition_row(snapshot, postcondition, input_bindings)
        if kind == "table_row_exists" and not row_exists:
            return {}
        if kind == "table_row_absent" and row_exists:
            return {}
    return postcondition


def _postcondition_with_supported_expect_values(
    postcondition: Dict[str, Any],
    result: Optional[Dict[str, Any]],
    input_bindings: Dict[str, Any],
) -> Dict[str, Any]:
    expected = _dict_field(postcondition.get("expect"))
    if not expected:
        return postcondition
    supported: Dict[str, Any] = {}
    supported_values = _postcondition_supported_result_values(result)
    for header, raw_value in expected.items():
        label = _normalize_visible_text(header)
        if not label:
            continue
        ref = _postcondition_ref_name(raw_value)
        if ref and ref.split(".", 1)[0] in input_bindings:
            supported[header] = raw_value
            continue
        value = _normalize_visible_text(raw_value)
        if value and value.lower() in supported_values:
            supported[header] = raw_value
    if len(supported) == len(expected):
        return postcondition
    pruned = dict(postcondition)
    if supported:
        pruned["expect"] = supported
    else:
        pruned.pop("expect", None)
    pruned["expect_pruned"] = True
    return pruned


_UNSTRUCTURED_RESULT_TEXT_KEYS = {
    "text",
    "visible_text",
    "page_text",
    "body_text",
    "row_text",
    "observed_row_text",
    "message",
    "error",
    "traceback",
}


def _postcondition_supported_result_values(result: Optional[Dict[str, Any]]) -> set[str]:
    if not isinstance(result, dict):
        return set()
    payload = {
        "output": _safe_jsonable(result.get("output")),
        "effect": _safe_jsonable(result.get("effect")),
        "signals": _safe_jsonable(result.get("signals")),
        "terminal_verification": _safe_jsonable(result.get("terminal_verification")),
    }
    values: set[str] = set()
    _collect_structured_postcondition_values(payload, values)
    return values


def _collect_structured_postcondition_values(value: Any, values: set[str], parent_key: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key or "").strip().lower()
            if key_text in _UNSTRUCTURED_RESULT_TEXT_KEYS:
                continue
            _collect_structured_postcondition_values(item, values, key_text)
        return
    if isinstance(value, list):
        for item in value:
            _collect_structured_postcondition_values(item, values, parent_key)
        return
    text = _normalize_visible_text(value)
    if text and len(text) <= 120:
        values.add(text.lower())


_POSTCONDITION_REF_RE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\s*\}\}$")


def _postcondition_has_parameterized_key(postcondition: Dict[str, Any], input_bindings: Dict[str, Any]) -> bool:
    return _postcondition_has_replay_key(postcondition, input_bindings, allow_literal_key=False)


def _postcondition_has_replay_key(
    postcondition: Dict[str, Any],
    input_bindings: Dict[str, Any],
    *,
    allow_literal_key: bool,
) -> bool:
    key = postcondition.get("key")
    if not isinstance(key, dict) or not key:
        return False
    for raw_value in key.values():
        ref = _postcondition_ref_name(raw_value)
        if ref and ref.split(".", 1)[0] in input_bindings:
            return True
        if allow_literal_key and _normalize_visible_text(raw_value):
            return True
    return False


def _postcondition_ref_name(value: Any) -> str:
    match = _POSTCONDITION_REF_RE.match(str(value or "").strip())
    return match.group(1) if match else ""


def _snapshot_contains_postcondition_row(
    snapshot: Dict[str, Any],
    postcondition: Dict[str, Any],
    input_bindings: Dict[str, Any],
) -> bool:
    required_headers = _normalized_header_set(
        list(postcondition.get("table_headers") or [])
        + list((_dict_field(postcondition.get("key"))).keys())
        + list((_dict_field(postcondition.get("expect"))).keys())
    )
    key_values = _resolve_postcondition_values(_dict_field(postcondition.get("key")), input_bindings)
    expect_values = _resolve_postcondition_values(_dict_field(postcondition.get("expect")), input_bindings)
    if not required_headers or not key_values:
        return False
    for table in _iter_snapshot_tables(snapshot):
        headers = _normalized_header_set(table.get("headers") or [])
        if required_headers and not required_headers.issubset(headers):
            continue
        for row in table.get("rows") or []:
            if _row_matches_values(row, key_values) and _row_matches_values(row, expect_values):
                return True
    return False


def _resolve_postcondition_values(values: Dict[str, Any], input_bindings: Dict[str, Any]) -> Dict[str, str]:
    resolved: Dict[str, str] = {}
    for header, raw_value in values.items():
        label = _normalize_visible_text(header)
        if not label:
            continue
        ref = _postcondition_ref_name(raw_value)
        if ref:
            binding = input_bindings.get(ref.split(".", 1)[0])
            default = binding.get("default") if isinstance(binding, dict) else None
            value = _normalize_visible_text(default)
        else:
            value = _normalize_visible_text(raw_value)
        if value:
            resolved[label] = value
    return resolved


def _iter_snapshot_tables(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    for view in list(snapshot.get("table_views") or []):
        if not isinstance(view, dict):
            continue
        headers = [
            _normalize_visible_text(column.get("header"))
            for column in list(view.get("columns") or [])
            if isinstance(column, dict)
        ]
        rows = []
        for row in list(view.get("rows") or []):
            if not isinstance(row, dict):
                continue
            row_map: Dict[str, str] = {}
            for cell in list(row.get("cells") or []):
                if not isinstance(cell, dict):
                    continue
                header = _normalize_visible_text(cell.get("column_header"))
                text = _normalize_visible_text(cell.get("text"))
                if header and text:
                    row_map[header] = text
            if row_map:
                rows.append(row_map)
        tables.append({"headers": headers, "rows": rows})
    for region in list(snapshot.get("expanded_regions") or []):
        if not isinstance(region, dict) or str(region.get("kind") or "") != "table":
            continue
        evidence = region.get("evidence") if isinstance(region.get("evidence"), dict) else {}
        headers = [_normalize_visible_text(item) for item in list(evidence.get("headers") or [])]
        rows = []
        for row in list(evidence.get("sample_rows") or []):
            if isinstance(row, dict):
                row_map = {
                    _normalize_visible_text(key): _normalize_visible_text(value)
                    for key, value in row.items()
                    if _normalize_visible_text(key) and _normalize_visible_text(value)
                }
                if row_map:
                    rows.append(row_map)
        tables.append({"headers": headers, "rows": rows})
    return tables


def _normalized_header_set(headers: List[Any]) -> set[str]:
    return {_normalize_visible_text(header) for header in headers if _normalize_visible_text(header)}


def _row_matches_values(row: Dict[str, str], expected: Dict[str, str]) -> bool:
    for header, value in expected.items():
        cell = _normalize_visible_text(row.get(header))
        if cell != value:
            return False
    return True


def _json_object_candidates(raw: str) -> List[str]:
    candidates: List[str] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE):
        candidate = str(match.group(1) or "").strip()
        if candidate:
            candidates.append(candidate)
    candidates.append(raw)
    return candidates


def _build_detail_extract_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _instruction_is_detail_extract_only(instruction):
        return None

    all_fields: List[Dict[str, Any]] = []
    section_titles: List[str] = []
    seen: set[tuple[str, str]] = set()
    for detail in list(snapshot.get("detail_views") or []):
        section_title = str(detail.get("section_title") or "").strip()
        if section_title:
            section_titles.append(section_title)
        for field in list(detail.get("fields") or []):
            label = str(field.get("label") or "").strip()
            if not label:
                continue
            visible = bool(field.get("visible", True))
            if not visible:
                continue
            value = field.get("value")
            if value in (None, ""):
                continue
            dedupe_key = (label, str(value))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            all_fields.append(
                {
                    "label": label,
                    "value": value,
                    "visible": visible,
                    "data_prop": str(field.get("data_prop") or "").strip(),
                    "value_kind": str(field.get("value_kind") or "").strip(),
                    "field_locator": dict(field.get("field_locator") or {}),
                    "label_locator": dict(field.get("label_locator") or {}),
                    "value_locator": dict(field.get("value_locator") or {}),
                    "locator_hints": list(field.get("locator_hints") or [])[:3],
                    "adapter": str(field.get("adapter") or detail.get("framework_hint") or "").strip(),
                    "value_selector": str(field.get("value_selector") or "").strip(),
                    "value_selectors": list(field.get("value_selectors") or [])[:6],
                    "replay_required": True,
                }
            )
    if not all_fields:
        return None
    return {
        "description": "Extract visible detail fields from the current page snapshot",
        "action_type": "extract_snapshot",
        "expected_effect": "extract",
        "allow_empty_output": False,
        "output_key": "detail_fields",
        "source": "detail_views",
        "section_title": " / ".join(section_titles[:3]),
        "frame_path": [],
        "fields": all_fields[:40],
    }


def _instruction_is_detail_extract_only(instruction: str) -> bool:
    text = str(instruction or "").strip().lower()
    if not text:
        return False
    if _contains_any(
        text,
        (
            "create",
            "submit",
            "save",
            "generate",
            "download",
            "fill",
            "type",
            "open",
            "click",
            "filter",
            "search",
            "query",
            "navigate",
            "go to",
            "新建",
            "创建",
            "提交",
            "保存",
            "生成",
            "下载",
            "填写",
            "填入",
            "打开",
            "点击",
            "筛选",
            "搜索",
            "查询",
            "进入",
            "导航",
        ),
    ):
        return False
    return _contains_any(
        text,
        (
            "extract",
            "collect",
            "read",
            "return",
            "summarize",
            "字段",
            "提取",
            "抽取",
            "读取",
            "收集",
            "返回",
        ),
    )


def _build_preplanned_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    plan = build_download_preplanned_plan(instruction, snapshot)
    if plan:
        plan.setdefault("preplanned_source", "download_snapshot")
        return plan
    plan = build_modal_form_preplanned_plan(instruction, snapshot)
    if plan:
        plan.setdefault("preplanned_source", "modal_snapshot")
        return plan
    plan = build_search_preplanned_plan(instruction, snapshot)
    if plan:
        plan.setdefault("preplanned_source", "search_snapshot")
        return plan
    plan = build_ordinal_preplanned_plan(instruction, snapshot)
    if plan:
        return plan
    plan = _build_detail_extract_plan(instruction, snapshot)
    if plan:
        plan = dict(plan)
        plan.setdefault("preplanned_source", "detail_snapshot")
        return plan
    return None


def _build_read_only_preplanned_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Backward-compatible helper for callers that only want extraction preplans."""
    plan = _build_preplanned_plan(instruction, snapshot)
    if plan and _normalize_expected_effect(plan.get("expected_effect")) == "extract":
        return plan
    return None


def _normalize_generated_playwright_code(code: str) -> str:
    return normalize_generated_playwright_code(code)


def _combine_run_python_attempts(
    failed_plans: List[Dict[str, Any]],
    repair_plan: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    repair_code = str(repair_plan.get("code") or "").strip()
    if not _has_run_function(repair_code):
        return None
    precondition_codes: List[str] = []
    precondition_calls: List[str] = []
    for plan in failed_plans:
        if str(plan.get("action_type") or "").strip() != "run_python":
            continue
        code = str(plan.get("code") or "").strip()
        if not _has_run_function(code):
            continue
        precondition_codes.append(code)
        precondition_calls.extend(
            [
                "    try:",
                f"        await _rpa_run_isolated(_RPA_PRECONDITION_CODES[{len(precondition_codes) - 1}], page, results)",
                "    except Exception as _rpa_precondition_error:",
                "        results.setdefault('_rpa_precondition_errors', []).append(str(_rpa_precondition_error))",
            ]
        )
    if not precondition_calls:
        return None
    combined_code = "\n\n".join(
        [
            f"_RPA_PRECONDITION_CODES = {precondition_codes!r}",
            f"_RPA_REPAIR_CODE = {repair_code!r}",
            "",
            "async def _rpa_run_isolated(code, page, results):",
            "    namespace = {}",
            "    exec(compile(code, '<rpa_combined_attempt>', 'exec'), namespace, namespace)",
            "    runner = namespace.get('run')",
            "    if not callable(runner):",
            "        raise RuntimeError('Combined RPA attempt is missing async run(page, results)')",
            "    return await runner(page, results)",
            "",
            "async def run(page, results):",
            *precondition_calls,
            "    return await _rpa_run_isolated(_RPA_REPAIR_CODE, page, results)",
            "",
        ]
    )
    return {
        **repair_plan,
        "description": str(repair_plan.get("description") or "Repaired browser action with replayable preconditions"),
        "code": combined_code,
    }


def _has_run_function(code: str) -> bool:
    return bool(re.search(r"(?m)^\s*async\s+def\s+run\s*\(\s*page\s*,\s*results\s*\)\s*:", str(code or "")))

def _is_terminal_contract_failure_result(result: Dict[str, Any]) -> bool:
    terminal = result.get("terminal_verification")
    return isinstance(terminal, dict) and terminal.get("required") is True and terminal.get("passed") is False


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


async def _settle_after_browser_action(page: Any, timeout_ms: int = 200) -> None:
    wait_for_timeout = getattr(page, "wait_for_timeout", None)
    if not callable(wait_for_timeout):
        await asyncio.sleep(timeout_ms / 1000)
        return
    try:
        result = wait_for_timeout(timeout_ms)
        if inspect.isawaitable(result):
            await result
    except Exception:
        await asyncio.sleep(timeout_ms / 1000)


async def _page_state(page: Any) -> RPAPageState:
    title = ""
    title_fn = getattr(page, "title", None)
    if callable(title_fn):
        try:
            value = title_fn()
            if inspect.isawaitable(value):
                value = await value
            title = str(value or "")
        except Exception:
            title = ""
    return RPAPageState(url=str(getattr(page, "url", "") or ""), title=title)


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


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

