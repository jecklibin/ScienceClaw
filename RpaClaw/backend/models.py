from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime
import uuid
import time
from loguru import logger

from backend.storage import get_repository
from backend.config import settings

class ModelConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., description="Display Name")
    provider: str = Field(..., description="openai, anthropic, etc.")
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: str = Field(..., description="Actual model name e.g. gpt-4o")
    context_window: Optional[int] = Field(
        default=None,
        description="Model context window in tokens. Auto-detected from model_name if not set.",
    )
    is_system: bool = False
    user_id: Optional[str] = None
    is_active: bool = True
    created_at: int = Field(default_factory=lambda: int(time.time()))
    updated_at: int = Field(default_factory=lambda: int(time.time()))

class CreateModelRequest(BaseModel):
    name: str
    provider: str = "openai"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: str
    context_window: Optional[int] = Field(
        default=None,
        ge=1024, le=10_000_000,
        description="Model context window in tokens. Leave empty for auto-detection.",
    )

class UpdateModelRequest(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    context_window: Optional[int] = Field(
        default=None,
        ge=1024, le=10_000_000,
        description="Model context window in tokens. Leave empty for auto-detection.",
    )
    is_active: Optional[bool] = None

async def init_system_models():
    """
    Initialize system models from environment variables or settings.
    Only creates system model when DS_API_KEY is configured;
    otherwise cleans up any existing system model with empty key.
    """
    now = int(time.time())
    repo = get_repository("models")

    await repo.delete_one({"_id": "system-qwen", "is_system": True})

    if not settings.model_ds_api_key:
        await repo.delete_one({"_id": "system-default", "is_system": True})
        logger.info("DS_API_KEY not set, skipping system model creation")
        return

    system_definitions = [
        {
            "_id": "system-default",
            "name": "DeepSeek V3.2",
            "provider": "deepseek",
            "base_url": settings.model_ds_base_url,
            "api_key": settings.model_ds_api_key,
            "model_name": settings.model_ds_name,
            "context_window": settings.context_window,
            "is_system": True,
            "is_active": True,
        }
    ]

    for doc in system_definitions:
        existing = await repo.find_one({"_id": doc["_id"]})
        doc = {**doc, "updated_at": now}
        if not existing:
            doc["created_at"] = now
            await repo.insert_one(doc)
        else:
            await repo.update_one({"_id": doc["_id"]}, {"$set": doc})

async def get_model_config(model_id: str) -> Optional[ModelConfig]:
    repo = get_repository("models")
    doc = await repo.find_one({"_id": model_id})
    if not doc:
        return None
    # Remap _id to id
    doc["id"] = doc["_id"]
    return ModelConfig(**doc)


async def resolve_default_model_config(user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve the app-visible default model config.

    User-created active models win over system/env-derived models. DS env remains
    a legacy fallback through system-default or get_llm_model(config=None), but
    it should not outrank a model configured from the product UI.
    """
    repo = get_repository("models")
    filter_doc: Dict[str, Any] = {
        "is_active": True,
        "api_key": {"$nin": ["", None]},
    }
    if user_id:
        filter_doc["$or"] = [{"user_id": user_id}, {"is_system": True}]
    else:
        filter_doc["is_system"] = True
    docs = await repo.find_many(
        filter_doc,
        sort=[("is_system", 1), ("updated_at", -1), ("created_at", -1)],
        limit=1,
    )
    doc = docs[0] if docs else None
    if not doc:
        return None
    return {
        "id": doc.get("_id") or doc.get("id"),
        "provider": doc.get("provider") or "",
        "model_name": doc.get("model_name") or "",
        "base_url": doc.get("base_url"),
        "api_key": doc.get("api_key"),
        "context_window": doc.get("context_window"),
        "is_system": bool(doc.get("is_system", False)),
        "user_id": doc.get("user_id"),
        "requested_user_id": user_id,
        "selected_owner": "system" if bool(doc.get("is_system", False)) else "user",
        "resolution_reason": "system_fallback" if bool(doc.get("is_system", False)) else "user_active_model",
    }


async def list_user_models(user_id: str) -> List[ModelConfig]:
    # Return System models + User models
    repo = get_repository("models")
    docs = await repo.find_many(
        {"$or": [{"is_system": True}, {"user_id": user_id}]},
        sort=[("created_at", -1)],
    )
    models = []
    for doc in docs:
        doc["id"] = doc["_id"]
        models.append(ModelConfig(**doc))
    return models
