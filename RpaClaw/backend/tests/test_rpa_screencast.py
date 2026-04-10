import importlib
import unittest


SCREencAST_MODULE = importlib.import_module("backend.rpa.screencast")


class _FakeWebSocket:
    def __init__(self):
        self.messages = []
        self.closed = None

    async def send_json(self, payload):
        self.messages.append(payload)

    async def close(self, code=1000, reason=""):
        self.closed = {"code": code, "reason": reason}


class _FailingWebSocket:
    def __init__(self):
        self.closed = None

    async def send_json(self, payload):
        raise RuntimeError("socket send failed")

    async def close(self, code=1000, reason=""):
        self.closed = {"code": code, "reason": reason}


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

    async def test_emit_tabs_snapshot_stops_controller_when_socket_send_fails(self):
        ws = _FailingWebSocket()
        controller = SCREencAST_MODULE.SessionScreencastController(
            page_provider=lambda: None,
            tabs_provider=lambda: [{"tab_id": "tab-1"}],
        )
        controller._ws = ws
        controller._running = True

        await controller._emit_tabs_snapshot(force=True)

        self.assertFalse(controller._running)
        self.assertEqual(ws.closed["code"], SCREencAST_MODULE.SCREENCAST_FATAL_CLOSE_CODE)

    async def test_frame_send_failure_stops_controller(self):
        ws = _FailingWebSocket()
        controller = SCREencAST_MODULE.SessionScreencastController(
            page_provider=lambda: None,
            tabs_provider=lambda: [],
        )
        controller._ws = ws
        controller._cdp = _FakeCDPSession()
        controller._running = True

        await controller._on_frame({"data": "abc", "metadata": {"deviceWidth": 800, "deviceHeight": 600}})

        self.assertFalse(controller._running)
        self.assertEqual(ws.closed["reason"], "frame send failed")


if __name__ == "__main__":
    unittest.main()
