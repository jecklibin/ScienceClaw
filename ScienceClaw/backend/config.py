import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "local")
    if ENVIRONMENT == "local":
        load_dotenv(".env")

    model_ds_name: str = os.environ.get("DS_MODEL") or "deepseek-chat"
    model_ds_api_key: str = os.environ.get("DS_API_KEY") or ""
    model_ds_base_url: str = os.environ.get("DS_URL") or "https://api.deepseek.com/v1"
    max_tokens: int = int(os.environ.get("MAX_TOKENS", "100000"))
    context_window: int = int(os.environ.get("CONTEXT_WINDOW", "131072"))

    https_only: bool = os.environ.get("HTTPS_ONLY", "false").lower() == "true"
    session_cookie: str = os.environ.get("SESSION_COOKIE") or "zdtc-agent-session"
    session_max_age: int = int(os.environ.get("SESSION_MAX_AGE", str(3600 * 24 * 7)))

    auth_provider: str = os.environ.get("AUTH_PROVIDER", "local")

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
    local_data_dir: str = os.environ.get("LOCAL_DATA_DIR", "./data")

    # Skills directories
    external_skills_dir: str = os.environ.get("EXTERNAL_SKILLS_DIR", "./Skills")
    builtin_skills_dir: str = os.environ.get("BUILTIN_SKILLS_DIR", "./builtin_skills")

    xelatex_cmd: str = os.environ.get("XELATEX_CMD", "/usr/local/texlive/2025/bin/universal-darwin/xelatex")
    pandoc_cmd: str = os.environ.get("PANDOC_CMD", "/usr/local/bin/pandoc")

    # 沙盒服务（MCP 协议）
    sandbox_mcp_url: str = os.environ.get("SANDBOX_MCP_URL", "http://sandbox:8080/mcp")

    # 前端可访问的沙盒地址（分离部署时需配置，默认空表示与前端同 host）
    sandbox_public_url: str = os.environ.get("SANDBOX_PUBLIC_URL", "")

    # 后端代理 VNC 时使用的 WebSocket 地址（可选）。
    # 未配置时会根据 SANDBOX_MCP_URL 自动推导。
    sandbox_vnc_ws_url: str = os.environ.get("SANDBOX_VNC_WS_URL", "")

    # 后端代理到沙盒/浏览器时附带的额外请求头，JSON 对象格式
    # 例如: {"Authorization":"Bearer xxx","X-API-Key":"yyy"}
    sandbox_proxy_headers: str = os.environ.get("SANDBOX_PROXY_HEADERS", "")

    # 任务调度服务调用聊天接口时的 API Key（可选）
    task_service_api_key: str = os.environ.get("TASK_SERVICE_API_KEY", "")
    credential_key: str = os.environ.get("CREDENTIAL_KEY", "")
    runtime_mode: str = os.environ.get("RUNTIME_MODE", "shared")
    runtime_idle_ttl_seconds: int = int(os.environ.get("RUNTIME_IDLE_TTL_SECONDS", "3600"))
    runtime_image: str = os.environ.get("SESSION_SANDBOX_IMAGE", "scienceclaw-sandbox:local")
    runtime_service_port: int = int(os.environ.get("SESSION_SANDBOX_PORT", "8080"))
    runtime_wait_timeout_seconds: int = int(os.environ.get("RUNTIME_WAIT_TIMEOUT_SECONDS", "30"))
    docker_runtime_network: str = os.environ.get("DOCKER_RUNTIME_NETWORK", "scienceclaw_default")
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
    k8s_runtime_workspace_mount_path: str = os.environ.get("K8S_RUNTIME_WORKSPACE_MOUNT_PATH", "/home/scienceclaw")
    k8s_runtime_workspace_pvc_claim: str = os.environ.get("K8S_RUNTIME_WORKSPACE_PVC_CLAIM", "")
    k8s_runtime_extra_volumes_json: str = os.environ.get("K8S_RUNTIME_EXTRA_VOLUMES_JSON", "")
    k8s_runtime_extra_volume_mounts_json: str = os.environ.get("K8S_RUNTIME_EXTRA_VOLUME_MOUNTS_JSON", "")
    shared_sandbox_rest_url: str = os.environ.get("SHARED_SANDBOX_REST_URL", "http://sandbox:8080")

    # class Config:
    #     env_prefix = 'APP_'


# 全局配置实例
settings = Settings()
