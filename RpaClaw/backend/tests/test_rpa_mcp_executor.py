import pytest

from backend.rpa.mcp_executor import RpaMcpExecutor, InvalidCookieError
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


def _trace_backed_tool():
    tool = _sample_tool(requires_cookies=False)
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
    return tool


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
async def test_execute_compiles_trace_backed_steps_with_trace_compiler():
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    executor = RpaMcpExecutor(browser_factory=lambda *_args, **_kwargs: browser, script_runner=_fake_runner)

    result = await executor.execute(_trace_backed_tool(), {})

    script = result["data"]["script"]
    assert "Auto-generated skill from RPA trace recording" in script
    assert "Click first project" in script
    assert "RecordingRuntimeAgent" in script or "async def run(page, results)" in script


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
