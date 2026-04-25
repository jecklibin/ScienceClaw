from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from backend.config import settings
from backend.rpa.cdp_connector import get_cdp_connector
from backend.rpa.execution_plan import build_rpa_mcp_execution_plan
from backend.rpa.manager import rpa_manager
from backend.rpa.mcp_converter import RpaMcpConverter
from backend.rpa.mcp_executor import InvalidCookieError, RpaMcpExecutor
from backend.rpa.mcp_models import RpaMcpToolDefinition
from backend.rpa.mcp_preview_registry import RpaMcpPreviewDraftRegistry
from backend.rpa.mcp_registry import RpaMcpToolRegistry
from backend.rpa.mcp_step_projection import session_to_mcp_steps
from backend.user.dependencies import User, require_user

router = APIRouter(tags=["rpa-mcp"])


class ApiResponse(BaseModel):
    code: int = 0
    msg: str = "ok"
    data: Any = None


class PreviewRequest(BaseModel):
    name: str
    description: str = ""
    allowed_domains: list[str] = Field(default_factory=list)
    post_auth_start_url: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    schema_source: str = ""


class SaveToolRequest(PreviewRequest):
    input_schema: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    schema_source: str = ""
    output_schema: dict[str, Any] = Field(default_factory=dict)


class UpdateToolRequest(BaseModel):
    name: str = ""
    description: str = ""
    enabled: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    post_auth_start_url: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    schema_source: str = ""
    output_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema_confirmed: bool | None = None


class TestToolRequest(BaseModel):
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)


class PreviewTestRequest(PreviewRequest):
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def get_rpa_session_steps(session_id: str, user_id: str) -> dict[str, Any]:
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if str(session.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {
        "steps": session_to_mcp_steps(session),
        "params": getattr(session, 'params', {}) or {},
        "skill_name": getattr(session, 'skill_name', '') or getattr(session, 'title', '') or '',
    }


def _preview_config_signature(
    *,
    session_id: str,
    user_id: str,
    name: str,
    description: str,
    allowed_domains: list[str],
    post_auth_start_url: str,
    input_schema: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "user_id": user_id,
            "allowed_domains": allowed_domains,
            "post_auth_start_url": post_auth_start_url,
            "input_schema": input_schema or {},
            "params": params or {},
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _apply_confirmed_input_contract(preview: RpaMcpToolDefinition, body: Any) -> None:
    input_schema = getattr(body, "input_schema", None)
    params = getattr(body, "params", None)
    schema_source = getattr(body, "schema_source", "")
    if input_schema:
        preview.input_schema = input_schema
    if params:
        preview.params = params
    if schema_source:
        preview.schema_source = schema_source


async def _preview_payload(session_id: str, user_id: str, body: PreviewRequest | SaveToolRequest | PreviewTestRequest):
    payload = await _maybe_await(get_rpa_session_steps(session_id, user_id))
    preview = await RpaMcpConverter().preview_with_semantics(
        user_id=user_id,
        session_id=session_id,
        skill_name=payload.get('skill_name', ''),
        name=body.name,
        description=body.description,
        steps=payload.get('steps', []),
        params=payload.get('params', {}),
    )
    if not isinstance(preview, RpaMcpToolDefinition) and hasattr(preview, "model_dump"):
        preview_payload = preview.model_dump(mode='python')
        preview_payload.setdefault("user_id", user_id)
        preview_payload.setdefault("source", {"type": "rpa_skill", "session_id": session_id, "skill_name": payload.get('skill_name', '')})
        preview = RpaMcpToolDefinition(**preview_payload)
    if body.allowed_domains:
        preview.allowed_domains = body.allowed_domains
    if body.post_auth_start_url:
        preview.post_auth_start_url = body.post_auth_start_url
    _apply_confirmed_input_contract(preview, body)
    config_signature = _preview_config_signature(
        session_id=session_id,
        user_id=user_id,
        name=preview.name,
        description=preview.description,
        allowed_domains=preview.allowed_domains,
        post_auth_start_url=preview.post_auth_start_url,
        input_schema=preview.input_schema,
        params=preview.params,
    )
    draft = await _maybe_await(RpaMcpPreviewDraftRegistry().get(session_id, user_id, config_signature))
    if draft and draft.get("tested"):
        preview.recommended_output_schema = draft.get("recommended_output_schema") or preview.recommended_output_schema
        preview.output_schema = draft.get("recommended_output_schema") or preview.output_schema
        preview.output_examples = draft.get("output_examples") or []
        preview.output_inference_report = draft.get("output_inference_report") or {}
    return preview


async def _build_gateway_tools(user_id: str) -> list[dict[str, Any]]:
    tools = await RpaMcpToolRegistry().list_enabled_for_user(user_id)
    return [
        {
            "name": tool.tool_name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "inputSchema": tool.input_schema,
            "outputSchema": tool.output_schema,
        }
        for tool in tools
    ]


def _is_json_rpc_request(body: dict[str, Any]) -> bool:
    return body.get("jsonrpc") == "2.0"


def _json_rpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _json_rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status_code=200,
    )


def _mcp_initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {
            "tools": {"listChanged": False},
        },
        "serverInfo": {
            "name": "RPA Tool Gateway",
            "version": "0.1.0",
        },
        "instructions": (
            "Provides RPA MCP tools published from RpaClaw Tools. "
            "If a tool requires browser identity, pass Playwright cookies in the tool arguments."
        ),
    }


def _mcp_call_result_payload(result: dict[str, Any], output_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False),
            }
        ],
        "structuredContent": result,
        "isError": not bool(result.get("success", True)),
    }
    if output_schema:
        payload["outputSchema"] = output_schema
    return payload


async def _resolve_tool_browser_session_id(tool: RpaMcpToolDefinition) -> str | None:
    source_session_id = str(getattr(tool.source, 'session_id', '') or '')
    if not source_session_id:
        return None
    session = await _maybe_await(rpa_manager.get_session(source_session_id))
    if not session:
        return None
    return getattr(session, 'sandbox_session_id', None) or None


def _build_rpa_mcp_executor(*, current_user_id: str) -> RpaMcpExecutor:
    connector = get_cdp_connector()
    pw_loop_runner = getattr(connector, 'run_in_pw_loop', None)

    async def _browser_factory(*, tool: RpaMcpToolDefinition):
        sandbox_session_id = await _resolve_tool_browser_session_id(tool)
        return await connector.get_browser(session_id=sandbox_session_id, user_id=current_user_id)

    return RpaMcpExecutor(
        browser_factory=_browser_factory,
        pw_loop_runner=pw_loop_runner,
        downloads_dir_factory=lambda tool: str(Path(settings.workspace_dir) / 'rpa_mcp_downloads' / tool.id),
    )


@router.post('/rpa-mcp/session/{session_id}/preview', response_model=ApiResponse)
async def preview_rpa_mcp_tool(session_id: str, body: PreviewRequest, current_user: User = Depends(require_user)) -> ApiResponse:
    preview = await _preview_payload(session_id, str(current_user.id), body)
    return ApiResponse(data=preview.model_dump(mode='python'))


@router.post('/rpa-mcp/session/{session_id}/test-preview', response_model=ApiResponse)
async def test_preview_rpa_mcp_tool(session_id: str, body: PreviewTestRequest, current_user: User = Depends(require_user)) -> ApiResponse:
    preview = await _preview_payload(session_id, str(current_user.id), body)
    try:
        executor = _build_rpa_mcp_executor(current_user_id=str(current_user.id))
        result = await executor.execute(preview, {"cookies": body.cookies, **body.arguments})
    except InvalidCookieError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("success"):
        recommended_output_schema, output_inference_report = RpaMcpConverter().infer_output_from_execution(preview, result)
        config_signature = _preview_config_signature(
            session_id=session_id,
            user_id=str(current_user.id),
            name=preview.name,
            description=preview.description,
            allowed_domains=preview.allowed_domains,
            post_auth_start_url=preview.post_auth_start_url,
            input_schema=preview.input_schema,
            params=preview.params,
        )
        await RpaMcpPreviewDraftRegistry().save(
            session_id,
            str(current_user.id),
            config_signature,
            {
                "tested": True,
                "recommended_output_schema": recommended_output_schema,
                "output_examples": [result],
                "output_inference_report": output_inference_report,
                "test_result": result,
            },
        )
    return ApiResponse(data=result)


@router.post('/rpa-mcp/session/{session_id}/tools', response_model=ApiResponse)
async def create_rpa_mcp_tool(session_id: str, body: SaveToolRequest, current_user: User = Depends(require_user)) -> ApiResponse:
    preview = await _preview_payload(session_id, str(current_user.id), body)
    config_signature = _preview_config_signature(
        session_id=session_id,
        user_id=str(current_user.id),
        name=preview.name,
        description=preview.description,
        allowed_domains=preview.allowed_domains,
        post_auth_start_url=preview.post_auth_start_url,
        input_schema=preview.input_schema,
        params=preview.params,
    )
    draft = await RpaMcpPreviewDraftRegistry().get(session_id, str(current_user.id), config_signature)
    if not draft or not draft.get("tested"):
        raise HTTPException(status_code=400, detail='A successful preview test is required before saving the tool')
    preview.id = f"rpa_mcp_{uuid.uuid4().hex[:12]}"
    preview.recommended_output_schema = draft.get("recommended_output_schema") or preview.recommended_output_schema
    preview.output_examples = draft.get("output_examples") or []
    preview.output_inference_report = draft.get("output_inference_report") or {}
    _apply_confirmed_input_contract(preview, body)
    preview.output_schema = body.output_schema or preview.recommended_output_schema
    preview.output_schema_confirmed = True
    saved = await RpaMcpToolRegistry().save(preview)
    return ApiResponse(data=saved.model_dump(mode='python'))


@router.get('/rpa-mcp/tools', response_model=ApiResponse)
async def list_rpa_mcp_tools(current_user: User = Depends(require_user)) -> ApiResponse:
    tools = await RpaMcpToolRegistry().list_for_user(str(current_user.id))
    return ApiResponse(data=[tool.model_dump(mode='python') for tool in tools])


@router.get('/rpa-mcp/tools/{tool_id}', response_model=ApiResponse)
async def get_rpa_mcp_tool(tool_id: str, current_user: User = Depends(require_user)) -> ApiResponse:
    tool = await RpaMcpToolRegistry().get_owned(tool_id, str(current_user.id))
    if not tool:
        raise HTTPException(status_code=404, detail='RPA MCP tool not found')
    return ApiResponse(data=tool.model_dump(mode='python'))


@router.get('/rpa-mcp/tools/{tool_id}/execution-plan', response_model=ApiResponse)
async def get_rpa_mcp_execution_plan(tool_id: str, current_user: User = Depends(require_user)) -> ApiResponse:
    tool = await RpaMcpToolRegistry().get_owned(tool_id, str(current_user.id))
    if not tool:
        raise HTTPException(status_code=404, detail='RPA MCP tool not found')
    return ApiResponse(data=build_rpa_mcp_execution_plan(tool))


@router.put('/rpa-mcp/tools/{tool_id}', response_model=ApiResponse)
async def update_rpa_mcp_tool(tool_id: str, body: UpdateToolRequest, current_user: User = Depends(require_user)) -> ApiResponse:
    registry = RpaMcpToolRegistry()
    tool = await registry.get_owned(tool_id, str(current_user.id))
    if not tool:
        raise HTTPException(status_code=404, detail='RPA MCP tool not found')
    fields_set = body.model_fields_set
    if "name" in fields_set:
        tool.name = body.name
        tool.tool_name = RpaMcpConverter()._tool_name(body.name)
    if "description" in fields_set:
        tool.description = body.description
    tool.enabled = body.enabled
    if "allowed_domains" in fields_set:
        tool.allowed_domains = body.allowed_domains
    if "post_auth_start_url" in fields_set:
        tool.post_auth_start_url = body.post_auth_start_url
    if "input_schema" in fields_set:
        tool.input_schema = body.input_schema
    if "params" in fields_set:
        tool.params = body.params
    if "schema_source" in fields_set and body.schema_source:
        tool.schema_source = body.schema_source
    if "output_schema" in fields_set:
        tool.output_schema = body.output_schema
    if body.output_schema_confirmed is not None:
        tool.output_schema_confirmed = body.output_schema_confirmed
    saved = await registry.save(tool)
    return ApiResponse(data=saved.model_dump(mode='python'))


@router.delete('/rpa-mcp/tools/{tool_id}', response_model=ApiResponse)
async def delete_rpa_mcp_tool(tool_id: str, current_user: User = Depends(require_user)) -> ApiResponse:
    deleted = await RpaMcpToolRegistry().delete(tool_id, str(current_user.id))
    if not deleted:
        raise HTTPException(status_code=404, detail='RPA MCP tool not found')
    return ApiResponse(data={"id": tool_id, "deleted": True})


@router.post('/rpa-mcp/tools/{tool_id}/test', response_model=ApiResponse)
async def test_rpa_mcp_tool(tool_id: str, body: TestToolRequest, current_user: User = Depends(require_user)) -> ApiResponse:
    tool = await RpaMcpToolRegistry().get_owned(tool_id, str(current_user.id))
    if not tool:
        raise HTTPException(status_code=404, detail='RPA MCP tool not found')
    try:
        executor = _build_rpa_mcp_executor(current_user_id=str(current_user.id))
        result = await executor.execute(tool, {"cookies": body.cookies, **body.arguments})
    except InvalidCookieError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=result)


@router.post('/rpa-mcp/mcp', response_model=None)
async def rpa_mcp_gateway(
    body: dict[str, Any],
    current_user: User = Depends(require_user),
) -> dict[str, Any] | JSONResponse | Response:
    method = body.get('method')
    params = body.get('params') or {}
    request_id = body.get('id')
    is_json_rpc = _is_json_rpc_request(body)
    if is_json_rpc and method == 'initialize':
        return _json_rpc_result(request_id, _mcp_initialize_result())
    if is_json_rpc and method == 'notifications/initialized':
        return Response(status_code=202)
    if is_json_rpc and method == 'ping':
        return _json_rpc_result(request_id, {})
    if method == 'tools/list':
        result = {"tools": await _maybe_await(_build_gateway_tools(str(current_user.id)))}
        if is_json_rpc:
            return _json_rpc_result(request_id, result)
        return {"result": result}
    if method == 'tools/call':
        tool_name = str(params.get('name') or '')
        arguments = dict(params.get('arguments') or {})
        tool = await RpaMcpToolRegistry().get_by_tool_name(tool_name, str(current_user.id))
        if not tool:
            if is_json_rpc:
                return _json_rpc_error(request_id, -32602, 'RPA MCP tool not found')
            raise HTTPException(status_code=404, detail='RPA MCP tool not found')
        try:
            executor = _build_rpa_mcp_executor(current_user_id=str(current_user.id))
            result = await executor.execute(tool, arguments)
        except InvalidCookieError as exc:
            if is_json_rpc:
                return _json_rpc_result(
                    request_id,
                    _mcp_call_result_payload(
                        {
                            "success": False,
                            "message": str(exc),
                            "data": {},
                            "downloads": [],
                            "artifacts": [],
                            "error": {"message": str(exc)},
                        },
                        tool.output_schema,
                    ),
                )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = _mcp_call_result_payload(result, tool.output_schema)
        if is_json_rpc:
            return _json_rpc_result(request_id, payload)
        return {"result": payload}
    if is_json_rpc:
        return _json_rpc_error(request_id, -32601, 'Unsupported MCP method')
    raise HTTPException(status_code=400, detail='Unsupported MCP method')
