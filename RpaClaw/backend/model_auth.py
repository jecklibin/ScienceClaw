from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import httpx
from pydantic import BaseModel, Field

from backend.config import settings
from backend.credential.vault import get_vault
from backend.models import (
    DynamicTokenAuthConfig,
    ModelAuthConfig,
    ModelAuthCredentialRef,
    StaticHeadersAuthConfig,
)


_TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][\w-]*)\.(password|username|domain)\s*}}")
_VARIABLE_TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][\w-]*)\s*}}")
_RESPONSE_FIELD_RE = re.compile(r"{\s*(\$(?:\.[^{}\s]+|\[[^{}\]]+\])*)\s*}")
_CACHE_REFRESH_SKEW = 30
_DEFAULT_TOKEN_TTL_SECONDS = 300


class CredentialValueResolver(Protocol):
    async def resolve_credential_values(self, user_id: str, cred_id: str) -> Mapping[str, str] | None: ...


class ModelAuthResolutionError(ValueError):
    """Raised when model auth cannot be resolved without exposing secrets."""


class ResolvedModelAuth(BaseModel):
    api_key: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)
    default_query: dict[str, str] = Field(default_factory=dict)
    default_body: dict[str, Any] = Field(default_factory=dict)


@dataclass
class _CachedToken:
    response_data: Any
    expires_at: float


class ModelAuthResolver:
    _token_cache: dict[str, _CachedToken] = {}
    _locks: dict[str, asyncio.Lock] = {}

    def __init__(self, vault: CredentialValueResolver | None = None) -> None:
        self._vault = vault

    @classmethod
    def invalidate_cache_for_model(cls, user_id: str | None, config: Mapping[str, Any] | None) -> None:
        """Drop cached dynamic token responses for a user/model pair."""
        effective_user_id = (user_id or str((config or {}).get("user_id") or "")).strip()
        if not effective_user_id:
            return
        model_id = str((config or {}).get("id") or (config or {}).get("_id") or (config or {}).get("model_name") or "")
        prefix = f"{effective_user_id}:{model_id}:"
        for cache_key in list(cls._token_cache.keys()):
            if cache_key.startswith(prefix):
                cls._token_cache.pop(cache_key, None)

    async def resolve(
        self,
        config: Mapping[str, Any] | None,
        user_id: str | None = None,
    ) -> ResolvedModelAuth:
        api_key = str((config or {}).get("api_key") or settings.model_ds_api_key or "") or None
        auth_credential_id = str((config or {}).get("auth_credential_id") or "").strip()
        if auth_credential_id:
            effective_user_id = (user_id or str((config or {}).get("user_id") or "")).strip()
            if not effective_user_id:
                raise ModelAuthResolutionError("模型认证需要用户上下文，请重新选择模型或登录后重试")
            vault = self._vault or get_vault()
            if not hasattr(vault, "resolve_model_auth"):
                raise ModelAuthResolutionError("模型认证凭据解析器不可用，请检查凭据配置")
            model_auth = await vault.resolve_model_auth(effective_user_id, auth_credential_id)  # type: ignore[attr-defined]
            if not model_auth:
                raise ModelAuthResolutionError("模型认证凭据不存在，请重新选择认证配置")
            resolved = await self._resolve_model_auth_profile(
                effective_user_id,
                auth_credential_id,
                config or {},
                model_auth,
            )
            return ResolvedModelAuth(
                api_key=str((config or {}).get("api_key") or "") or "not-needed",
                default_headers=resolved.default_headers,
                default_query=resolved.default_query,
                default_body=resolved.default_body,
            )

        auth_config = self._parse_auth_config((config or {}).get("auth_config"))
        if auth_config is None:
            return ResolvedModelAuth(api_key=api_key, default_headers={}, default_query={})

        effective_user_id = (user_id or str((config or {}).get("user_id") or "")).strip()
        if not effective_user_id:
            raise ModelAuthResolutionError("模型认证需要用户上下文，请重新选择模型或登录后重试")

        if isinstance(auth_config, StaticHeadersAuthConfig):
            resolved = await self._resolve_static_headers(effective_user_id, auth_config)
            return ResolvedModelAuth(
                api_key=api_key,
                default_headers=resolved.default_headers,
                default_query=resolved.default_query,
                default_body=resolved.default_body,
            )

        if isinstance(auth_config, DynamicTokenAuthConfig):
            resolved = await self._resolve_dynamic_token(effective_user_id, config or {}, auth_config)
            return ResolvedModelAuth(
                api_key=api_key,
                default_headers=resolved.default_headers,
                default_query=resolved.default_query,
                default_body=resolved.default_body,
            )

        return ResolvedModelAuth(api_key=api_key, default_headers={}, default_query={})

    def _parse_auth_config(self, raw: Any) -> ModelAuthConfig | None:
        if raw is None:
            return None
        if isinstance(raw, (StaticHeadersAuthConfig, DynamicTokenAuthConfig)):
            return raw
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        if not isinstance(raw, Mapping):
            raise ModelAuthResolutionError("模型认证配置格式不正确，请重新保存模型认证信息")
        auth_type = raw.get("type")
        try:
            if auth_type == "static_headers":
                return StaticHeadersAuthConfig(**dict(raw))
            if auth_type == "dynamic_token":
                return DynamicTokenAuthConfig(**dict(raw))
        except Exception as exc:
            raise ModelAuthResolutionError("模型认证配置格式不正确，请重新保存模型认证信息") from exc
        return None

    async def _resolve_static_headers(
        self,
        user_id: str,
        auth_config: StaticHeadersAuthConfig,
    ) -> ResolvedModelAuth:
        alias_values = await self._load_referenced_alias_values(
            user_id,
            auth_config.credentials,
            [auth_config.headers, auth_config.query],
        )
        return ResolvedModelAuth(
            default_headers=self._render_map(auth_config.headers, alias_values),
            default_query=self._render_map(auth_config.query, alias_values),
        )

    async def _resolve_dynamic_token(
        self,
        user_id: str,
        config: Mapping[str, Any],
        auth_config: DynamicTokenAuthConfig,
    ) -> ResolvedModelAuth:
        cache_key = self._cache_key(user_id, config, auth_config)
        cached = self._token_cache.get(cache_key)
        now = time.time()
        if cached and cached.expires_at - _CACHE_REFRESH_SKEW > now:
            response_data = cached.response_data
        else:
            lock = self._locks.setdefault(cache_key, asyncio.Lock())
            async with lock:
                cached = self._token_cache.get(cache_key)
                now = time.time()
                if cached and cached.expires_at - _CACHE_REFRESH_SKEW > now:
                    response_data = cached.response_data
                else:
                    response_data, expires_at = await self._fetch_dynamic_token(user_id, auth_config)
                    self._token_cache[cache_key] = _CachedToken(
                        response_data=response_data,
                        expires_at=expires_at,
                    )

        return ResolvedModelAuth(
            default_headers=self._render_token_map(auth_config.inject.headers, response_data),
            default_query=self._render_token_map(auth_config.inject.query, response_data),
            default_body=self._render_token_value(auth_config.inject.body, response_data),
        )

    async def _resolve_model_auth_profile(
        self,
        user_id: str,
        auth_credential_id: str,
        config: Mapping[str, Any],
        model_auth: Mapping[str, Any],
    ) -> ResolvedModelAuth:
        auth_type = str(model_auth.get("type") or "").strip()
        auth_config = model_auth.get("config") or {}
        variables = self._profile_variable_values(model_auth.get("variables") or {})
        if auth_type == "static_headers":
            return ResolvedModelAuth(
                default_headers=self._render_profile_map((auth_config or {}).get("headers") or {}, variables),
                default_query=self._render_profile_map((auth_config or {}).get("query") or {}, variables),
                default_body=self._render_profile_value((auth_config or {}).get("body") or {}, variables),
            )
        if auth_type == "dynamic_token":
            cache_key = self._profile_cache_key(user_id, auth_credential_id, config, model_auth)
            cached = self._token_cache.get(cache_key)
            now = time.time()
            if cached and cached.expires_at - _CACHE_REFRESH_SKEW > now:
                response_data = cached.response_data
            else:
                lock = self._locks.setdefault(cache_key, asyncio.Lock())
                async with lock:
                    cached = self._token_cache.get(cache_key)
                    now = time.time()
                    if cached and cached.expires_at - _CACHE_REFRESH_SKEW > now:
                        response_data = cached.response_data
                    else:
                        response_data, expires_at = await self._fetch_dynamic_profile_token(auth_config, variables)
                        self._token_cache[cache_key] = _CachedToken(
                            response_data=response_data,
                            expires_at=expires_at,
                        )
            inject = (auth_config or {}).get("inject") or {}
            return ResolvedModelAuth(
                default_headers=self._render_token_map(inject.get("headers") or {}, response_data),
                default_query=self._render_token_map(inject.get("query") or {}, response_data),
                default_body=self._render_token_value(inject.get("body") or {}, response_data),
            )
        raise ModelAuthResolutionError("不支持的模型认证凭据类型，请重新选择认证配置")

    async def _fetch_dynamic_profile_token(
        self,
        auth_config: Mapping[str, Any],
        variables: Mapping[str, str],
    ) -> tuple[Any, float]:
        token_request = (auth_config or {}).get("token_request") or {}
        url = self._render_profile_template(str(token_request.get("url") or ""), variables)
        headers = self._render_profile_map(token_request.get("headers") or {}, variables)
        query = self._render_profile_map(token_request.get("query") or {}, variables)
        body = self._render_profile_value(token_request.get("body") or {}, variables)
        method = str(token_request.get("method") or "POST").upper()
        body_type = str(token_request.get("body_type") or "json")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers, params=query)
                elif body_type == "form":
                    response = await client.request(method, url, headers=headers, params=query, data=body or None)
                elif body_type == "raw":
                    response = await client.request(method, url, headers=headers, params=query, content=str(body or ""))
                else:
                    response = await client.request(method, url, headers=headers, params=query, json=body or None)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise ModelAuthResolutionError("获取动态 Token 失败，请检查 Token URL、网络和凭据") from exc

        expires_at = time.time() + _DEFAULT_TOKEN_TTL_SECONDS
        return data, expires_at

    async def _fetch_dynamic_token(
        self,
        user_id: str,
        auth_config: DynamicTokenAuthConfig,
    ) -> tuple[Any, float]:
        token_request = auth_config.token_request
        alias_values = await self._load_referenced_alias_values(
            user_id,
            auth_config.credentials,
            [token_request.url, token_request.headers, token_request.query, token_request.body],
        )
        url = self._render_template(token_request.url, alias_values)
        headers = self._render_map(token_request.headers, alias_values)
        query = self._render_map(token_request.query, alias_values)
        body = self._render_value(token_request.body, alias_values)

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if token_request.method == "GET":
                    response = await client.get(url, headers=headers, params=query)
                elif token_request.body_type == "form":
                    response = await client.request(token_request.method, url, headers=headers, params=query, data=body or None)
                elif token_request.body_type == "raw":
                    response = await client.request(token_request.method, url, headers=headers, params=query, content=str(body or ""))
                else:
                    response = await client.request(token_request.method, url, headers=headers, params=query, json=body or None)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise ModelAuthResolutionError("获取动态 Token 失败，请检查 Token URL、网络和凭据") from exc

        expires_at = time.time() + _DEFAULT_TOKEN_TTL_SECONDS
        return data, expires_at

    async def _load_referenced_alias_values(
        self,
        user_id: str,
        credentials: list[ModelAuthCredentialRef],
        template_sources: list[Any],
    ) -> dict[str, Mapping[str, str]]:
        alias_to_credential_id = {
            item.alias.strip(): item.credential_id.strip()
            for item in credentials
            if item.alias.strip() and item.credential_id.strip()
        }
        referenced_aliases = self._referenced_aliases(template_sources)
        unknown_aliases = [alias for alias in referenced_aliases if alias not in alias_to_credential_id]
        if unknown_aliases:
            raise ModelAuthResolutionError("模型认证配置引用了未知凭据，请重新配置认证信息")

        values: dict[str, Mapping[str, str]] = {}
        vault = self._vault or get_vault()
        for alias in referenced_aliases:
            credential_values = await vault.resolve_credential_values(user_id, alias_to_credential_id[alias])
            if credential_values is None:
                raise ModelAuthResolutionError("模型认证引用的凭据不存在，请重新配置认证信息")
            values[alias] = credential_values
        return values

    def _referenced_aliases(self, values: list[Any]) -> list[str]:
        aliases: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                for nested in value.values():
                    visit(nested)
                return
            if isinstance(value, list):
                for nested in value:
                    visit(nested)
                return
            for match in _TEMPLATE_RE.finditer(str(value)):
                alias = match.group(1)
                if alias not in aliases:
                    aliases.append(alias)

        for value in values:
            visit(value)
        return aliases

    def _render_map(
        self,
        values: Mapping[str, Any],
        alias_values: Mapping[str, Mapping[str, str]],
    ) -> dict[str, str]:
        return {
            str(key): str(self._render_value(value, alias_values))
            for key, value in values.items()
            if str(key).strip()
        }

    def _render_value(self, value: Any, alias_values: Mapping[str, Mapping[str, str]]) -> Any:
        if isinstance(value, Mapping):
            return {str(k): self._render_value(v, alias_values) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render_value(v, alias_values) for v in value]
        return self._render_template(str(value), alias_values)

    def _render_template(self, value: str, alias_values: Mapping[str, Mapping[str, str]]) -> str:
        def replace(match: re.Match[str]) -> str:
            alias = match.group(1)
            field_name = match.group(2)
            credential_values = alias_values.get(alias)
            if credential_values is None:
                raise ModelAuthResolutionError("模型认证模板引用了未知凭据，请重新配置认证信息")
            return str(credential_values.get(field_name) or "")

        return _TEMPLATE_RE.sub(replace, value)

    def _profile_variable_values(self, variables: Mapping[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, raw in variables.items():
            if not str(name).strip():
                continue
            if isinstance(raw, Mapping):
                result[str(name)] = str(raw.get("value") or "")
            else:
                result[str(name)] = str(raw or "")
        return result

    def _render_profile_map(self, values: Mapping[str, Any], variables: Mapping[str, str]) -> dict[str, str]:
        return {
            str(key): str(self._render_profile_value(value, variables))
            for key, value in values.items()
            if str(key).strip()
        }

    def _render_profile_value(self, value: Any, variables: Mapping[str, str]) -> Any:
        if isinstance(value, Mapping):
            return {str(k): self._render_profile_value(v, variables) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render_profile_value(v, variables) for v in value]
        return self._render_profile_template(str(value), variables)

    def _render_profile_template(self, value: str, variables: Mapping[str, str]) -> str:
        def replace(match: re.Match[str]) -> str:
            return str(variables.get(match.group(1)) or "")

        return _VARIABLE_TEMPLATE_RE.sub(replace, value)

    def _render_token_map(self, values: Mapping[str, Any], response_data: Any) -> dict[str, str]:
        return {
            str(key): str(self._render_token_value(value, response_data))
            for key, value in values.items()
            if str(key).strip()
        }

    def _render_token_value(self, value: Any, response_data: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(k): self._render_token_value(v, response_data) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render_token_value(v, response_data) for v in value]
        rendered = str(value)

        def replace_field(match: re.Match[str]) -> str:
            field_value = self._extract_path(response_data, match.group(1))
            if field_value is None:
                return ""
            if isinstance(field_value, (Mapping, list)):
                return json.dumps(field_value, ensure_ascii=False, separators=(",", ":"))
            return str(field_value)

        return _RESPONSE_FIELD_RE.sub(replace_field, rendered)

    def _extract_path(self, data: Any, path: str) -> Any:
        normalized = (path or "").strip()
        if normalized.startswith("$."):
            normalized = normalized[2:]
        elif normalized.startswith("$"):
            normalized = normalized[1:].lstrip(".")
        if not normalized:
            return data
        current = data
        for part in self._path_parts(normalized):
            if isinstance(current, Mapping):
                current = current.get(part)
            elif isinstance(current, list) and isinstance(part, int):
                if part < 0 or part >= len(current):
                    return None
                current = current[part]
            else:
                return None
        return current

    def _path_parts(self, path: str) -> list[str | int]:
        parts: list[str | int] = []
        for chunk in path.split("."):
            if not chunk:
                continue
            pos = 0
            name = ""
            while pos < len(chunk):
                char = chunk[pos]
                if char == "[":
                    if name:
                        parts.append(name)
                        name = ""
                    end = chunk.find("]", pos)
                    if end == -1:
                        parts.append(chunk[pos:])
                        break
                    index_text = chunk[pos + 1:end].strip()
                    if (index_text.startswith("'") and index_text.endswith("'")) or (
                        index_text.startswith('"') and index_text.endswith('"')
                    ):
                        parts.append(index_text[1:-1])
                    else:
                        try:
                            parts.append(int(index_text))
                        except ValueError:
                            parts.append(index_text)
                    pos = end + 1
                    continue
                name += char
                pos += 1
            if name:
                parts.append(name)
        return parts

    def _cache_key(
        self,
        user_id: str,
        config: Mapping[str, Any],
        auth_config: DynamicTokenAuthConfig,
    ) -> str:
        model_id = str(config.get("id") or config.get("_id") or config.get("model_name") or "")
        payload = auth_config.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return f"{user_id}:{model_id}:{digest}"

    def _profile_cache_key(
        self,
        user_id: str,
        auth_credential_id: str,
        config: Mapping[str, Any],
        model_auth: Mapping[str, Any],
    ) -> str:
        model_id = str(config.get("id") or config.get("_id") or config.get("model_name") or "")
        digest = hashlib.sha256(json.dumps(model_auth, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return f"{user_id}:{model_id}:{auth_credential_id}:{digest}"
