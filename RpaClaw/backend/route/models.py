from typing import Any, Mapping
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
import re
import time
import uuid
from langchain_core.messages import HumanMessage

from backend.user.dependencies import get_current_user, require_user, User
from backend.storage import get_repository
from backend.credential.models import CredentialCreate
from backend.credential.vault import get_vault
from backend.model_auth import ModelAuthResolutionError
from backend.models import (
    CreateModelRequest,
    DynamicTokenAuthConfig,
    DynamicTokenTestRequest,
    ModelConfig,
    ModelAuthSaveRequest,
    StaticHeadersAuthConfig,
    UpdateModelRequest,
    list_user_models,
)

router = APIRouter(prefix="/models", tags=["models"])
from loguru import logger

class ApiResponse(BaseModel):
    code: int = Field(default=0)
    msg: str = Field(default="ok")
    data: Any = Field(default=None)

_HEADER_ALIAS_RE = re.compile(r"[^a-z0-9_]+")
_TEMPLATE_ALIAS_RE = re.compile(r"{{\s*([A-Za-z_][\w-]*)\.(password|username|domain)\s*}}")
_AUTH_ALIAS_NAME_RE = re.compile(r"^[A-Za-z_][\w-]*$")
_SENSITIVE_TOKEN_REQUEST_KEY_RE = re.compile(
    r"(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|secret|password|passwd|pwd)",
    re.IGNORECASE,
)


def _redact_secrets(value: str) -> str:
    redacted = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|((?:api[_-]?key|token|secret|authorization)[\"'\s:=]+)[^,\s\"']+",
        lambda m: (m.group(1) + "********") if m.group(1) else (m.group(2) + "********"),
        value,
    )
    return re.sub(r"\bsk-[A-Za-z0-9._-]+", "sk-********", redacted)


async def verify_model_connection(
    provider: str,
    base_url: str | None,
    api_key: str | None,
    model_name: str,
    *,
    user_id: str | None = None,
    auth_config: Mapping[str, Any] | None = None,
    auth_credential_id: str | None = None,
):
    """
    Verify model availability by making a simple request.
    """
    logger.info(f"[verify_model] provider={provider}, model_name={model_name}, base_url={base_url}, has_api_key={bool(api_key)}")
    try:
        if provider == "gemini" and not api_key:
            raise ValueError("API Key is required for verification")

        if provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI
            logger.info("[verify_model] Using ChatGoogleGenerativeAI for Gemini")
            chat = ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key,
                max_output_tokens=5,
                timeout=10,
            )
        else:
            from backend.deepagent.engine import get_llm_model_for_user

            logger.info(f"[verify_model] Using ChatOpenAI, base_url={base_url or '(default)'}")
            chat = await get_llm_model_for_user(
                config={
                    "provider": provider,
                    "base_url": base_url,
                    "api_key": api_key,
                    "model_name": model_name,
                    "auth_config": auth_config,
                    "auth_credential_id": auth_credential_id,
                    "user_id": user_id,
                },
                user_id=user_id,
                max_tokens_override=5,
                streaming=False,
            )
        
        logger.info("[verify_model] Sending test message...")
        await chat.ainvoke([HumanMessage(content="Hi")])
        logger.info("[verify_model] Verification succeeded")
        return True
    except HTTPException:
        raise
    except ModelAuthResolutionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[verify_model] Verification failed: {type(e).__name__}: {_redact_secrets(str(e))}")
        detail = _extract_api_error(e)
        raise HTTPException(status_code=400, detail=detail)


def _extract_api_error(e: Exception) -> str:
    """Extract the original API error message from provider SDK exceptions."""
    import json as _json

    # openai SDK errors (NotFoundError, AuthenticationError, etc.) have a `body` dict
    body = getattr(e, 'body', None)
    if isinstance(body, dict):
        err_obj = body.get('error', body)
        if isinstance(err_obj, dict):
            msg = err_obj.get('message') or err_obj.get('msg')
            err_type = err_obj.get('type', '')
            if msg:
                return f"{msg} ({err_type})" if err_type else str(msg)

    # Some SDKs attach a `response` object with the raw HTTP body
    resp = getattr(e, 'response', None)
    if resp is not None:
        try:
            text = resp.text if hasattr(resp, 'text') else str(resp)
            data = _json.loads(text)
            err_obj = data.get('error', data)
            if isinstance(err_obj, dict):
                msg = err_obj.get('message') or err_obj.get('msg')
                if msg:
                    return str(msg)
        except Exception:
            pass

    return _redact_secrets(str(e))


def _test_alias_values(credentials: list[Any]) -> dict[str, Mapping[str, str]]:
    values: dict[str, Mapping[str, str]] = {}
    for item in credentials:
        alias = str(item.alias or "").strip()
        if not alias:
            continue
        values[alias] = {
            "username": item.username or "",
            "password": item.password or "",
            "domain": item.domain or "",
        }
    return values


def _render_test_template(value: str, alias_values: Mapping[str, Mapping[str, str]]) -> str:
    def replace(match: re.Match[str]) -> str:
        alias, field = match.group(1), match.group(2)
        return str((alias_values.get(alias) or {}).get(field) or "")

    return _TEMPLATE_ALIAS_RE.sub(replace, value)


def _render_test_value(value: Any, alias_values: Mapping[str, Mapping[str, str]]) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _render_test_value(nested, alias_values) for key, nested in value.items()}
    if isinstance(value, list):
        return [_render_test_value(item, alias_values) for item in value]
    return _render_test_template(str(value), alias_values)


def _render_test_map(values: Mapping[str, Any], alias_values: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    return {
        str(key): str(_render_test_value(value, alias_values))
        for key, value in values.items()
        if str(key).strip()
    }


def _flatten_response_fields(value: Any) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []

    def visit(current: Any, path: str) -> None:
        if isinstance(current, Mapping):
            for key, nested in current.items():
                child_path = f"{path}.{key}" if path else f"$.{key}"
                visit(nested, child_path)
            return
        if isinstance(current, list):
            for index, nested in enumerate(current):
                visit(nested, f"{path}[{index}]")
            return
        fields.append({"path": path or "$", "value": current, "type": type(current).__name__})

    visit(value, "$")
    return fields


def _auth_alias_for_header(header_name: str, used_aliases: set[str]) -> str:
    base = _HEADER_ALIAS_RE.sub("_", header_name.strip().lower()).strip("_") or "header"
    alias = f"header_{base}"
    suffix = 2
    while alias in used_aliases:
        alias = f"header_{base}_{suffix}"
        suffix += 1
    used_aliases.add(alias)
    return alias


def _existing_static_header_credentials(auth_config: Mapping[str, Any] | None) -> dict[str, str]:
    if not auth_config or auth_config.get("type") != "static_headers":
        return {}
    aliases = {
        str(item.get("alias") or ""): str(item.get("credential_id") or "")
        for item in auth_config.get("credentials") or []
        if isinstance(item, dict)
    }
    result: dict[str, str] = {}
    headers = auth_config.get("headers") or {}
    if isinstance(headers, dict):
        for header_name, template in headers.items():
            match = _TEMPLATE_ALIAS_RE.search(str(template))
            if match and aliases.get(match.group(1)):
                result[str(header_name)] = aliases[match.group(1)]
    return result


def _static_header_credential_ids(auth_config: Mapping[str, Any] | None) -> set[str]:
    if not auth_config or auth_config.get("type") != "static_headers":
        return set()
    return {
        str(item.get("credential_id") or "")
        for item in auth_config.get("credentials") or []
        if isinstance(item, dict) and item.get("credential_id")
    }


def _existing_dynamic_credentials(auth_config: Mapping[str, Any] | None) -> dict[str, str]:
    if not auth_config or auth_config.get("type") != "dynamic_token":
        return {}
    return {
        str(item.get("alias") or ""): str(item.get("credential_id") or "")
        for item in auth_config.get("credentials") or []
        if isinstance(item, dict) and item.get("alias") and item.get("credential_id")
    }


def _model_auth_owned_credential_ids(auth_config: Mapping[str, Any] | None) -> set[str]:
    if not auth_config:
        return set()
    auth_type = auth_config.get("type")
    if auth_type == "static_headers":
        # Static header credentials created before owned_by_model existed should
        # still be cleaned up when the model auth config is removed.
        return _static_header_credential_ids(auth_config)
    if auth_type != "dynamic_token":
        return set()
    result: set[str] = set()
    for item in auth_config.get("credentials") or []:
        if not isinstance(item, dict):
            continue
        credential_id = str(item.get("credential_id") or "")
        if credential_id and item.get("owned_by_model"):
            result.add(credential_id)
    return result


def _owned_model_auth_credential_ids(model_doc: Mapping[str, Any] | None) -> set[str]:
    if not model_doc:
        return set()
    result = _model_auth_owned_credential_ids(model_doc.get("auth_config"))
    credential_id = str(model_doc.get("auth_credential_id") or "")
    if credential_id and model_doc.get("auth_credential_owned"):
        result.add(credential_id)
    return result


async def _cleanup_credentials(user_id: str, credential_ids: set[str]) -> None:
    vault = get_vault()
    for credential_id in credential_ids:
        try:
            await vault.delete(user_id, credential_id)
        except Exception:
            logger.warning("[model_auth] Failed to cleanup model auth credential id=%s", credential_id)


def _auth_type_label(auth_type: str) -> str:
    return {
        "static_headers": "静态 Header 认证",
        "dynamic_token": "动态 Token 认证",
    }.get(auth_type, "模型认证")


def _auth_var_name(value: str, fallback: str = "value") -> str:
    name = _HEADER_ALIAS_RE.sub("_", value.strip().lower()).strip("_")
    if not name:
        name = fallback
    if name[0].isdigit():
        name = f"v_{name}"
    return name


def _replace_dynamic_credential_templates(value: Any, alias_to_vars: Mapping[str, Mapping[str, str]]) -> Any:
    if isinstance(value, dict):
        return {str(k): _replace_dynamic_credential_templates(v, alias_to_vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_dynamic_credential_templates(item, alias_to_vars) for item in value]
    text = str(value)
    for alias, fields in alias_to_vars.items():
        for field_name, var_name in fields.items():
            text = re.sub(
                r"{{\s*" + re.escape(alias) + r"\." + re.escape(field_name) + r"\s*}}",
                "{{ " + var_name + " }}",
                text,
            )
    return text


def _is_template_value(value: str) -> bool:
    return bool(re.fullmatch(r"\s*{{\s*[A-Za-z_][\w-]*\s*}}\s*", value))


def _unique_variable_name(base: str, variables: Mapping[str, Any]) -> str:
    var_name = _auth_var_name(base, "value")
    candidate = var_name
    suffix = 2
    while candidate in variables:
        candidate = f"{var_name}_{suffix}"
        suffix += 1
    return candidate


def _protect_dynamic_token_request_secrets(value: Any, variables: dict[str, dict[str, Any]], path: list[str] | None = None) -> Any:
    path = path or []
    if isinstance(value, dict):
        protected: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            protected[key_text] = _protect_dynamic_token_request_secrets(nested, variables, [*path, key_text])
        return protected
    if isinstance(value, list):
        return [
            _protect_dynamic_token_request_secrets(item, variables, [*path, str(index)])
            for index, item in enumerate(value)
        ]
    if not path:
        return value
    key = path[-1]
    if not _SENSITIVE_TOKEN_REQUEST_KEY_RE.search(key):
        return value
    text = "" if value is None else str(value)
    if not text or _is_template_value(text):
        return value
    var_name = _unique_variable_name("token_request_" + "_".join(path), variables)
    variables[var_name] = {"sensitive": True, "value": text}
    return "{{ " + var_name + " }}"


async def _unique_model_auth_name(user_id: str, base_name: str) -> str:
    repo = get_repository("credentials")
    candidate = base_name
    suffix = 2
    while await repo.find_one({"user_id": user_id, "kind": "model_auth", "name": candidate}):
        candidate = f"{base_name} {suffix}"
        suffix += 1
    return candidate


async def _ensure_model_auth_credential(
    *,
    user_id: str,
    model_name: str,
    provider: str,
    base_url: str | None,
    api_key: str | None,
    requested: ModelAuthSaveRequest | None,
) -> tuple[str | None, set[str]]:
    model_auth = await _build_model_auth_from_request(
        user_id=user_id,
        provider=provider,
        api_key=api_key,
        requested=requested,
    )
    if not model_auth:
        return None, set()

    auth_type = str(model_auth.get("type") or "")
    name = await _unique_model_auth_name(user_id, f"{model_name} 认证")
    description = f"由模型「{model_name}」创建，类型：{_auth_type_label(auth_type)}"
    credential = await get_vault().create(
        user_id,
        CredentialCreate(
            kind="model_auth",
            name=name,
            description=description,
            domain=base_url or "",
            model_auth=model_auth,
        ),
    )
    return credential.id, {credential.id}


async def _build_model_auth_from_request(
    *,
    user_id: str,
    provider: str,
    api_key: str | None,
    requested: ModelAuthSaveRequest | None,
) -> dict[str, Any] | None:
    if requested is None or requested.type == "none":
        if provider != "gemini" and api_key:
            return {
                "type": "static_headers",
                "config": {
                    "headers": {"Authorization": "Bearer {{ api_key }}"},
                    "query": {},
                },
                "variables": {
                    "api_key": {"sensitive": True, "value": api_key},
                },
            }
        return None

    if requested.type == "static_headers":
        headers: dict[str, str] = {}
        variables: dict[str, dict[str, Any]] = {}
        seen_vars: set[str] = set()
        has_authorization = False
        vault = get_vault()
        for item in requested.static_headers:
            header_name = item.name.strip()
            if not header_name:
                raise HTTPException(status_code=400, detail="Header 名称不能为空")
            has_authorization = has_authorization or header_name.lower() == "authorization"
            var_name = _auth_var_name(header_name, "header")
            base_var_name = var_name
            suffix = 2
            while var_name in seen_vars:
                var_name = f"{base_var_name}_{suffix}"
                suffix += 1
            seen_vars.add(var_name)
            value = item.value or ""
            if not value and item.credential_id:
                values = await vault.resolve_credential_values(user_id, item.credential_id)
                value = str((values or {}).get("password") or "")
            if not value:
                raise HTTPException(status_code=400, detail=f"Header {header_name} 缺少值，请输入后再保存")
            headers[header_name] = "{{ " + var_name + " }}"
            variables[var_name] = {"sensitive": True, "value": value}

        if api_key and not has_authorization:
            headers = {"Authorization": "Bearer {{ api_key }}", **headers}
            variables["api_key"] = {"sensitive": True, "value": api_key}

        if not headers:
            return None
        return {
            "type": "static_headers",
            "config": {"headers": headers, "query": {}},
            "variables": variables,
        }

    if requested.type == "dynamic_token":
        dynamic = requested.dynamic_token
        if dynamic is None:
            raise HTTPException(status_code=400, detail="动态 Token 配置不完整")
        token_request = dynamic.token_request.model_dump(mode="json")
        inject = dynamic.inject.model_dump(mode="json")
        variables: dict[str, dict[str, Any]] = {}
        alias_to_vars: dict[str, dict[str, str]] = {}
        vault = get_vault()
        for item in dynamic.credentials:
            alias = item.alias.strip()
            if not alias:
                raise HTTPException(status_code=400, detail="动态 Token 凭据 alias 不能为空")
            if not _AUTH_ALIAS_NAME_RE.match(alias):
                raise HTTPException(status_code=400, detail=f"动态 Token 凭据 alias 不合法：{alias}")
            credential_values = None
            if item.credential_id:
                credential_values = await vault.resolve_credential_values(user_id, item.credential_id)
            fields = {
                "username": item.username if item.username is not None else (credential_values or {}).get("username", ""),
                "password": item.password if item.password is not None else (credential_values or {}).get("password", ""),
                "domain": item.domain if item.domain is not None else (credential_values or {}).get("domain", ""),
            }
            alias_to_vars[alias] = {}
            for field_name, value in fields.items():
                value = str(value or "")
                if not value:
                    continue
                var_name = f"{_auth_var_name(alias, 'credential')}_{field_name}"
                alias_to_vars[alias][field_name] = var_name
                variables[var_name] = {
                    "sensitive": field_name == "password",
                    "value": value,
                }

        token_request = _replace_dynamic_credential_templates(token_request, alias_to_vars)
        token_request = _protect_dynamic_token_request_secrets(token_request, variables)
        if not str(token_request.get("url") or "").strip():
            raise HTTPException(status_code=400, detail="Token URL 不能为空")
        return {
            "type": "dynamic_token",
            "config": {
                "token_request": token_request,
                "inject": inject,
            },
            "variables": variables,
        }

    raise HTTPException(status_code=400, detail="不支持的模型认证类型")


async def _prepare_auth_config(
    *,
    user_id: str,
    model_id: str,
    model_name: str,
    base_url: str | None,
    requested: ModelAuthSaveRequest | None,
    existing_auth_config: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, set[str]]:
    if requested is None:
        return dict(existing_auth_config) if existing_auth_config else None, set()
    if requested.type == "none":
        return None, set()
    if requested.type == "dynamic_token":
        if not requested.dynamic_token:
            raise HTTPException(status_code=400, detail="动态 Token 配置不完整")
        return await _prepare_dynamic_token_auth_config(
            user_id=user_id,
            model_name=model_name,
            base_url=base_url,
            requested=requested,
            existing_auth_config=existing_auth_config,
        )
    if requested.type != "static_headers":
        raise HTTPException(status_code=400, detail="不支持的模型认证类型")

    existing_by_header = _existing_static_header_credentials(existing_auth_config)
    seen_headers: set[str] = set()
    used_aliases: set[str] = set()
    credentials: list[dict[str, Any]] = []
    headers: dict[str, str] = {}
    created_credential_ids: set[str] = set()
    vault = get_vault()

    def append_credential(alias: str, credential_id: str) -> None:
        if not any(item.get("alias") == alias for item in credentials):
            credentials.append({"alias": alias, "credential_id": credential_id, "owned_by_model": True})

    for item in requested.static_headers:
        header_name = item.name.strip()
        if not header_name:
            raise HTTPException(status_code=400, detail="Header 名称不能为空")
        header_key = header_name.lower()
        if header_key in seen_headers:
            raise HTTPException(status_code=400, detail=f"Header 名称重复：{header_name}")
        seen_headers.add(header_key)

        credential_id = (item.credential_id or existing_by_header.get(header_name) or "").strip()
        header_value = item.value or ""
        if header_value:
            credential = await vault.create(
                user_id,
                CredentialCreate(
                    name=f"模型认证 Header: {model_name} / {header_name}",
                    username="",
                    password=header_value,
                    domain=base_url or "",
                ),
            )
            credential_id = credential.id
            created_credential_ids.add(credential_id)
        if not credential_id:
            raise HTTPException(status_code=400, detail=f"Header {header_name} 缺少值，请输入后再保存")

        alias = _auth_alias_for_header(header_name, used_aliases)
        append_credential(alias, credential_id)
        headers[header_name] = "{{ " + alias + ".password }}"

    if not headers:
        return None, created_credential_ids

    auth_config = StaticHeadersAuthConfig(
        credentials=credentials,
        headers=headers,
        query={},
    )
    return auth_config.model_dump(mode="json"), created_credential_ids


async def _prepare_dynamic_token_auth_config(
    *,
    user_id: str,
    model_name: str,
    base_url: str | None,
    requested: ModelAuthSaveRequest,
    existing_auth_config: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, set[str]]:
    dynamic = requested.dynamic_token
    if dynamic is None:
        raise HTTPException(status_code=400, detail="动态 Token 配置不完整")

    token_request = dynamic.token_request
    if not token_request.url.strip():
        raise HTTPException(status_code=400, detail="Token URL 不能为空")

    existing_by_alias = _existing_dynamic_credentials(existing_auth_config)
    used_aliases: set[str] = set()
    credentials: list[dict[str, Any]] = []
    created_credential_ids: set[str] = set()
    vault = get_vault()

    for item in dynamic.credentials:
        alias = item.alias.strip()
        if not alias:
            raise HTTPException(status_code=400, detail="动态 Token 凭据 alias 不能为空")
        if not _AUTH_ALIAS_NAME_RE.match(alias):
            raise HTTPException(status_code=400, detail=f"动态 Token 凭据 alias 不合法：{alias}")
        if alias in used_aliases:
            raise HTTPException(status_code=400, detail=f"动态 Token 凭据 alias 重复：{alias}")
        used_aliases.add(alias)

        credential_id = (item.credential_id or existing_by_alias.get(alias) or "").strip()
        should_create = item.password is not None and item.password != ""
        if not credential_id and any(v is not None for v in (item.username, item.password, item.domain)):
            should_create = True

        if should_create:
            credential = await vault.create(
                user_id,
                CredentialCreate(
                    name=item.name or f"模型动态 Token 凭据: {model_name} / {alias}",
                    username=item.username or "",
                    password=item.password or "",
                    domain=item.domain or base_url or "",
                ),
            )
            credential_id = credential.id
            created_credential_ids.add(credential_id)

        if not credential_id:
            continue

        credentials.append({
            "alias": alias,
            "credential_id": credential_id,
            "owned_by_model": True,
        })

    auth_config = DynamicTokenAuthConfig(
        credentials=credentials,
        token_request=token_request,
        inject=dynamic.inject,
    )
    return auth_config.model_dump(mode="json"), created_credential_ids

@router.get("", response_model=ApiResponse)
async def list_models(current_user: User = Depends(require_user)):
    """List all available models (System + User Defined)"""
    models = await list_user_models(current_user.id)
    results = []
    for m in models:
        d = m.model_dump()
        if d.get("api_key"):
            d["api_key"] = "********"
        results.append(d)
    return ApiResponse(data=results)

@router.post("", response_model=ApiResponse)
async def create_model(body: CreateModelRequest, current_user: User = Depends(require_user)):
    """Add a user defined model"""
    user_id = str(current_user.id)
    logger.info(f"[create_model] provider={body.provider}, model_name={body.model_name}, "
                f"name={body.name}, base_url={body.base_url}, has_api_key={bool(body.api_key)}")

    model_id = str(uuid.uuid4())
    now = int(time.time())
    auth_config: dict[str, Any] | None = None
    auth_credential_id = (body.auth_credential_id or "").strip() or None
    created_credential_ids: set[str] = set()
    auth_credential_owned = False
    try:
        if auth_credential_id:
            if not await get_vault().resolve_model_auth(user_id, auth_credential_id):
                raise HTTPException(status_code=400, detail="模型认证凭据不存在，请重新选择认证配置")
        else:
            auth_credential_id, created_credential_ids = await _ensure_model_auth_credential(
                user_id=user_id,
                model_name=body.name or body.model_name,
                provider=body.provider,
                base_url=body.base_url,
                api_key=body.api_key,
                requested=body.auth_config,
            )
            auth_credential_owned = bool(auth_credential_id and auth_credential_id in created_credential_ids)
        await verify_model_connection(
            body.provider,
            body.base_url,
            body.api_key if body.provider == "gemini" else None,
            body.model_name,
            user_id=user_id,
            auth_config=auth_config,
            auth_credential_id=auth_credential_id,
        )
    except Exception:
        await _cleanup_credentials(user_id, created_credential_ids)
        raise
    
    new_model = ModelConfig(
        id=model_id,
        name=body.name,
        provider=body.provider,
        base_url=body.base_url,
        api_key=body.api_key if body.provider == "gemini" or not auth_credential_id else None,
        model_name=body.model_name,
        context_window=body.context_window,
        is_system=False,
        user_id=user_id,
        is_active=True,
        auth_credential_id=auth_credential_id,
        auth_credential_owned=auth_credential_owned,
        auth_config=auth_config,
        created_at=now,
        updated_at=now
    )
    
    doc = new_model.model_dump()
    doc["_id"] = doc.pop("id")
    
    await get_repository("models").insert_one(doc)
    
    # Return with id
    data = new_model.model_dump(mode="json")
    if data.get("api_key"):
        data["api_key"] = "********"
    return ApiResponse(data=data)

class DetectContextWindowRequest(BaseModel):
    provider: str
    base_url: str | None = None
    api_key: str | None = None
    model_name: str
    model_id: str | None = None


@router.post("/test-dynamic-token", response_model=ApiResponse)
async def test_dynamic_token(body: DynamicTokenTestRequest, current_user: User = Depends(require_user)):
    """Send the configured token request and return the raw response plus flattened field paths."""
    import httpx

    token_request = body.dynamic_token.token_request
    alias_values = _test_alias_values(body.dynamic_token.credentials)
    url = _render_test_template(token_request.url, alias_values)
    headers = _render_test_map(token_request.headers, alias_values)
    query = _render_test_map(token_request.query, alias_values)
    request_body = _render_test_value(token_request.body, alias_values)

    if not url.strip():
        raise HTTPException(status_code=400, detail="Token URL 不能为空")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if token_request.method == "GET":
                response = await client.get(url, headers=headers, params=query)
            elif token_request.body_type == "form":
                response = await client.request(token_request.method, url, headers=headers, params=query, data=request_body or None)
            elif token_request.body_type == "raw":
                response = await client.request(token_request.method, url, headers=headers, params=query, content=str(request_body or ""))
            else:
                response = await client.request(token_request.method, url, headers=headers, params=query, json=request_body or None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="获取动态 Token 失败，请检查 Token URL、网络和凭据") from exc

    try:
        response_body: Any = response.json()
    except Exception:
        response_body = response.text

    return ApiResponse(
        data={
            "status_code": response.status_code,
            "ok": 200 <= response.status_code < 300,
            "body": response_body,
            "fields": _flatten_response_fields(response_body) if isinstance(response_body, (Mapping, list)) else [],
        }
    )


async def _probe_context_window_via_api(
    base_url: str | None,
    api_key: str | None,
    model_name: str,
    *,
    default_headers: Mapping[str, str] | None = None,
    default_query: Mapping[str, str] | None = None,
) -> int | None:
    """Try to retrieve context window from the provider's /models/{model} endpoint."""
    headers = dict(default_headers or {})
    query = dict(default_query or {})
    if not api_key and not headers and not query:
        return None
    import httpx
    url = (base_url or "https://api.openai.com/v1").rstrip("/")
    has_authorization = any(key.lower() == "authorization" for key in headers)
    if api_key and not has_authorization:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{url}/models/{model_name}", headers=headers, params=query)
            if resp.status_code != 200:
                return None
            data = resp.json()
            for key in ("context_window", "context_length", "max_model_len", "max_tokens"):
                val = data.get(key)
                if isinstance(val, int) and val >= 1024:
                    return val
    except Exception:
        pass
    return None


async def _probe_gemini_context_window(api_key: str | None, model_name: str) -> int | None:
    """Probe context window via Google Generative AI API."""
    if not api_key:
        return None
    import httpx
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            val = data.get("inputTokenLimit")
            if isinstance(val, int) and val >= 1024:
                return val
    except Exception:
        pass
    return None


@router.post("/detect-context-window", response_model=ApiResponse)
async def detect_context_window(body: DetectContextWindowRequest, current_user: User = Depends(require_user)):
    """Detect context window: try local table first, then probe via API."""
    from backend.deepagent.engine import _infer_context_window

    inferred = _infer_context_window(body.model_name)
    if inferred is not None:
        return ApiResponse(data={"context_window": inferred, "source": "local"})

    api_key = body.api_key
    base_url = body.base_url
    auth_headers: dict[str, str] = {}
    auth_query: dict[str, str] = {}
    if body.model_id:
        existing = await get_repository("models").find_one({"_id": body.model_id})
        if existing:
            if existing.get("is_system"):
                if current_user.role != "admin" and existing.get("user_id"):
                    raise HTTPException(status_code=403, detail="Cannot use this model")
            elif existing.get("user_id") != str(current_user.id):
                raise HTTPException(status_code=403, detail="Cannot use this model")
            api_key = api_key or existing.get("api_key")
            base_url = base_url or existing.get("base_url")
            if (existing.get("auth_credential_id") or existing.get("auth_config")) and body.provider != "gemini":
                from backend.model_auth import ModelAuthResolver

                resolved_auth = await ModelAuthResolver().resolve(
                    {
                        "api_key": api_key,
                        "auth_credential_id": existing.get("auth_credential_id"),
                        "auth_config": existing.get("auth_config"),
                        "user_id": existing.get("user_id"),
                    },
                    str(current_user.id),
                )
                auth_headers = resolved_auth.default_headers
                auth_query = resolved_auth.default_query

    if body.provider == "gemini":
        probed = await _probe_gemini_context_window(api_key, body.model_name)
    else:
        probed = await _probe_context_window_via_api(
            base_url,
            api_key,
            body.model_name,
            default_headers=auth_headers,
            default_query=auth_query,
        )
    if probed is not None:
        return ApiResponse(data={"context_window": probed, "source": "api"})

    raise HTTPException(status_code=404, detail=f"Unable to detect context window for model '{body.model_name}'. Please set it manually.")


@router.put("/{model_id}", response_model=ApiResponse)
async def update_model(model_id: str, body: UpdateModelRequest, current_user: User = Depends(require_user)):
    """Update a user defined model"""
    user_id = str(current_user.id)
    existing = await get_repository("models").find_one({"_id": model_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Model not found")

    if existing.get("is_system"):
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Cannot edit this model")
    else:
        if existing.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Cannot edit this model")

    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        return ApiResponse(data={"id": model_id})

    update_data.pop("auth_config", None)
    update_data.pop("auth_credential_id", None)
    auth_config_was_set = "auth_config" in body.model_fields_set
    auth_credential_was_set = "auth_credential_id" in body.model_fields_set

    merged_base_url = update_data.get("base_url", existing.get("base_url"))
    merged_api_key = update_data.get("api_key", existing.get("api_key"))
    merged_model_name = update_data.get("model_name", existing.get("model_name"))
    merged_provider = existing.get("provider")
    merged_auth_config = existing.get("auth_config")
    merged_auth_credential_id = existing.get("auth_credential_id")
    existing_auth_credential_id = existing.get("auth_credential_id")
    existing_auth_credential_owned = bool(existing.get("auth_credential_owned"))
    merged_auth_credential_owned = existing_auth_credential_owned
    created_credential_ids: set[str] = set()

    if auth_credential_was_set:
        requested_auth_credential_id = (body.auth_credential_id or "").strip() or None
        if requested_auth_credential_id and not await get_vault().resolve_model_auth(user_id, requested_auth_credential_id):
            raise HTTPException(status_code=400, detail="模型认证凭据不存在，请重新选择认证配置")
        merged_auth_credential_id = requested_auth_credential_id
        merged_auth_config = None
        merged_auth_credential_owned = False
        update_data["auth_credential_id"] = merged_auth_credential_id
        update_data["auth_credential_owned"] = False
        update_data["auth_config"] = None
    elif auth_config_was_set:
        requested_model = body.auth_config
        merged_auth_credential_id, created_credential_ids = await _ensure_model_auth_credential(
            user_id=user_id,
            model_name=update_data.get("name") or existing.get("name") or merged_model_name,
            provider=merged_provider,
            base_url=merged_base_url,
            api_key=merged_api_key,
            requested=requested_model,
        )
        merged_auth_config = None
        merged_auth_credential_owned = bool(
            merged_auth_credential_id and merged_auth_credential_id in created_credential_ids
        )
        update_data["auth_credential_id"] = merged_auth_credential_id
        update_data["auth_credential_owned"] = merged_auth_credential_owned
        update_data["auth_config"] = None

    if merged_provider != "gemini" and merged_auth_credential_id:
        merged_api_key = None
        update_data["api_key"] = None

    try:
        if any(k in update_data for k in ["base_url", "api_key", "model_name", "auth_config", "auth_credential_id"]):
            await verify_model_connection(
                merged_provider,
                merged_base_url,
                merged_api_key,
                merged_model_name,
                user_id=user_id,
                auth_config=merged_auth_config,
                auth_credential_id=merged_auth_credential_id,
            )
    except Exception:
        await _cleanup_credentials(user_id, created_credential_ids)
        raise

    update_data["updated_at"] = int(time.time())

    await get_repository("models").update_one(
        {"_id": model_id},
        {"$set": update_data}
    )

    replaced_auth_credential = (
        existing_auth_credential_id
        and existing_auth_credential_owned
        and (
            str(existing_auth_credential_id) != str(merged_auth_credential_id or "")
            or not merged_auth_credential_owned
        )
    )
    if auth_config_was_set or auth_credential_was_set or replaced_auth_credential:
        cleanup_ids = _model_auth_owned_credential_ids(existing.get("auth_config"))
        if replaced_auth_credential:
            cleanup_ids.add(str(existing_auth_credential_id))
        cleanup_ids -= _model_auth_owned_credential_ids(merged_auth_config)
        await _cleanup_credentials(user_id, cleanup_ids)
    
    return ApiResponse(data={"id": model_id})


@router.delete("/{model_id}", response_model=ApiResponse)
async def delete_model(model_id: str, current_user: User = Depends(require_user)):
    """Delete a user defined model"""
    user_id = str(current_user.id)
    existing = await get_repository("models").find_one({"_id": model_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Model not found")

    if existing.get("is_system"):
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Cannot delete this model")
    else:
        if existing.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Cannot delete this model")

    await get_repository("models").delete_one({"_id": model_id})
    await _cleanup_credentials(user_id, _owned_model_auth_credential_ids(existing))
    return ApiResponse(data={"ok": True})
