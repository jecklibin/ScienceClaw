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
    def __init__(self):
        self.evaluate_calls = []
        self.wait_for_load_state_calls = []

    async def inner_text(self, _selector):
        return "Example page"

    async def evaluate(self, script):
        self.evaluate_calls.append(script)
        return None

    async def wait_for_load_state(self, state, timeout=None):
        self.wait_for_load_state_calls.append((state, timeout))
        return None


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, _messages):
        return _FakeResponse(self._content)


class _FakeStep:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return dict(self._data)


class _FakeManager:
    def __init__(self):
        self.events = []
        self.page = _FakePage()
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

    async def test_execute_mode_resumes_recording_after_step_persisted(self):
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="打开示例页面",
            ai_mode="execute",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "get_llm_model",
            return_value=_FakeModel("await page.evaluate('1 + 1')"),
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
        self.assertGreaterEqual(len(fake_manager.page.evaluate_calls), 2)
        self.assertEqual(fake_manager.page.evaluate_calls[0], "window.__rpa_paused = true")
        self.assertEqual(fake_manager.page.evaluate_calls[-1], "window.__rpa_paused = false")
        self.assertEqual(
            fake_manager.page.wait_for_load_state_calls,
            [("domcontentloaded", 1500), ("networkidle", 1000)],
        )


if __name__ == "__main__":
    unittest.main()
