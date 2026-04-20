from __future__ import annotations

import json

from backend.rpa.manager import RPAStep


class StepRepairService:
    def select_locator_candidate(self, step: RPAStep, candidate_index: int) -> RPAStep:
        if candidate_index < 0 or candidate_index >= len(step.locator_candidates):
            raise ValueError("Invalid candidate index")

        updated = step.model_copy(deep=True)
        selected_candidate = updated.locator_candidates[candidate_index]
        locator = selected_candidate.get("locator")
        if locator is None:
            raise ValueError("Selected candidate is missing locator payload")

        for index, candidate in enumerate(updated.locator_candidates):
            candidate["selected"] = index == candidate_index

        updated.target = json.dumps(locator, ensure_ascii=False)
        updated.validation = dict(updated.validation) if isinstance(updated.validation, dict) else {}
        updated.validation["selected_candidate_index"] = candidate_index
        updated.validation["selected_candidate_kind"] = selected_candidate.get("kind", "")
        updated.validation["status"] = selected_candidate.get("status", "warning")
        updated.validation["details"] = selected_candidate.get("reason", "")
        return updated
