import time
from typing import Any

from pydantic import BaseModel, Field


class SessionRuntimeRecord(BaseModel):
    session_id: str
    user_id: str
    namespace: str
    pod_name: str
    service_name: str
    rest_base_url: str
    status: str
    sandbox_id: str | None = None
    route_base_url: str | None = None
    browser_view_url: str | None = None
    runtime_token: str | None = None
    created_at: int = Field(default_factory=lambda: int(time.time()))
    last_used_at: int = Field(default_factory=lambda: int(time.time()))
    expires_at: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
