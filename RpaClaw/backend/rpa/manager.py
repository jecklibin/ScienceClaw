import json
import logging
import uuid
import asyncio
import re
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from playwright.async_api import Page, BrowserContext

from .cdp_connector import get_cdp_connector
from .frame_selectors import build_frame_path
from .playwright_security import get_context_kwargs

logger = logging.getLogger(__name__)

RPA_PAGE_TIMEOUT_MS = 60000


class RPAStep(BaseModel):
    id: str
    action: str
    target: Optional[str] = None
    frame_path: List[str] = Field(default_factory=list)
    locator_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    signals: Dict[str, Any] = Field(default_factory=dict)
    element_snapshot: Dict[str, Any] = Field(default_factory=dict)
    value: Optional[str] = None
    screenshot_url: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    description: Optional[str] = None
    tag: Optional[str] = None
    label: Optional[str] = None
    url: Optional[str] = None
    source: str = "record"  # "record" or "ai"
    prompt: Optional[str] = None  # original user instruction for AI steps
    sensitive: bool = False
    tab_id: Optional[str] = None
    source_tab_id: Optional[str] = None
    target_tab_id: Optional[str] = None
    result_key: Optional[str] = None
    collection_hint: Dict[str, Any] = Field(default_factory=dict)
    item_hint: Dict[str, Any] = Field(default_factory=dict)
    ordinal: Optional[str] = None
    assistant_diagnostics: Dict[str, Any] = Field(default_factory=dict)
    sequence: Optional[int] = None
    event_timestamp_ms: Optional[int] = None


class RPATab(BaseModel):
    tab_id: str
    title: str = ""
    url: str = ""
    opener_tab_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    last_seen_at: datetime = Field(default_factory=datetime.now)
    status: str = "open"


class RPASession(BaseModel):
    id: str
    user_id: str
    start_time: datetime = Field(default_factory=datetime.now)
    status: str = "recording"  # recording, stopped, testing, saved
    steps: List[RPAStep] = []
    sandbox_session_id: str
    paused: bool = False  # pause event recording during AI execution
    active_tab_id: Optional[str] = None


# ── CAPTURE_JS: injected into pages to capture user events ──────────
# Calls window.__rpa_emit(JSON.stringify(evt)) which is bridged to
# Python via page.expose_function().
# CAPTURE_JS is loaded from vendor-managed assets so the browser recorder can
# reuse Playwright upstream selector logic without keeping a large JavaScript
# blob inline in Python.
RPA_VENDOR_DIR = Path(__file__).with_name("vendor")
PLAYWRIGHT_RECORDER_RUNTIME_PATH = RPA_VENDOR_DIR / "playwright_recorder_runtime.js"
PLAYWRIGHT_RECORDER_ACTIONS_PATH = RPA_VENDOR_DIR / "playwright_recorder_actions.js"
CAPTURE_SCRIPT_PATH = RPA_VENDOR_DIR / "playwright_recorder_capture.js"
PLAYWRIGHT_RECORDER_RUNTIME_JS = PLAYWRIGHT_RECORDER_RUNTIME_PATH.read_text(encoding="utf-8")
PLAYWRIGHT_RECORDER_ACTIONS_JS = PLAYWRIGHT_RECORDER_ACTIONS_PATH.read_text(encoding="utf-8")
CAPTURE_JS = CAPTURE_SCRIPT_PATH.read_text(encoding="utf-8")

class RPASessionManager:
    def __init__(self):
        self.sessions: Dict[str, RPASession] = {}
        self.ws_connections: Dict[str, List] = {}
        self._contexts: Dict[str, BrowserContext] = {}
        self._pages: Dict[str, Page] = {}
        self._tabs: Dict[str, Dict[str, Page]] = {}
        self._tab_meta: Dict[str, Dict[str, RPATab]] = {}
        self._page_tab_ids: Dict[str, Dict[int, str]] = {}
        self._bridged_context_ids: Dict[str, set[int]] = {}

    def attach_context(self, session_id: str, context: BrowserContext):
        self._contexts[session_id] = context
        self._pages.pop(session_id, None)
        self._tabs[session_id] = {}
        self._tab_meta[session_id] = {}
        self._page_tab_ids[session_id] = {}
        self._bridged_context_ids[session_id] = set()

        session = self.sessions.get(session_id)
        if session:
            session.active_tab_id = None

    def detach_context(self, session_id: str, context: Optional[BrowserContext] = None):
        current_context = self._contexts.get(session_id)
        if context is not None and current_context is not context:
            return

        self._contexts.pop(session_id, None)
        self._pages.pop(session_id, None)
        self._tabs.pop(session_id, None)
        self._tab_meta.pop(session_id, None)
        self._page_tab_ids.pop(session_id, None)
        self._bridged_context_ids.pop(session_id, None)

        session = self.sessions.get(session_id)
        if session:
            session.active_tab_id = None

    async def create_session(self, user_id: str, sandbox_session_id: str) -> RPASession:
        session_id = str(uuid.uuid4())
        session = RPASession(
            id=session_id,
            user_id=user_id,
            sandbox_session_id=sandbox_session_id,
        )
        self.sessions[session_id] = session

        browser = await get_cdp_connector().get_browser(
            session_id=sandbox_session_id,
            user_id=user_id,
        )
        context = await browser.new_context(**get_context_kwargs())
        page = await context.new_page()
        page.set_default_timeout(RPA_PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(RPA_PAGE_TIMEOUT_MS)

        self.attach_context(session_id, context)
        await self.register_page(session_id, page, make_active=True)

        def on_context_page(new_page):
            asyncio.create_task(self.register_context_page(session_id, new_page, make_active=True))

        context.on("page", on_context_page)
        await page.goto("about:blank")

        logger.info(f"[RPA] Session {session_id} started via CDP")
        return session

    async def register_page(
        self,
        session_id: str,
        page: Page,
        opener_tab_id: Optional[str] = None,
        make_active: bool = False,
    ) -> str:
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")

        tab_id = str(uuid.uuid4())
        self._tabs.setdefault(session_id, {})[tab_id] = page
        self._page_tab_ids.setdefault(session_id, {})[id(page)] = tab_id
        self._tab_meta.setdefault(session_id, {})[tab_id] = RPATab(
            tab_id=tab_id,
            title=await self._safe_page_title(page),
            url=getattr(page, "url", "") or "",
            opener_tab_id=opener_tab_id,
        )

        tab_bootstrap_script = self._build_tab_bootstrap_script(tab_id)
        await page.add_init_script(script=tab_bootstrap_script)
        bridged_context_ids = self._bridged_context_ids.setdefault(session_id, set())
        bootstrap_current_page = id(page.context) in bridged_context_ids
        await self._ensure_context_recorder(session_id, page.context)
        await self._bind_page(session_id, tab_id, page)
        if bootstrap_current_page:
            await page.evaluate(tab_bootstrap_script)
            await self._bootstrap_page_recorder(page)

        if make_active or not self.sessions[session_id].active_tab_id:
            await self.activate_tab(session_id, tab_id, source="auto")

        return tab_id

    async def register_context_page(self, session_id: str, page: Page, make_active: bool = True) -> str:
        session = self.sessions.get(session_id)
        opener_tab_id = session.active_tab_id if session else None
        tab_id = await self.register_page(
            session_id,
            page,
            opener_tab_id=opener_tab_id,
            make_active=make_active,
        )
        if opener_tab_id:
            await self._upgrade_recent_click_to_open_tab(session_id, opener_tab_id, tab_id)
        return tab_id

    @classmethod
    def _step_popup_target_tab_id(cls, step: RPAStep) -> Optional[str]:
        signals = step.signals if isinstance(step.signals, dict) else {}
        popup_signal = signals.get("popup")
        if isinstance(popup_signal, dict):
            target_tab_id = popup_signal.get("target_tab_id")
            if isinstance(target_tab_id, str) and target_tab_id:
                return target_tab_id
        if step.action == "open_tab_click":
            return step.target_tab_id
        return None

    @classmethod
    def _step_targets_popup_tab(cls, step: RPAStep, tab_id: Optional[str]) -> bool:
        if not tab_id:
            return False
        return cls._step_popup_target_tab_id(step) == tab_id

    @staticmethod
    def _merge_step_signal(step: RPAStep, signal_name: str, payload: Dict[str, Any]) -> None:
        signals = dict(step.signals) if isinstance(step.signals, dict) else {}
        existing = signals.get(signal_name)
        merged = dict(existing) if isinstance(existing, dict) else {}
        for key, value in payload.items():
            if value is not None:
                merged[key] = value
        signals[signal_name] = merged
        step.signals = signals

    @staticmethod
    def _append_step_description(step: RPAStep, suffix: str) -> None:
        description = step.description or ""
        if suffix and suffix not in description:
            step.description = f"{description}{suffix}" if description else suffix.strip()

    def _find_recent_action_step(
        self,
        session_id: str,
        *,
        tab_id: Optional[str] = None,
        popup_target_tab_id: Optional[str] = None,
    ) -> Optional[RPAStep]:
        session = self.sessions.get(session_id)
        if not session:
            return None

        now = datetime.now()
        for step in reversed(session.steps):
            age_s = (now - step.timestamp).total_seconds()
            if age_s > 5:
                break
            if step.action not in {"click", "press", "open_tab_click", "download_click"}:
                continue
            if tab_id and step.tab_id == tab_id:
                return step
            if popup_target_tab_id and self._step_targets_popup_tab(step, popup_target_tab_id):
                return step
        return None

    async def _upgrade_recent_click_to_open_tab(self, session_id: str, source_tab_id: str, target_tab_id: str):
        step = self._find_recent_action_step(session_id, tab_id=source_tab_id)
        if not step:
            return

        step.source_tab_id = source_tab_id
        step.target_tab_id = target_tab_id
        self._merge_step_signal(
            step,
            "popup",
            {
                "source_tab_id": source_tab_id,
                "target_tab_id": target_tab_id,
            },
        )
        self._append_step_description(step, " 并在新标签页打开")
        await self._broadcast_step(session_id, step)
        logger.debug(f"[RPA] Attached popup signal: source={source_tab_id} target={target_tab_id}")

    @staticmethod
    def _describe_switch_tab(tab_id: str, title: str = "") -> str:
        return f'切换到标签页 {title or tab_id}'

    @staticmethod
    def _describe_close_tab(title: str = "", has_fallback: bool = False) -> str:
        label = title or "当前标签页"
        suffix = " 并切换到其他标签页" if has_fallback else ""
        return f"关闭标签页 {label}{suffix}"

    @staticmethod
    def _normalize_url(url: str) -> str:
        normalized = (url or "").strip()
        if not normalized:
            raise ValueError("URL is required")
        parsed = urlparse(normalized)
        if not parsed.scheme:
            normalized = f"https://{normalized}"
            parsed = urlparse(normalized)
        if parsed.scheme in {"http", "https"} and not parsed.netloc:
            raise ValueError("Invalid URL")
        return normalized

    @staticmethod
    def _build_tab_bootstrap_script(tab_id: str) -> str:
        return f"window.__rpa_tab_id = {json.dumps(tab_id)};"

    async def navigate_active_tab(self, session_id: str, url: str) -> Dict[str, str]:
        session = self.sessions.get(session_id)
        if not session or not session.active_tab_id:
            raise ValueError(f"No active tab for session {session_id}")

        page = self.get_active_page(session_id)
        if page is None:
            raise ValueError(f"No active page for session {session_id}")

        normalized_url = self._normalize_url(url)
        await page.goto(normalized_url)
        await page.wait_for_load_state("domcontentloaded")

        tab = self._tab_meta.get(session_id, {}).get(session.active_tab_id)
        if tab:
            tab.url = getattr(page, "url", normalized_url) or normalized_url
            tab.last_seen_at = datetime.now()
            title = await self._safe_page_title(page)
            if title:
                tab.title = title

        return {
            "tab_id": session.active_tab_id,
            "url": getattr(page, "url", normalized_url) or normalized_url,
        }

    async def activate_tab(self, session_id: str, tab_id: str, source: str = "auto"):
        page = self._tabs.get(session_id, {}).get(tab_id)
        if page is None:
            raise ValueError(f"Tab {tab_id} not found for session {session_id}")

        session = self.sessions[session_id]
        previous_tab_id = session.active_tab_id
        session.active_tab_id = tab_id
        self._pages[session_id] = page

        tab = self._tab_meta.get(session_id, {}).get(tab_id)
        if tab:
            tab.last_seen_at = datetime.now()
            tab.url = getattr(page, "url", tab.url) or tab.url
            title = await self._safe_page_title(page)
            if title:
                tab.title = title

        try:
            await page.bring_to_front()
        except Exception:
            pass

        if (
            previous_tab_id
            and previous_tab_id != tab_id
            and source in {"user", "fallback"}
            and session.status == "recording"
            and not session.paused
        ):
            await self.add_step(
                session_id,
                {
                    "action": "switch_tab",
                    "target": "",
                    "value": "",
                    "label": "",
                    "tag": "",
                    "url": tab.url if tab else getattr(page, "url", ""),
                    "description": self._describe_switch_tab(tab_id, tab.title if tab else ""),
                    "sensitive": False,
                    "tab_id": previous_tab_id,
                    "source_tab_id": previous_tab_id,
                    "target_tab_id": tab_id,
                },
            )

        return {"tab_id": tab_id, "source": source}

    async def close_tab(self, session_id: str, tab_id: str, close_page: bool = True):
        page = self._tabs.get(session_id, {}).get(tab_id)
        tab = self._tab_meta.get(session_id, {}).get(tab_id)
        if page is None or tab is None:
            raise ValueError(f"Tab {tab_id} not found for session {session_id}")

        session = self.sessions[session_id]
        fallback_tab_id = None
        if session.active_tab_id == tab_id:
            if tab.opener_tab_id and tab.opener_tab_id in self._tabs.get(session_id, {}):
                fallback_tab_id = tab.opener_tab_id
            elif self._tabs.get(session_id):
                remaining_tab_ids = [existing_tab_id for existing_tab_id in self._tabs[session_id] if existing_tab_id != tab_id]
                if remaining_tab_ids:
                    fallback_tab_id = remaining_tab_ids[-1]

        tab.status = "closed"
        tab.last_seen_at = datetime.now()

        if session.status == "recording" and not session.paused:
            await self.add_step(
                session_id,
                {
                    "action": "close_tab",
                    "target": "",
                    "value": "",
                    "label": "",
                    "tag": "",
                    "url": tab.url,
                    "description": self._describe_close_tab(tab.title, fallback_tab_id is not None),
                    "sensitive": False,
                    "tab_id": tab_id,
                    "source_tab_id": tab_id,
                    "target_tab_id": fallback_tab_id,
                },
            )

        if close_page:
            try:
                await page.close()
            except Exception:
                pass

        self._tabs.get(session_id, {}).pop(tab_id, None)
        self._page_tab_ids.get(session_id, {}).pop(id(page), None)

        if session.active_tab_id == tab_id:
            if fallback_tab_id:
                await self.activate_tab(session_id, fallback_tab_id, source="fallback")
            else:
                session.active_tab_id = None
                self._pages.pop(session_id, None)

    def list_tabs(self, session_id: str) -> List[Dict[str, Any]]:
        active_tab_id = None
        if session_id in self.sessions:
            active_tab_id = self.sessions[session_id].active_tab_id

        return [
            {
                "tab_id": tab.tab_id,
                "title": tab.title,
                "url": tab.url,
                "opener_tab_id": tab.opener_tab_id,
                "status": tab.status,
                "active": tab.tab_id == active_tab_id,
            }
            for tab in self._tab_meta.get(session_id, {}).values()
        ]

    def get_active_page(self, session_id: str) -> Optional[Page]:
        active_tab_id = self.sessions.get(session_id).active_tab_id if session_id in self.sessions else None
        if not active_tab_id:
            return None
        return self._tabs.get(session_id, {}).get(active_tab_id)

    async def _ensure_context_recorder(self, session_id: str, context: BrowserContext):
        bridged_context_ids = self._bridged_context_ids.setdefault(session_id, set())
        context_key = id(context)
        if context_key in bridged_context_ids:
            return

        def _binding_source_get(source: Any, key: str) -> Any:
            if isinstance(source, dict):
                return source.get(key)
            return getattr(source, key, None)

        async def rpa_emit(source, event_json: str):
            try:
                evt = json.loads(event_json)
                source_page = _binding_source_get(source, "page")
                source_frame = _binding_source_get(source, "frame")
                resolved_tab_id = self._page_tab_ids.get(session_id, {}).get(id(source_page))
                if not resolved_tab_id:
                    session = self.sessions.get(session_id)
                    resolved_tab_id = session.active_tab_id if session else None
                if resolved_tab_id:
                    evt.setdefault("tab_id", resolved_tab_id)
                if source_frame:
                    reported_frame_path = evt.get("frame_path", []) or []
                    if reported_frame_path:
                        signals = evt.get("signals")
                        normalized_signals = dict(signals) if isinstance(signals, dict) else {}
                        normalized_signals["reported_frame_path"] = list(reported_frame_path)
                        evt["signals"] = normalized_signals
                    evt["frame_path"] = await self._build_frame_path(source_frame)
                await self._handle_event(session_id, evt)
            except Exception as e:
                logger.error(f"[RPA] binding emit error: {e}")

        await context.expose_binding("__rpa_emit", rpa_emit, handle=False)
        await context.add_init_script(path=str(PLAYWRIGHT_RECORDER_RUNTIME_PATH))
        await context.add_init_script(path=str(PLAYWRIGHT_RECORDER_ACTIONS_PATH))
        await context.add_init_script(script=CAPTURE_JS)
        bridged_context_ids.add(context_key)

    async def _build_frame_path(self, frame) -> List[str]:
        return await build_frame_path(frame)

    async def build_frame_path(self, frame) -> List[str]:
        return await self._build_frame_path(frame)

    async def _bootstrap_page_recorder(self, page: Page):
        try:
            await page.evaluate(PLAYWRIGHT_RECORDER_RUNTIME_JS)
            await page.evaluate(PLAYWRIGHT_RECORDER_ACTIONS_JS)
            await page.evaluate(CAPTURE_JS)
        except Exception as exc:
            logger.debug("[RPA] Failed to bootstrap recorder runtime for current page: %s", exc)

    async def _bind_page(self, session_id: str, tab_id: str, page: Page):
        last_url = {"value": ""}

        def on_navigated(frame):
            if frame != page.main_frame:
                return
            new_url = frame.url
            if new_url and new_url != last_url["value"] and new_url != "about:blank":
                last_url["value"] = new_url
                tab = self._tab_meta.get(session_id, {}).get(tab_id)
                if tab:
                    tab.url = new_url
                    tab.last_seen_at = datetime.now()
                evt = {
                    "action": "navigate",
                    "url": new_url,
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "tab_id": tab_id,
                }
                asyncio.create_task(self._handle_event(session_id, evt))

        page.on("framenavigated", on_navigated)

        async def on_load(loaded_page):
            tab = self._tab_meta.get(session_id, {}).get(tab_id)
            if tab:
                tab.url = getattr(page, "url", tab.url) or tab.url
                tab.title = await self._safe_page_title(page)
                tab.last_seen_at = datetime.now()

        page.on("load", on_load)

        async def on_download(download):
            suggested = download.suggested_filename
            # Wait briefly for the click step to be recorded before upgrading it
            await asyncio.sleep(0.3)
            tab_meta = self._tab_meta.get(session_id, {}).get(tab_id)
            opener_tab_id = tab_meta.opener_tab_id if tab_meta else None
            step = self._find_recent_action_step(session_id, tab_id=tab_id)
            if not step and opener_tab_id:
                step = self._find_recent_action_step(session_id, popup_target_tab_id=tab_id)
            if step:
                step.value = suggested
                self._merge_step_signal(
                    step,
                    "download",
                    {
                        "filename": suggested,
                        "tab_id": tab_id,
                        "opener_tab_id": opener_tab_id,
                    },
                )
                self._append_step_description(step, f" 并下载文件 {suggested}")
                await self._broadcast_step(session_id, step)
                return
            # Fallback: no preceding click found, record standalone
            evt = {
                "action": "download",
                "value": suggested,
                "url": getattr(page, "url", ""),
                "timestamp": int(datetime.now().timestamp() * 1000),
                "tab_id": tab_id,
            }
            await self._handle_event(session_id, evt)

        page.on("download", on_download)

        def on_close():
            if session_id in self.sessions and tab_id in self._tab_meta.get(session_id, {}):
                asyncio.create_task(self.close_tab(session_id, tab_id, close_page=False))

        page.on("close", on_close)

    async def _safe_page_title(self, page: Page) -> str:
        try:
            return await page.title()
        except Exception:
            return ""

    async def stop_session(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].status = "stopped"

        context = self._contexts.pop(session_id, None)
        self.detach_context(session_id)
        if context:
            try:
                await context.close()
            except Exception as e:
                logger.warning(f"[RPA] Error closing context: {e}")

        logger.info(f"[RPA] Session {session_id} stopped")

    async def get_session(self, session_id: str) -> Optional[RPASession]:
        return self.sessions.get(session_id)

    async def delete_step(self, session_id: str, step_index: int) -> bool:
        """Delete a step by index from the session."""
        session = self.sessions.get(session_id)
        if not session or step_index < 0 or step_index >= len(session.steps):
            return False
        session.steps.pop(step_index)
        return True

    @staticmethod
    def _unescape_playwright_literal(value: str) -> str:
        return value.replace('\\"', '"').replace("\\\\", "\\")

    @classmethod
    def _parse_playwright_locator_expression(cls, expression: str) -> Optional[Dict[str, Any]]:
        if not expression:
            return None
        remaining = expression.strip()
        if remaining.startswith("page."):
            remaining = remaining[5:]

        current: Optional[Dict[str, Any]] = None
        value_patterns = (
            ("testid", r'get_by_test_id\("((?:\\.|[^"\\])*)"\)'),
            ("label", r'get_by_label\("((?:\\.|[^"\\])*)"(?:,\s*exact=True)?\)'),
            ("placeholder", r'get_by_placeholder\("((?:\\.|[^"\\])*)"(?:,\s*exact=True)?\)'),
            ("alt", r'get_by_alt_text\("((?:\\.|[^"\\])*)"(?:,\s*exact=True)?\)'),
            ("title", r'get_by_title\("((?:\\.|[^"\\])*)"(?:,\s*exact=True)?\)'),
            ("text", r'get_by_text\("((?:\\.|[^"\\])*)"(?:,\s*exact=True)?\)'),
        )

        while remaining:
            matched_text = ""
            step: Optional[Dict[str, Any]] = None

            if current is None:
                locator_match = re.match(r'locator\("((?:\\.|[^"\\])*)"\)', remaining)
                if locator_match:
                    matched_text = locator_match.group(0)
                    step = {"method": "css", "value": cls._unescape_playwright_literal(locator_match.group(1))}
                else:
                    role_match = re.match(
                        r'get_by_role\("((?:\\.|[^"\\])*)"(?:,\s*name="((?:\\.|[^"\\])*)"(?:,\s*exact=True)?)?\)',
                        remaining,
                    )
                    if role_match:
                        matched_text = role_match.group(0)
                        step = {
                            "method": "role",
                            "role": cls._unescape_playwright_literal(role_match.group(1)),
                            "name": (
                                cls._unescape_playwright_literal(role_match.group(2))
                                if role_match.group(2) is not None
                                else ""
                            ),
                        }
                    else:
                        for method, pattern in value_patterns:
                            match = re.match(pattern, remaining)
                            if not match:
                                continue
                            matched_text = match.group(0)
                            step = {"method": method, "value": cls._unescape_playwright_literal(match.group(1))}
                            break
            else:
                nth_match = re.match(r'\.nth\((\d+)\)', remaining)
                if nth_match:
                    matched_text = nth_match.group(0)
                    current = {
                        "method": "nth",
                        "locator": current,
                        "index": int(nth_match.group(1)),
                    }
                else:
                    locator_match = re.match(r'\.locator\("((?:\\.|[^"\\])*)"\)', remaining)
                    if locator_match:
                        matched_text = locator_match.group(0)
                        step = {"method": "css", "value": cls._unescape_playwright_literal(locator_match.group(1))}
                    else:
                        role_match = re.match(
                            r'\.get_by_role\("((?:\\.|[^"\\])*)"(?:,\s*name="((?:\\.|[^"\\])*)"(?:,\s*exact=True)?)?\)',
                            remaining,
                        )
                        if role_match:
                            matched_text = role_match.group(0)
                            step = {
                                "method": "role",
                                "role": cls._unescape_playwright_literal(role_match.group(1)),
                                "name": (
                                    cls._unescape_playwright_literal(role_match.group(2))
                                    if role_match.group(2) is not None
                                    else ""
                                ),
                            }
                        else:
                            for method, pattern in value_patterns:
                                match = re.match(r"\." + pattern, remaining)
                                if not match:
                                    continue
                                matched_text = match.group(0)
                                step = {"method": method, "value": cls._unescape_playwright_literal(match.group(1))}
                                break

            if not matched_text:
                return None
            if step is not None:
                current = step if current is None else {"method": "nested", "parent": current, "child": step}
            remaining = remaining[len(matched_text):]

        return current

    @classmethod
    def _resolve_candidate_locator(cls, candidate: Dict[str, Any]) -> Dict[str, Any]:
        locator_payload = candidate.get("locator")
        locator: Optional[Dict[str, Any]] = None

        if isinstance(locator_payload, dict):
            locator = dict(locator_payload)
        elif isinstance(locator_payload, str) and locator_payload.strip():
            raw_payload = locator_payload.strip()
            try:
                parsed = json.loads(raw_payload)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                locator = parsed
            else:
                locator = {"method": "css", "value": raw_payload}

        if locator is None:
            playwright_locator = candidate.get("playwright_locator")
            if isinstance(playwright_locator, str):
                locator = cls._parse_playwright_locator_expression(playwright_locator)

        if locator is None:
            selector = candidate.get("selector")
            if isinstance(selector, str) and selector.strip():
                normalized_selector = selector.strip()
                if "internal:" not in normalized_selector:
                    locator = {"method": "css", "value": normalized_selector}

        if not isinstance(locator, dict):
            raise ValueError("Locator candidate is missing locator payload")

        nth_value = candidate.get("nth")
        if nth_value is not None and locator.get("method") != "nth":
            try:
                nth_index = int(nth_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("Locator candidate nth index is invalid") from exc
            if nth_index < 0:
                raise ValueError("Locator candidate nth index is invalid")
            locator = {"method": "nth", "locator": locator, "index": nth_index}
        elif locator.get("method") == "nth" and "locator" not in locator and "base" in locator:
            normalized_locator = dict(locator)
            normalized_locator["locator"] = normalized_locator.pop("base")
            locator = normalized_locator

        return locator

    @staticmethod
    def _candidate_score(candidate: Dict[str, Any]) -> float:
        score = candidate.get("score")
        if isinstance(score, (int, float)):
            return float(score)
        return float("inf")

    @classmethod
    def _candidate_is_nth(cls, candidate: Dict[str, Any], locator: Optional[Dict[str, Any]] = None) -> bool:
        if candidate.get("kind") == "nth":
            return True
        resolved_locator = locator
        if resolved_locator is None:
            try:
                resolved_locator = cls._resolve_candidate_locator(candidate)
            except ValueError:
                return False
        return isinstance(resolved_locator, dict) and resolved_locator.get("method") == "nth"

    @classmethod
    def _pick_best_strict_candidate(
        cls, locator_candidates: List[Dict[str, Any]]
    ) -> Optional[tuple[int, Dict[str, Any], Dict[str, Any]]]:
        best: Optional[tuple[int, Dict[str, Any], Dict[str, Any]]] = None
        best_score = float("inf")
        best_is_nth = False

        for index, candidate in enumerate(locator_candidates):
            if not isinstance(candidate, dict):
                continue
            strict_match_count = candidate.get("strict_match_count")
            if not isinstance(strict_match_count, int) or strict_match_count != 1:
                continue
            try:
                locator = cls._resolve_candidate_locator(candidate)
            except ValueError:
                continue

            score = cls._candidate_score(candidate)
            is_nth = cls._candidate_is_nth(candidate, locator=locator)
            if best is None:
                best = (index, candidate, locator)
                best_score = score
                best_is_nth = is_nth
                continue
            if score < best_score or (score == best_score and best_is_nth and not is_nth):
                best = (index, candidate, locator)
                best_score = score
                best_is_nth = is_nth

        return best

    @classmethod
    def _normalize_event_locator_payload(cls, evt: Dict[str, Any]) -> None:
        locator = evt.get("locator")
        locator_candidates = evt.get("locator_candidates")
        if not isinstance(locator_candidates, list) or not locator_candidates:
            return

        best_candidate_info = cls._pick_best_strict_candidate(locator_candidates)

        selected_index: Optional[int] = None
        for index, candidate in enumerate(locator_candidates):
            if isinstance(candidate, dict) and candidate.get("selected"):
                selected_index = index
                break

        if selected_index is None and isinstance(locator, dict):
            locator_json = json.dumps(locator, sort_keys=True)
            for index, candidate in enumerate(locator_candidates):
                if not isinstance(candidate, dict):
                    continue
                try:
                    candidate_locator = cls._resolve_candidate_locator(candidate)
                except ValueError:
                    continue
                if json.dumps(candidate_locator, sort_keys=True) == locator_json:
                    selected_index = index
                    break

        selected_candidate_info: Optional[tuple[int, Dict[str, Any], Dict[str, Any]]] = None
        if selected_index is not None and 0 <= selected_index < len(locator_candidates):
            selected_candidate = locator_candidates[selected_index]
            if isinstance(selected_candidate, dict):
                try:
                    selected_locator = cls._resolve_candidate_locator(selected_candidate)
                except ValueError:
                    selected_locator = None
                if isinstance(selected_locator, dict):
                    selected_candidate_info = (selected_index, selected_candidate, selected_locator)

        if not isinstance(locator, dict):
            candidate_info = best_candidate_info or selected_candidate_info
            if candidate_info is None:
                return
            cls._apply_candidate_selection(evt, locator_candidates, *candidate_info)
            return

        if best_candidate_info is None:
            return
        best_index, best_candidate, best_locator = best_candidate_info

        should_promote = False
        if selected_index is not None:
            selected_candidate = locator_candidates[selected_index]
            selected_strict_count = (
                selected_candidate.get("strict_match_count") if isinstance(selected_candidate, dict) else None
            )
            validation = evt.get("validation")
            status = validation.get("status") if isinstance(validation, dict) else None
            should_promote = selected_strict_count != 1 or status in {"fallback", "ambiguous", "warning", "broken"}
            if not should_promote and isinstance(selected_candidate, dict):
                selected_score = cls._candidate_score(selected_candidate)
                best_score = cls._candidate_score(best_candidate)
                selected_is_nth = cls._candidate_is_nth(selected_candidate)
                best_is_nth = cls._candidate_is_nth(best_candidate, locator=best_locator)
                should_promote = best_score < selected_score or (
                    best_score == selected_score and selected_is_nth and not best_is_nth
                )
        else:
            validation = evt.get("validation")
            status = validation.get("status") if isinstance(validation, dict) else None
            should_promote = status in {"fallback", "ambiguous", "warning", "broken"}

        if not should_promote:
            return

        cls._apply_candidate_selection(evt, locator_candidates, best_index, best_candidate, best_locator)

    @classmethod
    def _apply_candidate_selection(
        cls,
        evt: Dict[str, Any],
        locator_candidates: List[Dict[str, Any]],
        selected_index: int,
        selected_candidate: Dict[str, Any],
        selected_locator: Dict[str, Any],
    ) -> None:
        normalized_candidates: List[Dict[str, Any]] = []
        for index, candidate in enumerate(locator_candidates):
            if not isinstance(candidate, dict):
                continue
            normalized = dict(candidate)
            normalized["selected"] = index == selected_index
            if index == selected_index:
                normalized["locator"] = selected_locator
            normalized_candidates.append(normalized)

        evt["locator"] = selected_locator
        evt["locator_candidates"] = normalized_candidates
        validation = evt.get("validation")
        normalized_validation = dict(validation) if isinstance(validation, dict) else {}
        strict_match_count = selected_candidate.get("strict_match_count")
        if isinstance(strict_match_count, int):
            normalized_validation["status"] = "ok" if strict_match_count == 1 else "fallback"
        elif "status" not in normalized_validation:
            normalized_validation["status"] = "ok"
        if selected_candidate.get("reason"):
            normalized_validation["details"] = selected_candidate["reason"]
        normalized_validation["selected_candidate_index"] = selected_index
        normalized_validation["selected_candidate_kind"] = selected_candidate.get("kind", "")
        evt["validation"] = normalized_validation

    async def select_step_locator_candidate(self, session_id: str, step_index: int, candidate_index: int) -> RPAStep:
        session = self.sessions.get(session_id)
        if not session or step_index < 0 or step_index >= len(session.steps):
            raise ValueError("Invalid step index")

        step = session.steps[step_index]
        if candidate_index < 0 or candidate_index >= len(step.locator_candidates):
            raise ValueError("Invalid locator candidate index")

        selected_candidate = step.locator_candidates[candidate_index]
        locator = self._resolve_candidate_locator(selected_candidate)

        for index, candidate in enumerate(step.locator_candidates):
            candidate["selected"] = index == candidate_index

        selected_candidate["locator"] = locator

        step.target = json.dumps(locator)
        if step.validation:
            step.validation["selected_candidate_index"] = candidate_index
            step.validation["selected_candidate_kind"] = selected_candidate.get("kind", "")
            strict_match_count = selected_candidate.get("strict_match_count")
            if isinstance(strict_match_count, int):
                step.validation["status"] = "ok" if strict_match_count == 1 else "fallback"
            if selected_candidate.get("reason"):
                step.validation["details"] = selected_candidate["reason"]
        await self._broadcast_step(session_id, step)
        return step

    def pause_recording(self, session_id: str):
        """Pause event recording (used during AI execution)."""
        if session_id in self.sessions:
            self.sessions[session_id].paused = True

    def resume_recording(self, session_id: str):
        """Resume event recording."""
        if session_id in self.sessions:
            self.sessions[session_id].paused = False

    def get_page(self, session_id: str) -> Optional[Page]:
        active_page = self.get_active_page(session_id)
        if active_page is not None:
            return active_page
        return self._pages.get(session_id)

    def owns_sandbox_session(self, user_id: str, sandbox_session_id: str) -> bool:
        return any(
            session.user_id == user_id and session.sandbox_session_id == sandbox_session_id
            for session in self.sessions.values()
        )

    async def _handle_event(self, session_id: str, evt: dict):
        if session_id not in self.sessions:
            return
        session = self.sessions[session_id]
        if session.status != "recording" or session.paused:
            return

        event_tab_id = evt.get("tab_id")
        if event_tab_id and event_tab_id != session.active_tab_id:
            if event_tab_id in self._tabs.get(session_id, {}):
                await self.activate_tab(session_id, event_tab_id, source="event")

        if evt.get("action") == "navigate":
            nav_ts = evt.get("timestamp", 0)
            nav_sequence = evt.get("sequence")
            nav_tab_id = evt.get("tab_id")
            steps = self.sessions[session_id].steps
            predecessor = None

            def _step_event_ts_ms(step: RPAStep) -> int:
                if step.event_timestamp_ms is not None:
                    return step.event_timestamp_ms
                return int(step.timestamp.timestamp() * 1000)

            if steps and nav_sequence is not None:
                sequence_candidates = [
                    step
                    for step in steps
                    if step.sequence is not None
                    and step.sequence < nav_sequence
                    and (
                        step.tab_id == nav_tab_id
                        or self._step_targets_popup_tab(step, nav_tab_id)
                    )
                ]
                if sequence_candidates:
                    predecessor = max(sequence_candidates, key=lambda step: step.sequence)

            if steps and predecessor is None and nav_ts:
                timestamp_candidates = [
                    step
                    for step in steps
                    if _step_event_ts_ms(step) <= nav_ts
                    and (
                        step.tab_id == nav_tab_id
                        or self._step_targets_popup_tab(step, nav_tab_id)
                    )
                ]
                if timestamp_candidates:
                    predecessor = max(timestamp_candidates, key=_step_event_ts_ms)

            if steps and predecessor is None:
                for step in reversed(steps):
                    if step.tab_id == nav_tab_id or self._step_targets_popup_tab(step, nav_tab_id):
                        predecessor = step
                        break

            if predecessor:
                last_step = predecessor
                if (
                    self._step_targets_popup_tab(last_step, nav_tab_id)
                ):
                    logger.debug(f"[RPA] Skipping nav after popup open: {evt.get('url', '')[:60]}")
                    return
                if last_step.action in ("click", "press", "fill"):
                    last_ts = _step_event_ts_ms(last_step)
                    same_tab = last_step.tab_id == nav_tab_id
                    if nav_ts - last_ts < 5000 and same_tab:
                        if last_step.action == "click":
                            last_step.action = "navigate_click"
                            last_step.url = evt.get("url", last_step.url)
                            last_step.description = f"{last_step.description} 并跳转页面"
                            await self._broadcast_step(session_id, last_step)
                            logger.debug(f"[RPA] Upgraded click to navigate_click: {evt.get('url', '')[:60]}")
                            return
                        if last_step.action == "press":
                            last_step.action = "navigate_press"
                            last_step.url = evt.get("url", last_step.url)
                            await self._broadcast_step(session_id, last_step)
                            logger.debug(f"[RPA] Upgraded press to navigate_press: {evt.get('url', '')[:60]}")
                            return
                        logger.debug(f"[RPA] Preserving nav after {last_step.action}: {evt.get('url', '')[:60]}")

        self._normalize_event_locator_payload(evt)

        locator_info = evt.get("locator", {})
        is_sensitive = evt.get("sensitive", False)
        step_data = {
            "action": evt.get("action", "unknown"),
            "target": json.dumps(locator_info) if locator_info else "",
            "frame_path": evt.get("frame_path", []) or [],
            "locator_candidates": evt.get("locator_candidates", []) or [],
            "validation": evt.get("validation", {}) or {},
            "signals": evt.get("signals", {}) or {},
            "element_snapshot": evt.get("element_snapshot", {}) or {},
            "value": "{{credential}}" if is_sensitive else evt.get("value", ""),
            "label": "",
            "tag": evt.get("tag", ""),
            "url": evt.get("url", ""),
            "description": self._make_description(evt),
            "sensitive": is_sensitive,
            "tab_id": evt.get("tab_id"),
            "source_tab_id": evt.get("source_tab_id"),
            "target_tab_id": evt.get("target_tab_id"),
            "sequence": evt.get("sequence"),
            "event_timestamp_ms": evt.get("timestamp"),
        }
        await self.add_step(session_id, step_data)
        logger.debug(f"[RPA] Step: {step_data['description'][:60]}")

    @staticmethod
    def _make_description(evt: dict) -> str:
        action = evt.get("action", "")
        value = evt.get("value", "")
        locator = evt.get("locator", {})

        def _format_locator(value: Any) -> str:
            if not isinstance(value, dict):
                return str(value)

            method = value.get("method", "")
            if method == "role":
                name = value.get("name", "")
                return f'{value.get("role", "")}("{name}")' if name else value.get("role", "")
            if method in ("testid", "label", "placeholder", "alt", "title", "text"):
                return f'{method}("{value.get("value", "")}")'
            if method == "nested":
                parent = _format_locator(value.get("parent", {}))
                child = _format_locator(value.get("child", {}))
                return f"{parent} >> {child}"
            if method == "nth":
                base = value.get("locator", value.get("base"))
                base_target = _format_locator(base) if base is not None else "locator"
                return f"{base_target} >> nth={value.get('index', 0)}"
            if method == "css":
                return value.get("value", "")
            return str(value)

        target = _format_locator(locator)

        if action == "fill":
            display_value = '*****' if evt.get("sensitive") else f'"{value}"'
            return f'输入 {display_value} 到 {target}'
        if action == "click":
            return f"点击 {target}"
        if action == "check":
            return f"勾选 {target}"
        if action == "uncheck":
            return f"取消勾选 {target}"
        if action == "press":
            return f"按下 {value} 在 {target}"
        if action == "select":
            return f"选择 {value} 在 {target}"
        if action == "set_input_files":
            return f'上传文件 "{value}" 到 {target}'
        if action == "navigate":
            return f"导航到 {evt.get('url', '')}"
        if action == "download":
            return f"下载文件 {value}"
        return f"{action} on {target}"

    async def add_step(self, session_id: str, step_data: Dict[str, Any]) -> RPAStep:
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.sessions[session_id]
        step = RPAStep(id=str(uuid.uuid4()), **step_data)
        insert_at = len(session.steps)
        for index, existing_step in enumerate(session.steps):
            if existing_step.source != step.source:
                continue
            if self._step_sorts_before(step, existing_step):
                insert_at = index
                break

        if step.action == "fill" and step.source == "record":
            previous_step = session.steps[insert_at - 1] if insert_at > 0 else None
            if self._is_same_fill_target(previous_step, step):
                self._merge_fill_step(previous_step, step)
                await self._broadcast_step(session_id, previous_step)
                return previous_step

            next_step = session.steps[insert_at] if insert_at < len(session.steps) else None
            if self._is_same_fill_target(next_step, step):
                return next_step

        session.steps.insert(insert_at, step)

        await self._broadcast_step(session_id, step)
        return step

    @staticmethod
    def _step_event_ts_ms(step: RPAStep) -> int:
        if step.event_timestamp_ms is not None:
            return step.event_timestamp_ms
        return int(step.timestamp.timestamp() * 1000)

    @classmethod
    def _step_sorts_before(cls, incoming_step: RPAStep, existing_step: RPAStep) -> bool:
        incoming_ts = cls._step_event_ts_ms(incoming_step)
        existing_ts = cls._step_event_ts_ms(existing_step)
        if incoming_ts != existing_ts:
            return incoming_ts < existing_ts

        incoming_sequence = incoming_step.sequence
        existing_sequence = existing_step.sequence
        if (
            incoming_step.tab_id == existing_step.tab_id
            and incoming_sequence is not None
            and existing_sequence is not None
            and incoming_sequence != existing_sequence
        ):
            return incoming_sequence < existing_sequence

        return False

    @staticmethod
    def _is_same_fill_target(existing_step: Optional[RPAStep], incoming_step: RPAStep) -> bool:
        if existing_step is None:
            return False
        existing_sequence = existing_step.sequence
        incoming_sequence = incoming_step.sequence
        return (
            existing_step.action == "fill"
            and existing_step.source == incoming_step.source
            and existing_step.target == incoming_step.target
            and existing_step.frame_path == incoming_step.frame_path
            and existing_step.tab_id == incoming_step.tab_id
            and existing_sequence is not None
            and incoming_sequence is not None
            and abs(existing_sequence - incoming_sequence) == 1
        )

    @staticmethod
    def _merge_fill_step(existing_step: RPAStep, incoming_step: RPAStep) -> None:
        existing_step.value = incoming_step.value
        existing_step.description = incoming_step.description
        existing_step.tag = incoming_step.tag
        existing_step.url = incoming_step.url
        existing_step.sensitive = incoming_step.sensitive
        existing_step.locator_candidates = incoming_step.locator_candidates
        existing_step.validation = incoming_step.validation
        existing_step.signals = incoming_step.signals
        existing_step.element_snapshot = incoming_step.element_snapshot
        existing_step.sequence = incoming_step.sequence
        existing_step.event_timestamp_ms = incoming_step.event_timestamp_ms
        existing_step.timestamp = incoming_step.timestamp

    async def _broadcast_step(self, session_id: str, step: RPAStep):
        if session_id in self.ws_connections:
            message = {"type": "step", "data": step.model_dump()}
            for ws in self.ws_connections[session_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    def register_ws(self, session_id: str, websocket):
        if session_id not in self.ws_connections:
            self.ws_connections[session_id] = []
        self.ws_connections[session_id].append(websocket)

    def unregister_ws(self, session_id: str, websocket):
        if session_id in self.ws_connections:
            try:
                self.ws_connections[session_id].remove(websocket)
            except ValueError:
                pass


# ── Global instance ──────────────────────────────────────────────────
rpa_manager = RPASessionManager()

