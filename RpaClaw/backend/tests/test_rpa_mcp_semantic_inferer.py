import pytest

from backend.rpa.mcp_semantic_inferer import RpaMcpSemanticInferer


class FakeModelClient:
    def __init__(self, content: str):
        self.content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        content = self.content

        class Response:
            pass

        response = Response()
        response.content = content
        return response


@pytest.mark.anyio
async def test_semantic_inferer_accepts_valid_recommendation():
    client = FakeModelClient('{"tool":{"tool_name":"search_reports","display_name":"Search reports","description":"Search reports by keyword."},"param_recommendations":{"keyword":{"name":"report_keyword","description":"Keyword used to search reports.","type":"string","required":true,"confidence":0.86}},"warnings":[]}')
    recommendation = await RpaMcpSemanticInferer(model_client=client).infer(
        requested_name="rpa_tool",
        requested_description="",
        steps=[{"action": "fill", "description": "填写搜索关键词", "target": '{"method":"placeholder","value":"搜索关键词"}', "value": "cancer", "url": "https://example.com/search"}],
        removed_step_details=[],
        fallback_params={"keyword": {"original_value": "cancer", "type": "string", "description": "填写搜索关键词"}},
    )
    assert recommendation.source == "ai_inferred"
    assert recommendation.tool_name == "search_reports"
    assert recommendation.params["report_keyword"]["original_value"] == "cancer"
    assert recommendation.params["report_keyword"]["source_param"] == "keyword"
    assert recommendation.input_schema["required"] == ["report_keyword"]


@pytest.mark.anyio
async def test_semantic_inferer_drops_sensitive_recommendations():
    client = FakeModelClient('{"tool":{"tool_name":"login_then_search","display_name":"Login then search","description":"Search."},"param_recommendations":{"keyword":{"name":"password","description":"Password","type":"string","required":true,"confidence":0.9},"unexpected_param":{"name":"extra","description":"Extra","type":"string"}},"warnings":[]}')
    recommendation = await RpaMcpSemanticInferer(model_client=client).infer(
        requested_name="login_then_search",
        requested_description="",
        steps=[],
        removed_step_details=[{"index": 0, "description": "填写密码"}],
        fallback_params={"keyword": {"original_value": "cancer", "type": "string", "description": "Search keyword"}},
    )
    assert recommendation.source == "ai_inferred"
    assert "password" not in recommendation.params
    assert "keyword" in recommendation.params
    assert "extra" not in recommendation.params
    assert "password" not in recommendation.input_schema["properties"]
    assert "keyword" in recommendation.input_schema["properties"]
    assert any("sensitive" in warning.lower() for warning in recommendation.warnings)
    assert any("unknown" in warning.lower() for warning in recommendation.warnings)


@pytest.mark.anyio
async def test_semantic_inferer_falls_back_on_invalid_json():
    client = FakeModelClient("not json")
    recommendation = await RpaMcpSemanticInferer(model_client=client).infer(
        requested_name="Search Reports",
        requested_description="Search reports by keyword",
        steps=[],
        removed_step_details=[],
        fallback_params={"keyword": {"original_value": "cancer", "type": "string", "description": "Search keyword"}},
    )
    assert recommendation.source == "rule_inferred"
    assert recommendation.tool_name == "search_reports"
    assert recommendation.input_schema["properties"]["keyword"]["default"] == "cancer"
    assert recommendation.warnings


@pytest.mark.anyio
async def test_semantic_inferer_uses_active_user_model_when_env_model_is_missing(monkeypatch):
    class FakeRepo:
        async def find_many(self, filter_doc, sort=None, limit=0):
            assert filter_doc == {
                "$or": [{"is_system": True}, {"user_id": "user-1"}],
                "is_active": True,
                "api_key": {"$nin": ["", None]},
            }
            return [
                {
                    "_id": "model-1",
                    "name": "Configured model",
                    "provider": "openai",
                    "base_url": "https://llm.example/v1",
                    "api_key": "sk-user",
                    "model_name": "configured-model",
                    "context_window": 131072,
                    "is_system": False,
                    "user_id": "user-1",
                    "is_active": True,
                    "created_at": 1,
                    "updated_at": 1,
                }
            ]

    class FakeModel:
        async def ainvoke(self, _messages):
            class Response:
                content = '{"tool":{"tool_name":"search_reports","display_name":"Search reports","description":"Search."},"input_schema":{"type":"object","properties":{},"required":[]},"params":{},"warnings":[]}'

            return Response()

    captured = {}

    def fake_get_llm_model(config=None, max_tokens_override=None, streaming=False):
        captured["config"] = config
        captured["max_tokens_override"] = max_tokens_override
        captured["streaming"] = streaming
        return FakeModel()

    monkeypatch.setattr("backend.rpa.mcp_semantic_inferer.get_repository", lambda name: FakeRepo())
    monkeypatch.setattr("backend.rpa.mcp_semantic_inferer.get_llm_model", fake_get_llm_model)
    monkeypatch.setattr("backend.rpa.mcp_semantic_inferer.settings.model_ds_api_key", "")

    recommendation = await RpaMcpSemanticInferer().infer(
        user_id="user-1",
        requested_name="Search Reports",
        requested_description="Search.",
        steps=[],
        removed_step_details=[],
        fallback_params={},
    )

    assert recommendation.source == "ai_inferred"
    assert captured["config"]["api_key"] == "sk-user"
    assert captured["config"]["model_name"] == "configured-model"
    assert captured["max_tokens_override"] == 2000
    assert captured["streaming"] is False
