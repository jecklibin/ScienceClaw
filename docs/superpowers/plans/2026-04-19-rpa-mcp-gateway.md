# RPA MCP Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a centralized HTTP RPA MCP Gateway that converts recorded RPA sessions into cookie-authorized MCP tools, strips login steps, and exposes enabled tools through one MCP endpoint.

**Architecture:** Add a new backend RPA MCP layer that stores sanitized tool definitions, previews conversion from recorded sessions, validates cookie-scoped execution, and serves enabled tools through a single streamable HTTP MCP gateway. Reuse the existing RPA generator, credential-sensitive step metadata, storage abstraction, and MCP route/runtime patterns instead of introducing per-tool services.

**Tech Stack:** FastAPI, Python 3.13, Pydantic v2, existing storage repositories, Playwright, MCP Python SDK/runtime patterns, Vue 3 + TypeScript, Vite, existing `TestClient` and `unittest`/`pytest` backend tests.

---

## File Map

### Backend

- Create: `RpaClaw/backend/rpa/mcp_models.py`
  - Pydantic models for conversion preview, sanitized tool docs, cookie payloads, and test execution requests.
- Create: `RpaClaw/backend/rpa/mcp_registry.py`
  - Storage access for `rpa_mcp_tools` and helpers for list/get/create/update/delete enabled tools.
- Create: `RpaClaw/backend/rpa/mcp_converter.py`
  - Session step normalization, login step detection, sanitize report generation, schema building, and save payload assembly.
- Create: `RpaClaw/backend/rpa/mcp_executor.py`
  - Cookie validation, allowed-domain enforcement, isolated Playwright context execution, and result shaping.
- Create: `RpaClaw/backend/route/rpa_mcp.py`
  - Preview/save/list/update/delete/test routes plus the MCP gateway endpoint.
- Modify: `RpaClaw/backend/main.py`
  - Register the new router.
- Modify: `RpaClaw/backend/storage/__init__.py`
  - Register the `rpa_mcp_tools` repository in local mode.
- Modify: `mcp_servers.yaml`
  - Add the centralized `rpa_gateway` MCP server definition.

### Backend Tests

- Create: `RpaClaw/backend/tests/test_rpa_mcp_converter.py`
  - Converter/login-step detection/schema tests.
- Create: `RpaClaw/backend/tests/test_rpa_mcp_executor.py`
  - Cookie validation/domain enforcement/execution ordering tests.
- Create: `RpaClaw/backend/tests/test_rpa_mcp_route.py`
  - Preview/save/list/update/delete/test-route and MCP tool discovery tests.

### Frontend

- Create: `RpaClaw/frontend/src/api/rpaMcp.ts`
  - API wrapper for preview/save/list/update/delete/test calls.
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`
  - Add 鈥渃onvert to MCP tool鈥?action and handoff into conversion UI.
- Create: `RpaClaw/frontend/src/pages/rpa/McpConvertPage.vue`
  - Conversion preview editor for tool name, allowed domains, start URL, removed steps, and warnings.
- Modify: `RpaClaw/frontend/src/pages/ToolsPage.vue`
  - Add an RPA MCP Gateway section, list converted tools, show schema/sanitize report, and test invocation UI.
- Modify: `RpaClaw/frontend/src/main.ts`
  - Register the new conversion route.
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`
  - Add i18n strings for preview, gateway management, warnings, and test invocation.

## Task 1: Define backend models and storage plumbing

**Files:**
- Create: `RpaClaw/backend/rpa/mcp_models.py`
- Modify: `RpaClaw/backend/storage/__init__.py`
- Test: `RpaClaw/backend/tests/test_rpa_mcp_converter.py`

- [ ] **Step 1: Write the failing model/registry smoke test**

```python
from backend.rpa.mcp_models import RpaMcpToolDefinition


def test_rpa_mcp_tool_definition_defaults():
    tool = RpaMcpToolDefinition(
        id="rpa_mcp_tool_1",
        user_id="user-1",
        name="download_invoice",
        tool_name="rpa_download_invoice",
        description="Download invoice",
        allowed_domains=["example.com"],
        post_auth_start_url="https://example.com/dashboard",
        steps=[],
        params={},
        input_schema={"type": "object", "properties": {}, "required": []},
        sanitize_report={"removed_steps": [], "removed_params": [], "warnings": []},
        source={"type": "rpa_skill", "session_id": "session-1", "skill_name": "invoice_skill"},
    )

    assert tool.enabled is True
    assert tool.allowed_domains == ["example.com"]
    assert tool.sanitize_report.warnings == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_converter.py -k defaults -v
```

Expected: FAIL because `backend.rpa.mcp_models` does not exist yet.

- [ ] **Step 3: Add the models and local storage registration**

```python
# RpaClaw/backend/rpa/mcp_models.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RpaMcpSource(BaseModel):
    type: str = "rpa_skill"
    session_id: str
    skill_name: str = ""


class RpaMcpSanitizeReport(BaseModel):
    removed_steps: list[int] = Field(default_factory=list)
    removed_params: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RpaMcpToolDefinition(BaseModel):
    id: str
    user_id: str
    name: str
    tool_name: str
    description: str = ""
    enabled: bool = True
    source: RpaMcpSource
    allowed_domains: list[str] = Field(default_factory=list)
    post_auth_start_url: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    sanitize_report: RpaMcpSanitizeReport = Field(default_factory=RpaMcpSanitizeReport)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

```python
# RpaClaw/backend/storage/__init__.py
for name in (
    "users", "user_sessions", "sessions", "models",
    "skills", "blocked_tools", "task_settings", "session_events",
    "session_runtimes", "credentials", "rpa_mcp_tools",
):
    ...
```

- [ ] **Step 4: Run the targeted test to verify it passes**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_converter.py -k defaults -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/mcp_models.py RpaClaw/backend/storage/__init__.py RpaClaw/backend/tests/test_rpa_mcp_converter.py
git commit -m "feat: add rpa mcp models and storage plumbing"
```

## Task 2: Build the registry for converted tools

**Files:**
- Create: `RpaClaw/backend/rpa/mcp_registry.py`
- Test: `RpaClaw/backend/tests/test_rpa_mcp_route.py`

- [ ] **Step 1: Write the failing registry test**

```python
import pytest

from backend.rpa.mcp_registry import RpaMcpToolRegistry


@pytest.mark.asyncio
async def test_registry_lists_only_enabled_tools_for_user():
    repo = _MemoryRepo([
        {
            "_id": "tool-1",
            "user_id": "user-1",
            "name": "download_invoice",
            "tool_name": "rpa_download_invoice",
            "enabled": True,
            "source": {"type": "rpa_skill", "session_id": "session-1", "skill_name": "invoice"},
            "allowed_domains": ["example.com"],
            "post_auth_start_url": "https://example.com/dashboard",
            "steps": [],
            "params": {},
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "sanitize_report": {"removed_steps": [], "removed_params": [], "warnings": []},
        },
        {
            "_id": "tool-2",
            "user_id": "user-1",
            "name": "disabled_invoice",
            "tool_name": "rpa_disabled_invoice",
            "enabled": False,
            "source": {"type": "rpa_skill", "session_id": "session-2", "skill_name": "invoice"},
            "allowed_domains": ["example.com"],
            "post_auth_start_url": "https://example.com/dashboard",
            "steps": [],
            "params": {},
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "sanitize_report": {"removed_steps": [], "removed_params": [], "warnings": []},
        },
    ])
    registry = RpaMcpToolRegistry(repository=repo)

    tools = await registry.list_enabled_for_user("user-1")

    assert [tool.tool_name for tool in tools] == ["rpa_download_invoice"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_route.py -k registry_lists_only_enabled_tools_for_user -v
```

Expected: FAIL because `backend.rpa.mcp_registry` does not exist yet.

- [ ] **Step 3: Implement the registry**

```python
# RpaClaw/backend/rpa/mcp_registry.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.rpa.mcp_models import RpaMcpToolDefinition
from backend.storage import get_repository


class RpaMcpToolRegistry:
    def __init__(self, repository=None) -> None:
        self._repo = repository or get_repository("rpa_mcp_tools")

    async def list_for_user(self, user_id: str) -> list[RpaMcpToolDefinition]:
        docs = await self._repo.find_many({"user_id": user_id}, sort=[("updated_at", -1)])
        return [self._coerce(doc) for doc in docs]

    async def list_enabled_for_user(self, user_id: str) -> list[RpaMcpToolDefinition]:
        docs = await self._repo.find_many({"user_id": user_id, "enabled": True}, sort=[("updated_at", -1)])
        return [self._coerce(doc) for doc in docs]

    async def get_owned(self, tool_id: str, user_id: str) -> RpaMcpToolDefinition | None:
        doc = await self._repo.find_one({"_id": tool_id, "user_id": user_id})
        return self._coerce(doc) if doc else None

    async def save(self, tool: RpaMcpToolDefinition) -> RpaMcpToolDefinition:
        payload = tool.model_dump(mode="python")
        payload["_id"] = payload.pop("id")
        payload["updated_at"] = datetime.now()
        await self._repo.update_one(
            {"_id": payload["_id"], "user_id": tool.user_id},
            {"$set": payload, "$setOnInsert": {"created_at": payload["updated_at"]}},
            upsert=True,
        )
        return self._coerce(payload)

    def _coerce(self, doc: dict[str, Any] | None) -> RpaMcpToolDefinition | None:
        if not doc:
            return None
        payload = dict(doc)
        payload["id"] = str(payload.pop("_id", payload.get("id", "")))
        return RpaMcpToolDefinition(**payload)
```

- [ ] **Step 4: Run the targeted test to verify it passes**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_route.py -k registry_lists_only_enabled_tools_for_user -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/mcp_registry.py RpaClaw/backend/tests/test_rpa_mcp_route.py
git commit -m "feat: add rpa mcp tool registry"
```

## Task 3: Implement converter preview and sanitize logic

**Files:**
- Create: `RpaClaw/backend/rpa/mcp_converter.py`
- Test: `RpaClaw/backend/tests/test_rpa_mcp_converter.py`

- [ ] **Step 1: Write the failing converter tests**

```python
def test_preview_strips_login_steps_and_sensitive_params():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/login", "description": "Open login"},
        {"action": "fill", "target": "{\"method\":\"label\",\"value\":\"Email\"}", "value": "alice@example.com", "description": "Fill email"},
        {"action": "fill", "target": "{\"method\":\"label\",\"value\":\"Password\"}", "value": "{{credential}}", "description": "Fill password", "sensitive": True},
        {"action": "click", "target": "{\"method\":\"role\",\"role\":\"button\",\"name\":\"Sign in\"}", "description": "Sign in"},
        {"action": "navigate", "url": "https://example.com/dashboard", "description": "Open dashboard"},
        {"action": "click", "target": "{\"method\":\"role\",\"role\":\"button\",\"name\":\"Export\"}", "description": "Export invoice"},
    ]
    params = {
        "email": {"original_value": "alice@example.com"},
        "password": {"original_value": "{{credential}}", "sensitive": True, "credential_id": "cred-1"},
        "month": {"original_value": "2026-03", "description": "Invoice month"},
    }

    preview = converter.preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="invoice_skill",
        name="download_invoice",
        description="Download invoice",
        steps=steps,
        params=params,
    )

    assert preview.post_auth_start_url == "https://example.com/dashboard"
    assert preview.allowed_domains == ["example.com"]
    assert preview.sanitize_report.removed_params == ["email", "password"]
    assert [step["description"] for step in preview.steps] == ["Open dashboard", "Export invoice"]
    assert "cookies" in preview.input_schema["required"]
    assert "password" not in preview.input_schema["properties"]
```

```python
def test_preview_adds_warning_when_login_range_is_ambiguous():
    converter = RpaMcpConverter()
    steps = [
        {"action": "click", "target": "{\"method\":\"role\",\"role\":\"button\",\"name\":\"Continue\"}", "description": "Continue"},
        {"action": "navigate", "url": "https://example.com/workspace", "description": "Open workspace"},
    ]

    preview = converter.preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="skill",
        name="workspace_tool",
        description="Workspace tool",
        steps=steps,
        params={},
    )

    assert preview.sanitize_report.warnings
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_converter.py -k "preview_strips_login_steps or preview_adds_warning" -v
```

Expected: FAIL because `RpaMcpConverter` does not exist.

- [ ] **Step 3: Implement the converter**

```python
# RpaClaw/backend/rpa/mcp_converter.py
from __future__ import annotations

from urllib.parse import urlparse

from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.mcp_models import (
    RpaMcpSource,
    RpaMcpToolDefinition,
)


class RpaMcpConverter:
    def __init__(self) -> None:
        self._generator = PlaywrightGenerator()

    def preview(self, *, user_id: str, session_id: str, skill_name: str, name: str, description: str, steps: list[dict], params: dict) -> RpaMcpToolDefinition:
        normalized = self._generator._normalize_step_signals(
            self._generator._infer_missing_tab_transitions(
                self._generator._deduplicate_steps(steps)
            )
        )
        login_range = self._detect_login_range(normalized)
        sanitized_steps, report = self._strip_login_steps(normalized, login_range)
        allowed_domains = self._collect_domains(normalized)
        post_auth_start_url = self._pick_post_auth_start_url(normalized, sanitized_steps)
        sanitized_params = self._strip_login_params(params, report)
        input_schema = self._build_input_schema(sanitized_params)
        return RpaMcpToolDefinition(
            id="preview",
            user_id=user_id,
            name=name,
            tool_name=self._tool_name(name),
            description=description,
            source=RpaMcpSource(session_id=session_id, skill_name=skill_name),
            allowed_domains=allowed_domains,
            post_auth_start_url=post_auth_start_url,
            steps=sanitized_steps,
            params=sanitized_params,
            input_schema=input_schema,
            sanitize_report=report,
        )
```

Implementation details to include in the file:

- `_detect_login_range()` should treat `sensitive=true`, `{{credential}}`, labels matching `password/密码`, and buttons matching `login/sign in/登录` as strong login signals.
- `_strip_login_steps()` should remove the detected contiguous range and emit a warning if the range is missing or uncertain.
- `_strip_login_params()` should remove keys whose original values appear in removed login fill steps, plus sensitive/credential-bound params.
- `_collect_domains()` should gather unique URL hosts from recorded steps and return the registrable host strings used in `allowed_domains`.
- `_build_input_schema()` must always inject:

```python
"cookies": {
    "type": "array",
    "description": "Playwright-compatible cookies for allowed domains",
}
```

and mark `"cookies"` as required.

- [ ] **Step 4: Run the converter test file to verify it passes**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_converter.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/mcp_converter.py RpaClaw/backend/tests/test_rpa_mcp_converter.py
git commit -m "feat: add rpa mcp conversion preview"
```

## Task 4: Implement executor cookie validation and isolated execution

**Files:**
- Create: `RpaClaw/backend/rpa/mcp_executor.py`
- Test: `RpaClaw/backend/tests/test_rpa_mcp_executor.py`

- [ ] **Step 1: Write the failing executor tests**

```python
import pytest

from backend.rpa.mcp_executor import RpaMcpExecutor, InvalidCookieError


def test_validate_cookies_rejects_disallowed_domain():
    executor = RpaMcpExecutor()

    with pytest.raises(InvalidCookieError):
        executor.validate_cookies(
            cookies=[{"name": "sessionid", "value": "secret", "domain": ".other.com", "path": "/"}],
            allowed_domains=["example.com"],
            post_auth_start_url="https://example.com/dashboard",
        )
```

```python
@pytest.mark.asyncio
async def test_execute_adds_cookies_before_goto():
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    executor = RpaMcpExecutor(browser_factory=lambda *_args, **_kwargs: browser, script_runner=_fake_runner)

    tool = _sample_tool()
    await executor.execute(tool, {"cookies": [{"name": "sessionid", "value": "secret", "domain": ".example.com", "path": "/"}], "month": "2026-03"})

    assert context.calls[:2] == [
        ("add_cookies", [{"name": "sessionid", "value": "secret", "domain": ".example.com", "path": "/"}]),
        ("new_page", None),
    ]
    assert page.calls[0] == ("goto", "https://example.com/dashboard")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_executor.py -v
```

Expected: FAIL because `backend.rpa.mcp_executor` does not exist.

- [ ] **Step 3: Implement the executor**

```python
# RpaClaw/backend/rpa/mcp_executor.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from backend.rpa.generator import PlaywrightGenerator


class InvalidCookieError(ValueError):
    pass


@dataclass
class ExecutionResult:
    success: bool
    data: dict[str, Any]


class RpaMcpExecutor:
    def __init__(self, *, browser_factory=None, script_runner=None) -> None:
        self._browser_factory = browser_factory
        self._script_runner = script_runner
        self._generator = PlaywrightGenerator()

    def validate_cookies(self, *, cookies: list[dict[str, Any]], allowed_domains: list[str], post_auth_start_url: str) -> list[dict[str, Any]]:
        if not isinstance(cookies, list) or not cookies:
            raise InvalidCookieError("cookies must be a non-empty array")
        allowed = {domain.lstrip(".").lower() for domain in allowed_domains}
        target_host = urlparse(post_auth_start_url).hostname or ""
        for item in cookies:
            if not item.get("name") or not item.get("value"):
                raise InvalidCookieError("each cookie requires name and value")
            domain = str(item.get("domain") or urlparse(str(item.get("url") or "")).hostname or "").lstrip(".").lower()
            if not domain:
                raise InvalidCookieError("each cookie requires domain or url")
            if domain not in allowed and not any(domain.endswith(f".{value}") for value in allowed):
                raise InvalidCookieError("cookie domain is not allowed")
        if target_host and allowed and target_host not in allowed and not any(target_host.endswith(f".{value}") for value in allowed):
            raise InvalidCookieError("post-auth start URL is not within allowed domains")
        return cookies
```

Implementation details to include in the file:

- Build execution kwargs from all tool params except `cookies`.
- Generate script from sanitized steps with `is_local=(settings.storage_backend == "local")`.
- Create a fresh context, `await context.add_cookies(cookies)`, then `page = await context.new_page()`, then `await page.goto(tool.post_auth_start_url)`.
- Pass the page and kwargs into the existing execution path, and always close the context in `finally`.
- Log cookie count and domains only; never log cookie values.

- [ ] **Step 4: Run the executor test file to verify it passes**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_executor.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/mcp_executor.py RpaClaw/backend/tests/test_rpa_mcp_executor.py
git commit -m "feat: add rpa mcp executor"
```

## Task 5: Add the route layer and MCP gateway endpoint

**Files:**
- Create: `RpaClaw/backend/route/rpa_mcp.py`
- Modify: `RpaClaw/backend/main.py`
- Modify: `mcp_servers.yaml`
- Test: `RpaClaw/backend/tests/test_rpa_mcp_route.py`

- [ ] **Step 1: Write the failing route tests**

```python
def test_preview_route_returns_sanitize_report(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)

    monkeypatch.setattr(rpa_mcp_route, "get_rpa_session_steps", _fake_steps)
    monkeypatch.setattr(rpa_mcp_route, "RpaMcpConverter", lambda: _FakeConverter())

    response = client.post(
        "/api/v1/rpa-mcp/session/session-1/preview",
        json={"name": "download_invoice", "description": "Download invoice"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["sanitize_report"]["removed_steps"] == [0, 1, 2]
```

```python
def test_gateway_discover_tools_returns_enabled_user_tools(monkeypatch):
    app = _build_rpa_mcp_app()
    client = TestClient(app)
    monkeypatch.setattr(rpa_mcp_route, "_build_gateway_tools", _fake_gateway_tools)

    response = client.post("/api/v1/rpa-mcp/mcp", json={"method": "tools/list", "params": {}})

    assert response.status_code == 200
    assert response.json()["result"]["tools"][0]["name"] == "rpa_download_invoice"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_route.py -k "preview_route_returns_sanitize_report or gateway_discover_tools_returns_enabled_user_tools" -v
```

Expected: FAIL because `backend.route.rpa_mcp` does not exist.

- [ ] **Step 3: Implement the route module**

```python
# RpaClaw/backend/route/rpa_mcp.py
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.rpa.mcp_converter import RpaMcpConverter
from backend.rpa.mcp_executor import InvalidCookieError, RpaMcpExecutor
from backend.rpa.mcp_registry import RpaMcpToolRegistry
from backend.user.dependencies import require_user, User

router = APIRouter(tags=["rpa-mcp"])


class ApiResponse(BaseModel):
    code: int = 0
    msg: str = "ok"
    data: object | None = None


class PreviewRequest(BaseModel):
    name: str
    description: str = ""


class SaveToolRequest(BaseModel):
    name: str
    description: str = ""
    allowed_domains: list[str] = Field(default_factory=list)
    post_auth_start_url: str = ""
```

Implementation details to include in the file:

- `POST /rpa-mcp/session/{session_id}/preview`
  - Load the RPA session, verify ownership, feed steps + params to `RpaMcpConverter.preview()`.
- `POST /rpa-mcp/session/{session_id}/tools`
  - Rebuild preview, apply user overrides, assign `id=f"rpa_mcp_{uuid.uuid4().hex[:12]}"`, save through registry.
- `GET /rpa-mcp/tools`
  - Return current user鈥檚 tools.
- `GET /rpa-mcp/tools/{tool_id}`, `PUT`, `DELETE`
  - Enforce ownership.
- `POST /rpa-mcp/tools/{tool_id}/test`
  - Execute with manual cookies JSON and return structured result/errors.
- `POST /rpa-mcp/mcp`
  - Support the minimum MCP gateway operations needed in phase 1:
    - `tools/list`
    - `tools/call`
- For `tools/call`, locate the tool by `name`, execute it, and return MCP-shaped structured content.

Also update:

```python
# RpaClaw/backend/main.py
from backend.route.rpa_mcp import router as rpa_mcp_router
...
app.include_router(rpa_mcp_router, prefix="/api/v1")
```

```yaml
# mcp_servers.yaml
  - id: rpa_gateway
    name: RPA MCP Gateway
    description: Converted RPA tools exposed through one central gateway
    transport: streamable_http
    enabled: true
    default_enabled: false
    url: http://localhost:12001/api/v1/rpa-mcp/mcp
```

- [ ] **Step 4: Run the route test file to verify it passes**

Run:

```bash
uv run pytest RpaClaw/backend/tests/test_rpa_mcp_route.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/route/rpa_mcp.py RpaClaw/backend/main.py mcp_servers.yaml RpaClaw/backend/tests/test_rpa_mcp_route.py
git commit -m "feat: add rpa mcp routes and gateway endpoint"
```

## Task 6: Add frontend API and conversion page

**Files:**
- Create: `RpaClaw/frontend/src/api/rpaMcp.ts`
- Create: `RpaClaw/frontend/src/pages/rpa/McpConvertPage.vue`
- Modify: `RpaClaw/frontend/src/main.ts`

- [ ] **Step 1: Use the frontend build as the failing check for route/API wiring**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS before changes, then any broken imports after wiring will fail the build.

- [ ] **Step 2: Add the frontend API wrapper**

```ts
// RpaClaw/frontend/src/api/rpaMcp.ts
import { apiClient, ApiResponse } from './client';

export interface RpaMcpPreview {
  name: string;
  tool_name: string;
  description: string;
  allowed_domains: string[];
  post_auth_start_url: string;
  steps: Record<string, unknown>[];
  params: Record<string, unknown>;
  input_schema: Record<string, unknown>;
  sanitize_report: {
    removed_steps: number[];
    removed_params: string[];
    warnings: string[];
  };
}

export interface RpaMcpToolItem extends RpaMcpPreview {
  id: string;
  enabled: boolean;
}

export async function previewRpaMcpTool(sessionId: string, payload: { name: string; description?: string }) {
  const response = await apiClient.post<ApiResponse<RpaMcpPreview>>(`/rpa-mcp/session/${encodeURIComponent(sessionId)}/preview`, payload);
  return response.data.data;
}

export async function createRpaMcpTool(sessionId: string, payload: Record<string, unknown>) {
  const response = await apiClient.post<ApiResponse<RpaMcpToolItem>>(`/rpa-mcp/session/${encodeURIComponent(sessionId)}/tools`, payload);
  return response.data.data;
}
```

- [ ] **Step 3: Add the conversion page and route**

Implement `McpConvertPage.vue` with:

- tool name and description inputs,
- preview cards for removed steps, kept steps, warnings,
- editable `post_auth_start_url`,
- editable allowed domains,
- 鈥淪ave as MCP Tool鈥?button,
- back-link to `/rpa/configure`.

Register route:

```ts
{
  path: 'convert-mcp',
  component: McpConvertPage,
}
```

- [ ] **Step 4: Run the frontend build to verify it passes**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/frontend/src/api/rpaMcp.ts RpaClaw/frontend/src/pages/rpa/McpConvertPage.vue RpaClaw/frontend/src/main.ts
git commit -m "feat: add rpa mcp conversion page"
```

## Task 7: Integrate conversion flow and gateway management into existing UI

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`
- Modify: `RpaClaw/frontend/src/pages/ToolsPage.vue`
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: Add the ConfigurePage entry point**

Add a button that routes to the conversion page using the current RPA session context:

```ts
router.push({
  path: '/rpa/convert-mcp',
  query: { sessionId: sessionId.value },
});
```

The button should only appear when session steps exist and script generation is available.

- [ ] **Step 2: Add the RPA MCP section in ToolsPage**

Extend `ToolsPage.vue` with:

- a third section under the MCP tab titled 鈥淩PA MCP Gateway鈥?
- list of converted tools from `listRpaMcpTools()`,
- schema preview dialog,
- sanitize report preview,
- enable/disable toggle,
- test execution dialog that accepts cookies JSON.

Keep the styling aligned with the existing full-width section layout instead of introducing nested cards inside cards.

- [ ] **Step 3: Add i18n strings**

Add strings for:

- `Convert to MCP Tool`
- `RPA MCP Gateway`
- `Removed login steps`
- `Allowed domains`
- `Post-login start URL`
- `Gateway test cookies`
- `Cookies JSON is required`
- `Sanitize warnings`
- `Converted tool saved`

- [ ] **Step 4: Run the frontend build to verify it passes**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue RpaClaw/frontend/src/pages/ToolsPage.vue RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "feat: wire rpa mcp gateway into frontend"
```

## Task 8: Run full backend and frontend verification

**Files:**
- Modify: any files needed from previous tasks only if verification uncovers real defects.

- [ ] **Step 1: Run the focused backend suite**

Run:

```bash
uv run pytest \
  RpaClaw/backend/tests/test_rpa_mcp_converter.py \
  RpaClaw/backend/tests/test_rpa_mcp_executor.py \
  RpaClaw/backend/tests/test_rpa_mcp_route.py \
  RpaClaw/backend/tests/test_mcp_route.py \
  -v
```

Expected: PASS with 0 failures.

- [ ] **Step 2: Run the frontend build**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS.

- [ ] **Step 3: Start the backend if needed for a quick manual endpoint smoke check**

Run:

```bash
cd RpaClaw/backend
uv run uvicorn main:app --host 127.0.0.1 --port 12001
```

In a second shell:

```bash
curl -X POST http://127.0.0.1:12001/api/v1/rpa-mcp/mcp -H "Content-Type: application/json" -d "{\"method\":\"tools/list\",\"params\":{}}"
```

Expected: JSON response with `result.tools`, even if the list is empty.

- [ ] **Step 4: Commit any final fixes**

```bash
git add RpaClaw/backend RpaClaw/frontend mcp_servers.yaml
git commit -m "fix: polish rpa mcp gateway verification issues"
```

## Self-Review

### Spec coverage

- Centralized HTTP Gateway: Task 5.
- Login-step stripping and sanitize preview: Task 3 + Task 5 + Task 7.
- Direct per-call cookies input: Task 4 + Task 5 + Task 7.
- Allowed-domain enforcement: Task 3 + Task 4.
- Storage-backed converted tool definitions: Task 1 + Task 2.
- Gateway discovery and call path: Task 5.
- Frontend conversion preview and tool management: Task 6 + Task 7.
- Verification and safety checks: Task 8.

### Placeholder scan

- No `TODO`/`TBD`.
- Commands are concrete.
- Changed-file paths are explicit.
- Tests and expected commands are listed before implementation steps.

### Type consistency

- Backend model name: `RpaMcpToolDefinition`.
- Registry name: `RpaMcpToolRegistry`.
- Converter name: `RpaMcpConverter`.
- Executor name: `RpaMcpExecutor`.
- Route module name: `rpa_mcp.py`.
- Frontend API module name: `rpaMcp.ts`.

Plan complete and saved to `docs/superpowers/plans/2026-04-19-rpa-mcp-gateway.md`. User already selected inline execution, so the next step is to use `executing-plans` in this same worktree and implement it here without subagents.

