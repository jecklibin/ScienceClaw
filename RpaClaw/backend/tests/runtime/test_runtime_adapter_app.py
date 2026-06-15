from fastapi.testclient import TestClient

from backend.runtime import adapter_app
from backend.runtime.adapter_app import create_runtime_adapter_app, create_runtime_adapter_app_from_env


def test_runtime_adapter_health_reports_local_surface(tmp_path):
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
            cdp_url="ws://127.0.0.1:9222/devtools/browser/local",
            browser_view_url="http://127.0.0.1:6080/vnc.html",
            adapter_token="session-token",
            downloads_dir="downloads",
        )
    )

    response = client.get("/health", headers={"Authorization": "Bearer session-token"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "surface": "runtime-adapter",
        "contract_version": "v1",
        "mode": "local",
        "capabilities": {
            "browser_info": True,
            "browser_launch": False,
            "execute_step": True,
            "run_skill": True,
            "downloads": True,
            "files": True,
        },
        "config": {
            "workspace_root": str(tmp_path.resolve()),
            "downloads_dir": "downloads",
            "adapter_version": "local-dev",
            "browser_view_url": "http://127.0.0.1:6080/vnc.html",
            "file_policy": {
                "max_inline_file_write_bytes": 10 * 1024 * 1024,
                "max_file_download_bytes": 50 * 1024 * 1024,
                "oversized_hash_status": "skipped_oversized",
            },
            "token_required": True,
            "issues": [],
        },
    }


def test_runtime_adapter_health_reports_degraded_for_invalid_downloads_dir(tmp_path):
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            downloads_dir="../outside",
        )
    )

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["capabilities"]["downloads"] is False
    assert payload["config"]["issues"] == [
        "downloads_dir is outside adapter workspace",
    ]


def test_runtime_adapter_health_reports_degraded_for_file_workspace_root(tmp_path):
    workspace_file = tmp_path / "workspace-file"
    workspace_file.write_text("not a directory", encoding="utf-8")
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=workspace_file,
            enable_local_execution=True,
        )
    )

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["capabilities"] == {
        "browser_info": False,
        "browser_launch": False,
        "execute_step": False,
        "run_skill": False,
        "downloads": False,
        "files": False,
    }
    assert payload["config"]["issues"] == [
        "workspace_root is not a directory",
    ]


def test_runtime_adapter_file_endpoints_reject_unusable_workspace_root(tmp_path):
    workspace_file = tmp_path / "workspace-file"
    workspace_file.write_text("not a directory", encoding="utf-8")
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=workspace_file,
            enable_local_execution=True,
        )
    )

    assert client.get("/files/list").status_code == 503
    assert client.post("/files/write", json={"path": "x.txt", "content": "x"}).status_code == 503
    assert client.get("/files/download", params={"path": "x.txt"}).status_code == 503
    assert client.get("/rpa/downloads").status_code == 503
    assert client.post("/rpa/execute-step", json={"command": ["python", "--version"]}).status_code == 503
    assert client.post("/rpa/run-skill", json={"skill_path": "skills/demo"}).status_code == 503


def test_runtime_adapter_token_rejects_missing_and_accepts_bearer(tmp_path):
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            adapter_token="session-token",
        )
    )

    missing_response = client.get("/health")
    wrong_response = client.get("/health", headers={"Authorization": "Bearer wrong"})
    ok_response = client.get("/health", headers={"Authorization": "Bearer session-token"})

    assert missing_response.status_code == 401
    assert missing_response.json()["detail"] == "Missing runtime adapter bearer token"
    assert wrong_response.status_code == 403
    assert wrong_response.json()["detail"] == "Invalid runtime adapter bearer token"
    assert ok_response.status_code == 200


def test_runtime_adapter_app_from_env_configures_local_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_ADAPTER_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RUNTIME_ADAPTER_ENABLE_LOCAL_EXECUTION", "true")
    monkeypatch.setenv("RUNTIME_ADAPTER_CDP_URL", "ws://127.0.0.1:9222/devtools/browser/env")
    monkeypatch.setenv("RUNTIME_ADAPTER_BROWSER_VIEW_URL", "http://127.0.0.1:6080/env.html")
    monkeypatch.setenv("RUNTIME_ADAPTER_VERSION", "adapter-test-version")
    monkeypatch.setenv("RUNTIME_ADAPTER_TOKEN", "env-token")
    script = tmp_path / "skill.py"
    script.write_text("print('env-ready')", encoding="utf-8")
    client = TestClient(create_runtime_adapter_app_from_env())
    headers = {"Authorization": "Bearer env-token"}

    unauthorized_response = client.get("/v1/browser/info")
    browser_response = client.get("/v1/browser/info", headers=headers)
    skill_response = client.post("/rpa/run-skill", json={"skill_path": "skill.py"}, headers=headers)

    assert unauthorized_response.status_code == 401
    assert browser_response.status_code == 200
    assert browser_response.json()["data"] == {
        "cdp_url": "ws://127.0.0.1:9222/devtools/browser/env",
        "browser_view_url": "http://127.0.0.1:6080/env.html",
    }
    assert skill_response.status_code == 200
    assert skill_response.json()["stdout"].strip() == "env-ready"
    assert client.get("/health", headers=headers).json()["config"]["adapter_version"] == "adapter-test-version"


def test_runtime_adapter_self_check_cli_prints_sanitized_health(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNTIME_ADAPTER_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("RUNTIME_ADAPTER_ENABLE_LOCAL_EXECUTION", "true")
    monkeypatch.setenv("RUNTIME_ADAPTER_CDP_URL", "ws://127.0.0.1:9222/devtools/browser/self-check")
    monkeypatch.setenv("RUNTIME_ADAPTER_BROWSER_VIEW_URL", "http://127.0.0.1:6080/self-check.html")
    monkeypatch.setenv("RUNTIME_ADAPTER_VERSION", "adapter-self-check")
    monkeypatch.setenv("RUNTIME_ADAPTER_TOKEN", "self-check-secret")

    exit_code = adapter_app.main(["--self-check"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"status": "ok"' in output
    assert '"contract_version": "v1"' in output
    assert '"adapter_version": "adapter-self-check"' in output
    assert '"token_required": true' in output
    assert "self-check-secret" not in output


def test_runtime_adapter_self_check_cli_returns_nonzero_for_degraded_env(tmp_path, monkeypatch, capsys):
    workspace_file = tmp_path / "workspace-file"
    workspace_file.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("RUNTIME_ADAPTER_WORKSPACE_ROOT", str(workspace_file))
    monkeypatch.setenv("RUNTIME_ADAPTER_TOKEN", "degraded-secret")

    exit_code = adapter_app.main(["--self-check"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"status": "degraded"' in output
    assert "workspace_root is not a directory" in output
    assert "degraded-secret" not in output


def test_runtime_adapter_recording_contract_uses_cursor(tmp_path):
    client = TestClient(create_runtime_adapter_app(workspace_root=tmp_path))

    start_response = client.post("/rpa/recording/start", json={"session_id": "sess-1"})
    first_events_response = client.get("/rpa/events", params={"cursor": "0"})
    second_events_response = client.get(
        "/rpa/events",
        params={"cursor": first_events_response.json()["next_cursor"]},
    )
    stop_response = client.post("/rpa/recording/stop", json={"session_id": "sess-1"})
    stop_events_response = client.get(
        "/rpa/events",
        params={"cursor": first_events_response.json()["next_cursor"]},
    )

    assert start_response.status_code == 200
    assert start_response.json()["status"] == "recording"
    assert first_events_response.status_code == 200
    assert first_events_response.json() == {
        "events": [
            {
                "cursor": "1",
                "type": "recording_started",
                "session_id": "sess-1",
            }
        ],
        "next_cursor": "1",
    }
    assert second_events_response.status_code == 200
    assert second_events_response.json() == {"events": [], "next_cursor": "1"}
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "stopped"
    assert stop_events_response.status_code == 200
    assert stop_events_response.json() == {
        "events": [
            {
                "cursor": "2",
                "type": "recording_stopped",
                "session_id": "sess-1",
            }
        ],
        "next_cursor": "2",
    }


def test_runtime_adapter_can_emit_local_raw_recording_event(tmp_path):
    client = TestClient(create_runtime_adapter_app(workspace_root=tmp_path))
    client.post("/rpa/recording/start", json={"session_id": "sess-raw"})

    emitted = client.post(
        "/rpa/events/emit",
        json={
            "event_id": "evt-click-1",
            "action": "click",
            "locator": {"role": "button", "name": "Search"},
            "signals": {"url": "https://example.test/search"},
        },
    )
    events = client.get("/rpa/events", params={"cursor": "1"})

    assert emitted.status_code == 200
    assert emitted.json() == {
        "cursor": "2",
        "type": "raw_event",
        "event_id": "evt-click-1",
        "action": "click",
        "locator": {"role": "button", "name": "Search"},
        "signals": {"url": "https://example.test/search"},
    }
    assert events.status_code == 200
    assert events.json() == {
        "events": [emitted.json()],
        "next_cursor": "2",
    }


def test_runtime_adapter_returns_safe_empty_snapshot(tmp_path):
    client = TestClient(create_runtime_adapter_app(workspace_root=tmp_path))

    response = client.get("/rpa/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["raw_snapshot"] == {}
    assert payload["compact_snapshot"] == {}
    assert payload["page_state"] == {
        "url": None,
        "title": None,
    }


def test_runtime_adapter_can_emit_local_snapshot(tmp_path):
    client = TestClient(create_runtime_adapter_app(workspace_root=tmp_path))
    snapshot = {
        "raw_snapshot": {"html": "<button>Search</button>"},
        "compact_snapshot": {"buttons": [{"text": "Search"}]},
        "page_state": {
            "url": "https://example.test/search",
            "title": "Search",
        },
    }

    emitted = client.post("/rpa/snapshot/emit", json=snapshot)
    response = client.get("/rpa/snapshot")

    assert emitted.status_code == 200
    assert emitted.json() == snapshot
    assert response.status_code == 200
    assert response.json() == snapshot


def test_runtime_adapter_browser_info_returns_configured_cdp_url(tmp_path):
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            cdp_url="ws://127.0.0.1:9222/devtools/browser/local",
            browser_view_url="http://127.0.0.1:6080/vnc.html",
        )
    )

    response = client.get("/v1/browser/info")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "data": {
            "cdp_url": "ws://127.0.0.1:9222/devtools/browser/local",
            "browser_view_url": "http://127.0.0.1:6080/vnc.html",
        },
    }


def test_runtime_adapter_browser_info_reports_missing_without_cdp(tmp_path):
    client = TestClient(create_runtime_adapter_app(workspace_root=tmp_path))

    response = client.get("/v1/browser/info")

    assert response.status_code == 503
    assert response.json()["detail"] == "CDP browser is not configured"


def test_runtime_adapter_browser_info_can_launch_local_browser(tmp_path, monkeypatch):
    class _FakeBrowserController:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def ensure_browser(self):
            return {
                "cdp_url": "ws://127.0.0.1:9222/devtools/browser/fake",
                "browser_view_url": "http://127.0.0.1:9222",
                "listener": {
                    "status": "injected",
                    "marker": "__rpaclawRuntimeAdapterListener",
                },
            }

    monkeypatch.setattr(adapter_app, "LocalBrowserController", _FakeBrowserController)
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_browser_launch=True,
            browser_cdp_public_base_url="ws://127.0.0.1:9222",
            browser_view_url="http://127.0.0.1:9222",
        )
    )

    response = client.get("/v1/browser/info")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "data": {
            "cdp_url": "ws://127.0.0.1:9222/devtools/browser/fake",
            "browser_view_url": "http://127.0.0.1:9222",
            "listener": {
                "status": "injected",
                "marker": "__rpaclawRuntimeAdapterListener",
            },
        },
    }


def test_runtime_adapter_file_contract_is_workspace_scoped(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    report = root / "report.txt"
    report.write_text("hello-aio", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    client = TestClient(create_runtime_adapter_app(workspace_root=root))

    list_response = client.get("/files/list", params={"path": "."})
    download_response = client.get("/files/download", params={"path": "report.txt"})
    blocked_response = client.get("/files/download", params={"path": str(outside)})

    assert list_response.status_code == 200
    assert list_response.json() == {
        "path": ".",
        "entries": [
            {
                "name": "report.txt",
                "path": "report.txt",
                "type": "file",
                "size": 9,
            }
        ],
    }
    assert download_response.status_code == 200
    assert download_response.content == b"hello-aio"
    assert blocked_response.status_code == 403


def test_runtime_adapter_download_file_rejects_oversized_file(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    report = root / "report.bin"
    report.write_bytes(b"abcd")
    monkeypatch.setattr(adapter_app, "MAX_FILE_DOWNLOAD_BYTES", 3)
    client = TestClient(create_runtime_adapter_app(workspace_root=root))

    response = client.get("/files/download", params={"path": "report.bin"})

    assert response.status_code == 413
    assert response.json()["detail"] == "file exceeds adapter download limit"


def test_runtime_adapter_write_file_is_workspace_scoped(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    client = TestClient(create_runtime_adapter_app(workspace_root=root))

    write_response = client.post(
        "/files/write",
        json={"path": "skills/demo/skill.py", "content": "print('ok')"},
    )
    blocked_response = client.post(
        "/files/write",
        json={"path": str(outside), "content": "nope"},
    )

    assert write_response.status_code == 200
    assert write_response.json() == {
        "status": "success",
        "path": "skills/demo/skill.py",
        "size": 11,
    }
    assert (root / "skills" / "demo" / "skill.py").read_text(encoding="utf-8") == "print('ok')"
    assert blocked_response.status_code == 403


def test_runtime_adapter_write_file_accepts_base64_content(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    client = TestClient(create_runtime_adapter_app(workspace_root=root))

    write_response = client.post(
        "/files/write",
        json={"path": "inputs/blob.bin", "content_base64": "AAFi"},
    )
    invalid_response = client.post(
        "/files/write",
        json={"path": "inputs/bad.bin", "content_base64": "not-valid-***"},
    )

    assert write_response.status_code == 200
    assert write_response.json() == {
        "status": "success",
        "path": "inputs/blob.bin",
        "size": 3,
    }
    assert (root / "inputs" / "blob.bin").read_bytes() == b"\x00\x01b"
    assert invalid_response.status_code == 400
    assert invalid_response.json()["detail"] == "content_base64 is invalid"


def test_runtime_adapter_write_file_rejects_oversized_content(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    client = TestClient(create_runtime_adapter_app(workspace_root=root))
    oversized_content = "x" * (10 * 1024 * 1024 + 1)

    response = client.post(
        "/files/write",
        json={"path": "uploads/oversized.txt", "content": oversized_content},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "file content exceeds adapter write limit"
    assert not (root / "uploads").exists()


def test_runtime_adapter_lists_workspace_downloads(tmp_path):
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    (downloads_dir / "report.pdf").write_bytes(b"%PDF-local")
    nested = downloads_dir / "nested"
    nested.mkdir()
    (nested / "ignored.txt").write_text("nested", encoding="utf-8")
    client = TestClient(create_runtime_adapter_app(workspace_root=tmp_path))

    response = client.get("/rpa/downloads")

    assert response.status_code == 200
    assert response.json() == {
        "downloads": [
            {
                "name": "report.pdf",
                "path": "downloads/report.pdf",
                "sha256": "e886634ef385beefe73352c7cb07f5f9594117e0ae689bdc9e79fcaf573aa280",
                "size": 10,
            }
        ]
    }


def test_runtime_adapter_lists_oversized_download_without_hashing(tmp_path, monkeypatch):
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    (downloads_dir / "large.bin").write_bytes(b"abcd")
    monkeypatch.setattr(adapter_app, "MAX_FILE_DOWNLOAD_BYTES", 3)
    client = TestClient(create_runtime_adapter_app(workspace_root=tmp_path))

    response = client.get("/rpa/downloads")

    assert response.status_code == 200
    assert response.json() == {
        "downloads": [
            {
                "name": "large.bin",
                "path": "downloads/large.bin",
                "sha256": None,
                "size": 4,
                "hash_status": "skipped_oversized",
            }
        ]
    }


def test_runtime_adapter_execution_endpoints_are_explicit_stubs(tmp_path):
    client = TestClient(create_runtime_adapter_app(workspace_root=tmp_path))

    execute_response = client.post("/rpa/execute-step", json={"code": "print('x')"})
    skill_response = client.post("/rpa/run-skill", json={"skill": "demo"})
    downloads_response = client.get("/rpa/downloads")

    assert execute_response.status_code == 200
    assert execute_response.json()["status"] == "not_implemented"
    assert skill_response.status_code == 200
    assert skill_response.json()["status"] == "not_implemented"
    assert downloads_response.status_code == 200
    assert downloads_response.json() == {"downloads": []}


def test_runtime_adapter_execute_step_runs_enabled_workspace_command(tmp_path):
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )

    response = client.post(
        "/rpa/execute-step",
        json={
            "command": [
                "python",
                "-c",
                "from pathlib import Path; Path('out.txt').write_text('done', encoding='utf-8'); print('ok')",
            ],
            "cwd": ".",
            "timeout_seconds": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["exit_code"] == 0
    assert payload["stdout"].strip() == "ok"
    assert payload["stderr"] == ""
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "done"


def test_runtime_adapter_execute_step_bounds_stdout_and_stderr(tmp_path):
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )

    response = client.post(
        "/rpa/execute-step",
        json={
            "command": [
                "python",
                "-c",
                "import sys; print('o' * 5000); print('e' * 5000, file=sys.stderr)",
            ],
            "timeout_seconds": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert len(payload["stdout"]) <= 4096
    assert len(payload["stderr"]) <= 4096
    assert payload["stdout_truncated"] is True
    assert payload["stderr_truncated"] is True


def test_runtime_adapter_execute_step_scrubs_sensitive_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_ADAPTER_TOKEN", "adapter-secret")
    monkeypatch.setenv("AIO_RUNTIME_API_TOKEN", "aio-secret")
    monkeypatch.setenv("HOST_AUTHORIZATION", "Bearer host-secret")
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )

    response = client.post(
        "/rpa/execute-step",
        json={
            "command": [
                "python",
                "-c",
                (
                    "import os; "
                    "print('|'.join(str(os.environ.get(key)) for key in "
                    "['RUNTIME_ADAPTER_TOKEN','AIO_RUNTIME_API_TOKEN','HOST_AUTHORIZATION','PYTHONIOENCODING']))"
                ),
            ],
            "timeout_seconds": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["stdout"].strip() == "None|None|None|utf-8"


def test_runtime_adapter_execute_step_returns_before_after_snapshot_evidence(tmp_path):
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )
    before_snapshot = {
        "raw_snapshot": {"html": "<button>Before</button>"},
        "compact_snapshot": {"buttons": [{"text": "Before"}]},
        "page_state": {"url": "https://example.test/before", "title": "Before"},
    }
    after_snapshot = {
        "raw_snapshot": {"html": "<button>After</button>"},
        "compact_snapshot": {"buttons": [{"text": "After"}]},
        "page_state": {"url": "https://example.test/after", "title": "After"},
    }
    client.post("/rpa/snapshot/emit", json=before_snapshot)

    response = client.post(
        "/rpa/execute-step",
        json={
            "command": ["python", "-c", "print('step-ok')"],
            "after_snapshot": after_snapshot,
        },
    )
    current_snapshot = client.get("/rpa/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["before_snapshot"] == before_snapshot
    assert payload["after_snapshot"] == after_snapshot
    assert current_snapshot.json() == after_snapshot


def test_runtime_adapter_execute_step_rejects_cwd_outside_workspace(tmp_path):
    outside = tmp_path.parent
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )

    response = client.post(
        "/rpa/execute-step",
        json={
            "command": ["python", "-c", "print('nope')"],
            "cwd": str(outside),
        },
    )

    assert response.status_code == 403


def test_runtime_adapter_run_skill_executes_enabled_workspace_skill(tmp_path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import argparse",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--name')",
                "args = parser.parse_args()",
                "Path('result.txt').write_text(args.name, encoding='utf-8')",
                "print(f'hello {args.name}')",
            ]
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )

    response = client.post(
        "/rpa/run-skill",
        json={
            "skill_path": "skills/demo",
            "args": ["--name", "Ada"],
            "timeout_seconds": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["exit_code"] == 0
    assert payload["stdout"].strip() == "hello Ada"
    assert payload["skill_path"] == "skills/demo/skill.py"
    assert (skill_dir / "result.txt").read_text(encoding="utf-8") == "Ada"


def test_runtime_adapter_run_skill_scrubs_sensitive_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_ADAPTER_TOKEN", "adapter-secret")
    monkeypatch.setenv("AIO_RUNTIME_API_TOKEN", "aio-secret")
    monkeypatch.setenv("HOST_AUTHORIZATION", "Bearer host-secret")
    skill_dir = tmp_path / "skills" / "env"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").write_text(
        "\n".join(
            [
                "import os",
                "keys = ['RUNTIME_ADAPTER_TOKEN', 'AIO_RUNTIME_API_TOKEN', 'HOST_AUTHORIZATION', 'PYTHONIOENCODING']",
                "print('|'.join(str(os.environ.get(key)) for key in keys))",
            ]
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )

    response = client.post(
        "/rpa/run-skill",
        json={
            "skill_path": "skills/env",
            "timeout_seconds": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["stdout"].strip() == "None|None|None|utf-8"


def test_runtime_adapter_run_skill_returns_before_after_snapshot_evidence(tmp_path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").write_text("print('skill-step-ok')", encoding="utf-8")
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )
    before_snapshot = {
        "raw_snapshot": {"html": "<main>Before Skill</main>"},
        "compact_snapshot": {"headings": ["Before Skill"]},
        "page_state": {"url": "https://example.test/skill-before", "title": "Before Skill"},
    }
    after_snapshot = {
        "raw_snapshot": {"html": "<main>After Skill</main>"},
        "compact_snapshot": {"headings": ["After Skill"]},
        "page_state": {"url": "https://example.test/skill-after", "title": "After Skill"},
    }
    client.post("/rpa/snapshot/emit", json=before_snapshot)

    response = client.post(
        "/rpa/run-skill",
        json={
            "skill_path": "skills/demo",
            "after_snapshot": after_snapshot,
        },
    )
    current_snapshot = client.get("/rpa/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["before_snapshot"] == before_snapshot
    assert payload["after_snapshot"] == after_snapshot
    assert current_snapshot.json() == after_snapshot


def test_runtime_adapter_run_skill_rejects_skill_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside_skill.py"
    outside.write_text("print('nope')", encoding="utf-8")
    client = TestClient(
        create_runtime_adapter_app(
            workspace_root=tmp_path,
            enable_local_execution=True,
        )
    )

    response = client.post(
        "/rpa/run-skill",
        json={
            "skill_path": str(outside),
        },
    )

    assert response.status_code == 403
