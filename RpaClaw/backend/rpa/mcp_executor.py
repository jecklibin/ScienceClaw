from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.config import settings
from backend.rpa.mcp_script_compiler import generate_mcp_script
from backend.rpa.playwright_security import get_context_kwargs


class InvalidCookieError(ValueError):
    pass


class RpaMcpExecutor:
    def __init__(self, *, browser_factory=None, script_runner=None, pw_loop_runner=None, downloads_dir_factory=None) -> None:
        self._browser_factory = browser_factory
        self._script_runner = script_runner or self._default_runner
        self._pw_loop_runner = pw_loop_runner
        self._downloads_dir_factory = downloads_dir_factory

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
        script = generate_mcp_script(tool.steps, tool.params, is_local=(settings.storage_backend == 'local'))

        async def _run() -> dict[str, Any]:
            browser = await self._resolve_browser(tool)
            context = await browser.new_context(**get_context_kwargs())
            try:
                if cookies:
                    await context.add_cookies(cookies)
                page = await context.new_page()
                if tool.post_auth_start_url:
                    await page.goto(tool.post_auth_start_url)
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
