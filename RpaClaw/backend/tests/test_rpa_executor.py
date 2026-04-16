import importlib
import threading
import unittest


EXECUTOR_MODULE = importlib.import_module("backend.rpa.executor")


class _FakePage:
    def __init__(self, context, title="Page", url="about:blank"):
        self.context = context
        self._title = title
        self.url = url
        self.handlers = {}
        self.default_timeout = None
        self.default_navigation_timeout = None

    async def title(self):
        return self._title

    async def expose_function(self, _name, _fn):
        return None

    async def evaluate(self, _script):
        return None

    async def bring_to_front(self):
        return None

    async def wait_for_timeout(self, _timeout):
        return None

    def set_default_timeout(self, timeout):
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout):
        self.default_navigation_timeout = timeout

    def on(self, event_name, handler):
        self.handlers[event_name] = handler


class _FakeContext:
    def __init__(self):
        self.handlers = {}
        self.closed = False
        self.pages = []

    async def new_page(self):
        page = _FakePage(self, title=f"Page {len(self.pages) + 1}")
        self.pages.append(page)
        return page

    def on(self, event_name, handler):
        self.handlers[event_name] = handler

    async def create_popup(self):
        popup = _FakePage(self, title="Popup", url="https://example.com/popup")
        self.pages.append(popup)
        handler = self.handlers.get("page")
        if handler:
            handler(popup)
        return popup

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self):
        self.contexts = []
        self.new_context_calls = []

    async def new_context(self, **kwargs):
        self.new_context_calls.append(kwargs)
        context = _FakeContext()
        self.contexts.append(context)
        return context


class _FakeSessionManager:
    def __init__(self):
        self.attached = []
        self.registered = []
        self.context_pages = []
        self.detached = []

    def attach_context(self, session_id, context):
        self.attached.append((session_id, context))

    async def register_page(self, session_id, page, make_active=False):
        self.registered.append((session_id, page, make_active))
        return "root-tab"

    async def register_context_page(self, session_id, page, make_active=True):
        self.context_pages.append((session_id, page, make_active))
        return "popup-tab"

    def detach_context(self, session_id, context=None):
        self.detached.append((session_id, context))


class ScriptExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_registers_popup_pages_with_session_manager(self):
        browser = _FakeBrowser()
        session_manager = _FakeSessionManager()
        page_registry = {}
        script = """
import asyncio

async def execute_skill(page, **kwargs):
    await page.context.create_popup()
    await asyncio.sleep(0)
    return {"ok": True}
"""

        result = await EXECUTOR_MODULE.ScriptExecutor().execute(
            browser,
            script,
            session_id="session-1",
            page_registry=page_registry,
            session_manager=session_manager,
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(browser.contexts), 1)
        self.assertEqual(
            browser.new_context_calls,
            [
                {
                    "no_viewport": True,
                    "accept_downloads": True,
                    "ignore_https_errors": True,
                }
            ],
        )
        self.assertEqual(len(session_manager.attached), 1)
        self.assertEqual(len(session_manager.registered), 1)
        self.assertEqual(len(session_manager.context_pages), 1)
        self.assertEqual(session_manager.registered[0][0], "session-1")
        self.assertEqual(session_manager.context_pages[0][0], "session-1")
        self.assertEqual(session_manager.detached, [("session-1", browser.contexts[0])])
        self.assertEqual(page_registry, {})
        self.assertTrue(browser.contexts[0].closed)


class StepExecutionErrorTests(unittest.IsolatedAsyncioTestCase):
    """Tests for STEP_FAILED: parsing in the except Exception block."""

    async def test_execute_returns_failed_step_index_on_step_error(self):
        executor = EXECUTOR_MODULE.ScriptExecutor()
        script = '''
class StepExecutionError(Exception):
    def __init__(self, step_index, original_error):
        self.step_index = step_index
        self.original_error = original_error
        super().__init__(f"STEP_FAILED:{step_index}:{original_error}")

async def execute_skill(page, **kwargs):
    raise StepExecutionError(step_index=2, original_error="Timeout 30000ms exceeded")
'''
        browser = _FakeBrowser()
        result = await executor.execute(browser, script)

        self.assertFalse(result["success"])
        self.assertEqual(result["failed_step_index"], 2)
        self.assertEqual(result["error"], "Timeout 30000ms exceeded")

    async def test_execute_returns_none_failed_step_index_on_generic_error(self):
        executor = EXECUTOR_MODULE.ScriptExecutor()
        script = '''
async def execute_skill(page, **kwargs):
    raise RuntimeError("something broke")
'''
        browser = _FakeBrowser()
        result = await executor.execute(browser, script)

        self.assertFalse(result["success"])
        self.assertIsNone(result["failed_step_index"])

    async def test_execute_returns_none_failed_step_index_on_success(self):
        executor = EXECUTOR_MODULE.ScriptExecutor()
        script = '''
async def execute_skill(page, **kwargs):
    return {"ok": True}
'''
        browser = _FakeBrowser()
        result = await executor.execute(browser, script)

        self.assertTrue(result["success"])
        self.assertIsNone(result.get("failed_step_index"))

    async def test_execute_sanitizes_non_json_safe_success_data(self):
        executor = EXECUTOR_MODULE.ScriptExecutor()
        script = '''
import threading

async def execute_skill(page, **kwargs):
    return {"lock": threading.Lock(), "ok": True}
'''
        browser = _FakeBrowser()
        result = await executor.execute(browser, script)

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["ok"], True)
        self.assertIsInstance(result["data"]["lock"], str)
        self.assertIn("lock", result["data"]["lock"].lower())


if __name__ == "__main__":
    unittest.main()
