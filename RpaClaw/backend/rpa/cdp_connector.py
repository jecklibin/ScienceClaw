import asyncio
import logging
import threading
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, Playwright

from backend.config import settings
from backend.rpa.playwright_security import get_chromium_launch_kwargs
from backend.runtime.adapter_client import RuntimeAdapterClient
from backend.runtime.session_runtime_manager import get_session_runtime_manager

logger = logging.getLogger(__name__)
READY_RUNTIME_STATUSES = {"ready"}


class CDPConnector:
    """Singleton CDP connection manager.

    Connects to the sandbox's existing browser via CDP protocol.
    Runs Playwright in a dedicated thread with its own ProactorEventLoop
    to avoid Windows SelectorEventLoop subprocess limitations.
    """

    def __init__(self):
        self._playwrights: dict[str, Playwright] = {}
        self._browsers: dict[str, Browser] = {}
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

    async def get_browser(self, session_id: Optional[str] = None, user_id: Optional[str] = None) -> Browser:
        """Get or create a CDP browser connection."""
        session_key = session_id or "__shared__"
        async with self._lock:
            existing = self._browsers.get(session_key)
            if existing and existing.is_connected():
                return existing

            cdp_url = await self._fetch_cdp_url(session_id=session_id, user_id=user_id)
            logger.info(f"Connecting to browser via CDP: {cdp_url}")

            if self._should_connect_in_current_loop():
                playwright, browser = await self._connect(cdp_url)
            else:
                # Start Playwright and connect in the dedicated thread/loop
                playwright, browser = await self._run_in_pw_loop(
                    self._connect(cdp_url)
                )
            self._playwrights[session_key] = playwright
            self._browsers[session_key] = browser
            logger.info("CDP browser connection established")
            return browser

    @staticmethod
    def _should_connect_in_current_loop() -> bool:
        return str(getattr(settings, "runtime_mode", "") or "").strip() == "aio_native"

    @staticmethod
    async def _connect(cdp_url: str):
        """Start Playwright and connect via CDP (runs in pw_loop)."""
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        return pw, browser

    async def _fetch_cdp_url(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Fetch CDP WebSocket URL from sandbox API.

        The sandbox returns a cdp_url with its own internal host:port.
        We replace the host:port with the one from SANDBOX_MCP_URL so
        the backend can reach it (works for both local dev and Docker).
        """
        sandbox_base = self._sandbox_base_url
        if session_id:
            runtime = await get_session_runtime_manager().ensure_runtime(
                session_id,
                user_id or "system",
            )
            if runtime.status not in READY_RUNTIME_STATUSES:
                raise RuntimeError(
                    "Runtime is not ready for CDP connection: "
                    f"session_id={session_id}, status={runtime.status}"
                )
            data = await RuntimeAdapterClient(runtime).browser_info()
            sandbox_base = runtime.route_base_url or runtime.rest_base_url
        else:
            url = f"{self._sandbox_base_url}/v1/browser/info"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()

        cdp_url = data.get("data", {}).get("cdp_url", "")
        if not cdp_url:
            raise RuntimeError(f"No cdp_url in browser info response: {data}")
        # Rewrite host:port to match our reachable runtime/adapter route.
        from urllib.parse import urlparse
        sandbox_parsed = urlparse(sandbox_base)
        cdp_parsed = urlparse(cdp_url)
        cdp_url = cdp_parsed._replace(netloc=sandbox_parsed.netloc).geturl()
        return cdp_url

    async def close(self):
        """Clean up connections."""
        self._browsers.clear()
        for playwright in list(self._playwrights.values()):
            try:
                if self._pw_loop and self._pw_loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        playwright.stop(), self._pw_loop
                    )
                    future.result(timeout=5)
            except Exception:
                pass
        self._playwrights.clear()
        if self._pw_loop:
            self._pw_loop.call_soon_threadsafe(self._pw_loop.stop)
            self._pw_loop = None


class LocalCDPConnector:
    """Local Playwright browser manager.

    Launches a local Chromium browser in headful mode instead of
    connecting to a remote sandbox via CDP.
    Uses the same dedicated thread/ProactorEventLoop pattern as
    CDPConnector for Windows compatibility.
    """

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        # Dedicated event loop + thread for Playwright (Windows compat)
        self._pw_loop: Optional[asyncio.AbstractEventLoop] = None
        self._pw_thread: Optional[threading.Thread] = None

    def _ensure_pw_loop(self):
        """Start a background thread with ProactorEventLoop for Playwright."""
        if self._pw_thread and self._pw_thread.is_alive():
            return
        self._pw_loop = asyncio.new_event_loop()
        import sys
        if sys.platform == "win32":
            self._pw_loop = asyncio.ProactorEventLoop()
        self._pw_thread = threading.Thread(
            target=self._pw_loop.run_forever, daemon=True, name="playwright-local-loop"
        )
        self._pw_thread.start()

    async def _run_in_pw_loop(self, coro):
        """Schedule a coroutine on the Playwright event loop and await result."""
        self._ensure_pw_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._pw_loop)
        return await asyncio.wrap_future(future)

    async def run_in_pw_loop(self, coro):
        """Public: schedule a coroutine on the Playwright event loop."""
        return await self._run_in_pw_loop(coro)

    async def get_browser(self, session_id: Optional[str] = None, user_id: Optional[str] = None) -> Browser:
        """Get or create a local headful browser."""
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return self._browser

            logger.info("Launching local Playwright Chromium (headful)")
            self._playwright, self._browser = await self._run_in_pw_loop(
                self._launch()
            )
            logger.info("Local browser launched")
            return self._browser

    @staticmethod
    async def _launch():
        """Start Playwright and launch a local headful Chromium browser."""
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(**get_chromium_launch_kwargs(headless=False))
        return pw, browser

    async def close(self):
        """Clean up browser and playwright."""
        if self._browser:
            try:
                if self._pw_loop and self._pw_loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self._browser.close(), self._pw_loop
                    )
                    future.result(timeout=5)
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


# Global singletons
cdp_connector = CDPConnector()
local_cdp_connector = LocalCDPConnector()


def get_cdp_connector():
    """Return the appropriate CDP connector based on storage_backend."""
    runtime_mode = str(getattr(settings, "runtime_mode", "shared") or "shared").strip()
    if runtime_mode in {"aio", "aio_fixed", "aio_native", "docker", "session_pod"}:
        return cdp_connector
    if settings.storage_backend == "local":
        return local_cdp_connector
    return cdp_connector
