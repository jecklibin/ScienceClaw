import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpa_client import RpaClawClient, RpaClawTimeoutError, active_tab_url, parse_sse_lines, urls_match


class FakeResponse:
    def __init__(self, body: dict):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")


class RpaClawClientTests(unittest.TestCase):
    def test_resolves_model_config_id_by_model_name(self):
        requests = []

        def fake_urlopen(req, timeout):
            requests.append(req)
            return FakeResponse(
                {
                    "data": [
                        {"id": "model-a", "name": "Fast", "model_name": "fast-model"},
                        {"id": "model-b", "name": "Deep", "model_name": "deep-model"},
                    ]
                }
            )

        with patch("rpa_client.request.urlopen", fake_urlopen):
            client = RpaClawClient("http://rpaclaw", model_name="deep-model")

        self.assertEqual(client.model_config_id, "model-b")
        self.assertEqual(requests[0].full_url, "http://rpaclaw/api/v1/models")

    def test_chat_payload_includes_resolved_model_config_id(self):
        client = RpaClawClient("http://rpaclaw", model_config_id="model-b")
        req = client._request("POST", "/api/v1/rpa/session/s1/chat", {"message": "do it", "mode": "chat"})

        self.assertEqual(json.loads(req.data.decode("utf-8"))["model_config_id"], "model-b")

    def test_stop_session_posts_to_stop_endpoint(self):
        requests = []

        def fake_urlopen(req, timeout):
            requests.append(req)
            return FakeResponse({"status": "success"})

        with patch("rpa_client.request.urlopen", fake_urlopen):
            client = RpaClawClient("http://rpaclaw")
            client.stop_session("session-1")

        self.assertEqual(requests[0].get_method(), "POST")
        self.assertEqual(requests[0].full_url, "http://rpaclaw/api/v1/rpa/session/session-1/stop")

    def test_generate_script_posts_params_to_generate_endpoint(self):
        requests = []

        def fake_urlopen(req, timeout):
            requests.append((req, timeout))
            return FakeResponse({"status": "success", "script": "async def execute_skill(page): pass"})

        with patch("rpa_client.request.urlopen", fake_urlopen):
            client = RpaClawClient("http://rpaclaw")
            response = client.generate_script("session-1", {"contract_number": "CT-001"})

        self.assertEqual(response["status"], "success")
        self.assertEqual(requests[0][0].full_url, "http://rpaclaw/api/v1/rpa/session/session-1/generate")
        self.assertEqual(json.loads(requests[0][0].data.decode("utf-8")), {"params": {"contract_number": "CT-001"}})

    def test_test_script_uses_replay_timeout(self):
        requests = []

        def fake_urlopen(req, timeout):
            requests.append((req, timeout))
            return FakeResponse({"status": "success", "result": {"success": True}})

        with patch("rpa_client.request.urlopen", fake_urlopen):
            client = RpaClawClient("http://rpaclaw")
            client.test_script("session-1", timeout_s=123)

        self.assertEqual(requests[0][0].full_url, "http://rpaclaw/api/v1/rpa/session/session-1/test")
        self.assertEqual(requests[0][1], 123)
        self.assertEqual(json.loads(requests[0][0].data.decode("utf-8")), {"params": {}})

    def test_test_script_sends_optional_setup_navigation(self):
        requests = []

        def fake_urlopen(req, timeout):
            requests.append((req, timeout))
            return FakeResponse({"status": "success", "result": {"success": True}})

        with patch("rpa_client.request.urlopen", fake_urlopen):
            client = RpaClawClient("http://rpaclaw")
            client.test_script(
                "session-1",
                {"q": "abc"},
                timeout_s=123,
                setup_navigation=["http://eval/eval-auth.html?token=t", "http://eval/start"],
            )

        self.assertEqual(
            json.loads(requests[0][0].data.decode("utf-8")),
            {
                "params": {"q": "abc"},
                "setup_navigation": ["http://eval/eval-auth.html?token=t", "http://eval/start"],
            },
        )

    def test_active_tab_url_and_url_match_ignore_query(self):
        tabs = {
            "active_tab_id": "tab-2",
            "tabs": [
                {"id": "tab-1", "url": "http://eval/dashboard"},
                {"id": "tab-2", "url": "http://eval/regression-lab?x=1"},
            ],
        }

        self.assertEqual(active_tab_url(tabs), "http://eval/regression-lab?x=1")
        self.assertTrue(urls_match("http://eval/regression-lab?x=1", "http://eval/regression-lab"))
        self.assertFalse(urls_match("http://eval/dashboard", "http://eval/regression-lab"))

    def test_run_instruction_times_out_and_stops_session(self):
        client = RpaClawClient("http://rpaclaw")
        stopped = []

        client.start_session = lambda _case_id: "session-1"
        client.navigate = lambda _session_id, _url: None
        client.stop_session = lambda session_id, ignore_errors=False: stopped.append((session_id, ignore_errors))

        def slow_events(_session_id, _instruction, *, business_instruction=None):
            yield {"event": "agent_thought", "data": {"message": "started"}}
            time.sleep(0.2)
            yield {"event": "agent_done", "data": {}}

        client.iter_chat_events = slow_events

        with self.assertRaises(RpaClawTimeoutError) as raised:
            client.run_instruction(
                case_id="case-1",
                start_url="http://eval/login",
                instruction="do it",
                timeout_s=0.05,
            )

        self.assertEqual(raised.exception.session_id, "session-1")
        self.assertEqual(raised.exception.raw_events[0]["event"], "agent_thought")
        self.assertIn(("session-1", True), stopped)

    def test_chat_wall_timeout_returns_after_terminal_event_even_if_stream_hangs(self):
        client = RpaClawClient("http://rpaclaw")
        stopped = []
        client.stop_session = lambda session_id, ignore_errors=False: stopped.append((session_id, ignore_errors))

        def terminal_then_hang(_session_id, _instruction, *, business_instruction=None):
            yield {"event": "error", "data": {"message": "page closed"}}
            time.sleep(2)

        client.iter_chat_events = terminal_then_hang

        started = time.perf_counter()
        events = client.chat_with_wall_timeout("session-1", "do it", timeout_s=10)

        self.assertLess(time.perf_counter() - started, 7)
        self.assertEqual(events[0]["event"], "error")
        self.assertIn(("session-1", True), stopped)

    def test_parse_sse_lines_stops_after_agent_aborted(self):
        lines = [
            "event: agent_thought\n",
            "data: {\"text\":\"thinking\"}\n",
            "\n",
            "event: agent_aborted\n",
            "data: {\"reason\":\"failed\"}\n",
            "\n",
            "event: agent_thought\n",
            "data: {\"text\":\"should not be consumed\"}\n",
            "\n",
        ]

        events = list(parse_sse_lines(lines, stop_on_terminal=True))

        self.assertEqual([event["event"] for event in events], ["agent_thought", "agent_aborted"])


if __name__ == "__main__":
    unittest.main()
