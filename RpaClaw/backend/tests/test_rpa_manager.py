import importlib.util
import importlib
import sys
import unittest
import json
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

MANAGER_MODULE = importlib.import_module("backend.rpa.manager")


class _FakeContext:
    def __init__(self):
        self.handlers = {}
        self.exposed_bindings = []
        self.init_scripts = []

    def on(self, event_name, handler):
        self.handlers[event_name] = handler

    async def expose_binding(self, name, callback, handle=None):
        self.exposed_bindings.append((name, callback, handle))

    async def add_init_script(self, script=None, path=None):
        self.init_scripts.append({"script": script, "path": path})


class _FakePage:
    def __init__(self, url: str, title: str, context=None):
        self.url = url
        self._title = title
        self.context = context or _FakeContext()
        self.main_frame = SimpleNamespace(url=url)
        self.handlers = {}
        self.bring_to_front_calls = 0
        self.goto_calls = []
        self.wait_for_load_state_calls = []
        self.closed = False
        self.expose_function_calls = []
        self.evaluate_calls = []

    async def title(self):
        return self._title

    async def expose_function(self, _name, _fn):
        self.expose_function_calls.append((_name, _fn))
        return None

    async def evaluate(self, _script):
        self.evaluate_calls.append(_script)
        return None

    async def goto(self, url):
        self.goto_calls.append(url)
        self.url = url
        self.main_frame.url = url

    async def wait_for_load_state(self, state):
        self.wait_for_load_state_calls.append(state)

    async def bring_to_front(self):
        self.bring_to_front_calls += 1

    async def close(self):
        self.closed = True

    def on(self, event_name, handler):
        self.handlers[event_name] = handler

    def set_default_timeout(self, _timeout):
        return None

    def set_default_navigation_timeout(self, _timeout):
        return None


class _FakeFrameElement:
    def __init__(self, attrs=None, tag_name="iframe", nth_of_type=1):
        self.attrs = attrs or {}
        self.tag_name = tag_name
        self.nth_of_type = nth_of_type

    async def get_attribute(self, name):
        return self.attrs.get(name)

    async def evaluate(self, expression):
        if "tagName" in expression:
            return self.tag_name.upper()
        if "nth-of-type" in expression:
            return f"{self.tag_name}:nth-of-type({self.nth_of_type})"
        raise AssertionError(f"Unexpected evaluate expression: {expression}")


class _FakeFrame:
    def __init__(self, page, parent_frame=None, attrs=None, tag_name="iframe", nth_of_type=1):
        self.page = page
        self.parent_frame = parent_frame
        self._element = _FakeFrameElement(attrs=attrs, tag_name=tag_name, nth_of_type=nth_of_type)

    async def frame_element(self):
        return self._element


class RPASessionManagerTabTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = MANAGER_MODULE.RPASessionManager()
        self.session = MANAGER_MODULE.RPASession(
            id="session-1",
            user_id="user-1",
            sandbox_session_id="sandbox-1",
        )
        self.manager.sessions[self.session.id] = self.session

    async def test_register_page_tracks_first_tab_as_active(self):
        page = _FakePage("https://example.com", "Example")

        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)
        tabs = self.manager.list_tabs(self.session.id)

        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0]["tab_id"], tab_id)
        self.assertTrue(tabs[0]["active"])
        self.assertEqual(tabs[0]["title"], "Example")
        self.assertEqual(tabs[0]["url"], "https://example.com")
        self.assertIs(self.manager.get_active_page(self.session.id), page)
        self.assertEqual(page.bring_to_front_calls, 1)

    async def test_activate_tab_switches_active_page(self):
        first_page = _FakePage("https://example.com", "Example")
        second_page = _FakePage("https://example.org", "Example Org")

        first_tab_id = await self.manager.register_page(self.session.id, first_page, make_active=True)
        second_tab_id = await self.manager.register_page(self.session.id, second_page, make_active=False)

        await self.manager.activate_tab(self.session.id, second_tab_id, source="user")
        tabs = self.manager.list_tabs(self.session.id)

        self.assertEqual(first_tab_id, tabs[0]["tab_id"])
        self.assertFalse(next(tab for tab in tabs if tab["tab_id"] == first_tab_id)["active"])
        self.assertTrue(next(tab for tab in tabs if tab["tab_id"] == second_tab_id)["active"])
        self.assertIs(self.manager.get_active_page(self.session.id), second_page)
        self.assertEqual(second_page.bring_to_front_calls, 1)
        self.assertEqual(self.session.steps[-1].action, "switch_tab")
        self.assertEqual(self.session.steps[-1].source_tab_id, first_tab_id)
        self.assertEqual(self.session.steps[-1].target_tab_id, second_tab_id)

    async def test_close_active_tab_falls_back_to_opener_tab(self):
        first_page = _FakePage("https://example.com", "Example")
        popup_page = _FakePage("https://popup.example.com", "Popup")

        first_tab_id = await self.manager.register_page(self.session.id, first_page, make_active=True)
        popup_tab_id = await self.manager.register_page(
            self.session.id,
            popup_page,
            opener_tab_id=first_tab_id,
            make_active=True,
        )

        await self.manager.close_tab(self.session.id, popup_tab_id)
        tabs = self.manager.list_tabs(self.session.id)

        self.assertIs(self.manager.get_active_page(self.session.id), first_page)
        self.assertTrue(next(tab for tab in tabs if tab["tab_id"] == first_tab_id)["active"])
        self.assertEqual(
            next(tab for tab in tabs if tab["tab_id"] == popup_tab_id)["status"],
            "closed",
        )
        self.assertTrue(popup_page.closed)
        self.assertEqual(self.session.steps[-2].action, "close_tab")
        self.assertEqual(self.session.steps[-2].target_tab_id, first_tab_id)
        self.assertEqual(self.session.steps[-1].action, "switch_tab")
        self.assertEqual(self.session.steps[-1].target_tab_id, first_tab_id)

    async def test_event_from_inactive_tab_promotes_it_to_active_page(self):
        first_page = _FakePage("https://example.com", "Example")
        second_page = _FakePage("https://example.org", "Example Org")

        await self.manager.register_page(self.session.id, first_page, make_active=True)
        second_tab_id = await self.manager.register_page(self.session.id, second_page, make_active=False)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": second_tab_id,
                "tag": "BUTTON",
                "timestamp": 1234567890,
            },
        )

        self.assertIs(self.manager.get_active_page(self.session.id), second_page)
        self.assertEqual(self.session.active_tab_id, second_tab_id)
        self.assertEqual(self.session.steps[-1].tab_id, second_tab_id)

    async def test_handle_event_persists_frame_path(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": tab_id,
                "tag": "BUTTON",
                "timestamp": 1234567890,
                "frame_path": ["iframe[name='workspace']", "iframe[title='editor']"],
                "locator": {"method": "role", "role": "button", "name": "Save"},
            },
        )

        self.assertEqual(
            self.session.steps[-1].frame_path,
            ["iframe[name='workspace']", "iframe[title='editor']"],
        )

    async def test_handle_event_persists_locator_candidates_and_validation(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": tab_id,
                "tag": "BUTTON",
                "timestamp": 1234567890,
                "locator": {"method": "role", "role": "button", "name": "Save"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "score": 100,
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                        "reason": "strict unique role match",
                    },
                    {
                        "kind": "css",
                        "score": 520,
                        "selected": False,
                        "locator": {"method": "css", "value": "button.save"},
                        "reason": "class fallback",
                    },
                ],
                "validation": {"status": "ok", "details": "strict match count = 1"},
            },
        )

        self.assertEqual(self.session.steps[-1].locator_candidates[0]["kind"], "role")
        self.assertTrue(self.session.steps[-1].locator_candidates[0]["selected"])
        self.assertEqual(self.session.steps[-1].validation["status"], "ok")

    async def test_handle_event_prefers_best_scored_strict_candidate_over_earlier_nth(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": tab_id,
                "tag": "BUTTON",
                "timestamp": 1234567890,
                "locator": {"method": "role", "role": "button", "name": "Save"},
                "locator_candidates": [
                    {
                        "kind": "nth",
                        "score": 10100,
                        "strict_match_count": 1,
                        "visible_match_count": 1,
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                        "nth": 1,
                        "reason": "strict nth match for current target",
                    },
                    {
                        "kind": "css",
                        "score": 520,
                        "strict_match_count": 1,
                        "visible_match_count": 1,
                        "selected": False,
                        "locator": {"method": "css", "value": "button.save"},
                        "reason": "strict unique css match",
                    },
                ],
                "validation": {"status": "fallback", "details": "generated locator strict matches = 2"},
            },
        )

        step = self.session.steps[-1]
        self.assertEqual(json.loads(step.target), {"method": "css", "value": "button.save"})
        self.assertFalse(step.locator_candidates[0]["selected"])
        self.assertTrue(step.locator_candidates[1]["selected"])
        self.assertEqual(step.validation["status"], "ok")
        self.assertEqual(step.validation["details"], "strict unique css match")

    async def test_handle_event_normalizes_stale_selected_nth_even_when_validation_ok(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": tab_id,
                "tag": "BUTTON",
                "timestamp": 1234567891,
                "locator": {
                    "method": "nth",
                    "locator": {"method": "role", "role": "button", "name": "Save"},
                    "index": 1,
                },
                "locator_candidates": [
                    {
                        "kind": "nth",
                        "score": 10100,
                        "strict_match_count": 1,
                        "visible_match_count": 1,
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                        "nth": 1,
                        "reason": "strict nth match for current target",
                    },
                    {
                        "kind": "css",
                        "score": 520,
                        "strict_match_count": 1,
                        "visible_match_count": 1,
                        "selected": False,
                        "locator": {"method": "css", "value": "button.save"},
                        "reason": "strict unique css match",
                    },
                ],
                "validation": {"status": "ok", "details": "stale selection from older client"},
            },
        )

        step = self.session.steps[-1]
        self.assertEqual(json.loads(step.target), {"method": "css", "value": "button.save"})
        self.assertFalse(step.locator_candidates[0]["selected"])
        self.assertTrue(step.locator_candidates[1]["selected"])
        self.assertEqual(step.validation["status"], "ok")
        self.assertEqual(step.validation["details"], "strict unique css match")

    async def test_select_step_locator_candidate_promotes_target_and_selection(self):
        await self.manager.add_step(
            self.session.id,
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Save"}),
                "frame_path": [],
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                    },
                    {
                        "kind": "css",
                        "selected": False,
                        "locator": {"method": "css", "value": "button.save"},
                    },
                ],
                "validation": {"status": "ok"},
                "value": "",
                "label": "",
                "tag": "BUTTON",
                "url": "https://example.com",
                "description": "Click Save",
                "sensitive": False,
                "tab_id": "tab-1",
            },
        )

        updated = await self.manager.select_step_locator_candidate(self.session.id, 0, 1)

        self.assertEqual(json.loads(updated.target), {"method": "css", "value": "button.save"})
        self.assertFalse(updated.locator_candidates[0]["selected"])
        self.assertTrue(updated.locator_candidates[1]["selected"])

    async def test_select_step_locator_candidate_supports_nth_locator_payload(self):
        await self.manager.add_step(
            self.session.id,
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Save"}),
                "frame_path": [],
                "locator_candidates": [
                    {
                        "kind": "role",
                        "score": 100,
                        "strict_match_count": 2,
                        "visible_match_count": 2,
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                        "reason": "strict matches = 2",
                    },
                    {
                        "kind": "role",
                        "score": 10100,
                        "strict_match_count": 1,
                        "visible_match_count": 1,
                        "selected": False,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                        "nth": 1,
                        "reason": "strict nth match for current target",
                    },
                ],
                "validation": {"status": "fallback"},
                "value": "",
                "label": "",
                "tag": "BUTTON",
                "url": "https://example.com",
                "description": "Click Save",
                "sensitive": False,
                "tab_id": "tab-1",
            },
        )

        updated = await self.manager.select_step_locator_candidate(self.session.id, 0, 1)

        self.assertEqual(
            json.loads(updated.target),
            {
                "method": "nth",
                "locator": {"method": "role", "role": "button", "name": "Save"},
                "index": 1,
            },
        )
        self.assertFalse(updated.locator_candidates[0]["selected"])
        self.assertTrue(updated.locator_candidates[1]["selected"])

    async def test_select_step_locator_candidate_supports_legacy_selector_payload(self):
        await self.manager.add_step(
            self.session.id,
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Save"}),
                "frame_path": [],
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                    },
                    {
                        "kind": "css",
                        "selected": False,
                        "selector": "button.save",
                        "playwright_locator": 'page.locator("button.save")',
                    },
                ],
                "validation": {"status": "ok"},
                "value": "",
                "label": "",
                "tag": "BUTTON",
                "url": "https://example.com",
                "description": "Click Save",
                "sensitive": False,
                "tab_id": "tab-1",
            },
        )

        updated = await self.manager.select_step_locator_candidate(self.session.id, 0, 1)

        self.assertEqual(json.loads(updated.target), {"method": "css", "value": "button.save"})
        self.assertFalse(updated.locator_candidates[0]["selected"])
        self.assertTrue(updated.locator_candidates[1]["selected"])

    async def test_select_step_locator_candidate_validation_failure_does_not_mutate_selected_flags(self):
        await self.manager.add_step(
            self.session.id,
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Save"}),
                "frame_path": [],
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                    },
                    {
                        "kind": "legacy",
                        "selected": False,
                        "selector": "",
                        "playwright_locator": "",
                    },
                ],
                "validation": {"status": "ok"},
                "value": "",
                "label": "",
                "tag": "BUTTON",
                "url": "https://example.com",
                "description": "Click Save",
                "sensitive": False,
                "tab_id": "tab-1",
            },
        )

        step = self.session.steps[0]
        original_target = step.target
        before_selected = [candidate.get("selected", False) for candidate in step.locator_candidates]

        with self.assertRaises(ValueError):
            await self.manager.select_step_locator_candidate(self.session.id, 0, 1)

        after_selected = [candidate.get("selected", False) for candidate in step.locator_candidates]
        self.assertEqual(after_selected, before_selected)
        self.assertEqual(step.target, original_target)

    def test_capture_js_includes_frame_path_collection(self):
        self.assertIn("frame_path", MANAGER_MODULE.CAPTURE_JS)
        self.assertIn("window.frameElement", MANAGER_MODULE.CAPTURE_JS)
        self.assertIn("evt.sequence", MANAGER_MODULE.CAPTURE_JS)
        self.assertIn("_eventSequence", MANAGER_MODULE.CAPTURE_JS)

    def test_capture_js_records_fill_without_debounce_timer(self):
        self.assertNotIn("setTimeout(function()", MANAGER_MODULE.CAPTURE_JS)
        self.assertNotIn("}, 1500);", MANAGER_MODULE.CAPTURE_JS)

    def test_capture_js_keydown_resolves_from_remembered_active_target(self):
        js = MANAGER_MODULE.CAPTURE_JS
        keydown_block = js.split("document.addEventListener('keydown'", 1)[1]
        self.assertIn("var el = resolveActiveTarget();", keydown_block)
        self.assertNotIn("var el = resolveActiveTarget(e.target);", keydown_block)
        self.assertNotIn("var el = e.target;", keydown_block)

    def test_capture_js_input_updates_remembered_active_target(self):
        js = MANAGER_MODULE.CAPTURE_JS
        input_block = js.split("document.addEventListener('input'", 1)[1].split(
            "document.addEventListener('change'", 1
        )[0]
        self.assertIn("var el = rememberActiveTarget(e.target);", input_block)
        self.assertNotIn("resolveActiveTarget(e.target)", input_block)

    def test_capture_js_builds_nth_locator_payload_for_ambiguous_candidates(self):
        js = MANAGER_MODULE.CAPTURE_JS
        self.assertIn("function buildNthLocator(c, nthIndex)", js)
        self.assertIn("method:'nth'", js)
        self.assertIn("index:nthIndex", js)

    def test_capture_js_fallback_candidate_uses_real_match_count(self):
        js = MANAGER_MODULE.CAPTURE_JS
        self.assertIn("var primaryMatchCount = countLocatorMatches(primary);", js)
        fallback_block = js.split("if (!selectedPayload)", 1)[1].split("candidatePayloads.push", 1)[0]
        self.assertIn("strict_match_count: primaryMatchCount", fallback_block)
        self.assertNotIn("strict_match_count: 1", fallback_block)

    def test_capture_js_click_listener_does_not_dedupe_same_locator_clicks(self):
        js = MANAGER_MODULE.CAPTURE_JS
        click_block = js.split("document.addEventListener('click'", 1)[1].split(
            "document.addEventListener('focusin'", 1
        )[0]
        self.assertNotIn("_lastClick", js)
        self.assertNotIn("now-_lastClick.time<1000", click_block)
        self.assertNotIn("Deduplicate rapid clicks on the same element", click_block)

    def test_capture_js_prefers_strict_candidate_primary_when_generated_locator_is_ambiguous(self):
        js = MANAGER_MODULE.CAPTURE_JS
        build_locator_block = js.split("function buildLocatorBundle(el)", 1)[1].split(
            "function buildElementSnapshot", 1
        )[0]
        self.assertIn("if (primaryMatchCount !== 1)", build_locator_block)
        self.assertIn("candidate.strict_match_count !== 1 || !candidate.locator", build_locator_block)
        self.assertIn("if (candidateScore < strictScore)", build_locator_block)
        self.assertIn("primary = strictCandidate.locator;", build_locator_block)

    async def test_register_page_bootstraps_context_recorder_once(self):
        context = _FakeContext()
        first_page = _FakePage("https://example.com", "Example", context=context)
        second_page = _FakePage("https://example.org", "Example Org", context=context)

        await self.manager.register_page(self.session.id, first_page, make_active=True)
        await self.manager.register_page(self.session.id, second_page, make_active=False)

        self.assertEqual(len(context.exposed_bindings), 1)
        self.assertEqual(context.exposed_bindings[0][0], "__rpa_emit")
        self.assertEqual(len(context.init_scripts), 1)
        self.assertEqual(first_page.expose_function_calls, [])
        self.assertEqual(first_page.evaluate_calls, [])
        self.assertEqual(second_page.expose_function_calls, [])
        self.assertEqual(second_page.evaluate_calls, [])

    async def test_context_binding_callback_derives_frame_path_from_source_frame(self):
        context = _FakeContext()
        page = _FakePage("https://example.com", "Example", context=context)
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        _, binding_callback, _ = context.exposed_bindings[0]
        outer_frame = _FakeFrame(page, attrs={"name": "workspace"})
        inner_frame = _FakeFrame(page, parent_frame=outer_frame, attrs={"title": "editor"})

        await binding_callback(
            SimpleNamespace(page=page, frame=inner_frame),
            json.dumps(
                {
                    "action": "click",
                    "tag": "BUTTON",
                    "timestamp": 1234567890,
                    "locator": {"method": "role", "role": "button", "name": "Save"},
                }
            ),
        )

        self.assertEqual(self.session.steps[-1].tab_id, tab_id)
        self.assertEqual(
            self.session.steps[-1].frame_path,
            ["iframe[name='workspace']", "iframe[title='editor']"],
        )

    async def test_context_binding_callback_falls_back_to_active_tab_when_source_page_missing(self):
        context = _FakeContext()
        source_page = _FakePage("https://example.com", "Example", context=context)
        source_tab_id = await self.manager.register_page(self.session.id, source_page, make_active=True)
        popup_page = _FakePage("https://example.com/new", "Popup", context=context)
        popup_tab_id = await self.manager.register_context_page(self.session.id, popup_page, make_active=True)

        _, binding_callback, _ = context.exposed_bindings[0]
        await binding_callback(
            SimpleNamespace(page=None, frame=None),
            json.dumps(
                {
                    "action": "click",
                    "tag": "INPUT",
                    "timestamp": 1234567891,
                    "locator": {"method": "css", "value": "#s"},
                }
            ),
        )

        self.assertNotEqual(source_tab_id, popup_tab_id)
        self.assertEqual(self.session.active_tab_id, popup_tab_id)
        self.assertEqual(self.session.steps[-1].tab_id, popup_tab_id)

    async def test_navigation_after_click_upgrades_step_to_navigate_click(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)
        await self.manager.add_step(
            self.session.id,
            {
                "action": "click",
                "target": "",
                "value": "",
                "label": "",
                "tag": "A",
                "url": "https://example.com",
                "description": "点击链接",
                "sensitive": False,
                "tab_id": tab_id,
            },
        )

        navigate_ts = int(datetime.now().timestamp() * 1000)
        await self.manager._handle_event(
            self.session.id,
            {
                "action": "navigate",
                "url": "https://example.com/next",
                "timestamp": navigate_ts,
                "tab_id": tab_id,
            },
        )

        self.assertEqual(len(self.session.steps), 1)
        self.assertEqual(self.session.steps[-1].action, "navigate_click")
        self.assertEqual(self.session.steps[-1].url, "https://example.com/next")

    def test_make_description_formats_nth_locator(self):
        description = self.manager._make_description(
            {
                "action": "click",
                "locator": {
                    "method": "nth",
                    "locator": {"method": "role", "role": "button", "name": "Save"},
                    "index": 1,
                },
            }
        )

        self.assertIn("nth=1", description)
        self.assertIn('button("Save")', description)

    async def test_handle_event_orders_steps_by_sequence_when_events_arrive_out_of_order(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": tab_id,
                "tag": "BUTTON",
                "timestamp": 1234567891,
                "sequence": 20,
                "locator": {"method": "role", "role": "button", "name": "Second"},
            },
        )
        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": tab_id,
                "tag": "BUTTON",
                "timestamp": 1234567890,
                "sequence": 10,
                "locator": {"method": "role", "role": "button", "name": "First"},
            },
        )

        self.assertEqual([step.sequence for step in self.session.steps], [10, 20])
        self.assertIn("First", self.session.steps[0].description)
        self.assertIn("Second", self.session.steps[1].description)

    async def test_navigation_after_press_upgrades_step_to_navigate_press(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)
        await self.manager.add_step(
            self.session.id,
            {
                "action": "press",
                "target": "",
                "value": "Enter",
                "label": "",
                "tag": "INPUT",
                "url": "https://example.com",
                "description": "鎸変笅 Enter 鍦?input",
                "sensitive": False,
                "tab_id": tab_id,
            },
        )

        navigate_ts = int(datetime.now().timestamp() * 1000)
        await self.manager._handle_event(
            self.session.id,
            {
                "action": "navigate",
                "url": "https://example.com/next",
                "timestamp": navigate_ts,
                "tab_id": tab_id,
            },
        )

        self.assertEqual(len(self.session.steps), 1)
        self.assertEqual(self.session.steps[-1].action, "navigate_press")
        self.assertEqual(self.session.steps[-1].url, "https://example.com/next")

    async def test_navigation_upgrade_uses_sequence_predecessor_not_last_arrival(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": tab_id,
                "tag": "BUTTON",
                "timestamp": 1003,
                "sequence": 30,
                "locator": {"method": "role", "role": "button", "name": "Later Click"},
            },
        )
        await self.manager._handle_event(
            self.session.id,
            {
                "action": "press",
                "tab_id": tab_id,
                "tag": "INPUT",
                "timestamp": 1000,
                "sequence": 10,
                "value": "Enter",
                "locator": {"method": "role", "role": "textbox", "name": "Search"},
            },
        )

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "navigate",
                "url": "https://example.com/next",
                "timestamp": 1001,
                "sequence": 11,
                "tab_id": tab_id,
            },
        )

        self.assertEqual(len(self.session.steps), 2)
        self.assertEqual([step.sequence for step in self.session.steps], [10, 30])
        self.assertEqual(self.session.steps[0].action, "navigate_press")
        self.assertEqual(self.session.steps[1].action, "click")

    async def test_navigation_upgrade_uses_timestamp_fallback_when_navigate_has_no_sequence(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "click",
                "tab_id": tab_id,
                "tag": "BUTTON",
                "timestamp": 1003,
                "sequence": 30,
                "locator": {"method": "role", "role": "button", "name": "Later Click"},
            },
        )
        await self.manager._handle_event(
            self.session.id,
            {
                "action": "press",
                "tab_id": tab_id,
                "tag": "INPUT",
                "timestamp": 1000,
                "sequence": 10,
                "value": "Enter",
                "locator": {"method": "role", "role": "textbox", "name": "Search"},
            },
        )

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "navigate",
                "url": "https://example.com/next",
                "timestamp": 1001,
                "tab_id": tab_id,
            },
        )

        self.assertEqual(len(self.session.steps), 2)
        self.assertEqual(self.session.steps[0].action, "navigate_press")
        self.assertEqual(self.session.steps[0].event_timestamp_ms, 1000)
        self.assertEqual(self.session.steps[1].action, "click")

    async def test_sequence_order_keeps_fill_before_press_for_same_target(self):
        page = _FakePage("https://example.com", "Example")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)
        locator = {"method": "role", "role": "textbox", "name": "Search"}

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "press",
                "tab_id": tab_id,
                "tag": "INPUT",
                "timestamp": 2002,
                "sequence": 30,
                "value": "Enter",
                "locator": locator,
            },
        )
        await self.manager._handle_event(
            self.session.id,
            {
                "action": "fill",
                "tab_id": tab_id,
                "tag": "INPUT",
                "timestamp": 2001,
                "sequence": 20,
                "value": "cat",
                "locator": locator,
            },
        )

        self.assertEqual([step.action for step in self.session.steps], ["fill", "press"])
        self.assertEqual(self.session.steps[0].target, self.session.steps[1].target)

    async def test_register_context_page_upgrades_recent_click_to_open_tab_click(self):
        source_page = _FakePage("https://example.com", "Example")
        target_page = _FakePage("https://example.com/new", "Popup", context=source_page.context)
        source_tab_id = await self.manager.register_page(self.session.id, source_page, make_active=True)
        await self.manager.add_step(
            self.session.id,
            {
                "action": "click",
                "target": "",
                "value": "",
                "label": "",
                "tag": "A",
                "url": "https://example.com",
                "description": "点击链接",
                "sensitive": False,
                "tab_id": source_tab_id,
            },
        )

        target_tab_id = await self.manager.register_context_page(self.session.id, target_page, make_active=True)

        self.assertEqual(len(self.session.steps), 1)
        self.assertEqual(self.session.steps[-1].action, "open_tab_click")
        self.assertEqual(self.session.steps[-1].source_tab_id, source_tab_id)
        self.assertEqual(self.session.steps[-1].target_tab_id, target_tab_id)

    async def test_navigation_after_open_tab_click_is_skipped(self):
        source_page = _FakePage("https://example.com", "Example")
        target_page = _FakePage("https://example.com/new", "Popup", context=source_page.context)
        source_tab_id = await self.manager.register_page(self.session.id, source_page, make_active=True)
        await self.manager.add_step(
            self.session.id,
            {
                "action": "click",
                "target": "",
                "value": "",
                "label": "",
                "tag": "A",
                "url": "https://example.com",
                "description": "点击链接",
                "sensitive": False,
                "tab_id": source_tab_id,
            },
        )
        target_tab_id = await self.manager.register_context_page(self.session.id, target_page, make_active=True)

        await self.manager._handle_event(
            self.session.id,
            {
                "action": "navigate",
                "url": "https://example.com/new",
                "timestamp": int(datetime.now().timestamp() * 1000),
                "tab_id": target_tab_id,
            },
        )

        self.assertEqual(len(self.session.steps), 1)
        self.assertEqual(self.session.steps[-1].action, "open_tab_click")

    async def test_navigate_active_tab_normalizes_url_and_updates_metadata(self):
        page = _FakePage("about:blank", "Blank")
        tab_id = await self.manager.register_page(self.session.id, page, make_active=True)

        result = await self.manager.navigate_active_tab(self.session.id, "example.com")
        tabs = self.manager.list_tabs(self.session.id)

        self.assertEqual(result["tab_id"], tab_id)
        self.assertEqual(result["url"], "https://example.com")
        self.assertEqual(page.goto_calls, ["https://example.com"])
        self.assertEqual(page.wait_for_load_state_calls, ["domcontentloaded"])
        self.assertEqual(next(tab for tab in tabs if tab["tab_id"] == tab_id)["url"], "https://example.com")


if __name__ == "__main__":
    unittest.main()
