import pytest
from playwright.async_api import async_playwright
from types import SimpleNamespace

from backend.rpa.recording_runtime_agent import RecordingRuntimeAgent
from backend.rpa.trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPADataflowMapping,
    RPAPageState,
    RPATargetField,
    RPATraceType,
)
from backend.rpa.trace_skill_compiler import TraceSkillCompiler


def _load_execute_skill(script: str):
    namespace = {}
    exec(script, namespace, namespace)
    return namespace["execute_skill"]


@pytest.mark.asyncio
async def test_recording_runtime_agent_browser_e2e_auto_navigates_open_command_returned_url():
    async def planner(_payload):
        return {
            "description": "Find highest-star repo",
            "action_type": "run_python",
            "expected_effect": "navigate",
            "output_key": "selected_project",
            "code": (
                "async def run(page, results):\n"
                "    return {'name': 'ruvnet/RuView', 'url': 'https://github.com/ruvnet/RuView', 'stars': 47505}"
            ),
        }

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://github.com/trending", lambda route: route.fulfill(body="<html>trending</html>"))
    await page.route("https://github.com/ruvnet/RuView", lambda route: route.fulfill(body="<html>repo</html>"))
    await page.goto("https://github.com/trending")

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="打开star数最多的项目",
        runtime_results={},
    )
    await browser.close()
    await pw.stop()

    assert result.success is True
    assert result.trace.after_page.url == "https://github.com/ruvnet/RuView"
    assert result.output["url"] == "https://github.com/ruvnet/RuView"


@pytest.mark.asyncio
async def test_recording_runtime_agent_browser_e2e_extract_command_keeps_current_page():
    async def planner(_payload):
        return {
            "description": "Find highest-star repo",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "selected_project",
            "code": (
                "async def run(page, results):\n"
                "    return {'name': 'ruvnet/RuView', 'url': 'https://github.com/ruvnet/RuView', 'stars': 47505}"
            ),
        }

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://github.com/trending", lambda route: route.fulfill(body="<html>trending</html>"))
    await page.route("https://github.com/ruvnet/RuView", lambda route: route.fulfill(body="<html>repo</html>"))
    await page.goto("https://github.com/trending")

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="找到star数最多的项目",
        runtime_results={},
    )
    await browser.close()
    await pw.stop()

    assert result.success is True
    assert result.trace.after_page.url == "https://github.com/trending"
    assert result.output["url"] == "https://github.com/ruvnet/RuView"


@pytest.mark.asyncio
async def test_recording_runtime_agent_browser_e2e_extract_restores_after_api_fallback():
    async def planner(_payload):
        return {
            "description": "Extract latest issue title",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "latest_issue",
            "code": (
                "async def run(page, results):\n"
                "    await page.goto('https://api.github.com/repos/ruvnet/RuView/issues?per_page=1')\n"
                "    body = await page.locator('body').text_content()\n"
                "    return {'title': 'Latest issue', 'raw_seen': bool(body)}"
            ),
        }

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://github.com/ruvnet/RuView", lambda route: route.fulfill(body="<html>repo</html>"))
    await page.route(
        "https://api.github.com/repos/ruvnet/RuView/issues?per_page=1",
        lambda route: route.fulfill(
            content_type="application/json",
            body='[{"title":"Latest issue"}]',
        ),
    )
    await page.goto("https://github.com/ruvnet/RuView")

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="find the latest issue title",
        runtime_results={},
    )
    final_url = page.url
    await browser.close()
    await pw.stop()

    assert result.success is True
    assert final_url == "https://github.com/ruvnet/RuView"
    assert result.trace.after_page.url == "https://github.com/ruvnet/RuView"
    assert result.output["title"] == "Latest issue"


@pytest.mark.asyncio
async def test_recording_runtime_agent_browser_e2e_extract_restores_to_last_user_page_after_api_fallback():
    async def planner(_payload):
        return {
            "description": "Extract latest issue title",
            "action_type": "run_python",
            "expected_effect": "extract",
            "output_key": "latest_issue",
            "code": (
                "async def run(page, results):\n"
                "    await page.goto('https://github.com/ruvnet/RuView/issues?q=is%3Aissue')\n"
                "    await page.goto('https://api.github.com/repos/ruvnet/RuView/issues?per_page=1')\n"
                "    return {'title': 'Latest issue'}"
            ),
        }

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://github.com/ruvnet/RuView", lambda route: route.fulfill(body="<html>repo</html>"))
    await page.route(
        "https://github.com/ruvnet/RuView/issues?q=is%3Aissue",
        lambda route: route.fulfill(body="<html>issues</html>"),
    )
    await page.route(
        "https://api.github.com/repos/ruvnet/RuView/issues?per_page=1",
        lambda route: route.fulfill(content_type="application/json", body='[{"title":"Latest issue"}]'),
    )
    await page.goto("https://github.com/ruvnet/RuView")

    result = await RecordingRuntimeAgent(planner=planner).run(
        page=page,
        instruction="find the latest issue title",
        runtime_results={},
    )
    final_url = page.url
    await browser.close()
    await pw.stop()

    assert result.success is True
    assert final_url == "https://github.com/ruvnet/RuView/issues?q=is%3Aissue"
    assert result.trace.after_page.url == "https://github.com/ruvnet/RuView/issues?q=is%3Aissue"


@pytest.mark.asyncio
async def test_generated_highest_star_skill_uses_runtime_ai(monkeypatch):
    async def fake_runtime_ai_run(self, *, page, instruction, runtime_results=None):
        await page.goto("https://example.test/projects/big", wait_until="domcontentloaded")
        return SimpleNamespace(
            success=True,
            output_key="selected_project",
            output={"name": "big", "url": "https://example.test/projects/big", "score": 99},
            diagnostics=[],
            message="ok",
        )

    monkeypatch.setattr(RecordingRuntimeAgent, "run", fake_runtime_ai_run)

    trace = RPAAcceptedTrace(
        trace_id="trace-star",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the project with the highest star count",
        output_key="selected_project",
        output={"url": "https://example.test/projects/recorded"},
        ai_execution=RPAAIExecution(
            language="python",
            code="async def run(page, results):\n    return {'url': 'https://example.test/projects/recorded'}",
        ),
    )
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script([trace], is_local=True))

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://example.test/projects/big", lambda route: route.fulfill(body="<html>big</html>"))
    await page.set_content("<html><body>list</body></html>")
    result = await execute_skill(page)
    await browser.close()
    await pw.stop()

    assert result["selected_project"]["url"] == "https://example.test/projects/big"


@pytest.mark.asyncio
async def test_generated_skill_rewrites_recorded_subpage_url_to_dynamic_selected_object(monkeypatch):
    async def fake_runtime_ai_run(self, *, page, instruction, runtime_results=None):
        await page.goto("https://example.test/projects/live", wait_until="domcontentloaded")
        return SimpleNamespace(
            success=True,
            output_key="top_project",
            output={"url": "https://example.test/projects/live"},
            diagnostics=[],
            message="ok",
        )

    monkeypatch.setattr(RecordingRuntimeAgent, "run", fake_runtime_ai_run)

    traces = [
        RPAAcceptedTrace(
            trace_id="trace-star",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="open the project with the highest star count",
            output_key="top_project",
            output={"url": "https://example.test/projects/recorded"},
            ai_execution=RPAAIExecution(
                language="python",
                code="async def run(page, results):\n    return {'url': 'https://example.test/projects/recorded'}",
            ),
        ),
        RPAAcceptedTrace(
            trace_id="trace-issue",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="find the latest activity title",
            output_key="latest_activity_title",
            output={"latest_activity_title": "Recorded latest"},
            ai_execution=RPAAIExecution(
                language="python",
                code=(
                    "async def run(page, results):\n"
                    "    await page.goto('https://example.test/projects/recorded/activity')\n"
                    "    title = await page.locator('a.Link--primary').first.inner_text()\n"
                    "    return {'latest_activity_title': title.strip()}"
                ),
            ),
        ),
    ]
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script(traces, is_local=True))

    dynamic_activity_html = '<html><body><a class="Link--primary">Dynamic latest activity</a></body></html>'
    recorded_activity_html = '<html><body><a class="Link--primary">Recorded latest activity</a></body></html>'

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://example.test/projects/live", lambda route: route.fulfill(body="<html>live</html>"))
    await page.route("https://example.test/projects/live/activity", lambda route: route.fulfill(body=dynamic_activity_html))
    await page.route(
        "https://example.test/projects/recorded/activity",
        lambda route: route.fulfill(body=recorded_activity_html),
    )
    await page.set_content("<html><body>list</body></html>")
    result = await execute_skill(page)
    await browser.close()
    await pw.stop()

    assert result["top_project"]["url"] == "https://example.test/projects/live"
    assert result["latest_activity_title"] == {"latest_activity_title": "Dynamic latest activity"}


@pytest.mark.asyncio
async def test_generated_record_extraction_skill_runs_recorded_playwright_code():
    trace = RPAAcceptedTrace(
        trace_id="trace-records",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="collect visible record titles and owners",
        output_key="records",
        output=[{"title": "Recorded", "creator": "alice"}],
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    rows = await page.locator('.record-row').all()\n"
                "    result = []\n"
                "    for row in rows:\n"
                "        title = (await row.locator('.title').inner_text()).strip()\n"
                "        creator = (await row.locator('.creator').inner_text()).strip()\n"
                "        result.append({'title': title, 'creator': creator})\n"
                "    return result"
            )
        ),
    )
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script([trace], is_local=True))

    html = """
    <html><body>
      <div class="record-row">
        <a class="title" href="/records/2">Fix parser</a>
        <span class="creator">alice</span>
      </div>
      <div class="record-row">
        <a class="title" href="/records/1">Add docs</a>
        <span class="creator">bob</span>
      </div>
    </body></html>
    """

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    result = await execute_skill(page)
    await browser.close()
    await pw.stop()

    assert result["records"][:2] == [
        {"title": "Fix parser", "creator": "alice"},
        {"title": "Add docs", "creator": "bob"},
    ]


@pytest.mark.asyncio
async def test_generated_dataflow_skill_fills_from_previous_runtime_result():
    traces = [
        RPAAcceptedTrace(
            trace_id="capture",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="capture customer info",
            output_key="customer_info",
            output={"name": "Alice Zhang"},
            ai_execution=RPAAIExecution(
                code="async def run(page, results):\n    return {'name': 'Alice Zhang'}",
            ),
        ),
        RPAAcceptedTrace(
            trace_id="fill",
            trace_type=RPATraceType.DATAFLOW_FILL,
            source="manual",
            action="fill",
            value="Alice Zhang",
            dataflow=RPADataflowMapping(
                target_field=RPATargetField(
                    locator_candidates=[
                        {"locator": {"method": "role", "role": "textbox", "name": "Customer Name"}}
                    ],
                ),
                value="Alice Zhang",
                source_ref_candidates=["customer_info.name"],
                selected_source_ref="customer_info.name",
                confidence="exact_value_match",
            ),
        ),
    ]
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script(traces, is_local=True))

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content("<label>Customer Name <input /></label>")
    result = await execute_skill(page)
    filled = await page.get_by_role("textbox", name="Customer Name").input_value()
    await browser.close()
    await pw.stop()

    assert result["customer_info"]["name"] == "Alice Zhang"
    assert filled == "Alice Zhang"


@pytest.mark.asyncio
async def test_generated_skill_replays_trending_semantic_project_to_pr_extraction_flow(monkeypatch):
    async def fake_runtime_ai_run(self, *, page, instruction, runtime_results=None):
        await page.goto("https://github.com/openai/openai-agents-python", wait_until="domcontentloaded")
        return SimpleNamespace(
            success=True,
            output_key="selected_project",
            output={
                "name": "openai/openai-agents-python",
                "url": "https://github.com/openai/openai-agents-python",
            },
            diagnostics=[],
            message="ok",
        )

    monkeypatch.setattr(RecordingRuntimeAgent, "run", fake_runtime_ai_run)

    traces = [
        RPAAcceptedTrace(
            trace_id="nav-trending",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/trending"),
        ),
        RPAAcceptedTrace(
            trace_id="select-python",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="open the project most related to Python",
            description="open the project most related to Python",
            output_key="selected_project",
            output={
                "name": "openai/openai-agents-python",
                "url": "https://github.com/openai/openai-agents-python",
            },
            ai_execution=RPAAIExecution(code="async def run(page, results):\n    return {}"),
        ),
        RPAAcceptedTrace(
            trace_id="nav-pulls",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/openai/openai-agents-python/pulls"),
        ),
        RPAAcceptedTrace(
            trace_id="extract-prs",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="collect the first 10 PR titles and creators",
            output_key="top10_prs",
            output=[{"title": "Recorded", "creator": "alice"}],
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    rows = await page.locator('div.Box-row').all()\n"
                    "    result = []\n"
                    "    for row in rows:\n"
                    "        title = (await row.locator('a.Link--primary').inner_text()).strip()\n"
                    "        creator = (await row.locator('a[data-hovercard-type=\"user\"]').inner_text()).strip()\n"
                    "        url = await row.locator('a.Link--primary').get_attribute('href')\n"
                    "        result.append({'title': title, 'creator': creator, 'url': 'https://github.com' + url})\n"
                    "    return result"
                )
            ),
        ),
    ]
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script(traces, is_local=True))

    trending_html = """
    <html><body>
      <article class="Box-row">
        <h2><a href="/other/js-tool">other / js-tool</a></h2>
        <p>JavaScript utility</p>
      </article>
      <article class="Box-row">
        <h2><a href="/openai/openai-agents-python">openai / openai-agents-python</a></h2>
        <p>A Python framework for building agents</p>
      </article>
    </body></html>
    """
    pulls_html = """
    <html><body>
      <div class="Box-row">
        <a class="Link--primary" href="/openai/openai-agents-python/pull/20">Add memory backend</a>
        <a data-hovercard-type="user" href="/alice">alice</a>
      </div>
    </body></html>
    """

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://github.com/trending", lambda route: route.fulfill(body=trending_html))
    await page.route(
        "https://github.com/openai/openai-agents-python",
        lambda route: route.fulfill(body="<html><body>repo</body></html>"),
    )
    await page.route(
        "https://github.com/openai/openai-agents-python/pulls",
        lambda route: route.fulfill(body=pulls_html),
    )
    await page.route(
        "https://github.com/openai/openai-agents-python/pulls?q=is%3Apr",
        lambda route: route.fulfill(body=pulls_html),
    )

    result = await execute_skill(page)
    await browser.close()
    await pw.stop()

    assert result["selected_project"]["url"] == "https://github.com/openai/openai-agents-python"
    assert result["top10_prs"] == [
        {
            "title": "Add memory backend",
            "creator": "alice",
            "url": "https://github.com/openai/openai-agents-python/pull/20",
        }
    ]


@pytest.mark.asyncio
async def test_generated_skill_replays_semantic_project_manual_pr_click_and_two_page_pr_extraction(monkeypatch):
    async def fake_runtime_ai_run(self, *, page, instruction, runtime_results=None):
        await page.goto("https://github.com/openai/openai-agents-python", wait_until="domcontentloaded")
        return SimpleNamespace(
            success=True,
            output_key="selected_project",
            output={
                "name": "openai/openai-agents-python",
                "url": "https://github.com/openai/openai-agents-python",
                "reason": "Python appears in the repository name and description.",
            },
            diagnostics=[],
            message="ok",
        )

    monkeypatch.setattr(RecordingRuntimeAgent, "run", fake_runtime_ai_run)

    traces = [
        RPAAcceptedTrace(
            trace_id="nav-trending",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/trending"),
        ),
        RPAAcceptedTrace(
            trace_id="select-python",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="打开和python最相关的项目",
            description="Open the most Python-related trending project",
            output_key="selected_project",
            output={
                "name": "openai/openai-agents-python",
                "url": "https://github.com/openai/openai-agents-python",
            },
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    await page.locator('a[href=\"/openai/openai-agents-python\"]').click()\n"
                    "    return {'url': 'https://github.com/openai/openai-agents-python'}"
                ),
            ),
        ),
        RPAAcceptedTrace(
            trace_id="manual-pr-tab",
            trace_type=RPATraceType.MANUAL_ACTION,
            source="manual",
            action="click",
            description='点击 link("Pull requests")',
            locator_candidates=[
                {"locator": {"method": "role", "role": "link", "name": "Pull requests"}},
            ],
        ),
        RPAAcceptedTrace(
            trace_id="extract-prs",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="收集当前仓库的前两页PR（无论是什么状态）的信息，要求记录每个pr的创建人和标题，输出严格为数组",
            output_key="pr_list",
            output=[{"title": "Recorded", "creator": "alice"}],
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    result = []\n"
                    "    async def collect():\n"
                    "        rows = await page.locator('div.Box-row').all()\n"
                    "        for row in rows:\n"
                    "            title = (await row.locator('a.Link--primary').inner_text()).strip()\n"
                    "            creator = (await row.locator('a[data-hovercard-type=\"user\"]').inner_text()).strip()\n"
                    "            url = await row.locator('a.Link--primary').get_attribute('href')\n"
                    "            result.append({'title': title, 'creator': creator, 'url': 'https://github.com' + url})\n"
                    "    await collect()\n"
                    "    await page.goto('https://github.com/openai/openai-agents-python/pulls?q=is%3Apr&page=2')\n"
                    "    await collect()\n"
                    "    return result"
                )
            ),
        ),
    ]
    execute_skill = _load_execute_skill(TraceSkillCompiler().generate_script(traces, is_local=True))

    pulls_page_1 = """
    <html><body>
      <div class="Box-row">
        <a class="Link--primary" href="/openai/openai-agents-python/pull/20">Add memory backend</a>
        <a data-hovercard-type="user" href="/alice">alice</a>
      </div>
    </body></html>
    """
    pulls_page_2 = """
    <html><body>
      <div class="Box-row">
        <a class="Link--primary" href="/openai/openai-agents-python/pull/19">Fix duplicate tool names</a>
        <a data-hovercard-type="user" href="/bob">bob</a>
      </div>
    </body></html>
    """

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.route("https://github.com/trending", lambda route: route.fulfill(body="<html>trending</html>"))
    await page.route(
        "https://github.com/openai/openai-agents-python",
        lambda route: route.fulfill(
            body='<html><body><a href="/openai/openai-agents-python/pulls">Pull requests</a></body></html>'
        ),
    )
    await page.route(
        "https://github.com/openai/openai-agents-python/pulls",
        lambda route: route.fulfill(body=pulls_page_1),
    )
    await page.route(
        "https://github.com/openai/openai-agents-python/pulls?q=is%3Apr",
        lambda route: route.fulfill(body=pulls_page_1),
    )
    await page.route(
        "https://github.com/openai/openai-agents-python/pulls?q=is%3Apr&page=2",
        lambda route: route.fulfill(body=pulls_page_2),
    )

    result = await execute_skill(page)
    await browser.close()
    await pw.stop()

    assert result["selected_project"]["url"] == "https://github.com/openai/openai-agents-python"
    assert result["pr_list"] == [
        {
            "title": "Add memory backend",
            "creator": "alice",
            "url": "https://github.com/openai/openai-agents-python/pull/20",
        },
        {
            "title": "Fix duplicate tool names",
            "creator": "bob",
            "url": "https://github.com/openai/openai-agents-python/pull/19",
        },
    ]
