import json
import logging
import asyncio
from urllib.parse import urlparse
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import websockets
from websockets.exceptions import ConnectionClosed

from backend.rpa.manager import rpa_manager
from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.executor import ScriptExecutor
from backend.rpa.skill_exporter import SkillExporter
from backend.rpa.assistant import RPAAssistant
from backend.rpa.cdp_connector import get_cdp_connector
from backend.rpa.screencast import ScreencastService
from backend.user.dependencies import get_current_user, User
from backend.config import settings
from backend.storage import get_repository

logger = logging.getLogger(__name__)

RPA_TEST_TIMEOUT_S = 180.0
RPA_PAGE_TIMEOUT_MS = 60000

router = APIRouter(tags=["RPA"])
generator = PlaywrightGenerator()
executor = ScriptExecutor()
exporter = SkillExporter()
assistant = RPAAssistant()


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


def _get_sandbox_vnc_ws_url() -> str:
    """Build the internal sandbox VNC/websockify URL for backend proxying."""
    sandbox_base = settings.sandbox_mcp_url.replace("/mcp", "")
    parsed = urlparse(sandbox_base)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    return parsed._replace(scheme=ws_scheme, path="/vnc/websockify", query="", fragment="").geturl()


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
    request: GenerateRequest = GenerateRequest(),
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"))

    logs = []
    browser = await get_cdp_connector().get_browser()

    # 本地模式：先创建 page 并注册，等待前端连接 screencast
    if settings.storage_backend == "local":
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()
        page.set_default_timeout(RPA_PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(RPA_PAGE_TIMEOUT_MS)
        rpa_manager._pages[session_id] = page

        # 等待前端连接 screencast
        await asyncio.sleep(1.0)

        # 执行脚本
        try:
            namespace: Dict[str, Any] = {}
            exec(compile(script, "<rpa_script>", "exec"), namespace)

            if "execute_skill" not in namespace:
                result = {"success": False, "output": "", "error": "No execute_skill() function in script"}
            else:
                await asyncio.wait_for(namespace["execute_skill"](page), timeout=RPA_TEST_TIMEOUT_S)
                await page.wait_for_timeout(3000)
                result = {"success": True, "output": "SKILL_SUCCESS"}
        except Exception as e:
            result = {"success": False, "output": f"SKILL_ERROR: {e}", "error": str(e)}
        finally:
            # 清理
            await asyncio.sleep(1.0)
            rpa_manager._pages.pop(session_id, None)
            await context.close()
    else:
        # Docker 模式：使用原有逻辑
        result = await executor.execute(
            browser,
            script,
            on_log=lambda msg: logs.append(msg),
            session_id=session_id,
            page_registry=rpa_manager._pages,
        )

    return {"status": "success", "result": result, "logs": logs, "script": script}


@router.post("/session/{session_id}/save")
async def save_skill(
    session_id: str,
    request: SaveSkillRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"))

    skill_name = await exporter.export_skill(
        user_id=str(current_user.id),
        skill_name=request.skill_name,
        description=request.description,
        script=script,
        params=request.params,
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
            # Pause recording during AI execution
            rpa_manager.pause_recording(session_id)

            async for event in assistant.chat(
                session_id=session_id,
                page=page,
                message=request.message,
                steps=steps,
                model_config=model_config,
            ):
                evt_type = event.get("event", "message")
                evt_data = event.get("data", {})

                # If execution succeeded and returned a step, add it to session
                if evt_type == "result" and evt_data.get("success") and evt_data.get("step"):
                    step_data = evt_data["step"]
                    await rpa_manager.add_step(session_id, step_data)

                yield {
                    "event": evt_type,
                    "data": json.dumps(evt_data, ensure_ascii=False),
                }
        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}, ensure_ascii=False),
            }
            yield {"event": "done", "data": "{}"}
        finally:
            # Resume recording after AI execution
            rpa_manager.resume_recording(session_id)

    return EventSourceResponse(event_generator())


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
    """CDP screencast: push browser frames + receive input events.
    Only used in local mode (STORAGE_BACKEND=local).
    """
    user = await _get_ws_user(websocket)
    await websocket.accept()
    if not user:
        await websocket.close(code=1008, reason="Not authenticated")
        return

    session = await rpa_manager.get_session(session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found")
        return
    if session.user_id != str(user.id):
        await websocket.close(code=1008, reason="Not authorized")
        return

    page = rpa_manager.get_page(session_id)
    if not page:
        await websocket.close(code=1008, reason="No active page")
        return

    try:
        cdp_session = await page.context.new_cdp_session(page)
    except Exception as e:
        logger.error(f"Failed to create CDP session: {e}")
        await websocket.close(code=1011, reason="CDP session failed")
        return

    screencast = ScreencastService(cdp_session)
    try:
        await screencast.start(websocket)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Screencast error: {e}")
    finally:
        await screencast.stop()
        try:
            await cdp_session.detach()
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
            extra_headers=_get_sandbox_proxy_headers(),
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
