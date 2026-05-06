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


def test_compiler_does_not_swallow_recovered_attempt_without_postcondition():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Submit form before repair validation",
                user_instruction="submit the form and verify the result",
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    await page.get_by_role('button', name='Submit').click()\n    raise RuntimeError('terminal state not observed')",
                    error="terminal state not observed",
                ),
                signals={"recovered_attempt": {"ignore_errors": True}},
            ),
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract created identifier",
                user_instruction="verify created identifier",
                output_key="created",
                output={"id": "ID-1"},
                ai_execution=RPAAIExecution(language="python", code="async def run(page, results):\n    return {'id': 'ID-1'}"),
            ),
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "_recovered_attempt_errors" not in body
    assert "raise RuntimeError('terminal state not observed')" in body
    assert "_results['created'] = _result" in body


def test_compiler_keeps_recovered_attempt_failures_fatal_even_with_postcondition():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Submit form before repair validation",
                user_instruction="submit the form and verify the result",
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    await page.get_by_role('button', name='Submit').click()\n    raise RuntimeError('terminal state not observed')",
                    error="terminal state not observed",
                ),
                signals={"recovered_attempt": {"ignore_errors": True}},
                postcondition={
                    "kind": "table_row_exists",
                    "table_headers": ["ID", "Status"],
                    "key": {"ID": "ID-1"},
                    "expect": {"Status": "Created"},
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "_recovered_attempt_errors" not in body
    assert "_find_table_row_by_headers(" in body
    assert "raise RuntimeError('terminal state not observed')" in body


def test_compiler_keeps_idempotent_replay_failures_fatal():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Complete existing task",
                user_instruction="complete task TASK-001",
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    raise RuntimeError('task row not found')",
                ),
                signals={"idempotent_postcondition_replay": {"ignore_precondition_errors": True}},
                postcondition={
                    "kind": "table_row_absent",
                    "table_headers": ["Task ID"],
                    "key": {"Task ID": "TASK-001"},
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "_recovered_attempt_errors" not in body
    assert "raise RuntimeError('task row not found')" in body
    assert "verify table row absence postcondition" in body


def test_embedded_ai_code_preserves_bare_text_click_strictness():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Open section",
                user_instruction="open the section",
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    await page.get_by_text('合同台账', exact=True).click()\n    return {'action_performed': True}",
                ),
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "page.get_by_text('合同台账', exact=True).click()" in body
    assert "page.get_by_text('合同台账', exact=True).first.click()" not in body


def test_snapshot_table_cell_evidence_compiles_to_structural_row_extract():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract generated order status",
                user_instruction="extract generated order status",
                output_key="order",
                output={"order_no": "PO-001", "status": "pending_approval"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"status": "pending_approval"}),
                signals={
                    "extract_snapshot": {
                        "fields": [
                            {
                                "label": "order_no",
                                "value": "PO-001",
                                "text_pattern": {"prefix": "Created:", "suffix": ""},
                                "table_cell": {
                                    "table_headers": ["Order", "Status"],
                                    "row_key": {"Order": "PO-001"},
                                    "column_header": "Order",
                                    "column_index": 0,
                                },
                                "replay_required": True,
                            },
                            {
                                "label": "status",
                                "value": "pending_approval",
                                "table_cell": {
                                    "table_headers": ["Order", "Status"],
                                    "row_key": {"Order": "PO-001"},
                                    "column_header": "Status",
                                    "column_index": 1,
                                },
                                "replay_required": True,
                            },
                        ]
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "_execute_runtime_ai_instruction(" not in body
    assert "_extract_table_cell_value(current_page" in body
    assert "'row_key': {'Order': 'PO-001'}" in body
    assert "_extract_text_pattern_value(" not in body
    assert "async def _find_table_row_by_headers(" in script
    assert "async def _extract_table_cell_value(" in script


def test_table_postcondition_does_not_use_cross_table_ancestor_fallback():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Verify row exists",
                user_instruction="verify row exists",
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return {'action_performed': True}",
                ),
                postcondition={
                    "kind": "table_row_exists",
                    "table_headers": ["ID", "Status"],
                    "key": {"ID": "ROW-1"},
                    "expect": {"Status": "Ready"},
                },
            )
        ],
        is_local=True,
    )

    assert "root_body_rows" not in script
    assert "ancestor::*[count(.//table)" not in script


def test_snapshot_unique_text_only_fields_fall_back_to_runtime_ai():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Confirm generated row",
                user_instruction="confirm generated row",
                output_key="row",
                output={"status": "pending_approval"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"status": "pending_approval"}),
                signals={
                    "extract_snapshot": {
                        "fields": [
                            {
                                "label": "status",
                                "value": "pending_approval",
                                "unique_text": {"text": "pending_approval", "tag": "td"},
                                "replay_required": True,
                            }
                        ]
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "_execute_runtime_ai_instruction(" in body
    assert "_extract_unique_text_value" not in body


def test_runtime_ai_only_script_omits_unused_snapshot_and_download_helpers():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.NAVIGATION,
                after_page=RPAPageState(url="https://example.test/items"),
            ),
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Select most relevant item",
                user_instruction="select the most relevant item",
                output_key="selected_item",
                output={"value": "alpha"},
                signals={"runtime_ai": {"preserve": True, "reason": "semantic_candidate_selection"}},
            ),
        ],
        is_local=True,
    )

    assert "_execute_runtime_ai_instruction(" in script
    assert "def _normalize_runtime_ai_payload" in script
    assert "async def _extract_display_field_value" not in script
    assert "async def _extract_node_text_or_value" not in script
    assert "def _extract_url_path_value" not in script
    assert "async def _extract_text_pattern_value" not in script
    assert "async def _download_from_export_task" not in script


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


def test_snapshot_data_prop_extract_uses_generic_display_value_selectors_by_default():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract field",
                output_key="detail",
                output={"Amount": "100.00"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"Amount": "100.00"}),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "fields": [
                            {
                                "label": "Amount",
                                "value": "100.00",
                                "data_prop": "amount",
                                "visible": True,
                            }
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "async def _extract_display_field_value(field, value_selectors=None):" in script
    assert "_extract_display_field_value(_field, (" in body
    assert "[data-value]" in body
    assert ".aui-input-display-only__content" not in script
    assert ".aui-numeric-display-only__value" not in script


def test_snapshot_framework_adapter_does_not_inject_private_display_value_selectors():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract field from framework page",
                output_key="detail",
                output={"Amount": "100.00"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"Amount": "100.00"}),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "fields": [
                            {
                                "label": "Amount",
                                "value": "100.00",
                                "data_prop": "amount",
                                "visible": True,
                                "adapter": "aui",
                            }
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "_extract_display_field_value(_field, (" in body
    assert ".aui-input-display-only__content" not in script
    assert ".aui-numeric-display-only__value" not in script
    assert "[data-value]" in body


def test_snapshot_explicit_value_selector_is_preserved_as_field_evidence():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract field with observed value selector",
                output_key="detail",
                output={"Amount": "100.00"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"Amount": "100.00"}),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "fields": [
                            {
                                "label": "Amount",
                                "value": "100.00",
                                "data_prop": "amount",
                                "visible": True,
                                "value_selector": ".display-value",
                            }
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "_extract_display_field_value(_field, (" in body
    assert ".display-value" in body
    assert "[data-value]" in body


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


def test_compiler_falls_back_when_snapshot_output_labels_have_no_replayable_evidence():
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

    assert "_execute_runtime_ai_instruction" in body
    assert "purchase_info" in body
    assert "aui-form-item" not in body
    assert "100.00" not in body
    assert "USD" not in body


def test_compiler_does_not_treat_output_schema_keys_as_visible_dom_labels():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract repository summary",
                user_instruction="Extract repository project name, stars, and forks",
                output_key="repo_basic_info",
                output={"project_name": "mattpocock/skills", "star_count": "32.2k", "fork_count": "2.5k"},
                ai_execution=RPAAIExecution(
                    language="snapshot",
                    code="",
                    output={"project_name": "mattpocock/skills", "star_count": "32.2k", "fork_count": "2.5k"},
                ),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "section_title": "Repository summary",
                        "fields": [
                            {"label": "project_name", "value": "mattpocock/skills", "replay_required": True},
                            {"label": "star_count", "value": "32.2k", "replay_required": True},
                            {"label": "fork_count", "value": "2.5k", "replay_required": True},
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction" in body
    assert "repo_basic_info" in body
    assert "normalize-space()='project_name'" not in body
    assert "normalize-space()='star_count'" not in body
    assert "normalize-space()='fork_count'" not in body
    assert "mattpocock/skills" not in body
    assert "32.2k" not in body
    assert "2.5k" not in body


def test_compiler_renders_snapshot_extract_from_recorded_replay_evidence_without_runtime_ai():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract repository summary",
                user_instruction="Extract repository project name, stars, and forks",
                output_key="repo_basic_info",
                output={"project_name": "mattpocock/skills", "star_count": "32.2k", "fork_count": "2.5k"},
                ai_execution=RPAAIExecution(
                    language="snapshot",
                    code="",
                    output={"project_name": "mattpocock/skills", "star_count": "32.2k", "fork_count": "2.5k"},
                ),
                signals={
                    "extract_snapshot": {
                        "source": "visible_page",
                        "fields": [
                            {
                                "label": "project_name",
                                "value": "mattpocock/skills",
                                "replay_required": True,
                                "url_extraction": {
                                    "kind": "url_path_join",
                                    "start": 0,
                                    "count": 2,
                                    "separator": "/",
                                },
                            },
                            {
                                "label": "star_count",
                                "value": "32.2k",
                                "replay_required": True,
                                "text_pattern": {"tag": "a", "value": "32.2k", "prefix": "", "suffix": "stars"},
                            },
                            {
                                "label": "fork_count",
                                "value": "2.5k",
                                "replay_required": True,
                                "text_pattern": {"tag": "a", "value": "2.5k", "prefix": "", "suffix": "forks"},
                            },
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction" not in body
    assert "_extract_url_path_value(current_page.url" in body
    assert "_extract_text_pattern_value(current_page" in body
    assert "_results['repo_basic_info'] = _result" in body
    assert "normalize-space()='project_name'" not in body
    assert "aui-form-item" not in body
    assert "mattpocock/skills" not in body
    assert "32.2k" not in body
    assert "2.5k" not in body


def test_compiler_does_not_use_snapshot_unique_text_as_replay_locator():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract visible detail fields",
                output_key="detail_fields",
                output={"supplier_name": "Acme Data Ltd"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"supplier_name": "Acme Data Ltd"}),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "fields": [
                            {
                                "label": "supplier_name",
                                "value": "Acme Data Ltd",
                                "replay_required": True,
                                "unique_text": {"text": "Acme Data Ltd", "tag": "td"},
                            }
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" in body
    assert "_extract_unique_text_value" not in body


def test_snapshot_extract_uses_visible_label_adjacency_without_recorded_values():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract visible detail fields",
                output_key="contract_fields",
                output={
                    "合同编号": "CT-2026-RPA-001",
                    "供应商名称": "上海智采云科技有限公司",
                },
                ai_execution=RPAAIExecution(
                    language="snapshot",
                    code="",
                    output={
                        "合同编号": "CT-2026-RPA-001",
                        "供应商名称": "上海智采云科技有限公司",
                    },
                ),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "fields": [
                            {
                                "label": "合同编号",
                                "value": "CT-2026-RPA-001",
                                "replay_required": True,
                                "unique_text": {"text": "CT-2026-RPA-001", "tag": "td"},
                            },
                            {
                                "label": "供应商名称",
                                "value": "上海智采云科技有限公司",
                                "replay_required": True,
                                "unique_text": {"text": "上海智采云科技有限公司", "tag": "td"},
                            },
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" not in body
    assert "_extract_labeled_field_value(current_page, '合同编号')" in body
    assert "_extract_labeled_field_value(current_page, '供应商名称')" in body
    assert "CT-2026-RPA-001" not in body
    assert "上海智采云科技有限公司" not in body
    assert "_extract_unique_text_value" not in body


def test_snapshot_extract_uses_nested_visible_label_for_normalized_output_keys():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract normalized detail fields",
                output_key="contract_fields",
                output={"contract_number": {"label": "合同编号", "value": "CT-001"}},
                ai_execution=RPAAIExecution(
                    language="snapshot",
                    code="",
                    output={"contract_number": {"label": "合同编号", "value": "CT-001"}},
                ),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "fields": [
                            {
                                "label": "contract_number",
                                "value": "CT-001",
                                "observed_label": "合同编号",
                                "replay_required": True,
                            },
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" not in body
    assert "_extract_labeled_field_value(current_page, '合同编号')" in body
    assert "_result['contract_number'] = _value" in body
    assert "CT-001" not in body


def test_snapshot_extract_prefers_observed_dom_label_over_semantic_output_label():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract detail field with semantic alias",
                output_key="contract_fields",
                output={"合规条款摘要": "供应商须满足数据本地化要求。"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"合规条款摘要": "供应商须满足数据本地化要求。"}),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "fields": [
                            {
                                "label": "合规条款摘要",
                                "value": "供应商须满足数据本地化要求。",
                                "observed_label": "合规条款",
                                "replay_required": True,
                            },
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" not in body
    assert "_extract_labeled_field_value(current_page, '合规条款')" in body
    assert "_extract_labeled_field_value(current_page, '合规条款摘要')" not in body
    assert "_result['合规条款摘要'] = _value" in body
    assert "供应商须满足数据本地化要求。" not in body


def test_ai_operation_with_url_output_and_replayable_code_embeds_code_without_runtime_ai():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Open selected record",
                output_key="opened_record",
                output={"url": "https://example.test/records/123"},
                ai_execution=RPAAIExecution(
                    code=(
                        "async def run(page, results):\n"
                        "    await page.get_by_role('link', name='Open').click()\n"
                        "    return {'url': page.url}"
                    )
                ),
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "_execute_runtime_ai_instruction(" not in body
    assert "get_by_role('link', name='Open')" in body
    assert "_results['opened_record'] = _result" in body


def test_snapshot_extract_prefers_recorded_value_locator_and_fails_when_required_missing():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Extract visible detail",
                output_key="detail",
                output={"Amount": "100.00"},
                ai_execution=RPAAIExecution(language="snapshot", code="", output={"Amount": "100.00"}),
                signals={
                    "extract_snapshot": {
                        "source": "detail_views",
                        "section_title": "Detail",
                        "fields": [
                            {
                                "label": "Amount",
                                "value": "100.00",
                                "visible": True,
                                "required": True,
                                "value_locator": {"method": "css", "value": "[data-field='amount']"},
                            }
                        ],
                    }
                },
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "current_page.locator(\"[data-field='amount']\").first" in body
    assert "_missing_required_fields" in body
    assert "raise RuntimeError(f\"Snapshot extract missing required fields: {_missing_required_fields}\")" in body
    assert "100.00" not in body


def test_compiler_restores_recorded_start_url_for_ai_only_trace():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                description="Read visible details",
                before_page=RPAPageState(url="https://example.test/details/123"),
                output_key="details",
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return {'ok': True}",
                ),
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)

    assert "await current_page.goto('https://example.test/details/123', wait_until='domcontentloaded')" in body
    assert body.index("await current_page.goto('https://example.test/details/123'") < body.index("# trace 0:")


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
                signals={"runtime_ai": {"preserve": True, "reason": "semantic_candidate_selection"}},
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


def test_compiler_does_not_preserve_structural_top_row_click_as_runtime_ai():
    script = TraceSkillCompiler().generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-top-row",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="click the top row",
                output_key="selected_row",
                output={"clicked": True},
                ai_execution=RPAAIExecution(
                    language="python",
                    code=(
                        "async def run(page, results):\n"
                        "    await page.locator('tbody tr').first.click()\n"
                        "    return {'clicked': True}"
                    ),
                ),
            )
        ],
        is_local=True,
    )

    body = _execute_body(script)
    assert "_execute_runtime_ai_instruction(" not in body
    assert "page.locator('tbody tr').first.click()" in body


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
    assert "    _result = await run(current_page, _results)" in body
    assert body.index("    _result = await run(current_page, _results)") < body.index("    _download_payload = await _download_from_export_task(")
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
            signals={"runtime_ai": {"preserve": True, "reason": "semantic_candidate_selection"}},
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


def test_embedded_ai_code_rewrites_declared_input_binding_literal_to_kwargs_default():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Open invoice details",
        output_key="invoice_details",
        input_bindings={
            "invoice_number": {
                "source": "user_param",
                "default": "INV-001",
                "classification": "user_param",
            }
        },
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.get_by_role('textbox', name='Invoice number').fill('INV-001')\n"
                "    await page.get_by_role('button', name='INV-001').click()\n"
                "    await page.get_by_role('button', name='Search').click()\n"
                "    return {'invoice_number': 'INV-001'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "kwargs.get('invoice_number', 'INV-001')" in body
    assert ".fill('INV-001')" not in body
    assert "name='Invoice number'" in body
    assert "name=kwargs.get('invoice_number', 'INV-001')" in body


def test_embedded_ai_code_does_not_corrupt_prefixed_string_literals():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Read invoice patterns",
        input_bindings={
            "invoice_number": {
                "source": "user_param",
                "default": "INV-001",
                "classification": "user_param",
            }
        },
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    formatted = f'INV-001'\n"
                "    raw = r'INV-001'\n"
                "    await page.get_by_role('textbox', name='Invoice number').fill('INV-001')\n"
                "    return {'formatted': formatted, 'raw': raw}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "formatted = f'INV-001'" in body
    assert "raw = r'INV-001'" in body
    assert "fkwargs.get" not in body
    assert "rkwargs.get" not in body
    assert ".fill(kwargs.get('invoice_number', 'INV-001'))" in body


def test_embedded_ai_code_parameterizes_dynamic_values_inside_text_locators():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Search invoice row",
        input_bindings={
            "invoice_number": {
                "source": "user_param",
                "default": "INV-001",
                "classification": "user_param",
            }
        },
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.get_by_text('INV-001').click()\n"
                "    row = page.locator('tr').filter(has_text='INV-001')\n"
                "    await page.get_by_role('textbox', name='Invoice number').fill('INV-001')\n"
                "    return {'invoice_number': 'INV-001'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "page.get_by_text(kwargs.get('invoice_number', 'INV-001'))" in body
    assert "filter(has_text=kwargs.get('invoice_number', 'INV-001'))" in body
    assert ".fill(kwargs.get('invoice_number', 'INV-001'))" in body
    assert "return {'invoice_number': kwargs.get('invoice_number', 'INV-001')}" in body


def test_embedded_ai_code_preserves_stable_ui_labels_while_parameterizing_dynamic_values():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Search invoice row",
        input_bindings={
            "invoice_number": {
                "source": "user_param",
                "default": "INV-001",
                "classification": "user_param",
            }
        },
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.get_by_label('Invoice number').fill('INV-001')\n"
                "    await page.get_by_placeholder('Search invoices').fill('INV-001')\n"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_label('Invoice number')" in body
    assert "get_by_placeholder('Search invoices')" in body
    assert ".fill(kwargs.get('invoice_number', 'INV-001'))" in body


def test_table_row_postcondition_includes_generic_header_scoped_helper():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Verify invoice row",
        output_key="invoice_row",
        input_bindings={
            "invoice_number": {
                "source": "user_param",
                "default": "INV-001",
                "classification": "user_param",
            }
        },
        postcondition={
            "kind": "table_row_exists",
            "table_headers": ["Invoice", "Project", "Status"],
            "row_selector": ".invoice-grid tbody tr",
            "key": {"Invoice": "{{invoice_number}}", "Project": "Project Alpha"},
            "expect": {"Status": "Submitted"},
        },
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    return {'status': 'Submitted'}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "async def _find_table_row_by_headers(" in script
    assert "table_headers" in script
    assert "key_values" in script
    assert "role=grid" in script
    assert "role=columnheader" in script
    assert "row_sets.append(page.locator('tbody tr, [role=row]'))" not in script
    assert "split_body_rows = table.locator('xpath=following-sibling::table[1]//tbody/tr')" in script
    assert "same_split_root = await table.evaluate" in script
    assert "ancestor::*[.//table[.//tbody/tr]" not in script
    assert "row_text" not in script
    assert "direct_rows = page.locator(row_selector)" in script
    assert (
        "await _find_table_row_by_headers(current_page, ['Invoice', 'Project', 'Status'], "
        "{'Invoice': kwargs.get('invoice_number', 'INV-001'), 'Project': 'Project Alpha', 'Status': 'Submitted'}, "
        "row_selector='.invoice-grid tbody tr')"
        in body
    )
    assert "InvoiceApp" not in script
    assert "taskExportGridTable" not in script
    assert "github" not in script.lower()


def test_export_task_signal_respects_code_that_already_handles_download():
    trace = RPAAcceptedTrace(
        trace_id="ai-export-download",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="download the generated report",
        description="Download generated report",
        output_key="report_download",
        signals={
            "download": {
                "filename": "report.xlsx",
                "trigger_mode": "export_task",
            }
        },
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    async with page.expect_download() as dl_info:\n"
                "        await page.get_by_role('link', name='Download').click()\n"
                "    dl = await dl_info.value\n"
                "    await dl.save_as('report.xlsx')\n"
                "    return {'downloaded': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "_download_from_export_task(" not in body
    assert "async with page.expect_download() as dl_info:" in body
    assert "_results['report_download'] = _result" in body


def test_table_row_absence_postcondition_compiles_to_negative_check():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Verify task removed from current worklist",
        input_bindings={
            "task_id": {
                "source": "user_param",
                "default": "TASK-001",
                "classification": "user_param",
            }
        },
        postcondition={
            "kind": "table_row_absent",
            "table_headers": ["Task ID", "Status"],
            "key": {"Task ID": "{{task_id}}"},
        },
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    return {'submitted': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "async def _find_table_row_by_headers(" in script
    assert "verify table row absence postcondition" in body
    assert "timeout_ms=1500" in body
    assert "Table row matching postcondition was still present" in body


def test_embedded_ai_code_rewrites_double_quoted_random_like_locator_to_stable_candidate():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Open invoice menu",
        output_key="opened_menu",
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={"method": "css", "value": '[data-testid="invoice-menu-a1b2c3d4"]'},
            stable_self_signals={"role": "button", "name": "Open invoice menu"},
            unstable_signals=[{"attribute": "data-testid", "value": "invoice-menu-a1b2c3d4"}],
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "button", "name": "Open invoice menu"},
                    source="snapshot_actionable_node",
                    confidence="high",
                )
            ],
        ),
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    await page.locator(\"[data-testid=\\\"invoice-menu-a1b2c3d4\\\"]\").click()\n"
                "    return {'opened': True}"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "get_by_role('button', name='Open invoice menu')" in body
    assert "invoice-menu-a1b2c3d4" not in body


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


def test_embedded_ai_code_does_not_rewrite_fill_locator_to_non_editable_role():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Search by filter",
        user_instruction="search for a record number",
        output_key="search_result",
        output={"empty_state": "No matching results"},
        ai_execution=RPAAIExecution(
            language="python",
            code=(
                "async def run(page, results):\n"
                "    search = page.locator('[data-testid=\"contract-number-filter\"]')\n"
                "    await search.fill('CT-2026-RPA-NOT-FOUND')\n"
                "    return {'empty_state': 'No matching results'}\n"
            ),
        ),
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={"method": "css", "value": '[data-testid="contract-number-filter"]'},
            unstable_signals=[{"attribute": "data-testid", "value": "contract-number-filter"}],
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "menuitem", "name": "Dashboard"},
                    source="snapshot_actionable_node",
                    confidence="high",
                )
            ],
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "page.locator('[data-testid=\"contract-number-filter\"]')" in body
    assert "get_by_role('menuitem'" not in body


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


def test_embedded_ai_code_preserves_structural_table_row_locator():
    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Open row detail",
        locator_stability=RPALocatorStabilityMetadata(
            primary_locator={"method": "css", "value": ".el-table__body-wrapper tbody tr"},
            unstable_signals=[{"attribute": "class", "value": "el-table__body-wrapper"}],
            stable_self_signals={"role": "menuitem", "name": "Home"},
            alternate_locators=[
                RPALocatorStabilityCandidate(
                    locator={"method": "role", "role": "menuitem", "name": "Home"},
                    source="snapshot_actionable_node",
                    confidence="high",
                )
            ],
        ),
        ai_execution=RPAAIExecution(
            code=(
                "async def run(page, results):\n"
                "    row = page.locator('.el-table__body-wrapper tbody tr').filter(has_text='INV-001')\n"
                "    await row.get_by_role('button', name='Open').click()\n"
            ),
        ),
    )

    script = TraceSkillCompiler().generate_script([trace], is_local=True)
    body = _execute_body(script)

    assert "page.locator('.el-table__body-wrapper tbody tr').filter(has_text='INV-001')" in body
    assert "get_by_role('menuitem', name='Home')" not in body


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
