import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.rpa.recording_orchestrator import RecordingOrchestrator
from backend.rpa.step_validator import StepValidator


class RecordingOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_failure_does_not_persist_extract_step(self):
        history = []
        assistant = SimpleNamespace(
            generate_candidate_step=AsyncMock(),
            _execute_single_response=AsyncMock(),
            _get_history=lambda _session_id: history,
            _trim_history=lambda _session_id: None,
        )
        assistant.generate_candidate_step.return_value = {
            "raw_response": '{"action":"extract_text"}',
            "structured_intent": {"action": "extract_text"},
            "code": None,
            "snapshot": {"frames": []},
            "messages": [{"role": "user", "content": "extract latest issue title"}],
            "step_type": "extract",
        }
        assistant._execute_single_response.return_value = (
            {
                "success": True,
                "output": "",
                "step": {
                    "action": "extract_text",
                    "description": "extract latest issue title",
                    "result_key": "latest_issue_title",
                },
            },
            None,
            {"resolved": {}},
        )
        manager = AsyncMock()
        validator = StepValidator()
        orchestrator = RecordingOrchestrator(
            assistant=assistant,
            rpa_manager=manager,
            validator=validator,
        )

        events = []
        async for event in orchestrator.run(
            session_id="session-1",
            page=object(),
            message="extract latest issue title",
            steps=[],
            model_config=None,
            page_provider=None,
        ):
            events.append(event)

        manager.add_step.assert_not_awaited()
        self.assertEqual(events[-2]["event"], "result")
        self.assertFalse(events[-2]["data"]["success"])
        self.assertEqual(events[-3]["event"], "validation_failed")

    async def test_successful_candidate_is_saved_after_validation(self):
        history = []
        assistant = SimpleNamespace(
            generate_candidate_step=AsyncMock(),
            _execute_single_response=AsyncMock(),
            _get_history=lambda _session_id: history,
            _trim_history=lambda _session_id: None,
        )
        assistant.generate_candidate_step.return_value = {
            "raw_response": '{"action":"click"}',
            "structured_intent": {"action": "click"},
            "code": None,
            "snapshot": {"frames": []},
            "messages": [{"role": "user", "content": "click submit"}],
            "step_type": "action",
        }
        assistant._execute_single_response.return_value = (
            {
                "success": True,
                "output": "ok",
                "step": {
                    "action": "click",
                    "description": "click submit",
                    "target": '{"method":"role","role":"button","name":"Submit"}',
                },
            },
            None,
            {"resolved": {}},
        )
        manager = AsyncMock()
        manager.add_step.return_value = {"id": "saved-step"}
        orchestrator = RecordingOrchestrator(
            assistant=assistant,
            rpa_manager=manager,
            validator=StepValidator(),
        )

        events = []
        async for event in orchestrator.run(
            session_id="session-1",
            page=object(),
            message="click submit",
            steps=[],
            model_config=None,
            page_provider=None,
        ):
            events.append(event)

        manager.add_step.assert_awaited_once()
        self.assertEqual(events[-2]["event"], "result")
        self.assertTrue(events[-2]["data"]["success"])
        self.assertEqual(events[-1]["event"], "done")


if __name__ == "__main__":
    unittest.main()
