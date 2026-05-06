from __future__ import annotations

import asyncio
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
import httpx
import websockets

from backend.runtime.ownership import user_owns_runtime_session
from backend.runtime.session_runtime_manager import get_session_runtime_manager
from backend.config import settings
from backend.user.dependencies import (
    User,
    get_current_user,
    get_user_from_session_id,
    require_user,
)


router = APIRouter(tags=["runtime-proxy"])


def _runtime_belongs_to_user(runtime, current_user: User) -> bool:
    return runtime is not None and str(runtime.user_id) == str(current_user.id)


async def _user_owns_runtime_session(session_id: str, current_user: User) -> bool:
    return await user_owns_runtime_session(session_id, current_user.id)


def _filter_upstream_headers(headers) -> dict:
    excluded = {"host", "content-length", "connection"}
    return {k: v for k, v in headers.items() if k.lower() not in excluded}


def _build_runtime_http_url(rest_base_url: str, path: str, query_params=None) -> str:
    base = f"{rest_base_url.rstrip('/')}/{path.lstrip('/')}"
    if query_params:
        query_string = urlencode(list(query_params.multi_items()))
        if query_string:
            return f"{base}?{query_string}"
    return base


def _build_runtime_ws_url(rest_base_url: str, path: str, query_string: str = "") -> str:
    if rest_base_url.startswith("https://"):
        base = "wss://" + rest_base_url[len("https://") :]
    elif rest_base_url.startswith("http://"):
        base = "ws://" + rest_base_url[len("http://") :]
    else:
        base = rest_base_url
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    if query_string:
        return f"{url}?{query_string}"
    return url


async def _get_websocket_user(websocket: WebSocket) -> User | None:
    if getattr(settings, "auth_provider", "local") == "none":
        return await get_current_user(websocket)  # type: ignore[arg-type]

    auth = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        session_id = auth.split(" ", 1)[1].strip()
    else:
        session_id = websocket.cookies.get(settings.session_cookie)
    return await get_user_from_session_id(session_id)


@router.get("/runtime/session/{session_id}/status")
async def get_runtime_status(
    session_id: str,
    refresh: bool = False,
    current_user: User = Depends(require_user),
):
    if not await _user_owns_runtime_session(session_id, current_user):
        return {
            "status": "missing",
            "session_id": session_id,
            "runtime": None,
        }

    runtime = await get_session_runtime_manager().get_runtime(
        session_id,
        refresh=refresh,
    )
    if runtime is None:
        return {
            "status": "missing",
            "session_id": session_id,
            "runtime": None,
        }
    if not _runtime_belongs_to_user(runtime, current_user):
        return {
            "status": "missing",
            "session_id": session_id,
            "runtime": None,
        }

    return {
        "status": "success",
        "session_id": session_id,
        "runtime": runtime.model_dump(),
    }


@router.get("/runtime/sessions")
async def list_runtime_sessions(
    refresh: bool = False,
    current_user: User = Depends(require_user),
):
    runtimes = await get_session_runtime_manager().list_runtimes(
        user_id=current_user.id,
        refresh=refresh,
    )
    visible_runtimes = []
    for runtime in runtimes:
        if await _user_owns_runtime_session(runtime.session_id, current_user):
            visible_runtimes.append(runtime)
    return {
        "status": "success",
        "runtimes": [runtime.model_dump() for runtime in visible_runtimes],
    }


@router.api_route(
    "/runtime/session/{session_id}/http/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_runtime_http(
    session_id: str,
    path: str,
    request: Request,
    current_user: User = Depends(require_user),
):
    if not await _user_owns_runtime_session(session_id, current_user):
        raise HTTPException(status_code=404, detail="Runtime not found")

    runtime = await get_session_runtime_manager().ensure_runtime(session_id, current_user.id)
    if not _runtime_belongs_to_user(runtime, current_user):
        raise HTTPException(status_code=404, detail="Runtime not found")
    upstream_url = _build_runtime_http_url(runtime.rest_base_url, path, request.query_params)
    body = await request.body()
    headers = _filter_upstream_headers(request.headers)

    async with httpx.AsyncClient() as client:
        upstream_response = await client.request(
            request.method,
            upstream_url,
            headers=headers,
            content=body,
        )

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in {"content-length", "transfer-encoding", "connection"}
    }
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


@router.websocket("/runtime/session/{session_id}/http/{path:path}")
async def proxy_runtime_websocket(
    websocket: WebSocket,
    session_id: str,
    path: str,
):
    current_user = await _get_websocket_user(websocket)
    if not current_user:
        await websocket.close(code=4401)
        return
    if not await _user_owns_runtime_session(session_id, current_user):
        await websocket.close(code=4404)
        return

    runtime = await get_session_runtime_manager().ensure_runtime(session_id, current_user.id)
    if not _runtime_belongs_to_user(runtime, current_user):
        await websocket.close(code=4404)
        return
    upstream_url = _build_runtime_ws_url(
        runtime.rest_base_url,
        path,
        websocket.url.query,
    )
    await websocket.accept()

    async with websockets.connect(upstream_url) as upstream:
        async def _client_to_upstream():
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await upstream.send(message["bytes"])
                elif message.get("text") is not None:
                    await upstream.send(message["text"])

        async def _upstream_to_client():
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        tasks = [
            asyncio.create_task(_client_to_upstream()),
            asyncio.create_task(_upstream_to_client()),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
