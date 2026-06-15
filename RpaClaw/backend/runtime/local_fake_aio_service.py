from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _container_name(session_id: str) -> str:
    sanitized = "".join(ch if ch.isalnum() else "-" for ch in session_id.lower())
    sanitized = sanitized.strip("-") or "session"
    digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:8]
    return f"rpaclaw-aio-{sanitized[:24].rstrip('-')}-{digest}"


def _sandbox_id(session_id: str) -> str:
    digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:12]
    return f"local-aio-{digest}"


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _reserve_port(start: int, used: set[int]) -> int:
    port = start
    while port in used or not _port_available(port):
        port += 1
    used.add(port)
    return port


class _DockerCliContainer:
    def __init__(self, name: str):
        self.name = name
        self.attrs = {"State": {"Status": self._inspect_status()}}

    def _inspect_status(self) -> str:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", self.name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() or "unknown"

    def remove(self, force: bool = False) -> None:
        command = ["docker", "rm"]
        if force:
            command.append("-f")
        command.append(self.name)
        subprocess.run(command, capture_output=True, text=True, check=False)


class _DockerCliContainersApi:
    def run(self, image: str, **kwargs: Any) -> _DockerCliContainer:
        command = ["docker", "run"]
        if kwargs.get("detach"):
            command.append("-d")
        name = kwargs.get("name")
        if name:
            command.extend(["--name", str(name)])
        for container_port, host_port in (kwargs.get("ports") or {}).items():
            command.extend(["-p", f"{host_port}:{str(container_port).split('/')[0]}"])
        for key, value in (kwargs.get("environment") or {}).items():
            command.extend(["-e", f"{key}={value}"])
        for key, value in (kwargs.get("labels") or {}).items():
            command.extend(["--label", f"{key}={value}"])
        command.append(image)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "docker run failed")
        return _DockerCliContainer(str(name or result.stdout.strip()))

    def get(self, name: str) -> _DockerCliContainer:
        return _DockerCliContainer(name)


class _DockerCliClient:
    def __init__(self) -> None:
        self.containers = _DockerCliContainersApi()


def _default_docker_client():
    try:
        import docker
    except ModuleNotFoundError:
        return _DockerCliClient()
    return docker.from_env()


@dataclass
class LocalFakeAioSandbox:
    sandbox_id: str
    session_id: str
    container_name: str
    adapter_host_port: int
    cdp_host_port: int

    def payload(self) -> dict[str, Any]:
        route_base_url = f"http://127.0.0.1:{self.adapter_host_port}"
        return {
            "sandbox_id": self.sandbox_id,
            "status": "ready",
            "route_base_url": route_base_url,
            "browser_view_url": f"{route_base_url}/browser",
            "metadata": {
                "surface": "local_fake_aio",
                "adapter_host_port": self.adapter_host_port,
                "cdp_host_port": self.cdp_host_port,
            },
        }


def create_local_fake_aio_app(
    *,
    docker_client=None,
    image: str = "rpaclaw-runtime-adapter:dev",
    adapter_host_port_start: int = 18081,
    cdp_host_port_start: int = 19222,
    api_token: str | None = None,
) -> FastAPI:
    if docker_client is None:
        docker_client = _default_docker_client()

    app = FastAPI(title="RpaClaw Local Fake AIO Service")
    sandboxes: dict[str, LocalFakeAioSandbox] = {}
    used_ports: set[int] = set()

    def _require_token(authorization: str | None) -> None:
        if not api_token:
            return
        if authorization != f"Bearer {api_token}":
            raise HTTPException(status_code=401, detail="Invalid local fake AIO API token")

    @app.post("/v1/sandboxes")
    async def create_sandbox(
        payload: dict[str, Any] | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_token(authorization)
        payload = payload or {}
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "session")
        adapter_host_port = _reserve_port(adapter_host_port_start, used_ports)
        cdp_host_port = _reserve_port(cdp_host_port_start, used_ports)
        sandbox = LocalFakeAioSandbox(
            sandbox_id=_sandbox_id(session_id),
            session_id=session_id,
            container_name=_container_name(session_id),
            adapter_host_port=adapter_host_port,
            cdp_host_port=cdp_host_port,
        )
        environment = {
            "RUNTIME_ADAPTER_WORKSPACE_ROOT": "/workspace",
            "RUNTIME_ADAPTER_DOWNLOADS_DIR": "downloads",
            "RUNTIME_ADAPTER_ENABLE_LOCAL_EXECUTION": "true",
            "RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH": "true",
            "RUNTIME_ADAPTER_BROWSER_DEBUG_PORT": "9222",
            "RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL": f"ws://127.0.0.1:{cdp_host_port}",
            "RUNTIME_ADAPTER_VERSION": "local-fake-aio",
        }
        for env_key in ("env", "adapter_env"):
            adapter_env = payload.get(env_key)
            if isinstance(adapter_env, dict):
                for key, value in adapter_env.items():
                    if isinstance(key, str) and key.startswith("RUNTIME_ADAPTER_"):
                        environment[key] = str(value)
        docker_client.containers.run(
            image,
            detach=True,
            name=sandbox.container_name,
            ports={"8080/tcp": adapter_host_port, "9222/tcp": cdp_host_port},
            environment=environment,
            labels={
                "rpaclaw.local_fake_aio": "true",
                "rpaclaw.session_id": session_id,
                "rpaclaw.sandbox_id": sandbox.sandbox_id,
            },
        )
        sandboxes[sandbox.sandbox_id] = sandbox
        return {"data": sandbox.payload()}

    @app.get("/v1/sandboxes/{sandbox_id}")
    async def get_sandbox(
        sandbox_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_token(authorization)
        sandbox = sandboxes.get(sandbox_id)
        if sandbox is None:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        try:
            container = docker_client.containers.get(sandbox.container_name)
            status = str((getattr(container, "attrs", {}) or {}).get("State", {}).get("Status") or "unknown")
        except Exception:
            status = "missing"
        payload = sandbox.payload()
        payload["status"] = "ready" if status == "running" else status
        return {"data": payload}

    @app.delete("/v1/sandboxes/{sandbox_id}")
    async def delete_sandbox(
        sandbox_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _require_token(authorization)
        sandbox = sandboxes.pop(sandbox_id, None)
        if sandbox is None:
            return {"status": "deleted"}
        try:
            container = docker_client.containers.get(sandbox.container_name)
            container.remove(force=True)
        except Exception:
            pass
        return {"status": "deleted"}

    return app


def create_local_fake_aio_app_from_env() -> FastAPI:
    return create_local_fake_aio_app(
        image=os.environ.get("LOCAL_FAKE_AIO_IMAGE", "rpaclaw-runtime-adapter:dev"),
        adapter_host_port_start=int(os.environ.get("LOCAL_FAKE_AIO_ADAPTER_PORT_START", "18081")),
        cdp_host_port_start=int(os.environ.get("LOCAL_FAKE_AIO_CDP_PORT_START", "19222")),
        api_token=os.environ.get("LOCAL_FAKE_AIO_API_TOKEN") or None,
    )


app = create_local_fake_aio_app_from_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local fake AIO lifecycle service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "app",
    "create_local_fake_aio_app",
    "create_local_fake_aio_app_from_env",
]
