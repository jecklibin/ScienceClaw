import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_app_client import EvalAppUserSession
from runner import (
    CaseAssertionError,
    assert_api_assertions,
    assert_expected_telemetry,
    build_browser_instruction,
    build_eval_auth_url,
    configure_console_output,
    extract_final_url,
    generated_script_uses_runtime_ai,
    render_console_summary,
    resolve_case_timeout_s,
    read_artifact_text,
    replay_generated_skill,
    run_case,
)


class RunnerAssertionTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(__file__).resolve().parents[1] / ".runtime-test"
        self.tmp_dir.mkdir(exist_ok=True)

    def tearDown(self):
        for path in self.tmp_dir.glob("*"):
            if path.is_file():
                path.unlink()

    def test_console_output_uses_utf8_with_replacement_fallback(self):
        class FakeStream:
            def __init__(self):
                self.calls = []

            def reconfigure(self, **kwargs):
                self.calls.append(kwargs)

        original_stdout = sys.stdout
        original_stderr = sys.stderr
        fake_stdout = FakeStream()
        fake_stderr = FakeStream()
        try:
            sys.stdout = fake_stdout
            sys.stderr = fake_stderr
            configure_console_output()
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

        self.assertEqual(fake_stdout.calls, [{"encoding": "utf-8", "errors": "backslashreplace"}])
        self.assertEqual(fake_stderr.calls, [{"encoding": "utf-8", "errors": "backslashreplace"}])

    def test_final_url_prefers_last_accepted_trace_after_page(self):
        events = [
            {
                "event": "trace_added",
                "data": {
                    "accepted": True,
                    "after_page": {"url": "http://localhost:5175/contracts"},
                    "output": {"current_url": "http://localhost:5175/login"},
                },
            }
        ]

        self.assertEqual(extract_final_url(events, None), "http://localhost:5175/contracts")

    def test_numeric_expected_values_match_formatted_currency_text(self):
        assert_expected_telemetry(
            {"extracted_fields": {"amount": 680000}},
            {"output_text": "合同金额 ¥680,000.00 币种 CNY", "downloads": []},
        )

    def test_extracted_fields_do_not_pass_from_page_visible_text_only(self):
        with self.assertRaises(CaseAssertionError) as raised:
            assert_expected_telemetry(
                {"extracted_fields": {"amount": 680000}},
                {"visible_text": "合同金额 ¥680,000.00 币种 CNY", "downloads": []},
            )

        self.assertEqual(raised.exception.stage, "unsupported_output_telemetry")

    def test_contract_extract_case_starts_on_detail_without_reopening_it(self):
        case_path = Path(__file__).resolve().parent / "cases" / "contract_extract_001.yaml"
        import yaml

        case = yaml.safe_load(case_path.read_text(encoding="utf-8"))

        self.assertEqual(case["start_path"], "/contracts/CT-2026-RPA-001")
        self.assertIn("当前已经在合同详情页", case["instruction"])
        self.assertNotIn("打开合同", case["instruction"])

    def test_business_instruction_excludes_login_setup(self):
        text = build_browser_instruction(
            case={"instruction": "当前在工作台页面。请进入合同管理页面。"},
            login_url="http://localhost:5175/login",
            start_url="http://localhost:5175/dashboard",
            username="buyer",
            password="buyer123",
        )

        self.assertIn("只执行下面的业务任务", text)
        self.assertIn("当前在工作台页面。请进入合同管理页面。", text)
        self.assertNotIn("buyer123", text)
        self.assertNotIn("http://localhost:5175/login", text)

    def test_eval_auth_url_encodes_token(self):
        text = build_eval_auth_url("http://localhost:5175", "token with/slash+plus")

        self.assertEqual("http://localhost:5175/eval-auth.html?token=token%20with%2Fslash%2Bplus", text)

    def test_run_case_separates_login_setup_from_business_instruction(self):
        args = type(
            "Args",
            (),
            {
                "reset_token": "rpa-eval-reset",
                "eval_frontend_url": "http://localhost:5175",
                "case_timeout_s": 180,
            },
        )()
        case = {
            "id": "case_split",
            "name": "split",
            "tags": [],
            "user": {"username": "buyer"},
            "start_path": "/dashboard",
            "instruction": "当前在工作台页面。请进入合同管理页面。",
            "expected": {},
            "assertions": {},
        }
        eval_client = FakeRunnerEvalClient()
        rpa_client = FakeRunnerRpaClient()

        result = run_case(case, args, eval_client, rpa_client)

        self.assertTrue(result["passed"], result.get("failure_message"))
        self.assertEqual(
            [
                ("session-1", "http://localhost:5175/eval-auth.html?token=token"),
                ("session-1", "http://localhost:5175/dashboard"),
            ],
            rpa_client.navigations,
        )
        self.assertEqual(1, len(rpa_client.instructions))
        self.assertIn("只执行下面的业务任务", rpa_client.instructions[0])
        self.assertNotIn("buyer123", rpa_client.instructions[0])
        self.assertEqual("当前在工作台页面。请进入合同管理页面。", rpa_client.business_instruction)

    def test_case_timeout_uses_yaml_override(self):
        args = type("Args", (), {"case_timeout_s": 180})()

        self.assertEqual(resolve_case_timeout_s({"id": "simple", "timeout_s": 90}, args), 90)

    def test_run_case_can_verify_generate_and_replay_after_recording(self):
        args = type(
            "Args",
            (),
            {
                "reset_token": "rpa-eval-reset",
                "eval_frontend_url": "http://localhost:5175",
                "case_timeout_s": 180,
                "verify_replay": True,
                "replay_timeout_s": 90,
            },
        )()
        case = {
            "id": "case_replay",
            "name": "replay",
            "tags": [],
            "user": {"username": "buyer"},
            "start_path": "/contracts",
            "instruction": "Open contracts.",
            "expected": {},
            "assertions": {},
        }
        eval_client = FakeRunnerEvalClient()
        rpa_client = FakeRunnerRpaClient()
        action_log = []
        eval_client.actions = action_log
        rpa_client.actions = action_log

        result = run_case(case, args, eval_client, rpa_client)

        self.assertTrue(result["passed"], result.get("failure_message"))
        self.assertEqual("passed", result["phase_results"]["record"]["status"])
        self.assertEqual("passed", result["phase_results"]["compile"]["status"])
        self.assertEqual("passed", result["phase_results"]["replay"]["status"])
        self.assertEqual([("session-1", {})], rpa_client.generates)
        self.assertEqual(
            [
                (
                    "session-1",
                    {},
                    [
                        "http://localhost:5175/eval-auth.html?token=token",
                        "http://localhost:5175/contracts",
                    ],
                )
            ],
            rpa_client.tests,
        )
        self.assertEqual(
            [
                "reset:rpa-eval-reset",
                "login:buyer",
                "generate:session-1",
                "test:session-1",
            ],
            action_log[-4:],
        )

    def test_run_case_uses_replay_fixture_variant_and_replay_assertions(self):
        args = type(
            "Args",
            (),
            {
                "reset_token": "rpa-eval-reset",
                "eval_frontend_url": "http://localhost:5175",
                "case_timeout_s": 180,
                "verify_replay": True,
                "replay_timeout_s": 90,
            },
        )()
        case = {
            "id": "case_dynamic_replay",
            "name": "dynamic replay",
            "tags": [],
            "user": {"username": "buyer"},
            "start_path": "/regression-lab",
            "instruction": "Open the first item.",
            "expected": {},
            "assertions": {},
            "api_assertions": [{"name": "record assertion should not be used during replay", "path": "/api/unused"}],
            "replay": {
                "fixture_variant": "dynamic_first_item_reordered",
                "api_assertions": [
                    {
                        "name": "replay assertion",
                        "path": "/api/lab/events",
                        "find": {"event_key": "dynamic_first_item_opened"},
                        "absent": True,
                    }
                ],
            },
        }
        eval_client = FakeRunnerEvalClient()
        rpa_client = FakeRunnerRpaClient()
        action_log = []
        eval_client.actions = action_log
        rpa_client.actions = action_log

        result = run_case(case, args, eval_client, rpa_client)

        self.assertTrue(result["passed"], result.get("failure_message"))
        self.assertIn("reset:rpa-eval-reset:dynamic_first_item_reordered", action_log)
        self.assertIn("get:/api/lab/events", action_log)
        self.assertNotIn("get:/api/unused", action_log)

    def test_recording_aborted_is_record_phase_failure(self):
        args = type(
            "Args",
            (),
            {
                "reset_token": "rpa-eval-reset",
                "eval_frontend_url": "http://localhost:5175",
                "case_timeout_s": 180,
                "verify_replay": True,
                "replay_timeout_s": 90,
            },
        )()
        case = {
            "id": "case_record_fail",
            "name": "record fail",
            "tags": [],
            "user": {"username": "buyer"},
            "start_path": "/contracts",
            "instruction": "Open contracts.",
            "expected": {},
            "assertions": {},
        }
        eval_client = FakeRunnerEvalClient()
        rpa_client = FakeRunnerRpaClient()
        rpa_client.chat_events = [{"event": "agent_aborted", "data": {"reason": "no terminal evidence"}}]

        result = run_case(case, args, eval_client, rpa_client)

        self.assertFalse(result["passed"])
        self.assertEqual("record", result["failure_stage"])
        self.assertEqual("failed", result["phase_results"]["record"]["status"])
        self.assertEqual([], rpa_client.generates)

    def test_replay_generated_skill_fails_when_generated_script_uses_runtime_ai(self):
        rpa_client = FakeRunnerRpaClient()
        rpa_client.generated_script = (
            "async def execute_skill(page):\n"
            "    await _execute_runtime_ai_instruction(page, {}, 'x', 'y')\n"
        )

        with self.assertRaises(CaseAssertionError) as raised:
            replay_generated_skill(
                rpa_client=rpa_client,
                session_id="session-1",
                params={},
                timeout_s=90,
                allow_runtime_ai=False,
            )

        self.assertEqual("compile", raised.exception.stage)
        self.assertIn("runtime AI", str(raised.exception))
        self.assertEqual([], rpa_client.tests)

    def test_generated_script_runtime_ai_check_ignores_unused_helper_definition(self):
        script = (
            "async def _execute_runtime_ai_instruction(page, results, instruction, output_key):\n"
            "    return {}\n\n"
            "async def execute_skill(page):\n"
            "    return {'ok': True}\n"
        )

        self.assertFalse(generated_script_uses_runtime_ai(script))

    def test_run_case_marks_compile_phase_failed_when_replay_static_check_fails(self):
        args = type(
            "Args",
            (),
            {
                "reset_token": "rpa-eval-reset",
                "eval_frontend_url": "http://localhost:5175",
                "case_timeout_s": 180,
                "verify_replay": True,
                "replay_timeout_s": 90,
            },
        )()
        case = {
            "id": "case_compile_fail",
            "name": "compile fail",
            "tags": [],
            "user": {"username": "buyer"},
            "start_path": "/contracts",
            "instruction": "Open contracts.",
            "expected": {},
            "assertions": {},
        }
        eval_client = FakeRunnerEvalClient()
        rpa_client = FakeRunnerRpaClient()
        rpa_client.generated_script = (
            "async def execute_skill(page):\n"
            "    await _execute_runtime_ai_instruction(page, {}, 'x', 'y')\n"
        )

        result = run_case(case, args, eval_client, rpa_client)

        self.assertFalse(result["passed"])
        self.assertEqual("compile", result["failure_stage"])
        self.assertEqual("failed", result["phase_results"]["compile"]["status"])
        self.assertEqual("passed", result["phase_results"]["record"]["status"])

    def test_case_timeout_falls_back_to_cli_default(self):
        args = type("Args", (), {"case_timeout_s": 180})()

        self.assertEqual(resolve_case_timeout_s({"id": "default"}, args), 180)

    def test_all_cases_define_reasonable_timeout(self):
        import yaml

        cases_dir = Path(__file__).resolve().parent / "cases"
        for path in cases_dir.glob("*.yaml"):
            case = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIn("timeout_s", case, path.name)
            self.assertGreater(case["timeout_s"], 0, path.name)
            self.assertLessEqual(case["timeout_s"], 240, path.name)

    def test_cases_do_not_use_visible_text_without_page_telemetry(self):
        import yaml

        offenders = []
        cases_dir = Path(__file__).resolve().parent / "cases"
        for path in cases_dir.glob("*.yaml"):
            case = yaml.safe_load(path.read_text(encoding="utf-8"))
            if (case.get("expected") or {}).get("visible_text"):
                offenders.append(path.name)

        self.assertEqual([], offenders)

    def test_download_contains_can_validate_local_xlsx_content(self):
        path = self.tmp_dir / "contracts_2026.xlsx"
        self._write_xlsx(path, [["合同编号", "供应商编号"], ["CT-2026-RPA-001", "SUP-2026-001"]])

        assert_expected_telemetry(
            {
                "download": {
                    "filename": "contracts_2026.xlsx",
                    "contains": ["CT-2026-RPA-001", "SUP-2026-001"],
                }
            },
            {"visible_text": "", "downloads": [str(path)]},
        )

    def test_download_contains_fails_without_readable_content(self):
        with self.assertRaises(CaseAssertionError) as raised:
            assert_expected_telemetry(
                {
                    "download": {
                        "filename": "contracts_2026.xlsx",
                        "contains": ["CT-2026-RPA-001"],
                    }
                },
                {"visible_text": "", "downloads": ["contracts_2026.xlsx"]},
            )

        self.assertEqual(raised.exception.stage, "unsupported_download_content_telemetry")

    def test_json_artifact_text_flattens_values_for_content_assertions(self):
        path = self.tmp_dir / "artifact.json"
        path.write_text(json.dumps({"number": "PR-2026-RPA-NEW-001", "amount": 34000}), encoding="utf-8")

        self.assertIn("PR-2026-RPA-NEW-001", read_artifact_text(path))

    def test_empty_result_accepts_agent_conclusion_text(self):
        assert_expected_telemetry(
            {"output_text": ["没有匹配结果"]},
            {"output_text": "searched_contract_number CT-2026-RPA-NOT-FOUND no_match True conclusion 没有匹配结果"},
        )

    def test_regression_lab_visible_status_does_not_expose_internal_event_keys(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "frontend"
            / "src"
            / "views"
            / "RegressionLab.vue"
        ).read_text(encoding="utf-8") + (
            Path(__file__).resolve().parents[1]
            / "frontend"
            / "src"
            / "views"
            / "PopupReport.vue"
        ).read_text(encoding="utf-8")

        for internal_key in (
            "body_click_export_all completed",
            "collection_row_action completed",
            "split_grid_file_opened completed",
            "empty_audit_filtered completed",
            "dataflow_form_submitted",
            "parameterized_contract_opened",
            "modal_supplier_saved",
            "popup_report_downloaded",
        ):
            self.assertNotIn(internal_key, source)

    def test_empty_result_accepts_structured_zero_count_without_phrase(self):
        assert_expected_telemetry(
            {
                "empty_result": {"query": "CT-2026-RPA-NOT-FOUND", "should_open_record": False},
                "output_text": [["没有匹配结果", "未找到匹配结果"]],
            },
            {
                "output_text": (
                    "search_value\nCT-2026-RPA-NOT-FOUND\n"
                    "row_count\n0\nempty_result_confirmed\nTrue"
                )
            },
        )

    def test_empty_result_accepts_matched_rows_zero(self):
        assert_expected_telemetry(
            {"empty_result": {"query": "CT-2026-RPA-NOT-FOUND", "should_open_record": False}},
            {
                "output_text": (
                    'SKILL_DATA:{"no_match_result":{"filled_value":"CT-2026-RPA-NOT-FOUND",'
                    '"matched_rows":0,"conclusion":"no matching result"}}'
                )
            },
        )

    def test_empty_result_accepts_snake_case_boolean_signal(self):
        assert_expected_telemetry(
            {"empty_result": {"query": "CT-2026-RPA-NOT-FOUND", "should_open_record": False}},
            {
                "output_text": (
                    'SKILL_DATA:{"no_matching_contract_result":{'
                    '"contract_no":"CT-2026-RPA-NOT-FOUND",'
                    '"empty_result":true,"evidence":"zero_table_rows"}}'
                )
            },
        )

    def test_empty_result_accepts_no_matching_results_signal(self):
        assert_expected_telemetry(
            {"empty_result": {"query": "CT-2026-RPA-NOT-FOUND", "should_open_record": False}},
            {
                "output_text": (
                    'SKILL_DATA:{"no_matching_contract_result":{'
                    '"filled_value":"CT-2026-RPA-NOT-FOUND",'
                    '"no_matching_results":true}}'
                )
            },
        )

    def test_empty_result_accepts_no_matching_result_value(self):
        assert_expected_telemetry(
            {"empty_result": {"query": "CT-2026-RPA-NOT-FOUND", "should_open_record": False}},
            {
                "output_text": (
                    'SKILL_DATA:{"no_matching_contracts_result":{'
                    '"filled_value":"CT-2026-RPA-NOT-FOUND",'
                    '"result":"no_matching_contracts"}}'
                )
            },
        )

    def test_empty_result_accepts_no_match_confirmed_signal(self):
        assert_expected_telemetry(
            {"empty_result": {"query": "CT-2026-RPA-NOT-FOUND", "should_open_record": False}},
            {
                "output_text": (
                    'SKILL_DATA:{"contract_no_match_result":{'
                    '"searched_contract_number":"CT-2026-RPA-NOT-FOUND",'
                    '"no_match_confirmed":true}}'
                )
            },
        )

    def test_empty_result_accepts_flattened_matched_rows_zero(self):
        assert_expected_telemetry(
            {"empty_result": {"query": "CT-2026-RPA-NOT-FOUND", "should_open_record": False}},
            {
                "output_text": (
                    "filled_value\nCT-2026-RPA-NOT-FOUND\n"
                    "matched_rows\n0\nconclusion\nno matching result"
                )
            },
        )

    def test_empty_result_accepts_empty_state_visibility_signal(self):
        assert_expected_telemetry(
            {"empty_result": {"query": "CT-2026-RPA-NOT-FOUND", "should_open_record": False}},
            {
                "output_text": (
                    "searched_contract_number\nCT-2026-RPA-NOT-FOUND\n"
                    "matched_rows\nempty_state_visible\nTrue\nconclusion\nno_match_confirmed"
                )
            },
        )

    def test_output_text_accepts_alternative_phrasings(self):
        assert_expected_telemetry(
            {"output_text": [["没有匹配结果", "未找到匹配结果"]]},
            {"output_text": "searched_contract_number CT-2026-RPA-NOT-FOUND no_match True conclusion 未找到匹配结果"},
        )

    def test_visible_text_does_not_pass_from_agent_output_only(self):
        with self.assertRaises(CaseAssertionError):
            assert_expected_telemetry(
                {"visible_text": ["没有匹配结果"]},
                {"output_text": "conclusion 没有匹配结果", "visible_text": ""},
            )

    def test_console_summary_contains_totals_and_case_rows(self):
        text = render_console_summary(
            [
                {"id": "case_a", "passed": True, "latency_ms": 1200},
                {"id": "case_b", "passed": False, "failure_stage": "assertion", "failure_message": "bad"},
            ]
        )

        self.assertIn("Total: 2", text)
        self.assertIn("Passed: 1", text)
        self.assertIn("case_a", text)
        self.assertIn("PASS", text)
        self.assertIn("case_b", text)
        self.assertIn("FAIL", text)

    def test_summary_includes_phase_pass_rates(self):
        from report import summarize_cases

        summary = summarize_cases(
            [
                {
                    "passed": True,
                    "latency_ms": 100,
                    "phase_results": {
                        "record": {"status": "passed"},
                        "compile": {"status": "passed"},
                        "replay": {"status": "passed"},
                    },
                },
                {
                    "passed": False,
                    "latency_ms": 200,
                    "phase_results": {
                        "record": {"status": "passed"},
                        "compile": {"status": "failed"},
                        "replay": {"status": "skipped"},
                    },
                },
            ]
        )

        self.assertEqual(1.0, summary["record_pass_rate"])
        self.assertEqual(0.5, summary["compile_pass_rate"])
        self.assertEqual(0.5, summary["replay_pass_rate"])

    def test_api_assertion_supports_absent_records(self):
        client = FakeEvalClient({"/api/purchase-orders": [{"number": "PO-2026-RPA-001"}]})

        assert_api_assertions(
            [
                {
                    "name": "new_order_not_present_before_run",
                    "path": "/api/purchase-orders",
                    "find": {"number": "PO-2026-RPA-NEW-001"},
                    "absent": True,
                }
            ],
            client,
            "token",
        )

    def test_api_assertion_supports_item_list_partial_match(self):
        client = FakeEvalClient(
            {
                "/api/purchase-requests": [
                    {
                        "number": "PR-2026-RPA-NEW-001",
                        "items": [
                            {
                                "name": "RPA采购审批机器人许可",
                                "quantity": 5,
                                "unit_price": 6800.0,
                                "cost_center": "PROC-RPA-2026",
                            }
                        ],
                    }
                ]
            }
        )

        assert_api_assertions(
            [
                {
                    "path": "/api/purchase-requests",
                    "find": {"number": "PR-2026-RPA-NEW-001"},
                    "expect": {
                        "items": [
                            {
                                "name": "RPA采购审批机器人许可",
                                "quantity": 5,
                                "unit_price": 6800.0,
                                "cost_center": "PROC-RPA-2026",
                            }
                        ]
                    },
                }
            ],
            client,
            "token",
        )

    @staticmethod
    def _write_xlsx(path: Path, rows: list[list[object]]) -> None:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        workbook.save(path)
        workbook.close()


class FakeEvalClient:
    def __init__(self, responses):
        self.responses = responses

    def get_json(self, path, token):
        return self.responses[path]


class FakeRunnerEvalClient:
    def __init__(self):
        self.actions = []

    def reset(self, reset_token, *, fixture_variant=None):
        suffix = f":{fixture_variant}" if fixture_variant else ""
        self.actions.append(f"reset:{reset_token}{suffix}")
        self.reset_token = reset_token
        self.fixture_variant = fixture_variant

    def login(self, username, password):
        self.actions.append(f"login:{username}")
        self.login_args = (username, password)
        return EvalAppUserSession(username=username, token="token", user={"username": username})

    def get_json(self, path, token):
        self.actions.append(f"get:{path}")
        return []


class FakeRunnerRpaClient:
    def __init__(self):
        self.instructions = []
        self.navigations = []
        self.stopped = []
        self.generates = []
        self.tests = []
        self.chat_events = [{"event": "agent_done", "data": {"message": "ok"}}]
        self.generated_script = "async def execute_skill(page):\n    return {}\n"
        self.test_response = {"status": "success", "result": {"success": True, "data": {}}, "logs": []}
        self.actions = []

    def start_session(self, case_id):
        self.case_id = case_id
        return "session-1"

    def navigate(self, session_id, url):
        self.navigations.append((session_id, url))
        self.current_url = url

    def navigate_and_wait(self, session_id, url, *, timeout_s=10.0):
        self.navigate(session_id, url)

    def get_tabs(self, session_id):
        return {
            "active_tab_id": "tab-1",
            "tabs": [{"id": "tab-1", "url": getattr(self, "current_url", "")}],
        }

    def chat_with_wall_timeout(self, session_id, instruction, *, timeout_s, business_instruction=None):
        self.instructions.append(instruction)
        self.business_instruction = business_instruction
        return list(self.chat_events)

    def get_session(self, session_id):
        return {}

    def generate_script(self, session_id, params=None):
        self.actions.append(f"generate:{session_id}")
        self.generates.append((session_id, params or {}))
        return {"status": "success", "script": self.generated_script}

    def test_script(self, session_id, params=None, timeout_s=None, setup_navigation=None):
        self.actions.append(f"test:{session_id}")
        self.tests.append((session_id, params or {}, list(setup_navigation or [])))
        return self.test_response

    def stop_session(self, session_id, *, ignore_errors=False):
        self.stopped.append((session_id, ignore_errors))


if __name__ == "__main__":
    unittest.main()
