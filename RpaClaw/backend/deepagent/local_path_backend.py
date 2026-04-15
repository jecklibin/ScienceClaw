from __future__ import annotations

from deepagents.backends.protocol import EditResult, FileDownloadResponse, FileUploadResponse, SandboxBackendProtocol, WriteResult

from backend.config import settings
from backend.deepagent.local_path_utils import canonicalize_local_agent_path, normalize_presented_local_path


class LocalPathBackend(SandboxBackendProtocol):
    def __init__(self, inner: SandboxBackendProtocol) -> None:
        self._inner = inner
        self._path_style = settings.local_path_style

    @property
    def id(self) -> str:
        return self._inner.id

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def _normalize_incoming_path(self, path: str) -> str:
        if path == "/":
            cwd = getattr(self._inner, "cwd", None)
            if cwd is None:
                raise ValueError("Backend does not expose cwd for root path resolution")
            return canonicalize_local_agent_path(str(cwd), path_style=self._path_style)
        return canonicalize_local_agent_path(path, path_style=self._path_style)

    def _normalize_optional_path(self, path: str | None) -> str | None:
        if path is None:
            return None
        return self._normalize_incoming_path(path)

    def ls_info(self, path: str):
        infos = self._inner.ls_info(self._normalize_incoming_path(path))
        return [self._normalize_file_info(item) for item in infos]

    async def als_info(self, path: str):
        infos = await self._inner.als_info(self._normalize_incoming_path(path))
        return [self._normalize_file_info(item) for item in infos]

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        return self._inner.read(self._normalize_incoming_path(file_path), offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        return await self._inner.aread(self._normalize_incoming_path(file_path), offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        result = self._inner.write(self._normalize_incoming_path(file_path), content)
        return WriteResult(
            error=result.error,
            path=normalize_presented_local_path(result.path, path_style=self._path_style),
            files_update=result.files_update,
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        result = await self._inner.awrite(self._normalize_incoming_path(file_path), content)
        return WriteResult(
            error=result.error,
            path=normalize_presented_local_path(result.path, path_style=self._path_style),
            files_update=result.files_update,
        )

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        result = self._inner.edit(self._normalize_incoming_path(file_path), old_string, new_string, replace_all=replace_all)
        return EditResult(
            error=result.error,
            path=normalize_presented_local_path(result.path, path_style=self._path_style),
            files_update=result.files_update,
            occurrences=result.occurrences,
        )

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        result = await self._inner.aedit(self._normalize_incoming_path(file_path), old_string, new_string, replace_all=replace_all)
        return EditResult(
            error=result.error,
            path=normalize_presented_local_path(result.path, path_style=self._path_style),
            files_update=result.files_update,
            occurrences=result.occurrences,
        )

    def glob_info(self, pattern: str, path: str = "/"):
        infos = self._inner.glob_info(pattern, path=self._normalize_incoming_path(path))
        return [self._normalize_file_info(item) for item in infos]

    async def aglob_info(self, pattern: str, path: str = "/"):
        infos = await self._inner.aglob_info(pattern, path=self._normalize_incoming_path(path))
        return [self._normalize_file_info(item) for item in infos]

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None):
        raw = self._inner.grep_raw(pattern, path=self._normalize_optional_path(path), glob=glob)
        if isinstance(raw, str):
            return raw
        return [self._normalize_grep_match(item) for item in raw]

    async def agrep_raw(self, pattern: str, path: str | None = None, glob: str | None = None):
        raw = await self._inner.agrep_raw(pattern, path=self._normalize_optional_path(path), glob=glob)
        if isinstance(raw, str):
            return raw
        return [self._normalize_grep_match(item) for item in raw]

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        normalized = [(self._normalize_incoming_path(path), content) for path, content in files]
        responses = self._inner.upload_files(normalized)
        return [
            FileUploadResponse(
                path=normalize_presented_local_path(item.path, path_style=self._path_style) or item.path,
                error=item.error,
            )
            for item in responses
        ]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        normalized = [(self._normalize_incoming_path(path), content) for path, content in files]
        responses = await self._inner.aupload_files(normalized)
        return [
            FileUploadResponse(
                path=normalize_presented_local_path(item.path, path_style=self._path_style) or item.path,
                error=item.error,
            )
            for item in responses
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = self._inner.download_files([self._normalize_incoming_path(path) for path in paths])
        return [
            FileDownloadResponse(
                path=normalize_presented_local_path(item.path, path_style=self._path_style) or item.path,
                content=item.content,
                error=item.error,
            )
            for item in responses
        ]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = await self._inner.adownload_files([self._normalize_incoming_path(path) for path in paths])
        return [
            FileDownloadResponse(
                path=normalize_presented_local_path(item.path, path_style=self._path_style) or item.path,
                content=item.content,
                error=item.error,
            )
            for item in responses
        ]

    def execute(self, command: str, *, timeout: int | None = None):
        return self._inner.execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None):
        return await self._inner.aexecute(command, timeout=timeout)

    def _normalize_file_info(self, item: dict) -> dict:
        normalized = dict(item)
        if normalized.get("path"):
            normalized["path"] = normalize_presented_local_path(normalized["path"], path_style=self._path_style)
        return normalized

    def _normalize_grep_match(self, item: dict) -> dict:
        normalized = dict(item)
        if normalized.get("path"):
            normalized["path"] = normalize_presented_local_path(normalized["path"], path_style=self._path_style)
        return normalized
