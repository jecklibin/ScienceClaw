import pytest

from backend.rpa.mcp_converter import RpaMcpConverter
from backend.rpa.mcp_models import RpaMcpToolDefinition


class FakeSemanticInferer:
    async def infer(self, **_kwargs):
        from backend.rpa.mcp_semantic_inferer import RpaMcpSemanticRecommendation

        return RpaMcpSemanticRecommendation(
            source="ai_inferred",
            tool_name="search_reports",
            display_name="Search reports",
            description="Search reports by keyword.",
            input_schema={
                "type": "object",
                "properties": {
                    "report_keyword": {
                        "type": "string",
                        "description": "Keyword used to search reports.",
                        "default": "cancer",
                    },
                },
                "required": ["report_keyword"],
            },
            params={
                "report_keyword": {
                    "original_value": "cancer",
                    "type": "string",
                    "description": "Keyword used to search reports.",
                    "required": True,
                    "confidence": 0.9,
                },
            },
            confidence=0.9,
            warnings=[],
            model="fake-model",
        )


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
    assert tool.requires_cookies is False
    assert tool.output_schema["properties"]["data"]["type"] == "object"
    assert tool.recommended_output_schema["properties"]["data"]["type"] == "object"
    assert tool.output_schema_confirmed is False
    assert tool.output_examples == []
    assert tool.allowed_domains == ["example.com"]
    assert tool.sanitize_report.removed_step_details == []
    assert tool.sanitize_report.warnings == []
    assert tool.semantic_inference["source"] == "rule_inferred"
    assert tool.schema_source == "rule_inferred"


def test_preview_strips_login_steps_and_sensitive_params():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/login", "description": "Open login"},
        {"action": "fill", "target": '{"method":"label","value":"Email"}', "value": "alice@example.com", "description": "Fill email"},
        {"action": "fill", "target": '{"method":"label","value":"Password"}', "value": "{{credential}}", "description": "Fill password", "sensitive": True},
        {"action": "click", "target": '{"method":"role","role":"button","name":"Sign in"}', "description": "Sign in"},
        {"action": "navigate", "url": "https://example.com/dashboard", "description": "Open dashboard"},
        {"action": "click", "target": '{"method":"role","role":"button","name":"Export"}', "description": "Export invoice"},
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
    assert preview.requires_cookies is True
    assert preview.sanitize_report.removed_params == ["email", "password"]
    assert [step["description"] for step in preview.steps] == ["Open dashboard", "Export invoice"]
    assert "cookies" in preview.input_schema["required"]
    assert "password" not in preview.input_schema["properties"]


def test_preview_without_login_does_not_require_cookies_or_warning():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/workspace", "description": "Open workspace"},
        {"action": "click", "target": '{"method":"role","role":"button","name":"Export"}', "description": "Export invoice"},
    ]

    preview = converter.preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="skill",
        name="workspace_tool",
        description="Workspace tool",
        steps=steps,
        params={"month": {"original_value": "2026-03", "description": "Invoice month"}},
    )

    assert preview.requires_cookies is False
    assert "cookies" not in preview.input_schema["properties"]
    assert "cookies" not in preview.input_schema["required"]
    assert preview.sanitize_report.warnings == []


def test_preview_strips_auth_range_when_login_submit_is_missing():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/login", "description": "Open login"},
        {"action": "fill", "target": '{"method":"label","value":"Email"}', "value": "alice@example.com", "description": "Fill email"},
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

    assert preview.requires_cookies is True
    assert preview.sanitize_report.removed_steps == [0, 1]
    assert [step["description"] for step in preview.steps] == ["Open workspace"]


def test_preview_builds_recommended_output_schema_from_recording_signals():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/workspace", "description": "Open workspace"},
        {
            "action": "extract_text",
            "target": '{"method":"role","role":"heading","name":"Invoice total"}',
            "description": "Capture invoice total",
            "result_key": "invoice_total",
        },
        {
            "action": "click",
            "target": '{"method":"role","role":"button","name":"Download invoice"}',
            "description": "Download invoice",
            "signals": {"download": {"filename": "invoice.pdf"}},
        },
    ]

    preview = converter.preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="skill",
        name="invoice_tool",
        description="Invoice tool",
        steps=steps,
        params={},
    )

    data_schema = preview.recommended_output_schema["properties"]["data"]
    assert data_schema["type"] == "object"
    assert data_schema["properties"]["invoice_total"]["type"] == "string"
    assert preview.recommended_output_schema["properties"]["downloads"]["items"]["properties"]["filename"]["type"] == "string"
    assert "recording_signals" in preview.output_inference_report
    assert any(signal["kind"] == "extract_text" for signal in preview.output_inference_report["recording_signals"])


def test_preview_strips_chinese_login_steps_and_infers_business_params():
    steps = [
        {
            "id": "1",
            "action": "click",
            "description": "点击 登录",
            "target": '{"method":"role","role":"link","name":"登录"}',
            "url": "https://example.com/",
        },
        {
            "id": "2",
            "action": "fill",
            "description": "填写账号",
            "target": '{"method":"label","value":"账号"}',
            "value": "alice@example.com",
            "url": "https://example.com/login",
        },
        {
            "id": "3",
            "action": "fill",
            "description": "填写密码",
            "target": '{"method":"label","value":"密码"}',
            "value": "{{credential}}",
            "sensitive": True,
            "url": "https://example.com/login",
        },
        {
            "id": "4",
            "action": "navigate_click",
            "description": "点击 登录 并跳转页面",
            "target": '{"method":"role","role":"button","name":"登录"}',
            "url": "https://example.com/dashboard",
        },
        {
            "id": "5",
            "action": "fill",
            "description": "填写搜索关键词",
            "target": '{"method":"placeholder","value":"搜索关键词"}',
            "value": "cancer",
            "url": "https://example.com/dashboard",
        },
        {
            "id": "6",
            "action": "click",
            "description": "点击 查询",
            "target": '{"method":"role","role":"button","name":"查询"}',
            "url": "https://example.com/dashboard",
        },
    ]

    preview = RpaMcpConverter().preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="search_skill",
        name="search reports",
        description="Search reports",
        steps=steps,
        params={},
    )

    assert preview.requires_cookies is True
    assert preview.sanitize_report.removed_steps == [0, 1, 2, 3]
    assert [item["description"] for item in preview.sanitize_report.removed_step_details] == [
        "点击 登录",
        "填写账号",
        "填写密码",
        "点击 登录 并跳转页面",
    ]
    assert [step["description"] for step in preview.steps] == ["填写搜索关键词", "点击 查询"]
    assert preview.post_auth_start_url == "https://example.com/dashboard"

    properties = preview.input_schema["properties"]
    assert "cookies" in properties
    assert "keyword" in properties
    assert properties["keyword"]["default"] == "cancer"
    assert "account" not in properties
    assert "password" not in properties


def test_preview_strips_login_params_before_building_input_schema():
    steps = [
        {
            "id": "1",
            "action": "fill",
            "description": "Fill title",
            "target": '{"method":"label","value":"Title"}',
            "value": "Quarterly report",
            "url": "https://example.com/editor",
        },
    ]

    preview = RpaMcpConverter().preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="editor_skill",
        name="create report",
        description="Create report",
        steps=steps,
        params={
            "account": {"original_value": "alice@example.com", "description": "Login account"},
            "password": {"original_value": "{{credential}}", "sensitive": True},
            "title": {"original_value": "Quarterly report", "description": "Report title"},
        },
    )

    properties = preview.input_schema["properties"]
    assert "account" not in properties
    assert "password" not in properties
    assert properties["title"]["default"] == "Quarterly report"


def test_preview_infers_candidate_params_like_skill_configure_page():
    steps = [
        {
            "id": "fill-title",
            "action": "fill",
            "description": "Fill title",
            "target": '{"method":"label","value":"Title"}',
            "value": "Quarterly report",
            "url": "https://example.com/editor",
        },
        {
            "id": "upload-file",
            "action": "set_input_files",
            "description": "Upload attachment",
            "target": '{"method":"css","value":"input[type=file]"}',
            "value": "C:/tmp/report.pdf",
            "url": "https://example.com/editor",
        },
    ]

    preview = RpaMcpConverter().preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="editor_skill",
        name="create report",
        description="Create report",
        steps=steps,
        params={},
    )

    assert set(preview.params.keys()) == {"title"}
    assert preview.params["title"]["source_step_index"] == 0
    assert preview.params["title"]["source_step_id"] == "fill-title"
    assert "file" not in preview.input_schema["properties"]


@pytest.mark.anyio
async def test_preview_with_semantics_uses_ai_recommendation_after_login_strip():
    steps = [
        {
            "action": "fill",
            "description": "填写密码",
            "target": '{"method":"label","value":"密码"}',
            "value": "{{credential}}",
            "url": "https://example.com/login",
        },
        {
            "action": "fill",
            "description": "填写搜索关键词",
            "target": '{"method":"placeholder","value":"搜索关键词"}',
            "value": "cancer",
            "url": "https://example.com/search",
        },
    ]

    preview = await RpaMcpConverter(semantic_inferer=FakeSemanticInferer()).preview_with_semantics(
        user_id="user-1",
        session_id="session-1",
        skill_name="search_skill",
        name="rpa_tool",
        description="",
        steps=steps,
        params={},
    )

    assert preview.tool_name == "search_reports"
    assert preview.name == "Search reports"
    assert preview.description == "Search reports by keyword."
    assert preview.schema_source == "ai_inferred"
    assert "report_keyword" in preview.input_schema["properties"]
    assert "password" not in preview.input_schema["properties"]
