"""
FastAPI 应用入口 — 精简版。

挂载路由：auth / models / sessions / file / rpa / chat / statistics
启动时：连接 MongoDB → 初始化系统模型 → 创建默认 admin
"""
import asyncio
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from contextlib import asynccontextmanager

from backend.storage import init_storage, close_storage, get_repository
from backend.route.auth import router as auth_router
from backend.route.sessions import router as sessions_router, cleanup_orphaned_sessions, graceful_shutdown_agents
from backend.route.file import router as file_router
from backend.route.models import router as models_router
from backend.route.task_settings import router as task_settings_router
from backend.route.memory import router as memory_router
from backend.route.chat import router as chat_router
from backend.route.statistics import router as statistics_router
from backend.route.rpa import router as rpa_router
from backend.route.credential import router as credential_router
from backend.route.mcp import router as mcp_router
from backend.route.rpa_mcp import router as rpa_mcp_router
from backend.route.runtime_proxy import router as runtime_proxy_router
from backend.runtime.session_runtime_manager import get_session_runtime_manager
from backend.models import init_system_models
from backend.user.bootstrap import ensure_admin_user


async def _runtime_cleanup_loop(stop_event: asyncio.Event) -> None:
    from backend.config import settings

    interval_seconds = max(30, min(settings.runtime_idle_ttl_seconds // 2, 300))
    manager = get_session_runtime_manager()

    while not stop_event.is_set():
        try:
            cleaned = await manager.cleanup_expired()
            if cleaned:
                logger.info(f"Cleaned up {cleaned} expired runtime(s)")
        except Exception as exc:
            logger.error(f"Failed to cleanup expired runtimes: {exc}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime_cleanup_stop = asyncio.Event()
    runtime_cleanup_task: asyncio.Task | None = None
    await init_storage()
    try:
        await init_system_models()
    except Exception as e:
        logger.error(f"Failed to init system models: {e}")
    try:
        await ensure_admin_user()
    except Exception as e:
        logger.error(f"Failed to bootstrap admin user: {e}")
    try:
        await cleanup_orphaned_sessions()
    except Exception as e:
        logger.error(f"Failed to cleanup orphaned sessions: {e}")
    try:
        cleaned = await get_session_runtime_manager().cleanup_orphans()
        if cleaned:
            logger.info(f"Cleaned up {cleaned} orphaned runtime(s) on startup")
    except Exception as e:
        logger.error(f"Failed to cleanup orphaned runtimes: {e}")
    runtime_cleanup_task = asyncio.create_task(_runtime_cleanup_loop(runtime_cleanup_stop))
    yield
    runtime_cleanup_stop.set()
    if runtime_cleanup_task is not None:
        try:
            await runtime_cleanup_task
        except Exception as e:
            logger.error(f"Failed to stop runtime cleanup loop: {e}")
    try:
        await graceful_shutdown_agents()
    except Exception as e:
        logger.error(f"Failed to gracefully shutdown agents: {e}")
    try:
        cleaned = await get_session_runtime_manager().cleanup_orphans()
        if cleaned:
            logger.info(f"Cleaned up {cleaned} runtime(s) on shutdown")
    except Exception as e:
        logger.error(f"Failed to cleanup runtimes on shutdown: {e}")
    try:
        from backend.rpa.cdp_connector import cdp_connector
        await cdp_connector.close()
    except Exception as e:
        logger.error(f"Failed to close CDP connector: {e}")
    await close_storage()


def create_app() -> FastAPI:
    app = FastAPI(title="RpaClaw Agent Backend", lifespan=lifespan)

    cors_origins = [
        o.strip()
        for o in os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        try:
            repo = get_repository("sessions")
            await repo.find_one({})
            return {"status": "ready", "storage": "ok"}
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "storage": str(exc)},
            )

    @app.get("/api/v1/client-config")
    async def client_config():
        """Return configuration needed by the frontend."""
        from backend.config import settings
        return {
            "sandbox_public_url": settings.sandbox_base_url or "",
            "storage_backend": settings.storage_backend,
        }

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    app.include_router(file_router, prefix="/api/v1")
    app.include_router(models_router, prefix="/api/v1")
    app.include_router(task_settings_router, prefix="/api/v1")
    app.include_router(memory_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(statistics_router, prefix="/api/v1")
    app.include_router(rpa_router, prefix="/api/v1/rpa")
    app.include_router(runtime_proxy_router, prefix="/api/v1")
    app.include_router(credential_router, prefix="/api/v1")
    app.include_router(mcp_router, prefix="/api/v1")
    app.include_router(rpa_mcp_router, prefix="/api/v1")

    logger.info("FastAPI initialized with /api/v1 endpoints")
    return app


def resolve_frontend_dist_dir(module_file: str | None = None) -> str | None:
    frontend_dist = os.environ.get("FRONTEND_DIST_DIR")
    if frontend_dist and os.path.exists(frontend_dist):
        return frontend_dist

    current_file = Path(module_file or __file__).resolve()
    fallback_dir = current_file.parent.parent / "frontend-dist"
    if fallback_dir.exists():
        return str(fallback_dir)

    return None


app = create_app()

# Serve frontend static files (for Electron packaged app)
from fastapi.staticfiles import StaticFiles

frontend_dist = resolve_frontend_dist_dir()
if frontend_dist:
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    logger.info(f"Serving frontend static files from: {frontend_dist}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
