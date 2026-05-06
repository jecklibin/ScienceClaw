import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_auth_provider(extra_env: dict[str, str]) -> str:
    env = os.environ.copy()
    for key in ("AUTH_PROVIDER", "STORAGE_BACKEND", "ENVIRONMENT"):
        env.pop(key, None)
    env.update(extra_env)
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend.config import settings; print(settings.auth_provider)",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def test_local_storage_defaults_to_no_auth_admin_mode():
    assert read_auth_provider({"ENVIRONMENT": "production", "STORAGE_BACKEND": "local"}) == "none"


def test_explicit_local_auth_provider_is_preserved():
    assert (
        read_auth_provider(
            {
                "ENVIRONMENT": "production",
                "STORAGE_BACKEND": "local",
                "AUTH_PROVIDER": "local",
            }
        )
        == "local"
    )


def test_non_local_storage_keeps_login_auth_default():
    assert read_auth_provider({"ENVIRONMENT": "production", "STORAGE_BACKEND": "mongo"}) == "local"
