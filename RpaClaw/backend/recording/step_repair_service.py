from __future__ import annotations

import json

from backend.rpa.manager import RPASessionManager, RPAStep


class StepRepairService:
    def select_locator_candidate(self, step: RPAStep, candidate_index: int) -> RPAStep:
        if candidate_index < 0 or candidate_index >= len(step.locator_candidates):
            raise ValueError("Invalid candidate index")

        updated = step.model_copy(deep=True)
        selected_candidate = updated.locator_candidates[candidate_index]
        try:
            locator = RPASessionManager._resolve_candidate_locator(selected_candidate)
        except ValueError as exc:
            raise ValueError("Selected candidate is missing locator payload") from exc

        for index, candidate in enumerate(updated.locator_candidates):
            candidate["selected"] = index == candidate_index

        selected_candidate["locator"] = locator
        updated.target = json.dumps(locator, ensure_ascii=False)
        updated.validation = dict(updated.validation) if isinstance(updated.validation, dict) else {}
        updated.validation["selected_candidate_index"] = candidate_index
        updated.validation["selected_candidate_kind"] = selected_candidate.get("kind", "")
        updated.validation["status"] = selected_candidate.get("status", "warning")
        updated.validation["details"] = selected_candidate.get("reason", "")
        return updated
