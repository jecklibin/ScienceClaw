from backend.rpa.manual_recording_normalizer import (
    build_manual_recording_outcome,
    normalize_manual_candidate,
)


def test_normalize_playwright_locator_first_into_nth_role_locator():
    normalized = normalize_manual_candidate(
        {
            "kind": "role",
            "playwright_locator": 'page.get_by_role("textbox").first',
            "selected": True,
        }
    )
    assert normalized["locator"] == {
        "method": "nth",
        "locator": {"method": "role", "role": "textbox"},
        "index": 0,
    }


def test_build_outcome_accepts_canonicalized_interactive_action():
    outcome = build_manual_recording_outcome(
        action="click",
        description='点击 textbox("Search")',
        target="",
        locator_candidates=[
            {
                "kind": "role",
                "playwright_locator": 'page.get_by_role("textbox").first',
                "selected": True,
            }
        ],
        validation={"status": "ok"},
    )
    assert outcome.accepted_action is not None
    assert outcome.diagnostic is None
    assert outcome.accepted_action.target == {
        "method": "nth",
        "locator": {"method": "role", "role": "textbox"},
        "index": 0,
    }


def test_build_outcome_routes_missing_canonical_target_to_diagnostic():
    outcome = build_manual_recording_outcome(
        action="fill",
        description='输入 "foo" 到 None',
        target="",
        locator_candidates=[{"playwright_locator": 'page.locator(".mystery")'}],
        validation={"status": "ok"},
        value="foo",
    )
    assert outcome.accepted_action is None
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.failure_reason == "canonical_target_missing"


def test_build_outcome_does_not_accept_unselected_canonical_candidate():
    outcome = build_manual_recording_outcome(
        action="click",
        description="点击 None",
        target="",
        locator_candidates=[
            {"playwright_locator": 'page.locator(".mystery")', "selected": True},
            {
                "locator": {"method": "role", "role": "textbox", "name": "Search"},
                "selected": False,
            },
        ],
        validation={"status": "ok"},
    )
    assert outcome.accepted_action is None
    assert outcome.diagnostic is not None


def test_build_outcome_accepts_json_encoded_target():
    outcome = build_manual_recording_outcome(
        action="fill",
        description='输入 "abc" 到 textbox("Search")',
        target='{"method":"role","role":"textbox","name":"Search"}',
        locator_candidates=[],
        validation={"status": "ok"},
        value="abc",
    )
    assert outcome.accepted_action is not None
    assert outcome.accepted_action.target == {
        "method": "role",
        "role": "textbox",
        "name": "Search",
    }


def test_build_outcome_accepts_hover_action_with_canonical_target():
    outcome = build_manual_recording_outcome(
        action="hover",
        description='悬停到 button("Export")',
        target="",
        locator_candidates=[
            {
                "locator": {"method": "role", "role": "button", "name": "Export"},
                "selected": True,
            }
        ],
        validation={"status": "ok"},
    )
    assert outcome.accepted_action is not None
    assert outcome.diagnostic is None
    assert outcome.accepted_action.action_kind.value == "hover"
    assert outcome.accepted_action.target == {
        "method": "role",
        "role": "button",
        "name": "Export",
    }
