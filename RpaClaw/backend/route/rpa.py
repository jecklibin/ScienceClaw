import json
import logging
import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import websockets
from websockets.exceptions import ConnectionClosed
import httpx
from fastapi.responses import Response as FastAPIResponse

from backend.rpa.manager import rpa_manager
from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.executor import ScriptExecutor
from backend.rpa.skill_exporter import SkillExporter
from backend.rpa.assistant import RPAAssistant, RPAReActAgent, _active_agents
from backend.rpa.cdp_connector import get_cdp_connector
from backend.rpa.screencast import SessionScreencastController
from backend.user.dependencies import get_current_user, User
from backend.config import settings
from backend.deepagent.engine import get_llm_model
from backend.storage import get_repository
from backend.credential.vault import inject_credentials

logger = logging.getLogger(__name__)

RPA_TEST_TIMEOUT_S = 180.0
RPA_PAGE_TIMEOUT_MS = 60000
AI_COMMAND_NAVIGATION_SUPPRESS_MS = 2000
AI_COMMAND_NAVIGATION_SETTLE_MS = 500
AI_COMMAND_DOMCONTENTLOADED_TIMEOUT_MS = 5000
AI_COMMAND_NETWORKIDLE_TIMEOUT_MS = 2000
AI_COMMAND_RENDER_SETTLE_TIMEOUT_MS = 1500

router = APIRouter(tags=["RPA"])
generator = PlaywrightGenerator()
executor = ScriptExecutor()
exporter = SkillExporter()
assistant = RPAAssistant()


async def _wait_for_ai_command_page_stability(page) -> None:
    """Give AI-triggered navigations a chance to settle before resuming recording.

    A short initial delay lets any navigation triggered by the AI code start,
    so that ``wait_for_load_state`` can detect the loading state instead of
    returning immediately on the already-loaded old page.
    """
    # Give the browser a moment to initiate any navigation triggered by the
    # AI-generated code.  Without this, ``wait_for_load_state`` sees the old
    # page still in "complete" state and returns instantly.
    try:
        await page.wait_for_timeout(AI_COMMAND_NAVIGATION_SETTLE_MS)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=AI_COMMAND_DOMCONTENTLOADED_TIMEOUT_MS)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=AI_COMMAND_NETWORKIDLE_TIMEOUT_MS)
    except Exception:
        pass
    try:
        await page.wait_for_function(
            """
            () => new Promise((resolve) => {
              requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)));
            })
            """,
            timeout=AI_COMMAND_RENDER_SETTLE_TIMEOUT_MS,
        )
    except Exception:
        pass
    try:
        await page.wait_for_timeout(300)
    except Exception:
        pass


async def _capture_ai_command_page_context(page) -> str:
    """Capture a richer snapshot of the current page so extraction sees the latest DOM state."""
    if not page:
        return ""

    try:
        snapshot = await page.evaluate(
            """
            () => {
              const bodyText = (document.body?.innerText || "").trim();
              const interactiveValues = Array.from(
                document.querySelectorAll("input, textarea, select, [contenteditable='true']")
              )
                .map((node) => {
                  const el = node;
                  const tag = (el.tagName || "").toLowerCase();
                  const type = (el.getAttribute?.("type") || "").toLowerCase();
                  const name = el.getAttribute?.("name") || "";
                  const id = el.id || "";
                  const placeholder = el.getAttribute?.("placeholder") || "";
                  const aria = el.getAttribute?.("aria-label") || "";
                  let value = "";
                  if (tag === "select") {
                    value = el.value || "";
                  } else if (tag === "textarea" || tag === "input") {
                    value = el.value || "";
                  } else {
                    value = el.textContent || "";
                  }
                  return [tag, type, name, id, placeholder, aria, value]
                    .filter(Boolean)
                    .join(" | ");
                })
                .filter(Boolean)
                .slice(0, 80);
              return {
                url: window.location.href,
                title: document.title || "",
                bodyText,
                interactiveValues,
              };
            }
            """
        )
        if isinstance(snapshot, dict):
            parts = []
            url = str(snapshot.get("url") or "").strip()
            title = str(snapshot.get("title") or "").strip()
            body_text = str(snapshot.get("bodyText") or "").strip()
            interactive_values = snapshot.get("interactiveValues") or []
            if url:
                parts.append(f"URL: {url}")
            if title:
                parts.append(f"Title: {title}")
            if body_text:
                parts.append(f"Body Text:\n{body_text}")
            if interactive_values:
                values_text = "\n".join(f"- {str(item)}" for item in interactive_values if str(item).strip())
                if values_text:
                    parts.append(f"Interactive Values:\n{values_text}")
            context = "\n\n".join(parts).strip()
            if context:
                return context[:50000]
    except Exception as e:
        logger.warning(f"Failed to capture structured page context: {e}")

    try:
        fallback = await page.inner_text("body")
        return fallback[:50000] if len(fallback) > 50000 else fallback
    except Exception as e:
        logger.warning(f"Failed to capture fallback page context: {e}")
        return ""


async def _resolve_ai_command_page(session_id: str, fallback_page):
    """Prefer the latest active page after AI operations, including popup/new-tab transitions."""
    latest_page = fallback_page
    # Quick polling: the initial asyncio.sleep(0.2) before this call already
    # gave pending tab-registration tasks a chance to run, so a few fast
    # checks (50 ms each) are enough.  For same-tab navigation the loop
    # always exhausts, so we keep the total short to avoid visible latency.
    for _ in range(10):
        current_page = rpa_manager.get_page(session_id) or latest_page
        if current_page is not latest_page:
            latest_page = current_page
            break
        await asyncio.sleep(0.05)
    return latest_page


def _strip_code_fences(text: str) -> str:
    import re as _re
    cleaned = (text or "").strip()
    cleaned = _re.sub(r"^```(?:json|python)?\s*\n?", "", cleaned)
    cleaned = _re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


def _parse_ai_command_plan(raw_text: str) -> Dict[str, Any]:
    cleaned = _strip_code_fences(raw_text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("AI command plan must be a JSON object")
    operation = payload.get("operation")
    data = payload.get("data")
    if not isinstance(operation, dict):
        operation = {}
    if not isinstance(data, dict):
        data = {}
    payload["operation"] = operation
    payload["data"] = data
    return payload


async def _invoke_ai_command_model(messages: list[tuple[str, str]], model_config: Dict[str, Any]) -> str:
    model = get_llm_model(config=model_config, streaming=False)
    response = await model.ainvoke(messages)
    return response.content if hasattr(response, "content") else str(response)


class StartSessionRequest(BaseModel):
    sandbox_session_id: str


class GenerateRequest(BaseModel):
    params: Dict[str, Any] = {}


class SaveSkillRequest(BaseModel):
    skill_name: str
    description: str
    params: Dict[str, Any] = {}


class ChatRequest(BaseModel):
    message: str
    mode: str = "chat"


class ConfirmRequest(BaseModel):
    approved: bool


class AICommandRequest(BaseModel):
    prompt: str
    page_context: str = ""
    mode: str = "data"  # "execute" or "data"


class SessionAICommandRequest(BaseModel):
    prompt: str
    output_variable: str = ""
    ai_mode: str = "auto"  # auto | execute | data


class NavigateRequest(BaseModel):
    url: str


class PromoteLocatorRequest(BaseModel):
    candidate_index: int


async def _get_ws_user(websocket: WebSocket) -> User | None:
    """Resolve the current user for a WebSocket request.

    Browser WebSocket APIs cannot attach custom Authorization headers in the
    same way axios does, so we accept a bearer token via query param as a
    fallback and keep the existing local-mode shortcut.
    """
    if settings.storage_backend == "local":
        return User(id="local_admin", username="admin", role="admin")

    if getattr(settings, "auth_provider", "local") == "none":
        return User(id="anonymous", username="Anonymous", role="user")

    session_id = (
        websocket.query_params.get("token")
        or websocket.cookies.get(settings.session_cookie)
    )
    if not session_id:
        return None

    repo = get_repository("user_sessions")
    session_doc = await repo.find_one({"_id": session_id})
    if not session_doc:
        return None

    import time
    if session_doc.get("expires_at", 0) < time.time():
        await repo.delete_one({"_id": session_id})
        return None

    return User(
        id=str(session_doc["user_id"]),
        username=session_doc["username"],
        role=session_doc.get("role", "user"),
    )


async def _get_http_user(request: Request) -> User | None:
    """Resolve the current user for normal HTTP requests.

    This mirrors websocket auth so iframe-based noVNC pages can use either
    the session cookie or a `token` query param.
    """
    if settings.storage_backend == "local":
        return User(id="local_admin", username="admin", role="admin")

    if getattr(settings, "auth_provider", "local") == "none":
        return User(id="anonymous", username="Anonymous", role="user")

    session_id = (
        request.query_params.get("token")
        or request.cookies.get(settings.session_cookie)
    )
    if not session_id:
        return None

    repo = get_repository("user_sessions")
    session_doc = await repo.find_one({"_id": session_id})
    if not session_doc:
        return None

    import time
    if session_doc.get("expires_at", 0) < time.time():
        await repo.delete_one({"_id": session_id})
        return None

    return User(
        id=str(session_doc["user_id"]),
        username=session_doc["username"],
        role=session_doc.get("role", "user"),
    )


def _extract_request_auth_token(request: Request | None) -> str:
    """Extract the auth session token from either Authorization header or session cookie."""
    if request is None:
        return ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return (request.cookies.get(settings.session_cookie) or "").strip()


def _build_ai_command_url_for_request(request: Request | None, *, is_local: bool) -> str:
    """Build a runtime-accessible AI command URL for generated scripts."""
    env_override = (os.environ.get("RPA_AI_COMMAND_URL") or "").strip()
    if env_override:
        return env_override.rstrip("/")
    if request is None:
        return generator._get_ai_command_url(is_local)

    base = str(request.base_url).rstrip("/")
    parsed = urlparse(base)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if not is_local and host in {"127.0.0.1", "localhost"}:
        host = "host.docker.internal"
    netloc = host if port is None else f"{host}:{port}"
    return parsed._replace(
        scheme=scheme,
        netloc=netloc,
        path="/api/v1/rpa/ai-command",
        params="",
        query="",
        fragment="",
    ).geturl()


def _get_sandbox_vnc_ws_url() -> str:
    """Return the configured upstream sandbox VNC WebSocket URL."""
    return settings.sandbox_vnc_ws_url.rstrip("/")


def _get_sandbox_vnc_http_url(path: str) -> str:
    sandbox_base = settings.sandbox_base_url.rstrip("/")
    return f"{sandbox_base}/vnc/{path.lstrip('/')}"


def _get_sandbox_novnc_ws_url() -> str:
    sandbox_base = settings.sandbox_base_url.rstrip("/")
    parsed = urlparse(sandbox_base)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    return parsed._replace(scheme=ws_scheme, path="/websockify", query="", fragment="").geturl()


def _get_sandbox_proxy_headers() -> list[tuple[str, str]] | None:
    """Parse optional proxy request headers from env.

    Expected format:
      SANDBOX_PROXY_HEADERS={"Authorization":"Bearer xxx","X-API-Key":"yyy"}
    """
    raw = (getattr(settings, "sandbox_proxy_headers", "") or "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid SANDBOX_PROXY_HEADERS JSON; ignoring proxy headers")
        return None

    if not isinstance(parsed, dict):
        logger.warning("SANDBOX_PROXY_HEADERS must be a JSON object; ignoring proxy headers")
        return None

    headers: list[tuple[str, str]] = []
    for key, value in parsed.items():
        if value is None:
            continue
        headers.append((str(key), str(value)))
    return headers or None


def _get_sandbox_proxy_headers_dict() -> dict[str, str]:
    headers = _get_sandbox_proxy_headers() or []
    return {key: value for key, value in headers}


def _filter_proxy_query(params: dict[str, str] | Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(params).items() if k != "token"}


def _rewrite_vnc_html(html: str, session_id: str) -> str:
    proxy_prefix = f"/api/v1/rpa/vnc/page/{session_id}/"
    rewritten = html.replace('href="/vnc/', f'href="{proxy_prefix}')
    rewritten = rewritten.replace('src="/vnc/', f'src="{proxy_prefix}')
    rewritten = rewritten.replace('action="/vnc/', f'action="{proxy_prefix}')
    rewritten = rewritten.replace('url: "/vnc/', f'url: "{proxy_prefix}')
    rewritten = rewritten.replace("url: '/vnc/", f"url: '{proxy_prefix}")
    rewritten = rewritten.replace('path: "websockify"', f'path: "{proxy_prefix}websockify"')
    rewritten = rewritten.replace("path: 'websockify'", f"path: '{proxy_prefix}websockify'")
    rewritten = rewritten.replace('path = "websockify"', f'path = "{proxy_prefix}websockify"')
    rewritten = rewritten.replace("path = 'websockify'", f"path = '{proxy_prefix}websockify'")
    if "<head>" in rewritten:
        rewritten = rewritten.replace("<head>", f'<head><base href="{proxy_prefix}">', 1)
    return rewritten


async def _resolve_user_model_config(user_id: str) -> dict | None:
    """Resolve the user's model config for the RPA assistant.

    Priority: user's own models → system models → env defaults (None).
    """
    # Try user's own active model first, then system models
    docs = await get_repository("models").find_many(
        {"$or": [{"user_id": user_id}, {"is_system": True}], "is_active": True, "api_key": {"$nin": ["", None]}},
        sort=[("is_system", 1), ("updated_at", -1)],  # user models first
        limit=1,
    )
    doc = docs[0] if docs else None
    if doc:
        return {
            "model_name": doc.get("model_name"),
            "base_url": doc.get("base_url"),
            "api_key": doc.get("api_key"),
            "context_window": doc.get("context_window"),
            "provider": doc.get("provider", ""),
        }
    # Fall back to env defaults
    if (getattr(settings, "model_ds_api_key", None) or "").strip():
        return None  # get_llm_model(config=None) uses env defaults
    return None


@router.post("/session/start")
async def start_rpa_session(
    request: StartSessionRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        session = await rpa_manager.create_session(
            user_id=str(current_user.id),
            sandbox_session_id=request.sandbox_session_id,
        )
        return {"status": "success", "session": session}
    except Exception as e:
        logger.error(f"Failed to start RPA session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}")
async def get_rpa_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"status": "success", "session": session}


@router.get("/session/{session_id}/tabs")
async def list_rpa_tabs(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {
        "status": "success",
        "tabs": rpa_manager.list_tabs(session_id),
        "active_tab_id": session.active_tab_id,
    }


@router.post("/session/{session_id}/tabs/{tab_id}/activate")
async def activate_rpa_tab(
    session_id: str,
    tab_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        result = await rpa_manager.activate_tab(session_id, tab_id, source="user")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "result": result,
        "tabs": rpa_manager.list_tabs(session_id),
        "active_tab_id": session.active_tab_id,
    }


@router.post("/session/{session_id}/navigate")
async def navigate_rpa_session(
    session_id: str,
    request: NavigateRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        result = await rpa_manager.navigate_active_tab(session_id, request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "result": result,
        "tabs": rpa_manager.list_tabs(session_id),
        "active_tab_id": session.active_tab_id,
    }


@router.post("/session/{session_id}/stop")
async def stop_rpa_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    await rpa_manager.stop_session(session_id)
    return {"status": "success", "session": session}


@router.delete("/session/{session_id}/step/{step_index}")
async def delete_step(
    session_id: str,
    step_index: int,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    success = await rpa_manager.delete_step(session_id, step_index)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid step index")
    return {"status": "success"}


@router.post("/session/{session_id}/step/{step_index}/locator")
async def promote_step_locator(
    session_id: str,
    step_index: int,
    request: PromoteLocatorRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        step = await rpa_manager.select_step_locator_candidate(
            session_id,
            step_index,
            request.candidate_index,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"status": "success", "step": step}


@router.post("/session/{session_id}/generate")
async def generate_script(
    session_id: str,
    request: GenerateRequest = GenerateRequest(),
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"))
    return {"status": "success", "script": script}


@router.post("/session/{session_id}/test")
async def test_script(
    session_id: str,
    body: GenerateRequest = GenerateRequest(),
    current_user: User = Depends(get_current_user),
    http_request: Request = None,
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    is_local_mode = settings.storage_backend == "local"
    ai_command_url = _build_ai_command_url_for_request(http_request, is_local=is_local_mode)
    script = generator.generate_script(
        steps,
        body.params,
        is_local=is_local_mode,
        test_mode=True,
        ai_command_url=ai_command_url,
    )

    logs = []
    browser = await get_cdp_connector().get_browser(
        session_id=session.sandbox_session_id,
        user_id=str(current_user.id),
    )

    downloads_dir = str(Path(settings.workspace_dir) / "rpa_downloads" / session_id)
    connector = get_cdp_connector()
    pw_loop_runner = getattr(connector, "run_in_pw_loop", None)

    # 本地模式：通过 pw_loop_runner 确保 Playwright 操作在正确的事件循环里执行
    _ai_token = _extract_request_auth_token(http_request)
    if is_local_mode:
        test_kwargs: Dict[str, Any] = {"_downloads_dir": downloads_dir, "_ai_token": _ai_token}
        if body.params:
            test_kwargs.update(await inject_credentials(str(current_user.id), body.params, {}))
        result = await executor.execute(
            browser,
            script,
            on_log=lambda msg: logs.append(msg),
            session_id=session_id,
            page_registry=rpa_manager._pages,
            session_manager=rpa_manager,
            kwargs=test_kwargs,
            downloads_dir=downloads_dir,
            pw_loop_runner=pw_loop_runner,
        )
    else:
        # Docker 模式：使用原有逻辑
        docker_kwargs: Dict[str, Any] = {"_ai_token": _ai_token}
        if body.params:
            docker_kwargs = await inject_credentials(
                str(current_user.id), body.params, {}
            )
        result = await executor.execute(
            browser,
            script,
            on_log=lambda msg: logs.append(msg),
            session_id=session_id,
            page_registry=rpa_manager._pages,
            session_manager=rpa_manager,
            kwargs=docker_kwargs,
            downloads_dir=downloads_dir,
        )

    # Extract failed step candidates for locator retry
    deduped_failed_index = result.get("failed_step_index")
    failed_step_index = None
    failed_step_candidates = []
    if deduped_failed_index is not None:
        deduped = generator._deduplicate_steps(steps)
        deduped = generator._infer_missing_tab_transitions(deduped)
        deduped = generator._normalize_step_signals(deduped)
        if 0 <= deduped_failed_index < len(deduped):
            failed_step = deduped[deduped_failed_index]
            # Map deduped index back to original steps index via step id
            failed_step_id = failed_step.get("id")
            if failed_step_id:
                for orig_i, orig_step in enumerate(steps):
                    if orig_step.get("id") == failed_step_id:
                        failed_step_index = orig_i
                        break
            if failed_step_index is None:
                failed_step_index = min(deduped_failed_index, len(steps) - 1)
            candidates = failed_step.get("locator_candidates", [])
            filtered = []
            for orig_idx, c in enumerate(candidates):
                if not c.get("selected"):
                    entry = dict(c)
                    entry["original_index"] = orig_idx
                    filtered.append(entry)
            failed_step_candidates = sorted(
                filtered,
                key=lambda c: (
                    0 if c.get("strict_match_count") == 1 else 1,
                    c.get("score", 999),
                ),
            )

    return {
        "status": "success" if result.get("success") else "failed",
        "result": result,
        "logs": logs,
        "script": script,
        "failed_step_index": failed_step_index,
        "failed_step_candidates": failed_step_candidates,
    }


@router.post("/session/{session_id}/save")
async def save_skill(
    session_id: str,
    request: SaveSkillRequest,
    current_user: User = Depends(get_current_user),
    http_request: Request = None,
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    is_local_mode = settings.storage_backend == "local"
    ai_command_url = _build_ai_command_url_for_request(http_request, is_local=is_local_mode)
    script = generator.generate_script(
        steps, request.params, is_local=is_local_mode,
        ai_command_url=ai_command_url,
    )

    skill_name = await exporter.export_skill(
        user_id=str(current_user.id),
        skill_name=request.skill_name,
        description=request.description,
        script=script,
        params=request.params,
        ai_command_url=ai_command_url,
    )

    session.status = "saved"
    return {"status": "success", "skill_name": skill_name}


@router.post("/session/{session_id}/chat")
async def chat_with_assistant(
    session_id: str,
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Resolve user's model config
    model_config = await _resolve_user_model_config(str(current_user.id))

    # Get the page object for this session
    page = rpa_manager.get_page(session_id)
    if not page:
        raise HTTPException(status_code=400, detail="No active page for this session")

    steps = [step.model_dump() for step in session.steps]

    async def event_generator():
        try:
            rpa_manager.pause_recording(session_id)

            if request.mode == "react":
                # Reuse existing agent for this session to preserve history across turns
                agent = _active_agents.get(session_id)
                if agent is None:
                    agent = RPAReActAgent()
                    _active_agents[session_id] = agent
                try:
                    async for event in agent.run(
                        session_id=session_id,
                        page=page,
                        goal=request.message,
                        existing_steps=steps,
                        model_config=model_config,
                        page_provider=lambda: rpa_manager.get_page(session_id),
                    ):
                        evt_type = event.get("event", "message")
                        evt_data = event.get("data", {})
                        if evt_type == "agent_step_done" and evt_data.get("step"):
                            await rpa_manager.add_step(session_id, evt_data["step"])
                        if evt_type == "agent_aborted":
                            _active_agents.pop(session_id, None)
                        yield {
                            "event": evt_type,
                            "data": json.dumps(evt_data, ensure_ascii=False),
                        }
                except Exception:
                    _active_agents.pop(session_id, None)
                    raise
            else:
                async for event in assistant.chat(
                    session_id=session_id,
                    page=page,
                    message=request.message,
                    steps=steps,
                    model_config=model_config,
                    page_provider=lambda: rpa_manager.get_page(session_id),
                ):
                    evt_type = event.get("event", "message")
                    evt_data = event.get("data", {})
                    if evt_type == "result" and evt_data.get("success") and evt_data.get("step"):
                        await rpa_manager.add_step(session_id, evt_data["step"])
                    yield {
                        "event": evt_type,
                        "data": json.dumps(evt_data, ensure_ascii=False),
                    }
        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield {"event": "error", "data": json.dumps({"message": str(e)}, ensure_ascii=False)}
            yield {"event": "done", "data": "{}"}
        finally:
            rpa_manager.resume_recording(session_id)

    return EventSourceResponse(event_generator())


@router.post("/session/{session_id}/agent/confirm")
async def agent_confirm(
    session_id: str,
    body: ConfirmRequest,
    current_user: User = Depends(get_current_user),
):
    agent = _active_agents.get(session_id)
    if agent:
        agent.resolve_confirm(body.approved)
    return {"ok": True}


@router.post("/session/{session_id}/agent/abort")
async def agent_abort(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    agent = _active_agents.get(session_id)
    if agent:
        agent.abort()
    return {"ok": True}


@router.post("/ai-command")
async def execute_ai_command(
    request: AICommandRequest,
    current_user: User = Depends(get_current_user),
):
    """Call LLM with a prompt — used by generated scripts at runtime.

    mode='execute': AI generates Playwright Python code (async, using `page` variable).
    mode='data': AI returns plain text data.
    """
    model_config = await _resolve_user_model_config(str(current_user.id))
    model = get_llm_model(config=model_config, streaming=False)

    system_prompt = ""
    if request.mode == "execute":
        system_prompt = (
            "你是一个 Playwright 自动化专家。根据用户的指令和页面内容，生成简短的 Playwright Python 代码（async）。\n"
            "规则：\n"
            "- 代码中使用 `page` 变量来操作页面\n"
            "- 只输出纯 Python 代码，绝对不要使用 markdown 代码块标记（不要写 ```python 或 ```）\n"
            "- 不要包含注释说明或 import 语句\n"
            "- 每行一条 await 语句\n"
            "- 不要使用 try/except\n"
        )
        if request.page_context:
            system_prompt += f"\n当前页面文本内容：\n\n{request.page_context[:50000]}"
    else:
        if request.page_context:
            system_prompt = f"以下是当前页面的文本内容：\n\n{request.page_context[:50000]}"

    messages = []
    if system_prompt:
        messages.append(("system", system_prompt))
    messages.append(("human", request.prompt))

    response = await model.ainvoke(messages)
    ai_response = response.content if hasattr(response, "content") else str(response)

    # Strip markdown code fences for execute mode
    if request.mode == "execute":
        import re as _re
        cleaned = ai_response.strip()
        cleaned = _re.sub(r"^```(?:python)?\s*\n?", "", cleaned)
        cleaned = _re.sub(r"\n?```\s*$", "", cleaned)
        ai_response = cleaned.strip()

    return {"data": {"response": ai_response}}


@router.post("/session/{session_id}/ai-command")
async def session_ai_command(
    session_id: str,
    request: SessionAICommandRequest,
    current_user: User = Depends(get_current_user),
):
    """Insert a unified AI command step that can perform operations, extract data, or both."""
    recording_paused = False
    try:
        session = await rpa_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != str(current_user.id):
            raise HTTPException(status_code=403, detail="Not authorized")

        page = rpa_manager.get_page(session_id)
        legacy_mode = (request.ai_mode or "auto").strip().lower()
        if legacy_mode not in {"auto", "execute", "data"}:
            legacy_mode = "auto"

        if legacy_mode in {"auto", "execute"} and not page:
            logger.warning(
                "RPA session ai-command execute requested without active page session=%s user=%s",
                session_id,
                current_user.username,
            )
            raise HTTPException(status_code=400, detail="No active page for this session")
        if legacy_mode in {"auto", "execute"}:
            rpa_manager.pause_recording(session_id)
            recording_paused = True

        # Capture page context
        page_context = await _capture_ai_command_page_context(page) if page else ""

        model_config = await _resolve_user_model_config(str(current_user.id))

        logger.info(
            "RPA session ai-command start session=%s user=%s mode=%s prompt_len=%s has_page=%s",
            session_id,
            current_user.username,
            legacy_mode,
            len(request.prompt or ""),
            bool(page),
        )

        output_variable = (request.output_variable or "").strip()
        plan_system_prompt = (
            "你是一个网页自动化与信息提取规划器。"
            "请把用户请求拆解成两个可选部分：operation 和 data。"
            "operation 用于改变页面状态或执行浏览器操作；data 用于说明最终要提取什么信息。"
            "严格输出 JSON 对象，不要输出 markdown，不要输出解释。\n"
            "JSON schema:\n"
            "{\n"
            '  "operation": {"needed": boolean, "description": string, "code": string},\n'
            '  "data": {"needed": boolean, "description": string, "extract_prompt": string, "format": "text|json", "output_variable": string}\n'
            "}\n"
            "规则：\n"
            "- operation.code 必须是简短的 Playwright Python async 代码，只能使用 page 变量\n"
            "- 如果不需要操作，operation.needed=false 且 code 为空字符串\n"
            "- 如果不需要数据，data.needed=false 且 extract_prompt 为空字符串\n"
            "- 如果用户只是想读取当前页信息，不要编造操作\n"
            "- 如果用户只是想执行操作，不要编造数据\n"
            "- 如果用户既要操作又要结果，先把操作写进 operation，再把最终提取目标写进 data\n"
            "- 不要使用 markdown 代码块\n"
        )
        if legacy_mode == "execute":
            plan_system_prompt += "- 当前请求是兼容 execute 模式：必须只输出 operation，data.needed=false\n"
        elif legacy_mode == "data":
            plan_system_prompt += "- 当前请求是兼容 data 模式：operation.needed=false，只输出 data\n"
        if page_context:
            plan_system_prompt += f"\n当前页面文本内容：\n\n{page_context[:50000]}"

        ai_response = await _invoke_ai_command_model(
            [("system", plan_system_prompt), ("human", request.prompt)],
            model_config,
        )
        plan = _parse_ai_command_plan(ai_response)
        operation_plan = plan.get("operation", {}) or {}
        data_plan = plan.get("data", {}) or {}

        if legacy_mode == "execute":
            operation_plan["needed"] = True
            data_plan = {"needed": False, "description": "", "extract_prompt": "", "format": "empty", "output_variable": ""}
        elif legacy_mode == "data":
            operation_plan = {"needed": False, "description": "", "code": ""}
            data_plan["needed"] = True

        operation_needed = bool(operation_plan.get("needed")) and bool((operation_plan.get("code") or "").strip())
        operation_code = _strip_code_fences(operation_plan.get("code", "")) if operation_needed else ""
        operation_summary = (operation_plan.get("description") or "").strip()

        data_needed = bool(data_plan.get("needed"))
        data_prompt = (data_plan.get("extract_prompt") or request.prompt or "").strip() if data_needed else ""
        data_summary = (data_plan.get("description") or "").strip()
        data_format = (data_plan.get("format") or "text").strip().lower() or "text"
        resolved_output_variable = output_variable or (data_plan.get("output_variable") or "").strip()

        execute_error = None
        effective_page = page
        if operation_needed and page:
            try:
                await page.evaluate("window.__rpa_paused = true")
            except Exception:
                pass
            try:
                fn_src = "async def __ai_fn():\n" + "\n".join("    " + line for line in operation_code.split("\n"))
                ns = {"page": page}
                exec(fn_src, ns)
                await ns["__ai_fn"]()
            except Exception:
                logger.exception(
                    "Failed to execute AI-generated code session=%s user=%s mode=%s",
                    session_id,
                    current_user.username,
                    legacy_mode,
                )
                import traceback as _traceback
                execute_error = _traceback.format_exc()
            finally:
                rpa_manager.suppress_navigation_events(
                    session_id,
                    session.active_tab_id,
                    duration_ms=AI_COMMAND_NAVIGATION_SUPPRESS_MS,
                )
                # Give the event loop a chance to process pending tab
                # registrations (e.g. from context.on("page") when AI code
                # opens a new tab via asyncio.create_task).
                await asyncio.sleep(0.2)
                # Resolve the effective page FIRST, then wait for stability
                # on that page — not on the original one.
                effective_page = await _resolve_ai_command_page(session_id, page)
                await _wait_for_ai_command_page_stability(effective_page)
                # Re-resolve: the stability wait may have allowed another
                # tab transition to complete.
                final_page = await _resolve_ai_command_page(session_id, effective_page)
                if final_page is not effective_page:
                    await _wait_for_ai_command_page_stability(final_page)
                    effective_page = final_page
                try:
                    await page.evaluate("window.__rpa_paused = false")
                except Exception:
                    pass
        else:
            effective_page = page

        data_value = ""
        if data_needed:
            data_page_context = page_context
            if effective_page:
                data_page_context = await _capture_ai_command_page_context(effective_page)
            data_system_prompt = (
                "你是一个网页信息提取助手。"
                "根据用户指定的提取目标和当前页面内容，直接返回最终数据。"
                "不要返回代码，不要解释。"
            )
            if data_format == "json":
                data_system_prompt += " 输出必须是合法 JSON。"
            if data_page_context:
                data_system_prompt += f"\n当前页面文本内容：\n\n{data_page_context}"
            data_value = _strip_code_fences(
                await _invoke_ai_command_model(
                    [("system", data_system_prompt), ("human", data_prompt)],
                    model_config,
                )
            )

        if operation_needed and data_needed:
            ai_result_mode = "operation_and_data"
        elif operation_needed:
            ai_result_mode = "operation_only"
        else:
            ai_result_mode = "data_only"

        # Add step to session
        step_data = {
            "action": "ai_command",
            "prompt": request.prompt,
            "value": data_value or operation_code,
            "output_variable": resolved_output_variable or None,
            "include_page_context": True,  # always capture page context for AI commands
            "ai_mode": legacy_mode,
            "ai_result_mode": ai_result_mode,
            "operation_code": operation_code or None,
            "operation_summary": operation_summary or None,
            "data_prompt": data_prompt or None,
            "data_value": data_value or None,
            "data_summary": data_summary or None,
            "data_format": data_format if data_needed else "empty",
            "source": "record",
            "description": f"AI 命令: {request.prompt[:40]}",
        }
        step = await rpa_manager.add_step(session_id, step_data)

        result = {
            "status": "success",
            "step": step.model_dump(),
            "ai_response": ai_response,
            "ai_mode": legacy_mode,
            "ai_result_mode": ai_result_mode,
            "operation_code": operation_code,
            "data_value": data_value,
        }
        if execute_error:
            result["execute_error"] = execute_error
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "RPA session ai-command failed session=%s user=%s mode=%s",
            session_id,
            current_user.username,
            request.ai_mode,
        )
        raise
    finally:
        if recording_paused:
            rpa_manager.resume_recording(session_id)


@router.websocket("/session/{session_id}/steps")
async def steps_stream(websocket: WebSocket, session_id: str):
    """Stream real-time step updates to frontend."""
    await websocket.accept()

    session = await rpa_manager.get_session(session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found")
        return

    rpa_manager.register_ws(session_id, websocket)

    try:
        for step in session.steps:
            await websocket.send_json({"type": "step", "data": step.model_dump()})

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        rpa_manager.unregister_ws(session_id, websocket)


@router.websocket("/screencast/{session_id}")
async def rpa_screencast(websocket: WebSocket, session_id: str):
    """Session-scoped CDP screencast with active-tab switching."""
    logger.info(
        "Screencast websocket connect session=%s client=%s query=%s",
        session_id,
        getattr(websocket.client, "host", None),
        dict(websocket.query_params),
    )
    user = await _get_ws_user(websocket)
    await websocket.accept()
    if not user:
        logger.warning("Screencast websocket unauthenticated session=%s", session_id)
        await websocket.close(code=1008, reason="Not authenticated")
        return

    session = await rpa_manager.get_session(session_id)
    if not session:
        logger.warning("Screencast websocket missing session=%s user=%s", session_id, user.username)
        await websocket.close(code=1008, reason="Session not found")
        return
    if session.user_id != str(user.id):
        logger.warning(
            "Screencast websocket forbidden session=%s request_user=%s owner=%s",
            session_id,
            user.id,
            session.user_id,
        )
        await websocket.close(code=1008, reason="Not authorized")
        return

    active_page = rpa_manager.get_page(session_id)
    if active_page:
        logger.info(
            "Screencast websocket ready session=%s user=%s page_id=%s url=%s",
            session_id,
            user.username,
            id(active_page),
            getattr(active_page, "url", ""),
        )
    else:
        logger.info(
            "Screencast websocket waiting for active page session=%s user=%s",
            session_id,
            user.username,
        )

    screencast = SessionScreencastController(
        page_provider=lambda: rpa_manager.get_page(session_id),
        tabs_provider=lambda: rpa_manager.list_tabs(session_id),
    )
    try:
        await screencast.start(websocket)
    except WebSocketDisconnect:
        logger.info("Screencast websocket disconnected session=%s", session_id)
    except Exception as e:
        logger.exception("Screencast error session=%s: %s", session_id, e)
        try:
            await websocket.close(code=1011, reason="Screencast failed")
        except Exception:
            pass
    finally:
        await screencast.stop()


@router.get("/vnc/page/{session_id}")
@router.get("/vnc/page/{session_id}/{path:path}")
async def proxy_vnc_page(session_id: str, request: Request, path: str = "index.html"):
    logger.info(
        "noVNC page proxy request session=%s path=%s query=%s",
        session_id,
        path or "index.html",
        dict(request.query_params),
    )
    user = await _get_http_user(request)
    if not user:
        logger.warning("noVNC page proxy unauthenticated session=%s", session_id)
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await rpa_manager.get_session(session_id)
    if session and session.user_id != str(user.id):
        logger.warning(
            "noVNC page proxy forbidden session=%s request_user=%s owner=%s",
            session_id,
            user.id,
            session.user_id,
        )
        raise HTTPException(status_code=403, detail="Not authorized")

    upstream_url = _get_sandbox_vnc_http_url(path or "index.html")
    query = _filter_proxy_query(request.query_params)
    logger.info(
        "noVNC page proxy upstream session=%s upstream=%s filtered_query=%s",
        session_id,
        upstream_url,
        query,
    )

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        upstream = await client.get(
            upstream_url,
            params=query,
            headers=_get_sandbox_proxy_headers_dict(),
        )
    logger.info(
        "noVNC page proxy response session=%s status=%s content_type=%s",
        session_id,
        upstream.status_code,
        upstream.headers.get("content-type", ""),
    )

    excluded_headers = {"content-length", "transfer-encoding", "connection", "content-encoding"}
    headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in excluded_headers
    }

    content_type = upstream.headers.get("content-type", "")
    content = upstream.content
    if "text/html" in content_type:
        content = _rewrite_vnc_html(upstream.text, session_id).encode("utf-8")
        headers["content-type"] = "text/html; charset=utf-8"

    return FastAPIResponse(
        content=content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=None,
    )


@router.websocket("/vnc/page/{session_id}/websockify")
async def proxy_vnc_page_websocket(websocket: WebSocket, session_id: str):
    logger.info(
        "noVNC websocket proxy request session=%s query=%s client=%s",
        session_id,
        dict(websocket.query_params),
        getattr(websocket.client, "host", None),
    )
    user = await _get_ws_user(websocket)

    requested_protocols = [
        p.strip()
        for p in (websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if p.strip()
    ]
    accepted_subprotocol = requested_protocols[0] if requested_protocols else None

    await websocket.accept(subprotocol=accepted_subprotocol)
    if not user:
        logger.warning("noVNC websocket proxy unauthenticated session=%s", session_id)
        await websocket.close(code=1008, reason="Not authenticated")
        return

    session = await rpa_manager.get_session(session_id)
    if session and session.user_id != str(user.id):
        logger.warning(
            "noVNC websocket proxy forbidden session=%s request_user=%s owner=%s",
            session_id,
            user.id,
            session.user_id,
        )
        await websocket.close(code=1008, reason="Not authorized")
        return

    upstream_url = _get_sandbox_novnc_ws_url()
    query = _filter_proxy_query(websocket.query_params)
    if query:
        from urllib.parse import urlencode
        upstream_url = f"{upstream_url}?{urlencode(query)}"

    logger.info(
        "Opening proxied noVNC websocket for user=%s session=%s upstream=%s subprotocols=%s",
        user.username,
        session_id,
        upstream_url,
        requested_protocols,
    )

    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=requested_protocols or None,
            additional_headers=_get_sandbox_proxy_headers(),
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        ) as upstream:
            logger.info(
                "Proxied noVNC websocket upstream connected session=%s upstream_subprotocol=%s",
                session_id,
                getattr(upstream, "subprotocol", None),
            )

            async def client_to_upstream():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        logger.info("noVNC websocket client disconnected session=%s", session_id)
                        break
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            relay_tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending = await asyncio.wait(
                relay_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*done, return_exceptions=True)
    except ConnectionClosed as exc:
        logger.info("Proxied noVNC websocket closed session=%s detail=%s", session_id, exc)
    except WebSocketDisconnect:
        logger.info("Proxied noVNC websocket local disconnect session=%s", session_id)
        pass
    except Exception as exc:
        logger.exception("Proxied noVNC websocket error session=%s: %s", session_id, exc)
        try:
            await websocket.close(code=1011, reason="noVNC proxy failed")
        except Exception:
            pass


@router.websocket("/vnc/{session_id}")
async def vnc_proxy(websocket: WebSocket, session_id: str):
    """Proxy frontend VNC WebSocket traffic through the backend.

    This keeps the sandbox or local browser endpoint private to the backend,
    so the browser only talks to `/api/v1/rpa/vnc/...`.
    """
    user = await _get_ws_user(websocket)

    requested_protocols = [
        p.strip()
        for p in (websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if p.strip()
    ]
    accepted_subprotocol = requested_protocols[0] if requested_protocols else None

    await websocket.accept(subprotocol=accepted_subprotocol)
    if not user:
        await websocket.close(code=1008, reason="Not authenticated")
        return

    upstream_url = _get_sandbox_vnc_ws_url()
    logger.info(
        "Opening VNC proxy for user=%s session=%s upstream=%s",
        user.username,
        session_id,
        upstream_url,
    )

    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=requested_protocols or None,
            additional_headers=_get_sandbox_proxy_headers(),
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        ) as upstream:

            async def client_to_upstream():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            relay_tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending = await asyncio.wait(
                relay_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*done, return_exceptions=True)
    except ConnectionClosed as exc:
        logger.info("VNC proxy closed: %s", exc)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("VNC proxy error: %s", exc)
        try:
            await websocket.close(code=1011, reason="VNC proxy failed")
        except Exception:
            pass
