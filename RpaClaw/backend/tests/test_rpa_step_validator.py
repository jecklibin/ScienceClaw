import unittest

from backend.rpa.step_validator import StepValidator


class StepValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = StepValidator()

    def test_recording_validation_rejects_extract_step_without_output(self):
        candidate = {"step_type": "extract"}
        execution_result = {
            "success": True,
            "output": "",
            "step": {
                "action": "extract_text",
                "result_key": "latest_issue_title",
            },
        }

        validation = self.validator.validate_recording_step(candidate, execution_result)

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["code"], "empty_extract_output")

    def test_recording_validation_accepts_extract_step_with_output(self):
        candidate = {"step_type": "extract"}
        execution_result = {
            "success": True,
            "output": "Issue A",
            "step": {
                "action": "extract_text",
                "result_key": "latest_issue_title",
            },
        }

        validation = self.validator.validate_recording_step(candidate, execution_result)

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["code"], "ok")


if __name__ == "__main__":
    unittest.main()
