import importlib
import unittest
from unittest.mock import patch


SCREencAST_MODULE = importlib.import_module("backend.rpa.screencast")


class _FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


class _FakeCDPSession:
    def __init__(self):
        self.handlers = {}
        self.sent = []
        self.detached = False

    def on(self, event_name, handler):
        self.handlers[event_name] = handler

    async def send(self, method, params):
        self.sent.append((method, params))

    async def detach(self):
        self.detached = True


class _FakeContext:
    def __init__(self, failures_before_success=0):
        self.failures_before_success = failures_before_success
        self.calls = 0

    async def new_cdp_session(self, _page):
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise RuntimeError("attach failed")
        return _FakeCDPSession()


class _FakePage:
    def __init__(self, context):
        self.context = context


class SessionScreencastControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_page_retries_once_before_succeeding(self):
        context = _FakeContext(failures_before_success=1)
        page = _FakePage(context)
        ws = _FakeWebSocket()
        controller = SCREencAST_MODULE.SessionScreencastController(
            page_provider=lambda: page,
            tabs_provider=lambda: [],
        )
        controller._ws = ws
        controller._running = True

        await controller._switch_page_if_needed(force=True)

        self.assertEqual(context.calls, 2)
        self.assertIsNotNone(controller._cdp)
        self.assertEqual([message["type"] for message in ws.messages], [])

    async def test_switch_page_emits_preview_error_after_retries_fail(self):
        context = _FakeContext(failures_before_success=3)
        page = _FakePage(context)
        ws = _FakeWebSocket()
        controller = SCREencAST_MODULE.SessionScreencastController(
            page_provider=lambda: page,
            tabs_provider=lambda: [],
        )
        controller._ws = ws
        controller._running = True

        with self.assertRaises(RuntimeError):
            await controller._switch_page_if_needed(force=True)

        self.assertEqual(context.calls, 2)
        self.assertEqual(ws.messages[-1]["type"], "preview_error")


class RouteScreencastSelectionTests(unittest.TestCase):
    def test_node_mode_prefers_proxy_selection(self):
        import backend.route.rpa as route_rpa

        with patch.object(route_rpa.settings, "rpa_engine_mode", "node"):
            self.assertTrue(route_rpa._should_proxy_session_screencast())


if __name__ == "__main__":
    unittest.main()
