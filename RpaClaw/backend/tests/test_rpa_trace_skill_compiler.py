from backend.rpa.trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPADataflowMapping,
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


def test_compiler_generalizes_highest_star_trace_instead_of_hardcoding_url():
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

    assert "stargazers" in script
    assert "max_stars" in script
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
    assert "_validate_non_empty_records('top10_prs', _result)" in script


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
    assert "await current_page.get_by_role('textbox', name='Customer Name').fill(str(_value))" in script
    assert "Alice Zhang" not in _execute_body(script)


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


def test_manual_pull_request_click_compiles_to_dynamic_repo_subpage_navigation():
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

    assert "_github_repo_base(str(_resolve_result_ref(_results, 'selected_project.url')))" in body
    assert "+ '/pulls?q=is%3Apr'" in body
    assert "get_by_role('link', name='Pull requests').click()" not in body


def test_pr_extraction_two_pages_all_states_uses_paged_dynamic_pulls_template():
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

    assert "_page_count = 2" in body
    assert "_target_url = _repo_base + '/pulls?q=is%3Apr'" in body
    assert "_target_url += f'&page={_page_number}'" in body
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
    assert "_resolve_first_result_ref(_results, ['selected_project.url', 'selected_project.value'])" in body
    assert "_target_url = _repo_base + '/pulls?q=is%3Apr'" in body


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
