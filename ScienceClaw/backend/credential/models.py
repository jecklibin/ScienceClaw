from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class Credential(BaseModel):
    id: str = Field(default_factory=lambda: f"cred_{uuid.uuid4().hex[:12]}")
    name: str
    username: str = ""
    encrypted_password: str = ""
    domain: str = ""
    user_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class CredentialCreate(BaseModel):
    name: str
    username: str = ""
    password: str  # plaintext, will be encrypted before storage
    domain: str = ""


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None  # plaintext, empty means no change
    domain: Optional[str] = None


class CredentialResponse(BaseModel):
    """Response model — never includes password."""
    id: str
    name: str
    username: str
    domain: str
    created_at: datetime
    updated_at: datetime
