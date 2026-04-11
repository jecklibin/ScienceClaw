import importlib
import json
import unittest
from unittest.mock import AsyncMock, patch


ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant")


class RPAStepClassifierTests(unittest.TestCase):
    def test_extract_text_style_candidate_defaults_to_script_step(self):
        classifier_module = importlib.import_module("backend.rpa.step_classifier")

        step_type = classifier_module.classify_candidate_step(
            prompt="提取最新评论内容",
            structured_intent={
                "action": "extract_text",
                "description": "提取最新评论内容",
                "prompt": "提取最新评论内容",
                "result_key": "latest_comment_text",
            },
            code=None,
        )

        self.assertEqual(step_type, "script_step")

    def test_semantic_branching_prompt_becomes_agent_step(self):
        classifier_module = importlib.import_module("backend.rpa.step_classifier")

        step_type = classifier_module.classify_candidate_step(
            prompt="判断评论情绪，如果积极就回复，否则归档",
            structured_intent={
                "action": "extract_text",
                "description": "读取评论文本",
                "prompt": "判断评论情绪，如果积极就回复，否则归档",
                "result_key": "comment_text",
            },
            code=None,
        )

        self.assertEqual(step_type, "agent_step")


class RPAAssistantCandidateGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_candidate_step_returns_classified_candidate_payload(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        llm_response = json.dumps(
            {
                "action": "extract_text",
                "description": "提取最新评论内容",
                "prompt": "提取最新评论内容",
                "result_key": "latest_comment_text",
                "target_hint": {"role": "article", "name": "comment"},
            },
            ensure_ascii=False,
        )

        async def fake_stream(_messages, _model_config=None):
            yield llm_response

        assistant._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            candidate = await assistant.generate_candidate_step(
                session_id="session-1",
                page=object(),
                message="提取最新评论内容",
                steps=[],
            )

        self.assertEqual(candidate["raw_response"], llm_response)
        self.assertEqual(candidate["structured_intent"]["action"], "extract_text")
        self.assertIsNone(candidate["code"])
        self.assertEqual(candidate["snapshot"], snapshot)
        self.assertEqual(candidate["step_type"], "script_step")
        self.assertTrue(isinstance(candidate["messages"], list))


if __name__ == "__main__":
    unittest.main()
