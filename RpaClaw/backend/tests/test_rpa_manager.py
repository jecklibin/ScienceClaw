import importlib.util
import importlib
import sys
import unittest
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

    def on(self, event_name, handler):
        self.handlers[event_name] = handler


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

    async def title(self):
        return self._title

    async def expose_function(self, _name, _fn):
        return None

    async def evaluate(self, _script):
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
