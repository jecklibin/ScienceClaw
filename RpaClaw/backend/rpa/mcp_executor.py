from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.config import settings
from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.playwright_security import get_context_kwargs


class InvalidCookieError(ValueError):
    pass


class RpaMcpExecutor:
    def __init__(self, *, browser_factory=None, script_runner=None, pw_loop_runner=None, downloads_dir_factory=None) -> None:
        self._browser_factory = browser_factory
        self._script_runner = script_runner or self._default_runner
        self._pw_loop_runner = pw_loop_runner
        self._downloads_dir_factory = downloads_dir_factory
        self._generator = PlaywrightGenerator()

    def validate_cookies(self, *, cookies: list[dict[str, Any]], allowed_domains: list[str], post_auth_start_url: str) -> list[dict[str, Any]]:
        if not isinstance(cookies, list) or not cookies:
            raise InvalidCookieError("cookies must be a non-empty array")
        allowed = {domain.lstrip('.').lower() for domain in allowed_domains}
        target_host = (urlparse(post_auth_start_url).hostname or '').lstrip('.').lower()
        for item in cookies:
            if not item.get('name') or not item.get('value'):
                raise InvalidCookieError('each cookie requires name and value')
            raw_domain = str(item.get('domain') or urlparse(str(item.get('url') or '')).hostname or '')
            domain = raw_domain.lstrip('.').lower()
            if not domain:
                raise InvalidCookieError('each cookie requires domain or url')
            if allowed and domain not in allowed and not any(domain.endswith(f'.{candidate}') for candidate in allowed):
                raise InvalidCookieError('cookie domain is not allowed')
        if target_host and allowed and target_host not in allowed and not any(target_host.endswith(f'.{candidate}') for candidate in allowed):
            raise InvalidCookieError('post-auth start URL is not within allowed domains')
        return cookies

    async def execute(self, tool, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_cookies = list(arguments.get('cookies') or [])
        should_validate_cookies = bool(getattr(tool, 'requires_cookies', False) or raw_cookies)
        cookies = self.validate_cookies(
            cookies=raw_cookies,
            allowed_domains=list(tool.allowed_domains or []),
            post_auth_start_url=tool.post_auth_start_url,
        ) if should_validate_cookies else []
        kwargs = {key: value for key, value in arguments.items() if key != 'cookies'}
        downloads_dir = self._prepare_downloads_dir(tool)
        if downloads_dir:
            kwargs.setdefault('_downloads_dir', downloads_dir)
        workflow_package = getattr(tool, "workflow_package", None) or {}
        script = "" if workflow_package else self._generator.generate_script(tool.steps, tool.params, is_local=(settings.storage_backend == 'local'))

        async def _run() -> dict[str, Any]:
            browser = await self._resolve_browser(tool)
            context = await browser.new_context(**get_context_kwargs())
            try:
                if cookies:
                    await context.add_cookies(cookies)
                page = await context.new_page()
                if tool.post_auth_start_url:
                    await page.goto(tool.post_auth_start_url)
                if workflow_package:
                    workflow_result = await self._run_workflow_package(page, workflow_package, kwargs)
                    public_data = self._project_workflow_data_for_schema(workflow_result, getattr(tool, "output_schema", {}))
                    return self._normalize_execution_result(
                        {"success": True, "message": "Execution completed", "data": public_data}
                    )
                return self._normalize_execution_result(await self._script_runner(page, script, kwargs))
            finally:
                await context.close()

        if self._pw_loop_runner:
            return await self._pw_loop_runner(_run())
        return await _run()

    async def _resolve_browser(self, tool):
        if self._browser_factory is None:
            raise RuntimeError('No browser factory configured for RPA MCP execution')
        browser = self._browser_factory(tool=tool)
        if hasattr(browser, '__await__'):
            browser = await browser
        return browser

    def _prepare_downloads_dir(self, tool) -> str | None:
        if self._downloads_dir_factory is None:
            return None
        downloads_dir = self._downloads_dir_factory(tool)
        if not downloads_dir:
            return None
        Path(downloads_dir).mkdir(parents=True, exist_ok=True)
        return downloads_dir

    async def _default_runner(self, page, script: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        namespace: dict[str, Any] = {}
        exec(compile(script, '<rpa_mcp_script>', 'exec'), namespace)
        execute_skill = namespace.get('execute_skill')
        if not callable(execute_skill):
            raise RuntimeError('No execute_skill() function in generated MCP script')
        result = await execute_skill(page, **kwargs)
        await page.wait_for_timeout(3000)
        return {"success": True, "message": "Execution completed", "data": result or {}}

    async def _run_workflow_package(self, page, workflow_package: dict[str, Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        workflow = workflow_package.get("workflow") if isinstance(workflow_package, dict) else {}
        files = workflow_package.get("files") if isinstance(workflow_package, dict) else {}
        params_config = workflow_package.get("params") if isinstance(workflow_package, dict) else {}
        if not isinstance(workflow, dict) or not isinstance(files, dict):
            raise RuntimeError("Invalid workflow package")

        context = _WorkflowExecutionContext(params=self._load_workflow_params(params_config, kwargs))
        context.runtime["current_page"] = page
        for segment in workflow.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            kind = str(segment.get("kind") or "")
            current_page = context.runtime.get("current_page", page)
            if kind == "rpa":
                result = await self._run_workflow_rpa_segment(segment, files, context, current_page)
            elif kind == "script":
                result = await self._run_workflow_script_segment(segment, files, context)
            elif kind in {"mcp", "llm"}:
                result = {"status": "metadata_only", "config": segment.get("config") or {}}
            else:
                raise ValueError(f"Unsupported segment kind: {kind}")
            context.store_segment_outputs(segment, self._normalize_segment_result(result))
        return context.final_outputs()

    def _load_workflow_params(self, params_config: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if isinstance(params_config, dict):
            for name, spec in params_config.items():
                if not isinstance(spec, dict):
                    continue
                original = spec.get("original_value", "")
                if original and original != "{{credential}}":
                    params[str(name)] = original
        params.update(kwargs)
        return params

    async def _run_workflow_rpa_segment(
        self,
        segment: dict[str, Any],
        files: dict[str, Any],
        context: "_WorkflowExecutionContext",
        page,
    ) -> dict[str, Any]:
        module = self._load_workflow_module(str(segment.get("entry") or ""), files)
        execute_segment = module.get("execute_segment")
        if not callable(execute_segment):
            raise RuntimeError(f"RPA segment {segment.get('id')} must define execute_segment(page, workflow_context, **kwargs)")
        result = execute_segment(page, context.runtime, **self._build_segment_kwargs(segment, context))
        if inspect.isawaitable(result):
            result = await result
        return self._normalize_segment_result(result)

    async def _run_workflow_script_segment(
        self,
        segment: dict[str, Any],
        files: dict[str, Any],
        context: "_WorkflowExecutionContext",
    ) -> dict[str, Any]:
        module = self._load_workflow_module(str(segment.get("entry") or ""), files)
        run = module.get("run")
        if not callable(run):
            raise RuntimeError(f"Script segment {segment.get('id')} must define run(context)")
        result = run(context)
        if inspect.isawaitable(result):
            result = await result
        return self._normalize_segment_result(result)

    def _load_workflow_module(self, relative_path: str, files: dict[str, Any]) -> dict[str, Any]:
        source = files.get(relative_path)
        if source is None:
            raise RuntimeError(f"Workflow package is missing segment source: {relative_path}")
        namespace: dict[str, Any] = {}
        exec(compile(str(source), f"<rpa_mcp_workflow:{relative_path}>", "exec"), namespace)
        return namespace

    def _build_segment_kwargs(self, segment: dict[str, Any], context: "_WorkflowExecutionContext") -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for input_spec in segment.get("inputs") or []:
            if not isinstance(input_spec, dict):
                continue
            name = input_spec.get("name")
            if not name:
                continue
            source_ref = input_spec.get("source_ref")
            value = context.resolve(str(source_ref)) if source_ref else context.params.get(str(name), input_spec.get("default"))
            if value is not None:
                resolved[str(name)] = value
        return resolved

    @staticmethod
    def _normalize_segment_result(result: Any) -> dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        return {"result": result}

    def _project_workflow_data_for_schema(self, workflow_result: dict[str, Any], output_schema: Any) -> dict[str, Any]:
        data = dict(workflow_result or {})
        data.pop("status", None)
        data.pop("outputs", None)

        data_schema = self._output_data_schema(output_schema)
        properties = data_schema.get("properties") if isinstance(data_schema, dict) else None
        if not isinstance(properties, dict):
            return data
        if data_schema.get("additionalProperties", True) is not False:
            return data
        return {
            name: data[name]
            for name in properties
            if name in data
        }

    @staticmethod
    def _output_data_schema(output_schema: Any) -> dict[str, Any]:
        if not isinstance(output_schema, dict):
            return {}
        properties = output_schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        data_schema = properties.get("data")
        return data_schema if isinstance(data_schema, dict) else {}

    def _normalize_execution_result(self, result: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(result or {})
        downloads = payload.get("downloads")
        if not isinstance(downloads, list):
            downloads = self._extract_downloads_from_data(payload.get("data"))
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            artifacts = []
        return {
            "success": bool(payload.get("success", True)),
            "message": str(payload.get("message") or ("Execution completed" if payload.get("success", True) else "Execution failed")),
            "data": payload.get("data") if isinstance(payload.get("data"), dict) else payload.get("data") or {},
            "downloads": downloads,
            "artifacts": artifacts,
            "error": payload.get("error"),
        }

    def _extract_downloads_from_data(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        downloads = []
        for key, value in data.items():
            if not str(key).startswith("download_") or not isinstance(value, dict):
                continue
            filename = value.get("filename")
            path = value.get("path")
            if filename and path:
                downloads.append({"filename": filename, "path": path})
        return downloads


class _WorkflowExecutionContext:
    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.outputs: dict[str, dict[str, Any]] = {}
        self.artifacts: dict[str, Any] = {}
        self.runtime: dict[str, Any] = {}

    def resolve(self, source_ref: str | None) -> Any:
        if not source_ref:
            return None
        if source_ref.startswith("params."):
            return self.params.get(source_ref.removeprefix("params."))
        if source_ref.startswith("artifact:"):
            value = self.artifacts.get(source_ref.removeprefix("artifact:"))
            if isinstance(value, dict) and value.get("path"):
                return value.get("path")
            return value
        if ".outputs." in source_ref:
            segment_id, output_name = source_ref.split(".outputs.", 1)
            return self.outputs.get(segment_id, {}).get(output_name)
        return None

    def store_segment_outputs(self, segment: dict[str, Any], values: dict[str, Any]) -> None:
        segment_id = str(segment.get("id") or "")
        if segment_id:
            self.outputs[segment_id] = values
        self._store_artifact_outputs(segment, values)

    def _store_artifact_outputs(self, segment: dict[str, Any], values: dict[str, Any]) -> None:
        for output_spec in segment.get("outputs") or []:
            artifact_ref = output_spec.get("artifact_ref") if isinstance(output_spec, dict) else None
            if not artifact_ref:
                continue
            value = values.get(str(output_spec.get("name") or ""))
            if value is None:
                value = self._first_file_like_output(values)
            if value is not None:
                self.artifacts[str(artifact_ref)] = value

    @staticmethod
    def _first_file_like_output(values: dict[str, Any]) -> Any:
        for value in values.values():
            if isinstance(value, dict) and value.get("path"):
                return value
        for name, value in values.items():
            if str(name).startswith("download_"):
                return value
        return None

    def final_outputs(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "success",
            "outputs": self.outputs,
            "artifacts": self.artifacts,
        }
        for segment_id, values in self.outputs.items():
            for name, value in values.items():
                if name in payload:
                    payload[f"{segment_id}_{name}"] = value
                else:
                    payload[name] = value
        return payload
