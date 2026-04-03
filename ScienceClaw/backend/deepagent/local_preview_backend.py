from __future__ import annotations

import asyncio
import importlib.util
import shlex
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Dict, Optional

from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from backend.browser_preview import browser_preview_registry
from backend.rpa.cdp_connector import get_cdp_connector

RPA_PAGE_TIMEOUT_MS = 60000


@dataclass
class ParsedSkillCommand:
    script_path: Path
    kwargs: Dict[str, str]


class LocalPreviewShellBackend(LocalShellBackend):
    """Intercept local RPA skill execution so chat preview can stream the browser."""

    def __init__(self, session_id: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._session_id = session_id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        parsed = self._parse_skill_command(command)
        if parsed is None:
            return super().execute(command, timeout=timeout)
        return asyncio.run(self._run_skill_command(parsed, timeout=timeout))

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        parsed = self._parse_skill_command(command)
        if parsed is None:
            return await super().aexecute(command, timeout=timeout)
        return await self._run_skill_command(parsed, timeout=timeout)

    def _parse_skill_command(self, command: str) -> Optional[ParsedSkillCommand]:
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return None

        cwd = Path(self.cwd)
        run_tokens = tokens
        if len(tokens) >= 4 and tokens[0] == "cd" and "&&" in tokens:
            and_idx = tokens.index("&&")
            if and_idx >= 2:
                cwd = Path(tokens[1]).expanduser()
                run_tokens = tokens[and_idx + 1 :]

        if len(run_tokens) < 2 or run_tokens[0] not in {"python", "python3"}:
            return None

        script_path = Path(run_tokens[1])
        if script_path.name != "skill.py":
            return None
        if not script_path.is_absolute():
            script_path = cwd / script_path
        script_path = script_path.resolve()
        if not script_path.is_file():
            return None

        kwargs: Dict[str, str] = {}
        for arg in run_tokens[2:]:
            if arg.startswith("--") and "=" in arg:
                key, value = arg[2:].split("=", 1)
                kwargs[key] = value

        return ParsedSkillCommand(script_path=script_path, kwargs=kwargs)

    async def _run_skill_command(
        self,
        parsed: ParsedSkillCommand,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        effective_timeout = timeout if timeout is not None else 90
        context = None
        page = None
        try:
            module = self._load_module(parsed.script_path)
            execute_skill = getattr(module, "execute_skill", None)
            if not callable(execute_skill):
                return ExecuteResponse(
                    output="SKILL_ERROR: No execute_skill() function found\n",
                    exit_code=1,
                    truncated=False,
                )

            browser = await get_cdp_connector().get_browser()
            context = await browser.new_context(no_viewport=True)
            page = await context.new_page()
            page.set_default_timeout(RPA_PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(RPA_PAGE_TIMEOUT_MS)
            await browser_preview_registry.register(self._session_id, page)

            await asyncio.sleep(0.5)
            await asyncio.wait_for(execute_skill(page, **parsed.kwargs), timeout=effective_timeout)
            await page.wait_for_timeout(3000)

            return ExecuteResponse(
                output="SKILL_SUCCESS\n\n[Command succeeded with exit code 0]\n",
                exit_code=0,
                truncated=False,
            )
        except asyncio.TimeoutError:
            return ExecuteResponse(
                output=f"SKILL_ERROR: Timeout after {effective_timeout}s\n",
                exit_code=1,
                truncated=False,
            )
        except Exception as exc:
            return ExecuteResponse(
                output=f"SKILL_ERROR: {exc}\n",
                exit_code=1,
                truncated=False,
            )
        finally:
            if page is not None:
                await browser_preview_registry.unregister(self._session_id, page)
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    @staticmethod
    def _load_module(script_path: Path) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            f"scienceclaw_skill_{script_path.stem}_{abs(hash(str(script_path)))}",
            script_path,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load skill module: {script_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
