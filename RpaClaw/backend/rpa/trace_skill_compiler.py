from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from backend.rpa.playwright_security import get_chromium_launch_kwargs, get_context_kwargs

from .trace_locator_utils import (
    has_valid_locator,
    locator_has_unstable_identity,
    locator_is_replay_safe_for_region_extract,
    locator_instability_penalty,
    normalize_locator,
)
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
        trace_list = self._normalize_redirect_continuation_traces(
            self._normalize_redundant_navigation_traces(
                self._normalize_download_traces(list(traces))
            )
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

    @classmethod
    def _normalize_redirect_continuation_traces(cls, traces: List[RPAAcceptedTrace]) -> List[RPAAcceptedTrace]:
        normalized: List[RPAAcceptedTrace] = []
        index = 0
        while index < len(traces):
            trace = traces[index]
            if not cls._can_absorb_redirect_continuation(trace):
                normalized.append(trace)
                index += 1
                continue

            chain: List[RPAAcceptedTrace] = []
            cursor = index + 1
            while cursor < len(traces) and cls._is_redirect_continuation_trace(trace, traces[cursor]):
                chain.append(traces[cursor])
                cursor += 1

            if not cls._has_redirect_auth_evidence(trace, chain) or not cls._is_short_redirect_chain(trace, chain):
                normalized.append(trace)
                index += 1
                continue

            final_url = cls._redirect_continuation_final_url(
                chain,
                traces[cursor] if cursor < len(traces) else None,
                trace,
            )
            if not final_url:
                normalized.append(trace)
                index += 1
                continue

            merged = trace.model_copy(deep=True)
            signals = dict(merged.signals or {})
            post_navigation = dict(signals.get("post_navigation") or {})
            post_navigation.update(
                {
                    "final_url": final_url,
                    "folded_trace_ids": [item.trace_id for item in chain],
                    "source": "redirect_continuation",
                }
            )
            callback_url = cls._trace_url(merged)
            if cls._looks_like_auth_callback_url(callback_url):
                post_navigation["callback_url"] = callback_url
            signals["post_navigation"] = post_navigation
            merged.signals = signals
            merged.after_page.url = final_url
            normalized.append(merged)
            index = cursor
        return normalized

    @staticmethod
    def _can_absorb_redirect_continuation(trace: RPAAcceptedTrace) -> bool:
        return (
            trace.trace_type == RPATraceType.MANUAL_ACTION
            and str(trace.action or "") in {"navigate_click", "navigate_press"}
            and not _trace_signal(trace, "popup")
            and not _trace_signal(trace, "download")
        )

    @classmethod
    def _is_redirect_continuation_trace(
        cls,
        source_trace: RPAAcceptedTrace,
        candidate: RPAAcceptedTrace,
    ) -> bool:
        if candidate.trace_type != RPATraceType.NAVIGATION:
            return False
        if candidate.locator_candidates:
            return False
        source_tab = cls._trace_tab_id(source_trace)
        candidate_tab = cls._trace_tab_id(candidate)
        if source_tab and candidate_tab and source_tab != candidate_tab:
            return False
        return bool(cls._trace_url(candidate))

    @classmethod
    def _has_redirect_auth_evidence(cls, source_trace: RPAAcceptedTrace, chain: List[RPAAcceptedTrace]) -> bool:
        return any(cls._looks_like_auth_callback_url(cls._trace_url(trace)) for trace in [source_trace, *chain])

    @staticmethod
    def _is_short_redirect_chain(source_trace: RPAAcceptedTrace, chain: List[RPAAcceptedTrace]) -> bool:
        if not chain:
            return False
        source_time = source_trace.ended_at or source_trace.started_at
        if source_time is None:
            return False
        for trace in chain:
            trace_time = trace.started_at or trace.ended_at
            if trace_time is None:
                return False
            if abs((trace_time - source_time).total_seconds()) > 5:
                return False
        return True

    @classmethod
    def _redirect_continuation_final_url(
        cls,
        chain: List[RPAAcceptedTrace],
        next_trace: Optional[RPAAcceptedTrace],
        source_trace: RPAAcceptedTrace,
    ) -> str:
        if not chain:
            return ""
        chain_urls = [cls._trace_url(trace) for trace in chain if cls._trace_url(trace)]
        if not chain_urls:
            return ""

        next_url = cls._trace_url(next_trace) if next_trace is not None else ""
        if next_url:
            normalized_next = cls._normalized_url(next_url)
            for url in reversed(chain_urls):
                if cls._normalized_url(url) == normalized_next:
                    return url

        for url in reversed(chain_urls):
            if not cls._looks_like_auth_callback_url(url):
                return url

        source_url = cls._trace_url(source_trace)
        return "" if cls._looks_like_auth_callback_url(source_url) else source_url

    @staticmethod
    def _trace_url(trace: Optional[RPAAcceptedTrace]) -> str:
        if trace is None:
            return ""
        return str(trace.after_page.url or trace.value or "").strip()

    @staticmethod
    def _looks_like_auth_callback_url(url: str) -> bool:
        text = str(url or "").lower()
        if not text:
            return False
        if any(token in text for token in ("code=", "state=", "token=", "ticket=", "samlresponse=")):
            return True
        return any(token in text for token in ("/callback", "/oauth", "/saml", "/cas", "login.html?"))

    @staticmethod
    def _normalized_url(url: str) -> str:
        return str(url or "").strip().rstrip("/")

    @staticmethod
    def _navigation_replay_target_url(trace: RPAAcceptedTrace) -> str:
        navigation_signal = _trace_signal(trace, "navigation")
        signal_target = str(navigation_signal.get("target_url") or "").strip()
        if signal_target:
            return signal_target
        trace_value = str(trace.value or "").strip()
        if trace_value:
            return trace_value
        return str(trace.after_page.url or "").strip()

    @staticmethod
    def _helper_dependency_map() -> Dict[str, List[str]]:
        return {
            "_trace_start": ["_trace_emit"],
            "_trace_done": ["_trace_emit"],
            "_trace_error": ["_trace_emit"],
            "_trace_emit": ["_trace_page_url"],
            "_resolve_first_result_ref": ["_resolve_result_ref"],
            "_execute_runtime_ai_instruction": [
                "_normalize_runtime_ai_payload",
                "_runtime_ai_model_config",
            ],
            "_ensure_recorded_tab": ["_activate_recorded_page"],
        }

    @staticmethod
    def _helper_render_order() -> List[str]:
        return [
            "_resolve_result_ref",
            "_resolve_first_result_ref",
            "_download_from_export_task",
            "_trace_page_url",
            "_trace_emit",
            "_trace_start",
            "_trace_done",
            "_trace_error",
            "_normalize_runtime_ai_payload",
            "_extract_display_field_value",
            "_extract_bounded_section_text",
            "_runtime_ai_model_config",
            "_execute_runtime_ai_instruction",
            "_activate_recorded_page",
            "_ensure_recorded_tab",
            "_resolve_recorded_frame",
        ]

    @classmethod
    def _collect_required_helpers(cls, rendered_lines: List[str]) -> set[str]:
        known_helpers = set(cls._helper_render_order())
        required = {
            helper
            for line in rendered_lines
            for helper in known_helpers
            if helper in line
        }
        deps = cls._helper_dependency_map()
        changed = True
        while changed:
            changed = False
            for helper in list(required):
                for dependency in deps.get(helper, []):
                    if dependency not in required:
                        required.add(dependency)
                        changed = True
        return required

    @classmethod
    def _render_helper_prelude(cls, required_helpers: set[str]) -> List[str]:
        lines: List[str] = [""]
        for helper in cls._helper_render_order():
            if helper not in required_helpers:
                continue
            lines.extend(cls._helper_block(helper))
            lines.append("")
        return lines

    @staticmethod
    def _helper_block(helper: str) -> List[str]:
        blocks: Dict[str, List[str]] = {
            "_resolve_result_ref": [
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
            ],
            "_resolve_first_result_ref": [
                "def _resolve_first_result_ref(results, refs):",
                "    last_error = None",
                "    for ref in refs:",
                "        try:",
                "            return _resolve_result_ref(results, ref)",
                "        except KeyError as exc:",
                "            last_error = exc",
                "    raise last_error or KeyError(refs[0] if refs else '')",
            ],
            "_download_from_export_task": [
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
            ],
            "_trace_page_url": [
                "def _trace_page_url(page):",
                "    try:",
                "        return str(getattr(page, 'url', '') or '')",
                "    except Exception:",
                "        return ''",
            ],
            "_trace_emit": [
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
            ],
            "_trace_start": [
                "def _trace_start(logger, index, description, page):",
                "    started_at = time.perf_counter()",
                "    _trace_emit(logger, 'START', index, description, page)",
                "    return started_at",
            ],
            "_trace_done": [
                "def _trace_done(logger, index, description, page, started_at):",
                "    _trace_emit(logger, 'DONE', index, description, page, started_at)",
            ],
            "_trace_error": [
                "def _trace_error(logger, index, description, page, started_at, error):",
                "    _trace_emit(logger, 'ERROR', index, description, page, started_at, error)",
            ],
            "_normalize_runtime_ai_payload": [
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
            ],
            "_extract_display_field_value": [
                "async def _extract_display_field_value(field):",
                "    value_selectors = [",
                "        '.aui-input-display-only__content',",
                "        '.aui-numeric-display-only__value',",
                "        '.aui-range-editor-display-only',",
                "        '.aui-input-display-only',",
                "        '.no-value',",
                "        'input',",
                "        'textarea',",
                "        'select',",
                "    ]",
                "    for selector in value_selectors:",
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
            ],
            "_extract_bounded_section_text": [
                "async def _extract_bounded_section_text(heading):",
                "    try:",
                "        if not await heading.count():",
                "            return ''",
                "        handle = await heading.element_handle()",
                "        if handle is None:",
                "            return ''",
                "        value = await handle.evaluate(\"\"\"",
                "node => {",
                "  const blockTags = new Set(['P', 'DIV', 'SPAN', 'LI', 'DD']);",
                "  const stopTags = new Set(['H1', 'H2', 'H3', 'H4', 'H5', 'H6']);",
                "  const excludedTags = new Set(['A', 'BUTTON', 'NAV', 'UL', 'OL', 'FORM', 'INPUT', 'TEXTAREA', 'SELECT']);",
                "  const clean = value => String(value || '').replace(/\\\\s+/g, ' ').trim();",
                "  const visible = el => {",
                "    const style = window.getComputedStyle(el);",
                "    const rect = el.getBoundingClientRect();",
                "    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 1 && rect.height > 1;",
                "  };",
                "  const usable = el => {",
                "    if (!el || excludedTags.has(el.tagName) || stopTags.has(el.tagName) || !visible(el)) return false;",
                "    if (!blockTags.has(el.tagName)) return false;",
                "    const text = clean(el.innerText || el.textContent);",
                "    if (!text) return false;",
                "    const linkText = clean(Array.from(el.querySelectorAll('a')).map(a => a.innerText || a.textContent).join(' '));",
                "    return !linkText || linkText.length < text.length;",
                "  };",
                "  const root = node.parentElement;",
                "  if (!root) return '';",
                "  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);",
                "  let seenHeading = false;",
                "  while (walker.nextNode()) {",
                "    const el = walker.currentNode;",
                "    if (el === node) { seenHeading = true; continue; }",
                "    if (!seenHeading) continue;",
                "    if (stopTags.has(el.tagName)) break;",
                "    if (usable(el)) return clean(el.innerText || el.textContent);",
                "  }",
                "  return '';",
                "}",
                "\"\"\")",
                "        return str(value or '').strip()",
                "    except Exception:",
                "        return ''",
            ],
            "_runtime_ai_model_config": [
                "def _runtime_ai_model_config(kwargs):",
                "    runtime_context = kwargs.get('_runtime_context') if isinstance(kwargs, dict) else None",
                "    runtime_ai = runtime_context.get('runtime_ai') if isinstance(runtime_context, dict) else None",
                "    model_config = runtime_ai.get('model_config') if isinstance(runtime_ai, dict) else None",
                "    return model_config or kwargs.get('_model_config')",
            ],
            "_execute_runtime_ai_instruction": [
                "async def _execute_runtime_ai_instruction(page, results, kwargs, instruction, output_key):",
                "    from backend.rpa.recording_runtime_agent import RecordingRuntimeAgent",
                "    agent = RecordingRuntimeAgent(model_config=_runtime_ai_model_config(kwargs))",
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
            ],
            "_activate_recorded_page": [
                "async def _activate_recorded_page(page, kwargs, tab_id=''):",
                "    activator = kwargs.get('_activate_recorded_page') if isinstance(kwargs, dict) else None",
                "    if callable(activator):",
                "        await activator(page, tab_id)",
            ],
            "_ensure_recorded_tab": [
                "async def _ensure_recorded_tab(tabs, current_page, kwargs, tab_id, recorded_url='', require_recorded_url=False):",
                "    if tab_id in tabs:",
                "        page = tabs[tab_id]",
                "    else:",
                "        if require_recorded_url and not recorded_url:",
                "            raise RuntimeError(f'Recorded tab {tab_id} is missing recorded URL; cannot materialize replay page safely')",
                "        page = await current_page.context.new_page()",
                "        tabs[tab_id] = page",
                "        if recorded_url:",
                "            await page.goto(recorded_url, wait_until='domcontentloaded')",
                "    await page.bring_to_front()",
                "    await _activate_recorded_page(page, kwargs, tab_id)",
                "    return page",
            ],
            "_resolve_recorded_frame": [
                "async def _resolve_recorded_frame(page_or_frame, *, url_contains='', timeout_ms=60000):",
                "    deadline = time.perf_counter() + (timeout_ms / 1000)",
                "    last_urls = []",
                "    while time.perf_counter() < deadline:",
                "        frames = list(getattr(page_or_frame, 'frames', None) or getattr(page_or_frame, 'child_frames', []) or [])",
                "        last_urls = []",
                "        for frame in frames:",
                "            frame_url = str(getattr(frame, 'url', '') or '')",
                "            if frame_url:",
                "                last_urls.append(frame_url)",
                "            if url_contains and url_contains in frame_url:",
                "                return frame",
                "        await asyncio.sleep(0.5)",
                "    observed = ', '.join(last_urls[:5])",
                "    detail = f' Observed frames: {observed}' if observed else ''",
                "    raise RuntimeError(f'Recorded iframe context was not found for url_contains={url_contains!r}.{detail}')",
            ],
        }
        return blocks[helper]

    def _render_execute_skill(self, traces: List[RPAAcceptedTrace]) -> List[str]:
        root_tab_id = self._first_trace_tab_id(traces)
        known_tab_ids = {root_tab_id} if root_tab_id else set()
        current_tab_id = root_tab_id
        used_output_keys: Dict[str, int] = {}
        rendered_trace_lines: List[str] = []
        for index, trace in enumerate(traces):
            alignment_lines, current_tab_id = self._render_trace_tab_alignment(
                trace,
                known_tab_ids,
                current_tab_id,
            )
            trace_lines = self._render_trace(index, trace, traces[:index], used_output_keys)
            if alignment_lines:
                trace_lines = alignment_lines + trace_lines
            rendered_trace_lines.extend(self._wrap_trace_logging(index, trace, trace_lines))
            current_tab_id = self._record_trace_tab_side_effects(trace, known_tab_ids, current_tab_id)

        lines = self._render_helper_prelude(self._collect_required_helpers(rendered_trace_lines))
        lines.extend(
            [
                "async def execute_skill(page, **kwargs):",
                '    """Auto-generated skill from RPA trace recording."""',
                "    _results = {}",
                "    current_page = page",
                f"    tabs = {{{json.dumps(root_tab_id, ensure_ascii=False)}: page}}" if root_tab_id else "    tabs = {}",
                "    _trace_logger = kwargs.get('_on_log')",
            ]
        )
        lines.extend(rendered_trace_lines)
        lines.append("    return _results")
        return lines

    @staticmethod
    def _first_trace_tab_id(traces: List[RPAAcceptedTrace]) -> str:
        for trace in traces:
            tab_id = TraceSkillCompiler._trace_tab_id(trace)
            if tab_id:
                return tab_id
            tab_signal = _trace_signal(trace, "tab")
            source_tab_id = str(tab_signal.get("source_tab_id") or "").strip()
            if source_tab_id:
                return source_tab_id
            popup_signal = _trace_signal(trace, "popup")
            popup_source_tab_id = str(popup_signal.get("source_tab_id") or "").strip()
            if popup_source_tab_id:
                return popup_source_tab_id
        return ""

    @staticmethod
    def _trace_tab_id(trace: RPAAcceptedTrace) -> str:
        tab_signal = _trace_signal(trace, "tab")
        return str(tab_signal.get("tab_id") or "").strip()

    @staticmethod
    def _trace_has_frame_context(trace: RPAAcceptedTrace) -> bool:
        return bool(TraceSkillCompiler._trace_replay_frame_path(trace))

    @staticmethod
    def _trace_replay_frame_path(trace: RPAAcceptedTrace) -> List[str]:
        reported_frame_path = trace.signals.get("reported_frame_path") if isinstance(trace.signals, dict) else None
        if isinstance(reported_frame_path, list):
            normalized = [str(item).strip() for item in reported_frame_path if str(item or "").strip()]
            if normalized:
                return normalized
        return list(trace.frame_path or [])

    @classmethod
    def _render_trace_tab_alignment(
        cls,
        trace: RPAAcceptedTrace,
        known_tab_ids: set[str],
        current_tab_id: str,
    ) -> tuple[List[str], str]:
        tab_id = cls._trace_tab_id(trace)
        if not tab_id or tab_id == current_tab_id:
            return [], current_tab_id

        tab_id_literal = json.dumps(tab_id, ensure_ascii=False)
        if tab_id in known_tab_ids:
            return [
                "",
                f"    current_page = await _ensure_recorded_tab(tabs, current_page, kwargs, {tab_id_literal})",
            ], tab_id
        if cls._trace_has_frame_context(trace):
            return [], current_tab_id

        known_tab_ids.add(tab_id)
        return [
            "",
            f"    # Materialize recorded tab {tab_id}; opener/popup evidence was not available.",
            f"    current_page = await _ensure_recorded_tab(tabs, current_page, kwargs, {tab_id_literal})",
        ], tab_id

    @staticmethod
    def _record_trace_tab_side_effects(
        trace: RPAAcceptedTrace,
        known_tab_ids: set[str],
        current_tab_id: str,
    ) -> str:
        popup_signal = _trace_signal(trace, "popup")
        popup_target_tab_id = str(popup_signal.get("target_tab_id") or "").strip()
        if popup_target_tab_id:
            known_tab_ids.add(popup_target_tab_id)
            current_tab_id = popup_target_tab_id

        action = str(trace.action or "")
        tab_signal = _trace_signal(trace, "tab")
        if action == "switch_tab":
            target_tab_id = str(tab_signal.get("target_tab_id") or "").strip()
            if target_tab_id:
                known_tab_ids.add(target_tab_id)
                current_tab_id = target_tab_id
        elif action == "close_tab":
            closing_tab_id = str(tab_signal.get("tab_id") or tab_signal.get("source_tab_id") or "").strip()
            if closing_tab_id:
                known_tab_ids.discard(closing_tab_id)
            fallback_tab_id = str(tab_signal.get("target_tab_id") or "").strip()
            if fallback_tab_id:
                known_tab_ids.add(fallback_tab_id)
                current_tab_id = fallback_tab_id

        return current_tab_id

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
        url = self._navigation_replay_target_url(trace)
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
            scope_lines, scope_var = self._frame_scope_lines(self._trace_replay_frame_path(trace))
            lines.extend(scope_lines)
            expr = _locator_expression(scope_var, locator)
            lines.append("    async with current_page.expect_navigation(wait_until='domcontentloaded'):")
            if action == "navigate_click":
                lines.append(f"        await {expr}.click()")
            else:
                lines.append(f"        await {expr}.press({str(trace.value or '')!r})")
            lines.append("    await current_page.wait_for_load_state('domcontentloaded')")
            post_navigation = _trace_signal(trace, "post_navigation")
            final_url = str(post_navigation.get("final_url") or "").strip()
            if final_url:
                lines.append(f"    await current_page.wait_for_url({final_url!r}, timeout=60000)")
            return lines
        if action == "switch_tab":
            lines.extend(self._render_switch_tab_trace(trace))
            return lines
        if action == "close_tab":
            lines.extend(self._render_close_tab_trace(trace))
            return lines
        if not locator and action in {"hover", "click", "fill", "press", "check", "uncheck", "select", "set_input_files"}:
            lines.extend(self._invalid_manual_action_lines(action))
            return lines
        if not locator:
            lines.append("    # No stable locator was recorded for this manual action.")
            return lines
        scope_lines, scope_var = self._frame_scope_lines(self._trace_replay_frame_path(trace))
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
        elif action == "set_input_files":
            input_files_value = self._build_input_files_value(trace)
            lines.append(f"    await {expr}.set_input_files({input_files_value})")
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
        target_tab_id_literal = json.dumps(target_tab_id, ensure_ascii=False)
        recorded_url_literal = json.dumps(str(trace.after_page.url or "").strip(), ensure_ascii=False)
        lines.append(
            "    current_page = await _ensure_recorded_tab("
            f"tabs, current_page, kwargs, {target_tab_id_literal}, {recorded_url_literal}, True)"
        )
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
            fallback_tab_id_literal = json.dumps(fallback_tab_id, ensure_ascii=False)
            fallback_url = str(
                tab_signal.get("target_url")
                or tab_signal.get("target_tab_url")
                or ""
            ).strip()
            fallback_url_literal = json.dumps(fallback_url, ensure_ascii=False)
            lines.append(
                "    current_page = await _ensure_recorded_tab("
                f"tabs, current_page, kwargs, {fallback_tab_id_literal}, {fallback_url_literal}, True)"
            )
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
            lines.append(f"{popup_indent}await _activate_recorded_page(current_page, kwargs, {json.dumps(target_tab_id, ensure_ascii=False)})")

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
        if _has_selected_region_text_extract(trace):
            return self._render_selected_region_text_extract_trace(index, trace, used_output_keys)
        if _snapshot_extract_is_selected_text_region_evidence(trace):
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        if self._has_usable_snapshot_extract_fields(trace):
            return self._render_snapshot_extract_trace(index, trace, used_output_keys)
        if _is_selected_region_local_text_extract(trace):
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        if _has_heading_scoped_text_extract(trace):
            return self._render_heading_scoped_text_extract_trace(index, trace, used_output_keys)
        if _is_region_scoped_free_text_extract(trace):
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        if _should_preserve_runtime_ai_instruction(trace):
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        if _trace_has_side_effect_evidence(trace):
            if trace.ai_execution and trace.ai_execution.code:
                return self._render_embedded_ai_code_trace(index, trace, previous_traces, used_output_keys)
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        region_lines = self._render_region_extract_trace(index, trace, used_output_keys)
        if region_lines:
            return region_lines
        region_runtime_requirement = _trace_region_runtime_ai_requirement(trace)
        if region_runtime_requirement is True:
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        if _embedded_ai_code_has_weak_empty_extract_evidence(trace):
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        if trace.ai_execution and trace.ai_execution.code:
            return self._render_embedded_ai_code_trace(index, trace, previous_traces, used_output_keys)
        if trace.user_instruction or trace.description:
            return self._render_runtime_ai_instruction_trace(index, trace, used_output_keys)
        return ["", f"    # trace {index}: AI operation has no executable body"]

    def _render_region_extract_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        region_kind = _trace_region_kind(trace)
        region_context = _trace_region_context(trace)
        if region_kind == "single_value":
            locator = self._preferred_locator_for_trace(
                trace,
                _trace_region_value_locator_candidates(trace, region_context),
            )
            if locator:
                return self._render_region_single_value_trace(index, trace, used_output_keys, locator, region_context)
        if region_kind == "table_region":
            table_summary = (
                region_context.get("table_summary") if isinstance(region_context.get("table_summary"), dict) else {}
            )
            locator = self._best_locator(list(table_summary.get("locator_candidates") or []))
            if locator:
                return self._render_region_table_trace(index, trace, used_output_keys, locator, region_context)
        if region_kind in {"list_sample", "list_region"}:
            list_summary = (
                region_context.get("list_summary") if isinstance(region_context.get("list_summary"), dict) else {}
            )
            item_selector = str(list_summary.get("item_selector") or "").strip()
            locator = self._best_locator(list(list_summary.get("container_locator_candidates") or []))
            if locator and item_selector:
                return self._render_region_list_trace(
                    index,
                    trace,
                    used_output_keys,
                    locator,
                    item_selector,
                    region_context,
                )
        return []

    def _render_region_single_value_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
        locator: Dict[str, Any],
        region_context: Dict[str, Any],
    ) -> List[str]:
        key = self._allocate_output_key(trace, trace.output_key or f"region_value_{index}", used_output_keys)
        lines = ["", f"    # trace {index}: {trace.description or 'region value extract'}"]
        scope_lines, scope_var = self._frame_scope_lines(_trace_region_frame_path(trace, region_context))
        lines.extend(scope_lines)
        lines.append(f"    _result = (await {_locator_expression(scope_var, locator)}.inner_text()).strip()")
        lines.append(f"    _results[{key!r}] = _result")
        return lines

    def _render_region_table_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
        locator: Dict[str, Any],
        region_context: Dict[str, Any],
    ) -> List[str]:
        key = self._allocate_output_key(trace, trace.output_key or f"region_table_{index}", used_output_keys)
        lines = ["", f"    # trace {index}: {trace.description or 'region table extract'}"]
        scope_lines, scope_var = self._frame_scope_lines(_trace_region_frame_path(trace, region_context))
        lines.extend(scope_lines)
        table_summary = region_context.get("table_summary") if isinstance(region_context.get("table_summary"), dict) else {}
        selected_indexes = _selected_indexes(table_summary.get("selected_row_indexes"))
        row_source = "Array.from(table.querySelectorAll('tr'))"
        if selected_indexes:
            row_source += ".filter((row, index) => selectedIndexes.has(index))"
        row_mapping = (
            f"{row_source}"
            ".map((row) => Array.from(row.querySelectorAll('th,td'))"
            ".map((cell) => (cell.innerText || cell.textContent || '').trim())"
            ".filter(Boolean))"
            ".filter((row) => row.length)"
        )
        table_js = f"(table) => {row_mapping}"
        if selected_indexes:
            table_js = (
                f"(table) => {{const selectedIndexes = new Set({json.dumps(selected_indexes)});"
                f"return {row_mapping};}}"
            )
        lines.append(
            f"    _result = await {_locator_expression(scope_var, locator)}.evaluate("
            f"\"\"\"{table_js}\"\"\")"
        )
        lines.append(f"    _results[{key!r}] = _result")
        return lines

    def _render_region_list_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
        locator: Dict[str, Any],
        item_selector: str,
        region_context: Dict[str, Any],
    ) -> List[str]:
        key = self._allocate_output_key(trace, trace.output_key or f"region_list_{index}", used_output_keys)
        lines = ["", f"    # trace {index}: {trace.description or 'region list extract'}"]
        scope_lines, scope_var = self._frame_scope_lines(_trace_region_frame_path(trace, region_context))
        lines.extend(scope_lines)
        list_summary = region_context.get("list_summary") if isinstance(region_context.get("list_summary"), dict) else {}
        selected_indexes = _selected_indexes(list_summary.get("selected_item_indexes"))
        item_source = "items"
        if selected_indexes:
            item_source += ".filter((item, index) => selectedIndexes.has(index))"
        item_mapping = f"{item_source}.map((item) => (item.innerText || item.textContent || '').trim()).filter(Boolean)"
        list_js = f"(items) => {item_mapping}"
        if selected_indexes:
            list_js = (
                f"(items) => {{const selectedIndexes = new Set({json.dumps(selected_indexes)});"
                f"return {item_mapping};}}"
            )
        lines.append(
            f"    _result = await {_locator_expression(scope_var, locator)}.locator({item_selector!r}).evaluate_all("
            f"\"\"\"{list_js}\"\"\")"
        )
        lines.append(f"    _results[{key!r}] = _result")
        return lines

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
            f"    _result = await _execute_runtime_ai_instruction(current_page, _results, kwargs, {instruction!r}, {key!r})",
        ]

    def _render_snapshot_extract_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        signal = _trace_signal(trace, "extract_snapshot")
        fields = self._snapshot_extract_fields(signal)
        key = self._allocate_output_key(trace, trace.output_key or f"snapshot_extract_{index}", used_output_keys)
        lines = ["", f"    # trace {index}: {trace.description or 'snapshot extract'}"]
        frame_path = signal.get("frame_path") if isinstance(signal.get("frame_path"), list) else trace.frame_path
        scope_lines, scope_var = self._frame_scope_lines(list(frame_path or []))
        lines.extend(scope_lines)
        lines.append("    _result = {}")
        for field in fields:
            label = str(field.get("label") or "").strip()
            data_prop = str(field.get("data_prop") or "").strip()
            if not label:
                continue
            if data_prop:
                selector = f'[data-prop="{data_prop}"]'
                lines.append(f"    _field = {scope_var}.locator({selector!r}).first")
            else:
                xpath = (
                    "xpath=//*[normalize-space()="
                    + _xpath_literal(label)
                    + "]/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' aui-form-item ')][1]"
                )
                lines.append(f"    _field = {scope_var}.locator({xpath!r}).first")
            lines.append("    if await _field.count():")
            lines.append("        _value = await _extract_display_field_value(_field)")
            lines.append(f"        _result[{label!r}] = _value")
        lines.append(f"    _results[{key!r}] = _result")
        return lines

    def _render_heading_scoped_text_extract_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        signal = _trace_signal(trace, "region_text_extract")
        key = self._allocate_output_key(trace, trace.output_key or signal.get("output_key") or f"region_text_{index}", used_output_keys)
        heading_locator = signal.get("heading_locator") if isinstance(signal.get("heading_locator"), dict) else {}
        frame_path = signal.get("frame_path") if isinstance(signal.get("frame_path"), list) else trace.frame_path
        scope_lines, scope_var = self._frame_scope_lines(list(frame_path or []))
        heading_expr = _locator_expression(scope_var, normalize_locator(heading_locator))
        if not heading_expr.endswith(".first"):
            heading_expr = f"{heading_expr}.first"
        lines = ["", f"    # trace {index}: {trace.description or 'heading scoped text extract'}"]
        lines.extend(scope_lines)
        lines.append("    _result = ''")
        lines.append(f"    _heading = {heading_expr}")
        lines.append("    _result = await _extract_bounded_section_text(_heading)")
        lines.append(f"    _results[{key!r}] = _result")
        return lines

    def _render_selected_region_text_extract_trace(
        self,
        index: int,
        trace: RPAAcceptedTrace,
        used_output_keys: Dict[str, int],
    ) -> List[str]:
        signal = _trace_signal(trace, "selected_region_text_extract")
        key = self._allocate_output_key(
            trace,
            trace.output_key or signal.get("output_key") or f"region_text_{index}",
            used_output_keys,
        )
        locator = normalize_locator(signal.get("locator") if isinstance(signal.get("locator"), dict) else {})
        frame_path = signal.get("frame_path") if isinstance(signal.get("frame_path"), list) else trace.frame_path
        label = str(signal.get("label") or "").strip()
        scope_lines, scope_var = self._frame_scope_lines(list(frame_path or []))
        locator_expr = _locator_expression(scope_var, locator)
        if not locator_expr.endswith(".first"):
            locator_expr = f"{locator_expr}.first"

        lines = ["", f"    # trace {index}: {trace.description or 'selected region text extract'}"]
        lines.extend(scope_lines)
        if label:
            lines.append("    _result = {}")
            lines.append(f"    _value = (await {locator_expr}.inner_text()).strip()")
            lines.append(f"    _result[{label!r}] = _value")
        else:
            lines.append(f"    _result = (await {locator_expr}.inner_text()).strip()")
        lines.append(f"    _results[{key!r}] = _result")
        return lines

    @staticmethod
    def _has_usable_snapshot_extract_fields(trace: RPAAcceptedTrace) -> bool:
        signal = _trace_signal(trace, "extract_snapshot")
        return bool(TraceSkillCompiler._snapshot_extract_fields(signal))

    @staticmethod
    def _snapshot_extract_fields(signal: Dict[str, Any]) -> List[Dict[str, Any]]:
        fields = [dict(field) for field in list(signal.get("fields") or []) if isinstance(field, dict)]
        return [field for field in fields if str(field.get("label") or "").strip()]

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
        harness_input = re.fullmatch(r"\{\{input:([A-Za-z_][A-Za-z0-9_]*)\}\}", value)
        if harness_input:
            param_name = harness_input.group(1)
            return f"kwargs.get({param_name!r}, {value!r})"

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
        default_value = param_info.get("default_value")
        if default_value in (None, ""):
            default_value = value
        return f"kwargs.get({param_name!r}, {default_value!r})"

    def _build_input_files_value(self, trace: RPAAcceptedTrace) -> str:
        signal = _trace_signal(trace, "set_input_files")
        raw_files = signal.get("files")
        files = [str(item) for item in raw_files if str(item)] if isinstance(raw_files, list) else []
        if len(files) > 1:
            return repr(files)
        effective_value = files[0] if files else str(trace.value or "")
        return self._maybe_parameterize_value(effective_value)

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
            heading, row_selector, action_selector = self._export_task_download_hints(code)
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
        ordered = [item for item in candidates if item.get("selected")]
        ordered.extend(item for item in candidates if not item.get("selected"))
        normalized_ordered: List[Dict[str, Any]] = []
        for candidate in ordered:
            locator = candidate.get("locator") if isinstance(candidate, dict) else None
            normalized = normalize_locator(locator if isinstance(locator, dict) else candidate)
            if has_valid_locator(normalized):
                normalized_ordered.append(normalized)
        if not normalized_ordered:
            return {}
        selected = normalized_ordered[0]
        if locator_has_unstable_identity(selected):
            stable_candidates = []
            seen = set()
            for candidate in normalized_ordered[1:]:
                if locator_instability_penalty(candidate) > 0:
                    continue
                key = repr(candidate)
                if key in seen:
                    continue
                seen.add(key)
                stable_candidates.append(candidate)
            if len(stable_candidates) == 1:
                return stable_candidates[0]
            return {}
        for normalized in normalized_ordered:
            if has_valid_locator(normalized):
                return normalized
        return {}

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
        locator = self._apply_exact_defaults(locator)
        if trace.trace_type == RPATraceType.MANUAL_ACTION and self._effective_manual_action(trace) in {
            "navigate_click",
            "navigate_press",
        }:
            locator = self._relax_navigating_link_exact(locator)
        return locator

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

    def _relax_navigating_link_exact(self, locator: Dict[str, Any]) -> Dict[str, Any]:
        method = locator.get("method")
        normalized = dict(locator)
        if method == "nested":
            parent = locator.get("parent")
            child = locator.get("child")
            if isinstance(parent, dict):
                normalized["parent"] = self._relax_navigating_link_exact(parent)
            if isinstance(child, dict):
                normalized["child"] = self._relax_navigating_link_exact(child)
            return normalized
        if method == "nth":
            base = locator.get("locator") or locator.get("base")
            if isinstance(base, dict):
                normalized["locator"] = self._relax_navigating_link_exact(base)
                normalized.pop("base", None)
            return normalized
        if method == "role" and str(locator.get("role") or "").strip() == "link":
            normalized.pop("exact", None)
        return normalized

    @classmethod
    def _frame_scope_lines(cls, frame_path: List[str]) -> tuple[List[str], str]:
        if not frame_path:
            return [], "current_page"
        lines: List[str] = []
        frame_parent = "current_page"
        for frame_selector in frame_path:
            stable_url = cls._dynamic_frame_url_contains(str(frame_selector))
            if stable_url:
                lines.append(
                    f"    frame_scope = await _resolve_recorded_frame({frame_parent}, "
                    f"url_contains={json.dumps(stable_url, ensure_ascii=False)})"
                )
            else:
                lines.append(
                    f"    frame_scope = {frame_parent}.frame_locator({json.dumps(str(frame_selector), ensure_ascii=False)})"
                )
            frame_parent = "frame_scope"
        return lines, "frame_scope"

    @classmethod
    def _dynamic_frame_url_contains(cls, frame_selector: str) -> str:
        src = cls._exact_iframe_src(frame_selector)
        if not src:
            return ""
        parsed = urlparse(src)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        has_dynamic_url_state = bool(parsed.query or "?" in parsed.fragment or "&" in parsed.fragment)
        if not has_dynamic_url_state:
            return ""
        stable = f"{parsed.netloc}{parsed.path or '/'}"
        fragment_path = parsed.fragment.split("?", 1)[0].split("&", 1)[0].strip()
        if fragment_path:
            stable = f"{stable}#{fragment_path}"
        return stable

    @staticmethod
    def _exact_iframe_src(frame_selector: str) -> str:
        match = re.fullmatch(r"""iframe\[src=(["'])(?P<src>.*)\1\]""", str(frame_selector or "").strip())
        if not match:
            return ""
        return re.sub(r"\\(.)", r"\1", match.group("src"))

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
    if method == "filter_has_text":
        base_locator = locator.get("locator") or locator.get("base") or {"method": "css", "value": "body"}
        has_text = locator.get("has_text", "")
        if isinstance(base_locator, dict) and base_locator.get("method") == "css":
            return f"{scope}.locator({base_locator.get('value', 'body')!r}).filter(has_text={has_text!r}).first"
        return f"{_locator_expression(scope, base_locator)}.filter(has_text={has_text!r})"
    if method == "css":
        return f"{scope}.locator({locator.get('value', '')!r}).first"
    return f"{scope}.locator({locator.get('value', 'body')!r}).first"


def _trace_signal(trace: RPAAcceptedTrace, name: str) -> Dict[str, Any]:
    signals = trace.signals if isinstance(trace.signals, dict) else {}
    signal = signals.get(name)
    return dict(signal) if isinstance(signal, dict) else {}


def _trace_region_context(trace: RPAAcceptedTrace) -> Dict[str, Any]:
    context = trace.region_context if isinstance(trace.region_context, dict) else {}
    if not context:
        return {}
    evidence = context.get("evidence")
    if isinstance(evidence, dict):
        merged = dict(evidence)
        for key, value in context.items():
            if key != "evidence":
                merged.setdefault(key, value)
        return merged
    return dict(context)


def _trace_region_kind(trace: RPAAcceptedTrace) -> str:
    context = _trace_region_context(trace)
    kind = str(context.get("inferred_kind") or context.get("kind") or "").strip()
    if kind:
        return kind
    table_summary = context.get("table_summary")
    if isinstance(table_summary, dict) and table_summary.get("locator_candidates"):
        return "table_region"
    list_summary = context.get("list_summary")
    if isinstance(list_summary, dict) and list_summary.get("item_selector"):
        return "list_sample"
    if context.get("locator_candidates"):
        return "single_value"
    return ""


def _trace_region_frame_path(trace: RPAAcceptedTrace, region_context: Dict[str, Any]) -> List[str]:
    frame_path = region_context.get("frame_path")
    if isinstance(frame_path, list):
        return [str(item) for item in frame_path if str(item or "").strip()]
    return list(trace.frame_path or [])


def _is_selected_region_local_text_extract(trace: RPAAcceptedTrace) -> bool:
    signal = _trace_signal(trace, "extract_snapshot")
    return str(signal.get("source") or "").strip() == "selected_region.local_text"


def _has_selected_region_text_extract(trace: RPAAcceptedTrace) -> bool:
    signal = _trace_signal(trace, "selected_region_text_extract")
    intent = str(signal.get("intent") or "single_value_extract").strip()
    if intent != "single_value_extract":
        return False
    locator = normalize_locator(signal.get("locator") if isinstance(signal.get("locator"), dict) else {})
    return locator_is_replay_safe_for_region_extract(
        locator,
        observed_values=list(_observed_text_values(trace, signal)),
    )


def _snapshot_extract_is_selected_text_region_evidence(trace: RPAAcceptedTrace) -> bool:
    if not _trace_signal(trace, "extract_snapshot"):
        return False
    if _trace_region_kind(trace) != "text_region":
        return False
    source = str(_trace_signal(trace, "extract_snapshot").get("source") or "").strip()
    return source in {"selected_region.local_text", "region_scoped_snapshot", "expanded_regions"}


def _has_heading_scoped_text_extract(trace: RPAAcceptedTrace) -> bool:
    signal = _trace_signal(trace, "region_text_extract")
    intent = str(signal.get("intent") or "anchored_region_extract").strip()
    if intent != "anchored_region_extract":
        return False
    if str(signal.get("kind") or "").strip() != "heading_scoped_text":
        return False
    strategy = str(signal.get("text_strategy") or "").strip()
    if strategy != "bounded_section_text":
        return False
    relation = str(signal.get("heading_relation") or "").strip()
    if relation not in {"inside_heading", "preceding_heading"}:
        return False
    heading_locator = signal.get("heading_locator")
    return locator_is_replay_safe_for_region_extract(
        heading_locator if isinstance(heading_locator, dict) else {},
        observed_values=list(_observed_result_text_values(trace, signal)),
    )


def _is_region_scoped_free_text_extract(trace: RPAAcceptedTrace) -> bool:
    if trace.trace_type != RPATraceType.AI_OPERATION:
        return False
    if TraceSkillCompiler._has_usable_snapshot_extract_fields(trace):
        return False
    if not (trace.region_scope or trace.region_context or _trace_signal(trace, "region_selection")):
        return False
    if _trace_output_is_action_evidence(trace.output):
        return False

    region_kind = _trace_region_kind(trace)
    if region_kind in {"table_region", "list_sample", "list_region", "single_value"}:
        return False
    if region_kind and region_kind != "text_region":
        return False

    decision = _trace_signal(trace, "region_context_decision")
    used_as = str(decision.get("used_as") or "").strip()
    action_type = str(decision.get("action_type") or "").strip()
    if used_as == "action_targeting":
        return False
    if used_as == "extraction" and action_type in {"", "run_python", "extract_snapshot"}:
        return True

    return bool(trace.output_key and _looks_like_extract_instruction(trace))


def _observed_text_values(trace: RPAAcceptedTrace, signal: Dict[str, Any]) -> set[str]:
    values: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            values.add(value.strip())

    add(signal.get("observed_text"))
    context = _trace_region_context(trace)
    for item in list(context.get("local_text") or []):
        add(item)
    for item in list(_trace_signal(trace, "region_selection").get("local_text_preview") or []):
        add(item)

    def walk(value: Any) -> None:
        if isinstance(value, str):
            add(value)
        elif isinstance(value, dict):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(trace.output)
    if trace.ai_execution:
        walk(trace.ai_execution.output)
    return values


def _observed_result_text_values(trace: RPAAcceptedTrace, signal: Dict[str, Any]) -> set[str]:
    values: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            values.add(value.strip())

    def walk(value: Any) -> None:
        if isinstance(value, str):
            add(value)
        elif isinstance(value, dict):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    add(signal.get("observed_text"))
    walk(trace.output)
    if trace.ai_execution:
        walk(trace.ai_execution.output)
    return values


def _trace_output_is_action_evidence(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    if output.get("action_performed") is True or output.get("downloaded") is True:
        return True
    output_type = str(output.get("type") or output.get("action_type") or "").strip().lower()
    return output_type in {"click", "fill", "select", "press", "hover", "navigate", "download"}


def _trace_has_side_effect_evidence(trace: RPAAcceptedTrace) -> bool:
    if _trace_output_is_action_evidence(trace.output):
        return True
    if trace.ai_execution and _trace_output_is_action_evidence(trace.ai_execution.output):
        return True
    for signal_name in ("download", "navigation", "post_navigation", "popup", "tab", "set_input_files"):
        if _trace_signal(trace, signal_name):
            return True
    decision = _trace_signal(trace, "region_context_decision")
    if str(decision.get("used_as") or "").strip() == "action_targeting":
        return True
    action_type = str(decision.get("action_type") or "").strip().lower()
    return action_type in {"click", "fill", "select", "press", "hover", "goto", "navigate", "download"}


def _looks_like_extract_instruction(trace: RPAAcceptedTrace) -> bool:
    text = f"{trace.user_instruction or ''} {trace.description or ''}".lower()
    if not text.strip():
        return False
    markers = (
        "extract",
        "get ",
        "collect",
        "return",
        "read",
        "summary",
        "description",
        "获取",
        "提取",
        "读取",
        "收集",
        "返回",
        "简介",
        "介绍",
        "摘要",
    )
    return any(marker in text for marker in markers)


def _locator_candidate_dicts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _selected_indexes(value: Any) -> List[int]:
    if not isinstance(value, list):
        return []
    indexes: List[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            index = int(item)
        except Exception:
            continue
        if index < 0 or index in seen:
            continue
        seen.add(index)
        indexes.append(index)
    return indexes


def _prioritized_region_locator_candidates(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for index, group in enumerate(groups):
        if not group:
            continue
        candidates = list(group)
        for fallback_group in groups[index + 1 :]:
            for candidate in fallback_group:
                fallback = dict(candidate)
                fallback.pop("selected", None)
                candidates.append(fallback)
        return candidates
    return []


def _trace_region_value_locator_candidates(
    trace: RPAAcceptedTrace,
    region_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    nested_candidates: List[Dict[str, Any]] = []
    intersecting_elements = region_context.get("intersecting_elements")
    if isinstance(intersecting_elements, list):
        for element in intersecting_elements:
            if not isinstance(element, dict):
                continue
            candidates = element.get("nested_locator_candidates")
            nested_candidates.extend(_locator_candidate_dicts(candidates))
    return _prioritized_region_locator_candidates(
        nested_candidates,
        _locator_candidate_dicts(region_context.get("locator_candidates")),
        _locator_candidate_dicts(trace.locator_candidates),
    )


def _trace_region_runtime_ai_requirement(trace: RPAAcceptedTrace) -> Optional[bool]:
    region_kind = _trace_region_kind(trace)
    region_context = _trace_region_context(trace)
    if region_kind == "single_value":
        candidates = _trace_region_value_locator_candidates(trace, region_context)
        return not bool(TraceSkillCompiler()._best_locator(candidates))
    if region_kind == "table_region":
        table_summary = (
            region_context.get("table_summary") if isinstance(region_context.get("table_summary"), dict) else {}
        )
        return not bool(TraceSkillCompiler()._best_locator(list(table_summary.get("locator_candidates") or [])))
    if region_kind in {"list_sample", "list_region"}:
        list_summary = (
            region_context.get("list_summary") if isinstance(region_context.get("list_summary"), dict) else {}
        )
        item_selector = str(list_summary.get("item_selector") or "").strip()
        locator = TraceSkillCompiler()._best_locator(list(list_summary.get("container_locator_candidates") or []))
        return not bool(locator and item_selector)
    return None


def _xpath_literal(value: str) -> str:
    text = str(value)
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in text.split("'")) + ")"


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
    text = str(code or "")
    if f"page.locator({selector!r}).nth(" in text:
        return True
    selector_literal = repr(selector)
    assignment_pattern = re.compile(
        rf"(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*page\.locator\({re.escape(selector_literal)}\)"
    )
    for match in assignment_pattern.finditer(text):
        var_name = re.escape(match.group("var"))
        if re.search(rf"\b{var_name}\.nth\(", text[match.end():]):
            return True
    return False


def _should_preserve_runtime_ai_instruction(trace: RPAAcceptedTrace) -> bool:
    text = f"{trace.user_instruction or ''} {trace.description or ''}".lower()
    runtime_ai_signal = _trace_signal(trace, "runtime_ai")
    if runtime_ai_signal.get("preserve") is True or runtime_ai_signal.get("preserve_runtime_ai") is True:
        return True
    if not text.strip():
        return False
    strong_semantic_markers = (
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
        "最相关",
        "最匹配",
        "推荐",
        "最佳",
        "最适合",
    )
    if any(marker in text for marker in strong_semantic_markers):
        return True
    if not trace.ai_execution or not trace.ai_execution.code:
        return False
    output = trace.output
    return isinstance(output, dict) and bool(output.get("url") or output.get("value"))


def trace_requires_runtime_ai_replay(trace: RPAAcceptedTrace) -> bool:
    if trace.trace_type != RPATraceType.AI_OPERATION:
        return False
    if _has_selected_region_text_extract(trace):
        return False
    if _snapshot_extract_is_selected_text_region_evidence(trace):
        return True
    if _trace_signal(trace, "extract_snapshot") and TraceSkillCompiler._has_usable_snapshot_extract_fields(trace):
        return False
    if _has_heading_scoped_text_extract(trace):
        return False
    if _is_selected_region_local_text_extract(trace):
        return True
    if _is_region_scoped_free_text_extract(trace):
        return True
    if _should_preserve_runtime_ai_instruction(trace):
        return True
    if _trace_has_side_effect_evidence(trace) and not (trace.ai_execution and trace.ai_execution.code):
        return True
    region_runtime_requirement = _trace_region_runtime_ai_requirement(trace)
    if region_runtime_requirement is not None:
        return region_runtime_requirement
    if _embedded_ai_code_has_weak_empty_extract_evidence(trace):
        return True
    if trace.ai_execution and trace.ai_execution.code:
        return False
    return bool(trace.user_instruction or trace.description)


def traces_require_runtime_ai_replay(traces: Iterable[RPAAcceptedTrace]) -> bool:
    return any(trace_requires_runtime_ai_replay(trace) for trace in traces)


def _embedded_ai_code_has_weak_empty_extract_evidence(trace: RPAAcceptedTrace) -> bool:
    if trace.trace_type != RPATraceType.AI_OPERATION:
        return False
    if not trace.ai_execution or not trace.ai_execution.code:
        return False
    if not trace.output_key:
        return False
    if _trace_allows_empty_output(trace):
        return False
    if trace.ai_execution.output is None and trace.output is None:
        return False
    output = trace.ai_execution.output if trace.ai_execution.output is not None else trace.output
    return _is_empty_trace_output(output)


def _trace_allows_empty_output(trace: RPAAcceptedTrace) -> bool:
    contract = _trace_signal(trace, "output_contract")
    return contract.get("allow_empty") is True or contract.get("allow_empty_output") is True


def _is_empty_trace_output(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set)):
        return not value or all(_is_empty_trace_output(item) for item in value)
    if isinstance(value, dict):
        return not value or all(_is_empty_trace_output(item) for item in value.values())
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


def _parse_cli_value(key, value):
    if key in {{"_runtime_context", "_model_config"}}:
        try:
            return _json.loads(value)
        except Exception:
            return value
    return value


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = _parse_cli_value(k, v)
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


def _parse_cli_value(key, value):
    if key in {{"_runtime_context", "_model_config"}}:
        try:
            return _json.loads(value)
        except Exception:
            return value
    return value


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = _parse_cli_value(k, v)
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

