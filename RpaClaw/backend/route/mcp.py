from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.config import settings
from backend.deepagent.mcp_config_loader import load_system_mcp_servers
from backend.deepagent.sessions import ScienceSessionNotFoundError, async_get_science_session
from backend.mcp.models import SessionMcpBindingUpdate, UserMcpServerCreate
from backend.storage import get_repository
from backend.user.dependencies import User, require_user

router = APIRouter(tags=["mcp"])


class ApiResponse(BaseModel):
    code: int = Field(default=0)
    msg: str = Field(default="ok")
    data: Any = Field(default=None)


class McpServerListItem(BaseModel):
    id: str
    server_key: str
    scope: str
    name: str
    description: str = ""
    transport: str
    enabled: bool = True
    default_enabled: bool = False
    readonly: bool = False
    endpoint_config: Dict[str, Any] = Field(default_factory=dict)
    credential_binding: Dict[str, Any] = Field(default_factory=dict)
    tool_policy: Dict[str, Any] = Field(default_factory=dict)


async def _list_user_mcp_servers(user_id: str) -> List[Dict[str, Any]]:
    repo = get_repository("user_mcp_servers")
    return await repo.find_many({"user_id": user_id}, sort=[("updated_at", -1)])


def _normalize_string_map(value: Any, field_name: str) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{field_name} must be an object")

    normalized: Dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise HTTPException(status_code=400, detail=f"{field_name} must be a string map")
        normalized[key] = item
    return normalized


def _normalize_string_list(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a list of strings")
    return list(value)


def _normalize_timeout_ms(value: Any) -> int:
    if value is None:
        return 20000
    if not isinstance(value, int) or value <= 0:
        raise HTTPException(status_code=400, detail="endpoint_config.timeout_ms must be a positive integer")
    return value


def _normalize_endpoint_config(transport: str, endpoint_config: Any) -> Dict[str, Any]:
    if not isinstance(endpoint_config, dict):
        raise HTTPException(status_code=400, detail="endpoint_config must be an object")

    normalized: Dict[str, Any] = {
        "env": _normalize_string_map(endpoint_config.get("env"), "endpoint_config.env"),
        "timeout_ms": _normalize_timeout_ms(endpoint_config.get("timeout_ms")),
    }

    if transport in {"streamable_http", "sse"}:
        url = endpoint_config.get("url")
        if not isinstance(url, str) or not url.strip():
            raise HTTPException(status_code=400, detail="endpoint_config.url is required for HTTP/SSE MCP")
        normalized["url"] = url.strip()
        normalized["headers"] = _normalize_string_map(endpoint_config.get("headers"), "endpoint_config.headers")
        return normalized

    if transport == "stdio":
        command = endpoint_config.get("command")
        if not isinstance(command, str) or not command.strip():
            raise HTTPException(status_code=400, detail="endpoint_config.command is required for stdio MCP")
        normalized["command"] = command.strip()
        normalized["args"] = _normalize_string_list(endpoint_config.get("args"), "endpoint_config.args")

        cwd = endpoint_config.get("cwd", "")
        if cwd is None:
            cwd = ""
        if not isinstance(cwd, str):
            raise HTTPException(status_code=400, detail="endpoint_config.cwd must be a string")
        normalized["cwd"] = cwd
        return normalized

    raise HTTPException(status_code=400, detail="Unsupported MCP transport")


async def _server_key_is_accessible(server_key: str, user_id: str) -> bool:
    scope, separator, server_id = server_key.partition(":")
    if not separator or not server_id:
        return False

    if scope == "system":
        return any(server.id == server_id for server in load_system_mcp_servers())

    if scope == "user":
        return any(str(doc.get("_id")) == server_id for doc in await _list_user_mcp_servers(user_id))

    return False


def _serialize_system_server(server: Any) -> Dict[str, Any]:
    endpoint_config = {
        "url": server.url,
        "command": server.command,
        "args": server.args,
        "cwd": server.cwd,
        "timeout_ms": server.timeout_ms,
    }
    return McpServerListItem(
        id=server.id,
        server_key=f"system:{server.id}",
        scope="system",
        name=server.name,
        description=server.description,
        transport=server.transport,
        enabled=server.enabled,
        default_enabled=server.default_enabled,
        readonly=True,
        endpoint_config=endpoint_config,
        credential_binding={},
        tool_policy=server.tool_policy.model_dump(),
    ).model_dump()


def _serialize_user_server(doc: Dict[str, Any]) -> Dict[str, Any]:
    return McpServerListItem(
        id=str(doc["_id"]),
        server_key=f"user:{doc['_id']}",
        scope="user",
        name=doc["name"],
        description=doc.get("description", ""),
        transport=doc["transport"],
        enabled=doc.get("enabled", True),
        default_enabled=doc.get("default_enabled", False),
        readonly=False,
        endpoint_config=doc.get("endpoint_config") or {},
        credential_binding=doc.get("credential_binding") or {},
        tool_policy=doc.get("tool_policy") or {},
    ).model_dump()


@router.get("/mcp/servers", response_model=ApiResponse)
async def list_mcp_servers(current_user: User = Depends(require_user)) -> ApiResponse:
    system_servers = [_serialize_system_server(server) for server in load_system_mcp_servers()]
    user_servers = [_serialize_user_server(doc) for doc in await _list_user_mcp_servers(str(current_user.id))]
    return ApiResponse(data=system_servers + user_servers)


@router.post("/mcp/servers", response_model=ApiResponse)
async def create_mcp_server(
    body: UserMcpServerCreate,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    if body.transport == "stdio" and settings.storage_backend != "local":
        raise HTTPException(status_code=400, detail="stdio MCP is only allowed in local mode")

    endpoint_config = _normalize_endpoint_config(body.transport, body.endpoint_config)

    repo = get_repository("user_mcp_servers")
    server_id = f"mcp_{uuid.uuid4().hex[:12]}"
    now = datetime.now()
    doc = {
        "_id": server_id,
        "user_id": str(current_user.id),
        "name": body.name,
        "description": body.description,
        "transport": body.transport,
        "enabled": True,
        "default_enabled": body.default_enabled,
        "endpoint_config": endpoint_config,
        "credential_binding": body.credential_binding.model_dump(),
        "tool_policy": body.tool_policy.model_dump(),
        "created_at": now,
        "updated_at": now,
    }
    await repo.insert_one(doc)
    return ApiResponse(data={"id": server_id, "saved": True})


@router.put("/sessions/{session_id}/mcp/servers/{server_key}", response_model=ApiResponse)
async def update_session_override(
    session_id: str,
    server_key: str,
    body: SessionMcpBindingUpdate,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    try:
        session = await async_get_science_session(session_id)
    except ScienceSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if str(session.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    if not await _server_key_is_accessible(server_key, str(current_user.id)):
        raise HTTPException(status_code=404, detail="MCP server not found")

    repo = get_repository("session_mcp_bindings")
    await repo.update_one(
        {"session_id": session_id, "user_id": str(current_user.id), "server_key": server_key},
        {"$set": {"mode": body.mode, "updated_at": datetime.now()}},
        upsert=True,
    )
    return ApiResponse(data={"session_id": session_id, "server_key": server_key, "mode": body.mode})
