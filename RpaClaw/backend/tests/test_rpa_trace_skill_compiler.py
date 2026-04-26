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
from backend.rpa.trace_skill_compiler import TraceSkillCompiler


def _execute_body(script: str) -> str:
    start = script.index("async def execute_skill")
    return script[start:]


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
    assert 'current_page = tabs["tab-root"]' in body


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
    assert 'current_page = tabs["tab-sales"]' in body
    assert "await current_page.bring_to_front()" in body


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
    assert 'current_page = tabs["tab-root"]' in body


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
    body = _execute_body(script)

    assert "_download_from_export_task(" in body
    assert "table_heading='导出列表'" in body
    assert "action_selector='td[data-colid=\"col_25\"] a'" in body
    assert "            _result = await run(current_page, _results)" not in body
    assert '_results["download_Conclusion_excelExport_17733824_20260426211105"]' in body
    assert "_results['table_row_action'] = _result" in body


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
