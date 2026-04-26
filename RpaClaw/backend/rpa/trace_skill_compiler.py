from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from backend.rpa.playwright_security import get_chromium_launch_kwargs, get_context_kwargs

from .trace_locator_utils import has_valid_locator, normalize_locator
from .trace_models import RPAAcceptedTrace, RPATraceType


_EXACT_DEFAULT_METHODS = {"role", "label", "placeholder", "alt", "title", "text"}


class TraceSkillCompiler:
    def generate_script(
        self,
        traces: Iterable[RPAAcceptedTrace],
        params: Optional[Dict[str, Any]] = None,
        *,
        is_local: bool = False,
        test_mode: bool = False,
    ) -> str:
        self._compiled_output_keys: Dict[int, str] = {}
        self._param_lookup = self._build_param_lookup(params or {})
        self._param_cursors: Dict[str, int] = {}
        trace_list = self._normalize_redundant_navigation_traces(
            self._normalize_download_traces(list(traces))
        )
        execute_skill_func = "\n".join(self._render_execute_skill(trace_list))
        return _runner_template(is_local).format(
            execute_skill_func=execute_skill_func,
            launch_kwargs=repr(get_chromium_launch_kwargs(headless=False)),
            context_kwargs=repr(get_context_kwargs()),
        )

    @classmethod
    def _normalize_download_traces(cls, traces: List[RPAAcceptedTrace]) -> List[RPAAcceptedTrace]:
        normalized: List[RPAAcceptedTrace] = []
        for trace in traces:
            if cls._is_standalone_download_trace(trace) and normalized:
                previous = normalized[-1]
                if cls._can_attach_download_signal(previous):
                    previous = previous.model_copy(deep=True)
                    signals = dict(previous.signals or {})
                    download_signal = dict(signals.get("download") or {})
                    filename = str(trace.value or "").strip()
                    if filename:
                        download_signal.setdefault("filename", filename)
                    for key, value in (trace.signals or {}).items():
                        if key == "download" and isinstance(value, dict):
                            for download_key, download_value in value.items():
                                if download_value is not None:
                                    download_signal.setdefault(download_key, download_value)
                        elif value is not None:
                            download_signal.setdefault(key, value)
                    cls._classify_download_signal(previous, download_signal)
                    signals["download"] = download_signal
                    previous.signals = signals
                    normalized[-1] = previous
                    continue
            normalized.append(trace)
        return normalized

    @staticmethod
    def _is_standalone_download_trace(trace: RPAAcceptedTrace) -> bool:
        return trace.trace_type == RPATraceType.MANUAL_ACTION and str(trace.action or "") == "download"

    @staticmethod
    def _can_attach_download_signal(trace: RPAAcceptedTrace) -> bool:
        if trace.trace_type == RPATraceType.AI_OPERATION:
            return bool(trace.ai_execution and trace.ai_execution.code)
        if trace.trace_type != RPATraceType.MANUAL_ACTION:
            return False
        return str(trace.action or "") in {"click", "press", "navigate_click", "navigate_press"}

    @classmethod
    def _classify_download_signal(cls, trace: RPAAcceptedTrace, download_signal: Dict[str, Any]) -> None:
        if download_signal.get("trigger_mode"):
            return
        code = str(trace.ai_execution.code or "") if trace.ai_execution else ""
        if trace.trace_type == RPATraceType.AI_OPERATION and cls._looks_like_export_task_download_code(code):
            download_signal["trigger_mode"] = "export_task"

    @staticmethod
    def _looks_like_export_task_download_code(code: str) -> bool:
        text = str(code or "")
        return (
            "tbody tr" in text
            and "td[data-colid=" in text
            and ".locator(" in text
            and ".click(" in text
        )

    @classmethod
    def _normalize_redundant_navigation_traces(cls, traces: List[RPAAcceptedTrace]) -> List[RPAAcceptedTrace]:
        normalized: List[RPAAcceptedTrace] = []
        for trace in traces:
            if trace.trace_type == RPATraceType.NAVIGATION and normalized:
                previous_url = cls._normalized_url(normalized[-1].after_page.url)
                current_url = cls._normalized_url(trace.after_page.url or str(trace.value or ""))
                if previous_url and current_url and previous_url == current_url:
                    continue
            normalized.append(trace)
        return normalized

    @staticmethod
    def _normalized_url(url: str) -> str:
        return str(url or "").strip().rstrip("/")

    def _render_execute_skill(self, traces: List[RPAAcceptedTrace]) -> List[str]:
        lines = [
            "",
            "def _resolve_result_ref(results, ref):",
            "    current = results",
            "    for segment in str(ref).split('.'):",
            "        if isinstance(current, dict) and segment in current:",
            "            current = current[segment]",
            "            continue",
            "        if isinstance(current, list) and segment.isdigit():",
            "            current = current[int(segment)]",
            "            continue",
            "        raise KeyError(ref)",
            "    return current",
            "",
            "def _resolve_first_result_ref(results, refs):",
            "    last_error = None",
            "    for ref in refs:",
            "        try:",
            "            return _resolve_result_ref(results, ref)",
            "        except KeyError as exc:",
            "            last_error = exc",
            "    raise last_error or KeyError(refs[0] if refs else '')",
            "",
            "def _validate_non_empty_records(key, value):",
            "    if not isinstance(value, list) or not value:",
            "        raise RuntimeError(f'AI trace output {key} is empty')",
            "",
            "async def _download_from_export_task(page, kwargs, results, download_key, *, table_heading='', action_selector='a', row_index=0, timeout_ms=60000):",
            "    import os as _os",
            "    _dl_dir = kwargs.get('_downloads_dir', '.')",
            "    _os.makedirs(_dl_dir, exist_ok=True)",
            "    deadline = time.perf_counter() + (timeout_ms / 1000)",
            "    last_error = None",
            "    while time.perf_counter() < deadline:",
            "        try:",
            "            if table_heading:",
            "                heading = page.get_by_text(table_heading, exact=True).first",
            "                if await heading.count():",
            "                    rows = heading.locator(\"xpath=following::table[.//tbody/tr][1]//tbody/tr\")",
            "                else:",
            "                    rows = page.locator('tbody tr')",
            "            else:",
            "                rows = page.locator('tbody tr')",
            "            if await rows.count() <= row_index:",
            "                await page.wait_for_timeout(1000)",
            "                continue",
            "            row = rows.nth(row_index)",
            "            action = row.locator(action_selector).first",
            "            if not await action.count() or not await action.is_visible() or not await action.is_enabled():",
            "                await page.wait_for_timeout(1000)",
            "                continue",
            "            async with page.expect_download(timeout=3000) as _dl_info:",
            "                await action.click()",
            "            _dl = await _dl_info.value",
            "            _dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)",
            "            await _dl.save_as(_dl_dest)",
            "            return {\"filename\": _dl.suggested_filename, \"path\": _dl_dest}",
            "        except Exception as exc:",
            "            last_error = exc",
            "            await page.wait_for_timeout(1000)",
            "    detail = f': {last_error}' if last_error else ''",
            "    raise RuntimeError(f'Export task download did not produce a file within {timeout_ms}ms{detail}')",
            "",
            "def _trace_page_url(page):",
            "    try:",
            "        return str(getattr(page, 'url', '') or '')",
            "    except Exception:",
            "        return ''",
            "",
            "def _trace_emit(logger, event, index, description, page, started_at=None, error=None):",
            "    if not callable(logger):",
            "        return",
            "    prefix = {'START': 'TRACE_START', 'DONE': 'TRACE_DONE', 'ERROR': 'TRACE_ERROR'}.get(event, f'TRACE_{event}')",
            "    parts = [f'{prefix} {index}: {description}']",
            "    if started_at is not None:",
            "        parts.append(f'duration_ms={(time.perf_counter() - started_at) * 1000:.1f}')",
            "    page_url = _trace_page_url(page)",
            "    if page_url:",
            "        parts.append(f'url={page_url}')",
            "    if error is not None:",
            "        message = str(error).replace('\\n', ' ')[:300]",
            "        parts.append(f'error={type(error).__name__}: {message}')",
            "    try:",
            "        logger(' | '.join(parts))",
            "    except Exception:",
            "        pass",
            "",
            "def _trace_start(logger, index, description, page):",
            "    started_at = time.perf_counter()",
            "    _trace_emit(logger, 'START', index, description, page)",
            "    return started_at",
            "",
            "def _trace_done(logger, index, description, page, started_at):",
            "    _trace_emit(logger, 'DONE', index, description, page, started_at)",
            "",
            "def _trace_error(logger, index, description, page, started_at, error):",
            "    _trace_emit(logger, 'ERROR', index, description, page, started_at, error)",
            "",
            "def _normalize_runtime_ai_payload(payload, page_url=''):",
            "    if isinstance(payload, dict) and len(payload) == 1:",
            "        only_value = next(iter(payload.values()))",
            "        if isinstance(only_value, dict):",
            "            payload = only_value",
            "    if isinstance(payload, str):",
            "        payload = {'value': payload}",
            "    if not isinstance(payload, dict):",
            "        payload = {'value': payload}",
            "    value = payload.get('value')",
            "    if 'url' not in payload and isinstance(value, str) and value.startswith(('http://', 'https://')):",
            "        payload['url'] = value",
            "    if 'url' not in payload and page_url:",
            "        payload['url'] = page_url",
            "    return payload",
            "",
            "async def _execute_runtime_ai_instruction(page, results, instruction, output_key):",
            "    from backend.rpa.recording_runtime_agent import RecordingRuntimeAgent",
            "    agent = RecordingRuntimeAgent()",
            "    outcome = await agent.run(page=page, instruction=instruction, runtime_results=results)",
            "    if not outcome.success:",
            "        detail = '; '.join(str(item.message) for item in outcome.diagnostics) or outcome.message",
            "        raise RuntimeError(f'Runtime semantic instruction failed: {detail}')",
            "    payload = outcome.output",
            "    if isinstance(payload, dict) and output_key in payload and isinstance(payload.get(output_key), (dict, list, str)):",
            "        payload = payload.get(output_key)",
            "    payload = _normalize_runtime_ai_payload(payload, getattr(page, 'url', ''))",
            "    if outcome.output_key and outcome.output_key not in results:",
            "        results[outcome.output_key] = payload",
            "    if output_key:",
            "        results[output_key] = payload",
            "    return payload",
            "",
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA trace recording."""',
            "    _results = {}",
            "    current_page = page",
            "    tabs = {}",
            "    _trace_logger = kwargs.get('_on_log')",
        ]
        used_output_keys: Dict[str, int] = {}
        for index, trace in enumerate(traces):
            trace_lines = self._render_trace(index, trace, traces[:index], used_output_keys)
            lines.extend(self._wrap_trace_logging(index, trace, trace_lines))
        lines.append("    return _results")
        return lines

    def _wrap_trace_logging(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        trace_lines: List[str],
    ) -> List[str]:
        description = self._trace_log_description(trace)
        wrapped = [
            "",
            f"    _trace_started_at = _trace_start(_trace_logger, {index}, {description!r}, current_page)",
            "    try:",
        ]
        for line in trace_lines:
            wrapped.append(f"    {line}" if line else "")
        wrapped.extend(
            [
                "    except Exception as _trace_exc:",
                f"        _trace_error(_trace_logger, {index}, {description!r}, current_page, _trace_started_at, _trace_exc)",
                "        raise",
                "    else:",
                f"        _trace_done(_trace_logger, {index}, {description!r}, current_page, _trace_started_at)",
            ]
        )
        return wrapped

    @staticmethod
    def _trace_log_description(trace: RPAAcceptedTrace) -> str:
        text = trace.description or trace.user_instruction or trace.action or trace.trace_type.value
        return " ".join(str(text or "").split())[:160]

    def _render_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        previous_traces: List[RPAAcceptedTrace],
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        if trace.trace_type == RPATraceType.NAVIGATION:
            return self._render_navigation_trace(index, trace, previous_traces)
        if trace.trace_type == RPATraceType.DATAFLOW_FILL and trace.dataflow:
            return self._render_dataflow_fill_trace(index, trace)
        if trace.trace_type == RPATraceType.MANUAL_ACTION:
            return self._render_manual_action_trace(index, trace, previous_traces)
        if trace.trace_type == RPATraceType.DATA_CAPTURE:
            return self._render_data_capture_trace(index, trace, used_output_keys)
        if trace.trace_type == RPATraceType.AI_OPERATION:
            return self._render_ai_operation_trace(index, trace, previous_traces, used_output_keys)
        return ["", f"    # trace {index}: unsupported trace type {trace.trace_type.value}"]

    def _render_navigation_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        previous_traces: List[RPAAcceptedTrace],
    ) -> List[str]:
        url = trace.after_page.url or str(trace.value or "")
        dynamic = self._dynamic_url_expression(url, previous_traces)
        lines = ["", f"    # trace {index}: {trace.description or 'navigation'}"]
        if dynamic:
            lines.append(f"    _target_url = {dynamic}")
        else:
            lines.append(f"    _target_url = {url!r}")
        lines.extend(
            [
                "    await current_page.goto(_target_url, wait_until='domcontentloaded')",
                "    await current_page.wait_for_load_state('domcontentloaded')",
            ]
        )
        return lines

    def _render_manual_action_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        previous_traces: List[RPAAcceptedTrace],
    ) -> List[str]:
        action = self._effective_manual_action(trace)
        locator = self._preferred_locator_for_trace(trace, trace.locator_candidates)
        lines = ["", f"    # trace {index}: {trace.description or action}"]
        if action in {"navigate_click", "navigate_press"}:
            if not locator:
                lines.extend(self._invalid_manual_action_lines(action))
                return lines
            scope_lines, scope_var = self._frame_scope_lines(trace.frame_path)
            lines.extend(scope_lines)
            expr = _locator_expression(scope_var, locator)
            lines.append("    async with current_page.expect_navigation(wait_until='domcontentloaded'):")
            if action == "navigate_click":
                lines.append(f"        await {expr}.click()")
            else:
                lines.append(f"        await {expr}.press({str(trace.value or '')!r})")
            lines.append("    await current_page.wait_for_load_state('domcontentloaded')")
            return lines
        if action == "switch_tab":
            lines.extend(self._render_switch_tab_trace(trace))
            return lines
        if action == "close_tab":
            lines.extend(self._render_close_tab_trace(trace))
            return lines
        if not locator and action in {"hover", "click", "fill", "press", "check", "uncheck", "select"}:
            lines.extend(self._invalid_manual_action_lines(action))
            return lines
        if not locator:
            lines.append("    # No stable locator was recorded for this manual action.")
            return lines
        scope_lines, scope_var = self._frame_scope_lines(trace.frame_path)
        lines.extend(scope_lines)
        expr = _locator_expression(scope_var, locator)
        popup_signal = _trace_signal(trace, "popup")
        download_signal = _trace_signal(trace, "download")
        if action in {"click", "press"} and (popup_signal or download_signal):
            lines.extend(
                self._render_side_effect_interaction(
                    action=action,
                    expr=expr,
                    value=str(trace.value or ""),
                    popup_signal=popup_signal,
                    download_signal=download_signal,
                )
            )
            return lines
        if action == "hover":
            lines.append(f"    await {expr}.hover()")
        elif action == "click":
            lines.append(f"    await {expr}.click()")
            lines.append("    await current_page.wait_for_timeout(500)")
        elif action == "fill":
            fill_value = self._maybe_parameterize_value(str(trace.value or ""))
            lines.append(f"    await {expr}.fill({fill_value})")
        elif action == "press":
            lines.append(f"    await {expr}.press({str(trace.value or '')!r})")
        elif action == "check":
            lines.append(f"    await {expr}.check()")
        elif action == "uncheck":
            lines.append(f"    await {expr}.uncheck()")
        elif action == "select":
            lines.append(f"    await {expr}.select_option({str(trace.value or '')!r})")
        else:
            lines.append(f"    # Unsupported manual action preserved as no-op: {action}")
        return lines

    @staticmethod
    def _render_switch_tab_trace(trace: RPAAcceptedTrace) -> List[str]:
        tab_signal = _trace_signal(trace, "tab")
        source_tab_id = str(tab_signal.get("source_tab_id") or tab_signal.get("tab_id") or "").strip()
        target_tab_id = str(tab_signal.get("target_tab_id") or "").strip()
        if not target_tab_id:
            return ["    # Switch tab trace is missing target_tab_id."]

        lines: List[str] = []
        if source_tab_id:
            lines.append(f"    tabs.setdefault({json.dumps(source_tab_id, ensure_ascii=False)}, current_page)")
        lines.append(f"    current_page = tabs[{json.dumps(target_tab_id, ensure_ascii=False)}]")
        lines.append("    await current_page.bring_to_front()")
        return lines

    @staticmethod
    def _render_close_tab_trace(trace: RPAAcceptedTrace) -> List[str]:
        tab_signal = _trace_signal(trace, "tab")
        closing_tab_id = str(
            tab_signal.get("tab_id")
            or tab_signal.get("source_tab_id")
            or ""
        ).strip()
        fallback_tab_id = str(tab_signal.get("target_tab_id") or "").strip()

        lines: List[str] = []
        if closing_tab_id:
            lines.append(f"    tabs.setdefault({json.dumps(closing_tab_id, ensure_ascii=False)}, current_page)")
            lines.append(f"    closing_page = tabs.pop({json.dumps(closing_tab_id, ensure_ascii=False)}, current_page)")
        else:
            lines.append("    closing_page = current_page")
        lines.append("    await closing_page.close()")
        if fallback_tab_id:
            lines.append(f"    current_page = tabs[{json.dumps(fallback_tab_id, ensure_ascii=False)}]")
            lines.append("    await current_page.bring_to_front()")
        return lines

    @staticmethod
    def _render_side_effect_interaction(
        *,
        action: str,
        expr: str,
        value: str,
        popup_signal: Dict[str, Any],
        download_signal: Dict[str, Any],
    ) -> List[str]:
        lines: List[str] = []
        interaction = f"await {expr}.click()" if action == "click" else f"await {expr}.press({value!r})"
        outer_indent = "    "
        if download_signal:
            lines.append(f"{outer_indent}async with current_page.expect_download() as _dl_info:")
            outer_indent += "    "
        if popup_signal:
            source_tab_id = str(popup_signal.get("source_tab_id") or "").strip()
            if source_tab_id:
                lines.append(f"{outer_indent}tabs.setdefault({json.dumps(source_tab_id, ensure_ascii=False)}, current_page)")
            lines.append(f"{outer_indent}async with current_page.expect_popup() as popup_info:")
            outer_indent += "    "
        lines.append(f"{outer_indent}{interaction}")

        if popup_signal:
            popup_indent = "    " + ("    " if download_signal else "")
            target_tab_id = str(popup_signal.get("target_tab_id") or "tab-new")
            lines.append(f"{popup_indent}new_page = await popup_info.value")
            lines.append(f"{popup_indent}tabs[{json.dumps(target_tab_id, ensure_ascii=False)}] = new_page")
            lines.append(f"{popup_indent}current_page = new_page")

        if download_signal:
            download_name = str(download_signal.get("filename") or value or "file")
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", download_name.split(".")[0]) or "file"
            lines.extend(
                [
                    "    _dl = await _dl_info.value",
                    "    _dl_dir = kwargs.get('_downloads_dir', '.')",
                    "    import os as _os; _os.makedirs(_dl_dir, exist_ok=True)",
                    "    _dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)",
                    "    await _dl.save_as(_dl_dest)",
                    f"    _results[{json.dumps('download_' + safe_name, ensure_ascii=False)}] = {{\"filename\": _dl.suggested_filename, \"path\": _dl_dest}}",
                ]
            )
        lines.append("    await current_page.wait_for_timeout(500)")
        return lines

    def _render_data_capture_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        locator = self._preferred_locator_for_trace(trace, trace.locator_candidates)
        key = self._allocate_output_key(trace, trace.output_key or f"capture_{index}", used_output_keys)
        lines = ["", f"    # trace {index}: {trace.description or 'data capture'}"]
        if locator:
            scope_lines, scope_var = self._frame_scope_lines(trace.frame_path)
            lines.extend(scope_lines)
            lines.append(f"    _result = await {_locator_expression(scope_var, locator)}.inner_text()")
        else:
            lines.append(f"    _result = {trace.output!r}")
        lines.append(f"    _results[{key!r}] = _result")
        return lines

    def _render_ai_operation_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        previous_traces: List[RPAAcceptedTrace],
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        if _should_preserve_runtime_ai_instruction(trace):
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        if trace.ai_execution and trace.ai_execution.code:
            return self._render_embedded_ai_code_trace(index, trace, previous_traces, used_output_keys)
        if trace.user_instruction or trace.description:
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        return ["", f"    # trace {index}: AI operation has no executable body"]

    def _render_runtime_ai_instruction_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        key = self._allocate_output_key(trace, trace.output_key or f"ai_result_{index}", used_output_keys)
        instruction = str(trace.user_instruction or trace.description or "").strip()
        return [
            "",
            f"    # trace {index}: runtime semantic instruction",
            f"    _result = await _execute_runtime_ai_instruction(current_page, _results, {instruction!r}, {key!r})",
        ]

    @staticmethod
    def _build_param_lookup(params: Dict[str, Any]) -> Dict[str, List[tuple[str, Dict[str, Any]]]]:
        lookup: Dict[str, List[tuple[str, Dict[str, Any]]]] = {}
        for param_name, param_info in params.items():
            if not isinstance(param_info, dict):
                continue
            original = param_info.get("original_value")
            if original is None:
                continue
            lookup.setdefault(str(original), []).append((str(param_name), param_info))
        return lookup

    def _maybe_parameterize_value(self, value: str) -> str:
        candidates = self._param_lookup.get(value) or []
        if not candidates:
            return repr(value)

        if len(candidates) == 1:
            param_name, param_info = candidates[0]
        else:
            cursor = self._param_cursors.get(value, 0)
            param_name, param_info = candidates[min(cursor, len(candidates) - 1)]
            self._param_cursors[value] = cursor + 1

        if param_info.get("sensitive"):
            return f"kwargs[{param_name!r}]"
        return f"kwargs.get({param_name!r}, {value!r})"

    def _render_embedded_ai_code_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        previous_traces: List[RPAAcceptedTrace],
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        key = self._allocate_output_key(trace, trace.output_key, used_output_keys) if trace.output_key else ""
        code = self._rewrite_dynamic_urls_in_code(
            (trace.ai_execution.code if trace.ai_execution else "").strip(),
            previous_traces,
        )
        code = _rewrite_random_like_locator_in_code(code, trace)
        download_signal = _trace_signal(trace, "download")
        if download_signal:
            self._classify_download_signal(trace, download_signal)
        code_handles_download = "expect_download" in code or ".save_as(" in code
        lines = ["", f"    # trace {index}: {trace.description or 'AI operation'}"]
        for code_line in code.splitlines():
            lines.append(f"    {code_line}" if code_line.strip() else "")
        if download_signal and self._download_trigger_mode(download_signal) == "export_task":
            download_name = str(download_signal.get("filename") or "file")
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", download_name.split(".")[0]) or "file"
            download_key = "download_" + safe_name
            heading, action_selector = self._export_task_download_hints(code)
            lines.append(
                "    _download_payload = await _download_from_export_task("
                "current_page, kwargs, _results, "
                f"{json.dumps(download_key, ensure_ascii=False)}, "
                f"table_heading={heading!r}, "
                f"action_selector={action_selector!r})"
            )
            lines.append(f"    _results[{json.dumps(download_key, ensure_ascii=False)}] = _download_payload")
            lines.append("    _result = {'action_performed': True, 'downloaded': True}")
        elif download_signal and not code_handles_download:
            download_name = str(download_signal.get("filename") or "file")
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", download_name.split(".")[0]) or "file"
            download_key = "download_" + safe_name
            lines.append("    async with current_page.expect_download() as _dl_info:")
            lines.append("        _result = await run(current_page, _results)")
            lines.extend(
                [
                    "    _dl = await _dl_info.value",
                    "    _dl_dir = kwargs.get('_downloads_dir', '.')",
                    "    import os as _os; _os.makedirs(_dl_dir, exist_ok=True)",
                    "    _dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)",
                    "    await _dl.save_as(_dl_dest)",
                    f"    _results[{json.dumps(download_key, ensure_ascii=False)}] = {{\"filename\": _dl.suggested_filename, \"path\": _dl_dest}}",
                ]
            )
        else:
            download_key = ""
            lines.append("    _result = await run(current_page, _results)")
        if key and key != download_key:
            lines.append(f"    _results[{key!r}] = _result")
        return lines

    @staticmethod
    def _download_trigger_mode(download_signal: Dict[str, Any]) -> str:
        return str(download_signal.get("trigger_mode") or "immediate").strip().lower()

    @staticmethod
    def _export_task_download_hints(code: str) -> tuple[str, str]:
        heading = ""
        heading_match = re.search(r"get_by_text\((['\"])(.*?)\1,\s*exact=True\)", code)
        if heading_match:
            heading = heading_match.group(2)

        action_selector = "a"
        selector_match = re.search(r"\.locator\((['\"])(td\[data-colid=.*?)\1\)\.click\(", code)
        if selector_match:
            action_selector = selector_match.group(2)
        return heading, action_selector

    def _render_dataflow_fill_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        ref = trace.dataflow.selected_source_ref if trace.dataflow else None
        locator = self._preferred_locator_for_trace(
            trace,
            trace.dataflow.target_field.locator_candidates if trace.dataflow else [],
        )
        lines = ["", f"    # trace {index}: dataflow fill {ref or ''}"]
        if not ref or not locator:
            lines.append("    # Unresolved dataflow fill skipped.")
            return lines
        scope_lines, scope_var = self._frame_scope_lines(trace.frame_path)
        lines.extend(scope_lines)
        lines.append(f"    _value = _resolve_result_ref(_results, {ref!r})")
        lines.append(f"    await {_locator_expression(scope_var, locator)}.fill(str(_value))")
        return lines

    def _best_locator(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not candidates:
            return {}
        selected = next((item for item in candidates if item.get("selected")), candidates[0])
        locator = selected.get("locator") if isinstance(selected, dict) else None
        normalized = normalize_locator(locator if isinstance(locator, dict) else selected)
        return normalized if has_valid_locator(normalized) else {}

    def _preferred_locator_for_trace(self, trace: RPAAcceptedTrace, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        locator = self._best_locator(candidates)
        if not locator:
            return {}
        if trace.source == "ai":
            return locator
        if trace.trace_type not in {
            RPATraceType.MANUAL_ACTION,
            RPATraceType.DATAFLOW_FILL,
            RPATraceType.DATA_CAPTURE,
        }:
            return locator
        return self._apply_exact_defaults(locator)

    def _apply_exact_defaults(self, locator: Dict[str, Any]) -> Dict[str, Any]:
        method = locator.get("method")
        normalized = dict(locator)
        if method == "nested":
            parent = locator.get("parent")
            child = locator.get("child")
            if isinstance(parent, dict):
                normalized["parent"] = self._apply_exact_defaults(parent)
            if isinstance(child, dict):
                normalized["child"] = self._apply_exact_defaults(child)
            return normalized
        if method == "nth":
            base = locator.get("locator") or locator.get("base")
            if isinstance(base, dict):
                normalized["locator"] = self._apply_exact_defaults(base)
                normalized.pop("base", None)
            return normalized
        if method in _EXACT_DEFAULT_METHODS and normalized.get("exact") is None:
            normalized["exact"] = True
        return normalized

    @staticmethod
    def _frame_scope_lines(frame_path: List[str]) -> tuple[List[str], str]:
        if not frame_path:
            return [], "current_page"
        lines: List[str] = []
        frame_parent = "current_page"
        for frame_selector in frame_path:
            lines.append(
                f"    frame_scope = {frame_parent}.frame_locator({json.dumps(str(frame_selector), ensure_ascii=False)})"
            )
            frame_parent = "frame_scope"
        return lines, "frame_scope"

    def _effective_manual_action(self, trace: RPAAcceptedTrace) -> str:
        action = trace.action or ""
        if action in {"click", "press"}:
            navigation_signal = trace.signals.get("navigation") if isinstance(trace.signals, dict) else None
            if isinstance(navigation_signal, dict) and str(navigation_signal.get("url") or "").strip():
                return f"navigate_{action}"
        return action

    @staticmethod
    def _invalid_manual_action_lines(action: str) -> List[str]:
        return [
            (
                f"    raise RuntimeError("
                f"{('Recorded ' + action + ' action is missing a valid target locator; ' + 're-record or reselect the target element')!r}"
                f")"
            )
        ]

    def _dynamic_url_expression(self, url: str, previous_traces: List[RPAAcceptedTrace]) -> str:
        if not url:
            return ""
        latest_trace = previous_traces[-1] if previous_traces else None
        for trace in reversed(previous_traces):
            result_expr = self._trace_result_url_expression(trace)
            output = trace.output if isinstance(trace.output, dict) else {}
            base = output.get("url") or output.get("value")
            if result_expr and isinstance(base, str) and base and url.startswith(base):
                suffix = url[len(base):]
                return f"str({result_expr}).rstrip('/') + {suffix!r}"
            observed_base = str(trace.after_page.url or "").rstrip("/")
            if result_expr and observed_base and url.startswith(observed_base):
                suffix = url[len(observed_base):]
                return f"str({result_expr}).rstrip('/') + {suffix!r}"
            if trace is latest_trace and observed_base and url.startswith(observed_base):
                suffix = url[len(observed_base):]
                return f"str(_trace_page_url(current_page)).rstrip('/') + {suffix!r}"
        return ""

    def _trace_result_url_expression(self, trace: RPAAcceptedTrace) -> str:
        key = self._compiled_output_keys.get(id(trace), trace.output_key or "")
        if not key:
            return ""
        output = trace.output if isinstance(trace.output, dict) else {}
        if output.get("url"):
            return f"_resolve_result_ref(_results, {key + '.url'!r})"
        if output.get("value"):
            return f"_resolve_result_ref(_results, {key + '.value'!r})"
        if trace.trace_type == RPATraceType.AI_OPERATION and trace.output is None:
            return f"_resolve_first_result_ref(_results, [{key + '.url'!r}, {key + '.value'!r}])"
        return ""

    def _rewrite_dynamic_urls_in_code(self, code: str, previous_traces: List[RPAAcceptedTrace]) -> str:
        if not code or not previous_traces:
            return code

        def replace(match: re.Match[str]) -> str:
            url = match.group("url")
            dynamic = self._dynamic_url_expression(url, previous_traces)
            return dynamic or match.group(0)

        return re.sub(
            r"(?P<quote>['\"])(?P<url>https?://[^'\"\s]+)(?P=quote)",
            replace,
            code,
        )

    def _allocate_output_key(
        self,
        trace: RPAAcceptedTrace,
        raw_key: Optional[str],
        used_output_keys: Dict[str, int],
    ) -> str:
        key = str(raw_key or "").strip()
        if not key:
            return ""
        count = used_output_keys.get(key, 0) + 1
        used_output_keys[key] = count
        allocated = key if count == 1 else f"{key}_{count}"
        self._compiled_output_keys[id(trace)] = allocated
        return allocated


def _locator_expression(scope: str, locator: Dict[str, Any]) -> str:
    method = locator.get("method")
    if method == "role" or (method is None and locator.get("role")):
        role = locator.get("role", "button")
        name = locator.get("name")
        exact = locator.get("exact")
        args = [repr(role)]
        kwargs = []
        if name:
            kwargs.append(f"name={name!r}")
        if exact is not None:
            kwargs.append(f"exact={bool(exact)!r}")
        return f"{scope}.get_by_role({', '.join(args + kwargs)})"
    if method == "text":
        value = locator.get("value", "")
        exact = locator.get("exact")
        suffix = f", exact={bool(exact)!r}" if exact is not None else ""
        return f"{scope}.get_by_text({value!r}{suffix})"
    if method == "testid":
        return f"{scope}.get_by_test_id({locator.get('value', '')!r})"
    if method == "label":
        return f"{scope}.get_by_label({locator.get('value', '')!r})"
    if method == "placeholder":
        return f"{scope}.get_by_placeholder({locator.get('value', '')!r})"
    if method == "alt":
        return f"{scope}.get_by_alt_text({locator.get('value', '')!r})"
    if method == "title":
        return f"{scope}.get_by_title({locator.get('value', '')!r})"
    if method == "nested":
        parent = _locator_expression(scope, locator.get("parent") or {})
        return _locator_expression(parent, locator.get("child") or {})
    if method == "nth":
        base = _locator_expression(scope, locator.get("locator") or locator.get("base") or {"method": "css", "value": "body"})
        return f"{base}.nth({int(locator.get('index') or 0)})"
    if method == "css":
        return f"{scope}.locator({locator.get('value', '')!r}).first"
    return f"{scope}.locator({locator.get('value', 'body')!r}).first"


def _trace_signal(trace: RPAAcceptedTrace, name: str) -> Dict[str, Any]:
    signals = trace.signals if isinstance(trace.signals, dict) else {}
    signal = signals.get(name)
    return dict(signal) if isinstance(signal, dict) else {}


def _trace_has_random_like_primary_locator(trace: RPAAcceptedTrace) -> bool:
    metadata = trace.locator_stability
    return bool(metadata and metadata.primary_locator and metadata.unstable_signals)


def _select_conservative_replacement_locator(trace: RPAAcceptedTrace) -> Dict[str, Any]:
    metadata = trace.locator_stability
    if not metadata or not metadata.alternate_locators:
        return {}
    strong_candidates = [
        candidate.locator
        for candidate in metadata.alternate_locators
        if candidate.confidence == "high" and candidate.locator
    ]
    if len(strong_candidates) != 1:
        return {}
    return strong_candidates[0]


def _rewrite_random_like_locator_in_code(code: str, trace: RPAAcceptedTrace) -> str:
    if not _trace_has_random_like_primary_locator(trace):
        return code
    replacement_locator = _select_conservative_replacement_locator(trace)
    if not replacement_locator:
        return code
    metadata = trace.locator_stability
    if not metadata:
        return code
    primary_locator = metadata.primary_locator
    if primary_locator.get("method") != "css":
        return code
    selector = str(primary_locator.get("value") or "")
    if not selector:
        return code
    if _code_uses_positional_collection_locator(code, selector):
        return code
    replacement_expr = _locator_expression("page", replacement_locator)
    return code.replace(f"page.locator({selector!r})", replacement_expr)


def _code_uses_positional_collection_locator(code: str, selector: str) -> bool:
    return f"page.locator({selector!r}).nth(" in str(code or "")


def _should_preserve_runtime_ai_instruction(trace: RPAAcceptedTrace) -> bool:
    text = f"{trace.user_instruction or ''} {trace.description or ''}".lower()
    if not text.strip():
        return False
    semantic_markers = (
        "best",
        "most relevant",
        "most related",
        "related to",
        "semantic",
        "similar",
        "summarize",
        "highest risk",
        "highest priority",
        "recommend",
    )
    if any(marker in text for marker in semantic_markers):
        return True
    if not trace.ai_execution or not trace.ai_execution.code:
        return False
    output = trace.output
    return isinstance(output, dict) and bool(output.get("url") or output.get("value"))


def _runner_template(is_local: bool) -> str:
    if is_local:
        return '''\
import asyncio
import json as _json
import re
import sys
import time
from playwright.async_api import async_playwright

{execute_skill_func}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(**{launch_kwargs})
    context = await browser.new_context(**{context_kwargs})
    page = await context.new_page()
    page.set_default_timeout(60000)
    page.set_default_navigation_timeout(60000)
    try:
        result = await execute_skill(page, **kwargs)
        if result:
            print("SKILL_DATA:" + _json.dumps(result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as exc:
        print(f"SKILL_ERROR: {{exc}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''
    return '''\
import asyncio
import json as _json
import re
import sys
import time
import httpx
from playwright.async_api import async_playwright


async def _get_cdp_url() -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("http://127.0.0.1:8080/v1/browser/info")
        resp.raise_for_status()
        return resp.json()["data"]["cdp_url"]


{execute_skill_func}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v
    cdp_url = await _get_cdp_url()
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = await browser.new_context(**{context_kwargs})
    page = await context.new_page()
    page.set_default_timeout(60000)
    page.set_default_navigation_timeout(60000)
    try:
        result = await execute_skill(page, **kwargs)
        if result:
            print("SKILL_DATA:" + _json.dumps(result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as exc:
        print(f"SKILL_ERROR: {{exc}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''

