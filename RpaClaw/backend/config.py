import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic_settings import BaseSettings


def _resolve_home() -> str:
    """Return RPA_CLAW_HOME, falling back to ./rpaclaw_home."""
    return os.environ.get("RPA_CLAW_HOME", "")


def _resolve_home_env_path() -> Path | None:
    home = (_resolve_home() or "").strip()
    if not home:
        return None
    return Path(home) / ".env"


def _resolve_sandbox_home() -> str:
    """Return SANDBOX_RPA_CLAW_HOME, falling back to /home/rpaclaw."""
    return os.environ.get("SANDBOX_RPA_CLAW_HOME", "/home/rpaclaw")


def _sub(env_key: str, home: str, sub_dir: str, fallback: str) -> str:
    """If *env_key* is set explicitly, use it; otherwise derive from *home*."""
    explicit = os.environ.get(env_key)
    if explicit:
        return explicit
    if home:
        return str(Path(home) / sub_dir)
    return fallback


def _env_or_default(env_key: str, default: str) -> str:
    explicit = (os.environ.get(env_key) or "").strip()
    return explicit or default


def _resolve_auth_provider() -> str:
    explicit = (os.environ.get("AUTH_PROVIDER") or "").strip()
    if explicit:
        return explicit
    return "none" if os.environ.get("STORAGE_BACKEND") == "local" else "local"


def _resolve_sandbox_base_url() -> str:
    explicit_base = (os.environ.get("SANDBOX_BASE_URL") or "").strip()
    if explicit_base:
        return explicit_base.rstrip("/")

    explicit_mcp = (os.environ.get("SANDBOX_MCP_URL") or "").strip()
    if explicit_mcp:
        parsed = urlparse(explicit_mcp)
        derived_path = parsed.path[:-4] if parsed.path.endswith("/mcp") else parsed.path
        return parsed._replace(
            path=derived_path.rstrip("/"),
            params="",
            query="",
            fragment="",
        ).geturl().rstrip("/")

    return "http://sandbox:8080"


def _resolve_sandbox_mcp_url() -> str:
    return _env_or_default(
        "SANDBOX_MCP_URL",
        f"{_resolve_sandbox_base_url()}/mcp",
    ).rstrip("/")


def _resolve_tools_dir() -> str:
    return _sub("TOOLS_DIR", _resolve_home(), "tools", "/app/Tools")


def _resolve_sandbox_tools_dir() -> str:
    return _env_or_default("SANDBOX_TOOLS_DIR", "/app/Tools").rstrip("/")


def _resolve_system_mcp_config_path() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return _env_or_default("SYSTEM_MCP_CONFIG_PATH", str(repo_root / "mcp_servers.yaml"))


def _derive_sandbox_vnc_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url.rstrip("/"))
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"

    port = parsed.port
    if port == 8080:
        return parsed._replace(
            scheme=ws_scheme,
            netloc=f"{parsed.hostname}:6080",
            path="",
            query="",
            fragment="",
        ).geturl()
    if port == 18080:
        return parsed._replace(
            scheme=ws_scheme,
            netloc=f"{parsed.hostname}:16080",
            path="",
            query="",
            fragment="",
        ).geturl()

    return parsed._replace(
        scheme=ws_scheme,
        path="/vnc/websockify",
        query="",
        fragment="",
    ).geturl()


def _resolve_sandbox_vnc_ws_url() -> str:
    return _env_or_default(
        "SANDBOX_VNC_WS_URL",
        _derive_sandbox_vnc_ws_url(_resolve_sandbox_base_url()),
    ).rstrip("/")


class Settings(BaseSettings):
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "local")
    if ENVIRONMENT == "local":
        load_dotenv(".env")
    _home_env_path = _resolve_home_env_path()
    if _home_env_path and _home_env_path.exists():
        load_dotenv(_home_env_path)

    model_ds_name: str = os.environ.get("DS_MODEL") or "deepseek-chat"
    model_ds_api_key: str = os.environ.get("DS_API_KEY") or ""
    model_ds_base_url: str = os.environ.get("DS_URL") or "https://api.deepseek.com/v1"
    max_tokens: int = int(os.environ.get("MAX_TOKENS", "100000"))
    context_window: int = int(os.environ.get("CONTEXT_WINDOW", "131072"))

    https_only: bool = os.environ.get("HTTPS_ONLY", "false").lower() == "true"
    session_cookie: str = os.environ.get("SESSION_COOKIE") or "zdtc-agent-session"
    session_max_age: int = int(os.environ.get("SESSION_MAX_AGE", str(3600 * 24 * 7)))

    auth_provider: str = _resolve_auth_provider()

    bootstrap_admin_enabled: bool = os.environ.get("BOOTSTRAP_ADMIN_ENABLED", "true").lower() == "true"
    bootstrap_admin_username: str = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin")
    bootstrap_admin_password: str = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "admin123")
    bootstrap_admin_fullname: str = os.environ.get("BOOTSTRAP_ADMIN_FULLNAME", "Administrator")
    bootstrap_admin_email: str = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@localhost")
    bootstrap_update_admin_password: bool = os.environ.get("BOOTSTRAP_UPDATE_ADMIN_PASSWORD", "false").lower() == "true"

    mongodb_host: str = os.environ.get("MONGODB_HOST", "localhost")
    mongodb_port: int = int(os.environ.get("MONGODB_PORT", "27014"))
    mongodb_db_name: str = os.environ.get("MONGODB_DB", "ai_agent")
    mongodb_username: str = os.environ.get("MONGODB_USER", "")
    mongodb_password: str = os.environ.get("MONGODB_PASSWORD", "")

    # Storage backend: "mongo" (cloud) or "local" (edge)
    storage_backend: str = os.environ.get("STORAGE_BACKEND", "mongo")
    local_path_style: str = os.environ.get("LOCAL_PATH_STYLE", "windows").strip().lower() or "windows"
    rpa_recording_debug_snapshot_dir: str = os.environ.get("RPA_RECORDING_DEBUG_SNAPSHOT_DIR", "")

    # ── RPA_CLAW_HOME: 统一根目录，子目录自动派生 ──
    # 本地后端使用 RPA_CLAW_HOME，沙箱内使用 SANDBOX_RPA_CLAW_HOME（默认 /home/rpaclaw）
    rpa_claw_home: str = _resolve_home()
    sandbox_rpa_claw_home: str = _resolve_sandbox_home()

    # 以下四个目录优先读取各自环境变量，未设置时从 rpa_claw_home 派生
    workspace_dir: str = _sub("WORKSPACE_DIR", _resolve_home(), "workspace", "/home/rpaclaw")
    external_skills_dir: str = _sub("EXTERNAL_SKILLS_DIR", _resolve_home(), "external_skills", "./Skills")
    builtin_skills_dir: str = _sub("BUILTIN_SKILLS_DIR", _resolve_home(), "builtin_skills", "./builtin_skills")
    local_data_dir: str = _sub("LOCAL_DATA_DIR", _resolve_home(), "data", "./data")
    tools_dir: str = _resolve_tools_dir()
    sandbox_tools_dir: str = _resolve_sandbox_tools_dir()
    system_mcp_config_path: str = _resolve_system_mcp_config_path()

    # 沙箱内 workspace 路径（与后端共享卷）
    sandbox_workspace_dir: str = _sub("SANDBOX_WORKSPACE_DIR", _resolve_sandbox_home(), "workspace", "/home/rpaclaw")

    xelatex_cmd: str = os.environ.get("XELATEX_CMD", "/usr/local/texlive/2025/bin/universal-darwin/xelatex")
    pandoc_cmd: str = os.environ.get("PANDOC_CMD", "/usr/local/bin/pandoc")

    # 沙盒服务主配置。未设置细项时，各类 URL 会从这里派生。
    sandbox_base_url: str = _resolve_sandbox_base_url()

    # 沙盒服务（MCP 协议）
    sandbox_mcp_url: str = _resolve_sandbox_mcp_url()

    # 后端代理 VNC 时使用的 WebSocket 地址。默认从 SANDBOX_BASE_URL 派生，可单独覆盖。
    sandbox_vnc_ws_url: str = _resolve_sandbox_vnc_ws_url()

    # 后端代理到沙盒/浏览器时附带的额外请求头，JSON 对象格式
    # 例如: {"Authorization":"Bearer xxx","X-API-Key":"yyy"}
    sandbox_proxy_headers: str = os.environ.get("SANDBOX_PROXY_HEADERS", "")

    # 任务调度服务调用聊天接口时的 API Key（可选）
    task_service_api_key: str = os.environ.get("TASK_SERVICE_API_KEY", "")
    credential_key: str = os.environ.get("CREDENTIAL_KEY", "")
    rpa_mcp_semantic_inference: bool = os.environ.get("RPA_MCP_SEMANTIC_INFERENCE", "true").lower() == "true"
    rpa_mcp_semantic_timeout_seconds: int = int(os.environ.get("RPA_MCP_SEMANTIC_TIMEOUT_SECONDS", "20"))
    runtime_mode: str = os.environ.get("RUNTIME_MODE", "shared")
    runtime_idle_ttl_seconds: int = int(os.environ.get("RUNTIME_IDLE_TTL_SECONDS", "3600"))
    runtime_image: str = os.environ.get("SESSION_SANDBOX_IMAGE", "rpaclaw-sandbox:local")
    runtime_service_port: int = int(os.environ.get("SESSION_SANDBOX_PORT", "8080"))
    runtime_wait_timeout_seconds: int = int(os.environ.get("RUNTIME_WAIT_TIMEOUT_SECONDS", "30"))
    docker_runtime_network: str = os.environ.get("DOCKER_RUNTIME_NETWORK", "rpaclaw_default")
    docker_runtime_volumes_from: str = os.environ.get("DOCKER_RUNTIME_VOLUMES_FROM", "")
    docker_runtime_shm_size: str = os.environ.get("DOCKER_RUNTIME_SHM_SIZE", "2gb")
    docker_runtime_mem_limit: str = os.environ.get("DOCKER_RUNTIME_MEM_LIMIT", "8g")
    docker_runtime_security_opt: str = os.environ.get("DOCKER_RUNTIME_SECURITY_OPT", "seccomp:unconfined")
    docker_runtime_extra_hosts: str = os.environ.get("DOCKER_RUNTIME_EXTRA_HOSTS", "host.docker.internal:host-gateway")
    k8s_namespace: str = os.environ.get("K8S_RUNTIME_NAMESPACE", "default")
    k8s_runtime_service_account: str = os.environ.get("K8S_RUNTIME_SERVICE_ACCOUNT", "")
    k8s_runtime_image_pull_policy: str = os.environ.get("K8S_RUNTIME_IMAGE_PULL_POLICY", "IfNotPresent")
    k8s_runtime_image_pull_secrets: str = os.environ.get("K8S_RUNTIME_IMAGE_PULL_SECRETS", "")
    k8s_runtime_node_selector: str = os.environ.get("K8S_RUNTIME_NODE_SELECTOR", "")
    k8s_runtime_env: str = os.environ.get("K8S_RUNTIME_ENV", "")
    k8s_runtime_labels: str = os.environ.get("K8S_RUNTIME_LABELS", "")
    k8s_runtime_annotations: str = os.environ.get("K8S_RUNTIME_ANNOTATIONS", "")
    k8s_runtime_cpu_request: str = os.environ.get("K8S_RUNTIME_CPU_REQUEST", "")
    k8s_runtime_cpu_limit: str = os.environ.get("K8S_RUNTIME_CPU_LIMIT", "")
    k8s_runtime_memory_request: str = os.environ.get("K8S_RUNTIME_MEMORY_REQUEST", "")
    k8s_runtime_memory_limit: str = os.environ.get("K8S_RUNTIME_MEMORY_LIMIT", "")
    k8s_runtime_tolerations_json: str = os.environ.get("K8S_RUNTIME_TOLERATIONS_JSON", "")
    k8s_runtime_workspace_volume_name: str = os.environ.get("K8S_RUNTIME_WORKSPACE_VOLUME_NAME", "workspace")
    k8s_runtime_workspace_mount_path: str = os.environ.get("K8S_RUNTIME_WORKSPACE_MOUNT_PATH", "/home/rpaclaw")
    k8s_runtime_workspace_pvc_claim: str = os.environ.get("K8S_RUNTIME_WORKSPACE_PVC_CLAIM", "")
    k8s_runtime_extra_volumes_json: str = os.environ.get("K8S_RUNTIME_EXTRA_VOLUMES_JSON", "")
    k8s_runtime_extra_volume_mounts_json: str = os.environ.get("K8S_RUNTIME_EXTRA_VOLUME_MOUNTS_JSON", "")

    # class Config:
    #     env_prefix = 'APP_'


# 全局配置实例
settings = Settings()

if settings.local_path_style not in {"windows", "posix"}:
    raise ValueError(
        f"Invalid LOCAL_PATH_STYLE: {settings.local_path_style}. Expected 'windows' or 'posix'."
    )
