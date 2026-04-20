from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Dict, List
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.config import settings
from backend.deepagent.mcp_config_loader import load_system_mcp_servers
from backend.deepagent.mcp_credentials import McpCredentialResolutionError
from backend.deepagent.mcp_registry import apply_mcp_credentials
from backend.deepagent.mcp_runtime import McpSdkRuntimeFactory, coerce_mcp_tool_definition
from backend.deepagent.sessions import ScienceSessionNotFoundError, async_get_science_session
from backend.mcp.models import McpServerDefinition, SessionMcpBindingUpdate, UserMcpServerCreate, UserMcpServerUpdate
from backend.storage import get_repository
from backend.user.dependencies import User, require_user

router = APIRouter(tags=["mcp"])

RPA_GATEWAY_SYSTEM_SERVER_ID = "rpa_gateway"


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


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _list_user_mcp_servers(user_id: str) -> List[Dict[str, Any]]:
    repo = get_repository("user_mcp_servers")
    return await repo.find_many({"user_id": user_id}, sort=[("updated_at", -1)])


async def _build_rpa_gateway_tools(user_id: str) -> list[dict[str, Any]]:
    from backend.route.rpa_mcp import _build_gateway_tools

    return await _maybe_await(_build_gateway_tools(user_id))


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
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
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
        headers = endpoint_config.get("headers")
        if headers is None:
            headers = endpoint_config.get("http_headers")
        if headers is None:
            headers = endpoint_config.get("http_header")
        normalized["headers"] = _normalize_string_map(headers, "endpoint_config.headers")
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
    server = await _resolve_server_by_key(server_key, user_id)
    return server is not None


async def _resolve_server_by_key(server_key: str, user_id: str) -> Dict[str, Any] | None:
    scope, separator, server_id = server_key.partition(":")
    if not separator or not server_id:
        return None

    if scope == "system":
        for server in load_system_mcp_servers():
            if server.id == server_id:
                return _serialize_system_server(server)
        return None

    if scope == "user":
        for doc in await _maybe_await(_list_user_mcp_servers(user_id)):
            if str(doc.get("_id")) == server_id:
                return _serialize_user_server(doc)
        return None

    return None


def _serialize_system_server(server: Any) -> Dict[str, Any]:
    endpoint_config = {
        "url": server.url,
        "command": server.command,
        "args": server.args,
        "cwd": server.cwd,
        "headers": server.headers,
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


def _apply_session_mode(server: Dict[str, Any], session_mode: str) -> Dict[str, Any]:
    effective_enabled = (
        server.get("enabled", True)
        and (
            session_mode == "enabled"
            or (session_mode == "inherit" and server.get("default_enabled", False))
        )
    )
    payload = dict(server)
    payload["session_mode"] = session_mode
    payload["effective_enabled"] = effective_enabled
    return payload


def _to_server_definition(server: Dict[str, Any]):
    endpoint = server.get("endpoint_config") or {}
    return McpServerDefinition(
        id=server["id"],
        name=server["name"],
        description=server.get("description", ""),
        transport=server["transport"],
        scope=server["scope"],
        enabled=server.get("enabled", True),
        default_enabled=server.get("default_enabled", False),
        url=endpoint.get("url", ""),
        command=endpoint.get("command", ""),
        args=endpoint.get("args", []),
        cwd=endpoint.get("cwd", ""),
        headers=endpoint.get("headers", {}),
        env=endpoint.get("env", {}),
        timeout_ms=endpoint.get("timeout_ms", 20000),
        credential_binding=server.get("credential_binding") or {},
        tool_policy=server.get("tool_policy") or {},
    )


async def _get_owned_user_server_doc(server_id: str, user_id: str) -> Dict[str, Any]:
    repo = get_repository("user_mcp_servers")
    doc = await repo.find_one({"_id": server_id, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return doc


async def _discover_tools(server_key: str, user_id: str) -> Dict[str, Any]:
    server = await _resolve_server_by_key(server_key, user_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    definition = _to_server_definition(server)
    if definition.scope == "system" and definition.id == RPA_GATEWAY_SYSTEM_SERVER_ID:
        raw_tools = await _build_rpa_gateway_tools(user_id)
        tools = []
        for raw_tool in raw_tools:
            tool = coerce_mcp_tool_definition(raw_tool)
            if not tool.name:
                continue
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
            )
        return {
            "server_key": server_key,
            "tools": tools,
            "tool_count": len(tools),
        }

    if definition.scope == "user":
        try:
            definition = await apply_mcp_credentials(definition, user_id)
        except McpCredentialResolutionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    runtime = McpSdkRuntimeFactory().create_runtime(definition)
    tools = []
    for raw_tool in await runtime.list_tools():
        tool = coerce_mcp_tool_definition(raw_tool)
        if not tool.name:
            continue
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
        )
    return {
        "server_key": server_key,
        "tools": tools,
        "tool_count": len(tools),
    }


@router.get("/mcp/servers", response_model=ApiResponse)
async def list_mcp_servers(current_user: User = Depends(require_user)) -> ApiResponse:
    system_servers = [_serialize_system_server(server) for server in load_system_mcp_servers()]
    user_servers = [_serialize_user_server(doc) for doc in await _maybe_await(_list_user_mcp_servers(str(current_user.id)))]
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


@router.get("/mcp/servers/{server_key}", response_model=ApiResponse)
async def get_mcp_server(
    server_key: str,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    server = await _resolve_server_by_key(server_key, str(current_user.id))
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return ApiResponse(data=server)


@router.put("/mcp/servers/{server_id}", response_model=ApiResponse)
async def update_mcp_server(
    server_id: str,
    body: UserMcpServerUpdate,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    if body.transport == "stdio" and settings.storage_backend != "local":
        raise HTTPException(status_code=400, detail="stdio MCP is only allowed in local mode")

    await _get_owned_user_server_doc(server_id, str(current_user.id))
    endpoint_config = _normalize_endpoint_config(body.transport, body.endpoint_config)
    repo = get_repository("user_mcp_servers")
    await repo.update_one(
        {"_id": server_id, "user_id": str(current_user.id)},
        {
            "$set": {
                "name": body.name,
                "description": body.description,
                "transport": body.transport,
                "enabled": body.enabled,
                "default_enabled": body.default_enabled,
                "endpoint_config": endpoint_config,
                "credential_binding": body.credential_binding.model_dump(),
                "tool_policy": body.tool_policy.model_dump(),
                "updated_at": datetime.now(),
            }
        },
    )
    return ApiResponse(data={"id": server_id, "saved": True})


@router.delete("/mcp/servers/{server_id}", response_model=ApiResponse)
async def delete_mcp_server(
    server_id: str,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    repo = get_repository("user_mcp_servers")
    deleted = await repo.delete_one({"_id": server_id, "user_id": str(current_user.id)})
    if not deleted:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return ApiResponse(data={"id": server_id, "deleted": True})


@router.post("/mcp/servers/{server_key}/test", response_model=ApiResponse)
async def test_mcp_server(
    server_key: str,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    data = await _discover_tools(server_key, str(current_user.id))
    return ApiResponse(data={"server_key": server_key, "ok": True, "tool_count": data["tool_count"]})


@router.post("/mcp/servers/{server_key}/discover-tools", response_model=ApiResponse)
async def discover_mcp_tools(
    server_key: str,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    return ApiResponse(data=await _discover_tools(server_key, str(current_user.id)))


@router.get("/sessions/{session_id}/mcp", response_model=ApiResponse)
async def list_session_mcp_servers(
    session_id: str,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    try:
        session = await async_get_science_session(session_id)
    except ScienceSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if str(session.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    binding_repo = get_repository("session_mcp_bindings")
    binding_docs = await binding_repo.find_many({"session_id": session_id, "user_id": str(current_user.id)})
    binding_modes = {doc["server_key"]: doc.get("mode", "inherit") for doc in binding_docs}

    servers = [_serialize_system_server(server) for server in load_system_mcp_servers()]
    servers.extend(
        _serialize_user_server(doc)
        for doc in await _maybe_await(_list_user_mcp_servers(str(current_user.id)))
    )

    return ApiResponse(
        data=[
            _apply_session_mode(server, binding_modes.get(server["server_key"], "inherit"))
            for server in servers
        ]
    )


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
