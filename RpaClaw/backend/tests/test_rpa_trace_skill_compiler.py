import ast
import asyncio
from datetime import datetime, timedelta

from backend.rpa.trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPADataflowMapping,
    RPALocatorStabilityCandidate,
    RPALocatorStabilityMetadata,
    RPAPageState,
    RPATargetField,
    RPATraceType,
)
from backend.rpa.trace_skill_compiler import (
    TraceSkillCompiler,
    trace_requires_runtime_ai_replay,
    traces_require_runtime_ai_replay,
)


def _execute_body(script: str) -> str:
    start = script.index("async def execute_skill")
    return script[start:]


def _execute_prelude(script: str) -> str:
    start = script.index("async def execute_skill")
    return script[:start]


def _load_execute_skill(script: str):
    end = script.index("\ndef _parse_cli_value")
    namespace = {"__name__": "compiled_skill_test"}
    exec(script[:end], namespace)
    return namespace["execute_skill"]


def _assert_script_loads(script: str):
    ast.parse(script)
    return _load_execute_skill(script)


class _FakeTracePage:
    def __init__(self, name: str = "page", url: str = "about:blank") -> None:
        self.name = name
        self.url = url
        self.context = _FakeTraceContext()
        self.brought_to_front = 0
        self.closed = False
        self.goto_calls = []

    async def bring_to_front(self) -> None:
        self.brought_to_front += 1

    async def close(self) -> None:
        self.closed = True

    async def goto(self, url: str, wait_until: str = "") -> None:
        self.goto_calls.append((url, wait_until))
        self.url = url


class _FakeTraceContext:
    def __init__(self) -> None:
        self.created_pages = []

    async def new_page(self):
        page = _FakeTracePage(f"page-{len(self.created_pages) + 1}")
        page.context = self
        self.created_pages.append(page)
        return page


def test_compiler_renders_navigation_trace():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.NAVIGATION,
                after_page=RPAPageState(url="https://github.com/trending"),
            )
        ],
        is_local=True,
    )

    assert "async def execute_skill" in script
    assert "https://github.com/trending" in script


def test_navigation_script_only_includes_required_helper_prelude():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.NAVIGATION,
                after_page=RPAPageState(url="https://github.com/trending"),
            )
        ],
        is_local=True,
    )
    prelude = _execute_prelude(script)

    assert "_trace_start" in prelude
    assert "_download_from_export_task" not in prelude
    assert "_execute_runtime_ai_instruction" not in prelude
    assert "_extract_display_field_value" not in prelude
    assert "_extract_bounded_section_text" not in prelude
    assert "_resolve_recorded_frame" not in prelude
    assert "_validate_non_empty_records" not in prelude


def test_explicit_navigation_replays_recorded_target_not_redirect_login_url():
    trace = RPAAcceptedTrace(
        trace_id="explicit-sso-entry",
        trace_type=RPATraceType.NAVIGATION,
        action="navigate",
        description="Navigate to https://business.example.com/dashboard",
        value="https://business.example.com/dashboard",
        after_page=RPAPageState(url="https://sso.example.com/login?nonce=random-123"),
        signals={
            "navigation": {
                "target_url": "https://business.example.com/dashboard",
                "observed_url": "https://sso.example.com/login?nonce=random-123",
                "redirected": True,
            }
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_target_url = 'https://business.example.com/dashboard'" in body
    assert "sso.example.com/login?nonce=random-123" not in body


def test_navigation_traces_with_same_tab_id_stay_on_one_page():
    traces = [
        RPAAcceptedTrace(
            trace_id="nav-a",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/vercel-labs/agent-browser"),
            signals={"tab": {"tab_id": "tab-root"}},
        ),
        RPAAcceptedTrace(
            trace_id="nav-b",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://www.browseract.com/"),
            signals={"tab": {"tab_id": "tab-root"}},
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert 'tabs = {"tab-root": page}' in body
    assert "await current_page.context.new_page()" not in body
    assert body.count("await current_page.goto(_target_url, wait_until='domcontentloaded')") == 2


def test_compiler_renders_manual_set_input_files_trace():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-upload",
                trace_type=RPATraceType.MANUAL_ACTION,
                source="manual",
                action="set_input_files",
                description="Upload file",
                locator_candidates=[
                    {
                        "kind": "label",
                        "locator": {"method": "label", "value": "Upload file"},
                        "selected": True,
                    }
                ],
                signals={
                    "set_input_files": {
                        "files": ["C:/Users/example/report.pdf"],
                    }
                },
                value="C:/Users/example/report.pdf",
            )
        ],
        is_local=True,
    )
    body = _execute_body(script)

    assert ".get_by_label(" in body
    assert "Upload file" in body
    assert ".set_input_files(" in body
    assert "C:/Users/example/report.pdf" in body
    assert "Unsupported manual action preserved as no-op: set_input_files" not in body


def test_compiler_renders_multiple_manual_set_input_files_trace():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-upload-many",
                trace_type=RPATraceType.MANUAL_ACTION,
                source="manual",
                action="set_input_files",
                description="Upload files",
                locator_candidates=[
                    {
                        "kind": "css",
                        "locator": {"method": "css", "value": "input[type=file]"},
                        "selected": True,
                    }
                ],
                signals={
                    "set_input_files": {
                        "files": ["C:/tmp/a.txt", "C:/tmp/b.txt"],
                    }
                },
            )
        ],
        is_local=True,
    )
    body = _execute_body(script)

    assert ".locator(" in body
    assert "input[type=file]" in body
    assert ".set_input_files(['C:/tmp/a.txt', 'C:/tmp/b.txt'])" in body


def test_manual_action_prefers_stable_candidate_over_selected_random_like_testid():
    trace = RPAAcceptedTrace(
        trace_id="trace-search-field",
        trace_type=RPATraceType.MANUAL_ACTION,
        source="manual",
        action="click",
        description="Click ESN search field",
        locator_candidates=[
            {
                "kind": "testid",
                "selected": True,
                "locator": {
                    "method": "nested",
                    "parent": {
                        "method": "testid",
                        "value": "DIV-_standingActiveManage_standingBook-id-611090413",
                    },
                    "child": {
                        "method": "testid",
                        "value": "DIV-_standingActiveManage_standingBook-id-1064443668",
                    },
                },
            },
            {
                "kind": "role",
                "selected": False,
                "locator": {"method": "role", "role": "textbox", "name": "ESN"},
            },
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_role('textbox', name='ESN', exact=True)" in body
    assert "get_by_test_id('DIV-_standingActiveManage_standingBook-id-611090413')" not in body


def test_manual_action_rejects_selected_random_like_testid_without_stable_candidate():
    trace = RPAAcceptedTrace(
        trace_id="trace-search-field",
        trace_type=RPATraceType.MANUAL_ACTION,
        source="manual",
        action="click",
        description="Click generated test id",
        locator_candidates=[
            {
                "kind": "testid",
                "selected": True,
                "locator": {
                    "method": "nested",
                    "parent": {
                        "method": "testid",
                        "value": "DIV-_standingActiveManage_standingBook-id-1213867279",
                    },
                    "child": {
                        "method": "testid",
                        "value": "DIV-_standingActiveManage_standingBook-id-1064443668",
                    },
                },
            },
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_test_id('DIV-_standingActiveManage_standingBook-id-1213867279')" not in body
    assert "Recorded click action is missing a valid target locator" in body


def test_navigation_trace_with_new_tab_id_materializes_page_before_goto():
    traces = [
        RPAAcceptedTrace(
            trace_id="nav-root",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/vercel-labs/agent-browser"),
            signals={"tab": {"tab_id": "tab-root"}},
        ),
        RPAAcceptedTrace(
            trace_id="nav-second",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://www.browseract.com/"),
            signals={"tab": {"tab_id": "tab-second"}},
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    new_page = "page = await current_page.context.new_page()"
    store_tab = 'tabs[tab_id] = page'
    ensure_tab = 'current_page = await _ensure_recorded_tab(tabs, current_page, kwargs, "tab-second")'
    second_url = "_target_url = 'https://www.browseract.com/'"
    assert 'tabs = {"tab-root": page}' in body
    assert new_page in script
    assert store_tab in script
    assert ensure_tab in body
    assert body.index(ensure_tab) < body.index(second_url)


def test_navigation_trace_with_new_tab_id_activates_materialized_page_for_preview():
    traces = [
        RPAAcceptedTrace(
            trace_id="nav-root",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/vercel-labs/agent-browser"),
            signals={"tab": {"tab_id": "tab-root"}},
        ),
        RPAAcceptedTrace(
            trace_id="nav-second",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://www.browseract.com/"),
            signals={"tab": {"tab_id": "tab-second"}},
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "async def _activate_recorded_page(page, kwargs, tab_id=''):" in script
    activation = '_ensure_recorded_tab(tabs, current_page, kwargs, "tab-second")'
    second_url = "_target_url = 'https://www.browseract.com/'"
    assert activation in body
    assert body.index(activation) < body.index(second_url)


def test_manual_action_with_new_tab_id_and_frame_path_stays_on_current_page():
    traces = [
        RPAAcceptedTrace(
            trace_id="click-menu",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="click",
            description='点击 text("操作")',
            locator_candidates=[
                {"locator": {"method": "text", "value": "操作", "exact": True}, "selected": True},
            ],
            signals={"tab": {"tab_id": "tab-root"}},
        ),
        RPAAcceptedTrace(
            trace_id="click-confirm",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="click",
            description='点击 text("确定")',
            frame_path=["iframe:nth-of-type(2)"],
            locator_candidates=[
                {"locator": {"method": "text", "value": "确定", "exact": True}, "selected": True},
            ],
            signals={
                "reported_frame_path": ['iframe[src*="kweweb-b4.huawei.com/pr/"]'],
                "tab": {"tab_id": "tab-frame"},
            },
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert 'current_page = await _ensure_recorded_tab(tabs, current_page, kwargs, "tab-frame")' not in body
    assert "Materialize recorded tab tab-frame" not in body
    assert 'frame_scope = current_page.frame_locator("iframe[src*=\\"kweweb-b4.huawei.com/pr/\\"]")' in body
    assert "iframe:nth-of-type(2)" not in body
    assert "frame_scope.get_by_text('确定', exact=True).click()" in body


def test_manual_action_with_dynamic_reported_frame_src_uses_stable_frame_resolver():
    dynamic_frame = (
        'iframe[src="https\\:\\/\\/kweweb-b4\\.huawei\\.com\\/pr\\/\\#\\!purpr'
        '\\/shoppingcar\\/index\\.html\\?prHeadId\\=33533937\\&interfaceSourceCode\\=iPlatform'
        '\\&sourceSystemAppId\\=3548124130716926322"]'
    )
    traces = [
        RPAAcceptedTrace(
            trace_id="click-menu",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="click",
            description='click text("operation")',
            locator_candidates=[
                {"locator": {"method": "text", "value": "operation", "exact": True}, "selected": True},
            ],
            signals={"tab": {"tab_id": "tab-root"}},
        ),
        RPAAcceptedTrace(
            trace_id="click-confirm",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="click",
            description="click confirm icon",
            frame_path=["iframe:nth-of-type(2)"],
            locator_candidates=[
                {"locator": {"method": "css", "value": ".jalor-icon.confirm"}, "selected": True},
            ],
            signals={
                "reported_frame_path": [dynamic_frame],
                "tab": {"tab_id": "tab-frame"},
            },
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert 'current_page = await _ensure_recorded_tab(tabs, current_page, kwargs, "tab-frame")' not in body
    assert "_resolve_recorded_frame(" in body
    assert "kweweb-b4.huawei.com/pr/" in body
    assert "prHeadId" not in body
    assert "sourceSystemAppId" not in body
    assert "iframe:nth-of-type(2)" not in body
    assert "frame_scope.locator('.jalor-icon.confirm').first.click()" in body


def test_navigation_url_difference_without_tab_fact_does_not_create_new_page():
    traces = [
        RPAAcceptedTrace(
            trace_id="nav-a",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/vercel-labs/agent-browser"),
        ),
        RPAAcceptedTrace(
            trace_id="nav-b",
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://www.browseract.com/"),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "await current_page.context.new_page()" not in body
    assert body.count("await current_page.goto(_target_url, wait_until='domcontentloaded')") == 2


def test_compiler_does_not_emit_github_helpers_for_generic_web_trace():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.NAVIGATION,
                after_page=RPAPageState(url="https://example.test/customers/alpha"),
            )
        ],
        is_local=True,
    )

    assert "https://example.test/customers/alpha" in script
    assert "github" not in script.lower()
    assert "_abs_github_url" not in script
    assert "_github_repo_base" not in script


def test_compiler_renders_snapshot_detail_extract_as_playwright_code():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract procurement info",
                user_instruction="提取采购信息中的内容",
                output_key="procurement_info",
                output={"预计总金额 (含税）": "100.00"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"预计总金额 (含税）": "100.00"}),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "section_title": "采购信息",
                        "fields": [
                            {
                                "label": "预计总金额 (含税）",
                                "value": "100.00",
                                "data_prop": "2652409177955720363",
                                "visible": True,
                                "value_kind": "number",
                            }
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction" not in body
    assert "current_page.locator('[data-prop=\"2652409177955720363\"]')" in body
    assert "_results['procurement_info'] = _result" in body
    assert "'预计总金额 (含税）'" in body
    assert "100.00" not in body


def test_compiler_renders_snapshot_detail_extract_in_frame_scope():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract iframe detail",
                output_key="iframe_detail",
                output={"Amount": "100.00"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"Amount": "100.00"}),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "section_title": "Detail",
                        "frame_path": ["iframe[title='detail']"],
                        "fields": [
                            {
                                "label": "Amount",
                                "value": "100.00",
                                "data_prop": "amount",
                                "visible": True,
                                "value_kind": "number",
                            }
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "frame_scope = current_page.frame_locator(\"iframe[title='detail']\")" in body
    assert "frame_scope.locator('[data-prop=\"amount\"]')" in body
    assert "current_page.locator('[data-prop=\"amount\"]')" not in body


def test_compiler_falls_back_from_snapshot_extract_when_field_evidence_missing():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract procurement info",
                output_key="purchase_info",
                output={"预计总金额 (含税）": "100.00", "币种": "USD"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"预计总金额 (含税）": "100.00", "币种": "USD"}),
                signals={"extract_snapshot": {"source": "detail_views", "section_title": "采购信息", "fields": []}},
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" in body
    assert "aui-form-item" not in body
    assert "ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' aui-form-item ')]" not in body
    assert "100.00" not in body
    assert "USD" not in body


def test_compiler_does_not_turn_star_count_output_label_into_snapshot_locator():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="获取star数",
        user_instruction="获取star数",
        output_key="star_count",
        output={"Star count": "48.2k"},
        ai_execution=RPAAIExecution(language="snapshot", code="", output={"Star count": "48.2k"}),
        signals={"extract_snapshot": {"source": "detail_views", "fields": []}},
    )

    script = TraceSkillCompiler().generate_script(
        [trace],
        is_local=True,
    )

    body = _execute_body(script)

    assert traces_require_runtime_ai_replay([trace]) is True
    assert "_execute_runtime_ai_instruction(" in body
    assert "aui-form-item" not in body
    assert "xpath=//*[normalize-space()='Star count']" not in body
    assert "Star count" not in body
    assert "48.2k" not in body


def test_compiler_replays_empty_embedded_extract_through_runtime_ai_without_allow_empty_contract():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="extract the star count from the current page",
        description="Extract star count",
        output_key="star_count",
        output={"star_count": ""},
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    link = page.locator('a[href$=\"stargazers\"]').first\n"
                "    return {'star_count': (await link.inner_text()).strip()}"
            ),
            output={"star_count": ""},
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert traces_require_runtime_ai_replay([trace]) is True
    assert "_execute_runtime_ai_instruction(" in body
    assert 'href$="stargazers"' not in body


def test_compiler_preserves_empty_embedded_extract_when_trace_allows_empty_output():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="collect optional notifications",
        description="Collect optional notifications",
        output_key="notifications",
        output={"notifications": []},
        ai_execution=RPAAIExecution(
            language="python",
            code="async def run(page, results):\n    return {'notifications': []}",
            output={"notifications": []},
        ),
        signals={"output_contract": {"allow_empty": True}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert traces_require_runtime_ai_replay([trace]) is False
    assert "_execute_runtime_ai_instruction(" not in body
    assert "return {'notifications': []}" in body


def test_compiler_wraps_each_trace_with_trace_level_logging():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.NAVIGATION,
                description="打开趋势页",
                after_page=RPAPageState(url="https://github.com/trending"),
            ),
            RPAAcceptedTrace(
                trace_type=RPATraceType.DATA_CAPTURE,
                description="读取标题",
                output_key="title",
                output="GitHub Trending",
            ),
        ],
        is_local=True,
    )
    body = _execute_body(script)

    assert "_trace_logger = kwargs.get('_on_log')" in body
    assert "_trace_started_at = _trace_start(_trace_logger, 0, '打开趋势页', current_page)" in body
    assert "_trace_started_at = _trace_start(_trace_logger, 1, '读取标题', current_page)" in body
    assert "_trace_error(_trace_logger, 0, '打开趋势页', current_page, _trace_started_at, _trace_exc)" in body
    assert "_trace_done(_trace_logger, 1, '读取标题', current_page, _trace_started_at)" in body
    assert "TRACE_START" in script
    assert "TRACE_DONE" in script
    assert "TRACE_ERROR" in script


def test_compiler_preserves_highest_star_selection_as_runtime_ai():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-star",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="open the project with the highest star count",
                output_key="selected_project",
                output={"url": "https://github.com/recorded/repo"},
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return {'url': 'https://github.com/recorded/repo'}",
                ),
            )
        ],
        is_local=True,
    )

    assert "_execute_runtime_ai_instruction(" in script
    assert "stargazers" not in script
    assert "max_stars" not in script
    assert "_abs_github_url" not in script
    assert "https://github.com/recorded/repo" not in _execute_body(script)


def test_compiler_preserves_pr_record_extraction_as_python_playwright():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-prs",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="collect the first 10 PRs in the current repository with title and creator",
                output_key="top10_prs",
                output=[{"title": "Fix bug", "creator": "alice"}],
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return [{'title': 'Fix bug', 'creator': 'alice'}]",
                ),
            )
        ],
        is_local=True,
    )

    assert "top10_prs" in script
    assert "page.evaluate" not in script
    assert "_validate_non_empty_records('top10_prs', _result)" not in script


def test_compiler_uniquifies_duplicate_ai_output_keys():
    traces = [
        RPAAcceptedTrace(
            trace_id=f"basic-{index}",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction=f"extract PR basic info {index}",
            output_key="pr_basic_info",
            output={"requestor": f"user-{index}"},
            ai_execution=RPAAIExecution(
                language="python",
                code=f"async def run(page, results):\n    return {{'requestor': 'user-{index}'}}",
            ),
        )
        for index in range(1, 4)
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)

    assert "_results['pr_basic_info'] = _result" in script
    assert "_results['pr_basic_info_2'] = _result" in script
    assert "_results['pr_basic_info_3'] = _result" in script


def test_compiler_preserves_ai_positional_collection_locator_when_locator_stability_has_alternate():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_id="ordinal-1",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="获取第一个项目的名称",
                description="Extract ordinal item title",
                output_key="ordinal_item_name",
                output="Alishahryar1 / free-claude-code",
                ai_execution=RPAAIExecution(
                    language="python",
                    code=(
                        "async def run(page, results):\n"
                        "    _item = page.locator('h2.lh-condensed a').nth(0)\n"
                        "    return (await _item.inner_text()).strip()"
                    ),
                ),
                locator_stability=RPALocatorStabilityMetadata(
                    primary_locator={"method": "css", "value": "h2.lh-condensed a"},
                    unstable_signals=[{"type": "css"}],
                    alternate_locators=[
                        RPALocatorStabilityCandidate(
                            locator={"method": "role", "role": "link", "name": "Skip to content"},
                            confidence="high",
                        )
                    ],
                ),
            )
        ],
        is_local=True,
    )
    body = _execute_body(script)

    assert "page.locator('h2.lh-condensed a').nth(0)" in body
    assert "Skip to content" not in body


def test_compiler_uses_source_ref_for_dataflow_fill():
    trace = RPAAcceptedTrace(
        trace_id="fill-1",
        trace_type=RPATraceType.DATAFLOW_FILL,
        source="manual",
        action="fill",
        value="Alice Zhang",
        dataflow=RPADataflowMapping(
            target_field=RPATargetField(
                label="Customer Name",
                locator_candidates=[{"locator": {"method": "role", "role": "textbox", "name": "Customer Name"}}],
            ),
            value="Alice Zhang",
            source_ref_candidates=["customer_info.name"],
            selected_source_ref="customer_info.name",
            confidence="exact_value_match",
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    prelude = _execute_prelude(script)

    assert "_resolve_result_ref" in prelude
    assert "customer_info.name" in script
    assert "await current_page.get_by_role('textbox', name='Customer Name', exact=True).fill(str(_value))" in script
    assert "Alice Zhang" not in _execute_body(script)


def test_manual_fill_uses_sensitive_credential_param():
    trace = RPAAcceptedTrace(
        trace_id="password-fill",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="fill",
        value="{{credential}}",
        locator_candidates=[
            {"locator": {"method": "role", "role": "textbox", "name": "Password"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script(
        [trace],
        params={
            "password": {
                "original_value": "{{credential}}",
                "sensitive": True,
                "credential_id": "cred_123",
            }
        },
        is_local=True,
    )
    body = _execute_body(script)

    assert "get_by_role('textbox', name='Password', exact=True).fill(kwargs['password'])" in body
    assert "fill('{{credential}}')" not in body


def test_manual_fill_uses_harness_input_placeholder_runtime_param():
    trace = RPAAcceptedTrace(
        trace_id="account-fill",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="fill",
        value="{{input:account}}",
        locator_candidates=[
            {"locator": {"method": "role", "role": "textbox", "name": "Account"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_role('textbox', name='Account', exact=True).fill(kwargs.get('account', '{{input:account}}'))" in body


def test_manual_fill_uses_plain_param_default_when_configured():
    trace = RPAAcceptedTrace(
        trace_id="username-fill",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="fill",
        value="admi",
        locator_candidates=[
            {"locator": {"method": "role", "role": "textbox", "name": "Username"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script(
        [trace],
        params={
            "username": {
                "original_value": "admi",
                "sensitive": False,
                "credential_id": "",
            }
        },
        is_local=True,
    )
    body = _execute_body(script)

    assert "get_by_role('textbox', name='Username', exact=True).fill(kwargs.get('username', 'admi'))" in body
    assert "fill('admi')" not in body


def test_manual_fill_uses_configured_default_value_as_runtime_fallback():
    trace = RPAAcceptedTrace(
        trace_id="search-fill",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="fill",
        value="recorded query",
        locator_candidates=[
            {"locator": {"method": "role", "role": "textbox", "name": "Search"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script(
        [trace],
        params={
            "query": {
                "original_value": "recorded query",
                "default_value": "configured query",
                "sensitive": False,
                "credential_id": "",
            }
        },
        is_local=True,
    )
    body = _execute_body(script)

    assert "get_by_role('textbox', name='Search', exact=True).fill(kwargs.get('query', 'configured query'))" in body
    assert "kwargs.get('query', 'recorded query')" not in body


def test_manual_click_defaults_to_exact_match_for_role_locator():
    trace = RPAAcceptedTrace(
        trace_id="manual-click",
        trace_type=RPATraceType.MANUAL_ACTION,
        source="manual",
        action="click",
        locator_candidates=[
            {"locator": {"method": "role", "role": "link", "name": "菜鸟笔记"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "await current_page.get_by_role('link', name='菜鸟笔记', exact=True).click()" in body


def test_ai_data_capture_does_not_force_exact_match_when_unspecified():
    trace = RPAAcceptedTrace(
        trace_id="ai-capture",
        trace_type=RPATraceType.DATA_CAPTURE,
        source="ai",
        output_key="cta_text",
        description="Read CTA text",
        locator_candidates=[
            {"locator": {"method": "role", "role": "button", "name": "Search"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_role('button', name='Search').inner_text()" in body
    assert "exact=True" not in body


def test_duplicate_sensitive_fill_values_consume_params_in_order():
    traces = [
        RPAAcceptedTrace(
            trace_id="portal-password",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="fill",
            value="{{credential}}",
            locator_candidates=[
                {"locator": {"method": "role", "role": "textbox", "name": "Portal Password"}, "selected": True},
            ],
        ),
        RPAAcceptedTrace(
            trace_id="erp-password",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="fill",
            value="{{credential}}",
            locator_candidates=[
                {"locator": {"method": "role", "role": "textbox", "name": "ERP Password"}, "selected": True},
            ],
        ),
    ]

    script = TraceSkillCompiler().generate_script(
        traces,
        params={
            "password": {
                "original_value": "{{credential}}",
                "sensitive": True,
                "credential_id": "cred_portal",
            },
            "password_2": {
                "original_value": "{{credential}}",
                "sensitive": True,
                "credential_id": "cred_erp",
            },
        },
        is_local=True,
    )
    body = _execute_body(script)

    assert "get_by_role('textbox', name='Portal Password', exact=True).fill(kwargs['password'])" in body
    assert "get_by_role('textbox', name='ERP Password', exact=True).fill(kwargs['password_2'])" in body


def test_manual_hover_compiles_to_locator_hover():
    trace = RPAAcceptedTrace(
        trace_id="hover-export",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="hover",
        description='悬停到 button("Export")',
        locator_candidates=[
            {"locator": {"method": "role", "role": "button", "name": "Export"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_role('button', name='Export', exact=True).hover()" in body


def test_manual_click_filter_has_text_locator_compiles_to_playwright_filter():
    trace = RPAAcceptedTrace(
        trace_id="click-confirm",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="click",
        description="点击确定",
        locator_candidates=[
            {
                "locator": {
                    "method": "filter_has_text",
                    "locator": {"method": "css", "value": "span"},
                    "has_text": "确定",
                },
                "selected": True,
            },
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "current_page.locator('span').filter(has_text='确定').first.click()" in body


def test_manual_popup_click_compiles_to_expect_popup_and_switches_page():
    trace = RPAAcceptedTrace(
        trace_id="popup-export",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="click",
        description='click text("Export all")',
        locator_candidates=[
            {"locator": {"method": "text", "value": "Export all", "exact": True}, "selected": True},
        ],
        signals={"popup": {"target_tab_id": "tab-export"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "async with current_page.expect_popup() as popup_info:" in body
    assert "await current_page.get_by_text('Export all', exact=True).click()" in body
    assert "new_page = await popup_info.value" in body
    assert 'tabs["tab-export"] = new_page' in body
    assert "current_page = new_page" in body


def test_manual_popup_click_registers_source_tab_before_switching_to_new_page():
    traces = [
        RPAAcceptedTrace(
            trace_id="popup-export",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="click",
            description='click text("Export all")',
            locator_candidates=[
                {"locator": {"method": "text", "value": "Export all", "exact": True}, "selected": True},
            ],
            signals={"popup": {"source_tab_id": "tab-root", "target_tab_id": "tab-export"}},
        ),
        RPAAcceptedTrace(
            trace_id="switch-root",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="switch_tab",
            description="Switch back to root tab",
            signals={"tab": {"source_tab_id": "tab-export", "target_tab_id": "tab-root"}},
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    source_registration = 'tabs.setdefault("tab-root", current_page)'
    popup_wait = "async with current_page.expect_popup() as popup_info:"
    assert source_registration in body
    assert body.index(source_registration) < body.index(popup_wait)
    assert 'tabs["tab-export"] = new_page' in body
    assert 'current_page = await _ensure_recorded_tab(tabs, current_page, kwargs, "tab-root", "", True)' in body


def test_manual_switch_tab_trace_compiles_to_page_context_switch():
    trace = RPAAcceptedTrace(
        trace_id="switch-to-sales",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="switch_tab",
        description="切换到标签页 iSales+",
        signals={"tab": {"source_tab_id": "tab-root", "target_tab_id": "tab-sales"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "No stable locator was recorded" not in body
    assert 'tabs.setdefault("tab-root", current_page)' in body
    assert 'current_page = await _ensure_recorded_tab(tabs, current_page, kwargs, "tab-sales", "", True)' in body
    assert "await page.bring_to_front()" in script


def test_manual_switch_tab_without_known_target_materializes_recorded_url_at_runtime():
    trace = RPAAcceptedTrace(
        trace_id="switch-to-sales",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="switch_tab",
        description="切换到标签页 iSales+",
        after_page=RPAPageState(url="https://example.com/sales"),
        signals={"tab": {"source_tab_id": "tab-root", "target_tab_id": "tab-sales"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    execute_skill = _load_execute_skill(script)
    page = _FakeTracePage("root")

    asyncio.run(execute_skill(page))

    assert len(page.context.created_pages) == 1
    created_page = page.context.created_pages[0]
    assert created_page.goto_calls == [("https://example.com/sales", "domcontentloaded")]
    assert created_page.brought_to_front == 1


def test_manual_switch_tab_without_known_target_or_recorded_url_fails_clearly():
    trace = RPAAcceptedTrace(
        trace_id="switch-to-sales",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="switch_tab",
        description="切换到标签页 iSales+",
        signals={"tab": {"source_tab_id": "tab-root", "target_tab_id": "tab-sales"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    execute_skill = _load_execute_skill(script)
    page = _FakeTracePage("root")

    try:
        asyncio.run(execute_skill(page))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing recorded tab URL should fail")

    assert "tab-sales" in message
    assert "recorded URL" in message


def test_manual_close_tab_trace_compiles_to_page_close_and_fallback_switch():
    trace = RPAAcceptedTrace(
        trace_id="close-sales",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="close_tab",
        description="关闭标签页 iSales+ 并切换到其他标签页",
        signals={"tab": {"tab_id": "tab-sales", "target_tab_id": "tab-root"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "No stable locator was recorded" not in body
    assert 'tabs.setdefault("tab-sales", current_page)' in body
    assert 'closing_page = tabs.pop("tab-sales", current_page)' in body
    assert "await closing_page.close()" in body
    assert 'current_page = await _ensure_recorded_tab(tabs, current_page, kwargs, "tab-root", "", True)' in body


def test_manual_close_tab_without_known_fallback_url_does_not_use_closing_page_url():
    trace = RPAAcceptedTrace(
        trace_id="close-sales",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="close_tab",
        description="关闭标签页 iSales+ 并切换到其他标签页",
        after_page=RPAPageState(url="https://example.com/sales"),
        signals={"tab": {"tab_id": "tab-sales", "target_tab_id": "tab-root"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    execute_skill = _load_execute_skill(script)
    page = _FakeTracePage("sales")

    try:
        asyncio.run(execute_skill(page))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing fallback tab URL should fail")

    assert page.closed is True
    assert "tab-root" in message
    assert "recorded URL" in message
    assert all(
        ("https://example.com/sales", "domcontentloaded") not in created.goto_calls
        for created in page.context.created_pages
    )


def test_manual_close_tab_with_recorded_fallback_url_materializes_fallback_at_runtime():
    trace = RPAAcceptedTrace(
        trace_id="close-sales",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="close_tab",
        description="关闭标签页 iSales+ 并切换到其他标签页",
        after_page=RPAPageState(url="https://example.com/sales"),
        signals={
            "tab": {
                "tab_id": "tab-sales",
                "target_tab_id": "tab-root",
                "target_url": "https://example.com/root",
            }
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    execute_skill = _load_execute_skill(script)
    page = _FakeTracePage("sales")

    asyncio.run(execute_skill(page))

    assert page.closed is True
    assert len(page.context.created_pages) == 1
    created_page = page.context.created_pages[0]
    assert created_page.goto_calls == [("https://example.com/root", "domcontentloaded")]
    assert created_page.brought_to_front == 1


def test_manual_download_click_compiles_to_expect_download():
    trace = RPAAcceptedTrace(
        trace_id="download-report",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="click",
        description='click link("report.xlsx")',
        locator_candidates=[
            {"locator": {"method": "role", "role": "link", "name": "report.xlsx", "exact": True}, "selected": True},
        ],
        signals={"download": {"filename": "report.xlsx"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "async with current_page.expect_download() as _dl_info:" in body
    assert "await current_page.get_by_role('link', name='report.xlsx', exact=True).click()" in body
    assert "_dl = await _dl_info.value" in body
    assert '_results["download_report"]' in body


def test_ai_operation_with_download_signal_compiles_to_expect_download():
    trace = RPAAcceptedTrace(
        trace_id="ai-download-report",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="download the report",
        description="Download report",
        output_key="download_report",
        output={"action_performed": True},
        signals={"download": {"filename": "report.xlsx"}},
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    await page.get_by_role('link', name='report.xlsx').click()\n"
                "    return {'action_performed': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "async with current_page.expect_download() as _dl_info:" in body
    assert "            _result = await run(current_page, _results)" in body
    assert "_dl = await _dl_info.value" in body
    assert '_results["download_report"]' in body


def test_ai_operation_with_existing_expect_download_is_not_wrapped_twice():
    trace = RPAAcceptedTrace(
        trace_id="ai-download-report",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="download the report",
        description="Download report",
        signals={"download": {"filename": "report.xlsx"}},
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    async with page.expect_download() as download_info:\n"
                "        await page.get_by_role('link', name='report.xlsx').click()\n"
                "    return {'filename': (await download_info.value).suggested_filename}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)

    assert script.count("expect_download()") == 1


def test_standalone_download_trace_after_ai_operation_merges_into_trigger():
    traces = [
        RPAAcceptedTrace(
            trace_id="ai-click-download-link",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="click the report download link",
            description="Click report download link",
            output_key="download_action",
            output={"action_performed": True},
            ai_execution=RPAAIExecution(
                language="python",
                code=(
                    "async def run(page, results):\n"
                    "    await page.get_by_role('link', name='report.xlsx').click()\n"
                    "    return {'action_performed': True}"
                ),
            ),
        ),
        RPAAcceptedTrace(
            trace_id="download-export-file",
            trace_type=RPATraceType.MANUAL_ACTION,
            source="manual",
            action="download",
            description="下载文件 Conclusion excelExport_17728726_20260425155427.xlsx",
            value="Conclusion excelExport_17728726_20260425155427.xlsx",
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "async with current_page.expect_download() as _dl_info:" in body
    assert "            _result = await run(current_page, _results)" in body
    assert "_dl = await _dl_info.value" in body
    assert "No stable locator was recorded for this manual action" not in body
    assert "_trace_start(_trace_logger, 1, '下载文件" not in body


def test_standalone_export_table_download_trace_merges_as_export_task():
    traces = [
        RPAAcceptedTrace(
            trace_id="ai-click-export-file",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="click the first file name in the export table",
            description="Click table row column action",
            output_key="table_row_action",
            output={"action_performed": True},
            ai_execution=RPAAIExecution(
                language="python",
                code=(
                    "async def run(page, results):\n"
                    "    _heading = page.get_by_text('导出列表', exact=True).first\n"
                    "    if await _heading.count():\n"
                    "        _rows = _heading.locator(\"xpath=following::table[.//tbody/tr][1]//tbody/tr\")\n"
                    "    else:\n"
                    "        _rows = page.locator('tbody tr')\n"
                    "    _row = _rows.nth(0)\n"
                    "    await _row.locator('td[data-colid=\"col_25\"] a').click()\n"
                    "    return {'action_performed': True}"
                ),
            ),
        ),
        RPAAcceptedTrace(
            trace_id="download-export-file",
            trace_type=RPATraceType.MANUAL_ACTION,
            source="manual",
            action="download",
            description="Download file",
            value="Conclusion excelExport_17733824_20260426211105.xlsx",
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "_download_from_export_task(" in body
    assert "            _result = await run(current_page, _results)" not in body
    assert "async with current_page.expect_download() as _dl_info:" not in body


def test_ai_export_task_download_signal_compiles_to_export_task_helper():
    trace = RPAAcceptedTrace(
        trace_id="ai-click-export-file",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="click the first file name in the export table",
        description="Click table row column action",
        output_key="table_row_action",
        output={"action_performed": True},
        signals={
            "download": {
                "filename": "Conclusion excelExport_17733824_20260426211105.xlsx",
                "trigger_mode": "export_task",
            }
        },
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    _heading = page.get_by_text('导出列表', exact=True).first\n"
                "    if await _heading.count():\n"
                "        _rows = _heading.locator(\"xpath=following::table[.//tbody/tr][1]//tbody/tr\")\n"
                "    else:\n"
                "        _rows = page.locator('tbody tr')\n"
                "    _row = _rows.nth(0)\n"
                "    await _row.locator('td[data-colid=\"col_25\"] a').click()\n"
                "    return {'action_performed': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    prelude = _execute_prelude(script)
    body = _execute_body(script)

    assert "_download_from_export_task" in prelude
    assert "_download_from_export_task(" in body
    assert "table_heading='导出列表'" in body
    assert "action_selector='td[data-colid=\"col_25\"] a'" in body
    assert "            _result = await run(current_page, _results)" not in body
    assert '_results["download_Conclusion_excelExport_17733824_20260426211105"]' in body
    assert "_results['table_row_action'] = _result" in body


def test_jalor_export_task_download_uses_scoped_row_selector_helper():
    trace = RPAAcceptedTrace(
        trace_id="ai-click-jalor-export-file",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="点击列表中第一行的文件名称",
        description="Click table row column action",
        output_key="table_row_action",
        output={"action_performed": True},
        signals={"download": {"filename": "ContractList20260427102109.xlsx"}},
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    _rows = page.locator('#taskExportGridTable tbody.igrid-data tr.grid-row')\n"
                "    _row = _rows.nth(0)\n"
                "    await _row.locator('td[field=\"tmpName\"] a').click()\n"
                "    return {'action_performed': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_download_from_export_task(" in body
    assert "row_selector='#taskExportGridTable tbody.igrid-data tr.grid-row'" in body
    assert "action_selector='td[field=\"tmpName\"] a'" in body
    assert "            _result = await run(current_page, _results)" not in body
    assert "async with current_page.expect_download() as _dl_info:" not in body
    assert '_results["download_ContractList20260427102109"]' in body
    assert "_results['table_row_action'] = _result" in body


def test_jalor_export_task_download_with_region_context_preserves_action_replay():
    trace = RPAAcceptedTrace(
        trace_id="ai-click-jalor-export-file",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="Click the file name in the first row of the export list",
        description="Click the file name in the first row of the export list",
        output_key="click_first_file_name",
        output={"action_performed": True, "action_type": "click"},
        signals={"download": {"filename": "ContractList20260427102109.xlsx"}},
        region_context={
            "inferred_kind": "table_region",
            "table_summary": {
                "selected_row_indexes": [0, 1, 2, 3, 4, 5, 6, 7],
                "locator_candidates": [
                    {
                        "kind": "css",
                        "locator": {"method": "css", "value": "#taskExportGridTable"},
                    }
                ],
            },
        },
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    _rows = page.locator('#taskExportGridTable tbody.igrid-data tr.grid-row')\n"
                "    _row = _rows.nth(0)\n"
                "    await _row.locator('td[field=\"tmpName\"] a').click()\n"
                "    return {'action_performed': True, 'action_type': 'click'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_download_payload = await _download_from_export_task(" in body
    assert "row_selector='#taskExportGridTable tbody.igrid-data tr.grid-row'" in body
    assert "action_selector='td[field=\"tmpName\"] a'" in body
    assert "selectedIndexes" not in body
    assert ".evaluate(\"\"\"(table)" not in body
    assert "_results['click_first_file_name'] = _result" in body


def test_ai_table_region_click_without_download_preserves_action_replay():
    trace = RPAAcceptedTrace(
        trace_id="ai-click-table-row",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="Click the first matching table row",
        description="Click the first matching table row",
        output_key="clicked_row",
        output={"action_performed": True, "action_type": "click", "target": "Alpha"},
        region_context={
            "inferred_kind": "table_region",
            "table_summary": {
                "selected_row_indexes": [0],
                "locator_candidates": [
                    {
                        "kind": "css",
                        "locator": {"method": "css", "value": "table.results"},
                    }
                ],
            },
        },
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    await page.locator('table.results tbody tr').nth(0).click()\n"
                "    return {'action_performed': True, 'action_type': 'click', 'target': 'Alpha'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "await page.locator('table.results tbody tr').nth(0).click()" in body
    assert ".evaluate(\"\"\"(table)" not in body
    assert "selectedIndexes" not in body
    assert "_results['clicked_row'] = _result" in body


def test_manual_navigation_signal_click_compiles_to_expect_navigation():
    trace = RPAAcceptedTrace(
        trace_id="menu-settings",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="click",
        description='click text("Settings")',
        locator_candidates=[
            {"locator": {"method": "text", "value": "Settings", "exact": True}, "selected": True},
        ],
        signals={"navigation": {"url": "https://example.com/settings"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "async with current_page.expect_navigation(wait_until='domcontentloaded'):" in body
    assert "await current_page.get_by_text('Settings', exact=True).click()" in body
    assert "await current_page.wait_for_load_state('domcontentloaded')" in body


def test_manual_navigate_click_preserves_click_navigation_semantics():
    trace = RPAAcceptedTrace(
        trace_id="login-submit",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="navigate_click",
        description='点击 button("登录") 并跳转页面',
        after_page=RPAPageState(url="https://example.com/app"),
        locator_candidates=[
            {"locator": {"method": "role", "role": "button", "name": "登录"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "expect_navigation" in body
    assert "get_by_role('button', name='登录', exact=True).click()" in body
    assert "goto(_target_url" not in body


def test_manual_sso_redirect_chain_folds_into_post_click_url_wait():
    base_time = datetime(2026, 5, 12, 17, 43, 15)
    tab_signal = {"tab": {"tab_id": "tab-root"}}
    traces = [
        RPAAcceptedTrace(
            trace_id="sso-click",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="navigate_click",
            description='点击 button("使用 SSO 登录") 并跳转页面',
            after_page=RPAPageState(
                url="https://oseasy.his.huawei.com/login.html?code=abc123&state=nonce",
            ),
            locator_candidates=[
                {"locator": {"method": "role", "role": "button", "name": "使用 SSO 登录"}, "selected": True},
            ],
            signals=tab_signal,
            started_at=base_time,
            ended_at=base_time,
        ),
        RPAAcceptedTrace(
            trace_id="redirect-root",
            trace_type=RPATraceType.NAVIGATION,
            description="导航到 https://oseasy.his.huawei.com/",
            after_page=RPAPageState(url="https://oseasy.his.huawei.com/"),
            signals=tab_signal,
            started_at=base_time + timedelta(milliseconds=500),
            ended_at=base_time + timedelta(milliseconds=500),
        ),
        RPAAcceptedTrace(
            trace_id="redirect-hash-root",
            trace_type=RPATraceType.NAVIGATION,
            description="导航到 https://oseasy.his.huawei.com/#/",
            after_page=RPAPageState(url="https://oseasy.his.huawei.com/#/"),
            signals=tab_signal,
            started_at=base_time + timedelta(milliseconds=800),
            ended_at=base_time + timedelta(milliseconds=800),
        ),
        RPAAcceptedTrace(
            trace_id="redirect-final",
            trace_type=RPATraceType.NAVIGATION,
            description="导航到 https://oseasy.his.huawei.com/#/ha/cluster",
            after_page=RPAPageState(url="https://oseasy.his.huawei.com/#/ha/cluster"),
            signals=tab_signal,
            started_at=base_time + timedelta(milliseconds=1000),
            ended_at=base_time + timedelta(milliseconds=1000),
        ),
        RPAAcceptedTrace(
            trace_id="next-menu-click",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="click",
            description='点击 menuitem("虚拟机")',
            after_page=RPAPageState(url="https://oseasy.his.huawei.com/#/ha/cluster"),
            locator_candidates=[
                {"locator": {"method": "role", "role": "menuitem", "name": "虚拟机"}, "selected": True},
            ],
            signals={**tab_signal, "menu_context": {"is_menu_item": True}},
            started_at=base_time + timedelta(seconds=5),
            ended_at=base_time + timedelta(seconds=5),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "get_by_role('button', name='使用 SSO 登录', exact=True).click()" in body
    assert "wait_for_url('https://oseasy.his.huawei.com/#/ha/cluster'" in body
    assert "login.html?code=" not in body
    assert "#/#/ha/cluster" not in body
    assert "get_by_role('menuitem', name='虚拟机', exact=True).click()" in body
    assert body.count("await current_page.goto(_target_url, wait_until='domcontentloaded')") == 0


def test_non_auth_navigation_after_click_is_not_folded_into_post_click_wait():
    base_time = datetime(2026, 5, 12, 17, 43, 15)
    tab_signal = {"tab": {"tab_id": "tab-root"}}
    traces = [
        RPAAcceptedTrace(
            trace_id="settings-click",
            trace_type=RPATraceType.MANUAL_ACTION,
            action="navigate_click",
            description='点击 link("Settings") 并跳转页面',
            after_page=RPAPageState(url="https://example.com/settings"),
            locator_candidates=[
                {"locator": {"method": "role", "role": "link", "name": "Settings"}, "selected": True},
            ],
            signals=tab_signal,
            started_at=base_time,
            ended_at=base_time,
        ),
        RPAAcceptedTrace(
            trace_id="manual-address-navigation",
            trace_type=RPATraceType.NAVIGATION,
            description="导航到 https://example.com/reports",
            after_page=RPAPageState(url="https://example.com/reports"),
            signals=tab_signal,
            started_at=base_time + timedelta(seconds=10),
            ended_at=base_time + timedelta(seconds=10),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "wait_for_url('https://example.com/reports'" not in body
    assert "_target_url = 'https://example.com/reports'" in body
    assert body.count("await current_page.goto(_target_url, wait_until='domcontentloaded')") == 1


def test_standalone_hash_route_navigation_uses_absolute_recorded_url():
    trace = RPAAcceptedTrace(
        trace_id="hash-route",
        trace_type=RPATraceType.NAVIGATION,
        description="导航到 https://oseasy.his.huawei.com/#/ha/cluster",
        after_page=RPAPageState(url="https://oseasy.his.huawei.com/#/ha/cluster"),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_target_url = 'https://oseasy.his.huawei.com/#/ha/cluster'" in body
    assert "#/#/ha/cluster" not in body


def test_manual_fill_without_valid_locator_raises_clear_runtime_error():
    trace = RPAAcceptedTrace(
        trace_id="broken-fill",
        trace_type=RPATraceType.MANUAL_ACTION,
        action="fill",
        description='输入 "abc" 到 None',
        value="abc",
        locator_candidates=[{"selected": True}],
        validation={"status": "broken", "details": "missing strict locator"},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "Recorded fill action is missing a valid target locator" in body
    assert "locator('body')" not in body


def test_region_single_value_extract_compiles_to_inner_text_result_key():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract the selected total value",
        description="Extract selected total",
        output_key="total_due",
        region_context={"inferred_kind": "single_value"},
        locator_candidates=[
            {
                "selected": True,
                "locator": {"method": "css", "value": "[data-field='total']"},
            }
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "inner_text()" in body
    assert ".strip()" in body
    assert "_results['total_due'] = _result" in body
    assert "_execute_runtime_ai_instruction" not in body


def test_region_single_value_prefers_nested_scope_locator():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract order status from the selected region",
        description="Extract selected order status",
        output_key="order_status",
        locator_candidates=[
            {
                "selected": True,
                "kind": "text",
                "locator": {"method": "text", "value": "Order A Paid Refund"},
                "source": "trace_target",
            }
        ],
        region_context={
            "inferred_kind": "single_value",
            "locator_candidates": [
                {
                    "kind": "text",
                    "locator": {"method": "text", "value": "Order A Paid Refund"},
                    "source": "dominant_scope",
                }
            ],
            "intersecting_elements": [
                {
                    "tag": "span",
                    "text": "Paid",
                    "nested_locator_candidates": [
                        {
                            "kind": "nested",
                            "locator": {
                                "method": "nested",
                                "parent": {"method": "text", "value": "Order A"},
                                "child": {"method": "text", "value": "Paid"},
                            },
                            "source": "region_ancestor_scope",
                        }
                    ],
                }
            ],
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "get_by_text('Order A').get_by_text('Paid')" in body
    assert "get_by_text('Order A Paid Refund')" not in body
    assert "_results['order_status'] = _result" in body


def test_region_single_value_falls_back_when_first_nested_locator_is_invalid():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract order status from the selected region",
        description="Extract selected order status",
        output_key="order_status",
        region_context={
            "inferred_kind": "single_value",
            "locator_candidates": [
                {
                    "kind": "css",
                    "locator": {"method": "css", "value": "[data-field='status']"},
                    "source": "dominant_scope",
                }
            ],
            "intersecting_elements": [
                {
                    "tag": "span",
                    "text": "Paid",
                    "nested_locator_candidates": [
                        {
                            "kind": "nested",
                            "locator": {
                                "method": "nested",
                                "parent": {"method": "text", "value": ""},
                                "child": {"method": "text", "value": "Paid"},
                            },
                            "source": "region_ancestor_scope",
                        }
                    ],
                }
            ],
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "locator(\"[data-field='status']\")" in body
    assert "_execute_runtime_ai_instruction" not in body


def test_selected_region_text_extract_with_explicit_locator_compiles_to_inner_text():
    recorded_text = "Recorded purchase title 3333"
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get title info",
        description="Get title info",
        output_key="title_info",
        output={"Title": recorded_text},
        signals={
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "title_info",
            },
            "selected_region_text_extract": {
                "source": "region_context",
                "region_id": "region-1",
                "output_key": "title_info",
                "label": "Title",
                "locator": {"method": "css", "value": ".titlePanel-left"},
                "frame_path": [],
                "observed_text": recorded_text,
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": [recorded_text],
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction" not in body
    assert "current_page.locator('.titlePanel-left').first.inner_text()" in body
    assert "_result['Title'] = _value" in body
    assert "_results['title_info'] = _result" in body
    assert recorded_text not in body
    assert "aui-form-item" not in body
    assert trace_requires_runtime_ai_replay(trace) is False


def test_selected_region_local_text_fields_fall_back_to_runtime_ai_without_explicit_locator():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="获取框选区域的模型数量",
        description="Extract selected model count",
        output_key="model_count",
        output={"模型数量": "99"},
        signals={
            "extract_snapshot": {
                "source": "selected_region.local_text",
                "fields": [{"label": "模型数量", "value": "99", "visible": True}],
            }
        },
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": ["Total 99 models"],
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction" in body
    assert "'region_id': 'region-1'" not in body
    assert "Total 99 models" not in body
    assert "aui-form-item" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_selected_region_expanded_region_fields_fall_back_to_runtime_ai_without_explicit_locator():
    recorded_text = "Recorded purchase title 3333"
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get title info",
        description="Get title info",
        output_key="title_info",
        output={"Title": recorded_text},
        signals={
            "extract_snapshot": {
                "source": "expanded_regions",
                "section_title": recorded_text,
                "fields": [{"label": "Title", "value": recorded_text, "visible": True}],
            },
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "extract_snapshot",
                "output_key": "title_info",
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": [recorded_text],
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(current_page, _results, kwargs, 'Get title info', 'title_info')" in body
    assert recorded_text not in body
    assert "aui-form-item" not in body
    assert "xpath=//*[normalize-space()='Title']" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_selected_region_text_extract_rejects_observed_text_driven_locator():
    recorded_text = "Recorded purchase title 3333"
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get title info",
        description="Get title info",
        output_key="title_info",
        output={"Title": recorded_text},
        signals={
            "region_selection": {
                "region_id": "region-1",
                "inferred_kind": "text_region",
                "local_text_preview": [recorded_text],
            },
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "title_info",
            },
            "selected_region_text_extract": {
                "source": "region_context",
                "region_id": "region-1",
                "output_key": "title_info",
                "label": "Title",
                "locator": {"method": "text", "value": recorded_text, "exact": True},
                "frame_path": [],
                "observed_text": recorded_text,
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": [recorded_text],
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(current_page, _results, kwargs, 'Get title info', 'title_info')" in body
    assert recorded_text not in body
    assert "get_by_text" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_selected_region_text_extract_rejects_observed_role_name_locator():
    recorded_text = "Recorded purchase title 3333"
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get title info",
        description="Get title info",
        output_key="title_info",
        output={"Title": recorded_text},
        signals={
            "region_selection": {
                "region_id": "region-1",
                "inferred_kind": "text_region",
                "local_text_preview": [recorded_text],
            },
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "title_info",
            },
            "selected_region_text_extract": {
                "source": "region_context",
                "region_id": "region-1",
                "output_key": "title_info",
                "label": "Title",
                "locator": {"method": "role", "role": "heading", "name": recorded_text},
                "frame_path": [],
                "observed_text": recorded_text,
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": [recorded_text],
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(current_page, _results, kwargs, 'Get title info', 'title_info')" in body
    assert recorded_text not in body
    assert "get_by_role" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_selected_region_text_extract_rejects_dynamic_framework_id_locator():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get purchase info content",
        description="Get purchase info content",
        output_key="purchase_info",
        output="采购信息",
        signals={
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "purchase_info",
            },
            "selected_region_text_extract": {
                "source": "region_context",
                "region_id": "region-1",
                "output_key": "purchase_info",
                "locator": {"method": "css", "value": "#aui-collapse-head-09521894"},
                "frame_path": [],
                "observed_text": "采购信息",
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={"region_id": "region-1", "inferred_kind": "text_region", "local_text": ["采购信息"]},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(current_page, _results, kwargs, 'Get purchase info content', 'purchase_info')" in body
    assert "#aui-collapse-head-09521894" not in body
    assert "inner_text()" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_selected_region_text_extract_rejects_structural_region_header_locator():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get purchase info content",
        description="Get purchase info content",
        output_key="purchase_info",
        output="采购信息",
        signals={
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "purchase_info",
            },
            "selected_region_text_extract": {
                "source": "region_context",
                "region_id": "region-1",
                "output_key": "purchase_info",
                "locator": {"method": "css", "value": ".aui-collapse-item__word-overflow"},
                "frame_path": [],
                "observed_text": "采购信息",
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={"region_id": "region-1", "inferred_kind": "text_region", "local_text": ["采购信息"]},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(current_page, _results, kwargs, 'Get purchase info content', 'purchase_info')" in body
    assert ".aui-collapse-item__word-overflow" not in body
    assert "inner_text()" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_selected_region_local_text_without_fields_does_not_inject_recorded_region_context():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract the selected model count",
        description="Extract selected model count",
        output_key="model_count",
        output={"model_count": "99"},
        signals={"extract_snapshot": {"source": "selected_region.local_text", "fields": []}},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": ["Total 99 models"],
            "rect": {"x": 10, "y": 20, "width": 120, "height": 30},
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(current_page, _results, kwargs, 'Extract the selected model count', 'model_count')" in body
    assert "'region_id': 'region-1'" not in body
    assert "Total 99 models" not in body
    assert "'rect':" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_region_scoped_text_extract_does_not_embed_recorded_text_locator():
    recorded_text = "Official, Anthropic-managed directory of high quality Claude Code Plugins."
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="获取这段项目介绍",
        description="提取项目介绍文本",
        output_key="project_description",
        output=recorded_text,
        signals={
            "region_selection": {
                "region_id": "region-1",
                "inferred_kind": "text_region",
                "local_text_preview": [recorded_text],
            },
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "project_description",
            },
            "output_contract": {"allow_empty": True},
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": [recorded_text],
        },
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                f"    locator = page.get_by_text({recorded_text!r}, exact=True)\n"
                "    if await locator.count() > 0:\n"
                "        return await locator.inner_text()\n"
                "    return ''"
            ),
            output=recorded_text,
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert (
        "_execute_runtime_ai_instruction(current_page, _results, kwargs, "
        "'获取这段项目介绍', 'project_description')"
    ) in body
    assert recorded_text not in body
    assert "get_by_text" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_heading_scoped_region_text_extract_compiles_to_deterministic_script():
    recorded_text = "Reusable project description"
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get About description",
        description="Extract About description",
        output_key="about_content",
        output=recorded_text,
        signals={
            "region_selection": {
                "region_id": "region-1",
                "inferred_kind": "text_region",
                "local_text_preview": [recorded_text],
            },
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "about_content",
            },
            "region_text_extract": {
                "source": "region_scoped_snapshot",
                "kind": "heading_scoped_text",
                "section_title": "About",
                "heading_locator": {"method": "text", "value": "About", "exact": True},
                "heading_relation": "inside_heading",
                "text_strategy": "bounded_section_text",
                "output_key": "about_content",
                "frame_path": [],
            },
            "output_contract": {"allow_empty": True},
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": [recorded_text],
        },
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                f"    locator = page.get_by_text({recorded_text!r}, exact=True)\n"
                "    return await locator.inner_text()"
            ),
            output=recorded_text,
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction" not in body
    assert "get_by_text('About', exact=True)" in body
    assert "_extract_bounded_section_text" in body
    assert recorded_text not in body
    assert "_results['about_content'] = _result" in body
    assert trace_requires_runtime_ai_replay(trace) is False


def test_heading_scoped_region_text_extract_rejects_observed_text_driven_heading_locator():
    recorded_text = "采购一批电脑3333"
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get selected purchase title",
        description="Extract selected purchase title",
        output_key="purchase_title",
        output=recorded_text,
        signals={
            "region_selection": {
                "region_id": "region-1",
                "inferred_kind": "text_region",
                "local_text_preview": [recorded_text],
            },
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "purchase_title",
            },
            "region_text_extract": {
                "source": "region_scoped_snapshot",
                "kind": "heading_scoped_text",
                "section_title": recorded_text,
                "heading_locator": {"method": "text", "value": recorded_text, "exact": True},
                "heading_relation": "inside_heading",
                "text_strategy": "bounded_section_text",
                "output_key": "purchase_title",
                "frame_path": [],
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": [recorded_text],
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(current_page, _results, kwargs, 'Get selected purchase title', 'purchase_title')" in body
    assert recorded_text not in body
    assert "get_by_text" not in body
    assert "_extract_bounded_section_text" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_heading_scoped_region_text_extract_rejects_dynamic_framework_heading_locator():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get purchase info content",
        description="Get purchase info content",
        output_key="purchase_info",
        output="采购内容",
        signals={
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "purchase_info",
            },
            "region_text_extract": {
                "source": "region_scoped_snapshot",
                "kind": "heading_scoped_text",
                "section_title": "采购信息",
                "heading_locator": {"method": "css", "value": "#aui-collapse-head-09521894"},
                "heading_relation": "inside_heading",
                "text_strategy": "bounded_section_text",
                "output_key": "purchase_info",
                "frame_path": [],
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={"region_id": "region-1", "inferred_kind": "text_region"},
    )

    body = _execute_body(TraceSkillCompiler().generate_script([trace], is_local=True))

    assert "_execute_runtime_ai_instruction" in body
    assert "#aui-collapse-head-09521894" not in body
    assert "_extract_bounded_section_text" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_heading_scoped_region_text_extract_rejects_structural_header_heading_locator():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get purchase info content",
        description="Get purchase info content",
        output_key="purchase_info",
        output="采购内容",
        signals={
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "purchase_info",
            },
            "region_text_extract": {
                "source": "region_scoped_snapshot",
                "kind": "heading_scoped_text",
                "section_title": "采购信息",
                "heading_locator": {"method": "css", "value": ".aui-collapse-item__word-overflow"},
                "heading_relation": "inside_heading",
                "text_strategy": "bounded_section_text",
                "output_key": "purchase_info",
                "frame_path": [],
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={"region_id": "region-1", "inferred_kind": "text_region"},
    )

    body = _execute_body(TraceSkillCompiler().generate_script([trace], is_local=True))

    assert "_execute_runtime_ai_instruction" in body
    assert ".aui-collapse-item__word-overflow" not in body
    assert "_extract_bounded_section_text" not in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_heading_scoped_region_text_extract_classification_requires_durable_anchor():
    recorded_text = "Stable section text"
    deterministic_trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Read selected section text",
        description="Read selected section text",
        output_key="section_text",
        output=recorded_text,
        signals={
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "section_text",
            },
            "region_text_extract": {
                "source": "region_scoped_snapshot",
                "kind": "heading_scoped_text",
                "section_title": "Warranty",
                "heading_locator": {"method": "text", "value": "Warranty", "exact": True},
                "heading_relation": "preceding_heading",
                "text_strategy": "bounded_section_text",
                "output_key": "section_text",
                "frame_path": [],
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={"region_id": "region-1", "inferred_kind": "text_region"},
    )
    missing_anchor_trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Read selected section text",
        description="Read selected section text",
        output_key="section_text",
        output=recorded_text,
        signals={
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "section_text",
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={
            "region_id": "region-1",
            "inferred_kind": "text_region",
            "local_text": [recorded_text],
        },
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                f"    return await page.get_by_text({recorded_text!r}).inner_text()"
            ),
            output=recorded_text,
        ),
    )

    deterministic_body = _execute_body(TraceSkillCompiler().generate_script([deterministic_trace], is_local=True))
    fallback_body = _execute_body(TraceSkillCompiler().generate_script([missing_anchor_trace], is_local=True))

    assert trace_requires_runtime_ai_replay(deterministic_trace) is False
    assert "_extract_bounded_section_text" in deterministic_body
    assert "_execute_runtime_ai_instruction" not in deterministic_body
    assert trace_requires_runtime_ai_replay(missing_anchor_trace) is True
    assert "_execute_runtime_ai_instruction" in fallback_body
    assert "get_by_text('Stable section text'" not in fallback_body
    assert recorded_text not in fallback_body


def test_heading_scoped_region_text_extract_rejects_after_context_anchor():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get About description",
        description="Extract About description",
        output_key="about_content",
        signals={
            "region_selection": {"region_id": "region-1", "inferred_kind": "text_region"},
            "region_context_decision": {
                "used_as": "extraction",
                "region_id": "region-1",
                "action_type": "run_python",
                "output_key": "about_content",
            },
            "region_text_extract": {
                "source": "region_scoped_snapshot",
                "kind": "heading_scoped_text",
                "section_title": "Topics",
                "heading_locator": {"method": "text", "value": "Topics"},
                "heading_relation": "after_context",
                "text_strategy": "bounded_section_text",
                "output_key": "about_content",
                "frame_path": [],
            },
        },
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={"region_id": "region-1", "inferred_kind": "text_region"},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "get_by_text('Topics'" not in body
    assert "_execute_runtime_ai_instruction" in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_broad_extract_markers_do_not_override_structured_region_or_action_evidence():
    single_value = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Read selected total",
        description="Read selected total",
        output_key="total_due",
        region_context={"inferred_kind": "single_value"},
        locator_candidates=[{"selected": True, "locator": {"method": "css", "value": "[data-field='total']"}}],
    )
    table = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get selected table rows",
        description="Get selected table rows",
        output_key="selected_rows",
        region_context={
            "inferred_kind": "table_region",
            "table_summary": {
                "selected_row_indexes": [1],
                "locator_candidates": [{"locator": {"method": "css", "value": "table.orders"}}],
            },
        },
    )
    selected_list = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Read selected list items",
        description="Read selected list items",
        output_key="selected_items",
        region_context={
            "inferred_kind": "list_region",
            "list_summary": {
                "item_selector": "li",
                "selected_item_indexes": [0],
                "container_locator_candidates": [{"locator": {"method": "css", "value": "ul.results"}}],
            },
        },
    )
    action = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Get the selected project open",
        description="Open selected project",
        output_key="open_project",
        output={"action_performed": True, "action_type": "click", "target": "Alpha"},
        region_scope={"region_id": "region-1", "mode": "region_scoped_snapshot"},
        region_context={"region_id": "region-1", "inferred_kind": "text_region"},
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    await page.get_by_text('Alpha').click()\n"
                "    return {'action_performed': True, 'action_type': 'click', 'target': 'Alpha'}"
            ),
            output={"action_performed": True, "action_type": "click", "target": "Alpha"},
        ),
    )

    for trace in (single_value, table, selected_list, action):
        script = TraceSkillCompiler().generate_script([trace], is_local=True)
        _assert_script_loads(script)
        body = _execute_body(script)

        assert trace_requires_runtime_ai_replay(trace) is False
        assert "_execute_runtime_ai_instruction" not in body


def test_region_table_extract_filters_to_selected_row_indexes():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract the selected table row",
        description="Extract selected row from selected region",
        output_key="selected_row",
        region_context={
            "inferred_kind": "table_region",
            "table_summary": {
                "headers": ["Name", "Price"],
                "selected_row_indexes": [2],
                "sample_rows": [["Beta", "$2"]],
                "row_count": 1,
                "locator_candidates": [
                    {"kind": "css", "locator": {"method": "css", "value": "table.orders"}}
                ],
            },
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "const selectedIndexes = new Set([2])" in body
    assert "(table) => {const selectedIndexes = new Set([2]);return Array.from(table.querySelectorAll('tr'))" in body
    assert ".filter((row, index) => selectedIndexes.has(index))" in body
    assert ";}})()" not in body
    assert "_execute_runtime_ai_instruction" not in body


def test_region_list_extract_filters_to_selected_item_indexes():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract selected list items",
        description="Extract selected list region",
        output_key="selected_items",
        region_context={
            "inferred_kind": "list_region",
            "list_summary": {
                "item_selector": "li",
                "selected_item_indexes": [1, 3],
                "sample_items": ["Second", "Fourth"],
                "item_count": 2,
                "container_locator_candidates": [
                    {"kind": "css", "locator": {"method": "css", "value": "ul.results"}}
                ],
            },
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert "const selectedIndexes = new Set([1, 3])" in body
    assert "(items) => {const selectedIndexes = new Set([1, 3]);return items" in body
    assert ".filter((item, index) => selectedIndexes.has(index))" in body
    assert ";}})()" not in body
    assert "_execute_runtime_ai_instruction" not in body


def test_region_table_extract_compiles_to_deterministic_row_arrays():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract the visible table rows",
        description="Extract table rows from selected region",
        output_key="order_rows",
        region_context={
            "inferred_kind": "table_region",
            "table_summary": {
                "headers": ["Order", "Status"],
                "locator_candidates": [
                    {
                        "selected": True,
                        "locator": {"method": "css", "value": "table.orders"},
                    }
                ],
            },
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert trace_requires_runtime_ai_replay(trace) is False
    assert "querySelectorAll('tr')" in body
    assert "querySelectorAll('th,td')" in body
    assert "_results['order_rows'] = _result" in body
    assert "_execute_runtime_ai_instruction" not in body


def test_region_list_sample_extract_compiles_to_repeated_item_texts():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract the selected cards",
        description="Extract list sample from selected region",
        output_key="cards",
        region_context={
            "inferred_kind": "list_sample",
            "list_summary": {
                "item_count": 3,
                "item_selector": "li[data-title=\"Owner's card\"]",
                "container_locator_candidates": [
                    {
                        "selected": True,
                        "locator": {"method": "css", "value": ".card-list"},
                    }
                ],
            },
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert trace_requires_runtime_ai_replay(trace) is False
    assert ".locator('li[data-title=\"Owner\\'s card\"]').evaluate_all" in body
    assert "_results['cards'] = _result" in body
    assert "_execute_runtime_ai_instruction" not in body


def test_region_table_missing_locator_preserves_runtime_ai_replay():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract the visible table rows",
        description="Extract table rows from selected region",
        output_key="order_rows",
        region_context={
            "inferred_kind": "table_region",
            "table_summary": {"headers": ["Order"], "sample_rows": [["A-1"]]},
        },
        ai_execution=RPAAIExecution(
            code="async def run(page, results):\n    return [['embedded']]",
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert trace_requires_runtime_ai_replay(trace) is True
    assert "_execute_runtime_ai_instruction" in body
    assert "await run(current_page, _results)" not in body


def test_region_list_missing_selector_preserves_runtime_ai_replay():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        user_instruction="Extract the selected cards",
        description="Extract list sample from selected region",
        output_key="cards",
        region_context={
            "inferred_kind": "list_sample",
            "list_summary": {
                "item_count": 3,
                "container_locator_candidates": [
                    {
                        "selected": True,
                        "locator": {"method": "css", "value": ".card-list"},
                    }
                ],
            },
        },
        ai_execution=RPAAIExecution(
            code="async def run(page, results):\n    return ['embedded']",
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    _assert_script_loads(script)
    body = _execute_body(script)

    assert trace_requires_runtime_ai_replay(trace) is True
    assert "_execute_runtime_ai_instruction" in body
    assert "await run(current_page, _results)" not in body


def test_navigation_after_selected_project_uses_dynamic_result_url():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            user_instruction="open the project most related to Python",
            output_key="selected_project",
            output={"url": "https://github.com/openai/openai-agents-python"},
            ai_execution=RPAAIExecution(
                code="async def run(page, results):\n    return {'url': 'https://github.com/openai/openai-agents-python'}",
            ),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/openai/openai-agents-python/pulls"),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)

    assert "_resolve_result_ref(_results, 'selected_project.url')" in script
    assert "+ '/pulls'" in script


def test_navigation_after_action_result_without_url_uses_current_page_not_result_ref():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            description="Click ordinal item",
            output_key="ordinal_item_action",
            output={"action_performed": True},
            after_page=RPAPageState(url="https://github.com/owner/recorded-repo"),
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    await page.locator('h2.lh-condensed a').nth(0).click()\n"
                    "    return {'action_performed': True}"
                ),
            ),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/owner/recorded-repo/pulls"),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "_resolve_first_result_ref(_results, ['ordinal_item_action.url', 'ordinal_item_action.value'])" not in body
    assert "https://github.com/owner/recorded-repo/pulls" not in body
    assert "_trace_page_url(current_page)" in body
    assert "+ '/pulls'" in body


def test_navigation_does_not_use_stale_observed_base_from_older_trace():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            description="Click old site item",
            output_key="old_action",
            output={"action_performed": True},
            after_page=RPAPageState(url="https://example.com"),
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    await page.get_by_text('Open').click()\n"
                    "    return {'action_performed': True}"
                ),
            ),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://other.com"),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://example.com/foo"),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "_target_url = str(_trace_page_url(current_page)).rstrip('/') + '/foo'" not in body
    assert "_target_url = 'https://example.com/foo'" in body


def test_navigation_after_manual_action_that_already_reached_url_is_skipped():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.MANUAL_ACTION,
            action="click",
            description='点击 link("Pull requests")',
            after_page=RPAPageState(url="https://github.com/owner/repo/pulls"),
            locator_candidates=[
                {"locator": {"method": "role", "role": "link", "name": "Pull requests"}, "selected": True},
            ],
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            description="导航到 https://github.com/owner/repo/pulls",
            after_page=RPAPageState(url="https://github.com/owner/repo/pulls"),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "get_by_role('link', name='Pull requests', exact=True).click()" in body
    assert "goto(_target_url" not in body
    assert "导航到 https://github.com/owner/repo/pulls" not in body


def test_manual_link_locator_defaults_to_exact_when_unspecified():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.MANUAL_ACTION,
        action="click",
        description='click link("Pull requests")',
        locator_candidates=[
            {"locator": {"method": "role", "role": "link", "name": "Pull requests"}, "selected": True},
        ],
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_role('link', name='Pull requests', exact=True).click()" in body


def test_manual_navigate_link_locator_does_not_default_to_exact():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.MANUAL_ACTION,
        action="click",
        description='click link("Issues")',
        locator_candidates=[
            {"locator": {"method": "role", "role": "link", "name": "Issues"}, "selected": True},
        ],
        signals={"navigation": {"url": "https://github.com/owner/repo/issues"}},
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "async with current_page.expect_navigation(wait_until='domcontentloaded'):" in body
    assert "get_by_role('link', name='Issues').click()" in body
    assert "get_by_role('link', name='Issues', exact=True).click()" not in body


def test_semantic_project_selection_compiles_to_runtime_ai_not_recorded_click():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/trending"),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="打开和python最相关的项目",
            description="Open the most Python-related trending project",
            output_key="selected_project",
            output={"url": "https://github.com/openai/openai-agents-python"},
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    await page.locator('a[href=\"/openai/openai-agents-python\"]').click()\n"
                    "    return {'url': 'https://github.com/openai/openai-agents-python'}"
                ),
            ),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" in body
    assert "RecordingRuntimeAgent" in script
    assert "page.locator('a[href=\"/openai/openai-agents-python\"]')" not in body


def test_chinese_semantic_project_click_without_url_stays_runtime_ai():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/trending"),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="\u6253\u5f00\u548c skill \u6700\u76f8\u5173\u7684\u9879\u76ee",
            description="Click the link for 'mattpocock / skills' repository",
            output_key="opened_skill_repo",
            output={"action_performed": True, "action_type": "click", "target": "mattpocock / skills"},
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    await page.get_by_role(\"link\", name=\"mattpocock / skills\").click()\n"
                    "    return {\"action_performed\": True, \"action_type\": \"click\", \"target\": \"mattpocock / skills\"}"
                ),
            ),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" in body
    assert "get_by_role(\"link\", name=\"mattpocock / skills\")" not in body


def test_runtime_ai_instruction_uses_runtime_model_config_kwarg_without_embedding_secret():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the project most related to SKILL",
        description="Click the semantically selected project",
        output_key="selected_project",
        output={"action_performed": True},
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator('a.project').nth(0).click()\n"
                "    return {'action_performed': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    prelude = _execute_prelude(script)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction" in prelude
    assert "_normalize_runtime_ai_payload" in prelude
    assert "_runtime_ai_model_config" in prelude
    assert "RecordingRuntimeAgent(model_config=_runtime_ai_model_config(kwargs))" in script
    assert "sk-secret" not in script
    assert "_execute_runtime_ai_instruction(current_page, _results, kwargs," in body


def test_runtime_ai_instruction_prefers_runtime_context_over_legacy_model_config():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the project most related to SKILL",
        description="Click the semantically selected project",
        output_key="selected_project",
        output={"action_performed": True},
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator('a.project').nth(0).click()\n"
                "    return {'action_performed': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)

    assert "_runtime_ai_model_config(kwargs)" in script
    assert "kwargs.get('_model_config')" in script


def test_runtime_ai_preserve_signal_overrides_embedded_code():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the closest matching project",
        description="Click the selected project",
        output_key="selected_project",
        output={"action_performed": True, "action_type": "click", "target": "alpha"},
        signals={"runtime_ai": {"preserve": True, "reason": "select_best_matching_candidate"}},
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator('a.project').nth(0).click()\n"
                "    return {'action_performed': True, 'action_type': 'click', 'target': 'alpha'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" in body
    assert "page.locator('a.project').nth(0).click()" not in body


def test_runtime_ai_preserve_signal_with_table_region_requires_runtime_context():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the closest matching table row",
        description="Click the selected table row",
        output_key="selected_row",
        output={"action_performed": True, "action_type": "click", "target": "alpha"},
        signals={"runtime_ai": {"preserve": True, "reason": "semantic_table_selection"}},
        region_context={
            "inferred_kind": "table_region",
            "table_summary": {
                "locator_candidates": [
                    {"kind": "css", "locator": {"method": "css", "value": "table.results"}}
                ],
            },
        },
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator('table.results tbody tr').nth(0).click()\n"
                "    return {'action_performed': True, 'action_type': 'click', 'target': 'alpha'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_side_effect_region_trace_without_embedded_code_requires_runtime_context():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="Click the first matching table row",
        description="Click the first matching table row",
        output_key="clicked_row",
        output={"action_performed": True, "action_type": "click", "target": "Alpha"},
        region_context={
            "inferred_kind": "table_region",
            "table_summary": {
                "locator_candidates": [
                    {"kind": "css", "locator": {"method": "css", "value": "table.results"}}
                ],
            },
        },
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" in body
    assert trace_requires_runtime_ai_replay(trace) is True


def test_generic_chinese_related_extraction_keeps_embedded_code():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="\u63d0\u53d6\u91c7\u8d2d\u76f8\u5173\u4fe1\u606f",
        description="Extract procurement related information",
        output_key="procurement_info",
        output={"name": "paper"},
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    return {'name': 'paper'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" not in body
    assert "return {'name': 'paper'}" in body


def test_domain_related_project_text_without_signal_keeps_embedded_code():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="\u6253\u5f00\u76f8\u5173\u9879\u76ee",
        description="Open related project",
        output_key="selected_item",
        output={"action_performed": True, "action_type": "click", "target": "alpha"},
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator('a.project').nth(0).click()\n"
                "    return {'action_performed': True, 'action_type': 'click', 'target': 'alpha'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" not in body
    assert "page.locator('a.project').nth(0).click()" in body


def test_related_result_text_without_signal_keeps_embedded_code():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="\u6253\u5f00\u76f8\u5173\u7ed3\u679c",
        description="Open related result",
        output_key="selected_item",
        output={"action_performed": True, "action_type": "click", "target": "alpha"},
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator('a.result').nth(0).click()\n"
                "    return {'action_performed': True, 'action_type': 'click', 'target': 'alpha'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" not in body
    assert "page.locator('a.result').nth(0).click()" in body


def test_manual_pull_request_click_keeps_recorded_locator_without_github_subpage_template():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            user_instruction="打开和python最相关的项目",
            output_key="selected_project",
            output={"url": "https://github.com/openai/openai-agents-python"},
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.MANUAL_ACTION,
            action="click",
            description='点击 link("Pull requests")',
            locator_candidates=[
                {"locator": {"method": "role", "role": "link", "name": "Pull requests"}},
            ],
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "_github_repo_base" not in body
    assert "+ '/pulls?q=is%3Apr'" not in body
    assert "get_by_role('link', name='Pull requests', exact=True).click()" in body


def test_pr_extraction_instruction_stays_runtime_ai_without_pulls_template():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            user_instruction="打开和python最相关的项目",
            output_key="selected_project",
            output={"url": "https://github.com/openai/openai-agents-python"},
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            user_instruction="收集当前仓库的前两页PR（无论是什么状态）的信息，要求记录每个pr的创建人和标题，输出严格为数组",
            output_key="pr_list",
            output=[{"title": "Recorded", "creator": "alice"}],
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" in body
    assert "_page_count = 2" not in body
    assert "_target_url = _repo_base + '/pulls?q=is%3Apr'" not in body
    assert "_target_url += f'&page={_page_number}'" not in body
    assert "rows[:10]" not in body


def test_pr_extraction_does_not_fallback_to_recorded_observed_repo_url():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/trending"),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="open the project most related to Python",
            output_key="selected_project",
            output=None,
            after_page=RPAPageState(url="https://github.com/openai/openai-agents-python"),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="collect the first 10 PRs in the current repository with title and creator",
            output_key="pr_list",
            output=[{"title": "Recorded", "creator": "alice"}],
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "https://github.com/openai/openai-agents-python" not in body
    assert "_execute_runtime_ai_instruction(" in body
    assert "_resolve_first_result_ref(_results, ['selected_project.url', 'selected_project.value'])" not in body
    assert "_target_url = _repo_base + '/pulls?q=is%3Apr'" not in body


def test_issue_extraction_after_highest_star_uses_dynamic_result_not_recorded_repo_url():
    traces = [
        RPAAcceptedTrace(
            trace_id="star",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="open the project with the highest star count",
            output_key="top_star_project",
            output=None,
            after_page=RPAPageState(url="https://github.com/ruvnet/RuView"),
        ),
        RPAAcceptedTrace(
            trace_id="issue",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="find the latest issue title",
            output_key="latest_issue_title",
            output={"latest_issue_title": "Recorded"},
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    await page.goto('https://github.com/ruvnet/RuView/issues?q=is%3Aissue')\n"
                    "    return {'latest_issue_title': 'Recorded'}"
                ),
            ),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "https://github.com/ruvnet/RuView/issues" not in body
    assert "_resolve_first_result_ref(_results, ['top_star_project.url', 'top_star_project.value'])" in body
    assert "+ '/issues?q=is%3Aissue'" in body


def test_navigation_after_pr_extraction_does_not_reuse_list_output_as_repo_url():
    traces = [
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            user_instruction="open the project with the highest star count",
            output_key="top_repo_result",
            output=None,
            after_page=RPAPageState(url="https://github.com/cline/cline"),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            user_instruction="extract PR titles and authors from the first two pages of the repository's pull requests list",
            output_key="pr_list",
            output=[{"title": "Recorded", "creator": "alice"}],
            after_page=RPAPageState(url="https://github.com/cline/cline/pulls?q=is%3Apr&page=2"),
        ),
        RPAAcceptedTrace(
            trace_type=RPATraceType.NAVIGATION,
            after_page=RPAPageState(url="https://github.com/cline/cline/pulls?page=2&q=is%3Apr+is%3Aopen"),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "_resolve_first_result_ref(_results, ['pr_list.url', 'pr_list.value'])" not in body
    assert "_resolve_first_result_ref(_results, ['top_repo_result.url', 'top_repo_result.value'])" in body
    assert "+ '/pulls?page=2&q=is%3Apr+is%3Aopen'" in body


def test_embedded_ai_code_rewrites_recorded_subpage_url_to_dynamic_previous_result():
    traces = [
        RPAAcceptedTrace(
            trace_id="star",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="open the project with the highest star count",
            output_key="top_starred_project",
            output={"url": "https://github.com/ruvnet/RuView"},
            ai_execution=RPAAIExecution(
                code="async def run(page, results):\n    return {'url': 'https://github.com/ruvnet/RuView'}",
            ),
        ),
        RPAAcceptedTrace(
            trace_id="issue",
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction="find the latest issue title",
            output_key="latest_issue_title",
            output={"latest_issue_title": "Recorded"},
            ai_execution=RPAAIExecution(
                code=(
                    "async def run(page, results):\n"
                    "    await page.goto('https://github.com/ruvnet/RuView/issues?q=is%3Aissue')\n"
                    "    return {'latest_issue_title': 'Recorded'}"
                ),
            ),
        ),
    ]

    script = TraceSkillCompiler().generate_script(traces, is_local=True)
    body = _execute_body(script)

    assert "https://github.com/ruvnet/RuView/issues" not in body
    assert "_resolve_result_ref(_results, 'top_starred_project.url')" in body
    assert "+ '/issues?q=is%3Aissue'" in body


def test_embedded_ai_code_rewrites_random_like_data_testid_locator_to_stable_role_candidate():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Open report menu",
        output_key="opened_menu",
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={"method": "css", "value": '[data-testid="menu-btn-a1b2c3d4"]'},
            stable_self_signals={"role": "button", "name": "Open menu"},
            unstable_signals=[{"attribute": "data-testid", "value": "menu-btn-a1b2c3d4"}],
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "button", "name": "Open menu"},
                    source="snapshot_actionable_node",
                    confidence="high",
                )
            ],
        ),
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator('[data-testid=\"menu-btn-a1b2c3d4\"]').click()\n"
                "    return {'opened': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_role('button', name='Open menu')" in body
    assert '[data-testid="menu-btn-a1b2c3d4"]' not in body


def test_embedded_ai_code_preserves_random_like_locator_when_multiple_candidates_exist():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Open menu",
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={"method": "css", "value": '[data-testid="menu-btn-a1b2c3d4"]'},
            unstable_signals=[{"attribute": "data-testid", "value": "menu-btn-a1b2c3d4"}],
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "button", "name": "Open"},
                    source="snapshot",
                    confidence="high",
                ),
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "button", "name": "Open"},
                    source="anchor",
                    confidence="high",
                ),
            ],
        ),
        ai_execution=RPAAIExecution(
            code="async def run(page, results):\n    await page.locator('[data-testid=\"menu-btn-a1b2c3d4\"]').click()",
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert '[data-testid="menu-btn-a1b2c3d4"]' in body


def test_embedded_ai_code_preserves_collection_locator_when_nth_is_applied_to_variable():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Click table row column action",
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={
                "method": "css",
                "value": "#taskExportGridTable tbody.igrid-data tr.grid-row",
            },
            unstable_signals=[{"attribute": "id", "value": "taskExportGridTable"}],
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "link", "name": "W3主页"},
                    source="snapshot_actionable_node",
                    confidence="high",
                )
            ],
        ),
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    _rows = page.locator('#taskExportGridTable tbody.igrid-data tr.grid-row')\n"
                "    _row = _rows.nth(0)\n"
                "    await _row.locator('td[field=\"tmpName\"] a').click()\n"
                "    return {'action_performed': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "page.locator('#taskExportGridTable tbody.igrid-data tr.grid-row')" in body
    assert "_rows.nth(0)" in body
    assert "get_by_role('link', name='W3主页')" not in body


def test_embedded_ai_code_preserves_non_random_locator_even_when_stable_candidate_exists():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Click search",
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={"method": "css", "value": '[data-testid="search-button"]'},
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "button", "name": "Search"},
                    source="snapshot_actionable_node",
                    confidence="high",
                )
            ],
        ),
        ai_execution=RPAAIExecution(
            code="async def run(page, results):\n    await page.locator('[data-testid=\"search-button\"]').click()",
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert '[data-testid="search-button"]' in body
    assert "get_by_role('button', name='Search')" not in body


def test_embedded_ai_code_uses_single_anchor_scoped_candidate_when_self_signal_is_ambiguous():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Open report menu",
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={"method": "css", "value": '[data-testid="menu-btn-a1b2c3d4"]'},
            stable_anchor_signals={"title": "Quarterly Report"},
            unstable_signals=[{"attribute": "data-testid", "value": "menu-btn-a1b2c3d4"}],
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={
                        "method": "nested",
                        "parent": {"method": "text", "value": "Quarterly Report"},
                        "child": {"method": "role", "role": "button", "name": "Open menu"},
                    },
                    source="snapshot_anchor_scope",
                    confidence="high",
                )
            ],
        ),
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator('[data-testid=\"menu-btn-a1b2c3d4\"]').click()\n"
                "    return {'opened': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_text('Quarterly Report').get_by_role('button', name='Open menu')" in body


def test_embedded_ai_code_does_not_rewrite_without_unstable_signal_even_if_alternate_locator_exists():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Open menu",
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={"method": "css", "value": '[aria-label="Open menu"]'},
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "button", "name": "Open menu"},
                    source="snapshot_actionable_node",
                    confidence="high",
                )
            ],
        ),
        ai_execution=RPAAIExecution(
            code="async def run(page, results):\n    await page.locator('[aria-label=\"Open menu\"]').click()",
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert '[aria-label="Open menu"]' in body
