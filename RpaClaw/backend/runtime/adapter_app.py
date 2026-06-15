from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import copy
import glob
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from backend.runtime.adapter_file_policy import MAX_FILE_DOWNLOAD_BYTES, MAX_INLINE_FILE_WRITE_BYTES


DEFAULT_EXECUTION_TIMEOUT_SECONDS = 30
MAX_EXECUTION_TIMEOUT_SECONDS = 120
MAX_EXECUTION_OUTPUT_CHARS = 4096
MAX_FILE_WRITE_BYTES = MAX_INLINE_FILE_WRITE_BYTES
DEFAULT_BROWSER_DEBUG_PORT = 9222
DEFAULT_BROWSER_LAUNCH_TIMEOUT_SECONDS = 15
RUNTIME_ADAPTER_LISTENER_MARKER = "__rpaclawRuntimeAdapterListener"
RUNTIME_ADAPTER_LISTENER_SCRIPT = f"""
(() => {{
  if (window.{RUNTIME_ADAPTER_LISTENER_MARKER}) return;
  const events = [];
  Object.defineProperty(window, "{RUNTIME_ADAPTER_LISTENER_MARKER}", {{
    value: {{
      version: "v1",
      events,
      installedAt: Date.now()
    }},
    configurable: false
  }});
  const push = (event) => {{
    events.push({{
      type: event.type,
      tagName: event.target && event.target.tagName,
      timestamp: Date.now()
    }});
    if (events.length > 100) events.shift();
  }};
  window.addEventListener("click", push, true);
  window.addEventListener("input", push, true);
}})();
"""
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
SENSITIVE_SUBPROCESS_ENV_KEYWORDS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


class LocalBrowserController:
    def __init__(
        self,
        *,
        workspace_root: Path,
        executable: str | None = None,
        debug_port: int = DEFAULT_BROWSER_DEBUG_PORT,
        cdp_public_url: str | None = None,
        cdp_public_base_url: str | None = None,
        browser_view_url: str | None = None,
        launch_timeout_seconds: int = DEFAULT_BROWSER_LAUNCH_TIMEOUT_SECONDS,
    ):
        self.workspace_root = workspace_root
        self.executable = (executable or "").strip() or None
        self.debug_port = int(debug_port or DEFAULT_BROWSER_DEBUG_PORT)
        self.cdp_public_url = (cdp_public_url or "").strip() or None
        self.cdp_public_base_url = (cdp_public_base_url or "").strip().rstrip("/") or None
        self.browser_view_url = (browser_view_url or "").strip() or None
        self.launch_timeout_seconds = max(1, int(launch_timeout_seconds or DEFAULT_BROWSER_LAUNCH_TIMEOUT_SECONDS))
        self.process: subprocess.Popen | None = None
        self._listener_status: dict[str, Any] | None = None

    def _resolve_executable(self) -> str:
        if self.executable:
            if "*" in self.executable:
                matches = sorted(glob.glob(self.executable))
                if matches:
                    return matches[0]
            resolved = shutil.which(self.executable) or self.executable
            return resolved
        playwright_chromium = [
            *sorted(Path("/ms-playwright").glob("chromium-*/chrome-linux64/chrome")),
            *sorted(Path("/ms-playwright").glob("chromium-*/chrome-linux/chrome")),
        ]
        if playwright_chromium:
            return str(playwright_chromium[0])
        for candidate in (
            "chromium",
            "chromium-browser",
            "google-chrome",
            "google-chrome-stable",
            "msedge",
            "microsoft-edge",
        ):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        raise HTTPException(status_code=503, detail="Chromium executable is not available")

    def _version_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/version"

    def _page_list_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/list"

    def _browser_user_data_dir(self) -> Path:
        return self.workspace_root / ".runtime-adapter-browser"

    def _start_browser(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        user_data_dir = self._browser_user_data_dir()
        user_data_dir.mkdir(parents=True, exist_ok=True)
        executable = self._resolve_executable()
        command = [
            executable,
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--remote-debugging-address=0.0.0.0",
            f"--remote-debugging-port={self.debug_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={user_data_dir}",
            "about:blank",
        ]
        self.process = subprocess.Popen(  # noqa: S603 - explicit browser executable, shell=False.
            command,
            cwd=str(self.workspace_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )

    def _read_json(self, url: str) -> Any:
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))

    def _wait_for_browser_version(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.launch_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                payload = self._read_json(self._version_url())
                if isinstance(payload, dict) and payload.get("webSocketDebuggerUrl"):
                    return payload
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
            time.sleep(0.25)
        raise HTTPException(status_code=503, detail=f"Timed out waiting for local browser CDP: {last_error}")

    async def _inject_listener(self) -> dict[str, Any]:
        try:
            pages = await asyncio.to_thread(self._read_json, self._page_list_url())
            if not isinstance(pages, list) or not pages:
                return {"status": "unavailable", "marker": RUNTIME_ADAPTER_LISTENER_MARKER}
            page_ws = pages[0].get("webSocketDebuggerUrl")
            if not page_ws:
                return {"status": "unavailable", "marker": RUNTIME_ADAPTER_LISTENER_MARKER}
            import websockets

            async with websockets.connect(page_ws, open_timeout=2) as websocket:
                await websocket.send(
                    json.dumps(
                        {
                            "id": 1,
                            "method": "Page.addScriptToEvaluateOnNewDocument",
                            "params": {"source": RUNTIME_ADAPTER_LISTENER_SCRIPT},
                        }
                    )
                )
                await websocket.recv()
                await websocket.send(
                    json.dumps(
                        {
                            "id": 2,
                            "method": "Runtime.evaluate",
                            "params": {"expression": RUNTIME_ADAPTER_LISTENER_SCRIPT},
                        }
                    )
                )
                await websocket.recv()
            return {"status": "injected", "marker": RUNTIME_ADAPTER_LISTENER_MARKER}
        except Exception as exc:
            return {
                "status": "failed",
                "marker": RUNTIME_ADAPTER_LISTENER_MARKER,
                "reason": type(exc).__name__,
            }

    async def ensure_browser(self) -> dict[str, Any]:
        await asyncio.to_thread(self._start_browser)
        version = await asyncio.to_thread(self._wait_for_browser_version)
        private_cdp_url = str(version.get("webSocketDebuggerUrl") or "")
        cdp_url = self.cdp_public_url or private_cdp_url
        if self.cdp_public_base_url and private_cdp_url:
            parsed = urllib.parse.urlparse(private_cdp_url)
            cdp_url = f"{self.cdp_public_base_url}{parsed.path}"
        if not cdp_url:
            raise HTTPException(status_code=503, detail="Local browser CDP URL is not available")
        if self._listener_status is None or self._listener_status.get("status") != "injected":
            self._listener_status = await self._inject_listener()
        return {
            "cdp_url": cdp_url,
            "browser_view_url": self.browser_view_url,
            "listener": self._listener_status,
        }


class LocalRuntimeAdapterState:
    def __init__(
        self,
        *,
        workspace_root: Path,
        enable_local_execution: bool = False,
        cdp_url: str | None = None,
        browser_view_url: str | None = None,
        adapter_token: str | None = None,
        downloads_dir: str | None = None,
        adapter_version: str | None = None,
        enable_browser_launch: bool = False,
        browser_executable: str | None = None,
        browser_debug_port: int = DEFAULT_BROWSER_DEBUG_PORT,
        browser_cdp_public_url: str | None = None,
        browser_cdp_public_base_url: str | None = None,
        browser_launch_timeout_seconds: int = DEFAULT_BROWSER_LAUNCH_TIMEOUT_SECONDS,
    ):
        self.workspace_root = workspace_root.resolve()
        self.enable_local_execution = enable_local_execution
        self.cdp_url = (cdp_url or "").strip() or None
        self.browser_view_url = (browser_view_url or "").strip() or None
        self.adapter_token = (adapter_token or "").strip() or None
        self.downloads_dir = (downloads_dir or "downloads").strip() or "downloads"
        self.adapter_version = (adapter_version or "").strip() or "local-dev"
        self.enable_browser_launch = enable_browser_launch
        self.browser_controller = (
            LocalBrowserController(
                workspace_root=self.workspace_root,
                executable=browser_executable,
                debug_port=browser_debug_port,
                cdp_public_url=browser_cdp_public_url,
                cdp_public_base_url=browser_cdp_public_base_url,
                browser_view_url=self.browser_view_url,
                launch_timeout_seconds=browser_launch_timeout_seconds,
            )
            if enable_browser_launch
            else None
        )
        self.recording = False
        self.events: list[dict[str, Any]] = []
        self.next_event_cursor = 1
        self.snapshot: dict[str, Any] = {
            "raw_snapshot": {},
            "compact_snapshot": {},
            "page_state": {
                "url": None,
                "title": None,
            },
        }

    def resolve_workspace_path(self, path: str) -> Path:
        requested = Path(path)
        target = requested if requested.is_absolute() else self.workspace_root / requested
        resolved = target.resolve()
        if resolved != self.workspace_root and self.workspace_root not in resolved.parents:
            raise HTTPException(status_code=403, detail="Path is outside adapter workspace")
        return resolved

    def relative_path(self, path: Path) -> str:
        return path.relative_to(self.workspace_root).as_posix()

    def append_event(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "cursor": str(self.next_event_cursor),
            "type": event_type,
            **(payload or {}),
        }
        self.next_event_cursor += 1
        self.events.append(event)
        return event

    def workspace_health(self) -> tuple[bool, list[str]]:
        if self.workspace_root.exists() and not self.workspace_root.is_dir():
            return False, ["workspace_root is not a directory"]
        return True, []

    def download_health(self) -> tuple[bool, list[str]]:
        try:
            target = self.resolve_workspace_path(self.downloads_dir)
        except HTTPException:
            return False, ["downloads_dir is outside adapter workspace"]
        if target.exists() and not target.is_dir():
            return False, ["downloads_dir is not a directory"]
        return True, []

    def health_payload(self) -> dict[str, Any]:
        workspace_ready, workspace_issues = self.workspace_health()
        downloads_ready, download_issues = self.download_health()
        issues = [*workspace_issues, *download_issues]
        workspace_capabilities_ready = workspace_ready
        return {
            "status": "ok" if not issues else "degraded",
            "surface": "runtime-adapter",
            "contract_version": "v1",
            "mode": "local",
            "capabilities": {
                "browser_info": bool(self.cdp_url),
                "browser_launch": bool(self.browser_controller),
                "execute_step": self.enable_local_execution and workspace_capabilities_ready,
                "run_skill": self.enable_local_execution and workspace_capabilities_ready,
                "downloads": downloads_ready and workspace_capabilities_ready,
                "files": workspace_capabilities_ready,
            },
            "config": {
                "workspace_root": str(self.workspace_root),
                "downloads_dir": self.downloads_dir,
                "adapter_version": self.adapter_version,
                "browser_view_url": self.browser_view_url,
                "file_policy": {
                    "max_inline_file_write_bytes": MAX_INLINE_FILE_WRITE_BYTES,
                    "max_file_download_bytes": MAX_FILE_DOWNLOAD_BYTES,
                    "oversized_hash_status": "skipped_oversized",
                },
                "token_required": bool(self.adapter_token),
                "issues": issues,
            },
        }


def create_runtime_adapter_app(
    *,
    workspace_root: str | Path | None = None,
    enable_local_execution: bool = False,
    cdp_url: str | None = None,
    browser_view_url: str | None = None,
    adapter_token: str | None = None,
    downloads_dir: str | None = None,
    adapter_version: str | None = None,
    enable_browser_launch: bool = False,
    browser_executable: str | None = None,
    browser_debug_port: int = DEFAULT_BROWSER_DEBUG_PORT,
    browser_cdp_public_url: str | None = None,
    browser_cdp_public_base_url: str | None = None,
    browser_launch_timeout_seconds: int = DEFAULT_BROWSER_LAUNCH_TIMEOUT_SECONDS,
) -> FastAPI:
    """Create a local AIO Runtime Adapter-compatible FastAPI app.

    This app is intentionally a thin execution-plane shell. It exposes the
    semantic endpoints the Host Backend calls, but it does not infer accepted
    traces, expected signals, or skill facts.
    """

    root = Path(workspace_root or Path.cwd())
    state = LocalRuntimeAdapterState(
        workspace_root=root,
        enable_local_execution=enable_local_execution,
        cdp_url=cdp_url,
        browser_view_url=browser_view_url,
        adapter_token=adapter_token,
        downloads_dir=downloads_dir,
        adapter_version=adapter_version,
        enable_browser_launch=enable_browser_launch,
        browser_executable=browser_executable,
        browser_debug_port=browser_debug_port,
        browser_cdp_public_url=browser_cdp_public_url,
        browser_cdp_public_base_url=browser_cdp_public_base_url,
        browser_launch_timeout_seconds=browser_launch_timeout_seconds,
    )
    app = FastAPI(title="RpaClaw Runtime Adapter", version="0.1.0")

    @app.middleware("http")
    async def require_adapter_token(request: Request, call_next):
        if not state.adapter_token:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        prefix = "Bearer "
        if not auth.startswith(prefix):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing runtime adapter bearer token"},
            )
        token = auth[len(prefix):].strip()
        if not hmac.compare_digest(token, state.adapter_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid runtime adapter bearer token"},
            )
        return await call_next(request)

    def _normalize_command(command: Any) -> list[str]:
        if not isinstance(command, list) or not command:
            raise HTTPException(status_code=400, detail="command must be a non-empty list")
        normalized = []
        for part in command:
            if not isinstance(part, str) or not part:
                raise HTTPException(status_code=400, detail="command entries must be non-empty strings")
            normalized.append(sys.executable if part == "python" else part)
        return normalized

    def _normalize_args(args: Any) -> list[str]:
        if args is None:
            return []
        if not isinstance(args, list):
            raise HTTPException(status_code=400, detail="args must be a list")
        normalized = []
        for arg in args:
            if not isinstance(arg, str):
                raise HTTPException(status_code=400, detail="args entries must be strings")
            normalized.append(arg)
        return normalized

    def _resolve_skill_script(skill_path: Any) -> Path:
        if not isinstance(skill_path, str) or not skill_path:
            raise HTTPException(status_code=400, detail="skill_path must be a non-empty string")
        target = state.resolve_workspace_path(skill_path)
        script = target / "skill.py" if target.is_dir() else target
        if not script.exists():
            raise HTTPException(status_code=404, detail="skill.py does not exist")
        if not script.is_file():
            raise HTTPException(status_code=400, detail="skill_path is not a file")
        if script.name != "skill.py":
            raise HTTPException(status_code=400, detail="skill_path must point to skill.py or a skill directory")
        return script

    def _normalize_timeout(value: Any) -> int:
        if value is None:
            return DEFAULT_EXECUTION_TIMEOUT_SECONDS
        try:
            timeout = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="timeout_seconds must be an integer") from exc
        if timeout < 1:
            raise HTTPException(status_code=400, detail="timeout_seconds must be positive")
        return min(timeout, MAX_EXECUTION_TIMEOUT_SECONDS)

    def _bounded_output(value: Any) -> tuple[str, bool]:
        text = value or ""
        if len(text) <= MAX_EXECUTION_OUTPUT_CHARS:
            return text, False
        return text[:MAX_EXECUTION_OUTPUT_CHARS], True

    def _subprocess_env() -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(
                keyword in key.lower()
                for keyword in SENSITIVE_SUBPROCESS_ENV_KEYWORDS
            )
        }
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _run_command(command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                env=_subprocess_env(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout, stdout_truncated = _bounded_output(exc.stdout)
            stderr, stderr_truncated = _bounded_output(exc.stderr)
            return {
                "status": "timeout",
                "exit_code": None,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "timeout_seconds": timeout,
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Command not found: {command[0]}") from exc

        stdout, stdout_truncated = _bounded_output(completed.stdout)
        stderr, stderr_truncated = _bounded_output(completed.stderr)
        return {
            "status": "success" if completed.returncode == 0 else "failed",
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "timeout_seconds": timeout,
        }

    def _workspace_health() -> tuple[bool, list[str]]:
        return state.workspace_health()

    def _require_workspace_ready() -> None:
        workspace_ready, issues = _workspace_health()
        if not workspace_ready:
            raise HTTPException(status_code=503, detail=issues[0])

    def _download_health() -> tuple[bool, list[str]]:
        return state.download_health()

    def _normalize_snapshot(payload: dict[str, Any] | None) -> dict[str, Any]:
        payload = payload or {}
        return {
            "raw_snapshot": payload.get("raw_snapshot") or {},
            "compact_snapshot": payload.get("compact_snapshot") or {},
            "page_state": payload.get("page_state") or {"url": None, "title": None},
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return state.health_payload()

    @app.post("/rpa/recording/start")
    async def start_recording(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        state.recording = True
        state.events = []
        state.next_event_cursor = 1
        state.append_event("recording_started", {"session_id": payload.get("session_id")})
        return {
            "status": "recording",
            "session_id": payload.get("session_id"),
        }

    @app.post("/rpa/recording/stop")
    async def stop_recording(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        state.recording = False
        state.append_event("recording_stopped", {"session_id": payload.get("session_id")})
        return {
            "status": "stopped",
            "session_id": payload.get("session_id"),
        }

    @app.get("/rpa/events")
    async def get_events(cursor: str = "0") -> dict[str, Any]:
        try:
            cursor_value = int(cursor)
        except (TypeError, ValueError):
            cursor_value = 0
        events = [
            event
            for event in state.events
            if int(str(event.get("cursor") or "0")) > cursor_value
        ]
        next_cursor = events[-1]["cursor"] if events else str(max(cursor_value, state.next_event_cursor - 1))
        return {
            "events": events,
            "next_cursor": next_cursor,
        }

    @app.post("/rpa/events/emit")
    async def emit_raw_event(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if payload is None:
            raise HTTPException(status_code=400, detail="event payload must be a JSON object")
        return state.append_event("raw_event", payload)

    @app.get("/rpa/snapshot")
    async def get_snapshot() -> dict[str, Any]:
        return state.snapshot

    @app.post("/rpa/snapshot/emit")
    async def emit_snapshot(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if payload is None:
            raise HTTPException(status_code=400, detail="snapshot payload must be a JSON object")
        state.snapshot = _normalize_snapshot(payload)
        return state.snapshot

    @app.get("/v1/browser/info")
    async def browser_info() -> dict[str, Any]:
        if state.browser_controller is not None:
            browser = await state.browser_controller.ensure_browser()
            return {
                "status": "success",
                "data": browser,
            }
        if not state.cdp_url:
            raise HTTPException(status_code=503, detail="CDP browser is not configured")
        return {
            "status": "success",
            "data": {
                "cdp_url": state.cdp_url,
                "browser_view_url": state.browser_view_url,
            },
        }

    @app.post("/rpa/execute-step")
    async def execute_step(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        if state.enable_local_execution and "command" in payload:
            _require_workspace_ready()
            before_snapshot = copy.deepcopy(state.snapshot)
            command = _normalize_command(payload.get("command"))
            cwd = state.resolve_workspace_path(str(payload.get("cwd") or "."))
            if not cwd.exists():
                raise HTTPException(status_code=404, detail="cwd does not exist")
            if not cwd.is_dir():
                raise HTTPException(status_code=400, detail="cwd is not a directory")
            timeout = _normalize_timeout(payload.get("timeout_seconds"))
            result = await asyncio.to_thread(_run_command, command, cwd, timeout)
            if isinstance(payload.get("after_snapshot"), dict):
                state.snapshot = _normalize_snapshot(payload.get("after_snapshot"))
            result["before_snapshot"] = before_snapshot
            result["after_snapshot"] = copy.deepcopy(state.snapshot)
            return result

        return {
            "status": "not_implemented",
            "result": None,
            "logs": [],
            "payload": payload,
        }

    @app.post("/rpa/run-skill")
    async def run_skill(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        if state.enable_local_execution and "skill_path" in payload:
            _require_workspace_ready()
            before_snapshot = copy.deepcopy(state.snapshot)
            script = _resolve_skill_script(payload.get("skill_path"))
            args = _normalize_args(payload.get("args"))
            timeout = _normalize_timeout(payload.get("timeout_seconds"))
            result = await asyncio.to_thread(
                _run_command,
                [sys.executable, str(script), *args],
                script.parent,
                timeout,
            )
            result["skill_path"] = state.relative_path(script)
            if isinstance(payload.get("after_snapshot"), dict):
                state.snapshot = _normalize_snapshot(payload.get("after_snapshot"))
            result["before_snapshot"] = before_snapshot
            result["after_snapshot"] = copy.deepcopy(state.snapshot)
            return result

        return {
            "status": "not_implemented",
            "result": None,
            "logs": [],
            "payload": payload,
        }

    @app.get("/rpa/downloads")
    async def list_downloads() -> dict[str, list[dict[str, Any]]]:
        _require_workspace_ready()
        target = state.resolve_workspace_path(state.downloads_dir)
        if not target.exists():
            return {"downloads": []}
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="downloads_dir is not a directory")

        downloads = []
        for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_file():
                continue
            size = child.stat().st_size
            download = {
                "name": child.name,
                "path": state.relative_path(child),
                "sha256": None
                if size > MAX_FILE_DOWNLOAD_BYTES
                else hashlib.sha256(child.read_bytes()).hexdigest(),
                "size": size,
            }
            if size > MAX_FILE_DOWNLOAD_BYTES:
                download["hash_status"] = "skipped_oversized"
            downloads.append(download)
        return {"downloads": downloads}

    @app.get("/files/list")
    async def list_files(path: str = Query(".")) -> dict[str, Any]:
        _require_workspace_ready()
        target = state.resolve_workspace_path(path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path does not exist")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")

        entries = []
        for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
            entries.append(
                {
                    "name": child.name,
                    "path": state.relative_path(child),
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return {
            "path": path,
            "entries": entries,
        }

    @app.post("/files/write")
    async def write_file(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        _require_workspace_ready()
        payload = payload or {}
        path = payload.get("path")
        content = payload.get("content")
        content_base64 = payload.get("content_base64")
        if not isinstance(path, str) or not path:
            raise HTTPException(status_code=400, detail="path must be a non-empty string")
        has_content = content is not None
        has_content_base64 = content_base64 is not None
        if has_content == has_content_base64:
            raise HTTPException(status_code=400, detail="Provide exactly one of content or content_base64")
        if has_content and not isinstance(content, str):
            raise HTTPException(status_code=400, detail="content must be a string")
        if has_content_base64 and not isinstance(content_base64, str):
            raise HTTPException(status_code=400, detail="content_base64 must be a string")

        target = state.resolve_workspace_path(path)
        if target.exists() and target.is_dir():
            raise HTTPException(status_code=400, detail="path is a directory")
        if has_content_base64:
            try:
                encoded = base64.b64decode(content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(status_code=400, detail="content_base64 is invalid") from exc
        else:
            encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_WRITE_BYTES:
            raise HTTPException(status_code=413, detail="file content exceeds adapter write limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        return {
            "status": "success",
            "path": state.relative_path(target),
            "size": len(encoded),
        }

    @app.get("/files/download")
    async def download_file(path: str = Query(...)) -> Response:
        _require_workspace_ready()
        target = state.resolve_workspace_path(path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="File does not exist")
        if not target.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")
        if target.stat().st_size > MAX_FILE_DOWNLOAD_BYTES:
            raise HTTPException(status_code=413, detail="file exceeds adapter download limit")
        return Response(
            content=target.read_bytes(),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
        )

    return app


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_ENV_VALUES


def create_runtime_adapter_app_from_env() -> FastAPI:
    """Create the local adapter app from process environment.

    This keeps `uvicorn backend.runtime.adapter_app:app` usable for local AIO
    simulations without coupling Host runtime settings to adapter startup.
    """

    workspace_root = os.environ.get("RUNTIME_ADAPTER_WORKSPACE_ROOT") or None
    return create_runtime_adapter_app(
        workspace_root=workspace_root,
        enable_local_execution=_env_flag("RUNTIME_ADAPTER_ENABLE_LOCAL_EXECUTION"),
        cdp_url=os.environ.get("RUNTIME_ADAPTER_CDP_URL") or None,
        browser_view_url=os.environ.get("RUNTIME_ADAPTER_BROWSER_VIEW_URL") or None,
        adapter_token=os.environ.get("RUNTIME_ADAPTER_TOKEN") or None,
        downloads_dir=os.environ.get("RUNTIME_ADAPTER_DOWNLOADS_DIR") or None,
        adapter_version=os.environ.get("RUNTIME_ADAPTER_VERSION") or None,
        enable_browser_launch=_env_flag("RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH"),
        browser_executable=os.environ.get("RUNTIME_ADAPTER_BROWSER_EXECUTABLE") or None,
        browser_debug_port=int(os.environ.get("RUNTIME_ADAPTER_BROWSER_DEBUG_PORT") or DEFAULT_BROWSER_DEBUG_PORT),
        browser_cdp_public_url=os.environ.get("RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_URL") or None,
        browser_cdp_public_base_url=os.environ.get("RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL") or None,
        browser_launch_timeout_seconds=int(
            os.environ.get("RUNTIME_ADAPTER_BROWSER_LAUNCH_TIMEOUT_SECONDS")
            or DEFAULT_BROWSER_LAUNCH_TIMEOUT_SECONDS
        ),
    )


def runtime_adapter_self_check(
    *,
    workspace_root: str | Path | None = None,
    enable_local_execution: bool = False,
    cdp_url: str | None = None,
    browser_view_url: str | None = None,
    adapter_token: str | None = None,
    downloads_dir: str | None = None,
    adapter_version: str | None = None,
    enable_browser_launch: bool = False,
    browser_executable: str | None = None,
    browser_debug_port: int = DEFAULT_BROWSER_DEBUG_PORT,
    browser_cdp_public_url: str | None = None,
    browser_cdp_public_base_url: str | None = None,
    browser_launch_timeout_seconds: int = DEFAULT_BROWSER_LAUNCH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    state = LocalRuntimeAdapterState(
        workspace_root=Path(workspace_root or Path.cwd()),
        enable_local_execution=enable_local_execution,
        cdp_url=cdp_url,
        browser_view_url=browser_view_url,
        adapter_token=adapter_token,
        downloads_dir=downloads_dir,
        adapter_version=adapter_version,
        enable_browser_launch=enable_browser_launch,
        browser_executable=browser_executable,
        browser_debug_port=browser_debug_port,
        browser_cdp_public_url=browser_cdp_public_url,
        browser_cdp_public_base_url=browser_cdp_public_base_url,
        browser_launch_timeout_seconds=browser_launch_timeout_seconds,
    )
    return state.health_payload()


def runtime_adapter_self_check_from_env() -> dict[str, Any]:
    workspace_root = os.environ.get("RUNTIME_ADAPTER_WORKSPACE_ROOT") or None
    return runtime_adapter_self_check(
        workspace_root=workspace_root,
        enable_local_execution=_env_flag("RUNTIME_ADAPTER_ENABLE_LOCAL_EXECUTION"),
        cdp_url=os.environ.get("RUNTIME_ADAPTER_CDP_URL") or None,
        browser_view_url=os.environ.get("RUNTIME_ADAPTER_BROWSER_VIEW_URL") or None,
        adapter_token=os.environ.get("RUNTIME_ADAPTER_TOKEN") or None,
        downloads_dir=os.environ.get("RUNTIME_ADAPTER_DOWNLOADS_DIR") or None,
        adapter_version=os.environ.get("RUNTIME_ADAPTER_VERSION") or None,
        enable_browser_launch=_env_flag("RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH"),
        browser_executable=os.environ.get("RUNTIME_ADAPTER_BROWSER_EXECUTABLE") or None,
        browser_debug_port=int(os.environ.get("RUNTIME_ADAPTER_BROWSER_DEBUG_PORT") or DEFAULT_BROWSER_DEBUG_PORT),
        browser_cdp_public_url=os.environ.get("RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_URL") or None,
        browser_cdp_public_base_url=os.environ.get("RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL") or None,
        browser_launch_timeout_seconds=int(
            os.environ.get("RUNTIME_ADAPTER_BROWSER_LAUNCH_TIMEOUT_SECONDS")
            or DEFAULT_BROWSER_LAUNCH_TIMEOUT_SECONDS
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runtime adapter utilities.")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Print sanitized runtime adapter health diagnostics from environment.",
    )
    args = parser.parse_args(argv)
    if not args.self_check:
        parser.error("Only --self-check is supported")

    diagnostic = runtime_adapter_self_check_from_env()
    print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
    return 0 if diagnostic.get("status") == "ok" else 1


app = create_runtime_adapter_app_from_env()


__all__ = [
    "app",
    "create_runtime_adapter_app",
    "create_runtime_adapter_app_from_env",
    "LocalBrowserController",
    "runtime_adapter_self_check",
    "runtime_adapter_self_check_from_env",
]


if __name__ == "__main__":
    raise SystemExit(main())
