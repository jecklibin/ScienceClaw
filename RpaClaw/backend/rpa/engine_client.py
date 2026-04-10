import httpx

from backend.rpa.engine_models import (
    EngineAssistantExecuteRequest,
    EngineAssistantExecuteResponse,
    EngineAssistantSnapshotResponse,
    EngineActivateTabRequest,
    EngineCodegenResponse,
    EngineHealthResponse,
    EngineNavigateRequest,
    EngineReplayRequest,
    EngineReplayResponse,
    EngineStartSessionRequest,
    EngineSessionEnvelope,
)


class RPAEngineClient:
    def __init__(self, base_url: str, auth_token: str):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}

    async def health(self) -> EngineHealthResponse:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/health", headers=self._headers)
        except httpx.HTTPError as exc:
            raise RuntimeError("rpa engine health check failed") from exc
        if response.status_code != 200:
            raise RuntimeError("rpa engine health check failed")
        return EngineHealthResponse.model_validate(response.json())

    async def get_session(self, session_id: str) -> dict | None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{self._base_url}/sessions/{session_id}",
                headers=self._headers,
            )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def start_session(self, user_id: str, sandbox_session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions",
                headers=self._headers,
                json=EngineStartSessionRequest(
                    userId=user_id,
                    sandboxSessionId=sandbox_session_id,
                ).model_dump(),
            )
        if response.status_code not in {200, 201}:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def activate_tab(self, session_id: str, page_alias: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/activate",
                headers=self._headers,
                json=EngineActivateTabRequest(pageAlias=page_alias).model_dump(),
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def navigate_session(self, session_id: str, url: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/navigate",
                headers=self._headers,
                json=EngineNavigateRequest(url=url).model_dump(exclude_none=True),
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def stop_session(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/stop",
                headers=self._headers,
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineSessionEnvelope.model_validate(response.json()).model_dump()

    async def capture_snapshot(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self._base_url}/sessions/{session_id}/assistant/snapshot",
                headers=self._headers,
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineAssistantSnapshotResponse.model_validate(response.json()).model_dump()

    async def execute_assistant_intent(self, session_id: str, intent: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/assistant/execute",
                headers=self._headers,
                json=EngineAssistantExecuteRequest(intent=intent).model_dump(),
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineAssistantExecuteResponse.model_validate(response.json()).model_dump()

    async def generate_script(self, session_id: str, actions: list[dict], params: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/codegen",
                headers=self._headers,
                json=EngineReplayRequest(actions=actions, params=params).model_dump(),
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineCodegenResponse.model_validate(response.json()).model_dump()

    async def replay(self, session_id: str, actions: list[dict], params: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}/replay",
                headers=self._headers,
                json=EngineReplayRequest(actions=actions, params=params).model_dump(),
            )
        if response.status_code != 200:
            raise RuntimeError("rpa engine session request failed")
        return EngineReplayResponse.model_validate(response.json()).model_dump()
