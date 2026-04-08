import unittest

from backend.rpa.action_models import (
    ActionSignal,
    LocatorCandidate,
    LocatorValidation,
    RecordedActionV2,
)


class RecordedActionModelTests(unittest.TestCase):
    def test_recorded_action_preserves_frame_path_and_signals(self):
        action = RecordedActionV2(
            id="action-1",
            session_id="session-1",
            tab_id="tab-1",
            page_alias="page",
            frame_path=["iframe[name='workspace']", "iframe[title='editor']"],
            action="click",
            selector="getByRole('button', { name: 'Save' })",
            selector_source="role",
            signals=ActionSignal(popup={"target_tab_id": "tab-2"}),
            locator_candidates=[
                LocatorCandidate(
                    kind="role",
                    selector="internal:role=button[name=\"Save\"]",
                    playwright_locator="page.get_by_role(\"button\", name=\"Save\", exact=True)",
                    score=10,
                    strict_match_count=1,
                    visible_match_count=1,
                    actionability={"click": True},
                    selected=True,
                    reason="best strict role locator",
                )
            ],
            validation=LocatorValidation(status="ok", details="strictly resolved"),
            status="ok",
        )

        self.assertEqual(action.frame_path[0], "iframe[name='workspace']")
        self.assertEqual(action.signals.popup["target_tab_id"], "tab-2")
        self.assertEqual(action.locator_candidates[0].kind, "role")
        self.assertEqual(action.validation.status, "ok")


if __name__ == "__main__":
    unittest.main()
