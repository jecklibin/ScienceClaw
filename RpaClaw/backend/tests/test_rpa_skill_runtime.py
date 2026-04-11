import unittest

from backend.rpa.skill_runtime import SkillRuntime


class _StepExecutor:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    async def __call__(self, step, page):
        self.calls.append((step, page))
        return self._results.pop(0)


class _RecoveryAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def recover(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _AgentExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def __call__(self, step, page):
        self.calls.append((step, page))
        return self.result


class SkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_script_failure_triggers_recovery_and_retries_same_step(self):
        step = {"type": "script", "description": "Click submit"}
        page = object()
        script_executor = _StepExecutor(
            [
                {"success": False, "error": "first failure"},
                {"success": True, "step": {"action": "click"}, "output": "ok"},
            ]
        )
        recovery_agent = _RecoveryAgent({"success": True, "step": {"action": "recover"}})
        runtime = SkillRuntime(
            script_executor=script_executor,
            recovery_agent=recovery_agent,
        )

        result = await runtime.run_step(
            step=step,
            page=page,
            existing_steps=[{"description": "Open page"}],
        )

        self.assertTrue(result["success"])
        self.assertEqual(script_executor.calls, [(step, page), (step, page)])
        self.assertEqual(recovery_agent.calls[0]["failing_step"], step)

    async def test_validation_failure_runs_recovery_before_retry(self):
        step = {"type": "script", "description": "Click submit"}
        page = object()
        script_executor = _StepExecutor(
            [
                {"success": True, "step": {"action": "click"}, "output": "ok"},
                {"success": True, "step": {"action": "click"}, "output": "ok"},
            ]
        )
        recovery_agent = _RecoveryAgent({"success": True, "step": {"action": "recover"}})

        async def validate(_step, _result, _page):
            if len(script_executor.calls) == 1:
                return {"success": False, "error": "validation failed"}
            return {"success": True}

        runtime = SkillRuntime(
            script_executor=script_executor,
            validation_hook=validate,
            recovery_agent=recovery_agent,
        )

        result = await runtime.run_step(step=step, page=page, existing_steps=[])

        self.assertTrue(result["success"])
        self.assertEqual(script_executor.calls, [(step, page), (step, page)])
        self.assertEqual(recovery_agent.calls[0]["error"], "validation failed")

    async def test_agent_step_uses_agent_executor_without_recovery(self):
        step = {"type": "agent", "description": "Complete the task"}
        page = object()
        agent_executor = _AgentExecutor({"success": True, "step": {"action": "agent_done"}})
        recovery_agent = _RecoveryAgent({"success": True, "step": {"action": "recover"}})
        runtime = SkillRuntime(
            agent_executor=agent_executor,
            recovery_agent=recovery_agent,
        )

        result = await runtime.run_step(step=step, page=page, existing_steps=[])

        self.assertTrue(result["success"])
        self.assertEqual(agent_executor.calls, [(step, page)])
        self.assertEqual(recovery_agent.calls, [])

    async def test_kind_falls_back_when_type_is_missing(self):
        step = {"kind": "agent", "description": "Complete the task"}
        page = object()
        agent_executor = _AgentExecutor({"success": True, "step": {"action": "agent_done"}})
        runtime = SkillRuntime(agent_executor=agent_executor)

        result = await runtime.run_step(step=step, page=page, existing_steps=[])

        self.assertTrue(result["success"])
        self.assertEqual(agent_executor.calls, [(step, page)])


if __name__ == "__main__":
    unittest.main()
