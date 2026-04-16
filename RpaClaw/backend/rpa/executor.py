import json
import logging
import asyncio
from typing import Dict, Any, Callable, Optional

from playwright.async_api import Browser

from .playwright_security import get_context_kwargs

logger = logging.getLogger(__name__)

RPA_PAGE_TIMEOUT_MS = 60000


def _make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return str(value)


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
        pw_loop_runner: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Execute script in a new BrowserContext.

        pw_loop_runner: if provided (LocalCDPConnector.run_in_pw_loop), all
        Playwright coroutines are scheduled on the dedicated Playwright event loop
        to avoid "Future attached to a different loop" on Windows.
        """
        namespace: Dict[str, Any] = {}
        exec(compile(script, "<rpa_script>", "exec"), namespace)

        if "execute_skill" not in namespace:
            return {"success": False, "output": "", "error": "No execute_skill() function in script"}

        skill_kwargs = dict(kwargs or {})
        if downloads_dir:
            skill_kwargs.setdefault("_downloads_dir", downloads_dir)

        async def _run():
            context = None
            try:
                if on_log:
                    on_log("Creating browser context...")
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

                if on_log:
                    on_log("Executing script...")

                _result = await asyncio.wait_for(
                    namespace["execute_skill"](page, **skill_kwargs),
                    timeout=timeout,
                )
                await page.wait_for_timeout(3000)
                safe_result = _make_json_safe(_result or {})

                if _result:
                    output = "SKILL_DATA:" + json.dumps(safe_result, ensure_ascii=False, default=str) + "\nSKILL_SUCCESS"
                else:
                    output = "SKILL_SUCCESS"
                if on_log:
                    on_log("Execution completed successfully")
                return {"success": True, "output": output, "data": safe_result}

            except asyncio.TimeoutError:
                output = f"SKILL_ERROR: Script did not complete within {timeout}s"
                if on_log:
                    on_log(output)
                return {"success": False, "output": output, "error": f"Timeout after {timeout}s", "failed_step_index": None}

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
                if on_log:
                    if failed_step_index is not None:
                        on_log(f"Step {failed_step_index + 1} failed: {original_error}")
                    else:
                        on_log(f"Execution failed: {original_error}")
                return {
                    "success": False,
                    "output": output,
                    "error": original_error,
                    "failed_step_index": failed_step_index,
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
