from datetime import datetime
from types import SimpleNamespace

import anyio
import httpx

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend.route import rpa_mcp as rpa_mcp_route
from backend.rpa.execution_plan import build_rpa_mcp_execution_plan
from backend.rpa.manager import RPASession
from backend.rpa.manual_recording_models import ManualActionKind, ManualRecordedAction
from backend.rpa.trace_models import RPAAcceptedTrace, RPAAIExecution, RPATraceType


class _User:
    id = "user-1"


class _MemoryRepo:
    def __init__(self, docs=None):
        self.docs = {str(doc["_id"]): dict(doc) for doc in (docs or [])}

    async def find_one(self, filter_doc, projection=None):
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                return dict(doc)
        return None

    async def find_many(self, filter_doc, projection=None, sort=None, skip=0, limit=0):
        return [dict(doc) for doc in self.docs.values() if all(doc.get(key) == value for key, value in filter_doc.items())]

    async def update_one(self, filter_doc, update_doc, upsert=False):
        existing = await self.find_one(filter_doc)
        payload = dict(existing or filter_doc)
        payload.update(update_doc.get("$set", {}))
        payload.setdefault("_id", filter_doc.get("_id"))
        self.docs[str(payload["_id"])] = payload
        return 1

    async def delete_one(self, filter_doc):
        for doc_id, doc in list(self.docs.items()):
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                del self.docs[doc_id]
                return 1
        return 0


class _FakeRegistry:
    def __init__(self, tool):
        self.tool = tool

    async def get_owned(self, tool_id, user_id):
        assert tool_id == self.tool.id
        assert user_id == "user-1"
        return self.tool

    async def get_by_tool_name(self, tool_name, user_id):
        assert tool_name == self.tool.tool_name
        assert user_id == "user-1"
        return self.tool

    async def list_enabled_for_user(self, user_id):
        assert user_id == "user-1"
        return [self.tool]

    async def save(self, tool):
        self.tool = tool
        return tool


class _FakePreviewTestRegistry:
    def __init__(self):
        self.docs = {}

    async def get(self, session_id, user_id, config_signature):
        doc = self.docs.get((session_id, user_id, config_signature))
        return dict(doc) if doc else None

    async def save(self, session_id, user_id, config_signature, payload):
        self.docs[(session_id, user_id, config_signature)] = dict(payload)
        return dict(payload)


def _fake_preview_input_schema():
    return {"type": "object", "properties": {"cookies": {"type": "array"}}, "required": ["cookies"]}


def _build_rpa_mcp_app():
    app = FastAPI()
    app.include_router(rpa_mcp_route.router, prefix="/api/v1")
    app.dependency_overrides[rpa_mcp_route.require_user] = lambda: _User()
    return app


def _fake_steps(session_id: str, user_id: str):
    assert session_id == "session-1"
    assert user_id == "user-1"
    return {
        "steps": [{"action": "click", "description": "Export invoice", "url": "https://example.com/dashboard"}],
        "params": {},
        "skill_name": "invoice_skill",
    }


def test_get_rpa_session_steps_preserves_ai_traces_with_recorded_actions(monkeypatch):
    session = RPASession(id="session-1", user_id="user-1", sandbox_session_id="sandbox-1")
    session.recorded_actions.append(
        ManualRecordedAction(
            step_id="step-search",
            action_kind=ManualActionKind.CLICK,
            description='click button("Search")',
            target={"method": "role", "role": "button", "name": "Search"},
            validation={"status": "ok"},
            page_state={"url": "https://example.com/search"},
        )
    )
    session.traces.extend(
        [
            RPAAcceptedTrace(
                trace_id="trace-step-search",
                trace_type=RPATraceType.MANUAL_ACTION,
                source="manual",
                action="click",
                description="legacy manual trace",
            ),
            RPAAcceptedTrace(
                trace_id="trace-ai-select",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="click the first project",
                description="Click first project",
                ai_execution=RPAAIExecution(
                    code="async def run(page, results):\n    return {'url': 'https://example.com/repo'}",
                ),
            ),
        ]
    )

    async def fake_get_session(session_id):
        assert session_id == "session-1"
        return session

    monkeypatch.setattr(rpa_mcp_route.rpa_manager, "get_session", fake_get_session)

    payload = anyio.run(rpa_mcp_route.get_rpa_session_steps, "session-1", "user-1")

    assert [step["description"] for step in payload["steps"]] == [
        'click button("Search")',
        "Click first project",
    ]
    assert payload["steps"][0]["rpa_trace"]["trace_id"] == "trace-step-search"
    assert payload["steps"][1]["source"] == "ai"
    assert payload["steps"][1]["action"] == "ai_script"
    assert payload["steps"][1]["rpa_trace"]["trace_type"] == "ai_operation"


class _FakeConverter:
    async def preview_with_semantics(self, **kwargs):
        return self.preview(**kwargs)

    def preview(self, **kwargs):
        return SimpleNamespace(model_dump=lambda mode='python': {
            "id": "preview",
            "name": kwargs["name"],
            "tool_name": "rpa_download_invoice",
            "description": kwargs["description"],
            "allowed_domains": ["example.com"],
            "post_auth_start_url": "https://example.com/dashboard",
            "steps": kwargs["steps"],
            "params": kwargs["params"],
            "input_schema": _fake_preview_input_schema(),
            "output_schema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string"},
                    "data": {"type": "object", "properties": {}, "additionalProperties": True},
                    "downloads": {"type": "array", "items": {"type": "object"}},
                    "artifacts": {"type": "array", "items": {"type": "object"}},
                    "error": {"type": ["object", "null"]},
                },
                "required": ["success", "data", "downloads", "artifacts"],
            },
            "recommended_output_schema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string"},
                    "data": {"type": "object", "properties": {"invoice_total": {"type": "string"}}, "additionalProperties": False},
                    "downloads": {"type": "array", "items": {"type": "object"}},
                    "artifacts": {"type": "array", "items": {"type": "object"}},
                    "error": {"type": ["object", "null"]},
                },
                "required": ["success", "data", "downloads", "artifacts"],
            },
            "output_schema_confirmed": False,
            "output_examples": [],
            "output_inference_report": {"recording_signals": [{"kind": "extract_text", "key": "invoice_total"}]},
            "sanitize_report": {"removed_steps": [0, 1, 2], "removed_params": ["email", "password"], "warnings": []},
            "source": {"type": "rpa_skill", "session_id": kwargs["session_id"], "skill_name": kwargs["skill_name"]},
            "enabled": True,
        })

    def infer_output_from_execution(self, tool, result):
        return (
            {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string"},
                    "data": {
                        "type": "object",
                        "properties": {
                            "browser": {"type": "object"},
                            "arguments": {"type": "object"},
                            "has_pw_loop_runner": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    "downloads": {"type": "array", "items": {"type": "object"}},
                    "artifacts": {"type": "array", "items": {"type": "object"}},
                    "error": {"type": ["object", "null"]},
                },
                "required": ["success", "data", "downloads", "artifacts"],
            },
            {"test_result_keys": ["arguments", "browser", "has_pw_loop_runner"]},
        )


class _FakeRouteExecutor:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def execute(self, tool, arguments):
        browser = await self.kwargs["browser_factory"](tool=tool)
        return {
            "success": True,
            "message": "Execution completed",
            "data": {
                "browser": browser,
                "arguments": arguments,
                "has_pw_loop_runner": self.kwargs.get("pw_loop_runner") is not None,
            },
            "downloads": [{"filename": "invoice.pdf", "path": "D:/tmp/invoice.pdf"}],
            "artifacts": [],
            "error": None,
        }


class _FakeConnector:
    def __init__(self):
        self.calls = []

    async def get_browser(self, session_id=None, user_id=None):
        self.calls.append({"session_id": session_id, "user_id": user_id})
        return {"session_id": session_id, "user_id": user_id}

    async def run_in_pw_loop(self, coro):
        return await coro


def _sample_tool():
    return rpa_mcp_route.RpaMcpToolDefinition(
        id="tool-1",
        user_id="user-1",
        name="download_invoice",
        tool_name="rpa_download_invoice",
        description="Download invoice",
        requires_cookies=False,
        allowed_domains=["example.com"],
        post_auth_start_url="https://example.com/dashboard",
        steps=[{"action": "click", "description": "Export invoice"}],
        params={},
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"},
                "data": {"type": "object", "properties": {}, "additionalProperties": True},
                "downloads": {"type": "array", "items": {"type": "object"}},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "error": {"type": ["object", "null"]},
            },
            "required": ["success", "data", "downloads", "artifacts"],
        },
        recommended_output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"},
                "data": {"type": "object", "properties": {"invoice_total": {"type": "string"}}, "additionalProperties": False},
                "downloads": {"type": "array", "items": {"type": "object"}},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "error": {"type": ["object", "null"]},
            },
            "required": ["success", "data", "downloads", "artifacts"],
        },
        output_schema_confirmed=False,
        output_examples=[],
        output_inference_report={},
        sanitize_report={"removed_steps": [], "removed_params": [], "warnings": []},
        source={"type": "rpa_skill", "session_id": "session-1", "skill_name": "invoice_skill"},
    )


def _fake_gateway_tools(user_id: str):
    assert user_id == "user-1"
    return [
        {
            "name": "rpa_download_invoice",
            "description": "Download invoice",
            "input_schema": {"type": "object", "properties": {"cookies": {"type": "array"}}, "required": ["cookies"]},
            "output_schema": {"type": "object", "properties": {"data": {"type": "object"}}},
        }
    ]


def test_preview_route_returns_sanitize_report(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    draft_registry = _FakePreviewTestRegistry()

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: _FakeConverter())
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: draft_registry)

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/preview",
        json={"name": "download_invoice", "description": "Download invoice"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["sanitize_report"]["removed_steps"] == [0, 1, 2]


def test_gateway_discover_tools_returns_enabled_user_tools(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    monkeypatch.setattr(rpa_mcp_route, "_build_gateway_tools", _fake_gateway_tools)

    response = client.post("/api/v1/rpa-mcp/mcp", json={"method": "tools/list", "params": {}})

    assert response.status_code == 200
    assert response.json()["result"]["tools"][0]["name"] == "rpa_download_invoice"
    assert response.json()["result"]["tools"][0]["output_schema"]["properties"]["data"]["type"] == "object"


def test_test_tool_route_builds_runtime_executor(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    tool = _sample_tool()
    connector = _FakeConnector()

    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: _FakeRegistry(tool))
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpExecutor", lambda **kwargs: _FakeRouteExecutor(**kwargs))
    monkeypatch.setattr(rpa_mcp_route, "get_cdp_connector", lambda: connector)
    async def fake_get_session(session_id):
        return SimpleNamespace(id=session_id, sandbox_session_id="sandbox-1")

    monkeypatch.setattr(rpa_mcp_route.rpa_manager, "get_session", fake_get_session)

    response = client.post(
        "/api/v1/rpa-mcp/tools/tool-1/test",
        json={"arguments": {"month": "2026-03"}},
    )

    assert response.status_code == 200
    data = response.json()["data"]["data"]
    assert data["browser"] == {"session_id": "sandbox-1", "user_id": "user-1"}
    assert data["arguments"] == {"cookies": [], "month": "2026-03"}
    assert data["has_pw_loop_runner"] is True
    assert "recommended_output_schema" not in response.json()["data"]
    assert "output_examples" not in response.json()["data"]


def test_gateway_call_builds_runtime_executor(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    tool = _sample_tool()
    connector = _FakeConnector()

    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: _FakeRegistry(tool))
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpExecutor", lambda **kwargs: _FakeRouteExecutor(**kwargs))
    monkeypatch.setattr(rpa_mcp_route, "get_cdp_connector", lambda: connector)
    async def fake_get_session_call(session_id):
        return SimpleNamespace(id=session_id, sandbox_session_id="sandbox-1")

    monkeypatch.setattr(rpa_mcp_route.rpa_manager, "get_session", fake_get_session_call)

    response = client.post(
        "/api/v1/rpa-mcp/mcp",
        json={"method": "tools/call", "params": {"name": "rpa_download_invoice", "arguments": {"month": "2026-03"}}},
    )

    assert response.status_code == 200
    structured = response.json()["result"]["structuredContent"]["data"]
    assert structured["browser"] == {"session_id": "sandbox-1", "user_id": "user-1"}
    assert structured["arguments"] == {"month": "2026-03"}
    assert structured["has_pw_loop_runner"] is True
    assert response.json()["result"]["outputSchema"]["properties"]["data"]["type"] == "object"


def test_gateway_supports_streamable_http_mcp_sdk(monkeypatch):
    app = _build_rpa_mcp_app()
    tool = _sample_tool()
    connector = _FakeConnector()

    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: _FakeRegistry(tool))
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpExecutor", lambda **kwargs: _FakeRouteExecutor(**kwargs))
    monkeypatch.setattr(rpa_mcp_route, "get_cdp_connector", lambda: connector)

    async def fake_get_session(session_id):
        return SimpleNamespace(id=session_id, sandbox_session_id="sandbox-1")

    monkeypatch.setattr(rpa_mcp_route.rpa_manager, "get_session", fake_get_session)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            async with streamable_http_client(
                "http://testserver/api/v1/rpa-mcp/mcp",
                http_client=http_client,
                terminate_on_close=False,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool("rpa_download_invoice", {"month": "2026-03"})
        return tools, result

    tools, result = anyio.run(scenario)

    assert tools.tools[0].name == "rpa_download_invoice"
    assert tools.tools[0].inputSchema == {"type": "object", "properties": {}, "required": []}
    assert result.structuredContent["data"]["browser"] == {"session_id": "sandbox-1", "user_id": "user-1"}
    assert result.structuredContent["data"]["arguments"] == {"month": "2026-03"}
    assert result.isError is False


def test_update_tool_route_persists_confirmed_output_schema(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    tool = _sample_tool()
    registry = _FakeRegistry(tool)

    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: registry)

    new_schema = {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "message": {"type": "string"},
            "data": {"type": "object", "properties": {"invoice_total": {"type": "string"}}, "additionalProperties": False},
            "downloads": {"type": "array", "items": {"type": "object"}},
            "artifacts": {"type": "array", "items": {"type": "object"}},
            "error": {"type": ["object", "null"]},
        },
        "required": ["success", "data", "downloads", "artifacts"],
    }

    response = client.put(
        "/api/v1/rpa-mcp/tools/tool-1",
        json={"output_schema": new_schema, "output_schema_confirmed": True},
    )

    assert response.status_code == 200
    assert response.json()["data"]["output_schema_confirmed"] is True
    assert registry.tool.output_schema["properties"]["data"]["properties"]["invoice_total"]["type"] == "string"


def test_update_tool_route_allows_clearing_optional_metadata(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    tool = _sample_tool()
    registry = _FakeRegistry(tool)

    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: registry)

    response = client.put(
        "/api/v1/rpa-mcp/tools/tool-1",
        json={
            "description": "",
            "allowed_domains": [],
            "post_auth_start_url": "",
            "enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["description"] == ""
    assert response.json()["data"]["allowed_domains"] == []
    assert response.json()["data"]["post_auth_start_url"] == ""


def test_execution_plan_route_returns_compiled_script(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    tool = _sample_tool()

    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: _FakeRegistry(tool))
    monkeypatch.setattr(
        rpa_mcp_route,
        "build_rpa_mcp_execution_plan",
        lambda tool: {
            "tool_id": tool.id,
            "generated_at": "2026-04-24T12:00:00+08:00",
            "requires_cookies": tool.requires_cookies,
            "compiled_steps": tool.steps,
            "compiled_script": "async def run(page):\n    await page.click('text=Export invoice')\n",
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "source_hash": "hash-1",
        },
    )

    response = client.get("/api/v1/rpa-mcp/tools/tool-1/execution-plan")

    assert response.status_code == 200
    assert response.json()["data"]["tool_id"] == "tool-1"
    assert "await page.click" in response.json()["data"]["compiled_script"]


def test_build_execution_plan_serializes_datetime_values():
    tool = _sample_tool()
    tool.steps = [{
        "action": "click",
        "description": "Export invoice",
        "captured_at": datetime(2026, 4, 24, 9, 50, 0),
    }]
    tool.params = {
        "from_date": {
            "type": "string",
            "last_seen_at": datetime(2026, 4, 24, 9, 51, 0),
        },
    }

    payload = build_rpa_mcp_execution_plan(tool)

    assert payload["tool_id"] == "tool-1"
    assert payload["source_hash"]
    assert payload["compiled_script"]
    assert payload["compiled_steps"][0]["captured_at"] == datetime(2026, 4, 24, 9, 50, 0)


def test_build_execution_plan_uses_trace_compiler_for_trace_backed_steps():
    tool = _sample_tool()
    tool.steps = [
        {
            "id": "trace-ai-select",
            "action": "ai_script",
            "description": "Click first project",
            "source": "ai",
            "rpa_trace": {
                "trace_id": "trace-ai-select",
                "trace_type": "ai_operation",
                "source": "ai",
                "user_instruction": "click the first project",
                "description": "Click first project",
                "ai_execution": {
                    "language": "python",
                    "code": "async def run(page, results):\n    return {'url': 'https://example.com/repo'}",
                },
            },
        }
    ]

    payload = build_rpa_mcp_execution_plan(tool)

    assert "Auto-generated skill from RPA trace recording" in payload["compiled_script"]
    assert "Click first project" in payload["compiled_script"]


def test_preview_test_route_returns_execution_result_and_updates_preview_draft(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    connector = _FakeConnector()
    draft_registry = _FakePreviewTestRegistry()

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: _FakeConverter())
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpExecutor", lambda **kwargs: _FakeRouteExecutor(**kwargs))
    monkeypatch.setattr(rpa_mcp_route, "get_cdp_connector", lambda: connector)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: draft_registry)

    async def fake_get_session(session_id):
        return SimpleNamespace(id=session_id, sandbox_session_id="sandbox-1", user_id="user-1")

    monkeypatch.setattr(rpa_mcp_route.rpa_manager, "get_session", fake_get_session)

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/test-preview",
        json={
            "name": "download_invoice",
            "description": "Download invoice",
            "arguments": {"month": "2026-03"},
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["data"]["browser"] == {"session_id": "sandbox-1", "user_id": "user-1"}
    assert "recommended_output_schema" not in response.json()["data"]
    stored = next(iter(draft_registry.docs.values()))
    assert stored["recommended_output_schema"]["properties"]["data"]["properties"]["browser"]["type"] == "object"
    assert stored["test_result"]["data"]["arguments"]["month"] == "2026-03"


def test_preview_route_surfaces_latest_inferred_schema_for_matching_draft(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    draft_registry = _FakePreviewTestRegistry()
    converter = _FakeConverter()

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: converter)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: draft_registry)

    config_signature = rpa_mcp_route._preview_config_signature(
        session_id="session-1",
        user_id="user-1",
        name="download_invoice",
        description="Download invoice",
        allowed_domains=["example.com"],
        post_auth_start_url="https://example.com/dashboard",
        input_schema=_fake_preview_input_schema(),
        params={},
    )
    draft_registry.docs[("session-1", "user-1", config_signature)] = {
        "recommended_output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"},
                "data": {"type": "object", "properties": {"browser": {"type": "object"}}, "additionalProperties": False},
                "downloads": {"type": "array", "items": {"type": "object"}},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "error": {"type": ["object", "null"]},
            },
            "required": ["success", "data", "downloads", "artifacts"],
        },
        "output_examples": [{"success": True}],
        "output_inference_report": {"test_result_keys": ["browser"]},
        "tested": True,
    }

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/preview",
        json={
            "name": "download_invoice",
            "description": "Download invoice",
            "allowed_domains": ["example.com"],
            "post_auth_start_url": "https://example.com/dashboard",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["recommended_output_schema"]["properties"]["data"]["properties"]["browser"]["type"] == "object"
    assert response.json()["data"]["output_examples"] == [{"success": True}]


def test_preview_payload_applies_user_confirmed_input_schema_for_preview_tests(monkeypatch):
    converter = _FakeConverter()
    draft_registry = _FakePreviewTestRegistry()

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: converter)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: draft_registry)

    body = rpa_mcp_route.PreviewTestRequest(
        name="download_invoice",
        description="Download invoice",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Confirmed query"}},
            "required": ["query"],
        },
        params={"query": {"original_value": "cancer", "description": "Confirmed query", "required": True}},
        schema_source="user_edited",
    )

    preview = anyio.run(rpa_mcp_route._preview_payload, "session-1", "user-1", body)

    assert preview.schema_source == "user_edited"
    assert preview.input_schema["properties"]["query"]["description"] == "Confirmed query"
    assert preview.params["query"]["original_value"] == "cancer"


def test_create_tool_requires_matching_successful_preview_test(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    converter = _FakeConverter()
    registry = _FakeRegistry(_sample_tool())
    draft_registry = _FakePreviewTestRegistry()

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: converter)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: registry)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: draft_registry)

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/tools",
        json={"name": "download_invoice", "description": "Download invoice"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "A successful preview test is required before saving the tool"


def test_create_tool_requires_retest_when_confirmed_input_schema_changes(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    converter = _FakeConverter()
    registry = _FakeRegistry(_sample_tool())
    draft_registry = _FakePreviewTestRegistry()

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: converter)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: registry)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: draft_registry)

    old_signature = rpa_mcp_route._preview_config_signature(
        session_id="session-1",
        user_id="user-1",
        name="download_invoice",
        description="Download invoice",
        allowed_domains=["example.com"],
        post_auth_start_url="https://example.com/dashboard",
        input_schema=_fake_preview_input_schema(),
        params={},
    )
    draft_registry.docs[("session-1", "user-1", old_signature)] = {
        "recommended_output_schema": {"type": "object", "properties": {"data": {"type": "object"}}},
        "output_examples": [{"success": True}],
        "output_inference_report": {"test_result_keys": []},
        "tested": True,
    }

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/tools",
        json={
            "name": "download_invoice",
            "description": "Download invoice",
            "allowed_domains": ["example.com"],
            "post_auth_start_url": "https://example.com/dashboard",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Confirmed query"}},
                "required": ["query"],
            },
            "params": {"query": {"original_value": "cancer", "type": "string", "description": "Confirmed query"}},
            "schema_source": "user_edited",
            "output_schema": {"type": "object", "properties": {"data": {"type": "object"}}},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "A successful preview test is required before saving the tool"


def test_create_tool_uses_successful_preview_test_artifacts(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    converter = _FakeConverter()
    tool = _sample_tool()
    registry = _FakeRegistry(tool)
    draft_registry = _FakePreviewTestRegistry()

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: converter)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: registry)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: draft_registry)

    config_signature = rpa_mcp_route._preview_config_signature(
        session_id="session-1",
        user_id="user-1",
        name="download_invoice",
        description="Download invoice",
        allowed_domains=["example.com"],
        post_auth_start_url="https://example.com/dashboard",
        input_schema=_fake_preview_input_schema(),
        params={},
    )
    draft_registry.docs[("session-1", "user-1", config_signature)] = {
        "recommended_output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"},
                "data": {"type": "object", "properties": {"browser": {"type": "object"}}, "additionalProperties": False},
                "downloads": {"type": "array", "items": {"type": "object"}},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "error": {"type": ["object", "null"]},
            },
            "required": ["success", "data", "downloads", "artifacts"],
        },
        "output_examples": [{"success": True, "data": {"browser": {"session_id": "sandbox-1"}}}],
        "output_inference_report": {"test_result_keys": ["browser"]},
        "tested": True,
    }

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/tools",
        json={
            "name": "download_invoice",
            "description": "Download invoice",
            "allowed_domains": ["example.com"],
            "post_auth_start_url": "https://example.com/dashboard",
            "output_schema": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string"},
                    "data": {"type": "object", "properties": {"browser": {"type": "object"}, "arguments": {"type": "object"}}, "additionalProperties": False},
                    "downloads": {"type": "array", "items": {"type": "object"}},
                    "artifacts": {"type": "array", "items": {"type": "object"}},
                    "error": {"type": ["object", "null"]},
                },
                "required": ["success", "data", "downloads", "artifacts"],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["output_schema_confirmed"] is True
    assert registry.tool.output_schema["properties"]["data"]["properties"]["arguments"]["type"] == "object"
    assert registry.tool.output_examples[0]["data"]["browser"]["session_id"] == "sandbox-1"


def test_create_rpa_mcp_tool_preserves_user_edited_input_schema(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    saved = {}

    async def fake_preview_payload(session_id, user_id, body):
        return rpa_mcp_route.RpaMcpToolDefinition(
            id="preview",
            user_id=user_id,
            name=body.name,
            tool_name="search_reports",
            description=body.description,
            allowed_domains=["example.com"],
            post_auth_start_url="https://example.com/search",
            steps=[],
            params={"report_keyword": {"original_value": "cancer", "type": "string", "description": "Keyword"}},
            input_schema={"type": "object", "properties": {"report_keyword": {"type": "string"}}, "required": []},
            source={"session_id": session_id, "skill_name": "search_skill"},
            schema_source="ai_inferred",
            semantic_inference={"source": "ai_inferred", "confidence": 0.8, "warnings": [], "model": "fake", "generated_at": "preview"},
        )

    class FakeDraftRegistry:
        async def get(self, *_args):
            return {"tested": True, "recommended_output_schema": {"type": "object", "properties": {}, "required": []}}

    class FakeToolRegistry:
        async def save(self, tool):
            saved["tool"] = tool
            return tool

    monkeypatch.setattr(rpa_mcp_route, "_preview_payload", fake_preview_payload)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: FakeDraftRegistry())
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: FakeToolRegistry())

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/tools",
        json={
            "name": "Search reports",
            "description": "Search reports.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "User-edited search query."},
                },
                "required": ["query"],
            },
            "params": {
                "query": {
                    "original_value": "cancer",
                    "type": "string",
                    "description": "User-edited search query.",
                    "required": True,
                },
            },
            "schema_source": "user_edited",
            "output_schema": {"type": "object", "properties": {}, "required": []},
        },
    )

    assert response.status_code == 200
    assert saved["tool"].schema_source == "user_edited"
    assert "query" in saved["tool"].input_schema["properties"]
    assert "report_keyword" not in saved["tool"].input_schema["properties"]


def test_create_tool_allows_name_and_description_changes_after_successful_preview_test(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    converter = _FakeConverter()
    tool = _sample_tool()
    registry = _FakeRegistry(tool)
    draft_registry = _FakePreviewTestRegistry()

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: converter)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpToolRegistry", lambda: registry)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpPreviewDraftRegistry", lambda: draft_registry)

    config_signature = rpa_mcp_route._preview_config_signature(
        session_id="session-1",
        user_id="user-1",
        name="download_invoice",
        description="Download invoice",
        allowed_domains=["example.com"],
        post_auth_start_url="https://example.com/dashboard",
        input_schema=_fake_preview_input_schema(),
        params={},
    )
    draft_registry.docs[("session-1", "user-1", config_signature)] = {
        "recommended_output_schema": {"type": "object", "properties": {"data": {"type": "object"}}},
        "output_examples": [{"success": True}],
        "output_inference_report": {"test_result_keys": ["browser"]},
        "tested": True,
    }

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/tools",
        json={
            "name": "download_invoice_v2",
            "description": "Download invoice with better copy",
            "allowed_domains": ["example.com"],
            "post_auth_start_url": "https://example.com/dashboard",
            "output_schema": {"type": "object", "properties": {"data": {"type": "object"}}},
        },
    )

    assert response.status_code == 200
