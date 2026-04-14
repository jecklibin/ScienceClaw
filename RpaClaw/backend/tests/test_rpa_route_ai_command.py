import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

RPA_ROUTE_MODULE = importlib.import_module("backend.route.rpa")


class _FakePage:
    def __init__(self, manager=None, switch_to=None):
        self.manager = manager
        self.switch_to = switch_to
        self.evaluate_calls = []
        self.wait_for_load_state_calls = []
        self.wait_for_function_calls = []
        self.wait_for_timeout_calls = []
        self.inner_text_value = "Example page"
        self.url = "https://example.com/list"
        self.snapshot_queue = [
            {
                "url": "https://example.com/list",
                "title": "List Page",
                "bodyText": "Old list content",
                "interactiveValues": [],
            },
            {
                "url": "https://example.com/detail",
                "title": "Detail Page",
                "bodyText": "Fresh detail content",
                "interactiveValues": ["input | keyword | search | keyword | Search | Example"],
            },
        ]

    async def inner_text(self, _selector):
        return self.inner_text_value

    async def evaluate(self, script):
        self.evaluate_calls.append(script)
        if script.strip().startswith("() =>") and self.snapshot_queue:
            return self.snapshot_queue.pop(0)
        return None

    def get_by_role(self, *args, **kwargs):
        return self

    async def click(self):
        if self.manager and self.switch_to is not None:
            self.manager.page = self.switch_to
        return None

    async def wait_for_load_state(self, state, timeout=None):
        self.wait_for_load_state_calls.append((state, timeout))
        return None

    async def wait_for_function(self, script, timeout=None):
        self.wait_for_function_calls.append((script, timeout))
        return None

    async def wait_for_timeout(self, timeout):
        self.wait_for_timeout_calls.append(timeout)
        return None

    async def sleep(self, _seconds):
        return None


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, _messages):
        if isinstance(self._content, list):
            if not self._content:
                raise AssertionError("Fake model exhausted")
            return _FakeResponse(self._content.pop(0))
        return _FakeResponse(self._content)


class _FakeStep:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return dict(self._data)


class _FakeManager:
    def __init__(self):
        self.events = []
        self.page = _FakePage(manager=self)
        self.session = SimpleNamespace(user_id="user-1", active_tab_id="tab-1")

    async def get_session(self, _session_id):
        return self.session

    def get_page(self, _session_id):
        return self.page

    def pause_recording(self, _session_id):
        self.events.append("pause")

    def resume_recording(self, _session_id):
        self.events.append("resume")

    def suppress_navigation_events(self, _session_id, _tab_id, duration_ms=2000):
        self.events.append(("suppress_navigation", duration_ms))

    async def add_step(self, _session_id, step_data):
        self.events.append("add_step")
        return _FakeStep(step_data)


class SessionAICommandRouteTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_request_auth_token_falls_back_to_session_cookie(self):
        request = SimpleNamespace(
            headers={},
            cookies={RPA_ROUTE_MODULE.settings.session_cookie: "cookie-session-token"},
        )

        token = RPA_ROUTE_MODULE._extract_request_auth_token(request)

        self.assertEqual(token, "cookie-session-token")

    def test_build_ai_command_url_uses_request_origin_for_local_mode(self):
        request = SimpleNamespace(base_url="http://127.0.0.1:12001/")

        url = RPA_ROUTE_MODULE._build_ai_command_url_for_request(request, is_local=True)

        self.assertEqual(url, "http://127.0.0.1:12001/api/v1/rpa/ai-command")

    def test_build_ai_command_url_rewrites_localhost_for_sandbox_mode(self):
        request = SimpleNamespace(base_url="http://localhost:5173/")

        url = RPA_ROUTE_MODULE._build_ai_command_url_for_request(request, is_local=False)

        self.assertEqual(url, "http://host.docker.internal:5173/api/v1/rpa/ai-command")

    async def test_auto_mode_persists_operation_and_data_before_resuming(self):
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="打开示例页面并读取标题",
            output_variable="page_title",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")
        plan_response = (
            '{"operation":{"needed":true,"description":"打开示例页面","code":"await page.evaluate(\'1 + 1\')"},'
            '"data":{"needed":true,"description":"读取页面标题","extract_prompt":"读取当前页面标题","format":"text","output_variable":"page_title"}}'
        )

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "get_llm_model",
            return_value=_FakeModel([plan_response, "Example page title"]),
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            fake_manager.events,
            ["pause", ("suppress_navigation", 2000), "add_step", "resume"],
        )
        self.assertEqual(result["ai_result_mode"], "operation_and_data")
        self.assertEqual(result["operation_code"], "await page.evaluate('1 + 1')")
        self.assertEqual(result["data_value"], "Example page title")
        self.assertEqual(result["step"]["output_variable"], "page_title")
        self.assertEqual(result["step"]["data_prompt"], "读取当前页面标题")
        self.assertGreaterEqual(len(fake_manager.page.evaluate_calls), 4)
        self.assertIn("window.__rpa_paused = true", fake_manager.page.evaluate_calls)
        self.assertIn("window.__rpa_paused = false", fake_manager.page.evaluate_calls)
        self.assertEqual(
            fake_manager.page.wait_for_load_state_calls,
            [("domcontentloaded", 5000), ("networkidle", 2000)],
        )
        self.assertEqual(len(fake_manager.page.wait_for_function_calls), 1)
        self.assertIn(300, fake_manager.page.wait_for_timeout_calls)

    async def test_auto_mode_extracts_data_from_post_operation_context(self):
        fake_manager = _FakeManager()
        new_page = _FakePage(manager=fake_manager)
        new_page.snapshot_queue = [
            {
                "url": "https://example.com/detail",
                "title": "Detail Page",
                "bodyText": "Fresh detail content",
                "interactiveValues": ["input | keyword | search | keyword | Search | Example"],
            },
        ]
        fake_manager.page.switch_to = new_page
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="打开详情页并读取最新标题",
            output_variable="detail_title",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")
        plan_response = (
            '{"operation":{"needed":true,"description":"打开详情页","code":"await page.get_by_role(\'link\', name=\'详情\').click()"},'
            '"data":{"needed":true,"description":"读取最新详情标题","extract_prompt":"读取当前详情页标题","format":"text","output_variable":"detail_title"}}'
        )
        captured_messages = []

        async def _fake_invoke(messages, _model_config):
            captured_messages.append(messages)
            if len(captured_messages) == 1:
                return plan_response
            return "Detail page title"

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "_invoke_ai_command_model",
            side_effect=_fake_invoke,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["data_value"], "Detail page title")
        extraction_messages = captured_messages[1]
        self.assertIn("https://example.com/detail", extraction_messages[0][1])
        self.assertIn("Fresh detail content", extraction_messages[0][1])
        self.assertNotIn("Old list content", extraction_messages[0][1])


if __name__ == "__main__":
    unittest.main()
