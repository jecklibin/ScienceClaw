from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from playwright.async_api import Page


@dataclass
class BrowserPreviewSession:
    root_page: Page
    active_page: Page
    context: object
    known_page_ids: set[int] = field(default_factory=set)


class BrowserPreviewRegistry:
    """Session-scoped browser page registry for local chat preview."""

    def __init__(self) -> None:
        self._sessions: Dict[str, BrowserPreviewSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, session_id: str, page: Page) -> None:
        async with self._lock:
            self._sessions[session_id] = BrowserPreviewSession(
                root_page=page,
                active_page=page,
                context=page.context,
                known_page_ids={id(page)},
            )

    async def unregister(self, session_id: str, page: Page | None = None) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if page is not None and page is not session.root_page and page is not session.active_page:
                live_pages = self._live_pages(session)
                if page not in live_pages:
                    return
            self._sessions.pop(session_id, None)

    async def get(self, session_id: str) -> Optional[Page]:
        async with self._lock:
            return self.get_active_page(session_id)

    def get_active_page(self, session_id: str) -> Optional[Page]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        self._refresh_pages(session)
        return session.active_page

    async def wait_for_page(self, session_id: str, timeout: float = 8.0) -> Optional[Page]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            page = await self.get(session_id)
            if page is not None:
                return page
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.1)

    def list_tabs(self, session_id: str) -> List[dict]:
        session = self._sessions.get(session_id)
        if session is None:
            return []
        pages = self._refresh_pages(session)
        tabs = []
        for page in pages:
            tabs.append({
                "tab_id": self._tab_id(page),
                "title": "",
                "url": getattr(page, "url", "") or "",
                "opener_tab_id": None,
                "status": "open",
                "active": page is session.active_page,
            })
        return tabs

    async def activate_tab(self, session_id: str, tab_id: str) -> dict:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ValueError("Browser preview session not found")
            pages = self._refresh_pages(session)
            target = next((page for page in pages if self._tab_id(page) == tab_id), None)
            if target is None:
                raise ValueError("Browser preview tab not found")
            session.active_page = target

        bring_to_front = getattr(target, "bring_to_front", None)
        if callable(bring_to_front):
            await bring_to_front()

        return {
            "tab_id": tab_id,
            "url": getattr(target, "url", "") or "",
        }

    @staticmethod
    def _tab_id(page: Page) -> str:
        return f"page-{id(page)}"

    @staticmethod
    def _is_closed(page: Page) -> bool:
        is_closed = getattr(page, "is_closed", None)
        if callable(is_closed):
            try:
                return bool(is_closed())
            except Exception:
                return False
        return False

    def _live_pages(self, session: BrowserPreviewSession) -> List[Page]:
        pages = getattr(session.context, "pages", None)
        if pages is None:
            pages = [session.root_page]
        return [page for page in list(pages) if page is not None and not self._is_closed(page)]

    def _refresh_pages(self, session: BrowserPreviewSession) -> List[Page]:
        pages = self._live_pages(session)
        if not pages:
            return []

        live_ids = {id(page) for page in pages}
        new_pages = [page for page in pages if id(page) not in session.known_page_ids]
        session.known_page_ids = live_ids

        if new_pages:
            session.active_page = new_pages[-1]
        elif session.active_page not in pages:
            session.active_page = pages[-1]

        return pages


browser_preview_registry = BrowserPreviewRegistry()
