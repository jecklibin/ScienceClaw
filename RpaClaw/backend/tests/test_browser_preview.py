import unittest

from backend.browser_preview import BrowserPreviewRegistry


class _FakeContext:
    def __init__(self):
        self.pages = []


class _FakePage:
    def __init__(self, context, url):
        self.context = context
        self.url = url
        self.brought_to_front = 0
        self._closed = False
        context.pages.append(self)

    def is_closed(self):
        return self._closed

    async def bring_to_front(self):
        self.brought_to_front += 1


class BrowserPreviewRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_context_page_becomes_active_tab_in_snapshot(self):
        registry = BrowserPreviewRegistry()
        context = _FakeContext()
        first = _FakePage(context, "https://example.test/first")

        await registry.register("session-1", first)
        second = _FakePage(context, "https://example.test/second")

        tabs = registry.list_tabs("session-1")

        self.assertEqual([tab["url"] for tab in tabs], [
            "https://example.test/first",
            "https://example.test/second",
        ])
        self.assertFalse(tabs[0]["active"])
        self.assertTrue(tabs[1]["active"])
        self.assertIs(registry.get_active_page("session-1"), second)

    async def test_activate_tab_switches_active_page_and_brings_it_to_front(self):
        registry = BrowserPreviewRegistry()
        context = _FakeContext()
        first = _FakePage(context, "https://example.test/first")
        second = _FakePage(context, "https://example.test/second")

        await registry.register("session-1", first)
        second_tab = registry.list_tabs("session-1")[1]["tab_id"]

        result = await registry.activate_tab("session-1", second_tab)

        self.assertEqual(result["tab_id"], second_tab)
        self.assertIs(registry.get_active_page("session-1"), second)
        self.assertEqual(second.brought_to_front, 1)


if __name__ == "__main__":
    unittest.main()
