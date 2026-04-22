from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.config import settings
from backend.deepagent.engine import get_llm_model
from backend.storage import get_repository


_SENSITIVE_PARAM_RE = re.compile(
    r"password|passcode|token|secret|cookie|cookies|credential|验证码|动态码|短信码|密码|口令|账号|帐号|账户|用户名|邮箱|手机号|手机",
    re.IGNORECASE,
)


@dataclass
class RpaMcpSemanticRecommendation:
    source: str
    tool_name: str
    display_name: str
    description: str
    input_schema: dict[str, Any]
    params: dict[str, Any]
    confidence: float | None = None
    warnings: list[str] = field(default_factory=list)
    model: str = ""


class RpaMcpSemanticInferer:
    def __init__(self, model_client: Any | None = None) -> None:
        self._model_client = model_client

    async def infer(
        self,
        *,
        user_id: str = "",
        requested_name: str,
        requested_description: str,
        steps: list[dict[str, Any]],
        removed_step_details: list[dict[str, Any]],
        fallback_params: dict[str, Any],
    ) -> RpaMcpSemanticRecommendation:
        model_client, model_name = await self._resolve_model_client(user_id)
        if model_client is None:
            return self._fallback(
                requested_name,
                requested_description,
                fallback_params,
                ["Semantic inference model is not configured."],
            )

        context = self._build_context(steps, removed_step_details)
        try:
            response = await asyncio.wait_for(
                model_client.ainvoke(self._messages(requested_name, requested_description, context, fallback_params)),
                timeout=max(1, int(settings.rpa_mcp_semantic_timeout_seconds)),
            )
            payload = self._parse_json(str(getattr(response, "content", "") or ""))
            return self._validate_payload(payload, requested_name, requested_description, fallback_params, model_name)
        except Exception as exc:
            return self._fallback(
                requested_name,
                requested_description,
                fallback_params,
                [f"Semantic inference failed: {exc.__class__.__name__}"],
            )

    async def _resolve_model_client(self, user_id: str) -> tuple[Any | None, str]:
        if self._model_client is not None:
            return self._model_client, "injected"
        if not settings.rpa_mcp_semantic_inference:
            return None, ""
        if settings.model_ds_api_key:
            return get_llm_model(config=None, max_tokens_override=2000, streaming=False), settings.model_ds_name
        model_config = await self._resolve_configured_model(user_id)
        if not model_config:
            return None, ""
        return get_llm_model(config=model_config, max_tokens_override=2000, streaming=False), str(model_config.get("model_name") or "")

    async def _resolve_configured_model(self, user_id: str) -> dict[str, Any] | None:
        filter_doc: dict[str, Any] = {
            "is_active": True,
            "api_key": {"$nin": ["", None]},
        }
        if user_id:
            filter_doc["$or"] = [{"is_system": True}, {"user_id": user_id}]
        docs = await get_repository("models").find_many(
            filter_doc,
            sort=[("created_at", -1)],
            limit=1,
        )
        doc = docs[0] if docs else None
        if not doc:
            return None
        return {
            "provider": doc.get("provider") or "",
            "model_name": doc.get("model_name") or "",
            "base_url": doc.get("base_url"),
            "api_key": doc.get("api_key"),
            "context_window": doc.get("context_window"),
        }

    def _messages(self, requested_name: str, requested_description: str, context: dict[str, Any], fallback_params: dict[str, Any]) -> list[Any]:
        return [
            SystemMessage(
                content=(
                    "You infer MCP tool metadata from sanitized RPA browser steps. "
                    "Return strict JSON only. Never add login, account, password, cookie, token, or credential parameters. "
                    "Only recommend names, descriptions, types, and required flags for the provided candidate_params keys. "
                    "Do not invent new parameters."
                )
            ),
            HumanMessage(
                content=json.dumps(
                    {
                        "requested_name": requested_name,
                        "requested_description": requested_description,
                        "sanitized_context": context,
                        "candidate_params": self._candidate_param_context(fallback_params),
                        "required_json_shape": {
                            "tool": {
                                "tool_name": "snake_case",
                                "display_name": "Human title",
                                "description": "What the tool does",
                            },
                            "param_recommendations": {
                                "candidate_param_key": {
                                    "name": "snake_case",
                                    "description": "MCP caller-facing description",
                                    "type": "string",
                                    "required": False,
                                    "confidence": 0.8,
                                }
                            },
                            "warnings": [],
                        },
                    },
                    ensure_ascii=False,
                )
            ),
        ]

    def _candidate_param_context(self, fallback_params: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = []
        for key, info in fallback_params.items():
            info_dict = dict(info or {})
            candidates.append(
                {
                    "key": key,
                    "description": info_dict.get("description", ""),
                    "type": info_dict.get("type", "string"),
                    "example_value": "" if info_dict.get("sensitive") else info_dict.get("original_value", ""),
                    "source_step_index": info_dict.get("source_step_index"),
                    "source_step_id": info_dict.get("source_step_id"),
                }
            )
        return candidates

    def _build_context(self, steps: list[dict[str, Any]], removed_step_details: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "steps": [self._step_context(index, step) for index, step in enumerate(steps)],
            "removed_login_steps": [
                {
                    "index": detail.get("index"),
                    "action": detail.get("action"),
                    "description": detail.get("description"),
                    "url": detail.get("url"),
                }
                for detail in removed_step_details
            ],
        }

    def _step_context(self, index: int, step: dict[str, Any]) -> dict[str, Any]:
        description = str(step.get("description") or "")
        value = "" if _SENSITIVE_PARAM_RE.search(description) else step.get("value", "")
        return {
            "index": index,
            "action": step.get("action"),
            "description": description,
            "url": step.get("url"),
            "target": self._safe_target(step.get("target")),
            "example_value": value,
        }

    def _safe_target(self, target: Any) -> Any:
        if not isinstance(target, str):
            return target
        try:
            parsed = json.loads(target)
        except json.JSONDecodeError:
            return target[:300]
        allowed = {}
        for key in ("method", "role", "name", "value", "placeholder", "label", "text", "title", "alt"):
            if key in parsed:
                allowed[key] = parsed[key]
        return allowed

    def _parse_json(self, content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            stripped = re.sub(r"^json\s*", "", stripped.strip(), flags=re.IGNORECASE)
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise ValueError("No JSON object found")
        return json.loads(stripped[start:end + 1])

    def _validate_payload(
        self,
        payload: dict[str, Any],
        requested_name: str,
        requested_description: str,
        fallback_params: dict[str, Any],
        model_name: str,
    ) -> RpaMcpSemanticRecommendation:
        tool = payload.get("tool") or {}
        tool_name = self._snake_case(str(tool.get("tool_name") or requested_name or "rpa_tool"))
        display_name = str(tool.get("display_name") or requested_name or tool_name)
        description = str(tool.get("description") or requested_description or "")
        params = payload.get("param_recommendations") if isinstance(payload.get("param_recommendations"), dict) else {}
        if not params and isinstance(payload.get("params"), dict):
            params = payload.get("params") or {}
        warnings = [str(item) for item in payload.get("warnings", []) if str(item).strip()]

        clean_params = {}
        clean_properties = {}
        clean_required = []

        for raw_key in params.keys():
            if raw_key not in fallback_params:
                warnings.append(f"Dropped unknown parameter recommendation: {raw_key}")

        used_names: set[str] = set()
        for candidate_key, fallback_info in fallback_params.items():
            fallback_info_dict = dict(fallback_info or {})
            info_dict = dict(params.get(candidate_key) or {})
            recommended_name = self._snake_case(str(info_dict.get("name") or candidate_key))
            if not recommended_name or _SENSITIVE_PARAM_RE.search(recommended_name):
                warnings.append(f"Dropped sensitive parameter recommendation: {candidate_key}")
                recommended_name = self._snake_case(str(candidate_key))
            if _SENSITIVE_PARAM_RE.search(str(info_dict.get("description") or "")):
                warnings.append(f"Dropped sensitive parameter description recommendation: {candidate_key}")
                description = str(fallback_info_dict.get("description") or recommended_name)
            else:
                description = str(info_dict.get("description") or fallback_info_dict.get("description") or recommended_name)
            name = self._unique_name(recommended_name, used_names)
            if not name:
                continue
            used_names.add(name)
            param_type = self._safe_param_type(str(info_dict.get("type") or fallback_info_dict.get("type") or "string"))
            prop = {"type": param_type, "description": description}
            if fallback_info_dict.get("original_value") not in (None, "") and not fallback_info_dict.get("sensitive"):
                prop["default"] = fallback_info_dict.get("original_value")
            clean_properties[name] = prop
            clean_params[name] = fallback_info_dict
            clean_params[name]["source_param"] = candidate_key
            clean_params[name]["description"] = description
            clean_params[name]["type"] = param_type
            clean_params[name]["required"] = bool(info_dict.get("required", fallback_info_dict.get("required", False)))
            if info_dict.get("confidence") is not None:
                clean_params[name]["confidence"] = info_dict.get("confidence")
            if clean_params[name].get("required"):
                clean_required.append(name)

        return RpaMcpSemanticRecommendation(
            source="ai_inferred",
            tool_name=tool_name,
            display_name=display_name,
            description=description,
            input_schema={"type": "object", "properties": clean_properties, "required": clean_required},
            params=clean_params,
            confidence=self._average_confidence(clean_params),
            warnings=warnings,
            model=model_name,
        )

    def _fallback(
        self,
        requested_name: str,
        requested_description: str,
        fallback_params: dict[str, Any],
        warnings: list[str],
    ) -> RpaMcpSemanticRecommendation:
        properties = {}
        required = []
        for key, info in fallback_params.items():
            info_dict = dict(info or {})
            properties[key] = {
                "type": info_dict.get("type", "string"),
                "description": info_dict.get("description", ""),
            }
            if info_dict.get("original_value") not in (None, ""):
                properties[key]["default"] = info_dict.get("original_value")
            if info_dict.get("required"):
                required.append(key)
        return RpaMcpSemanticRecommendation(
            source="rule_inferred",
            tool_name=self._snake_case(requested_name or "rpa_tool"),
            display_name=requested_name or "RPA tool",
            description=requested_description or "",
            input_schema={"type": "object", "properties": properties, "required": required},
            params={key: dict(value or {}) for key, value in fallback_params.items()},
            warnings=warnings,
            model="",
        )

    def _average_confidence(self, params: dict[str, Any]) -> float | None:
        values = [
            float(info.get("confidence"))
            for info in params.values()
            if isinstance(info, dict) and info.get("confidence") is not None
        ]
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    def _snake_case(self, value: str) -> str:
        normalized = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower()).strip("_")
        return normalized or "rpa_tool"

    def _unique_name(self, base_name: str, used_names: set[str]) -> str:
        name = base_name or "param"
        if name not in used_names:
            return name
        suffix = 2
        while f"{name}_{suffix}" in used_names:
            suffix += 1
        return f"{name}_{suffix}"

    def _safe_param_type(self, value: str) -> str:
        return value if value in {"string", "number", "integer", "boolean", "array", "object"} else "string"
