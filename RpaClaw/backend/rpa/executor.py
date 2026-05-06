import json
import logging
import asyncio
import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Any, Callable, Optional

if TYPE_CHECKING:
    from playwright.async_api import Browser
else:
    Browser = Any

from .playwright_security import get_context_kwargs

logger = logging.getLogger(__name__)

RPA_PAGE_TIMEOUT_MS = 60000
TRACE_LOG_RE = re.compile(r"^TRACE_(START|DONE|ERROR)\s+(\d+):")


async def _collect_page_telemetry(page: Any, downloads_dir: Optional[str]) -> Dict[str, Any]:
    telemetry: Dict[str, Any] = {"final_url": "", "visible_text": "", "downloads": []}
    if page is not None:
        try:
            telemetry["final_url"] = str(getattr(page, "url", "") or "")
        except Exception:
            pass
        try:
            telemetry["visible_text"] = await page.locator("body").inner_text(timeout=2000)
        except Exception:
            pass
    if downloads_dir:
        try:
            for path in Path(downloads_dir).glob("*"):
                if path.is_file():
                    telemetry["downloads"].append(
                        {"filename": path.name, "path": str(path), "size": path.stat().st_size}
                    )
        except Exception:
            pass
    return telemetry


def _accepts_kwarg(func: Callable[..., Any], name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == name:
            return True
    return False


def _current_failed_trace_index(trace_state: Dict[str, Optional[int]]) -> Optional[int]:
    active_index = trace_state.get("active_index")
    if active_index is not None:
        return active_index
    return trace_state.get("failed_index")


class ScriptExecutor:
    """Execute generated Playwright scripts via CDP browser connection."""

    async def execute(
        self,
        browser: Browser,
        script: str,
        on_log: Optional[Callable[[str], None]] = None,
        timeout: float = 90.0,
        session_id: Optional[str] = None,
        page_registry: Optional[Any] = None,
        session_manager: Optional[Any] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        downloads_dir: Optional[str] = None,
        setup_navigation: Optional[list[str]] = None,
        pw_loop_runner: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Execute script in a new BrowserContext.

        pw_loop_runner: if provided (LocalCDPConnector.run_in_pw_loop), all
        Playwright coroutines are scheduled on the dedicated Playwright event loop
        to avoid "Future attached to a different loop" on Windows.
        """
        namespace: Dict[str, Any] = {}
        exec(compile(script, "<rpa_script>", "exec"), namespace)

        execute_skill = namespace.get("execute_skill")
        if not execute_skill:
            return {"success": False, "output": "", "error": "No execute_skill() function in script"}

        skill_kwargs = dict(kwargs or {})
        if downloads_dir:
            skill_kwargs.setdefault("_downloads_dir", downloads_dir)

        trace_state: Dict[str, Optional[int]] = {"active_index": None, "failed_index": None}

        def _emit_log(message: str) -> None:
            text = str(message)
            match = TRACE_LOG_RE.match(text)
            if match:
                event = match.group(1)
                trace_index = int(match.group(2))
                if event == "START":
                    trace_state["active_index"] = trace_index
                elif event == "DONE" and trace_state.get("active_index") == trace_index:
                    trace_state["active_index"] = None
                elif event == "ERROR":
                    trace_state["failed_index"] = trace_index
                    if trace_state.get("active_index") == trace_index:
                        trace_state["active_index"] = None
            if on_log:
                on_log(text)

        if _accepts_kwarg(execute_skill, "_on_log"):
            skill_kwargs.setdefault("_on_log", _emit_log)

        async def _run():
            context = None
            page = None
            try:
                _emit_log("Creating browser context...")
                context = await browser.new_context(**get_context_kwargs())
                page = await context.new_page()
                page.set_default_timeout(RPA_PAGE_TIMEOUT_MS)
                page.set_default_navigation_timeout(RPA_PAGE_TIMEOUT_MS)

                if session_id and page_registry:
                    page_registry[session_id] = page
                if session_id and session_manager:
                    session_manager.attach_context(session_id, context)
                    await session_manager.register_page(session_id, page, make_active=True)

                    def on_context_page(new_page):
                        asyncio.create_task(
                            session_manager.register_context_page(
                                session_id,
                                new_page,
                                make_active=True,
                            )
                        )

                    context.on("page", on_context_page)

                for setup_url in setup_navigation or []:
                    url = str(setup_url or "").strip()
                    if not url:
                        continue
                    _emit_log(f"Setup navigation: {url}")
                    await page.goto(url, wait_until="domcontentloaded")
                    await page.wait_for_load_state("domcontentloaded")

                _emit_log("Executing script...")

                _result = await asyncio.wait_for(
                    execute_skill(page, **skill_kwargs),
                    timeout=timeout,
                )
                await page.wait_for_timeout(3000)

                if _result:
                    output = "SKILL_DATA:" + json.dumps(_result, ensure_ascii=False, default=str) + "\nSKILL_SUCCESS"
                else:
                    output = "SKILL_SUCCESS"
                telemetry = await _collect_page_telemetry(page, downloads_dir)
                _emit_log("Execution completed successfully")
                return {"success": True, "output": output, "data": _result or {}, "telemetry": telemetry}

            except asyncio.TimeoutError:
                output = f"SKILL_ERROR: Script did not complete within {timeout}s"
                _emit_log(output)
                failed_step_index = _current_failed_trace_index(trace_state)
                telemetry = await _collect_page_telemetry(page, downloads_dir)
                return {
                    "success": False,
                    "output": output,
                    "error": f"Timeout after {timeout}s",
                    "failed_step_index": failed_step_index,
                    "telemetry": telemetry,
                }

            except Exception as e:
                failed_step_index = None
                original_error = str(e)

                error_str = str(e)
                if "STEP_FAILED:" in error_str:
                    try:
                        parts = error_str.split("STEP_FAILED:", 1)[1].split(":", 1)
                        failed_step_index = int(parts[0])
                        original_error = parts[1] if len(parts) > 1 else error_str
                    except (ValueError, IndexError):
                        pass

                output = f"SKILL_ERROR: {original_error}"
                if failed_step_index is None:
                    failed_step_index = _current_failed_trace_index(trace_state)
                if failed_step_index is not None:
                    _emit_log(f"Step {failed_step_index + 1} failed: {original_error}")
                else:
                    _emit_log(f"Execution failed: {original_error}")
                telemetry = await _collect_page_telemetry(page, downloads_dir)
                return {
                    "success": False,
                    "output": output,
                    "error": original_error,
                    "failed_step_index": failed_step_index,
                    "telemetry": telemetry,
                }

            finally:
                if session_id and page_registry and session_id in page_registry:
                    page_registry.pop(session_id, None)
                if session_id and session_manager:
                    session_manager.detach_context(session_id, context)
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass

        if pw_loop_runner:
            return await pw_loop_runner(_run())
        return await _run()
