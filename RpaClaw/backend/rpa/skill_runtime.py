from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from .recovery_agent import RecoveryAgent


RuntimeCallable = Callable[..., Awaitable[Dict[str, Any]]]


class SkillRuntime:
    """Minimal runtime state machine for exported skill steps."""

    def __init__(
        self,
        script_executor: Optional[RuntimeCallable] = None,
        validation_hook: Optional[RuntimeCallable] = None,
        recovery_agent: Optional[RecoveryAgent] = None,
        agent_executor: Optional[RuntimeCallable] = None,
    ):
        self._script_executor = script_executor
        self._validation_hook = validation_hook
        self._recovery_agent = recovery_agent
        self._agent_executor = agent_executor

    async def run_step(
        self,
        step: Dict[str, Any],
        page: Any,
        existing_steps: List[Dict[str, Any]],
        session_id: str = "skill-runtime",
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Any]]] = None,
    ) -> Dict[str, Any]:
        step_type = str(step.get("type") or step.get("kind") or "script").lower()
        if step_type == "agent":
            if not self._agent_executor:
                raise ValueError("agent_executor is required for agent steps")
            return await self._agent_executor(step, page)

        if not self._script_executor:
            raise ValueError("script_executor is required for script steps")

        first_result = await self._script_executor(step, page)
        first_error = self._get_failure_error(first_result)
        if first_error:
            return await self._recover_and_retry(
                step=step,
                page=page,
                existing_steps=existing_steps,
                session_id=session_id,
                model_config=model_config,
                page_provider=page_provider,
                error=first_error,
            )

        validation_error = await self._validate(step=step, result=first_result, page=page)
        if validation_error:
            return await self._recover_and_retry(
                step=step,
                page=page,
                existing_steps=existing_steps,
                session_id=session_id,
                model_config=model_config,
                page_provider=page_provider,
                error=validation_error,
            )

        return first_result

    async def _recover_and_retry(
        self,
        step: Dict[str, Any],
        page: Any,
        existing_steps: List[Dict[str, Any]],
        session_id: str,
        model_config: Optional[Dict[str, Any]],
        page_provider: Optional[Callable[[], Optional[Any]]],
        error: str,
    ) -> Dict[str, Any]:
        if not self._recovery_agent:
            return {"success": False, "error": error}

        recovery_result = await self._recovery_agent.recover(
            session_id=session_id,
            page=page,
            failing_step=step,
            error=error,
            existing_steps=existing_steps,
            model_config=model_config,
            page_provider=page_provider,
        )
        if not recovery_result.get("success"):
            return {
                "success": False,
                "error": recovery_result.get("error", error),
                "recovery": recovery_result,
            }

        retry_result = await self._script_executor(step, page)
        retry_error = self._get_failure_error(retry_result)
        if retry_error:
            retry_result.setdefault("recovery", recovery_result)
            return retry_result

        validation_error = await self._validate(step=step, result=retry_result, page=page)
        if validation_error:
            return {
                "success": False,
                "error": validation_error,
                "recovery": recovery_result,
            }

        retry_result.setdefault("recovery", recovery_result)
        return retry_result

    async def _validate(self, step: Dict[str, Any], result: Dict[str, Any], page: Any) -> Optional[str]:
        if not self._validation_hook:
            return None
        validation_result = await self._validation_hook(step, result, page)
        if validation_result.get("success", False):
            return None
        return str(validation_result.get("error") or "runtime validation failed")

    @staticmethod
    def _get_failure_error(result: Dict[str, Any]) -> Optional[str]:
        if result.get("success"):
            return None
        return str(result.get("error") or "step execution failed")
