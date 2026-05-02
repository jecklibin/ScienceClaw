from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from backend.rpa.playwright_security import get_chromium_launch_kwargs, get_context_kwargs

from .playwright_code_normalizer import (
    stabilize_bare_text_clicks,
    stabilize_callable_locator_filters,
    stabilize_fill_targets,
    stabilize_unsupported_locator_options,
)
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
            ("tbody tr" in text or "tr.grid-row" in text)
            and ("td[data-colid=" in text or "td[field=" in text)
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
        helper_lines = [
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
            "async def _download_from_export_task(page, kwargs, results, download_key, *, table_heading='', row_selector='tbody tr', action_selector='a', row_index=0, timeout_ms=60000):",
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
            "                    rows = page.locator(row_selector)",
            "            else:",
            "                rows = page.locator(row_selector)",
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
            "async def _extract_display_field_value(field, value_selectors=None):",
            "    selectors = list(value_selectors or ('[data-value]', 'output', 'dd', 'input', 'textarea', 'select'))",
            "    for selector in selectors:",
            "        candidate = field.locator(selector).first",
            "        try:",
            "            if not await candidate.count():",
            "                continue",
            "            tag_name = await candidate.evaluate('el => el.tagName.toLowerCase()')",
            "            if tag_name in ('input', 'textarea', 'select'):",
            "                value = await candidate.input_value()",
            "            else:",
            "                value = await candidate.inner_text()",
            "            value = str(value or '').strip()",
            "            if value and value != '-':",
            "                return value",
            "        except Exception:",
            "            continue",
            "    return ''",
            "",
            "async def _extract_node_text_or_value(node):",
            "    try:",
            "        tag_name = await node.evaluate('el => el.tagName.toLowerCase()')",
            "    except Exception:",
            "        tag_name = ''",
            "    try:",
            "        if tag_name in ('input', 'textarea', 'select'):",
            "            value = await node.input_value()",
            "        else:",
            "            value = await node.inner_text()",
            "    except Exception:",
            "        try:",
            "            value = await node.text_content()",
            "        except Exception:",
            "            value = ''",
            "    value = str(value or '').strip()",
            "    return '' if value == '-' else value",
            "",
            "async def _extract_labeled_field_value(scope, label):",
            "    label = _normalize_visible_text(label)",
            "    if not label:",
            "        return ''",
            "    def xpath_literal(value):",
            "        value = str(value)",
            "        if \"'\" not in value:",
            "            return \"'\" + value + \"'\"",
            "        if '\"' not in value:",
            "            return '\"' + value + '\"'",
            "        return 'concat(' + ', \"\\'\", '.join(\"'\" + part + \"'\" for part in value.split(\"'\")) + ')'",
            "    candidate_labels = [label, label + ':', label + '：']",
            "    js = r'''(labelEl) => {",
            "        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();",
            "        const nodeText = (node) => normalize(node && (node.innerText || node.textContent || ''));",
            "        const controlValue = (node) => {",
            "            if (!node) return '';",
            "            const tag = String(node.tagName || '').toLowerCase();",
            "            if (tag === 'input' || tag === 'textarea' || tag === 'select') return normalize(node.value || '');",
            "            return nodeText(node);",
            "        };",
            "        const clean = (value) => {",
            "            value = normalize(value);",
            "            return value && value !== '-' ? value : '';",
            "        };",
            "        const labelText = nodeText(labelEl).replace(/[：:]$/, '').trim();",
            "        const directControl = labelEl.id ? document.querySelector(`[aria-labelledby~=\"${CSS.escape(labelEl.id)}\"]`) : null;",
            "        let value = clean(controlValue(directControl));",
            "        if (value && value !== labelText) return value;",
            "        const forId = labelEl.getAttribute && labelEl.getAttribute('for');",
            "        value = clean(controlValue(forId ? document.getElementById(forId) : null));",
            "        if (value && value !== labelText) return value;",
            "        if (labelEl.matches && labelEl.matches('dt') && labelEl.nextElementSibling && labelEl.nextElementSibling.matches('dd')) {",
            "            value = clean(nodeText(labelEl.nextElementSibling));",
            "            if (value) return value;",
            "        }",
            "        const cell = labelEl.closest && labelEl.closest('td,th,[role=\"cell\"],[role=\"rowheader\"]');",
            "        const row = labelEl.closest && labelEl.closest('tr,[role=\"row\"]');",
            "        if (cell && row) {",
            "            const cells = Array.from(row.querySelectorAll('th,td,[role=\"cell\"],[role=\"rowheader\"]'));",
            "            const index = cells.indexOf(cell);",
            "            for (const sibling of cells.slice(index + 1)) {",
            "                value = clean(controlValue(sibling.querySelector('input,textarea,select,[data-value],output') || sibling));",
            "                if (value && value !== labelText) return value;",
            "            }",
            "        }",
            "        let sibling = labelEl.nextElementSibling;",
            "        for (let i = 0; sibling && i < 4; i += 1, sibling = sibling.nextElementSibling) {",
            "            value = clean(controlValue(sibling.querySelector('input,textarea,select,[data-value],output') || sibling));",
            "            if (value && value !== labelText) return value;",
            "        }",
            "        const parent = labelEl.parentElement;",
            "        if (parent) {",
            "            const preferred = Array.from(parent.querySelectorAll('[data-value],output,dd,input,textarea,select')).find(node => node !== labelEl && !labelEl.contains(node));",
            "            value = clean(controlValue(preferred));",
            "            if (value && value !== labelText) return value;",
            "            const parentText = nodeText(parent);",
            "            if (parentText && parentText !== labelText && parentText.startsWith(labelText)) {",
            "                return clean(parentText.slice(labelText.length).replace(/^[：:\\s]+/, ''));",
            "            }",
            "        }",
            "        let ancestor = labelEl.parentElement;",
            "        for (let depth = 0; ancestor && depth < 5; depth += 1, ancestor = ancestor.parentElement) {",
            "            let ancestorSibling = ancestor.nextElementSibling;",
            "            for (let i = 0; ancestorSibling && i < 4; i += 1, ancestorSibling = ancestorSibling.nextElementSibling) {",
            "                value = clean(controlValue(ancestorSibling.querySelector('input,textarea,select,[data-value],output') || ancestorSibling));",
            "                if (value && value !== labelText) return value;",
            "            }",
            "            const scopedPreferred = Array.from(ancestor.querySelectorAll('[data-value],output,dd,input,textarea,select'))",
            "                .find(node => node !== labelEl && !labelEl.contains(node) && !node.contains(labelEl));",
            "            value = clean(controlValue(scopedPreferred));",
            "            if (value && value !== labelText) return value;",
            "            const ancestorText = nodeText(ancestor);",
            "            if (ancestorText && ancestorText !== labelText && ancestorText.startsWith(labelText)) {",
            "                value = clean(ancestorText.slice(labelText.length).replace(/^[：:\\s]+/, ''));",
            "                if (value) return value;",
            "            }",
            "        }",
            "        return '';",
            "    }'''",
            "    candidate_locators = []",
            "    for candidate_label in candidate_labels:",
            "        candidate_locators.append(scope.get_by_text(candidate_label, exact=True))",
            "    literal = xpath_literal(label)",
            "    candidate_locators.append(scope.locator(",
            "        'xpath=.//*[contains(normalize-space(.), ' + literal + ') and string-length(normalize-space(.)) <= ' + str(len(label) + 6) + ']'",
            "    ))",
            "    for labels in candidate_locators:",
            "        count = min(await labels.count(), 20)",
            "        for index in range(count):",
            "            label_node = labels.nth(index)",
            "            try:",
            "                value = await label_node.evaluate(js)",
            "            except Exception:",
            "                value = ''",
            "            value = _normalize_visible_text(value)",
            "            if value and value != label:",
            "                return value",
            "    return ''",
            "",
            "def _normalize_visible_text(value):",
            "    return re.sub(r'\\s+', ' ', str(value or '')).strip()",
            "",
            "def _extract_url_path_value(url, spec):",
            "    from urllib.parse import unquote, urlparse",
            "    parsed = urlparse(str(url or ''))",
            "    segments = [unquote(segment) for segment in parsed.path.split('/') if segment]",
            "    start = max(int(spec.get('start') or 0), 0)",
            "    count = max(int(spec.get('count') or 1), 1)",
            "    separator = str(spec.get('separator') or '/')",
            "    return separator.join(segments[start:start + count]).strip()",
            "",
            "def _extract_text_pattern_from_text(text, spec):",
            "    text = _normalize_visible_text(text)",
            "    prefix = _normalize_visible_text(spec.get('prefix'))",
            "    suffix = _normalize_visible_text(spec.get('suffix'))",
            "    lowered = text.lower()",
            "    start = 0",
            "    end = len(text)",
            "    if prefix:",
            "        prefix_lower = prefix.lower()",
            "        if not lowered.startswith(prefix_lower):",
            "            return ''",
            "        start = len(prefix)",
            "    if suffix:",
            "        suffix_lower = suffix.lower()",
            "        if not lowered.endswith(suffix_lower):",
            "            return ''",
            "        end = len(text) - len(suffix)",
            "    value = text[start:end].strip()",
            "    return '' if value == '-' else value",
            "",
            "async def _locator_text_candidates(locator):",
            "    values = []",
            "    try:",
            "        values.append(await locator.inner_text())",
            "    except Exception:",
            "        pass",
            "    for attr in ('aria-label', 'title'):",
            "        try:",
            "            values.append(await locator.get_attribute(attr))",
            "        except Exception:",
            "            pass",
            "    result = []",
            "    for value in values:",
            "        text = _normalize_visible_text(value)",
            "        if text and text not in result:",
            "            result.append(text)",
            "    return result",
            "",
            "async def _extract_text_pattern_value(scope, spec):",
            "    role = _normalize_visible_text(spec.get('role'))",
            "    tag = _normalize_visible_text(spec.get('tag')) or '*'",
            "    candidates = scope.get_by_role(role) if role else scope.locator(tag)",
            "    count = min(await candidates.count(), 300)",
            "    for index in range(count):",
            "        candidate = candidates.nth(index)",
            "        try:",
            "            if hasattr(candidate, 'is_visible') and not await candidate.is_visible():",
            "                continue",
            "        except Exception:",
            "            pass",
            "        for text in await _locator_text_candidates(candidate):",
            "            value = _extract_text_pattern_from_text(text, spec)",
            "            if value:",
            "                return value",
            "    raise RuntimeError(f\"Text pattern value not found: {spec}\")",
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
        ]
        body_lines = [
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA trace recording."""',
            "    _results = {}",
            "    current_page = page",
            "    tabs = {}",
            "    _trace_logger = kwargs.get('_on_log')",
        ]
        used_output_keys: Dict[str, int] = {}
        body_lines.extend(self._render_start_state_setup(traces))
        for index, trace in enumerate(traces):
            trace_lines = self._render_trace(index, trace, traces[:index], used_output_keys)
            trace_lines.extend(self._render_postcondition_trace(trace))
            body_lines.extend(self._wrap_trace_logging(index, trace, trace_lines))
        body_lines.append("    return _results")

        body_text = "\n".join(body_lines)
        lines = self._select_required_helper_lines(helper_lines, body_text)
        if self._requires_table_row_helper(traces) or "_find_table_row_by_headers(" in body_text or "_extract_table_cell_value(" in body_text:
            lines.extend(self._table_row_helper_lines())
        lines.extend(body_lines)
        return lines

    @classmethod
    def _select_required_helper_lines(cls, helper_lines: List[str], body_text: str) -> List[str]:
        required = cls._required_helper_names(body_text)
        blocks: List[tuple[str, List[str]]] = []
        current_name = ""
        current_lines: List[str] = []
        for line in helper_lines:
            name = cls._helper_def_name(line)
            if name:
                if current_lines:
                    blocks.append((current_name, current_lines))
                current_name = name
                current_lines = [line]
                continue
            if current_lines or line:
                current_lines.append(line)
        if current_lines:
            blocks.append((current_name, current_lines))

        selected: List[str] = []
        for name, block in blocks:
            if not name or name not in required:
                continue
            if selected and selected[-1] != "":
                selected.append("")
            selected.extend(block)
        if selected and selected[-1] != "":
            selected.append("")
        return selected

    @staticmethod
    def _helper_def_name(line: str) -> str:
        match = re.match(r"^(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
        return match.group(1) if match else ""

    @staticmethod
    def _required_helper_names(body_text: str) -> set[str]:
        required = {
            "_trace_page_url",
            "_trace_emit",
            "_trace_start",
            "_trace_done",
            "_trace_error",
        }
        if "_resolve_first_result_ref(" in body_text:
            required.update({"_resolve_first_result_ref", "_resolve_result_ref"})
        if "_resolve_result_ref(" in body_text:
            required.add("_resolve_result_ref")
        if "_validate_non_empty_records(" in body_text:
            required.add("_validate_non_empty_records")
        if "_download_from_export_task(" in body_text:
            required.add("_download_from_export_task")
        if "_extract_display_field_value(" in body_text:
            required.add("_extract_display_field_value")
        if "_extract_node_text_or_value(" in body_text:
            required.add("_extract_node_text_or_value")
        if "_extract_labeled_field_value(" in body_text:
            required.update({"_extract_labeled_field_value", "_normalize_visible_text"})
        if "_extract_table_cell_value(" in body_text:
            required.update(
                {
                    "_find_table_row_by_headers",
                    "_extract_node_text_or_value",
                    "_extract_table_cell_value",
                }
            )
        if "_extract_url_path_value(" in body_text:
            required.add("_extract_url_path_value")
        if "_extract_text_pattern_value(" in body_text:
            required.update(
                {
                    "_normalize_visible_text",
                    "_extract_text_pattern_from_text",
                    "_locator_text_candidates",
                    "_extract_text_pattern_value",
                }
            )
        if "_execute_runtime_ai_instruction(" in body_text:
            required.update({"_normalize_runtime_ai_payload", "_execute_runtime_ai_instruction"})
        return required

    @staticmethod
    def _render_start_state_setup(traces: List[RPAAcceptedTrace]) -> List[str]:
        if not traces:
            return []
        first = traces[0]
        if first.trace_type == RPATraceType.NAVIGATION:
            return []
        url = str(first.before_page.url or "").strip()
        if not re.match(r"^(https?|file)://", url, flags=re.IGNORECASE):
            return []
        return [
            "",
            "    # restore recorded start page",
            f"    await current_page.goto({url!r}, wait_until='domcontentloaded')",
            "    await current_page.wait_for_load_state('domcontentloaded')",
        ]

    @staticmethod
    def _requires_table_row_helper(traces: List[RPAAcceptedTrace]) -> bool:
        return any(
            isinstance(trace.postcondition, dict)
            and str(trace.postcondition.get("kind") or "") in {"table_row_exists", "table_row_absent"}
            for trace in traces
        )

    @staticmethod
    def _table_row_helper_lines() -> List[str]:
        return [
            "async def _find_table_row_by_headers(page, table_headers, key_values, *, timeout_ms=10000):",
            "    def _norm_cell(value):",
            "        return re.sub(r'\\s+', ' ', str(value or '')).strip()",
            "    headers = [str(item).strip() for item in (table_headers or []) if str(item).strip()]",
            "    expected = {str(key).strip(): str(value).strip() for key, value in (key_values or {}).items()}",
            "    deadline = time.perf_counter() + (timeout_ms / 1000)",
            "    last_seen = ''",
            "    while time.perf_counter() < deadline:",
            "        tables = page.locator('table, [role=table], [role=grid], [role=treegrid]')",
            "        for table_index in range(await tables.count()):",
            "            table = tables.nth(table_index)",
            "            header_cells = table.locator('thead tr:first-child th, thead tr:first-child td, [role=columnheader]')",
            "            if not await header_cells.count():",
            "                header_cells = table.locator('tr:first-child th, tr:first-child td, [role=row]:first-child [role=cell], [role=row]:first-child [role=gridcell]')",
            "            header_map = {}",
            "            for cell_index in range(await header_cells.count()):",
            "                text = str(await header_cells.nth(cell_index).inner_text()).strip()",
            "                if text:",
            "                    header_map[text] = cell_index",
            "            if headers and not all(header in header_map for header in headers):",
            "                continue",
            "            if any(key not in header_map for key in expected):",
            "                continue",
            "            row_sets = []",
            "            primary_rows = table.locator('tbody tr, [role=row]')",
            "            if await primary_rows.count():",
            "                row_sets.append(primary_rows)",
            "            split_body_rows = table.locator('xpath=following-sibling::table[1]//tbody/tr')",
            "            if not row_sets and await split_body_rows.count():",
            "                same_split_root = await table.evaluate(\"\"\"table => {",
            "                    const parent = table.parentElement;",
            "                    if (!parent || table.querySelector('tbody tr')) return false;",
            "                    const tableChildren = Array.from(parent.children).filter(el => (el.tagName || '').toLowerCase() === 'table');",
            "                    const index = tableChildren.indexOf(table);",
            "                    if (index < 0 || index + 1 >= tableChildren.length) return false;",
            "                    const bodyTable = tableChildren[index + 1];",
            "                    return Boolean(bodyTable.querySelector('tbody tr')) && tableChildren.length <= 3;",
            "                }\"\"\")",
            "                if same_split_root:",
            "                    row_sets.append(split_body_rows)",
            "            if not row_sets:",
            "                root_body_rows = table.locator(\"xpath=ancestor::*[count(.//table) <= 6 and .//table[.//tbody/tr]][1]//table[.//tbody/tr]//tbody/tr\")",
            "                if await root_body_rows.count():",
            "                    row_sets.append(root_body_rows)",
            "            if not row_sets:",
            "                row_sets.append(table.locator('tr'))",
            "            for rows in row_sets:",
            "                for row_index in range(await rows.count()):",
            "                    row = rows.nth(row_index)",
            "                    cells = row.locator('th, td, [role=cell], [role=gridcell], [role=rowheader]')",
            "                    if await cells.count() < len(header_map):",
            "                        continue",
            "                    matched = True",
            "                    for key, value in expected.items():",
            "                        cell_index = header_map[key]",
            "                        if await cells.count() <= cell_index:",
            "                            matched = False",
            "                            break",
            "                        actual = _norm_cell(await cells.nth(cell_index).inner_text())",
            "                        expected_value = _norm_cell(value)",
            "                        if actual != expected_value:",
            "                            matched = False",
            "                            break",
            "                    if matched:",
            "                        return row",
            "            last_seen = ', '.join(header_map.keys())",
            "        await page.wait_for_timeout(250)",
            "    detail = f' Last headers seen: {last_seen}' if last_seen else ''",
            "    raise RuntimeError(f'Table row matching {expected} was not found.{detail}')",
            "",
            "async def _extract_table_cell_value(page, spec):",
            "    row = await _find_table_row_by_headers(page, spec.get('table_headers') or [], spec.get('row_key') or {})",
            "    cells = row.locator('th, td, [role=cell], [role=gridcell], [role=rowheader]')",
            "    cell_count = await cells.count()",
            "    column_index = spec.get('column_index')",
            "    try:",
            "        column_index = int(column_index)",
            "    except Exception:",
            "        column_index = -1",
            "    if column_index < 0 or column_index >= cell_count:",
            "        raise RuntimeError(f'Table cell column index {column_index} is out of range')",
            "    return await _extract_node_text_or_value(cells.nth(column_index))",
            "",
        ]

    def _render_postcondition_trace(self, trace: RPAAcceptedTrace) -> List[str]:
        postcondition = trace.postcondition if isinstance(trace.postcondition, dict) else {}
        kind = str(postcondition.get("kind") or "")
        if kind not in {"table_row_exists", "table_row_absent"}:
            return []
        headers = [
            str(item).strip()
            for item in list(postcondition.get("table_headers") or [])
            if str(item).strip()
        ]
        key_values = self._postcondition_table_values(trace, postcondition)
        if not headers or not key_values:
            return []
        expression = f"{{{', '.join(f'{key!r}: {value}' for key, value in key_values)}}}"
        if kind == "table_row_absent":
            return [
                "",
                "    # verify table row absence postcondition",
                "    try:",
                (
                    "        await _find_table_row_by_headers("
                    f"current_page, {headers!r}, {expression}, timeout_ms=1500)"
                ),
                "    except RuntimeError:",
                "        pass",
                "    else:",
                "        raise RuntimeError('Table row matching postcondition was still present')",
            ]
        return [
            "",
            "    # verify table row postcondition",
            (
                "    await _find_table_row_by_headers("
                f"current_page, {headers!r}, {expression})"
            ),
        ]

    def _has_compilable_postcondition(self, trace: RPAAcceptedTrace) -> bool:
        return bool(self._render_postcondition_trace(trace))

    def _postcondition_table_values(
        self,
        trace: RPAAcceptedTrace,
        postcondition: Dict[str, Any],
    ) -> List[tuple[str, str]]:
        values: List[tuple[str, str]] = []
        binding_lookup = self._binding_name_lookup(trace.input_bindings)
        for section_name in ("key", "expect"):
            section = postcondition.get(section_name)
            if not isinstance(section, dict):
                continue
            for raw_key, raw_value in section.items():
                key = str(raw_key).strip()
                if not key:
                    continue
                values.append((key, self._postcondition_value_expression(raw_value, binding_lookup)))
        return values

    def _postcondition_value_expression(
        self,
        value: Any,
        binding_lookup: Dict[str, Dict[str, Any]],
    ) -> str:
        if isinstance(value, str):
            binding_match = re.fullmatch(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", value.strip())
            if binding_match:
                param_name = binding_match.group(1)
                param_info = binding_lookup.get(param_name, {})
                return self._parameter_expression(param_name, param_info, str(param_info.get("default") or ""))
        return repr(value)

    @staticmethod
    def _binding_name_lookup(input_bindings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        lookup: Dict[str, Dict[str, Any]] = {}
        for param_name, raw_info in input_bindings.items():
            lookup[str(param_name)] = dict(raw_info) if isinstance(raw_info, dict) else {"default": raw_info}
        return lookup

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
        extract_snapshot_signal = _trace_signal(trace, "extract_snapshot")
        if extract_snapshot_signal:
            if self._snapshot_extract_has_required_replay_evidence(trace, extract_snapshot_signal):
                return self._render_snapshot_extract_trace(index, trace, used_output_keys)
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
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

    def _render_snapshot_extract_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        signal = _trace_signal(trace, "extract_snapshot")
        fields = self._snapshot_extract_fields(trace, signal)
        key = self._allocate_output_key(trace, trace.output_key or f"snapshot_extract_{index}", used_output_keys)
        lines = ["", f"    # trace {index}: {trace.description or 'snapshot extract'}"]
        frame_path = signal.get("frame_path") if isinstance(signal.get("frame_path"), list) else trace.frame_path
        scope_lines, scope_var = self._frame_scope_lines(list(frame_path or []))
        lines.extend(scope_lines)
        lines.append("    _result = {}")
        lines.append("    _missing_required_fields = []")
        for field in fields:
            label = str(field.get("label") or "").strip()
            data_prop = str(field.get("data_prop") or "").strip()
            if not label:
                continue
            value_locator = self._field_locator(field.get("value_locator"))
            field_locator = self._field_locator(field.get("field_locator"))
            url_extraction = self._snapshot_url_extraction(field.get("url_extraction"))
            text_pattern = self._snapshot_text_pattern(field.get("text_pattern"))
            label_extraction = self._snapshot_label_extraction(field)
            table_cell = self._snapshot_table_cell(field.get("table_cell"))
            replay_required = bool(field.get("replay_required", True))
            if url_extraction:
                lines.append(f"    _value = _extract_url_path_value(current_page.url, {url_extraction!r})")
                lines.append("    if _value:")
                lines.append(f"        _result[{label!r}] = _value")
                if replay_required:
                    lines.append("    else:")
                    lines.append(f"        _missing_required_fields.append({label!r})")
                continue
            if table_cell:
                lines.append("    try:")
                lines.append(f"        _value = await _extract_table_cell_value(current_page, {self._table_cell_spec_expression(table_cell)})")
                lines.append("    except Exception:")
                lines.append("        _value = ''")
                lines.append("    if _value:")
                lines.append(f"        _result[{label!r}] = _value")
                if replay_required:
                    lines.append("    else:")
                    lines.append(f"        _missing_required_fields.append({label!r})")
                continue
            if text_pattern:
                lines.append("    try:")
                lines.append(f"        _value = await _extract_text_pattern_value({scope_var}, {text_pattern!r})")
                lines.append("    except Exception:")
                lines.append("        _value = ''")
                lines.append("    if _value:")
                lines.append(f"        _result[{label!r}] = _value")
                if replay_required:
                    lines.append("    else:")
                    lines.append(f"        _missing_required_fields.append({label!r})")
                continue
            if label_extraction:
                lines.append(f"    _value = await _extract_labeled_field_value({scope_var}, {label_extraction['label']!r})")
                lines.append("    if _value:")
                lines.append(f"        _result[{label!r}] = _value")
                if replay_required:
                    lines.append("    else:")
                    lines.append(f"        _missing_required_fields.append({label!r})")
                continue
            if value_locator:
                lines.append(f"    _value_node = {_locator_expression(scope_var, value_locator)}")
                lines.append("    _value = ''")
                lines.append("    if await _value_node.count():")
                lines.append("        _value = await _extract_node_text_or_value(_value_node)")
                lines.append("    if _value:")
                lines.append(f"        _result[{label!r}] = _value")
                if replay_required:
                    lines.append("    else:")
                    lines.append(f"        _missing_required_fields.append({label!r})")
                continue
            if field_locator:
                lines.append(f"    _field = {_locator_expression(scope_var, field_locator)}")
            elif data_prop:
                selector = f'[data-prop="{data_prop}"]'
                lines.append(f"    _field = {scope_var}.locator({selector!r}).first")
            else:
                if replay_required:
                    lines.append(f"    _missing_required_fields.append({label!r})")
                continue
            lines.append("    if await _field.count():")
            display_selectors = self._display_value_selectors(field)
            lines.append(f"        _value = await _extract_display_field_value(_field, {tuple(display_selectors)!r})")
            lines.append("    else:")
            lines.append("        _value = ''")
            lines.append("    if _value:")
            lines.append(f"        _result[{label!r}] = _value")
            if replay_required:
                lines.append("    else:")
                lines.append(f"        _missing_required_fields.append({label!r})")
        lines.append("    if _missing_required_fields:")
        lines.append('        raise RuntimeError(f"Snapshot extract missing required fields: {_missing_required_fields}")')
        lines.append(f"    _results[{key!r}] = _result")
        return lines

    @staticmethod
    def _display_value_selectors(field: Dict[str, Any]) -> List[str]:
        generic = ["[data-value]", "output", "dd", "input", "textarea", "select"]
        explicit: List[str] = []
        raw_selectors = field.get("value_selectors")
        if isinstance(raw_selectors, list):
            explicit.extend(str(item).strip() for item in raw_selectors if str(item).strip())
        raw_selector = str(field.get("value_selector") or "").strip()
        if raw_selector:
            explicit.append(raw_selector)

        selectors: List[str] = []
        for selector in [*explicit, *generic]:
            if selector and selector not in selectors:
                selectors.append(selector)
        return selectors

    @staticmethod
    def _snapshot_extract_fields(trace: RPAAcceptedTrace, signal: Dict[str, Any]) -> List[Dict[str, Any]]:
        fields = [dict(field) for field in list(signal.get("fields") or []) if isinstance(field, dict)]
        usable_fields = [field for field in fields if str(field.get("label") or "").strip()]
        if usable_fields:
            return usable_fields
        if isinstance(trace.output, dict):
            return [{"label": str(label), "data_prop": ""} for label in trace.output.keys() if str(label).strip()]
        return []

    @staticmethod
    def _snapshot_extract_has_required_replay_evidence(
        trace: RPAAcceptedTrace,
        signal: Dict[str, Any],
    ) -> bool:
        fields = TraceSkillCompiler._snapshot_extract_fields(trace, signal)
        labeled_fields = [field for field in fields if str(field.get("label") or "").strip()]
        if not labeled_fields:
            return False

        saw_replayable_field = False
        for field in labeled_fields:
            has_replay_evidence = TraceSkillCompiler._snapshot_field_has_replay_evidence(field)
            saw_replayable_field = saw_replayable_field or has_replay_evidence
            if bool(field.get("replay_required", True)) and not has_replay_evidence:
                return False
        return saw_replayable_field

    @staticmethod
    def _snapshot_field_has_replay_evidence(field: Dict[str, Any]) -> bool:
        if TraceSkillCompiler._field_locator(field.get("value_locator")):
            return True
        if TraceSkillCompiler._field_locator(field.get("field_locator")):
            return True
        if str(field.get("data_prop") or "").strip():
            return True
        if TraceSkillCompiler._snapshot_url_extraction(field.get("url_extraction")):
            return True
        if TraceSkillCompiler._snapshot_text_pattern(field.get("text_pattern")):
            return True
        if TraceSkillCompiler._snapshot_table_cell(field.get("table_cell")):
            return True
        if TraceSkillCompiler._snapshot_label_extraction(field):
            return True
        return False

    @staticmethod
    def _snapshot_table_cell(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        headers = [str(item).strip() for item in list(value.get("table_headers") or []) if str(item).strip()]
        row_key = {
            str(key).strip(): str(item).strip()
            for key, item in dict(value.get("row_key") or {}).items()
            if str(key).strip() and str(item).strip()
        }
        column_header = str(value.get("column_header") or "").strip()
        try:
            column_index = int(value.get("column_index"))
        except Exception:
            column_index = -1
        if not headers or not row_key or not column_header or column_index < 0:
            return {}
        return {
            "table_headers": headers,
            "row_key": row_key,
            "column_header": column_header,
            "column_index": column_index,
        }

    def _table_cell_spec_expression(self, spec: Dict[str, Any]) -> str:
        row_items = []
        for key, value in dict(spec.get("row_key") or {}).items():
            row_items.append(f"{key!r}: {self._maybe_parameterize_value(str(value))}")
        row_expr = "{" + ", ".join(row_items) + "}"
        return (
            "{"
            f"'table_headers': {list(spec.get('table_headers') or [])!r}, "
            f"'row_key': {row_expr}, "
            f"'column_header': {str(spec.get('column_header') or '')!r}, "
            f"'column_index': {int(spec.get('column_index') or 0)}"
            "}"
        )

    @staticmethod
    def _snapshot_label_extraction(field: Dict[str, Any]) -> Dict[str, str]:
        observed_label = str(field.get("observed_label") or "").strip()
        if _looks_like_stable_field_label(observed_label):
            return {"kind": "label_value", "label": observed_label}
        value = field.get("value")
        if isinstance(value, dict):
            nested_label = str(value.get("label") or "").strip()
            if _looks_like_stable_field_label(nested_label):
                return {"kind": "label_value", "label": nested_label}
        label = str(field.get("label") or "").strip()
        if _looks_like_stable_field_label(label):
            return {"kind": "label_value", "label": label}
        return {}

    @staticmethod
    def _snapshot_url_extraction(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        if str(value.get("kind") or "") != "url_path_join":
            return {}
        try:
            start = max(int(value.get("start") or 0), 0)
            count = max(int(value.get("count") or 1), 1)
        except Exception:
            return {}
        return {
            "kind": "url_path_join",
            "start": start,
            "count": count,
            "separator": str(value.get("separator") or "/"),
        }

    @staticmethod
    def _snapshot_text_pattern(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        prefix = str(value.get("prefix") or "").strip()
        suffix = str(value.get("suffix") or "").strip()
        if not prefix and not suffix:
            return {}
        pattern: Dict[str, Any] = {"prefix": prefix, "suffix": suffix}
        role = str(value.get("role") or "").strip()
        tag = str(value.get("tag") or "").strip().lower()
        if role:
            pattern["role"] = role
        if tag:
            pattern["tag"] = tag
        return pattern

    @staticmethod
    def _field_locator(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        locator = normalize_locator(value)
        return locator if has_valid_locator(locator) else {}

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
        code = self._rewrite_input_bindings_in_code(code, trace.input_bindings)
        code = _rewrite_random_like_locator_in_code(code, trace)
        code = stabilize_callable_locator_filters(code)
        code = stabilize_unsupported_locator_options(code)
        code = stabilize_bare_text_clicks(code)
        code = stabilize_fill_targets(code)
        download_signal = _trace_signal(trace, "download")
        if download_signal:
            self._classify_download_signal(trace, download_signal)
        recovered_attempt = _trace_signal(trace, "recovered_attempt")
        idempotent_postcondition = _trace_signal(trace, "idempotent_postcondition_replay")
        code_handles_download = "expect_download" in code or ".save_as(" in code
        lines = ["", f"    # trace {index}: {trace.description or 'AI operation'}"]
        if (recovered_attempt or idempotent_postcondition) and self._has_compilable_postcondition(trace):
            lines.append("    try:")
            for code_line in code.splitlines():
                lines.append(f"        {code_line}" if code_line.strip() else "")
            lines.append("        _result = await run(current_page, _results)")
            if key:
                lines.append(f"        _results[{key!r}] = _result")
            lines.append("    except Exception as _recovered_exc:")
            lines.append("        _results.setdefault('_recovered_attempt_errors', []).append(str(_recovered_exc))")
            return lines
        for code_line in code.splitlines():
            lines.append(f"    {code_line}" if code_line.strip() else "")
        if download_signal and self._download_trigger_mode(download_signal) == "export_task" and not code_handles_download:
            download_name = str(download_signal.get("filename") or "file")
            safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", download_name.split(".")[0]) or "file"
            download_key = "download_" + safe_name
            heading, row_selector, action_selector = self._export_task_download_hints(code)
            lines.append("    _result = await run(current_page, _results)")
            lines.append(
                "    _download_payload = await _download_from_export_task("
                "current_page, kwargs, _results, "
                f"{json.dumps(download_key, ensure_ascii=False)}, "
                f"table_heading={heading!r}, "
                f"row_selector={row_selector!r}, "
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
    def _export_task_download_hints(code: str) -> tuple[str, str, str]:
        heading = ""
        heading_match = re.search(r"get_by_text\((['\"])(.*?)\1,\s*exact=True\)", code)
        if heading_match:
            heading = heading_match.group(2)

        row_selector = "tbody tr"
        row_selector_match = re.search(r"page\.locator\((['\"])(.*?(?:tbody tr|tr\.grid-row).*?)\1\)", code)
        if row_selector_match:
            row_selector = row_selector_match.group(2)

        action_selector = "a"
        selector_match = re.search(r"\.locator\((['\"])(td\[(?:data-colid|field)=.*?)\1\)\.click\(", code)
        if selector_match:
            action_selector = selector_match.group(2)
        return heading, row_selector, action_selector

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

    def _rewrite_input_bindings_in_code(self, code: str, input_bindings: Dict[str, Any]) -> str:
        if not code or not input_bindings:
            return code

        value_lookup = self._build_input_binding_lookup(input_bindings)
        if not value_lookup:
            return code

        def replace(match: re.Match[str]) -> str:
            value = match.group("value")
            binding = value_lookup.get(value)
            if not binding:
                return match.group(0)
            if self._is_prefixed_string_literal(code, match.start()):
                return match.group(0)
            param_name, param_info = binding
            if self._is_stable_ui_anchor_literal(code, match.start(), param_info):
                return match.group(0)
            return self._parameter_expression(param_name, param_info, value)

        return re.sub(
            r"(?P<quote>['\"])(?P<value>(?:\\.|(?!\1).)*?)(?P=quote)",
            replace,
            code,
        )

    @staticmethod
    def _is_prefixed_string_literal(code: str, quote_index: int) -> bool:
        prefix_start = quote_index
        while prefix_start > 0 and code[prefix_start - 1] in "bBrRfFuU":
            prefix_start -= 1
        if prefix_start == quote_index:
            return False
        prefix = code[prefix_start:quote_index]
        if not prefix or any(char not in "bBrRfFuU" for char in prefix):
            return False
        if prefix_start > 0 and (code[prefix_start - 1].isalnum() or code[prefix_start - 1] in "_."):
            return False
        return True

    @staticmethod
    def _is_stable_ui_anchor_literal(code: str, quote_index: int, param_info: Dict[str, Any]) -> bool:
        if not _binding_is_stable_ui_anchor(param_info):
            return False
        prefix = code[max(0, quote_index - 48):quote_index]
        return bool(
            re.search(r"\b(name|label|placeholder|alt|title)\s*=\s*$", prefix)
            or re.search(r"\.(get_by_text|get_by_label|get_by_placeholder|get_by_alt_text|get_by_title)\(\s*$", prefix)
            or re.search(r"\.locator\(\s*$", prefix)
        )

    @staticmethod
    def _build_input_binding_lookup(input_bindings: Dict[str, Any]) -> Dict[str, tuple[str, Dict[str, Any]]]:
        lookup: Dict[str, tuple[str, Dict[str, Any]]] = {}
        for param_name, raw_info in input_bindings.items():
            if isinstance(raw_info, dict):
                param_info = dict(raw_info)
            else:
                param_info = {"default": raw_info}
            value = (
                param_info.get("original_value")
                or param_info.get("recorded_value")
                or param_info.get("default")
                or param_info.get("value")
            )
            if value is None:
                continue
            lookup[str(value)] = (str(param_name), param_info)
        return lookup

    @staticmethod
    def _parameter_expression(param_name: str, param_info: Dict[str, Any], recorded_value: str) -> str:
        if param_info.get("sensitive"):
            return f"kwargs[{param_name!r}]"
        default = param_info.get("default", recorded_value)
        return f"kwargs.get({param_name!r}, {default!r})"

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


def _xpath_literal(value: str) -> str:
    text = str(value)
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in text.split("'")) + ")"


def _looks_like_stable_field_label(value: str) -> bool:
    label = re.sub(r"\s+", " ", str(value or "")).strip().strip(":：")
    if not label or len(label) > 80:
        return False
    if "_" in label:
        return False
    if re.search(r"https?://|@", label, flags=re.IGNORECASE):
        return False
    if re.match(r"^[A-Z]{2,}[-_0-9A-Z]+$", label):
        return False
    if re.match(r"^\d", label):
        return False
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", label):
        return False
    if re.search(r"[\u4e00-\u9fff]", label):
        return True
    if " " in label and re.search(r"[A-Za-z]", label):
        return True
    return bool(re.match(r"^[A-Z][A-Za-z0-9 ()/-]{1,79}$", label))


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
    if _selector_is_structural_collection(selector):
        return code
    if _code_uses_positional_collection_locator(code, selector):
        return code
    if _code_uses_fill_locator(code, selector) and not _locator_is_editable_target(replacement_locator):
        return code
    replacement_expr = _locator_expression("page", replacement_locator)
    rewritten = code
    for selector_literal in _string_literals_for_value(selector):
        rewritten = rewritten.replace(f"page.locator({selector_literal})", replacement_expr)
    return rewritten


def _code_uses_fill_locator(code: str, selector: str) -> bool:
    text = str(code or "")
    for selector_literal in _string_literals_for_value(selector):
        if re.search(rf"page\.locator\({re.escape(selector_literal)}\)\.fill\(", text):
            return True
        assignment_pattern = re.compile(
            rf"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*page\.locator\({re.escape(selector_literal)}\)"
        )
        for match in assignment_pattern.finditer(text):
            var_name = re.escape(match.group("var"))
            if re.search(rf"\b{var_name}\.fill\(", text[match.end():]):
                return True
    return False


def _selector_is_structural_collection(selector: str) -> bool:
    text = str(selector or "").strip().lower()
    structural_tokens = (
        " tbody",
        "thead",
        " tr",
        "td",
        "th",
        "[role=row]",
        "[role=cell]",
        "[role=gridcell]",
        ".el-table",
        "table",
    )
    return any(token in text for token in structural_tokens)


def _locator_is_editable_target(locator: Dict[str, Any]) -> bool:
    method = str(locator.get("method") or "").strip().lower()
    if method == "role":
        return str(locator.get("role") or "").strip().lower() in {
            "textbox",
            "searchbox",
            "combobox",
            "spinbutton",
        }
    if method == "css":
        selector = str(locator.get("value") or "").strip().lower()
        return any(token in selector for token in ("input", "textarea", "select", "contenteditable"))
    if method == "nested":
        child = locator.get("child")
        return isinstance(child, dict) and _locator_is_editable_target(child)
    if method == "nth":
        base = locator.get("locator") or locator.get("base")
        return isinstance(base, dict) and _locator_is_editable_target(base)
    return False


def _code_uses_positional_collection_locator(code: str, selector: str) -> bool:
    text = str(code or "")
    for selector_literal in _string_literals_for_value(selector):
        if f"page.locator({selector_literal}).nth(" in text:
            return True
        assignment_pattern = re.compile(
            rf"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*page\.locator\({re.escape(selector_literal)}\)"
        )
        for match in assignment_pattern.finditer(text):
            var_name = re.escape(match.group("var"))
            if re.search(rf"\b{var_name}\.nth\(", text[match.end():]):
                return True
    return False


def _string_literals_for_value(value: str) -> List[str]:
    literals = [repr(value), json.dumps(value, ensure_ascii=False)]
    unique: List[str] = []
    for literal in literals:
        if literal not in unique:
            unique.append(literal)
    return unique


def _binding_is_stable_ui_anchor(param_info: Dict[str, Any]) -> bool:
    classification = str(param_info.get("classification") or param_info.get("kind") or "").strip().lower()
    source = str(param_info.get("source") or "").strip().lower()
    stable_values = {
        "stable_ui_anchor",
        "ui_anchor",
        "ui_label",
        "label",
        "placeholder",
        "role_name",
        "title",
    }
    return classification in stable_values or source in stable_values


def _should_preserve_runtime_ai_instruction(trace: RPAAcceptedTrace) -> bool:
    runtime_ai_signal = _trace_signal(trace, "runtime_ai")
    if runtime_ai_signal.get("preserve") is True or runtime_ai_signal.get("preserve_runtime_ai") is True:
        return True
    return False


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

