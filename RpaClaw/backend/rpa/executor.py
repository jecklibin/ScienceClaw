import json
import logging
import asyncio
from typing import Dict, Any, Callable, Optional

from playwright.async_api import Browser

logger = logging.getLogger(__name__)

RPA_PAGE_TIMEOUT_MS = 60000


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
        namespace: Dict[str, Any] = {
            "__rpa_log__": on_log or (lambda message: logger.info("[RPA Script] %s", message)),
        }
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
                context = await browser.new_context(no_viewport=True, accept_downloads=True)
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

                if _result:
                    output = "SKILL_DATA:" + json.dumps(_result, ensure_ascii=False, default=str) + "\nSKILL_SUCCESS"
                else:
                    output = "SKILL_SUCCESS"
                if on_log:
                    on_log("Execution completed successfully")
                return {"success": True, "output": output, "data": _result or {}}

            except asyncio.TimeoutError:
                output = f"SKILL_ERROR: Script did not complete within {timeout}s"
                if on_log:
                    on_log(output)
                return {"success": False, "output": output, "error": f"Timeout after {timeout}s"}

            except Exception as e:
                output = f"SKILL_ERROR: {e}"
                if on_log:
                    on_log(f"Execution failed: {e}")
                return {"success": False, "output": output, "error": str(e)}

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
