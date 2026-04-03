from __future__ import annotations

import asyncio
from typing import Dict, Optional

from playwright.async_api import Page


class BrowserPreviewRegistry:
    """Session-scoped browser page registry for local chat preview."""

    def __init__(self) -> None:
        self._pages: Dict[str, Page] = {}
        self._lock = asyncio.Lock()

    async def register(self, session_id: str, page: Page) -> None:
        async with self._lock:
            self._pages[session_id] = page

    async def unregister(self, session_id: str, page: Page | None = None) -> None:
        async with self._lock:
            current = self._pages.get(session_id)
            if current is None:
                return
            if page is not None and current is not page:
                return
            self._pages.pop(session_id, None)

    async def get(self, session_id: str) -> Optional[Page]:
        async with self._lock:
            return self._pages.get(session_id)

    async def wait_for_page(self, session_id: str, timeout: float = 8.0) -> Optional[Page]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            page = await self.get(session_id)
            if page is not None:
                return page
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.1)


browser_preview_registry = BrowserPreviewRegistry()
