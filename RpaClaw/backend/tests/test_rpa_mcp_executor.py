import pytest

from backend.rpa.mcp_executor import RpaMcpExecutor, InvalidCookieError, _WorkflowExecutionContext
from backend.rpa.mcp_models import RpaMcpToolDefinition


class _FakePage:
    def __init__(self):
        self.calls = []

    async def goto(self, url):
        self.calls.append(("goto", url))

    async def wait_for_timeout(self, value):
        self.calls.append(("wait_for_timeout", value))


class _FakeContext:
    def __init__(self, page):
        self.calls = []
        self.page = page

    async def add_cookies(self, cookies):
        self.calls.append(("add_cookies", cookies))

    async def new_page(self):
        self.calls.append(("new_page", None))
        return self.page

    async def close(self):
        self.calls.append(("close", None))


class _FakeBrowser:
    def __init__(self, context):
        self.context = context

    async def new_context(self, **kwargs):
        return self.context


async def _fake_runner(page, script, kwargs):
    return {"success": True, "data": {"page_calls": page.calls, "kwargs": kwargs, "script": script}}


def _sample_tool(*, requires_cookies: bool = True):
    return RpaMcpToolDefinition(
        id="tool-1",
        user_id="user-1",
        name="download_invoice",
        tool_name="rpa_download_invoice",
        description="Download invoice",
        allowed_domains=["example.com"],
        post_auth_start_url="https://example.com/dashboard",
        steps=[{"action": "click", "target": '{"method":"role","role":"button","name":"Export"}', "description": "Export invoice"}],
        params={"month": {"original_value": "2026-03", "description": "Invoice month"}},
        input_schema={"type": "object", "properties": {"cookies": {"type": "array"}}, "required": ["cookies"]},
        sanitize_report={"removed_steps": [0], "removed_params": ["email", "password"], "warnings": []},
        source={"type": "rpa_skill", "session_id": "session-1", "skill_name": "invoice_skill"},
        requires_cookies=requires_cookies,
    )


def test_validate_cookies_rejects_disallowed_domain():
    executor = RpaMcpExecutor()

    with pytest.raises(InvalidCookieError):
        executor.validate_cookies(
            cookies=[{"name": "sessionid", "value": "secret", "domain": ".other.com", "path": "/"}],
            allowed_domains=["example.com"],
            post_auth_start_url="https://example.com/dashboard",
        )


@pytest.mark.anyio
async def test_execute_adds_cookies_before_goto():
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    executor = RpaMcpExecutor(browser_factory=lambda *_args, **_kwargs: browser, script_runner=_fake_runner)

    tool = _sample_tool(requires_cookies=True)
    await executor.execute(tool, {"cookies": [{"name": "sessionid", "value": "secret", "domain": ".example.com", "path": "/"}], "month": "2026-03"})

    assert context.calls[:2] == [
        ("add_cookies", [{"name": "sessionid", "value": "secret", "domain": ".example.com", "path": "/"}]),
        ("new_page", None),
    ]
    assert page.calls[0] == ("goto", "https://example.com/dashboard")


@pytest.mark.anyio
async def test_execute_allows_missing_cookies_when_tool_does_not_require_them():
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    executor = RpaMcpExecutor(browser_factory=lambda *_args, **_kwargs: browser, script_runner=_fake_runner)

    tool = _sample_tool(requires_cookies=False)
    await executor.execute(tool, {"month": "2026-03"})

    assert context.calls[0] == ("new_page", None)
    assert all(call[0] != "add_cookies" for call in context.calls)
    assert page.calls[0] == ("goto", "https://example.com/dashboard")


@pytest.mark.anyio
async def test_execute_rejects_missing_cookies_when_tool_requires_them():
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    executor = RpaMcpExecutor(browser_factory=lambda *_args, **_kwargs: browser, script_runner=_fake_runner)

    tool = _sample_tool(requires_cookies=True)

    with pytest.raises(InvalidCookieError, match="cookies must be a non-empty array"):
        await executor.execute(tool, {"month": "2026-03"})


@pytest.mark.anyio
async def test_execute_accepts_optional_cookies_when_user_provides_them():
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    executor = RpaMcpExecutor(browser_factory=lambda *_args, **_kwargs: browser, script_runner=_fake_runner)

    tool = _sample_tool(requires_cookies=False)
    await executor.execute(tool, {"cookies": [{"name": "sessionid", "value": "secret", "domain": ".example.com", "path": "/"}], "month": "2026-03"})

    assert context.calls[0] == ("add_cookies", [{"name": "sessionid", "value": "secret", "domain": ".example.com", "path": "/"}])


@pytest.mark.anyio
async def test_default_runner_executes_generated_script():
    page = _FakePage()
    executor = RpaMcpExecutor()
    script = """
async def execute_skill(page, month, _downloads_dir=None):
    await page.wait_for_timeout(123)
    return {"month": month, "downloads_dir": _downloads_dir}
"""

    result = await executor._default_runner(
        page,
        script,
        {"month": "2026-03", "_downloads_dir": "D:/tmp/downloads"},
    )

    assert result == {
        "success": True,
        "message": "Execution completed",
        "data": {"month": "2026-03", "downloads_dir": "D:/tmp/downloads"},
    }
    assert page.calls == [("wait_for_timeout", 123), ("wait_for_timeout", 3000)]


@pytest.mark.anyio
async def test_execute_workflow_package_resolves_segment_output_bindings():
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    executor = RpaMcpExecutor(browser_factory=lambda *_args, **_kwargs: browser)
    tool = RpaMcpToolDefinition(
        id="tool-1",
        user_id="user-1",
        name="bound workflow",
        tool_name="bound_workflow",
        description="Run a bound workflow.",
        allowed_domains=[],
        post_auth_start_url="",
        steps=[],
        params={},
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"},
                "data": {
                    "type": "object",
                    "properties": {
                        "project_name": {"type": "string"},
                        "search_keyword": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "downloads": {"type": "array", "items": {"type": "object"}},
                "artifacts": {"type": "array", "items": {"type": "object"}},
                "error": {"type": ["object", "null"]},
            },
            "required": ["success", "data", "downloads", "artifacts"],
        },
        source={"type": "rpa_skill", "session_id": "session-1", "skill_name": "bound_workflow"},
        workflow_package={
            "workflow": {
                "segments": [
                    {
                        "id": "segment-a",
                        "kind": "rpa",
                        "entry": "segments/segment-a_rpa.py",
                        "inputs": [],
                    },
                    {
                        "id": "segment-b",
                        "kind": "rpa",
                        "entry": "segments/segment-b_rpa.py",
                        "inputs": [
                            {
                                "name": "keyword",
                                "type": "string",
                                "source": "segment_output",
                                "source_ref": "segment-a.outputs.project_name",
                            }
                        ],
                    },
                ]
            },
            "params": {},
            "files": {
                "segments/segment-a_rpa.py": (
                    "async def execute_segment(page, workflow_context=None, **kwargs):\n"
                    "    return {'project_name': 'FinceptTerminal'}\n"
                ),
                "segments/segment-b_rpa.py": (
                    "async def execute_segment(page, workflow_context=None, **kwargs):\n"
                    "    return {'search_keyword': kwargs.get('keyword')}\n"
                ),
            },
        },
    )

    result = await executor.execute(tool, {})

    assert result["success"] is True
    assert result["data"] == {
        "project_name": "FinceptTerminal",
        "search_keyword": "FinceptTerminal",
    }


def test_workflow_context_resolves_artifact_refs_to_runtime_download_path():
    context = _WorkflowExecutionContext(params={})
    segment = {
        "id": "segment-download",
        "outputs": [
            {
                "name": "contracts.xlsx",
                "type": "file",
                "artifact_ref": "artifact-download",
            }
        ],
    }

    context.store_segment_outputs(
        segment,
        {
            "download_contracts_xlsx": {
                "filename": "contracts.xlsx",
                "path": "D:/runtime/downloads/contracts.xlsx",
            }
        },
    )

    assert context.resolve("artifact:artifact-download") == "D:/runtime/downloads/contracts.xlsx"
