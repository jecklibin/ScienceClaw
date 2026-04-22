import json

from backend.recording.step_repair_service import StepRepairService
from backend.rpa.manager import RPAStep


def test_select_locator_candidate_updates_validation_status():
    step = RPAStep(
        id="step-1",
        action="click",
        locator_candidates=[
            {
                "kind": "role",
                "status": "broken",
                "selected": True,
                "locator": {"method": "role", "role": "button", "name": "Save"},
                "reason": "stale role locator",
            },
            {
                "kind": "css",
                "status": "ok",
                "selected": False,
                "locator": {"method": "css", "value": "button.save"},
                "reason": "strict css match",
            },
        ],
        validation={"status": "broken", "details": "old candidate failed"},
    )
    service = StepRepairService()

    updated = service.select_locator_candidate(step, candidate_index=1)

    assert updated.validation["status"] == "ok"
    assert updated.validation["details"] == "strict css match"
    assert json.loads(updated.target) == {"method": "css", "value": "button.save"}
    assert updated.locator_candidates[0]["selected"] is False
    assert updated.locator_candidates[1]["selected"] is True


def test_select_locator_candidate_resolves_selector_payloads():
    step = RPAStep(
        id="step-1",
        action="click",
        locator_candidates=[
            {
                "kind": "role",
                "selected": True,
                "locator": {"method": "role", "role": "button", "name": "Save"},
            },
            {
                "kind": "css",
                "selected": False,
                "selector": "button.save",
                "status": "ok",
            },
        ],
        validation={"status": "fallback"},
    )
    service = StepRepairService()

    updated = service.select_locator_candidate(step, candidate_index=1)

    assert json.loads(updated.target) == {"method": "css", "value": "button.save"}
    assert updated.locator_candidates[1]["locator"] == {"method": "css", "value": "button.save"}
