import asyncio
import logging
import threading
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, Playwright

from backend.config import settings

logger = logging.getLogger(__name__)


class CDPConnector:
    """Singleton CDP connection manager.

    Connects to the sandbox's existing browser via CDP protocol.
    Runs Playwright in a dedicated thread with its own ProactorEventLoop
    to avoid Windows SelectorEventLoop subprocess limitations.
    """

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._sandbox_base_url = settings.sandbox_mcp_url.replace("/mcp", "")
        self._lock = asyncio.Lock()
        # Dedicated event loop + thread for Playwright (Windows compat)
        self._pw_loop: Optional[asyncio.AbstractEventLoop] = None
        self._pw_thread: Optional[threading.Thread] = None

    def _ensure_pw_loop(self):
        """Start a background thread with ProactorEventLoop for Playwright."""
        if self._pw_thread and self._pw_thread.is_alive():
            return
        self._pw_loop = asyncio.new_event_loop()
        # On Windows, force ProactorEventLoop for subprocess support
        import sys
        if sys.platform == "win32":
            self._pw_loop = asyncio.ProactorEventLoop()
        self._pw_thread = threading.Thread(
            target=self._pw_loop.run_forever, daemon=True, name="playwright-loop"
        )
        self._pw_thread.start()

    async def _run_in_pw_loop(self, coro):
        """Schedule a coroutine on the Playwright event loop and await result."""
        self._ensure_pw_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._pw_loop)
        # Await in current loop without blocking
        return await asyncio.wrap_future(future)

    async def get_browser(self) -> Browser:
        """Get or create a CDP browser connection."""
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return self._browser

            cdp_url = await self._fetch_cdp_url()
            logger.info(f"Connecting to browser via CDP: {cdp_url}")

            # Start Playwright and connect in the dedicated thread/loop
            self._playwright, self._browser = await self._run_in_pw_loop(
                self._connect(cdp_url)
            )
            logger.info("CDP browser connection established")
            return self._browser

    @staticmethod
    async def _connect(cdp_url: str):
        """Start Playwright and connect via CDP (runs in pw_loop)."""
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        return pw, browser

    async def _fetch_cdp_url(self) -> str:
        """Fetch CDP WebSocket URL from sandbox API.

        The sandbox returns a cdp_url with its own internal host:port.
        We replace the host:port with the one from SANDBOX_MCP_URL so
        the backend can reach it (works for both local dev and Docker).
        """
        url = f"{self._sandbox_base_url}/v1/browser/info"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            cdp_url = data.get("data", {}).get("cdp_url", "")
            if not cdp_url:
                raise RuntimeError(f"No cdp_url in response from {url}: {data}")
            # Rewrite host:port to match our sandbox base URL
            from urllib.parse import urlparse
            sandbox_parsed = urlparse(self._sandbox_base_url)
            cdp_parsed = urlparse(cdp_url)
            cdp_url = cdp_parsed._replace(netloc=sandbox_parsed.netloc).geturl()
            return cdp_url

    async def close(self):
        """Clean up connections."""
        if self._browser:
            try:
                self._browser = None
            except Exception:
                pass
        if self._playwright:
            try:
                if self._pw_loop and self._pw_loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self._playwright.stop(), self._pw_loop
                    )
                    future.result(timeout=5)
                self._playwright = None
            except Exception:
                pass
        if self._pw_loop:
            self._pw_loop.call_soon_threadsafe(self._pw_loop.stop)
            self._pw_loop = None


# Global singleton
cdp_connector = CDPConnector()
