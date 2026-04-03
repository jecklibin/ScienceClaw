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
    ) -> Dict[str, Any]:
        """Execute script in a new BrowserContext via CDP.

        Args:
            browser: CDP-connected browser instance
            script: Python source containing async def execute_skill(page, **kwargs)
            on_log: Optional callback for progress messages
            timeout: Max execution time in seconds
        """
        context = None
        try:
            if on_log:
                on_log("Creating browser context...")

            context = await browser.new_context(no_viewport=True)
            page = await context.new_page()
            page.set_default_timeout(RPA_PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(RPA_PAGE_TIMEOUT_MS)

            # Register page for screencast in local mode
            if session_id and page_registry:
                page_registry[session_id] = page

            if on_log:
                on_log("Executing script...")

            # Compile and extract execute_skill function
            namespace: Dict[str, Any] = {}
            exec(compile(script, "<rpa_script>", "exec"), namespace)

            if "execute_skill" not in namespace:
                return {"success": False, "output": "", "error": "No execute_skill() function in script"}

            # Run with timeout
            await asyncio.wait_for(
                namespace["execute_skill"](page),
                timeout=timeout,
            )

            # Brief pause so user can see result in VNC
            await page.wait_for_timeout(3000)

            output = "SKILL_SUCCESS"
            if on_log:
                on_log("Execution completed successfully")

            return {"success": True, "output": output}

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
            # Unregister page
            if session_id and page_registry and session_id in page_registry:
                page_registry.pop(session_id, None)

            if context:
                try:
                    await context.close()
                except Exception:
                    pass
