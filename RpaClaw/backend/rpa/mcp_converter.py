from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.mcp_models import (
    RpaMcpSanitizeReport,
    RpaMcpSource,
    RpaMcpToolDefinition,
    build_rpa_mcp_output_schema,
)
from backend.rpa.mcp_semantic_inferer import RpaMcpSemanticInferer


_LOGIN_BUTTON_RE = re.compile(r"\b(login|log in|sign in|sign-in|signin)\b|登录|登入|登陆", re.IGNORECASE)
_AUTH_SUBMIT_RE = re.compile(
    r"\b(login|log in|sign in|sign-in|signin|submit|continue|next)\b|登录|登入|登陆|提交|确认|下一步|继续",
    re.IGNORECASE,
)
_PASSWORD_RE = re.compile(r"password|passcode|密码|口令", re.IGNORECASE)
_EMAIL_RE = re.compile(
    r"email|e-mail|username|user name|account|login id|账号|帐号|账户|用户名|邮箱|电子邮件|手机号|手机|工号",
    re.IGNORECASE,
)
_AUTH_CODE_RE = re.compile(r"otp|captcha|verification code|验证码|动态码|短信码", re.IGNORECASE)

_PARAM_NAME_HINTS = [
    (re.compile(r"搜索|查询|关键词|关键字|search|keyword|query", re.IGNORECASE), "keyword"),
    (re.compile(r"标题|题名|title", re.IGNORECASE), "title"),
    (re.compile(r"月份|month", re.IGNORECASE), "month"),
    (re.compile(r"日期|date", re.IGNORECASE), "date"),
    (re.compile(r"开始|起始|start", re.IGNORECASE), "start"),
    (re.compile(r"结束|截止|end", re.IGNORECASE), "end"),
    (re.compile(r"姓名|名称|name", re.IGNORECASE), "name"),
    (re.compile(r"编号|号码|number|id", re.IGNORECASE), "id"),
    (re.compile(r"金额|价格|price|amount|total", re.IGNORECASE), "amount"),
    (re.compile(r"文件|file|path", re.IGNORECASE), "file"),
]


class RpaMcpConverter:
    def __init__(self, semantic_inferer: RpaMcpSemanticInferer | None = None) -> None:
        self._generator = PlaywrightGenerator()
        self._semantic_inferer = semantic_inferer

    def preview(self, *, user_id: str, session_id: str, skill_name: str, name: str, description: str, steps: list[dict], params: dict) -> RpaMcpToolDefinition:
        normalized = self._generator._normalize_step_signals(
            self._generator._infer_missing_tab_transitions(
                self._generator._deduplicate_steps(steps)
            )
        )
        login_range = self._detect_login_range(normalized)
        sanitized_steps, report = self._strip_login_steps(normalized, login_range)
        sanitized_params = self._strip_login_params(params, report)
        sanitized_params = self._infer_step_params(sanitized_steps, sanitized_params)
        return self._build_preview_from_parts(
            user_id=user_id,
            session_id=session_id,
            skill_name=skill_name,
            name=name,
            tool_name="",
            description=description,
            normalized_steps=normalized,
            sanitized_steps=sanitized_steps,
            sanitized_params=sanitized_params,
            report=report,
            semantic_recommendation=None,
        )

    async def preview_with_semantics(self, *, user_id: str, session_id: str, skill_name: str, name: str, description: str, steps: list[dict], params: dict) -> RpaMcpToolDefinition:
        normalized = self._generator._normalize_step_signals(
            self._generator._infer_missing_tab_transitions(
                self._generator._deduplicate_steps(steps)
            )
        )
        login_range = self._detect_login_range(normalized)
        sanitized_steps, report = self._strip_login_steps(normalized, login_range)
        sanitized_params = self._strip_login_params(params, report)
        sanitized_params = self._infer_step_params(sanitized_steps, sanitized_params)
        recommendation = await (self._semantic_inferer or RpaMcpSemanticInferer()).infer(
            user_id=user_id,
            requested_name=name,
            requested_description=description,
            steps=sanitized_steps,
            removed_step_details=report.removed_step_details,
            fallback_params=sanitized_params,
        )
        return self._build_preview_from_parts(
            user_id=user_id,
            session_id=session_id,
            skill_name=skill_name,
            name=recommendation.display_name or name,
            tool_name=recommendation.tool_name,
            description=recommendation.description or description,
            normalized_steps=normalized,
            sanitized_steps=sanitized_steps,
            sanitized_params=recommendation.params or sanitized_params,
            report=report,
            semantic_recommendation=recommendation,
        )

    def _build_preview_from_parts(
        self,
        *,
        user_id: str,
        session_id: str,
        skill_name: str,
        name: str,
        tool_name: str,
        description: str,
        normalized_steps: list[dict],
        sanitized_steps: list[dict],
        sanitized_params: dict,
        report: RpaMcpSanitizeReport,
        semantic_recommendation,
    ) -> RpaMcpToolDefinition:
        requires_cookies = bool(report.removed_steps)
        allowed_domains = self._collect_domains(normalized_steps)
        post_auth_start_url = self._pick_post_auth_start_url(normalized_steps, sanitized_steps)
        input_schema = (
            dict(semantic_recommendation.input_schema)
            if semantic_recommendation
            else self._build_input_schema(sanitized_params, requires_cookies=requires_cookies)
        )
        if requires_cookies and "cookies" not in (input_schema.get("properties") or {}):
            input_schema.setdefault("properties", {})["cookies"] = {
                "type": "array",
                "description": "Playwright-compatible cookies for allowed domains",
            }
            input_schema.setdefault("required", [])
            if "cookies" not in input_schema["required"]:
                input_schema["required"].append("cookies")
        recommended_output_schema, inference_report = self._build_recommended_output_schema(sanitized_steps)
        semantic_source = semantic_recommendation.source if semantic_recommendation else "rule_inferred"
        semantic_warnings = list(semantic_recommendation.warnings) if semantic_recommendation else []
        if semantic_warnings:
            report.warnings.extend(semantic_warnings)
        return RpaMcpToolDefinition(
            id="preview",
            user_id=user_id,
            name=name,
            tool_name=tool_name or self._tool_name(name),
            description=description,
            requires_cookies=requires_cookies,
            source=RpaMcpSource(session_id=session_id, skill_name=skill_name),
            allowed_domains=allowed_domains,
            post_auth_start_url=post_auth_start_url,
            steps=sanitized_steps,
            params=sanitized_params,
            input_schema=input_schema,
            output_schema=recommended_output_schema,
            recommended_output_schema=recommended_output_schema,
            output_inference_report=inference_report,
            sanitize_report=report,
            schema_source=semantic_source,
            semantic_inference={
                "source": semantic_source,
                "confidence": semantic_recommendation.confidence if semantic_recommendation else None,
                "warnings": semantic_warnings,
                "model": semantic_recommendation.model if semantic_recommendation else "",
                "generated_at": "preview",
            },
        )

    def infer_output_from_execution(self, tool: RpaMcpToolDefinition, execution_result: dict) -> tuple[dict, dict]:
        data_schema = self._infer_schema_from_value((execution_result or {}).get("data"))
        downloads_schema = self._infer_array_schema((execution_result or {}).get("downloads") or [])
        artifacts_schema = self._infer_array_schema((execution_result or {}).get("artifacts") or [])
        recommended_output_schema = build_rpa_mcp_output_schema(data_schema)
        recommended_output_schema["properties"]["downloads"] = downloads_schema
        recommended_output_schema["properties"]["artifacts"] = artifacts_schema
        report = dict(tool.output_inference_report or {})
        report["test_result_keys"] = sorted(((execution_result or {}).get("data") or {}).keys()) if isinstance((execution_result or {}).get("data"), dict) else []
        report["last_successful_example_at"] = "test"
        return recommended_output_schema, report

    def _detect_login_range(self, steps: list[dict]) -> tuple[int, int] | None:
        auth_indices = [
            index for index, step in enumerate(steps)
            if self._is_auth_field_step(step) or self._is_login_page_step(step)
        ]
        if not auth_indices:
            return None

        start = min(auth_indices)
        end = max(auth_indices)

        while start > 0 and self._should_extend_login_start(steps[start - 1]):
            start -= 1

        scan_index = end + 1
        while scan_index < len(steps):
            step = steps[scan_index]
            if self._is_auth_field_step(step) or self._is_login_page_step(step) or self._is_auth_submit_step(step):
                end = scan_index
                scan_index += 1
                continue
            break

        return start, end

    def _strip_login_steps(self, steps: list[dict], login_range: tuple[int, int] | None) -> tuple[list[dict], RpaMcpSanitizeReport]:
        report = RpaMcpSanitizeReport()
        if login_range is None:
            if self._contains_auth_signals(steps):
                report.warnings.append("Could not determine login step range automatically.")
            return list(steps), report
        start, end = login_range
        report.removed_steps = list(range(start, end + 1))
        report.removed_step_details = [
            {
                "index": idx,
                "action": str(step.get("action") or ""),
                "description": str(step.get("description") or ""),
                "url": str(step.get("url") or ""),
            }
            for idx, step in enumerate(steps)
            if start <= idx <= end
        ]
        return [dict(step) for idx, step in enumerate(steps) if idx < start or idx > end], report

    def _strip_login_params(self, params: dict, report: RpaMcpSanitizeReport) -> dict:
        sanitized = {}
        for key, value in params.items():
            info = dict(value or {})
            original = str(info.get("original_value") or "")
            if info.get("sensitive") or info.get("credential_id") or "{{credential}}" in original:
                report.removed_params.append(key)
                continue
            if _EMAIL_RE.search(key) or _EMAIL_RE.search(original):
                report.removed_params.append(key)
                continue
            sanitized[key] = info
        return sanitized

    def _infer_step_params(self, steps: list[dict], params: dict) -> dict:
        inferred = {key: dict(value or {}) for key, value in params.items()}
        used_names = set(inferred.keys())
        known_values = {
            str(info.get("original_value"))
            for info in inferred.values()
            if isinstance(info, dict) and info.get("original_value") not in (None, "")
        }
        inferred_count = 0

        for step_index, step in enumerate(steps):
            action = str(step.get("action") or "")
            if action not in {"fill", "select"}:
                continue
            if self._is_auth_like_step(step):
                continue

            original_value = str(step.get("value") or "")
            if not original_value or original_value == "{{credential}}" or original_value in known_values:
                continue

            inferred_count += 1
            base_name = self._derive_param_name(step) or f"param_{inferred_count}"
            name = self._unique_param_name(base_name, used_names)
            used_names.add(name)
            known_values.add(original_value)
            inferred[name] = {
                "original_value": original_value,
                "type": self._infer_param_type(original_value),
                "description": str(step.get("description") or name),
                "required": False,
                "sensitive": False,
                "source_step_index": step_index,
                "source_step_id": str(step.get("id") or ""),
            }

        return inferred

    def _collect_domains(self, steps: list[dict]) -> list[str]:
        domains = []
        for step in steps:
            host = (urlparse(str(step.get("url") or "")).hostname or "").lower().lstrip(".")
            if host and host not in domains:
                domains.append(host)
        return domains

    def _pick_post_auth_start_url(self, steps: list[dict], sanitized_steps: list[dict]) -> str:
        for step in sanitized_steps:
            url = str(step.get("url") or "").strip()
            if url:
                return url
        for step in steps:
            url = str(step.get("url") or "").strip()
            if url:
                return url
        return ""

    def _build_input_schema(self, params: dict, *, requires_cookies: bool) -> dict:
        properties = {}
        required = []
        if requires_cookies:
            properties["cookies"] = {
                "type": "array",
                "description": "Playwright-compatible cookies for allowed domains",
            }
            required.append("cookies")
        for key, info in params.items():
            prop = {
                "type": info.get("type", "string"),
                "description": info.get("description", ""),
            }
            original = info.get("original_value")
            if original and original != "{{credential}}":
                prop["default"] = original
            if info.get("required"):
                required.append(key)
            properties[key] = prop
        return {"type": "object", "properties": properties, "required": required}

    def _derive_param_name(self, step: dict) -> str:
        candidates = self._param_name_candidates(step)
        joined = " ".join(candidates)
        for pattern, name in _PARAM_NAME_HINTS:
            if pattern.search(joined):
                return name
        for candidate in candidates:
            ascii_name = self._normalize_ascii_name(candidate)
            if ascii_name:
                return ascii_name
        return ""

    def _param_name_candidates(self, step: dict) -> list[str]:
        candidates = [
            str(step.get("label") or ""),
            str(step.get("description") or ""),
        ]
        target = self._parse_target(step.get("target"))
        if isinstance(target, dict):
            for key in ("name", "value", "placeholder", "role"):
                value = target.get(key)
                if value:
                    candidates.append(str(value))
            for nested_key in ("parent", "child", "base", "locator"):
                nested = target.get(nested_key)
                if isinstance(nested, dict):
                    for key in ("name", "value", "placeholder", "role"):
                        value = nested.get(key)
                        if value:
                            candidates.append(str(value))
        return [candidate for candidate in candidates if candidate.strip()]

    @staticmethod
    def _normalize_ascii_name(value: str) -> str:
        text = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
        text = re.sub(r"_+", "_", text).strip("_")
        if not text or not re.search(r"[a-z]", text):
            return ""
        if text[0].isdigit():
            text = f"param_{text}"
        return text[:48]

    @staticmethod
    def _unique_param_name(base_name: str, used_names: set[str]) -> str:
        name = base_name or "param"
        if name not in used_names:
            return name
        suffix = 2
        while f"{name}_{suffix}" in used_names:
            suffix += 1
        return f"{name}_{suffix}"

    @staticmethod
    def _infer_param_type(value: str) -> str:
        stripped = value.strip()
        if re.fullmatch(r"-?\d+", stripped):
            return "integer"
        if re.fullmatch(r"-?\d+\.\d+", stripped):
            return "number"
        if stripped.lower() in {"true", "false"}:
            return "boolean"
        return "string"

    def _build_recommended_output_schema(self, steps: list[dict]) -> tuple[dict, dict]:
        properties = {}
        recording_signals = []
        has_download = False
        for step in steps:
            action = str(step.get("action") or "")
            result_key = self._generator._normalize_result_key(step.get("result_key"))
            if action == "extract_text" and result_key:
                properties[result_key] = {"type": "string", "description": str(step.get("description") or "")}
                recording_signals.append({"kind": "extract_text", "key": result_key, "description": str(step.get("description") or "")})
            download_signal = self._generator._download_signal(step)
            if download_signal:
                has_download = True
                recording_signals.append({
                    "kind": "download",
                    "filename": str(download_signal.get("filename") or ""),
                    "description": str(step.get("description") or ""),
                })
        data_schema = {
            "type": "object",
            "properties": properties,
            "additionalProperties": not bool(properties),
        }
        output_schema = build_rpa_mcp_output_schema(data_schema)
        if has_download:
            output_schema["properties"]["downloads"] = self._infer_array_schema([{"filename": "sample.file", "path": "/tmp/sample.file"}])
        return output_schema, {"recording_signals": recording_signals}

    def _infer_schema_from_value(self, value):
        if value is None:
            return {"type": "object", "properties": {}, "additionalProperties": True}
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, int) and not isinstance(value, bool):
            return {"type": "integer"}
        if isinstance(value, float):
            return {"type": "number"}
        if isinstance(value, str):
            return {"type": "string"}
        if isinstance(value, list):
            return self._infer_array_schema(value)
        if isinstance(value, dict):
            return {
                "type": "object",
                "properties": {str(key): self._infer_schema_from_value(item) for key, item in value.items()},
                "additionalProperties": False,
            }
        return {"type": "string"}

    def _infer_array_schema(self, value):
        items = value if isinstance(value, list) else []
        if not items:
            return {"type": "array", "items": {"type": "object", "additionalProperties": True}}
        merged_items = self._merge_object_schemas([self._infer_schema_from_value(item) for item in items])
        return {"type": "array", "items": merged_items}

    def _merge_object_schemas(self, schemas: list[dict]) -> dict:
        if not schemas:
            return {"type": "object", "additionalProperties": True}
        object_schemas = [schema for schema in schemas if schema.get("type") == "object"]
        if len(object_schemas) != len(schemas):
            return schemas[0]
        properties = {}
        for schema in object_schemas:
            for key, value in (schema.get("properties") or {}).items():
                properties.setdefault(key, value)
        return {"type": "object", "properties": properties, "additionalProperties": False}

    def _contains_auth_signals(self, steps: list[dict]) -> bool:
        return any(self._is_auth_like_step(step) for step in steps)

    def _is_auth_like_step(self, step: dict) -> bool:
        return bool(
            self._is_auth_field_step(step)
            or self._is_auth_submit_step(step)
            or self._is_login_page_step(step)
            or self._is_auth_entry_step(step)
        )

    def _is_auth_field_step(self, step: dict) -> bool:
        text = self._step_text(step)
        action = str(step.get("action") or "")
        if step.get("sensitive") or "{{credential}}" in text:
            return True
        if action in {"fill", "select"} and (_PASSWORD_RE.search(text) or _EMAIL_RE.search(text)):
            return True
        if action in {"fill", "select"} and self._is_login_page_step(step) and _AUTH_CODE_RE.search(text):
            return True
        return False

    def _is_auth_submit_step(self, step: dict) -> bool:
        action = str(step.get("action") or "")
        if action not in {"click", "navigate_click", "press", "navigate_press"}:
            return False
        text = self._step_text(step)
        if _AUTH_SUBMIT_RE.search(text):
            return True
        return bool(action in {"press", "navigate_press"} and (_PASSWORD_RE.search(text) or _EMAIL_RE.search(text)))

    def _is_auth_entry_step(self, step: dict) -> bool:
        action = str(step.get("action") or "")
        if action not in {"click", "navigate_click", "navigate", "goto"}:
            return False
        return bool(_LOGIN_BUTTON_RE.search(self._step_text(step)) or self._is_login_page_step(step))

    def _should_extend_login_start(self, step: dict) -> bool:
        return self._is_auth_entry_step(step) or self._is_login_page_step(step)

    def _is_login_page_step(self, step: dict) -> bool:
        url = str(step.get("url") or "").lower()
        return bool(
            "login" in url
            or "signin" in url
            or "sign-in" in url
            or "passport" in url
            or "/auth" in url
            or "sso" in url
        )

    def _step_text(self, step: dict) -> str:
        values = [str(step.get(key) or "") for key in ("description", "value", "target", "url", "label", "tag")]
        target = self._parse_target(step.get("target"))
        if isinstance(target, dict):
            values.extend(self._flatten_locator_text(target))
        return " ".join(values)

    def _parse_target(self, raw_target):
        if isinstance(raw_target, dict):
            return raw_target
        if not isinstance(raw_target, str) or not raw_target.strip():
            return None
        try:
            parsed = json.loads(raw_target)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _flatten_locator_text(self, locator: dict) -> list[str]:
        values = []
        for key in ("method", "role", "name", "value", "placeholder"):
            value = locator.get(key)
            if value:
                values.append(str(value))
        for key in ("parent", "child", "base", "locator"):
            nested = locator.get(key)
            if isinstance(nested, dict):
                values.extend(self._flatten_locator_text(nested))
        return values

    def _tool_name(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", (name or "tool").strip().lower()).strip("_")
        return f"rpa_{slug or 'tool'}"
