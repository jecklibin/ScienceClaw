from __future__ import annotations

from typing import Any, Dict


class StepValidator:
    """Validate AI-produced steps before they are persisted during recording."""

    def validate_recording_step(
        self,
        candidate: Dict[str, Any],
        execution_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not execution_result.get("success"):
            return {
                "valid": False,
                "code": "execution_failed",
                "message": execution_result.get("error") or "Step execution failed",
            }

        step = execution_result.get("step")
        if not step:
            return {
                "valid": False,
                "code": "missing_step",
                "message": "Execution succeeded without a persistable step",
            }

        step_type = candidate.get("step_type")
        action = step.get("action")
        if step_type == "extract" or action == "extract_text":
            output = execution_result.get("output")
            if not isinstance(output, str) or not output.strip():
                return {
                    "valid": False,
                    "code": "empty_extract_output",
                    "message": "Extract step did not produce output during recording",
                }

        return {
            "valid": True,
            "code": "ok",
            "message": "",
        }
