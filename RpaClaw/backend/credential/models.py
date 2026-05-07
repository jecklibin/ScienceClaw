from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field
import uuid


class Credential(BaseModel):
    id: str = Field(default_factory=lambda: f"cred_{uuid.uuid4().hex[:12]}")
    kind: Literal["basic", "model_auth"] = "basic"
    name: str
    description: str = ""
    username: str = ""
    encrypted_password: str = ""
    domain: str = ""
    model_auth: dict[str, Any] | None = None
    user_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class CredentialCreate(BaseModel):
    kind: Literal["basic", "model_auth"] = "basic"
    name: str
    description: str = ""
    username: str = ""
    password: str = ""  # plaintext, will be encrypted before storage
    domain: str = ""
    model_auth: dict[str, Any] | None = None


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None  # plaintext, empty means no change
    domain: Optional[str] = None
    model_auth: dict[str, Any] | None = None


class CredentialResponse(BaseModel):
    """Response model — never includes password."""
    id: str
    kind: Literal["basic", "model_auth"] = "basic"
    name: str
    description: str = ""
    username: str
    domain: str
    model_auth: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
