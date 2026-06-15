from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
ADAPTER_DIR = REPO_ROOT / "RpaClaw" / "runtime-adapter"


def test_runtime_adapter_image_dockerfile_defines_adapter_entrypoint_and_healthcheck():
    dockerfile = (ADAPTER_DIR / "Dockerfile").read_text(encoding="utf-8")

    assert "chromium" in dockerfile
    assert "python -m backend.runtime.adapter_app --self-check" in dockerfile
    assert "backend.runtime.adapter_app:app" in dockerfile
    assert "RUNTIME_ADAPTER_WORKSPACE_ROOT" in dockerfile
    assert "RUNTIME_ADAPTER_DOWNLOADS_DIR" in dockerfile
    assert "RUNTIME_ADAPTER_VERSION" in dockerfile
    assert "RUNTIME_ADAPTER_TOKEN" in dockerfile
    assert "RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH" in dockerfile
    assert "RUNTIME_ADAPTER_BROWSER_EXECUTABLE" in dockerfile
    assert "RUNTIME_ADAPTER_BROWSER_DEBUG_PORT" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "backend.main:app" not in dockerfile


def test_runtime_adapter_image_readme_records_local_build_and_aio_env_contract():
    readme = (ADAPTER_DIR / "README.md").read_text(encoding="utf-8")

    assert "docker build" in readme
    assert "rpaclaw-runtime-adapter:dev" in readme
    assert "RUNTIME_ADAPTER_WORKSPACE_ROOT" in readme
    assert "RUNTIME_ADAPTER_TOKEN" in readme
    assert "RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH" in readme
    assert "RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_URL" in readme
    assert "python -m backend.runtime.adapter_app --self-check" in readme
    assert "AIO_RUNTIME_IMAGE" in readme
    assert "AIO_RUNTIME_ADAPTER_ENV" in readme
    assert "Host Backend" in readme
