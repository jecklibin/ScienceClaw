from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from playwright.async_api import Page

from backend.rpa.step_validator import StepValidator


class RecordingOrchestrator:
    """Coordinate candidate generation, execution, validation, and persistence."""

    def __init__(self, assistant: Any, rpa_manager: Any, validator: Optional[StepValidator] = None):
        self.assistant = assistant
        self.rpa_manager = rpa_manager
        self.validator = validator or StepValidator()

    async def run(
        self,
        session_id: str,
        page: Page,
        message: str,
        steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {"event": "message_chunk", "data": {"text": "正在分析当前页面......\n\n"}}

        candidate = await self.assistant.generate_candidate_step(
            session_id=session_id,
            page=page,
            message=message,
            steps=steps,
            model_config=model_config,
            page_provider=page_provider,
        )

        raw_response = candidate.get("raw_response", "")
        if raw_response:
            yield {"event": "message_chunk", "data": {"text": raw_response}}

        yield {"event": "executing", "data": {}}

        current_page = page_provider() if page_provider else page
        if current_page is None:
            yield {"event": "error", "data": {"message": "No active page available"}}
            yield {"event": "done", "data": {}}
            return

        result, code, resolution = await self.assistant._execute_single_response(
            current_page,
            candidate["snapshot"],
            raw_response,
        )

        if resolution:
            yield {"event": "resolution", "data": {"intent": resolution}}

        history = self.assistant._get_history(session_id)
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": raw_response})
        self.assistant._trim_history(session_id)

        validation = self.validator.validate_recording_step(candidate, result)
        step_data = result.get("step")
        overall_success = bool(result.get("success") and validation["valid"])
        result_error = result.get("error")

        if validation["valid"]:
            if overall_success and step_data:
                await self.rpa_manager.add_step(session_id, step_data)
        else:
            yield {"event": "validation_failed", "data": validation}
            result_error = result_error or validation["message"]
            step_data = None

        yield {
            "event": "result",
            "data": {
                "success": overall_success,
                "error": result_error,
                "step": step_data if overall_success else None,
                "output": result.get("output"),
                "validation": validation,
                "code": code,
            },
        }
        yield {"event": "done", "data": {}}
