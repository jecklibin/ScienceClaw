from __future__ import annotations

import asyncio
import base64
import concurrent.futures
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from langchain.agents.middleware.types import ContextT, ModelRequest, ModelResponse, ResponseT
from langchain.tools import ToolRuntime
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.messages.content import create_image_block
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from deepagents.backends.protocol import EditResult, WriteResult
from deepagents.backends.utils import format_grep_matches, truncate_if_too_long
from deepagents.middleware._utils import append_to_system_message
from deepagents.middleware.filesystem import (
    DEFAULT_READ_LIMIT,
    DEFAULT_READ_OFFSET,
    EXECUTION_SYSTEM_PROMPT,
    GLOB_TIMEOUT,
    GREP_TOOL_DESCRIPTION,
    IMAGE_EXTENSIONS,
    IMAGE_MEDIA_TYPES,
    LIST_FILES_TOOL_DESCRIPTION,
    NUM_CHARS_PER_TOKEN,
    READ_FILE_TOOL_DESCRIPTION,
    READ_FILE_TRUNCATION_MSG,
    WRITE_FILE_TOOL_DESCRIPTION,
    EDIT_FILE_TOOL_DESCRIPTION,
    FilesystemMiddleware,
    _supports_execution,
)

from backend.config import settings
from backend.deepagent.local_path_utils import canonicalize_local_agent_path

LOCAL_FILESYSTEM_SYSTEM_PROMPT = """## Following Conventions

- Read files before editing and follow existing project patterns
- This agent is running in local filesystem mode on the host machine

## Filesystem Tools `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`

Use host absolute paths for filesystem tool calls that match `LOCAL_PATH_STYLE`.
When `LOCAL_PATH_STYLE=windows`, use paths like `D:/code/MyScienceClaw/workspace/session/file.txt`.
When `LOCAL_PATH_STYLE=posix`, use paths like `/workspace/session/file.txt`.
Backslash Windows paths like `D:\\code\\MyScienceClaw\\workspace\\session\\file.txt` are accepted only in `windows` mode.
The special path `/` is allowed for directory-oriented tools and means the current workspace root.

- ls: list files in a directory
- read_file: read a file from the filesystem
- write_file: write to a file in the filesystem
- edit_file: edit a file in the filesystem
- glob: find files matching a pattern
- grep: search for text within files"""


def validate_local_tool_path(path: str) -> str:
    if path == "/":
        return "/"
    return canonicalize_local_agent_path(path, path_style=settings.local_path_style)


class LocalFilesystemMiddleware(FilesystemMiddleware[ContextT, ResponseT]):
    def __init__(
        self,
        *,
        backend=None,
        system_prompt: str | None = None,
        custom_tool_descriptions: dict[str, str] | None = None,
        tool_token_limit_before_evict: int | None = 20000,
        max_execute_timeout: int = 3600,
    ) -> None:
        super().__init__(
            backend=backend,
            system_prompt=system_prompt or LOCAL_FILESYSTEM_SYSTEM_PROMPT,
            custom_tool_descriptions=custom_tool_descriptions,
            tool_token_limit_before_evict=tool_token_limit_before_evict,
            max_execute_timeout=max_execute_timeout,
        )

    def _validate_tool_path(self, path: str) -> str:
        return validate_local_tool_path(path)

    def _create_ls_tool(self) -> BaseTool:
        tool_description = self._custom_tool_descriptions.get("ls") or LIST_FILES_TOOL_DESCRIPTION

        def sync_ls(
            runtime: ToolRuntime[None, Any],
            path: Annotated[str, "Absolute directory path on the host machine. `/` means the current workspace root."],
        ) -> str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(path)
            except ValueError as e:
                return f"Error: {e}"
            infos = resolved_backend.ls_info(validated_path)
            paths = [fi.get("path", "") for fi in infos]
            return str(truncate_if_too_long(paths))

        async def async_ls(
            runtime: ToolRuntime[None, Any],
            path: Annotated[str, "Absolute directory path on the host machine. `/` means the current workspace root."],
        ) -> str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(path)
            except ValueError as e:
                return f"Error: {e}"
            infos = await resolved_backend.als_info(validated_path)
            paths = [fi.get("path", "") for fi in infos]
            return str(truncate_if_too_long(paths))

        return StructuredTool.from_function(name="ls", description=tool_description, func=sync_ls, coroutine=async_ls)

    def _create_read_file_tool(self) -> BaseTool:
        tool_description = self._custom_tool_descriptions.get("read_file") or READ_FILE_TOOL_DESCRIPTION
        token_limit = self._tool_token_limit_before_evict

        def sync_read_file(
            file_path: Annotated[str, "Absolute file path on the host machine to read."],
            runtime: ToolRuntime[None, Any],
            offset: Annotated[int, "Line number to start reading from (0-indexed)."] = DEFAULT_READ_OFFSET,
            limit: Annotated[int, "Maximum number of lines to read."] = DEFAULT_READ_LIMIT,
        ) -> ToolMessage | str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(file_path)
            except ValueError as e:
                return f"Error: {e}"

            ext = Path(validated_path).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                responses = resolved_backend.download_files([validated_path])
                if responses and responses[0].content is not None:
                    media_type = IMAGE_MEDIA_TYPES.get(ext, "image/png")
                    image_b64 = base64.standard_b64encode(responses[0].content).decode("utf-8")
                    return ToolMessage(
                        content_blocks=[create_image_block(base64=image_b64, mime_type=media_type)],
                        name="read_file",
                        tool_call_id=runtime.tool_call_id,
                        additional_kwargs={"read_file_path": validated_path, "read_file_media_type": media_type},
                    )
                if responses and responses[0].error:
                    return f"Error reading image: {responses[0].error}"
                return "Error reading image: unknown error"

            result = resolved_backend.read(validated_path, offset=offset, limit=limit)
            lines = result.splitlines(keepends=True)
            if len(lines) > limit:
                result = "".join(lines[:limit])
            if token_limit and len(result) >= NUM_CHARS_PER_TOKEN * token_limit:
                truncation_msg = READ_FILE_TRUNCATION_MSG.format(file_path=validated_path)
                max_content_length = NUM_CHARS_PER_TOKEN * token_limit - len(truncation_msg)
                result = result[:max_content_length] + truncation_msg
            return result

        async def async_read_file(
            file_path: Annotated[str, "Absolute file path on the host machine to read."],
            runtime: ToolRuntime[None, Any],
            offset: Annotated[int, "Line number to start reading from (0-indexed)."] = DEFAULT_READ_OFFSET,
            limit: Annotated[int, "Maximum number of lines to read."] = DEFAULT_READ_LIMIT,
        ) -> ToolMessage | str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(file_path)
            except ValueError as e:
                return f"Error: {e}"

            ext = Path(validated_path).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                responses = await resolved_backend.adownload_files([validated_path])
                if responses and responses[0].content is not None:
                    media_type = IMAGE_MEDIA_TYPES.get(ext, "image/png")
                    image_b64 = base64.standard_b64encode(responses[0].content).decode("utf-8")
                    return ToolMessage(
                        content_blocks=[create_image_block(base64=image_b64, mime_type=media_type)],
                        name="read_file",
                        tool_call_id=runtime.tool_call_id,
                        additional_kwargs={"read_file_path": validated_path, "read_file_media_type": media_type},
                    )
                if responses and responses[0].error:
                    return f"Error reading image: {responses[0].error}"
                return "Error reading image: unknown error"

            result = await resolved_backend.aread(validated_path, offset=offset, limit=limit)
            lines = result.splitlines(keepends=True)
            if len(lines) > limit:
                result = "".join(lines[:limit])
            if token_limit and len(result) >= NUM_CHARS_PER_TOKEN * token_limit:
                truncation_msg = READ_FILE_TRUNCATION_MSG.format(file_path=validated_path)
                max_content_length = NUM_CHARS_PER_TOKEN * token_limit - len(truncation_msg)
                result = result[:max_content_length] + truncation_msg
            return result

        return StructuredTool.from_function(name="read_file", description=tool_description, func=sync_read_file, coroutine=async_read_file)

    def _create_write_file_tool(self) -> BaseTool:
        tool_description = self._custom_tool_descriptions.get("write_file") or WRITE_FILE_TOOL_DESCRIPTION

        def sync_write_file(
            file_path: Annotated[str, "Absolute path on the host machine where the file should be created."],
            content: Annotated[str, "The text content to write to the file."],
            runtime: ToolRuntime[None, Any],
        ) -> Command | str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(file_path)
            except ValueError as e:
                return f"Error: {e}"
            res: WriteResult = resolved_backend.write(validated_path, content)
            if res.error:
                return res.error
            if res.files_update is not None:
                return Command(update={"files": res.files_update, "messages": [ToolMessage(content=f"Updated file {res.path}", tool_call_id=runtime.tool_call_id)]})
            return f"Updated file {res.path}"

        async def async_write_file(
            file_path: Annotated[str, "Absolute path on the host machine where the file should be created."],
            content: Annotated[str, "The text content to write to the file."],
            runtime: ToolRuntime[None, Any],
        ) -> Command | str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(file_path)
            except ValueError as e:
                return f"Error: {e}"
            res: WriteResult = await resolved_backend.awrite(validated_path, content)
            if res.error:
                return res.error
            if res.files_update is not None:
                return Command(update={"files": res.files_update, "messages": [ToolMessage(content=f"Updated file {res.path}", tool_call_id=runtime.tool_call_id)]})
            return f"Updated file {res.path}"

        return StructuredTool.from_function(name="write_file", description=tool_description, func=sync_write_file, coroutine=async_write_file)

    def _create_edit_file_tool(self) -> BaseTool:
        tool_description = self._custom_tool_descriptions.get("edit_file") or EDIT_FILE_TOOL_DESCRIPTION

        def sync_edit_file(
            file_path: Annotated[str, "Absolute path on the host machine to the file to edit."],
            old_string: Annotated[str, "The exact text to find and replace."],
            new_string: Annotated[str, "The replacement text."],
            runtime: ToolRuntime[None, Any],
            *,
            replace_all: Annotated[bool, "If True, replace all occurrences."] = False,
        ) -> Command | str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(file_path)
            except ValueError as e:
                return f"Error: {e}"
            res: EditResult = resolved_backend.edit(validated_path, old_string, new_string, replace_all=replace_all)
            if res.error:
                return res.error
            if res.files_update is not None:
                return Command(update={"files": res.files_update, "messages": [ToolMessage(content=f"Successfully replaced {res.occurrences} instance(s) of the string in '{res.path}'", tool_call_id=runtime.tool_call_id)]})
            return f"Successfully replaced {res.occurrences} instance(s) of the string in '{res.path}'"

        async def async_edit_file(
            file_path: Annotated[str, "Absolute path on the host machine to the file to edit."],
            old_string: Annotated[str, "The exact text to find and replace."],
            new_string: Annotated[str, "The replacement text."],
            runtime: ToolRuntime[None, Any],
            *,
            replace_all: Annotated[bool, "If True, replace all occurrences."] = False,
        ) -> Command | str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(file_path)
            except ValueError as e:
                return f"Error: {e}"
            res: EditResult = await resolved_backend.aedit(validated_path, old_string, new_string, replace_all=replace_all)
            if res.error:
                return res.error
            if res.files_update is not None:
                return Command(update={"files": res.files_update, "messages": [ToolMessage(content=f"Successfully replaced {res.occurrences} instance(s) of the string in '{res.path}'", tool_call_id=runtime.tool_call_id)]})
            return f"Successfully replaced {res.occurrences} instance(s) of the string in '{res.path}'"

        return StructuredTool.from_function(name="edit_file", description=tool_description, func=sync_edit_file, coroutine=async_edit_file)

    def _create_glob_tool(self) -> BaseTool:
        tool_description = self._custom_tool_descriptions.get("glob") or "Find files matching a glob pattern using absolute host paths. The special path `/` means the current workspace root."

        def sync_glob(
            pattern: Annotated[str, "Glob pattern to match files (for example `**/*.py`)."],
            runtime: ToolRuntime[None, Any],
            path: Annotated[str, "Base directory to search from. Use an absolute host path or `/` for the current workspace root."] = "/",
        ) -> str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(path)
            except ValueError as e:
                return f"Error: {e}"
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(resolved_backend.glob_info, pattern, path=validated_path)
                try:
                    infos = future.result(timeout=GLOB_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    return f"Error: glob timed out after {GLOB_TIMEOUT}s. Try a more specific pattern or a narrower path."
            paths = [fi.get("path", "") for fi in infos]
            return str(truncate_if_too_long(paths))

        async def async_glob(
            pattern: Annotated[str, "Glob pattern to match files (for example `**/*.py`)."],
            runtime: ToolRuntime[None, Any],
            path: Annotated[str, "Base directory to search from. Use an absolute host path or `/` for the current workspace root."] = "/",
        ) -> str:
            resolved_backend = self._get_backend(runtime)
            try:
                validated_path = self._validate_tool_path(path)
            except ValueError as e:
                return f"Error: {e}"
            try:
                infos = await asyncio.wait_for(resolved_backend.aglob_info(pattern, path=validated_path), timeout=GLOB_TIMEOUT)
            except TimeoutError:
                return f"Error: glob timed out after {GLOB_TIMEOUT}s. Try a more specific pattern or a narrower path."
            paths = [fi.get("path", "") for fi in infos]
            return str(truncate_if_too_long(paths))

        return StructuredTool.from_function(name="glob", description=tool_description, func=sync_glob, coroutine=async_glob)
