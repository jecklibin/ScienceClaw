import json
import logging
import re
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

RPA_PLAYWRIGHT_TIMEOUT_MS = 60000
RPA_NAVIGATION_TIMEOUT_MS = 60000


class PlaywrightGenerator:
    """Generate Playwright Python scripts from recorded RPA steps.

    Locators are pre-computed in the browser using a Playwright-codegen-style
    algorithm (role > testid > label > placeholder > alt > title > css).
    The generator simply translates the locator objects into Playwright API calls.
    """

    # Docker mode: connects to sandbox's browser via CDP
    RUNNER_TEMPLATE_DOCKER = '''\
import asyncio
import json as _json
import sys
import httpx
from playwright.async_api import async_playwright

_AI_COMMAND_URL = "{ai_command_url}"

async def _get_cdp_url() -> str:
    """Fetch CDP WebSocket URL from the local sandbox API."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("http://127.0.0.1:8080/v1/browser/info")
        resp.raise_for_status()
        return resp.json()["data"]["cdp_url"]


async def _ai_command(prompt: str, mode: str, page, token: str, url: str = None):
    """Call AI with prompt. mode='execute' runs Playwright code, mode='data' returns text."""
    _target_url = url or _AI_COMMAND_URL
    _ctx = ""
    try:
        _ctx = await page.inner_text("body")
        if len(_ctx) > 50000:
            _ctx = _ctx[:50000]
    except Exception:
        pass
    _headers = {{"Authorization": f"Bearer {{token}}"}} if token else {{}}
    async with httpx.AsyncClient(timeout=120) as _c:
        _r = await _c.post(
            _target_url,
            json={{"prompt": prompt, "page_context": _ctx, "mode": mode}},
            headers=_headers
        )
        _r.raise_for_status()
        try:
            _payload = _r.json()
        except Exception as _json_exc:
            raise RuntimeError(
                f"AI command returned non-JSON response (status={{_r.status_code}}): {{_r.text[:200]}}"
            ) from _json_exc
        _text = _payload["data"]["response"]
        if mode == "execute":
            _fn_src = "async def __ai():\\n" + "\\n".join("    " + l for l in _text.strip().split("\\n"))
            _ns = {{"page": page}}
            exec(_fn_src, _ns)
            await _ns["__ai"]()
            return None
        return _text


{execute_skill_func}


def _load_params():
    """Load params.json from skill directory if available."""
    try:
        from pathlib import Path as _P
        _pj = _P(__file__).parent / "params.json"
        if _pj.exists():
            return _json.loads(_pj.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {{}}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v
    _params = _load_params()
    _params.update(kwargs)
    kwargs = _params

    cdp_url = await _get_cdp_url()
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = await browser.new_context(no_viewport=True, accept_downloads=True)
    page = await context.new_page()
    page.set_default_timeout({default_timeout_ms})
    page.set_default_navigation_timeout({navigation_timeout_ms})
    try:
        _result = await execute_skill(page, **kwargs)
        if _result:
            print("SKILL_DATA:" + _json.dumps(_result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as e:
        print(f"SKILL_ERROR: {{e}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''

    # Local mode: launches local browser directly
    RUNNER_TEMPLATE_LOCAL = '''\
import asyncio
import json as _json
import sys
import httpx
from playwright.async_api import async_playwright

_AI_COMMAND_URL = "{ai_command_url}"


async def _ai_command(prompt: str, mode: str, page, token: str, url: str = None):
    """Call AI with prompt. mode='execute' runs Playwright code, mode='data' returns text."""
    _target_url = url or _AI_COMMAND_URL
    _ctx = ""
    try:
        _ctx = await page.inner_text("body")
        if len(_ctx) > 50000:
            _ctx = _ctx[:50000]
    except Exception:
        pass
    _headers = {{"Authorization": f"Bearer {{token}}"}} if token else {{}}
    async with httpx.AsyncClient(timeout=120) as _c:
        _r = await _c.post(
            _target_url,
            json={{"prompt": prompt, "page_context": _ctx, "mode": mode}},
            headers=_headers
        )
        _r.raise_for_status()
        try:
            _payload = _r.json()
        except Exception as _json_exc:
            raise RuntimeError(
                f"AI command returned non-JSON response (status={{_r.status_code}}): {{_r.text[:200]}}"
            ) from _json_exc
        _text = _payload["data"]["response"]
        if mode == "execute":
            _fn_src = "async def __ai():\\n" + "\\n".join("    " + l for l in _text.strip().split("\\n"))
            _ns = {{"page": page}}
            exec(_fn_src, _ns)
            await _ns["__ai"]()
            return None
        return _text


{execute_skill_func}


def _load_params():
    """Load params.json from skill directory if available."""
    try:
        from pathlib import Path as _P
        _pj = _P(__file__).parent / "params.json"
        if _pj.exists():
            return _json.loads(_pj.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {{}}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v
    _params = _load_params()
    _params.update(kwargs)
    kwargs = _params

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context(no_viewport=True, accept_downloads=True)
    page = await context.new_page()
    page.set_default_timeout({default_timeout_ms})
    page.set_default_navigation_timeout({navigation_timeout_ms})
    try:
        _result = await execute_skill(page, **kwargs)
        if _result:
            print("SKILL_DATA:" + _json.dumps(_result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as e:
        print(f"SKILL_ERROR: {{e}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''

    TEST_MODE_PREAMBLE = '''\

class StepExecutionError(Exception):
    def __init__(self, step_index: int, original_error: str = ""):
        self.step_index = step_index
        self.original_error = original_error
        super().__init__(f"STEP_FAILED:{step_index}:{original_error}")
'''

    def generate_script(
        self,
        steps: List[Dict[str, Any]],
        params: Dict[str, Any] = None,
        is_local: bool = False,
        test_mode: bool = False,
        ai_command_url: str | None = None,
    ) -> str:
        params = params or {}
        deduped = self._deduplicate_steps(steps)
        deduped = self._infer_missing_tab_transitions(deduped)
        deduped = self._normalize_step_signals(deduped)
        root_tab_id = deduped[0].get("tab_id") if deduped else None
        root_tab_id = root_tab_id or "tab-1"
        used_result_keys: Dict[str, int] = {}

        lines = [
            "",
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA recording."""',
            "    _results = {}",
            "    _ai_cmd_url = kwargs.get('_ai_command_url', _AI_COMMAND_URL)",
            f'    tabs = {{"{root_tab_id}": page}}',
            "    current_page = page",
        ]

        current_tab_id = root_tab_id
        prev_url = None
        prev_action = None
        # Add initial navigation if first step isn't a navigate action
        if deduped and deduped[0].get("action") not in ("navigate", "goto"):
            first_url = deduped[0].get("url", "")
            if first_url:
                lines.append(f'    await current_page.goto("{first_url}")')
                lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                lines.append("")
                prev_url = first_url

        for step_index, step in enumerate(deduped):
            action = step.get("action", "")
            target = step.get("target", "")
            value = step.get("value", "")
            url = step.get("url", "")
            desc = step.get("description", "")
            frame_path = step.get("frame_path") or []

            step_lines: List[str] = []

            if desc:
                lines.append(f"    # {desc}")

            # AI-generated script — embed directly with sync→async conversion
            if action == "ai_script":
                ai_code = step.get("value", "")
                if ai_code:
                    converted = self._sync_to_async(ai_code)
                    converted = self._inject_result_capture(converted)
                    converted = self._strip_locator_result_capture(converted)
                    for code_line in converted.split("\n"):
                        step_lines.append(f"    {code_line}" if code_line.strip() else "")
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                continue

            # AI command — call _ai_command(prompt, mode, page, token)
            if action == "ai_command":
                prompt_text = self._escape(step.get("prompt", ""))
                operation_code = step.get("operation_code") or ""
                operation_summary = self._escape(step.get("operation_summary") or "")
                data_prompt = self._escape(step.get("data_prompt") or "")
                data_value = step.get("data_value")
                output_var = step.get("output_variable") or ""
                result_mode = step.get("ai_result_mode") or "data_only"
                legacy_mode = step.get("ai_mode", "data")

                if not operation_code and legacy_mode == "execute":
                    step_lines.append(f'    await _ai_command("{prompt_text}", "execute", current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url)')
                    lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                    lines.append("")
                    continue

                has_operation = bool(operation_code)
                has_data = result_mode in {"data_only", "operation_and_data"} or (not operation_code and legacy_mode == "data")

                # Operation: call AI at runtime so it can see the actual page state
                # instead of hardcoding recorded code that may break on page changes.
                if has_operation:
                    operation_prompt = operation_summary or prompt_text
                    step_lines.append(f'    await _ai_command("{operation_prompt}", "execute", current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url)')

                # Stability wait between operation and data extraction
                if has_operation and has_data:
                    step_lines.append('    await current_page.wait_for_timeout(500)')
                    step_lines.append('    try:')
                    step_lines.append('        await current_page.wait_for_load_state("domcontentloaded", timeout=5000)')
                    step_lines.append('    except Exception:')
                    step_lines.append('        pass')
                    step_lines.append('    try:')
                    step_lines.append('        await current_page.wait_for_load_state("networkidle", timeout=2000)')
                    step_lines.append('    except Exception:')
                    step_lines.append('        pass')
                    step_lines.append('    await current_page.wait_for_timeout(500)')

                # Data extraction
                if has_data and (data_prompt or prompt_text or data_value is not None):
                    result_var = output_var or f"ai_result_{step_index + 1}"
                    result_key = output_var or f"ai_command_{step_index + 1}"
                    effective_prompt = data_prompt or prompt_text
                    step_lines.append(f'    {result_var} = await _ai_command("{effective_prompt}", "data", current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url)')
                    step_lines.append(f'    _results["{result_key}"] = {result_var}')

                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                continue

            # Navigation
            if action == "navigate" or (action == "goto" and url):
                step_lines.append(f'    await current_page.goto("{url}")')
                step_lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                prev_url = url
                prev_action = "navigate"
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                continue

            if action == "switch_tab":
                target_tab_id = step.get("target_tab_id") or step.get("tab_id") or root_tab_id
                step_lines.append(f'    current_page = tabs["{target_tab_id}"]')
                step_lines.append("    await current_page.bring_to_front()")
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                current_tab_id = target_tab_id
                prev_action = action
                continue

            if action == "close_tab":
                closing_tab_id = step.get("tab_id") or step.get("source_tab_id")
                fallback_tab_id = step.get("target_tab_id")
                if closing_tab_id:
                    step_lines.append(f'    closing_page = tabs.pop("{closing_tab_id}", current_page)')
                else:
                    step_lines.append("    closing_page = current_page")
                step_lines.append("    await closing_page.close()")
                if closing_tab_id == current_tab_id:
                    if fallback_tab_id:
                        step_lines.append(f'    current_page = tabs["{fallback_tab_id}"]')
                        step_lines.append("    await current_page.bring_to_front()")
                        current_tab_id = fallback_tab_id
                    else:
                        current_tab_id = closing_tab_id
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                prev_action = action
                continue

            # Standalone download step has no locator — handle before _build_locator
            if action == "download":
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', (value or "file").split('.')[0]) or "file"
                step_lines.append(f'    # NOTE: download of "{value}" was triggered by a previous action')
                step_lines.append(f'    # If this step appears, manually wrap the triggering click with expect_download()')
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                continue

            scope_var = "current_page"
            if frame_path:
                scope_var = "frame_scope"
                frame_parent = "current_page"
                for frame_selector in frame_path:
                    step_lines.append(
                        f'    frame_scope = {frame_parent}.frame_locator("{self._escape(frame_selector)}")'
                    )
                    frame_parent = "frame_scope"

            # Prefer adaptive collection locators for AI steps like "click the first item".
            locator = self._build_adaptive_locator_for_step(step, scope_var)
            if not locator:
                # Parse the locator object from target (stored as JSON string)
                locator = self._build_locator_for_page(target, scope_var)

            popup_signal = self._popup_signal(step)
            download_signal = self._download_signal(step)
            if popup_signal and not self._should_materialize_popup(deduped, step_index, popup_signal, download_signal):
                popup_signal = None
            if action in {"click", "press"} and (popup_signal or download_signal):
                interaction = f'await {locator}.click()' if action == "click" else f'await {locator}.press("{value}")'
                outer_indent = "    "
                if download_signal:
                    step_lines.append(f"{outer_indent}async with current_page.expect_download() as _dl_info:")
                    outer_indent += "    "
                if popup_signal:
                    step_lines.append(f"{outer_indent}async with current_page.expect_popup() as popup_info:")
                    outer_indent += "    "
                step_lines.append(f"{outer_indent}{interaction}")

                if popup_signal:
                    popup_indent = "    " + ("    " if download_signal else "")
                    target_tab_id = popup_signal.get("target_tab_id") or step.get("target_tab_id") or "tab-new"
                    step_lines.append(f"{popup_indent}new_page = await popup_info.value")
                    step_lines.append(f'{popup_indent}tabs["{target_tab_id}"] = new_page')
                    step_lines.append(f"{popup_indent}current_page = new_page")
                    current_tab_id = target_tab_id

                if download_signal:
                    download_name = download_signal.get("filename") or value or "file"
                    safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', str(download_name).split('.')[0]) or "file"
                    step_lines.append("    _dl = await _dl_info.value")
                    step_lines.append("    _dl_dir = kwargs.get('_downloads_dir', '.')")
                    step_lines.append("    import os as _os; _os.makedirs(_dl_dir, exist_ok=True)")
                    step_lines.append("    _dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)")
                    step_lines.append("    await _dl.save_as(_dl_dest)")
                    step_lines.append(f'    _results["download_{safe_name}"] = {{"filename": _dl.suggested_filename, "path": _dl_dest}}')

                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                prev_action = action
                continue

            if action == "navigate_click":
                step_lines.append(f"    async with current_page.expect_navigation(wait_until='domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS}):")
                step_lines.append(f"        await {locator}.click()")
            elif action == "navigate_press":
                step_lines.append(f"    async with current_page.expect_navigation(wait_until='domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS}):")
                step_lines.append(f'        await {locator}.press("{value}")')
            elif action == "click":
                step_lines.append(f"    await {locator}.click()")
                # After non-navigation click, wait briefly for UI changes
                step_lines.append("    await current_page.wait_for_timeout(500)")
            elif action == "download_click":
                # Click that triggers a file download — wrap with expect_download
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', (value or "file").split('.')[0]) or "file"
                step_lines.append(f"    async with current_page.expect_download() as _dl_info:")
                step_lines.append(f"        await {locator}.click()")
                step_lines.append(f"    _dl = await _dl_info.value")
                step_lines.append(f"    _dl_dir = kwargs.get('_downloads_dir', '.')")
                step_lines.append(f"    import os as _os; _os.makedirs(_dl_dir, exist_ok=True)")
                step_lines.append(f"    _dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)")
                step_lines.append(f"    await _dl.save_as(_dl_dest)")
                step_lines.append(f'    _results["download_{safe_name}"] = {{"filename": _dl.suggested_filename, "path": _dl_dest}}')
            elif action == "fill":
                fill_value = self._maybe_parameterize(value, params)
                step_lines.append(f"    await {locator}.fill({fill_value})")
            elif action == "check":
                step_lines.append(f"    await {locator}.check()")
            elif action == "uncheck":
                step_lines.append(f"    await {locator}.uncheck()")
            elif action == "set_input_files":
                input_files_value = self._build_input_files_value(step, value, params)
                step_lines.append(f"    await {locator}.set_input_files({input_files_value})")
            elif action == "extract_text":
                result_var = f"extract_text_value_{step_index + 1}"
                result_key = self._build_extract_result_key(step, used_result_keys)
                step_lines.append(f"    {result_var} = await {locator}.inner_text()")
                step_lines.append(f'    _results["{result_key}"] = {result_var}')
            elif action == "press":
                step_lines.append(f'    await {locator}.press("{value}")')
            elif action == "select":
                step_lines.append(f'    await {locator}.select_option("{value}")')

            prev_action = action
            lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
            lines.append("")

        lines.append("    return _results")

        # Wrap execute_skill function with the runner boilerplate
        execute_skill_func = "\n".join(lines)
        if test_mode:
            execute_skill_func = self.TEST_MODE_PREAMBLE + execute_skill_func
        template = self.RUNNER_TEMPLATE_LOCAL if is_local else self.RUNNER_TEMPLATE_DOCKER
        return template.format(
            execute_skill_func=execute_skill_func,
            default_timeout_ms=RPA_PLAYWRIGHT_TIMEOUT_MS,
            navigation_timeout_ms=RPA_NAVIGATION_TIMEOUT_MS,
            ai_command_url=ai_command_url or self._get_ai_command_url(is_local),
        )

    @staticmethod
    def _get_ai_command_url(is_local: bool) -> str:
        """Return the backend AI command URL for the generated script."""
        import os
        explicit = os.environ.get("RPA_AI_COMMAND_URL", "").strip()
        if explicit:
            return explicit.rstrip("/")
        # Default: assume backend is on localhost:8000
        return "http://127.0.0.1:8000/api/v1/rpa/ai-command"

    @staticmethod
    def _wrap_step_lines(step_lines: List[str], step_index: int, test_mode: bool) -> List[str]:
        """Optionally wrap step code lines in try/except for test mode error reporting."""
        if not test_mode or not step_lines:
            return step_lines
        wrapped = ["    try:"]
        for line in step_lines:
            if line == "":
                wrapped.append("")
            else:
                wrapped.append("    " + line)
        wrapped.append("    except StepExecutionError:")
        wrapped.append("        raise")
        wrapped.append("    except Exception as _e:")
        wrapped.append(f"        raise StepExecutionError(step_index={step_index}, original_error=str(_e))")
        return wrapped

    def _build_extract_result_key(self, step: Dict[str, Any], used_result_keys: Dict[str, int]) -> str:
        key = self._normalize_result_key(step.get("result_key"))
        if not key:
            fallback_count = used_result_keys.get("extract_text", 0) + 1
            used_result_keys["extract_text"] = fallback_count
            return f"extract_text_{fallback_count}"

        count = used_result_keys.get(key, 0) + 1
        used_result_keys[key] = count
        if count == 1:
            return key
        return f"{key}_{count}"

    def _normalize_result_key(self, raw_key: Any) -> str:
        text = str(raw_key or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"[^a-z0-9_]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        if not text:
            return ""
        if text[0].isdigit():
            text = f"extract_{text}"
        return text[:64]

    @classmethod
    def _infer_missing_tab_transitions(cls, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Backfill tab open/switch semantics for older recordings that only carry tab_id."""
        if not steps:
            return steps

        normalized: List[Dict[str, Any]] = []
        current_tab_id = steps[0].get("tab_id") or "tab-1"
        known_tabs = {current_tab_id}

        for original_step in steps:
            step = dict(original_step)
            step_tab_id = step.get("tab_id") or current_tab_id

            if step_tab_id != current_tab_id:
                previous_step = normalized[-1] if normalized else None
                if step_tab_id not in known_tabs and previous_step and previous_step.get("action") in {"click", "press", "open_tab_click"}:
                    signals = dict(previous_step.get("signals") or {})
                    popup_signal = dict(signals.get("popup") or {})
                    popup_signal.setdefault("source_tab_id", current_tab_id)
                    popup_signal["target_tab_id"] = step_tab_id
                    signals["popup"] = popup_signal
                    previous_step["signals"] = signals
                    previous_step["source_tab_id"] = current_tab_id
                    previous_step["target_tab_id"] = step_tab_id
                    known_tabs.add(step_tab_id)
                elif step_tab_id in known_tabs:
                    normalized.append(
                        {
                            "action": "switch_tab",
                            "tab_id": current_tab_id,
                            "target_tab_id": step_tab_id,
                            "description": "Switch tab",
                            "url": step.get("url", ""),
                        }
                    )
                else:
                    known_tabs.add(step_tab_id)

                current_tab_id = step_tab_id

            previous_step = normalized[-1] if normalized else None
            if (
                step.get("action") == "navigate"
                and previous_step
                and cls._popup_target_tab_id(previous_step) == step_tab_id
            ):
                continue

            normalized.append(step)

        return normalized

    @classmethod
    def _popup_target_tab_id(cls, step: Dict[str, Any]) -> str:
        signals = step.get("signals") or {}
        popup_signal = signals.get("popup") if isinstance(signals, dict) else None
        if isinstance(popup_signal, dict) and popup_signal.get("target_tab_id"):
            return str(popup_signal.get("target_tab_id"))
        return str(step.get("target_tab_id") or "")

    @staticmethod
    def _popup_signal(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        signals = step.get("signals")
        if not isinstance(signals, dict):
            return None
        popup_signal = signals.get("popup")
        if isinstance(popup_signal, dict):
            return popup_signal
        return None

    @staticmethod
    def _download_signal(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        signals = step.get("signals")
        if not isinstance(signals, dict):
            return None
        download_signal = signals.get("download")
        if isinstance(download_signal, dict):
            return download_signal
        return None

    @classmethod
    def _normalize_step_signals(cls, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for original_step in steps:
            step = dict(original_step)
            signals = dict(step.get("signals") or {})
            action = step.get("action")

            if action == "open_tab_click":
                popup_signal = dict(signals.get("popup") or {})
                popup_signal.setdefault("source_tab_id", step.get("source_tab_id") or step.get("tab_id"))
                if step.get("target_tab_id"):
                    popup_signal.setdefault("target_tab_id", step.get("target_tab_id"))
                signals["popup"] = popup_signal
                step["action"] = "click"

            if action == "download_click":
                download_signal = dict(signals.get("download") or {})
                if step.get("value"):
                    download_signal.setdefault("filename", step.get("value"))
                if step.get("tab_id"):
                    download_signal.setdefault("tab_id", step.get("tab_id"))
                signals["download"] = download_signal
                step["action"] = "click"

            step["signals"] = signals

            if step.get("action") == "download" and cls._merge_standalone_download_step(normalized, step):
                continue

            normalized.append(step)

        return normalized

    @classmethod
    def _merge_standalone_download_step(cls, normalized_steps: List[Dict[str, Any]], download_step: Dict[str, Any]) -> bool:
        download_tab_id = str(download_step.get("tab_id") or "")
        download_name = str(download_step.get("value") or "file")

        for previous_step in reversed(normalized_steps):
            if previous_step.get("action") not in {"click", "press"}:
                continue
            previous_tab_id = str(previous_step.get("tab_id") or "")
            popup_target_tab_id = cls._popup_target_tab_id(previous_step)
            if download_tab_id and download_tab_id not in {previous_tab_id, popup_target_tab_id}:
                continue

            signals = dict(previous_step.get("signals") or {})
            download_signal = dict(signals.get("download") or {})
            if download_name:
                download_signal.setdefault("filename", download_name)
            if download_tab_id:
                download_signal.setdefault("tab_id", download_tab_id)
            signals["download"] = download_signal
            previous_step["signals"] = signals
            if download_name and not previous_step.get("value"):
                previous_step["value"] = download_name
            return True

        return False

    @classmethod
    def _should_materialize_popup(
        cls,
        steps: List[Dict[str, Any]],
        step_index: int,
        popup_signal: Dict[str, Any],
        download_signal: Optional[Dict[str, Any]],
    ) -> bool:
        target_tab_id = str(popup_signal.get("target_tab_id") or "")
        if not target_tab_id:
            return False
        if not download_signal:
            return True

        for future_step in steps[step_index + 1:]:
            future_tab_id = str(future_step.get("tab_id") or "")
            future_target_tab_id = str(future_step.get("target_tab_id") or "")
            if future_tab_id == target_tab_id or future_target_tab_id == target_tab_id:
                return True
            future_popup_tab_id = cls._popup_target_tab_id(future_step)
            if future_popup_tab_id == target_tab_id:
                return True
        return False

    @staticmethod
    def _deduplicate_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove consecutive duplicate actions (same action + same target).

        For fill actions on the same target, keep the LAST one (final typed value).
        """
        if not steps:
            return steps
        result = [steps[0]]
        for step in steps[1:]:
            prev = result[-1]
            # Same action and same target → replace with the later one
            # (keeps the final/complete value for fill actions)
            # BUT: never deduplicate AI steps (each AI instruction is unique)
            if (step.get("action") == prev.get("action")
                    and step.get("target") == prev.get("target")
                    and step.get("action") not in ("navigate", "ai_script", "ai_command")):
                result[-1] = step  # Replace previous with current (keep last)
                continue
            result.append(step)
        return result

    def _build_locator(self, target: str) -> str:
        """Convert a locator JSON object (from browser capture) to Playwright API call.

        The locator object has a 'method' field indicating the strategy:
          role     → page.get_by_role(role, name=name, exact=True)
          testid   → page.get_by_test_id(value)
          label    → page.get_by_label(value, exact=True)
          placeholder → page.get_by_placeholder(value, exact=True)
          alt      → page.get_by_alt_text(value, exact=True)
          title    → page.get_by_title(value, exact=True)
          css      → page.locator(css_selector)
        """
        try:
            loc = json.loads(target) if isinstance(target, str) else target
        except (json.JSONDecodeError, TypeError):
            # Fallback: treat as raw CSS selector
            if target:
                return f'page.locator("{self._escape(target)}")'
            return 'page.locator("body")'

        if not isinstance(loc, dict):
            return f'page.locator("{self._escape(str(target))}")'

        method = loc.get("method", "css")

        if method == "collection_item":
            collection = loc.get("collection", {"method": "css", "value": "body"})
            item = loc.get("item", {"method": "css", "value": "body"})
            ordinal = str(loc.get("ordinal") or "first")
            collection_loc = self._build_locator(json.dumps(collection) if isinstance(collection, dict) else str(collection))
            scoped_collection = self._apply_ordinal_to_locator(collection_loc, ordinal)
            item_loc = self._build_locator(json.dumps(item) if isinstance(item, dict) else str(item))
            if item_loc.startswith("page."):
                return f'{scoped_collection}{item_loc[len("page"):]}'
            return f'{scoped_collection}.locator("{self._escape(str(item))}")'

        if method == "role":
            role = loc.get("role", "button")
            name = self._escape(loc.get("name", ""))
            if name:
                return f'page.get_by_role("{role}", name="{name}", exact=True)'
            return f'page.get_by_role("{role}")'

        if method == "testid":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_test_id("{val}")'

        if method == "label":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_label("{val}", exact=True)'

        if method == "placeholder":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_placeholder("{val}", exact=True)'

        if method == "alt":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_alt_text("{val}", exact=True)'

        if method == "title":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_title("{val}", exact=True)'

        if method == "text":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_text("{val}", exact=True)'

        if method == "nested":
            # parent >> child locator chaining
            parent = loc.get("parent", {})
            child = loc.get("child", {})
            parent_loc = self._build_locator(json.dumps(parent) if isinstance(parent, dict) else str(parent))
            child_loc = self._build_locator(json.dumps(child) if isinstance(child, dict) else str(child))
            # Chain the child query directly on the parent locator.
            # Examples:
            #   page.locator("button")        -> parent.locator("button")
            #   page.get_by_role("link")      -> parent.get_by_role("link")
            # Wrapping everything in .locator(...) breaks non-CSS child locators.
            if child_loc.startswith("page."):
                return f'{parent_loc}{child_loc[len("page"):]}'
            return f'{parent_loc}.locator("{self._escape(str(child))}")'

        if method == "nth":
            base = loc.get("locator", loc.get("base", {"method": "css", "value": "body"}))
            base_loc = self._build_locator(json.dumps(base) if isinstance(base, dict) else str(base))
            try:
                index = max(int(loc.get("index", 0)), 0)
            except Exception:
                index = 0
            return f"{base_loc}.nth({index})"

        # css (default)
        val = self._escape(loc.get("value", "body"))
        return f'page.locator("{val}")'

    def _build_locator_for_page(self, target: str, page_var: str) -> str:
        locator = self._build_locator(target)
        if page_var == "page":
            return locator
        if locator.startswith("page."):
            return f"{page_var}.{locator[len('page.'):]}"
        return locator

    def _build_adaptive_locator_for_step(self, step: Dict[str, Any], page_var: str) -> Optional[str]:
        ordinal = step.get("ordinal")
        if not ordinal:
            return None

        collection_hint = step.get("collection_hint") or {}
        item_hint = step.get("item_hint") or {}
        collection_locator = (collection_hint.get("container_hint") or {}).get("locator")
        item_locator = item_hint.get("locator")
        adaptive_target: Optional[Dict[str, Any]] = None
        if collection_locator and item_locator:
            adaptive_target = {
                "method": "collection_item",
                "collection": collection_locator,
                "item": item_locator,
                "ordinal": str(ordinal),
            }
        elif item_locator:
            adaptive_target = item_locator
        elif item_hint.get("role"):
            adaptive_target = {"method": "role", "role": item_hint["role"]}

        if not adaptive_target:
            return None

        locator = self._build_locator_for_page(json.dumps(adaptive_target), page_var)
        if adaptive_target.get("method") == "collection_item":
            return locator
        return self._apply_ordinal_to_locator(locator, str(ordinal))

    @staticmethod
    def _apply_ordinal_to_locator(locator: str, ordinal: str) -> str:
        normalized = (ordinal or "first").strip().lower()
        if normalized == "first":
            return f"{locator}.first"
        if normalized == "last":
            return f"{locator}.last"
        try:
            index = max(int(normalized) - 1, 0)
        except Exception:
            index = 0
        return f"{locator}.nth({index})"

    @staticmethod
    def _escape(s: str) -> str:
        """Escape and normalize a string for embedding in Python source code."""
        # Collapse all whitespace (newlines, tabs, multiple spaces) into single space
        import re
        s = re.sub(r'\s+', ' ', s).strip()
        return s.replace('\\', '\\\\').replace('"', '\\"')

    def _maybe_parameterize(self, value: str, params: Dict[str, Any]) -> str:
        """Check if value should be a parameter."""
        for param_name, param_info in params.items():
            if param_info.get("original_value") == value:
                if param_info.get("sensitive"):
                    # No default value for sensitive params
                    return f"kwargs['{param_name}']"
                return f"kwargs.get('{param_name}', '{value}')"
        safe = value.replace("'", "\\'")
        return f"'{safe}'"

    def _build_input_files_value(self, step: Dict[str, Any], value: str, params: Dict[str, Any]) -> str:
        signals = step.get("signals")
        files = None
        if isinstance(signals, dict):
            payload = signals.get("set_input_files")
            if isinstance(payload, dict) and isinstance(payload.get("files"), list):
                files = [str(item) for item in payload.get("files") if str(item)]

        if files and len(files) > 1:
            escaped = [item.replace("\\", "\\\\").replace("'", "\\'") for item in files]
            return "[" + ", ".join(f"'{item}'" for item in escaped) + "]"

        effective_value = files[0] if files else value
        return self._maybe_parameterize(str(effective_value or ""), params)

    @staticmethod
    def _sync_to_async(code: str) -> str:
        """Convert Playwright sync API code to async by adding await."""
        import re as _re
        lines = code.split("\n")
        result = []
        for line in lines:
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]
            if stripped and not stripped.startswith("#") and not stripped.startswith("def "):
                if stripped.startswith("page.") or _re.match(r'^(\w[\w\s,]*=\s*)(await\s+)?(page\..+)$', stripped):
                    if not stripped.startswith("await "):
                        # Check for assignment: var = page.xxx or var = await page.xxx
                        assign_match = _re.match(r'^(\w[\w\s,]*=\s*)(await\s+)?(page\..+)$', stripped)
                        if assign_match:
                            last_call = _re.search(r'\.(\w+)\([^)]*\)\s*$', assign_match.group(3))
                            if last_call and last_call.group(1) in PlaywrightGenerator._LOCATOR_BUILDER_METHODS:
                                result.append(f"{indent}{assign_match.group(1)}{assign_match.group(3)}")
                                continue
                            # If already has await, keep as-is; otherwise add await
                            if assign_match.group(2):  # already has await
                                result.append(line)
                            else:
                                result.append(f"{indent}{assign_match.group(1)}await {assign_match.group(3)}")
                            continue
                        result.append(f"{indent}await {stripped}")
                        continue
            result.append(line)
        return "\n".join(result)

    # Methods whose return value is not data (actions, not queries)
    _ACTION_METHODS = frozenset({
        'click', 'dblclick', 'fill', 'press', 'type', 'check', 'uncheck',
        'select_option', 'set_input_files', 'hover', 'focus', 'blur',
        'dispatch_event', 'scroll_into_view_if_needed',
        'goto', 'go_back', 'go_forward', 'reload',
        'wait_for_timeout', 'wait_for_load_state', 'wait_for_selector',
        'wait_for_url', 'wait_for_event', 'wait_for_function',
        'bring_to_front', 'close', 'set_content',
        'set_default_timeout', 'set_default_navigation_timeout',
        'add_init_script', 'expose_function', 'route', 'unroute',
    })

    _LOCATOR_BUILDER_METHODS = frozenset({
        'locator', 'frame_locator',
        'get_by_role', 'get_by_text', 'get_by_label', 'get_by_placeholder',
        'get_by_alt_text', 'get_by_title', 'get_by_test_id',
        'nth', 'first', 'last', 'filter',
    })

    _ASSIGN_RE = re.compile(r'^(\w+)\s*=\s*(?:await\s+)?page\.')
    _GENERIC_ASSIGN_RE = re.compile(r'^(?P<var>\w+)\s*=\s*(?:await\s+)?(?P<rhs>.+)$')
    _RESULT_CAPTURE_RE = re.compile(r'^_results\["[^"]+"\]\s*=\s*(?P<var>\w+)\s*$')

    @classmethod
    def _inject_result_capture(cls, code: str) -> str:
        """After data-extraction assignments, inject _results[var] = var."""
        lines = code.split('\n')
        result = []
        for line in lines:
            result.append(line)
            stripped = line.strip()
            m = cls._ASSIGN_RE.match(stripped)
            if not m:
                continue
            var_name = m.group(1)
            # Find the last method call in the line
            last_call = re.search(r'\.(\w+)\([^)]*\)\s*$', stripped)
            if last_call and last_call.group(1) in cls._LOCATOR_BUILDER_METHODS:
                continue
            if last_call and last_call.group(1) in cls._ACTION_METHODS:
                continue
            indent = line[:len(line) - len(line.lstrip())]
            result.append(f'{indent}_results["{var_name}"] = {var_name}')
        return '\n'.join(result)

    @classmethod
    def _strip_locator_result_capture(cls, code: str) -> str:
        """Drop `_results[...] = var` lines when `var` still points to a locator builder."""
        lines = code.split('\n')
        result = []
        locator_vars = set()
        for line in lines:
            stripped = line.strip()
            assign_match = cls._GENERIC_ASSIGN_RE.match(stripped)
            if assign_match:
                var_name = assign_match.group("var")
                rhs = assign_match.group("rhs")
                last_call = re.search(r'\.(\w+)\([^)]*\)\s*$', rhs)
                if last_call and last_call.group(1) in cls._LOCATOR_BUILDER_METHODS:
                    locator_vars.add(var_name)
                else:
                    locator_vars.discard(var_name)

            capture_match = cls._RESULT_CAPTURE_RE.match(stripped)
            if capture_match and capture_match.group("var") in locator_vars:
                continue

            result.append(line)
        return '\n'.join(result)
