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
from .manual_recording_models import ManualRecordedAction, ManualRecordingDiagnostic
from .manual_recording_normalizer import build_manual_recording_outcome
from .playwright_security import get_context_kwargs
from .region_context import RPARegionContext
from .trace_locator_utils import locator_has_unstable_identity, locator_instability_penalty
from .trace_models import RPAAcceptedTrace, RPAPageState, RPATraceDiagnostic, RPATraceType, RPARuntimeResults
from .trace_recorder import infer_dataflow_for_ai_fill, infer_dataflow_for_fill, manual_step_to_trace
from .harness.capture import HarnessCapturedPageState, HarnessCaptureSessionState
from .harness.capture import capture_current_page_state, capture_step_checkpoint
from .harness.config import harness_assets_dir
from .harness.input_contract import derive_fill_input_key
from .harness.store import HarnessAssetStore
from backend.config import settings

logger = logging.getLogger(__name__)

RPA_PAGE_TIMEOUT_MS = 60000
HOVER_PROMOTION_WINDOW_MS = 2500
RECORDED_FILL_DUPLICATE_WINDOW_MS = 3000
TRACE_DOWNLOAD_SETTLE_TIMEOUT_S = 3.0
TRACE_DOWNLOAD_SETTLE_POLL_S = 0.05
TRACE_DOWNLOAD_ACTION_TOKENS = (
    ".click(",
    ".press(",
    ".check(",
    ".uncheck(",
    ".select_option(",
)
TRACE_DOWNLOAD_INTENT_TOKENS = (
    "download",
    "file",
    "file name",
    "filename",
    "attachment",
    "xlsx",
    "xls",
    "csv",
    "pdf",
    "zip",
    "下载",
    "文件",
    "文件名",
    "文件名称",
    "附件",
)


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


class RPASkillConfigParam(BaseModel):
    original_value: str = ""
    default_value: Optional[str] = None
    enabled: bool = True
    sensitive: bool = False
    credential_id: str = ""
    type: str = "string"
    description: str = ""
    required: bool = False


class RPASkillConfigDraft(BaseModel):
    skill_name: str = ""
    description: str = ""
    params: Dict[str, RPASkillConfigParam] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=datetime.now)


class RPASession(BaseModel):
    id: str
    user_id: str
    start_time: datetime = Field(default_factory=datetime.now)
    last_activity_at: datetime = Field(default_factory=datetime.now)
    stopped_at: Optional[datetime] = None
    status: str = "recording"  # recording, stopped, testing, saved
    steps: List[RPAStep] = Field(default_factory=list)
    recorded_actions: List[ManualRecordedAction] = Field(default_factory=list)
    recording_diagnostics: List[ManualRecordingDiagnostic] = Field(default_factory=list)
    traces: List[RPAAcceptedTrace] = Field(default_factory=list)
    trace_diagnostics: List[RPATraceDiagnostic] = Field(default_factory=list)
    runtime_results: RPARuntimeResults = Field(default_factory=RPARuntimeResults)
    skill_config_draft: Optional[RPASkillConfigDraft] = None
    llm_model_config: Optional[Dict[str, Any]] = None
    pending_download_events: List[Dict[str, Any]] = Field(default_factory=list)
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
EXPLICIT_NAVIGATION_SUPPRESS_MARKER = "__explicit_navigation__"

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
        self._pending_hover_candidates: Dict[str, List[Dict[str, Any]]] = {}
        self._pending_event_counts: Dict[str, int] = {}
        self._pending_event_idle: Dict[str, asyncio.Event] = {}
        self._pending_region_contexts: Dict[str, Dict[str, RPARegionContext]] = {}
        self._harness_capture_sessions: Dict[str, HarnessCaptureSessionState] = {}
        self._manual_harness_before_states: Dict[str, Dict[str, HarnessCapturedPageState]] = {}
        self._manual_harness_checkpoint_candidates: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._harness_page_baselines: Dict[str, Dict[str, HarnessCapturedPageState]] = {}
        self._suppressed_navigation_events: Dict[str, Dict[str, str]] = {}
        self._explicit_navigation_trace_ids: Dict[str, Dict[str, str]] = {}

    def touch_session(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session:
            session.last_activity_at = datetime.now()

    def update_skill_config_draft(
        self,
        session_id: str,
        draft: RPASkillConfigDraft,
    ) -> RPASkillConfigDraft:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        draft.updated_at = datetime.now()
        session.skill_config_draft = draft
        self.touch_session(session_id)
        return draft

    async def cleanup_expired_sessions(self, max_idle_seconds: int) -> List[str]:
        now = datetime.now()
        removed: List[str] = []
        for session_id, session in list(self.sessions.items()):
            if session.status == "saved":
                continue
            idle_seconds = (now - session.last_activity_at).total_seconds()
            if idle_seconds <= max_idle_seconds:
                continue
            await self._dispose_session_resources(session_id)
            self.sessions.pop(session_id, None)
            self.ws_connections.pop(session_id, None)
            removed.append(session_id)
        return removed

    async def _dispose_session_resources(self, session_id: str) -> None:
        try:
            await asyncio.wait_for(self.wait_for_pending_events(session_id), timeout=2.0)
        except Exception as e:
            logger.warning(f"[RPA] Error waiting for pending events during cleanup: {e}")

        context = self._contexts.pop(session_id, None)
        self.detach_context(session_id)
        if context:
            try:
                await context.close()
            except Exception as e:
                logger.warning(f"[RPA] Error closing context during cleanup: {e}")

        connections = list(self.ws_connections.get(session_id, []) or [])
        for ws in connections:
            close = getattr(ws, "close", None)
            if not callable(close):
                continue
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"[RPA] Error closing websocket during cleanup: {e}")

    def attach_context(self, session_id: str, context: BrowserContext):
        self._contexts[session_id] = context
        self._pages.pop(session_id, None)
        self._tabs[session_id] = {}
        self._tab_meta[session_id] = {}
        self._page_tab_ids[session_id] = {}
        self._bridged_context_ids[session_id] = set()
        self._pending_event_counts[session_id] = 0
        idle_event = asyncio.Event()
        idle_event.set()
        self._pending_event_idle[session_id] = idle_event

        session = self.sessions.get(session_id)
        if session:
            session.active_tab_id = None
        self._pending_hover_candidates.pop(session_id, None)

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
        self._pending_event_counts.pop(session_id, None)
        self._pending_event_idle.pop(session_id, None)
        self._pending_region_contexts.pop(session_id, None)
        self._harness_capture_sessions.pop(session_id, None)
        self._manual_harness_before_states.pop(session_id, None)
        self._manual_harness_checkpoint_candidates.pop(session_id, None)
        self._harness_page_baselines.pop(session_id, None)
        self._suppressed_navigation_events.pop(session_id, None)
        self._explicit_navigation_trace_ids.pop(session_id, None)

        session = self.sessions.get(session_id)
        if session:
            session.active_tab_id = None
        self._pending_hover_candidates.pop(session_id, None)

    def get_harness_capture_session(self, session_id: str) -> Optional[HarnessCaptureSessionState]:
        return self._harness_capture_sessions.get(session_id)

    def start_harness_capture(
        self,
        session_id: str,
        *,
        capture_scope: str,
        enabled: bool,
    ) -> Optional[HarnessCaptureSessionState]:
        if not enabled:
            return None
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")
        state = HarnessCaptureSessionState(
            session_id=session_id,
            capture_scope=capture_scope,
        )
        self._manual_harness_before_states.pop(session_id, None)
        self._manual_harness_checkpoint_candidates.pop(session_id, None)
        self._harness_page_baselines.pop(session_id, None)
        self._harness_capture_sessions[session_id] = state
        return state

    def _is_full_sop_harness_active(self, session_id: str) -> bool:
        if not settings.rpa_harness_capture_enabled:
            return False
        state = self._harness_capture_sessions.get(session_id)
        return state is not None and state.capture_scope == "full_sop"

    async def _remember_harness_page_baseline(self, session_id: str, tab_id: Optional[str], page) -> None:
        if not tab_id or not self._is_full_sop_harness_active(session_id):
            return
        try:
            baseline = await capture_current_page_state(page)
        except Exception as exc:
            logger.debug("[RPA] Failed to capture harness page baseline: %s", exc)
            return
        self._harness_page_baselines.setdefault(session_id, {})[tab_id] = baseline

    def _attach_harness_navigation_baseline(self, session_id: str, evt: dict) -> None:
        if evt.get("action") != "navigate":
            return
        if not self._is_full_sop_harness_active(session_id):
            return
        tab_id = evt.get("tab_id")
        if not tab_id:
            return
        baseline = self._harness_page_baselines.get(session_id, {}).get(tab_id)
        if baseline is None:
            return
        evt["harness_before_page_state"] = baseline.model_dump(mode="json")

    def mark_harness_step_selected(
        self,
        session_id: str,
        *,
        step_index: int,
    ) -> Optional[HarnessCaptureSessionState]:
        state = self._harness_capture_sessions.get(session_id)
        if state is None:
            return None
        state.mark_step_selected(step_index)
        return state

    def mark_harness_next_natural_language_step_selected(
        self,
        session_id: str,
    ) -> Optional[HarnessCaptureSessionState]:
        state = self._harness_capture_sessions.get(session_id)
        if state is None:
            return None
        state.mark_next_natural_language_step_selected()
        return state

    async def set_harness_capture_runtime_active(self, session_id: str, active: bool) -> None:
        script = f"window.__rpa_harness_capture_active = {json.dumps(bool(active))};"
        context = self._contexts.get(session_id)
        if context is not None:
            try:
                await context.add_init_script(script=script)
            except Exception as exc:
                logger.debug("[RPA] Failed to set harness capture init flag: %s", exc)
        for page in list(self._tabs.get(session_id, {}).values()):
            try:
                await page.evaluate(script)
            except Exception as exc:
                logger.debug("[RPA] Failed to set harness capture page flag: %s", exc)
        if active:
            for tab_id, page in list(self._tabs.get(session_id, {}).items()):
                await self._remember_harness_page_baseline(session_id, tab_id, page)

    async def prepare_harness_step_capture(
        self,
        session_id: str,
        *,
        step_index: int,
        page,
    ) -> Optional[HarnessCapturedPageState]:
        if not settings.rpa_harness_capture_enabled:
            return None
        state = self._harness_capture_sessions.get(session_id)
        if state is None or not state.should_capture_step(step_index):
            return None
        return await capture_current_page_state(page)

    async def capture_harness_step_checkpoint(
        self,
        session_id: str,
        *,
        step_index: int,
        step_id: str,
        step_intent: str,
        recording_mode: str,
        before_page=None,
        after_page=None,
        before_state: Optional[HarnessCapturedPageState] = None,
        after_state: Optional[HarnessCapturedPageState] = None,
        trace_events: list[dict],
        runtime_status: str,
        error: Optional[str] = None,
        use_requested_full_sop_index: bool = False,
        extra_input_replacements: Optional[Dict[str, str]] = None,
    ):
        if not settings.rpa_harness_capture_enabled:
            return None
        state = self._harness_capture_sessions.get(session_id)
        if state is None:
            return None
        store = HarnessAssetStore(harness_assets_dir(settings))
        checkpoint = await capture_step_checkpoint(
            state,
            store,
            step_index=step_index,
            step_id=step_id,
            step_intent=step_intent,
            recording_mode=recording_mode,
            before_page=before_page,
            after_page=after_page,
            before_state=before_state,
            after_state=after_state,
            trace_events=trace_events,
            runtime_status=runtime_status,
            error=error,
            use_requested_full_sop_index=use_requested_full_sop_index,
            extra_input_replacements=extra_input_replacements,
        )
        if checkpoint is not None and recording_mode == "natural_language":
            state.consume_natural_language_step_selection(step_index)
        return checkpoint

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

        existing_tab_id = self._page_tab_ids.setdefault(session_id, {}).get(id(page))
        if existing_tab_id:
            if make_active or not self.sessions[session_id].active_tab_id:
                await self.activate_tab(session_id, existing_tab_id, source="auto")
            return existing_tab_id

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
        state = self._harness_capture_sessions.get(session_id)
        if settings.rpa_harness_capture_enabled and state is not None and state.capture_scope == "full_sop":
            try:
                await page.evaluate("window.__rpa_harness_capture_active = true;")
            except Exception as exc:
                logger.debug("[RPA] Failed to set harness capture page flag: %s", exc)

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

    async def activate_page(self, session_id: str, page: Page, tab_id: Optional[str] = None) -> str:
        existing_tab_id = self._page_tab_ids.get(session_id, {}).get(id(page))
        if existing_tab_id:
            await self.activate_tab(session_id, existing_tab_id, source="execution")
            return existing_tab_id

        return await self.register_page(
            session_id,
            page,
            opener_tab_id=None,
            make_active=True,
        )

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
        session = self.sessions.get(session_id)
        if not session:
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
        self._rebuild_manual_recording_state(session)
        await self._record_manual_trace_for_step(session_id, step)
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
        harness_state = self._harness_capture_sessions.get(session_id)
        should_record_navigation = session.status == "recording" and not session.paused
        harness_active = (
            should_record_navigation
            and settings.rpa_harness_capture_enabled
            and harness_state is not None
            and harness_state.capture_scope == "full_sop"
        )
        harness_before_state = None
        before_page = RPAPageState(
            url=getattr(page, "url", "") or "",
            title=await self._safe_page_title(page),
        )
        harness_step_index = len(session.traces) + 1
        if harness_active:
            harness_before_state = await capture_current_page_state(page)

        suppressed = self._suppressed_navigation_events.setdefault(session_id, {})
        suppressed[session.active_tab_id] = EXPLICIT_NAVIGATION_SUPPRESS_MARKER
        try:
            await page.goto(normalized_url)
            await page.wait_for_load_state("domcontentloaded")
        finally:
            suppressed = self._suppressed_navigation_events.get(session_id, {})
            if suppressed.get(session.active_tab_id) == EXPLICIT_NAVIGATION_SUPPRESS_MARKER:
                suppressed.pop(session.active_tab_id, None)
            if not suppressed:
                self._suppressed_navigation_events.pop(session_id, None)

        tab = self._tab_meta.get(session_id, {}).get(session.active_tab_id)
        after_title = await self._safe_page_title(page)
        if tab:
            tab.url = getattr(page, "url", normalized_url) or normalized_url
            tab.last_seen_at = datetime.now()
            if after_title:
                tab.title = after_title

        after_page = RPAPageState(
            url=getattr(page, "url", normalized_url) or normalized_url,
            title=after_title,
        )
        if not should_record_navigation:
            return {
                "tab_id": session.active_tab_id,
                "url": getattr(page, "url", normalized_url) or normalized_url,
            }

        after_state = await capture_current_page_state(page) if harness_before_state is not None else None
        trace = RPAAcceptedTrace(
            trace_id=f"trace-{uuid.uuid4()}",
            trace_type=RPATraceType.NAVIGATION,
            source="manual",
            action="navigate",
            description=f"Navigate to {normalized_url}",
            before_page=(
                RPAPageState(url=harness_before_state.url, title=harness_before_state.title)
                if harness_before_state is not None
                else before_page
            ),
            after_page=(
                RPAPageState(url=after_state.url, title=after_state.title)
                if after_state is not None
                else after_page
            ),
            value=normalized_url,
            signals={
                "tab": {"tab_id": session.active_tab_id},
                "navigation": {
                    "target_url": normalized_url,
                    "observed_url": after_page.url,
                    "redirected": not self._navigation_urls_match(after_page.url, normalized_url),
                },
            },
        )
        await self.append_trace(session_id, trace)
        self._explicit_navigation_trace_ids.setdefault(session_id, {})[session.active_tab_id] = trace.trace_id
        if harness_before_state is not None and after_state is not None:
            await self.capture_harness_step_checkpoint(
                session_id,
                step_index=harness_step_index,
                step_id=trace.trace_id,
                step_intent=trace.description,
                recording_mode="manual",
                before_state=harness_before_state,
                after_state=after_state,
                trace_events=[trace.model_dump(mode="json")],
                runtime_status="success",
            )
            self._harness_page_baselines.setdefault(session_id, {})[session.active_tab_id] = after_state

        return {
            "tab_id": session.active_tab_id,
            "url": getattr(page, "url", normalized_url) or normalized_url,
        }

    @staticmethod
    def _navigation_urls_match(left: str, right: str) -> bool:
        return str(left or "").rstrip("/") == str(right or "").rstrip("/")

    def _fold_explicit_navigation_redirect(self, session_id: str, evt: Dict[str, Any]) -> bool:
        tab_id = evt.get("tab_id")
        event_url = str(evt.get("url") or "")
        if not tab_id or not event_url:
            return False

        trace_id = self._explicit_navigation_trace_ids.get(session_id, {}).get(str(tab_id))
        if not trace_id:
            return False

        session = self.sessions.get(session_id)
        if not session:
            return False

        trace = next((candidate for candidate in reversed(session.traces) if candidate.trace_id == trace_id), None)
        if not trace or trace.trace_type != RPATraceType.NAVIGATION or trace.action != "navigate":
            self._clear_explicit_navigation_trace(session_id, tab_id)
            return False

        trace.after_page = RPAPageState(
            url=event_url,
            title=trace.after_page.title,
            snapshot_summary=trace.after_page.snapshot_summary,
        )
        signals = dict(trace.signals or {})
        navigation_signal = dict(signals.get("navigation") or {})
        target_url = str(navigation_signal.get("target_url") or trace.value or "")
        navigation_signal["target_url"] = target_url
        navigation_signal["observed_url"] = event_url
        navigation_signal["redirected"] = not self._navigation_urls_match(event_url, target_url)
        signals["navigation"] = navigation_signal
        trace.signals = signals
        return True

    def _clear_explicit_navigation_trace(self, session_id: str, tab_id: Any) -> None:
        if not tab_id:
            return
        pending = self._explicit_navigation_trace_ids.get(session_id)
        if not pending:
            return
        pending.pop(str(tab_id), None)
        if not pending:
            self._explicit_navigation_trace_ids.pop(session_id, None)

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

    def get_page_for_tab(self, session_id: str, tab_id: Optional[str] = None) -> Optional[Page]:
        if not tab_id:
            return self.get_page(session_id)
        return self._tabs.get(session_id, {}).get(tab_id)

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
                reported_frame_path = evt.get("frame_path", []) or []
                if reported_frame_path:
                    signals = evt.get("signals")
                    normalized_signals = dict(signals) if isinstance(signals, dict) else {}
                    normalized_signals["reported_frame_path"] = list(reported_frame_path)
                    evt["signals"] = normalized_signals
                if source_frame:
                    server_frame_path = await self._build_frame_path(source_frame)
                    if server_frame_path or not reported_frame_path:
                        evt["frame_path"] = server_frame_path
                await self._run_tracked_event(session_id, evt)
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
                suppressed = self._suppressed_navigation_events.get(session_id, {})
                expected_url = suppressed.get(tab_id)
                if expected_url == EXPLICIT_NAVIGATION_SUPPRESS_MARKER:
                    tab = self._tab_meta.get(session_id, {}).get(tab_id)
                    if tab:
                        tab.url = new_url
                        tab.last_seen_at = datetime.now()
                    return
                if expected_url and self._navigation_urls_match(new_url, expected_url):
                    suppressed.pop(tab_id, None)
                    if not suppressed:
                        self._suppressed_navigation_events.pop(session_id, None)
                    return
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
                asyncio.create_task(self._run_tracked_event(session_id, evt))

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
            session = self.sessions.get(session_id)
            if session and session.paused:
                session.pending_download_events.append(
                    {
                        "filename": suggested,
                        "url": getattr(page, "url", ""),
                        "tab_id": tab_id,
                        "opener_tab_id": opener_tab_id,
                        "event_timestamp_ms": int(datetime.now().timestamp() * 1000),
                    }
                )
                return
            # Fallback: no preceding click found, record standalone
            evt = {
                "action": "download",
                "value": suggested,
                "url": getattr(page, "url", ""),
                "timestamp": int(datetime.now().timestamp() * 1000),
                "tab_id": tab_id,
            }
            await self._run_tracked_event(session_id, evt)

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
        await self.wait_for_pending_events(session_id)
        if session_id in self.sessions:
            self.sessions[session_id].status = "stopped"
            self.sessions[session_id].stopped_at = datetime.now()
            self.touch_session(session_id)

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
        deleted_step = session.steps.pop(step_index)
        self._rebuild_manual_recording_state(session)
        deleted_trace_id = f"trace-{deleted_step.id}"
        session.traces = [
            trace
            for trace in session.traces
            if not (trace.source == "manual" and trace.trace_id == deleted_trace_id)
        ]
        return True

    async def delete_step_by_id(self, session_id: str, step_id: str) -> bool:
        """Delete a recorded step by stable ID and remove its derived manual trace."""
        session = self.sessions.get(session_id)
        if not session or not step_id:
            return False
        for index, step in enumerate(session.steps):
            if step.id == step_id:
                return await self.delete_step(session_id, index)
        return False

    async def delete_trace(self, session_id: str, trace_id: str) -> bool:
        """Delete an accepted trace by stable ID without relying on legacy step indexes."""
        session = self.sessions.get(session_id)
        if not session or not trace_id:
            return False
        original_count = len(session.traces)
        session.traces = [trace for trace in session.traces if trace.trace_id != trace_id]
        deleted = len(session.traces) != original_count
        if deleted:
            self._rebuild_runtime_results(session)
        return deleted

    async def delete_trace_diagnostic(self, session_id: str, diagnostic_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session or not diagnostic_id:
            return False
        diagnostic = next(
            (
                item
                for item in session.trace_diagnostics
                if item.diagnostic_id == diagnostic_id
            ),
            None,
        )
        if (
            diagnostic is not None
            and diagnostic.source == "manual_recording"
            and isinstance(diagnostic.raw, dict)
        ):
            related_step_id = str(diagnostic.raw.get("related_step_id") or "")
            if related_step_id:
                return await self.delete_step_by_id(session_id, related_step_id)
        original_count = len(session.trace_diagnostics)
        session.trace_diagnostics = [
            diagnostic
            for diagnostic in session.trace_diagnostics
            if diagnostic.diagnostic_id != diagnostic_id
        ]
        return len(session.trace_diagnostics) != original_count

    @staticmethod
    def _rebuild_runtime_results(session: RPASession) -> None:
        rebuilt = RPARuntimeResults()
        for trace in session.traces:
            rebuilt.write(trace.output_key, trace.output)
        session.runtime_results = rebuilt

    @staticmethod
    def _unescape_playwright_literal(value: str) -> str:
        return value.replace('\\"', '"').replace("\\\\", "\\")

    @staticmethod
    def _playwright_literal_pattern() -> str:
        return r'(?:"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\')'

    @staticmethod
    def _playwright_literal_value(match: re.Match, first_group: int) -> str:
        value = match.group(first_group)
        if value is None:
            value = match.group(first_group + 1)
        return RPASessionManager._unescape_playwright_literal(value or "")

    @classmethod
    def _parse_playwright_locator_expression(cls, expression: str) -> Optional[Dict[str, Any]]:
        if not expression:
            return None
        remaining = expression.strip()
        if remaining.startswith("page."):
            remaining = remaining[5:]

        literal = cls._playwright_literal_pattern()
        current: Optional[Dict[str, Any]] = None
        value_patterns = (
            ("testid", rf'get_by_test_id\({literal}\)'),
            ("label", rf'get_by_label\({literal}(?:,\s*exact=True)?\)'),
            ("placeholder", rf'get_by_placeholder\({literal}(?:,\s*exact=True)?\)'),
            ("alt", rf'get_by_alt_text\({literal}(?:,\s*exact=True)?\)'),
            ("title", rf'get_by_title\({literal}(?:,\s*exact=True)?\)'),
            ("text", rf'get_by_text\({literal}(?:,\s*exact=True)?\)'),
        )

        while remaining:
            matched_text = ""
            step: Optional[Dict[str, Any]] = None

            if current is None:
                locator_match = re.match(rf'locator\({literal}\)', remaining)
                if locator_match:
                    matched_text = locator_match.group(0)
                    step = {"method": "css", "value": cls._playwright_literal_value(locator_match, 1)}
                else:
                    role_match = re.match(
                        rf'get_by_role\({literal}(?:,\s*name={literal}(?:,\s*exact=True)?)?\)',
                        remaining,
                    )
                    if role_match:
                        matched_text = role_match.group(0)
                        step = {
                            "method": "role",
                            "role": cls._playwright_literal_value(role_match, 1),
                            "name": cls._playwright_literal_value(role_match, 3),
                        }
                    else:
                        for method, pattern in value_patterns:
                            match = re.match(pattern, remaining)
                            if not match:
                                continue
                            matched_text = match.group(0)
                            step = {"method": method, "value": cls._playwright_literal_value(match, 1)}
                            break
            else:
                filter_match = re.match(rf'\.filter\(has_text={literal}\)', remaining)
                if filter_match:
                    matched_text = filter_match.group(0)
                    current = {
                        "method": "filter_has_text",
                        "locator": current,
                        "has_text": cls._playwright_literal_value(filter_match, 1),
                    }
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
                        first_match = re.match(r'\.first(?:\(\))?', remaining)
                        if first_match:
                            matched_text = first_match.group(0)
                            current = {
                                "method": "nth",
                                "locator": current,
                                "index": 0,
                            }
                        else:
                            locator_match = re.match(rf'\.locator\({literal}\)', remaining)
                            if locator_match:
                                matched_text = locator_match.group(0)
                                step = {"method": "css", "value": cls._playwright_literal_value(locator_match, 1)}
                            else:
                                role_match = re.match(
                                    rf'\.get_by_role\({literal}(?:,\s*name={literal}(?:,\s*exact=True)?)?\)',
                                    remaining,
                                )
                                if role_match:
                                    matched_text = role_match.group(0)
                                    step = {
                                        "method": "role",
                                        "role": cls._playwright_literal_value(role_match, 1),
                                        "name": cls._playwright_literal_value(role_match, 3),
                                    }
                                else:
                                    for method, pattern in value_patterns:
                                        match = re.match(r"\." + pattern, remaining)
                                        if not match:
                                            continue
                                        matched_text = match.group(0)
                                        step = {"method": method, "value": cls._playwright_literal_value(match, 1)}
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
    def _candidate_score(candidate: Dict[str, Any], locator: Optional[Dict[str, Any]] = None) -> float:
        score = candidate.get("score")
        if not isinstance(score, (int, float)):
            return float("inf")
        return float(score) + RPASessionManager._locator_instability_penalty(candidate, locator=locator)

    @staticmethod
    def _locator_instability_penalty(
        candidate: Dict[str, Any],
        *,
        locator: Optional[Dict[str, Any]] = None,
    ) -> float:
        resolved_locator = locator if isinstance(locator, dict) else candidate.get("locator")
        if not isinstance(resolved_locator, dict):
            return 0.0

        return locator_instability_penalty(
            resolved_locator,
            extra_values=[
                candidate.get("selector"),
                candidate.get("playwright_locator"),
            ],
        )

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

            score = cls._candidate_score(candidate, locator=locator)
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
                selected_locator = selected_candidate_info[2] if selected_candidate_info else None
                selected_score = cls._candidate_score(selected_candidate, locator=selected_locator)
                best_score = cls._candidate_score(best_candidate, locator=best_locator)
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
        if locator_has_unstable_identity(locator):
            raise ValueError("Locator candidate is unstable and cannot be selected")

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
        self._rebuild_manual_recording_state(session)
        await self._record_manual_trace_for_step(session_id, step)
        await self._broadcast_step(session_id, step)
        return step

    async def select_trace_locator_candidate(
        self,
        session_id: str,
        trace_id: str,
        candidate_index: int,
    ) -> RPAAcceptedTrace:
        session = self.sessions.get(session_id)
        if not session or not trace_id:
            raise ValueError("Invalid trace id")

        trace = next((item for item in session.traces if item.trace_id == trace_id), None)
        if trace is None:
            raise ValueError("Invalid trace id")
        if candidate_index < 0 or candidate_index >= len(trace.locator_candidates):
            raise ValueError("Invalid locator candidate index")

        selected_candidate = trace.locator_candidates[candidate_index]
        locator = self._resolve_candidate_locator(selected_candidate)
        if locator_has_unstable_identity(locator):
            raise ValueError("Locator candidate is unstable and cannot be selected")

        for index, candidate in enumerate(trace.locator_candidates):
            candidate["selected"] = index == candidate_index

        selected_candidate["locator"] = locator
        trace.validation = dict(trace.validation or {})
        trace.validation["selected_candidate_index"] = candidate_index
        trace.validation["selected_candidate_kind"] = selected_candidate.get("kind", "")
        strict_match_count = selected_candidate.get("strict_match_count")
        if isinstance(strict_match_count, int):
            trace.validation["status"] = "ok" if strict_match_count == 1 else "fallback"
        if selected_candidate.get("reason"):
            trace.validation["details"] = selected_candidate["reason"]

        if trace.dataflow and trace.dataflow.target_field:
            trace.dataflow.target_field.locator_candidates = [
                dict(candidate) for candidate in trace.locator_candidates
            ]
        return trace

    async def resolve_trace_diagnostic_locator_candidate(
        self,
        session_id: str,
        diagnostic_id: str,
        candidate_index: int,
    ) -> RPAAcceptedTrace:
        session = self.sessions.get(session_id)
        if not session or not diagnostic_id:
            raise ValueError("Invalid diagnostic id")

        diagnostic = next(
            (item for item in session.trace_diagnostics if item.diagnostic_id == diagnostic_id),
            None,
        )
        if diagnostic is None:
            raise ValueError("Invalid diagnostic id")
        if diagnostic.source != "manual_recording" or not isinstance(diagnostic.raw, dict):
            raise ValueError("Diagnostic cannot be resolved by locator")

        related_step_id = str(diagnostic.raw.get("related_step_id") or "")
        step_index = next(
            (
                index
                for index, step in enumerate(session.steps)
                if step.id == related_step_id
            ),
            None,
        )
        if step_index is None:
            raise ValueError("Diagnostic backing step not found")

        await self.select_step_locator_candidate(session_id, step_index, candidate_index)
        trace_id = diagnostic.trace_id or f"trace-{related_step_id}"
        trace = next((item for item in session.traces if item.trace_id == trace_id), None)
        if trace is None:
            raise ValueError("Diagnostic did not resolve to trace")
        return trace

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

    def _ensure_pending_event_idle(self, session_id: str) -> asyncio.Event:
        idle_event = self._pending_event_idle.get(session_id)
        if idle_event is None:
            idle_event = asyncio.Event()
            idle_event.set()
            self._pending_event_idle[session_id] = idle_event
        self._pending_event_counts.setdefault(session_id, 0)
        return idle_event

    def _mark_pending_event_started(self, session_id: str) -> None:
        idle_event = self._ensure_pending_event_idle(session_id)
        self._pending_event_counts[session_id] = self._pending_event_counts.get(session_id, 0) + 1
        idle_event.clear()

    def _mark_pending_event_finished(self, session_id: str) -> None:
        if session_id not in self._pending_event_counts:
            return
        remaining = max(0, self._pending_event_counts.get(session_id, 0) - 1)
        self._pending_event_counts[session_id] = remaining
        if remaining == 0:
            self._ensure_pending_event_idle(session_id).set()

    async def _run_tracked_event(self, session_id: str, evt: Dict[str, Any]) -> None:
        self._mark_pending_event_started(session_id)
        try:
            await self._handle_event(session_id, evt)
        except Exception:
            logger.exception(
                "[RPA] failed to handle recorded event: session_id=%s action=%s url=%s locator=%s",
                session_id,
                evt.get("action"),
                evt.get("url"),
                evt.get("locator"),
            )
        finally:
            self._mark_pending_event_finished(session_id)

    async def wait_for_pending_events(self, session_id: str, timeout_ms: int = 1500) -> bool:
        idle_event = self._ensure_pending_event_idle(session_id)
        if self._pending_event_counts.get(session_id, 0) <= 0:
            return True
        try:
            await asyncio.wait_for(idle_event.wait(), timeout=timeout_ms / 1000)
            return True
        except asyncio.TimeoutError:
            logger.debug("[RPA] Timed out waiting for pending events for session %s", session_id)
            return False

    def _clear_pending_hover_candidates(self, session_id: str) -> None:
        self._pending_hover_candidates.pop(session_id, None)

    def _trim_pending_hover_candidates(self, session_id: str, reference_ts: Optional[int] = None) -> None:
        candidates = self._pending_hover_candidates.get(session_id)
        if not candidates:
            return
        if reference_ts is None:
            reference_ts = max(int(time.time() * 1000), 0)
        trimmed = []
        for candidate in candidates:
            candidate_ts = int(candidate.get("timestamp") or 0)
            if candidate_ts <= 0:
                continue
            if reference_ts >= candidate_ts and reference_ts - candidate_ts <= HOVER_PROMOTION_WINDOW_MS:
                trimmed.append(candidate)
        if trimmed:
            self._pending_hover_candidates[session_id] = trimmed[-8:]
        else:
            self._pending_hover_candidates.pop(session_id, None)

    def _queue_hover_candidate(self, session_id: str, evt: Dict[str, Any]) -> None:
        if self._is_menu_item_event(evt):
            return
        locator_signature = self._event_locator_signature(evt)
        if not locator_signature:
            return
        self._trim_pending_hover_candidates(session_id, int(evt.get("timestamp") or 0))
        queue = list(self._pending_hover_candidates.get(session_id, []))
        if queue:
            last = queue[-1]
            if (
                self._event_locator_signature(last) == locator_signature
                and (last.get("tab_id") or "") == (evt.get("tab_id") or "")
                and list(last.get("frame_path") or []) == list(evt.get("frame_path") or [])
            ):
                queue[-1] = dict(evt)
                self._pending_hover_candidates[session_id] = queue
                return
        queue.append(dict(evt))
        self._pending_hover_candidates[session_id] = queue[-8:]

    @staticmethod
    def _event_locator_signature(evt: Dict[str, Any]) -> str:
        locator = evt.get("locator")
        if not isinstance(locator, dict) or not locator:
            return ""
        try:
            return json.dumps(locator, sort_keys=True, ensure_ascii=False)
        except TypeError:
            return ""

    @classmethod
    def _is_menu_trigger_hover_candidate(cls, evt: Dict[str, Any]) -> bool:
        if evt.get("action") != "hover":
            return False
        signals = evt.get("signals")
        hover_signal = signals.get("hover") if isinstance(signals, dict) else None
        return isinstance(hover_signal, dict) and hover_signal.get("is_menu_trigger_candidate") is True

    @classmethod
    def _is_menu_item_click(cls, evt: Dict[str, Any]) -> bool:
        if evt.get("action") != "click":
            return False
        return cls._is_menu_item_event(evt)

    @classmethod
    def _is_menu_item_event(cls, evt: Dict[str, Any]) -> bool:
        signals = evt.get("signals")
        menu_context = signals.get("menu_context") if isinstance(signals, dict) else None
        if isinstance(menu_context, dict) and menu_context.get("is_menu_item") is True:
            return True
        locator = evt.get("locator")
        if isinstance(locator, dict) and locator.get("method") == "role":
            role = str(locator.get("role") or "").lower()
            if role in {"menuitem", "menuitemcheckbox", "menuitemradio", "option"}:
                return True
        return False

    @classmethod
    def _is_nonsemantic_initial_document_click(cls, session: RPASession, evt: Dict[str, Any]) -> bool:
        if evt.get("action") != "click":
            return False
        if session.steps or session.traces or session.recorded_actions:
            return False
        if cls._event_has_side_effect_evidence(evt):
            return False
        return cls._is_document_surface_click(evt)

    @staticmethod
    def _event_has_side_effect_evidence(evt: Dict[str, Any]) -> bool:
        if evt.get("target_tab_id") or evt.get("source_tab_id"):
            return True
        signals = evt.get("signals")
        if not isinstance(signals, dict):
            return False
        side_effect_keys = {
            "download",
            "file_chooser",
            "navigation",
            "open_tab",
            "popup",
            "set_input_files",
            "tab",
        }
        harmless_keys = {"recording"}
        for key, value in signals.items():
            if value in (None, "", {}, []):
                continue
            if key in side_effect_keys:
                return True
            if key not in harmless_keys:
                return True
        return False

    @staticmethod
    def _is_document_surface_click(evt: Dict[str, Any]) -> bool:
        snapshot = evt.get("element_snapshot") if isinstance(evt.get("element_snapshot"), dict) else {}
        tag = str(evt.get("tag") or snapshot.get("tag") or "").strip().lower()
        locator = evt.get("locator") if isinstance(evt.get("locator"), dict) else {}
        locator_method = str(locator.get("method") or "").strip().lower()
        locator_value = str(locator.get("value") or "").strip().lower()
        return tag in {"body", "html"} and locator_method == "css" and locator_value in {"body", "html"}

    def _consume_promotable_hover_candidate(self, session_id: str, click_evt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candidates = list(self._pending_hover_candidates.get(session_id, []))
        if not candidates:
            return None
        self._trim_pending_hover_candidates(session_id, int(click_evt.get("timestamp") or 0))
        candidates = list(self._pending_hover_candidates.get(session_id, []))
        if not candidates:
            return None
        if not self._is_menu_item_click(click_evt):
            self._clear_pending_hover_candidates(session_id)
            return None

        click_ts = int(click_evt.get("timestamp") or 0)
        eligible: List[Dict[str, Any]] = []
        for candidate in candidates:
            candidate_ts = int(candidate.get("timestamp") or 0)
            if candidate_ts <= 0 or click_ts <= 0 or click_ts < candidate_ts:
                continue
            if click_ts - candidate_ts > HOVER_PROMOTION_WINDOW_MS:
                continue
            if (candidate.get("tab_id") or "") != (click_evt.get("tab_id") or ""):
                continue
            if list(candidate.get("frame_path") or []) != list(click_evt.get("frame_path") or []):
                continue
            if self._event_locator_signature(candidate) == self._event_locator_signature(click_evt):
                continue
            eligible.append(candidate)

        self._clear_pending_hover_candidates(session_id)
        if not eligible:
            return None

        strong_candidates = [candidate for candidate in eligible if self._is_menu_trigger_hover_candidate(candidate)]
        pool = strong_candidates or eligible
        return max(pool, key=lambda candidate: int(candidate.get("timestamp") or 0))

    @staticmethod
    def _step_data_from_event(evt: Dict[str, Any]) -> Dict[str, Any]:
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
            "description": RPASessionManager._make_description(evt),
            "sensitive": is_sensitive,
            "tab_id": evt.get("tab_id"),
            "source_tab_id": evt.get("source_tab_id"),
            "target_tab_id": evt.get("target_tab_id"),
            "sequence": evt.get("sequence"),
            "event_timestamp_ms": evt.get("timestamp"),
        }
        harness_before_state = evt.get("harness_before_page_state")
        if isinstance(harness_before_state, dict):
            step_data["_harness_before_state"] = dict(harness_before_state)
        return step_data

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
            if self._fold_explicit_navigation_redirect(session_id, evt):
                return
            self._clear_pending_hover_candidates(session_id)
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
                            self._rebuild_manual_recording_state(session)
                            await self._record_manual_trace_for_step(session_id, last_step)
                            await self._broadcast_step(session_id, last_step)
                            logger.debug(f"[RPA] Upgraded click to navigate_click: {evt.get('url', '')[:60]}")
                            return
                        if last_step.action == "press":
                            last_step.action = "navigate_press"
                            last_step.url = evt.get("url", last_step.url)
                            self._rebuild_manual_recording_state(session)
                            await self._record_manual_trace_for_step(session_id, last_step)
                            await self._broadcast_step(session_id, last_step)
                            logger.debug(f"[RPA] Upgraded press to navigate_press: {evt.get('url', '')[:60]}")
                            return
                        logger.debug(f"[RPA] Preserving nav after {last_step.action}: {evt.get('url', '')[:60]}")

            self._attach_harness_navigation_baseline(session_id, evt)
        else:
            self._clear_explicit_navigation_trace(session_id, event_tab_id or session.active_tab_id)

        self._normalize_event_locator_payload(evt)

        if self._is_nonsemantic_initial_document_click(session, evt):
            self._clear_pending_hover_candidates(session_id)
            logger.debug("[RPA] Ignored nonsemantic initial document click")
            return

        if evt.get("action") == "hover":
            self._queue_hover_candidate(session_id, evt)
            return

        if evt.get("action") == "click":
            hover_evt = self._consume_promotable_hover_candidate(session_id, evt)
            if hover_evt:
                hover_step = self._step_data_from_event(hover_evt)
                await self.add_step(session_id, hover_step)
                logger.debug(f"[RPA] Step: {hover_step['description'][:60]}")
        else:
            self._clear_pending_hover_candidates(session_id)

        step_data = self._step_data_from_event(evt)
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
        if action == "hover":
            return f"悬停到 {target}"
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
        harness_before_state = step_data.pop("_harness_before_state", None)
        step = RPAStep(id=str(uuid.uuid4()), **step_data)
        insert_at = len(session.steps)
        for index, existing_step in enumerate(session.steps):
            if existing_step.source != step.source:
                continue
            if self._step_sorts_before(step, existing_step):
                insert_at = index
                break

        if step.action == "fill" and step.source == "record":
            if not isinstance(harness_before_state, dict):
                focus_before_state = self._text_input_focus_before_state_for_fill(
                    session_id,
                    session,
                    insert_at,
                    step,
                )
                if focus_before_state is not None:
                    harness_before_state = focus_before_state.model_dump(mode="json")
            insert_at = self._drop_redundant_pre_fill_focus_click(session, insert_at, step)
            previous_step = session.steps[insert_at - 1] if insert_at > 0 else None
            if self._is_same_fill_target(previous_step, step):
                self._merge_fill_step(previous_step, step)
                self._rebuild_manual_recording_state(session)
                await self._record_manual_trace_for_step(session_id, previous_step)
                await self._broadcast_step(session_id, previous_step)
                return previous_step

            next_step = session.steps[insert_at] if insert_at < len(session.steps) else None
            if self._is_same_fill_target(next_step, step):
                return next_step

        session.steps.insert(insert_at, step)
        if isinstance(harness_before_state, dict):
            self._manual_harness_before_states.setdefault(session_id, {})[step.id] = HarnessCapturedPageState(
                url=str(harness_before_state.get("url", "") or ""),
                title=str(harness_before_state.get("title", "") or ""),
                html=str(harness_before_state.get("html", "") or ""),
            )
        self._rebuild_manual_recording_state(session)
        await self._record_manual_trace_for_step(session_id, step)

        await self._broadcast_step(session_id, step)
        return step

    @staticmethod
    def _manual_trace_diagnostic_from_recording(
        diagnostic: ManualRecordingDiagnostic,
    ) -> RPATraceDiagnostic:
        related_step_id = diagnostic.related_step_id or ""
        page_state = dict(diagnostic.page_state or {})
        repairable_candidates, rejected_candidates = RPASessionManager._split_repairable_locator_candidates(
            diagnostic.raw_candidates or []
        )
        raw = {
            "related_step_id": related_step_id,
            "failure_reason": diagnostic.failure_reason,
            "action": diagnostic.related_action_kind.value,
            "locator_candidates": repairable_candidates,
            "rejected_locator_candidates": rejected_candidates,
            "element_snapshot": dict(diagnostic.element_snapshot or {}),
            "page_state": page_state,
            "url": str(page_state.get("url", "") or ""),
        }
        return RPATraceDiagnostic(
            diagnostic_id=f"diag-{related_step_id}" if related_step_id else "",
            trace_id=f"trace-{related_step_id}" if related_step_id else None,
            source="manual_recording",
            message=diagnostic.failure_reason,
            raw=raw,
        )

    @classmethod
    def _split_repairable_locator_candidates(
        cls,
        candidates: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        repairable: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            copied = dict(candidate)
            try:
                locator = cls._resolve_candidate_locator(copied)
            except ValueError:
                repairable.append(copied)
                continue
            copied["locator"] = locator
            if locator_has_unstable_identity(locator):
                copied["rejected_reason"] = "unstable_target_locator"
                rejected.append(copied)
            else:
                repairable.append(copied)
        return repairable, rejected

    @classmethod
    def _sync_manual_trace_diagnostics(cls, session: RPASession) -> None:
        preserved = [
            diagnostic
            for diagnostic in session.trace_diagnostics
            if diagnostic.source != "manual_recording"
        ]
        manual_diagnostics = [
            cls._manual_trace_diagnostic_from_recording(diagnostic)
            for diagnostic in session.recording_diagnostics
            if diagnostic.related_step_id
        ]
        session.trace_diagnostics = preserved + manual_diagnostics

    @staticmethod
    def _has_manual_trace_diagnostic_for_step(session: RPASession, step_id: str) -> bool:
        trace_id = f"trace-{step_id}"
        return any(
            diagnostic.source == "manual_recording"
            and (
                diagnostic.trace_id == trace_id
                or str((diagnostic.raw or {}).get("related_step_id") or "") == step_id
            )
            for diagnostic in session.trace_diagnostics
        )

    @staticmethod
    def _rebuild_manual_recording_state(session: RPASession) -> None:
        session.recorded_actions = []
        session.recording_diagnostics = []

        for step in session.steps:
            if step.source != "record":
                continue
            outcome = build_manual_recording_outcome(
                step_id=step.id,
                action=step.action,
                description=step.description or "",
                target=step.target or "",
                frame_path=step.frame_path,
                locator_candidates=step.locator_candidates,
                validation=step.validation,
                value=step.value,
                element_snapshot=step.element_snapshot,
                page_state={"url": step.url or ""},
                signals=step.signals,
            )
            if outcome.accepted_action is not None:
                session.recorded_actions.append(outcome.accepted_action)
            if outcome.diagnostic is not None:
                if step.action in {"navigate_click", "navigate_press"} and step.url:
                    continue
                session.recording_diagnostics.append(outcome.diagnostic)
        RPASessionManager._sync_manual_trace_diagnostics(session)

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

    @classmethod
    def _is_same_fill_target(cls, existing_step: Optional[RPAStep], incoming_step: RPAStep) -> bool:
        if existing_step is None:
            return False
        existing_sequence = existing_step.sequence
        incoming_sequence = incoming_step.sequence
        if not (
            existing_step.action == "fill"
            and existing_step.source == incoming_step.source
            and existing_step.target == incoming_step.target
            and existing_step.frame_path == incoming_step.frame_path
            and existing_step.tab_id == incoming_step.tab_id
        ):
            return False
        if existing_sequence is not None and incoming_sequence is not None:
            if abs(existing_sequence - incoming_sequence) == 1:
                return True
        if existing_step.value != incoming_step.value:
            return False
        existing_ts = cls._step_event_ts_ms(existing_step)
        incoming_ts = cls._step_event_ts_ms(incoming_step)
        return abs(incoming_ts - existing_ts) <= RECORDED_FILL_DUPLICATE_WINDOW_MS

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

    def _text_input_focus_before_state_for_fill(
        self,
        session_id: str,
        session: RPASession,
        insert_at: int,
        fill_step: RPAStep,
    ) -> Optional[HarnessCapturedPageState]:
        previous_step = session.steps[insert_at - 1] if insert_at > 0 else None
        if not self._is_same_text_input_focus_target(previous_step, fill_step):
            return None
        return self._manual_harness_before_states.get(session_id, {}).get(previous_step.id)

    def _late_text_input_focus_before_state_for_fill(
        self,
        session_id: str,
        session: RPASession,
        fill_step: RPAStep,
    ) -> Optional[HarnessCapturedPageState]:
        try:
            fill_index = next(
                index
                for index, existing_step in enumerate(session.steps)
                if existing_step.id == fill_step.id
            )
        except StopIteration:
            return None
        previous_step = session.steps[fill_index - 1] if fill_index > 0 else None
        if not self._is_same_text_input_focus_target(previous_step, fill_step):
            return None
        candidates = self._manual_harness_checkpoint_candidates.get(session_id, {})
        previous_candidate = candidates.get(previous_step.id) or {}
        before_state = previous_candidate.get("before_state")
        if isinstance(before_state, HarnessCapturedPageState):
            return before_state
        return self._manual_harness_before_states.get(session_id, {}).get(previous_step.id)

    @classmethod
    def _harness_input_replacements_for_trace(cls, trace: RPAAcceptedTrace) -> Dict[str, str]:
        event = trace.model_dump(mode="json")
        if str(event.get("action") or "").strip() != "fill":
            return {}
        raw_value = str(event.get("value") or "")
        if not raw_value or raw_value.startswith("{{input:"):
            return {}
        input_key = derive_fill_input_key(event)
        return {raw_value: f"{{{{input:{input_key}}}}}"}

    @classmethod
    def _is_same_text_input_focus_target(
        cls,
        click_step: Optional[RPAStep],
        fill_step: RPAStep,
    ) -> bool:
        if click_step is None:
            return False
        if click_step.action != "click" or fill_step.action != "fill":
            return False
        if click_step.source != "record" or fill_step.source != "record":
            return False
        if not cls._is_text_input_focus_click(click_step):
            return False
        if click_step.target != fill_step.target:
            return False
        if click_step.tab_id != fill_step.tab_id or click_step.frame_path != fill_step.frame_path:
            return False

        click_sequence = click_step.sequence
        fill_sequence = fill_step.sequence
        if click_sequence is not None and fill_sequence is not None:
            return fill_sequence - click_sequence == 1

        click_ts = cls._step_event_ts_ms(click_step)
        fill_ts = cls._step_event_ts_ms(fill_step)
        return click_ts <= fill_ts and fill_ts - click_ts <= 3000

    @classmethod
    def _is_redundant_pre_fill_focus_click(
        cls,
        session: RPASession,
        click_step: Optional[RPAStep],
        fill_step: RPAStep,
    ) -> bool:
        if click_step is None:
            return False
        if click_step.action != "click" or fill_step.action != "fill":
            return False
        if click_step.source != "record" or fill_step.source != "record":
            return False
        if click_step.tab_id != fill_step.tab_id or click_step.frame_path != fill_step.frame_path:
            return False
        is_text_input_focus = cls._is_same_text_input_focus_target(click_step, fill_step)
        if is_text_input_focus:
            return True
        if not cls._has_manual_trace_diagnostic_for_step(session, click_step.id):
            return False

        click_sequence = click_step.sequence
        fill_sequence = fill_step.sequence
        if click_sequence is not None and fill_sequence is not None:
            if fill_sequence - click_sequence != 1:
                return False
        else:
            click_ts = cls._step_event_ts_ms(click_step)
            fill_ts = cls._step_event_ts_ms(fill_step)
            if fill_ts < click_ts or fill_ts - click_ts > 3000:
                return False

        snapshot = click_step.element_snapshot if isinstance(click_step.element_snapshot, dict) else {}
        tag = str(click_step.tag or snapshot.get("tag") or "").strip().lower()
        role = str(snapshot.get("role") or "").strip().lower()
        interactive_tags = {"a", "button", "input", "select", "textarea"}
        interactive_roles = {
            "button",
            "link",
            "checkbox",
            "radio",
            "tab",
            "menuitem",
            "option",
            "switch",
            "combobox",
        }
        return tag not in interactive_tags and role not in interactive_roles

    @classmethod
    def _drop_redundant_pre_fill_focus_click(
        cls,
        session: RPASession,
        insert_at: int,
        fill_step: RPAStep,
    ) -> int:
        previous_index = insert_at - 1
        if previous_index < 0:
            return insert_at
        previous_step = session.steps[previous_index]
        if not cls._is_redundant_pre_fill_focus_click(session, previous_step, fill_step):
            return insert_at

        removed_step = session.steps.pop(previous_index)
        removed_trace_id = f"trace-{removed_step.id}"
        session.traces = [trace for trace in session.traces if trace.trace_id != removed_trace_id]
        cls._rebuild_manual_recording_state(session)
        return previous_index

    async def _broadcast_step(self, session_id: str, step: RPAStep):
        if session_id in self.ws_connections:
            message = {"type": "step", "data": step.model_dump()}
            for ws in self.ws_connections[session_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def append_trace(self, session_id: str, trace: RPAAcceptedTrace) -> List[RPAAcceptedTrace]:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        self._merge_pending_downloads_into_trace(session, trace)
        trace = infer_dataflow_for_ai_fill(trace, session.runtime_results)
        session.traces.append(trace)
        await self._broadcast_trace(session_id, trace)
        return session.traces

    @staticmethod
    def _merge_pending_downloads_into_trace(session: RPASession, trace: RPAAcceptedTrace) -> None:
        if not session.pending_download_events:
            return
        if trace.source != "ai":
            return
        signals = dict(trace.signals or {})
        if isinstance(signals.get("download"), dict):
            return
        downloads = list(session.pending_download_events)
        first = dict(downloads[0])
        download_signal = {
            "filename": first.get("filename", ""),
            "url": first.get("url", ""),
            "tab_id": first.get("tab_id"),
            "opener_tab_id": first.get("opener_tab_id"),
            "event_timestamp_ms": first.get("event_timestamp_ms"),
            "count": len(downloads),
        }
        if len(downloads) > 1:
            download_signal["files"] = downloads
        signals["download"] = {key: value for key, value in download_signal.items() if value is not None}
        trace.signals = signals
        session.pending_download_events.clear()

    @staticmethod
    def _trace_has_download_signal(trace: RPAAcceptedTrace) -> bool:
        signals = trace.signals if isinstance(trace.signals, dict) else {}
        return isinstance(signals.get("download"), dict)

    @classmethod
    def _should_wait_for_pending_download(cls, trace: RPAAcceptedTrace) -> bool:
        if trace.trace_type != RPATraceType.AI_OPERATION or trace.source != "ai":
            return False
        code = str(trace.ai_execution.code if trace.ai_execution else "")
        if not any(token in code for token in TRACE_DOWNLOAD_ACTION_TOKENS):
            return False
        if cls._trace_url_changed(trace):
            return False
        text = " ".join(
            str(value or "")
            for value in (
                trace.user_instruction,
                trace.description,
                code,
            )
        ).lower()
        return any(token in text for token in TRACE_DOWNLOAD_INTENT_TOKENS)

    @staticmethod
    def _trace_url_changed(trace: RPAAcceptedTrace) -> bool:
        before = str(getattr(trace.before_page, "url", "") or "").rstrip("/")
        after = str(getattr(trace.after_page, "url", "") or "").rstrip("/")
        return bool(before and after and before != after)

    async def append_trace_diagnostic(self, session_id: str, diagnostic: RPATraceDiagnostic) -> List[RPATraceDiagnostic]:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        session.trace_diagnostics.append(diagnostic)
        if session_id in self.ws_connections:
            message = {"type": "trace_diagnostic", "data": diagnostic.model_dump(mode="json")}
            for ws in self.ws_connections[session_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass
        return session.trace_diagnostics

    def write_runtime_result(self, session_id: str, key: Optional[str], value: Any) -> None:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        session.runtime_results.write(key, value)

    async def finalize_trace_side_effects(
        self,
        session_id: str,
        trace: RPAAcceptedTrace,
        *,
        timeout_s: float = TRACE_DOWNLOAD_SETTLE_TIMEOUT_S,
    ) -> None:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        self._merge_pending_downloads_into_trace(session, trace)
        if self._trace_has_download_signal(trace) or not self._should_wait_for_pending_download(trace):
            return

        deadline = asyncio.get_running_loop().time() + max(0.0, timeout_s)
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(TRACE_DOWNLOAD_SETTLE_POLL_S)
            self._merge_pending_downloads_into_trace(session, trace)
            if self._trace_has_download_signal(trace):
                return

    def store_region_context(
        self,
        session_id: str,
        context: RPARegionContext,
    ) -> RPARegionContext:
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")
        self._pending_region_contexts[session_id] = {context.region_id: context}
        return context

    def resolve_region_context(
        self,
        session_id: str,
        region_id: str | None,
        *,
        current_url: str | None = None,
    ) -> Optional[RPARegionContext]:
        if not region_id:
            return None
        context = self._pending_region_contexts.get(session_id, {}).get(region_id)
        if context is None:
            return None
        if current_url and context.page_url and context.page_url != current_url:
            return None
        return context

    def clear_region_context(self, session_id: str, region_id: str | None = None) -> None:
        if region_id:
            contexts = self._pending_region_contexts.get(session_id)
            if contexts is not None:
                contexts.pop(region_id, None)
                if not contexts:
                    self._pending_region_contexts.pop(session_id, None)
            return
        self._pending_region_contexts.pop(session_id, None)

    async def _record_manual_trace_for_step(self, session_id: str, step: RPAStep) -> None:
        if step.source != "record":
            return
        session = self.sessions.get(session_id)
        if not session:
            return
        try:
            trace_id = f"trace-{step.id}"
            if self._has_manual_trace_diagnostic_for_step(session, step.id):
                session.traces = [
                    existing for existing in session.traces if existing.trace_id != trace_id
                ]
                return
            trace = manual_step_to_trace(step.model_dump())
            trace = infer_dataflow_for_fill(trace, session.runtime_results)
            session.traces = [existing for existing in session.traces if existing.trace_id != trace.trace_id]
            session.traces.append(trace)
            await self._broadcast_trace(session_id, trace)
            await self._capture_manual_harness_checkpoint_for_step(session_id, step, trace)
        except Exception as exc:
            logger.debug("[RPA] Failed to record manual trace for step %s: %s", step.id, exc)

    @staticmethod
    def _is_text_input_focus_click(step: RPAStep) -> bool:
        if step.action != "click":
            return False
        snapshot = step.element_snapshot if isinstance(step.element_snapshot, dict) else {}
        tag = str(step.tag or snapshot.get("tag") or "").strip().lower()
        attrs = snapshot.get("attributes") if isinstance(snapshot.get("attributes"), dict) else {}
        input_type = str(attrs.get("type") or snapshot.get("type") or "").strip().lower()
        role = str(attrs.get("role") or snapshot.get("role") or "").strip().lower()
        non_text_input_types = {
            "button",
            "checkbox",
            "color",
            "date",
            "file",
            "hidden",
            "image",
            "radio",
            "range",
            "reset",
            "submit",
        }
        if tag == "textarea":
            return True
        if tag == "input":
            return input_type not in non_text_input_types
        return role == "textbox"

    async def _capture_manual_harness_checkpoint_for_step(
        self,
        session_id: str,
        step: RPAStep,
        trace: RPAAcceptedTrace,
    ) -> None:
        if not settings.rpa_harness_capture_enabled:
            return
        state = self._harness_capture_sessions.get(session_id)
        if state is None or state.capture_scope != "full_sop":
            return
        if step.action not in {"click", "fill", "press", "navigate", "navigate_click", "navigate_press"}:
            return
        page = self.get_page(session_id)
        if page is None:
            return
        session = self.sessions.get(session_id)
        if not session:
            return
        before_state = self._manual_harness_before_states.get(session_id, {}).get(step.id)
        after_state = None
        if not self._is_text_input_focus_click(step):
            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if callable(wait_for_timeout):
                await wait_for_timeout(100)
            after_state = await capture_current_page_state(page)
        self._manual_harness_checkpoint_candidates.setdefault(session_id, {})[step.id] = {
            "step": step,
            "trace": trace,
            "before_state": before_state,
            "after_state": after_state,
        }
        await self._flush_manual_harness_full_sop_checkpoints(session_id)

    async def _flush_manual_harness_full_sop_checkpoints(self, session_id: str) -> None:
        if not settings.rpa_harness_capture_enabled:
            return
        state = self._harness_capture_sessions.get(session_id)
        if state is None or state.capture_scope != "full_sop":
            return
        session = self.sessions.get(session_id)
        if not session:
            return
        page = self.get_page(session_id)
        if page is None:
            return
        candidates = self._manual_harness_checkpoint_candidates.get(session_id, {})
        step_index = 0
        last_state_by_tab = self._harness_page_baselines.setdefault(session_id, {})
        cumulative_input_replacements: Dict[str, str] = {}
        for step in session.steps:
            if step.source != "record":
                continue
            if step.action not in {"click", "fill", "press", "navigate", "navigate_click", "navigate_press"}:
                continue
            if self._is_text_input_focus_click(step):
                continue
            candidate = candidates.get(step.id)
            if not candidate:
                continue
            trace = candidate.get("trace")
            if not isinstance(trace, RPAAcceptedTrace):
                continue
            cumulative_input_replacements.update(self._harness_input_replacements_for_trace(trace))
            before_state = candidate.get("before_state")
            if not isinstance(before_state, HarnessCapturedPageState) and step.action == "fill":
                before_state = self._late_text_input_focus_before_state_for_fill(session_id, session, step)
            if not isinstance(before_state, HarnessCapturedPageState):
                continue
            after_state = candidate.get("after_state")
            if not isinstance(after_state, HarnessCapturedPageState):
                after_state = await capture_current_page_state(page)
                candidate["after_state"] = after_state
            step_index += 1
            await self.capture_harness_step_checkpoint(
                session_id,
                step_index=step_index,
                step_id=trace.trace_id,
                step_intent=trace.description or step.description or step.action,
                recording_mode="manual",
                before_state=before_state,
                after_state=after_state,
                trace_events=[trace.model_dump(mode="json")],
                runtime_status="success",
                use_requested_full_sop_index=True,
                extra_input_replacements=cumulative_input_replacements,
            )
            if step.tab_id:
                last_state_by_tab[step.tab_id] = after_state

    async def _broadcast_trace(self, session_id: str, trace: RPAAcceptedTrace) -> None:
        if session_id in self.ws_connections:
            message = {"type": "trace_added", "data": trace.model_dump(mode="json")}
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

