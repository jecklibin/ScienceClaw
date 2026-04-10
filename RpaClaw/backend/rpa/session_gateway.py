from backend.config import Settings
from backend.rpa.engine_client import RPAEngineClient
from backend.rpa.engine_models import EngineHealthResponse, EngineModeConfig
from backend.rpa.engine_supervisor import LocalRPAEngineSupervisor


class RPASessionGateway:
    def __init__(
        self,
        settings: Settings,
        client: RPAEngineClient | None = None,
        supervisor: LocalRPAEngineSupervisor | None = None,
    ) -> None:
        self._settings = settings
        self._mode_config = EngineModeConfig(
            mode=settings.rpa_engine_mode,
            base_url=settings.rpa_engine_base_url,
            auth_token=settings.rpa_engine_auth_token,
            host=settings.rpa_engine_host,
            port=settings.rpa_engine_port,
            start_cmd=settings.rpa_engine_start_cmd,
        )
        self._client = client or RPAEngineClient(
            base_url=self._mode_config.base_url,
            auth_token=self._mode_config.auth_token,
        )
        self._supervisor = supervisor

    @property
    def mode_config(self) -> EngineModeConfig:
        return self._mode_config

    async def ensure_engine_ready(self) -> EngineHealthResponse:
        if self._mode_config.mode in {"local", "node"} and self._supervisor is not None:
            await self._supervisor.ensure_running()
        return await self._client.health()

    async def get_session(self, session_id: str) -> dict | None:
        await self.ensure_engine_ready()
        return await self._client.get_session(session_id)

    async def start_session(self, user_id: str, sandbox_session_id: str) -> dict:
        await self.ensure_engine_ready()
        return await self._client.start_session(user_id, sandbox_session_id)

    async def activate_tab(self, session_id: str, page_alias: str) -> dict:
        await self.ensure_engine_ready()
        return await self._client.activate_tab(session_id, page_alias)

    async def navigate_session(self, session_id: str, url: str) -> dict:
        await self.ensure_engine_ready()
        return await self._client.navigate_session(session_id, url)

    async def stop_session(self, session_id: str) -> dict:
        await self.ensure_engine_ready()
        return await self._client.stop_session(session_id)

    async def capture_snapshot(self, session_id: str) -> dict:
        await self.ensure_engine_ready()
        return await self._client.capture_snapshot(session_id)

    async def execute_assistant_intent(self, session_id: str, intent: dict) -> dict:
        await self.ensure_engine_ready()
        return await self._client.execute_assistant_intent(session_id, intent)

    async def generate_script(self, session_id: str, actions: list[dict], params: dict) -> dict:
        await self.ensure_engine_ready()
        return await self._client.generate_script(session_id, actions, params)

    async def replay(self, session_id: str, actions: list[dict], params: dict) -> dict:
        await self.ensure_engine_ready()
        return await self._client.replay(session_id, actions, params)
