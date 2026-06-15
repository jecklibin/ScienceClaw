from fastapi.testclient import TestClient

from backend.runtime.local_fake_aio_service import create_local_fake_aio_app


class _FakeContainer:
    id = "container-123"
    name = "rpaclaw-aio-sess-1"
    attrs = {"State": {"Status": "running"}}

    def remove(self, force=False):
        self.removed = force


class _FakeContainersApi:
    def __init__(self):
        self.run_calls = []
        self._container = _FakeContainer()

    def run(self, image, **kwargs):
        self.run_calls.append((image, kwargs))
        return self._container

    def get(self, name):
        return self._container


class _FakeDockerClient:
    def __init__(self):
        self.containers = _FakeContainersApi()


def test_local_fake_aio_create_starts_runtime_adapter_container():
    docker_client = _FakeDockerClient()
    app = create_local_fake_aio_app(
        docker_client=docker_client,
        image="rpaclaw-runtime-adapter:dev",
        adapter_host_port_start=18081,
        cdp_host_port_start=19222,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/sandboxes",
        json={"session_id": "sess-1", "user_id": "user-1"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["sandbox_id"].startswith("local-aio-")
    assert payload["status"] == "ready"
    assert payload["route_base_url"] == "http://127.0.0.1:18081"
    assert payload["browser_view_url"] == "http://127.0.0.1:18081/browser"
    assert len(docker_client.containers.run_calls) == 1
    image, kwargs = docker_client.containers.run_calls[0]
    assert image == "rpaclaw-runtime-adapter:dev"
    assert kwargs["detach"] is True
    assert kwargs["ports"] == {"8080/tcp": 18081, "9222/tcp": 19222}
    assert kwargs["environment"]["RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH"] == "true"
    assert kwargs["environment"]["RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL"] == "ws://127.0.0.1:19222"


def test_local_fake_aio_create_merges_provider_env_into_adapter_container():
    docker_client = _FakeDockerClient()
    app = create_local_fake_aio_app(docker_client=docker_client)
    client = TestClient(app)

    response = client.post(
        "/v1/sandboxes",
        json={
            "session_id": "sess-env",
            "env": {
                "RUNTIME_ADAPTER_TOKEN": "adapter-token",
                "IGNORED_HOST_ENV": "nope",
            },
        },
    )

    assert response.status_code == 200
    _, kwargs = docker_client.containers.run_calls[0]
    assert kwargs["environment"]["RUNTIME_ADAPTER_TOKEN"] == "adapter-token"
    assert "IGNORED_HOST_ENV" not in kwargs["environment"]


def test_local_fake_aio_delete_removes_container():
    docker_client = _FakeDockerClient()
    app = create_local_fake_aio_app(docker_client=docker_client)
    client = TestClient(app)
    created = client.post("/v1/sandboxes", json={"session_id": "sess-delete"}).json()["data"]

    response = client.delete(f"/v1/sandboxes/{created['sandbox_id']}")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    assert docker_client.containers._container.removed is True
