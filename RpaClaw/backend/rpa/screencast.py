"""Session-scoped CDP screencast streaming with active-tab switching."""

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

SCREENCAST_FATAL_CLOSE_CODE = 1011


class SessionScreencastController:
    """Streams the currently active tab and switches targets when active tab changes."""

    def __init__(
        self,
        page_provider: Callable[[], Any],
        tabs_provider: Callable[[], list[dict[str, Any]]],
    ) -> None:
        self._page_provider = page_provider
        self._tabs_provider = tabs_provider
        self._ws: Optional[WebSocket] = None
        self._running = False
        self._page = None
        self._cdp = None
        self._viewport_width = 1280
        self._viewport_height = 720
        self._tabs_snapshot_json = ""
        self._last_preview_error = ""
        self._frames_sent = 0
        self._started_at = time.time()
        self._close_sent = False

    async def start(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._running = True
        logger.info("[Screencast] start controller")
        await self._emit_tabs_snapshot(force=True)
        await self._switch_page_if_needed(force=True)

        monitor_task = asyncio.create_task(self._monitor_loop())
        try:
            await self._recv_loop()
        except WebSocketDisconnect:
            logger.info("[Screencast] WebSocket disconnected")
        finally:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            await self.stop()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info(
            "[Screencast] stop controller frames_sent=%s uptime_s=%.2f",
            self._frames_sent,
            time.time() - self._started_at,
        )
        await self._detach_current_page()

    async def _fail_and_close(self, reason: str, *, code: int = SCREENCAST_FATAL_CLOSE_CODE) -> None:
        self._running = False
        if not self._ws or self._close_sent:
            return
        self._close_sent = True
        try:
            await self._ws.close(code=code, reason=reason[:120])
        except Exception as exc:
            logger.info("[Screencast] websocket close failed: %r", exc)

    async def _monitor_loop(self) -> None:
        while self._running:
            await self._emit_tabs_snapshot()
            try:
                await self._switch_page_if_needed()
            except Exception as exc:
                logger.error(f"[Screencast] switch error: {exc}")
                await self._emit_preview_error(str(exc))
            await asyncio.sleep(0.2)

    async def _emit_tabs_snapshot(self, force: bool = False) -> None:
        if not self._ws:
            return
        tabs = self._tabs_provider()
        payload = json.dumps(tabs, ensure_ascii=False, sort_keys=True)
        if not force and payload == self._tabs_snapshot_json:
            return
        self._tabs_snapshot_json = payload
        try:
            await self._ws.send_json({"type": "tabs_snapshot", "tabs": tabs})
        except Exception as exc:
            logger.info("[Screencast] tabs snapshot send failed: %r", exc)
            await self._fail_and_close("tabs snapshot send failed")

    async def _switch_page_if_needed(self, force: bool = False) -> None:
        next_page = self._page_provider()
        if next_page is None:
            if self._page is not None:
                logger.info("[Screencast] active page disappeared, detaching current page")
                await self._detach_current_page()
            return
        if not force and next_page is self._page:
            return

        await self._detach_current_page()
        last_error = None
        for attempt in range(2):
            try:
                self._page = next_page
                logger.info(
                    "[Screencast] attaching to page attempt=%s page_id=%s url=%s",
                    attempt + 1,
                    id(next_page),
                    getattr(next_page, "url", ""),
                )
                self._cdp = await next_page.context.new_cdp_session(next_page)
                self._cdp.on("Page.screencastFrame", self._on_frame)
                await self._cdp.send(
                    "Page.startScreencast",
                    {
                        "format": "jpeg",
                        "quality": 40,
                        "everyNthFrame": 1,
                    },
                )
                self._last_preview_error = ""
                logger.info(
                    "[Screencast] attached to page page_id=%s url=%s",
                    id(next_page),
                    getattr(next_page, "url", ""),
                )
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[Screencast] failed to attach page attempt=%s page_id=%s url=%s error=%r",
                    attempt + 1,
                    id(next_page),
                    getattr(next_page, "url", ""),
                    exc,
                )
                await self._detach_current_page()
                if attempt == 0:
                    await asyncio.sleep(0.2)

        assert last_error is not None
        await self._emit_preview_error(f"preview switch failed: {last_error}")
        raise last_error

    async def _detach_current_page(self) -> None:
        if not self._cdp:
            return
        logger.info(
            "[Screencast] detaching page page_id=%s url=%s",
            id(self._page) if self._page is not None else None,
            getattr(self._page, "url", "") if self._page is not None else "",
        )
        try:
            await self._cdp.send("Page.stopScreencast", {})
        except Exception:
            pass
        try:
            await self._cdp.detach()
        except Exception:
            pass
        self._cdp = None
        self._page = None

    async def _emit_preview_error(self, message: str) -> None:
        if not self._ws or not self._running:
            return
        if message == self._last_preview_error:
            return
        self._last_preview_error = message
        try:
            await self._ws.send_json({"type": "preview_error", "message": message})
        except Exception as exc:
            logger.info("[Screencast] preview error send failed: %r", exc)
            await self._fail_and_close("preview error send failed")

    async def _recv_loop(self) -> None:
        while self._running:
            try:
                raw = await asyncio.wait_for(self._ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    await self._ws.send_json({"type": "ping", "ts": time.time()})
                except Exception as exc:
                    logger.info("[Screencast] ping send failed: %r", exc)
                    break
                continue

            try:
                msg: Dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[Screencast] invalid JSON from client")
                continue

            msg_type = msg.get("type")
            if msg_type == "mouse":
                await self._dispatch_mouse(msg)
            elif msg_type == "keyboard":
                await self._dispatch_key(msg)
            elif msg_type == "wheel":
                await self._dispatch_wheel(msg)

    async def _on_frame(self, params: Dict[str, Any]) -> None:
        if not self._cdp or not self._ws or not self._running:
            return

        session_id = params.get("sessionId")
        if session_id:
            try:
                await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass

        metadata = params.get("metadata", {})
        device_w = metadata.get("deviceWidth")
        device_h = metadata.get("deviceHeight")
        if device_w and device_h:
            self._viewport_width = int(device_w)
            self._viewport_height = int(device_h)

        try:
            await self._ws.send_json(
                {
                    "type": "frame",
                    "data": params.get("data", ""),
                    "metadata": {
                        "width": self._viewport_width,
                        "height": self._viewport_height,
                        "timestamp": metadata.get("timestamp", time.time()),
                    },
                }
            )
            self._frames_sent += 1
            if self._frames_sent == 1:
                logger.info(
                    "[Screencast] first frame sent viewport=%sx%s",
                    self._viewport_width,
                    self._viewport_height,
                )
            self._last_preview_error = ""
        except Exception as exc:
            logger.info("[Screencast] frame send failed: %r", exc)
            await self._fail_and_close("frame send failed")

    async def _dispatch_mouse(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        x = event.get("x", 0) * self._viewport_width
        y = event.get("y", 0) * self._viewport_height
        params: Dict[str, Any] = {
            "type": event.get("action", "mouseMoved"),
            "x": x,
            "y": y,
            "button": event.get("button", "left") if event.get("action") != "mouseMoved" else "none",
            "clickCount": event.get("clickCount", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] mouse dispatch error: {exc}")

    async def _dispatch_key(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        params: Dict[str, Any] = {
            "type": event.get("action", "keyDown"),
            "key": event.get("key", ""),
            "code": event.get("code", ""),
            "text": event.get("text", ""),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchKeyEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] key dispatch error: {exc}")

    async def _dispatch_wheel(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        x = event.get("x", 0) * self._viewport_width
        y = event.get("y", 0) * self._viewport_height
        params: Dict[str, Any] = {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": event.get("deltaX", 0),
            "deltaY": event.get("deltaY", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] wheel dispatch error: {exc}")


class ScreencastService:
    """Backward-compatible single-page screencast service used by session preview routes."""

    def __init__(self, cdp_session: Any) -> None:
        self._cdp = cdp_session
        self._ws: Optional[WebSocket] = None
        self._running = False
        self._viewport_width = 1280
        self._viewport_height = 720

    async def start(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._running = True
        self._cdp.on("Page.screencastFrame", self._on_frame)
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 40,
                "everyNthFrame": 1,
            },
        )
        try:
            await self._recv_loop()
        except WebSocketDisconnect:
            logger.info("[Screencast] WebSocket disconnected")
        finally:
            await self.stop()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if not self._cdp:
            return
        try:
            await self._cdp.send("Page.stopScreencast", {})
        except Exception:
            pass

    async def _recv_loop(self) -> None:
        while self._running and self._ws:
            try:
                raw = await asyncio.wait_for(self._ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    await self._ws.send_json({"type": "ping", "ts": time.time()})
                except Exception:
                    break
                continue

            try:
                msg: Dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[Screencast] invalid JSON from client")
                continue

            msg_type = msg.get("type")
            if msg_type == "mouse":
                await self._dispatch_mouse(msg)
            elif msg_type == "keyboard":
                await self._dispatch_key(msg)
            elif msg_type == "wheel":
                await self._dispatch_wheel(msg)

    async def _on_frame(self, params: Dict[str, Any]) -> None:
        if not self._cdp or not self._ws or not self._running:
            return

        session_id = params.get("sessionId")
        if session_id:
            try:
                await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass

        metadata = params.get("metadata", {})
        device_w = metadata.get("deviceWidth")
        device_h = metadata.get("deviceHeight")
        if device_w and device_h:
            self._viewport_width = int(device_w)
            self._viewport_height = int(device_h)

        try:
            await self._ws.send_json(
                {
                    "type": "frame",
                    "data": params.get("data", ""),
                    "metadata": {
                        "width": self._viewport_width,
                        "height": self._viewport_height,
                        "timestamp": metadata.get("timestamp", time.time()),
                    },
                }
            )
        except Exception:
            self._running = False

    async def _dispatch_mouse(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        x = event.get("x", 0) * self._viewport_width
        y = event.get("y", 0) * self._viewport_height
        params: Dict[str, Any] = {
            "type": event.get("action", "mouseMoved"),
            "x": x,
            "y": y,
            "button": event.get("button", "left") if event.get("action") != "mouseMoved" else "none",
            "clickCount": event.get("clickCount", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] mouse dispatch error: {exc}")

    async def _dispatch_key(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        params: Dict[str, Any] = {
            "type": event.get("action", "keyDown"),
            "key": event.get("key", ""),
            "code": event.get("code", ""),
            "text": event.get("text", ""),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchKeyEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] key dispatch error: {exc}")

    async def _dispatch_wheel(self, event: Dict[str, Any]) -> None:
        if not self._cdp:
            return
        x = event.get("x", 0) * self._viewport_width
        y = event.get("y", 0) * self._viewport_height
        params: Dict[str, Any] = {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": event.get("deltaX", 0),
            "deltaY": event.get("deltaY", 0),
            "modifiers": event.get("modifiers", 0),
        }
        try:
            await self._cdp.send("Input.dispatchMouseEvent", params)
        except Exception as exc:
            logger.debug(f"[Screencast] wheel dispatch error: {exc}")
