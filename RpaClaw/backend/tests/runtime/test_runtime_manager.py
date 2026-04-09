from backend.runtime.models import SessionRuntimeRecord
from backend.runtime.docker_runtime_provider import DockerRuntimeProvider
from backend.runtime.k8s_runtime_provider import K8sRuntimeProvider
from backend.runtime.provider import build_runtime_provider
from backend.runtime.shared_runtime_provider import SharedRuntimeProvider
from backend.runtime.session_runtime_manager import (
    SessionRuntimeManager,
    get_session_runtime_manager,
    reset_session_runtime_manager,
)
import hashlib
import pytest


def test_runtime_record_roundtrip_defaults():
    record = SessionRuntimeRecord(
        session_id="sess-1",
        user_id="user-1",
        namespace="beta",
        pod_name="rpaclaw-sess-sess1",
        service_name="rpaclaw-sess-sess1-svc",
        rest_base_url="http://rpaclaw-sess-sess1-svc:8080",
        status="creating",
    )

    payload = record.model_dump()

    assert payload["session_id"] == "sess-1"
    assert payload["service_name"].endswith("-svc")
    assert payload["status"] == "creating"
    assert "created_at" in payload
    assert "last_used_at" in payload


class _Settings:
    def __init__(self, runtime_mode: str):
        self.runtime_mode = runtime_mode
        self.runtime_idle_ttl_seconds = 3600
        self.runtime_wait_timeout_seconds = 0


def test_provider_factory_returns_shared_provider_when_requested():
    provider = build_runtime_provider(_Settings("shared"))
    assert provider.__class__.__name__ == "SharedRuntimeProvider"


def test_provider_factory_returns_docker_provider_when_requested():
    provider = build_runtime_provider(_Settings("docker"))
    assert provider.__class__.__name__ == "DockerRuntimeProvider"


def test_provider_factory_returns_k8s_provider_when_requested():
    provider = build_runtime_provider(_Settings("session_pod"))
    assert provider.__class__.__name__ == "K8sRuntimeProvider"


@pytest.mark.asyncio
async def test_shared_runtime_provider_derives_rest_base_from_sandbox_mcp_url_when_env_not_set(monkeypatch):
    monkeypatch.delenv("SHARED_SANDBOX_REST_URL", raising=False)

    settings = _Settings("shared")
    settings.sandbox_mcp_url = "http://7.199.171.198:8080/mcp"
    settings.shared_sandbox_rest_url = "http://sandbox:8080"
    settings.k8s_namespace = "default"

    runtime = await SharedRuntimeProvider(settings).create_runtime("sess-1", "user-1")

    assert runtime.rest_base_url == "http://7.199.171.198:8080"


class _FakeProvider:
    def __init__(self):
        self.create_calls = []
        self.delete_calls = []
        self.refresh_calls = []

    async def create_runtime(self, session_id: str, user_id: str):
        self.create_calls.append((session_id, user_id))
        return SessionRuntimeRecord(
            session_id=session_id,
            user_id=user_id,
            namespace="beta",
            pod_name=f"rpaclaw-sess-{session_id}",
            service_name=f"rpaclaw-sess-{session_id}-svc",
            rest_base_url=f"http://rpaclaw-sess-{session_id}-svc:8080",
            status="ready",
        )

    async def delete_runtime(self, runtime_record) -> None:
        self.delete_calls.append(runtime_record)
        return None

    async def refresh_runtime(self, runtime_record):
        self.refresh_calls.append(runtime_record)
        return runtime_record


class _FakeRepository:
    def __init__(self, existing=None, records=None):
        self.existing = existing
        self.records = list(records or [])
        self.inserted = []
        self.updated = []
        self.deleted = []

    async def find_one(self, query):
        return self.existing

    async def find_many(self, query):
        if not query:
            return list(self.records)
        return [
            record
            for record in self.records
            if all(record.get(key) == value for key, value in query.items())
        ]

    async def insert_one(self, document):
        self.inserted.append(document)

    async def update_one(self, query, update):
        self.updated.append((query, update))

    async def delete_one(self, query):
        self.deleted.append(query)


@pytest.mark.asyncio
async def test_ensure_runtime_reuses_ready_record():
    existing = {
        "session_id": "sess-1",
        "user_id": "user-1",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-1",
        "service_name": "rpaclaw-sess-sess-1-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": None,
    }
    provider = _FakeProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.ensure_runtime("sess-1", "user-1")

    assert isinstance(runtime, SessionRuntimeRecord)
    assert runtime.session_id == "sess-1"
    assert provider.create_calls == []
    assert repository.inserted == []
    assert len(repository.updated) == 1


@pytest.mark.asyncio
async def test_ensure_runtime_recreates_ready_record_when_runtime_is_missing():
    existing = {
        "session_id": "sess-stale-ready",
        "user_id": "user-1",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-stale-ready",
        "service_name": "rpaclaw-sess-sess-stale-ready-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-stale-ready-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": 10,
    }

    class _RefreshingMissingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "missing"
            return runtime_record

    provider = _RefreshingMissingProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.ensure_runtime("sess-stale-ready", "user-1")

    assert runtime.session_id == "sess-stale-ready"
    assert provider.create_calls == [("sess-stale-ready", "user-1")]
    assert len(provider.refresh_calls) == 1
    assert repository.deleted == [{"session_id": "sess-stale-ready"}]
    assert len(repository.inserted) == 1


@pytest.mark.asyncio
async def test_ensure_runtime_creates_when_missing():
    provider = _FakeProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.ensure_runtime("sess-2", "user-2")

    assert runtime.session_id == "sess-2"
    assert provider.create_calls == [("sess-2", "user-2")]
    assert len(repository.inserted) == 1


@pytest.mark.asyncio
async def test_ensure_runtime_keeps_created_and_last_used_timestamps_consistent(monkeypatch):
    import backend.runtime.session_runtime_manager as runtime_manager_module

    class _SkewedTimestampProvider(_FakeProvider):
        async def create_runtime(self, session_id: str, user_id: str):
            runtime = await super().create_runtime(session_id, user_id)
            runtime.created_at = 150
            runtime.last_used_at = 150
            return runtime

    provider = _SkewedTimestampProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)
    monkeypatch.setattr(runtime_manager_module.time, "time", lambda: 100)

    runtime = await manager.ensure_runtime("sess-created", "user-created")

    assert runtime.created_at == 150
    assert runtime.last_used_at == 150
    assert runtime.expires_at == 3750
    assert repository.inserted[0]["created_at"] == 150
    assert repository.inserted[0]["last_used_at"] == 150


class _FakeContainer:
    def __init__(self, name="rpaclaw-sandbox-1", status="running", health_status="healthy"):
        self.name = name
        self.removed = False
        self.attrs = {"State": {"Status": status, "Health": {"Status": health_status}}}

    def remove(self, force=False):
        self.removed = force


class _FakeContainersApi:
    def __init__(self):
        self.run_calls = []
        self._container = _FakeContainer()
        self.list_calls = []
        self.list_result = []
        self.get_error = None

    def run(self, image, **kwargs):
        self.run_calls.append((image, kwargs))
        return self._container

    def get(self, name):
        if self.get_error is not None:
            raise self.get_error
        return self._container

    def list(self, filters=None):
        self.list_calls.append(filters or {})
        return list(self.list_result)


class _FakeDockerClient:
    def __init__(self):
        self.containers = _FakeContainersApi()


class _DockerSettings:
    runtime_mode = "docker"
    runtime_image = "rpaclaw-sandbox:local"
    docker_runtime_network = "rpaclaw_default"
    docker_runtime_volumes_from = ""
    docker_runtime_shm_size = "2gb"
    docker_runtime_mem_limit = "8g"
    docker_runtime_security_opt = "seccomp:unconfined"
    docker_runtime_extra_hosts = "host.docker.internal:host-gateway"
    runtime_service_port = 8080
    runtime_wait_timeout_seconds = 0
    k8s_namespace = "local"
    k8s_runtime_service_account = ""
    k8s_runtime_image_pull_policy = "IfNotPresent"


@pytest.mark.asyncio
async def test_docker_runtime_provider_creates_session_scoped_record():
    client = _FakeDockerClient()
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)

    runtime = await provider.create_runtime("sess-12345678", "user-1")
    expected_name = (
        "rpaclaw-sess-sess-12345678-"
        + hashlib.sha1("sess-12345678".encode("utf-8")).hexdigest()[:8]
    )

    assert runtime.session_id == "sess-12345678"
    assert runtime.service_name == expected_name
    assert runtime.rest_base_url == f"http://{expected_name}:8080"
    assert len(client.containers.run_calls) == 1
    image, kwargs = client.containers.run_calls[0]
    assert image == "rpaclaw-sandbox:local"
    assert kwargs["detach"] is True
    assert kwargs["name"] == expected_name
    assert kwargs["network"] == "rpaclaw_default"
    assert kwargs["shm_size"] == "2gb"
    assert kwargs["mem_limit"] == "8g"
    assert kwargs["security_opt"] == ["seccomp:unconfined"]
    assert kwargs["extra_hosts"] == {"host.docker.internal": "host-gateway"}


@pytest.mark.asyncio
async def test_docker_runtime_provider_deletes_container():
    client = _FakeDockerClient()
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)
    runtime = SessionRuntimeRecord(
        session_id="sess-12345678",
        user_id="user-1",
        namespace="local",
        pod_name="rpaclaw-sess-sess-123",
        service_name="rpaclaw-sess-sess-123",
        rest_base_url="http://rpaclaw-sess-sess-123:8080",
        status="ready",
    )

    await provider.delete_runtime(runtime)

    assert client.containers._container.removed is True


def test_docker_runtime_provider_container_name_uses_hash_suffix_for_uniqueness():
    name1 = DockerRuntimeProvider._container_name(
        "rpa-12345678-aaaaaaaa-bbbb-cccc-dddddddddddd"
    )
    name2 = DockerRuntimeProvider._container_name(
        "rpa-12345678-eeeeeeee-ffff-1111-222222222222"
    )

    assert name1 != name2
    assert name1.startswith("rpaclaw-sess-rpa-12345678-")
    assert name2.startswith("rpaclaw-sess-rpa-12345678-")
    assert len(name1.rsplit("-", 1)[-1]) == 8


@pytest.mark.asyncio
async def test_docker_runtime_provider_delete_is_idempotent_when_container_missing():
    client = _FakeDockerClient()
    client.containers.get_error = Exception('404 Client Error: Not Found ("No such container: rpaclaw-sess-missing")')
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)
    runtime = SessionRuntimeRecord(
        session_id="sess-missing",
        user_id="user-1",
        namespace="local",
        pod_name="rpaclaw-sess-missing",
        service_name="rpaclaw-sess-missing",
        rest_base_url="http://rpaclaw-sess-missing:8080",
        status="ready",
    )

    await provider.delete_runtime(runtime)


@pytest.mark.asyncio
async def test_docker_runtime_provider_refresh_reports_health_status():
    client = _FakeDockerClient()
    client.containers._container = _FakeContainer(
        name="rpaclaw-sess-healthy",
        status="running",
        health_status="healthy",
    )
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)
    runtime = SessionRuntimeRecord(
        session_id="sess-refresh-docker",
        user_id="user-1",
        namespace="local",
        pod_name="rpaclaw-sess-healthy",
        service_name="rpaclaw-sess-healthy",
        rest_base_url="http://rpaclaw-sess-healthy:8080",
        status="creating",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "ready"


@pytest.mark.asyncio
async def test_docker_runtime_provider_refresh_reports_missing_container():
    client = _FakeDockerClient()
    client.containers.get_error = Exception(
        '404 Client Error: Not Found ("No such container: rpaclaw-sess-missing")'
    )
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)
    runtime = SessionRuntimeRecord(
        session_id="sess-refresh-missing",
        user_id="user-1",
        namespace="local",
        pod_name="rpaclaw-sess-missing",
        service_name="rpaclaw-sess-missing",
        rest_base_url="http://rpaclaw-sess-missing:8080",
        status="ready",
    )

    refreshed = await provider.refresh_runtime(runtime)

    assert refreshed.status == "missing"


@pytest.mark.asyncio
async def test_docker_runtime_provider_uses_configured_volumes_from():
    client = _FakeDockerClient()

    class _ConfiguredDockerSettings(_DockerSettings):
        docker_runtime_volumes_from = "rpaclaw-sandbox-1"

    provider = DockerRuntimeProvider(_ConfiguredDockerSettings(), client=client)

    await provider.create_runtime("sess-aaaa1111", "user-1")

    _, kwargs = client.containers.run_calls[0]
    assert kwargs["volumes_from"] == ["rpaclaw-sandbox-1"]


@pytest.mark.asyncio
async def test_docker_runtime_provider_discovers_compose_sandbox_for_volumes():
    client = _FakeDockerClient()
    client.containers.list_result = [_FakeContainer(name="rpaclaw-sandbox-1")]
    provider = DockerRuntimeProvider(_DockerSettings(), client=client)

    await provider.create_runtime("sess-bbbb2222", "user-2")

    _, kwargs = client.containers.run_calls[0]
    assert kwargs["volumes_from"] == ["rpaclaw-sandbox-1"]
    assert client.containers.list_calls == [
        {"label": "com.docker.compose.service=sandbox"}
    ]


@pytest.mark.asyncio
async def test_docker_runtime_provider_waits_for_runtime_readiness(monkeypatch):
    client = _FakeDockerClient()

    class _WaitingDockerSettings(_DockerSettings):
        runtime_wait_timeout_seconds = 15

    provider = DockerRuntimeProvider(_WaitingDockerSettings(), client=client)
    waited = []

    async def _fake_wait(rest_base_url: str):
        waited.append(rest_base_url)

    monkeypatch.setattr(provider, "_wait_until_ready", _fake_wait)

    runtime = await provider.create_runtime("sess-ready01", "user-1")

    assert waited == [runtime.rest_base_url]


class _FakeK8sContainerStatus:
    def __init__(self, ready: bool):
        self.ready = ready


class _FakeK8sStatus:
    def __init__(self, phase: str, ready: bool = True):
        self.phase = phase
        self.container_statuses = [_FakeK8sContainerStatus(ready)]


class _FakeK8sPod:
    def __init__(self, phase: str = "Running", ready: bool = True):
        self.status = _FakeK8sStatus(phase, ready)


class _FakeApiException(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class _FakeCoreV1Api:
    def __init__(self):
        self.created_pods = []
        self.created_services = []
        self.deleted_pods = []
        self.deleted_services = []
        self.read_pod = _FakeK8sPod()
        self.delete_pod_error = None
        self.delete_service_error = None

    def create_namespaced_pod(self, namespace, body):
        self.created_pods.append((namespace, body))

    def create_namespaced_service(self, namespace, body):
        self.created_services.append((namespace, body))

    def read_namespaced_pod(self, name, namespace):
        return self.read_pod

    def delete_namespaced_pod(self, name, namespace, grace_period_seconds=0):
        if self.delete_pod_error is not None:
            raise self.delete_pod_error
        self.deleted_pods.append((namespace, name, grace_period_seconds))

    def delete_namespaced_service(self, name, namespace, grace_period_seconds=0):
        if self.delete_service_error is not None:
            raise self.delete_service_error
        self.deleted_services.append((namespace, name, grace_period_seconds))


class _K8sSettings(_DockerSettings):
    runtime_mode = "session_pod"
    k8s_namespace = "beta"
    runtime_wait_timeout_seconds = 5
    k8s_runtime_service_account = ""
    k8s_runtime_image_pull_policy = "IfNotPresent"
    k8s_runtime_image_pull_secrets = ""
    k8s_runtime_node_selector = ""
    k8s_runtime_env = ""
    k8s_runtime_labels = ""
    k8s_runtime_annotations = ""
    k8s_runtime_cpu_request = ""
    k8s_runtime_cpu_limit = ""
    k8s_runtime_memory_request = ""
    k8s_runtime_memory_limit = ""
    k8s_runtime_tolerations_json = ""
    k8s_runtime_workspace_volume_name = "workspace"
    k8s_runtime_workspace_mount_path = "/home/rpaclaw"
    k8s_runtime_workspace_pvc_claim = ""
    k8s_runtime_extra_volumes_json = ""
    k8s_runtime_extra_volume_mounts_json = ""


@pytest.mark.asyncio
async def test_k8s_runtime_provider_creates_pod_and_service(monkeypatch):
    api = _FakeCoreV1Api()
    provider = K8sRuntimeProvider(
        _K8sSettings(),
        core_v1_api=api,
        api_exception_cls=_FakeApiException,
        config_loader=lambda: None,
    )

    async def _no_wait(api_client, name):
        return None

    monkeypatch.setattr(provider, "_wait_until_ready", _no_wait)

    runtime = await provider.create_runtime("sess-k8s-1", "user-1")

    assert runtime.namespace == "beta"
    assert runtime.service_name.startswith("rpaclaw-sess-sess-k8s-1-")
    assert runtime.rest_base_url == (
        f"http://{runtime.service_name}.beta.svc.cluster.local:8080"
    )
    assert len(api.created_pods) == 1
    assert len(api.created_services) == 1
    _, pod_body = api.created_pods[0]
    _, service_body = api.created_services[0]
    assert pod_body["metadata"]["name"] == runtime.pod_name
    assert pod_body["spec"]["containers"][0]["image"] == "rpaclaw-sandbox:local"
    assert service_body["spec"]["selector"]["rpaclaw/runtime-name"] == runtime.service_name


@pytest.mark.asyncio
async def test_k8s_runtime_provider_delete_is_idempotent_when_resources_missing():
    api = _FakeCoreV1Api()
    api.delete_service_error = _FakeApiException(404, "service missing")
    api.delete_pod_error = _FakeApiException(404, "pod missing")
    provider = K8sRuntimeProvider(
        _K8sSettings(),
        core_v1_api=api,
        api_exception_cls=_FakeApiException,
        config_loader=lambda: None,
    )
    runtime = SessionRuntimeRecord(
        session_id="sess-k8s-missing",
        user_id="user-1",
        namespace="beta",
        pod_name="rpaclaw-sess-sess-k8s-missing-aaaa1111",
        service_name="rpaclaw-sess-sess-k8s-missing-aaaa1111",
        rest_base_url="http://rpaclaw-sess-sess-k8s-missing-aaaa1111.beta.svc.cluster.local:8080",
        status="ready",
    )

    await provider.delete_runtime(runtime)


def test_k8s_runtime_provider_builds_configurable_pod_manifest():
    class _ConfiguredK8sSettings(_K8sSettings):
        k8s_runtime_service_account = "rpaclaw-runtime"
        k8s_runtime_image_pull_policy = "Always"
        k8s_runtime_image_pull_secrets = "regcred,another-secret"
        k8s_runtime_node_selector = "pool:runtime,topology.kubernetes.io/zone:cn-beijing-a"
        k8s_runtime_env = "TZ:Asia/Shanghai,PLAYWRIGHT_BROWSERS_PATH:/ms-playwright"
        k8s_runtime_labels = "team:beta,track:session-runtime"
        k8s_runtime_annotations = "prometheus.io/scrape:false,owner:rpaclaw"
        k8s_runtime_cpu_request = "500m"
        k8s_runtime_cpu_limit = "2"
        k8s_runtime_memory_request = "1Gi"
        k8s_runtime_memory_limit = "4Gi"
        k8s_runtime_tolerations_json = '[{"key":"dedicated","operator":"Equal","value":"runtime","effect":"NoSchedule"}]'
        k8s_runtime_workspace_volume_name = "workspace-data"
        k8s_runtime_workspace_mount_path = "/home/rpaclaw"
        k8s_runtime_workspace_pvc_claim = "rpaclaw-workspace"
        k8s_runtime_extra_volumes_json = '[{"name":"tools","persistentVolumeClaim":{"claimName":"rpaclaw-tools"}}]'
        k8s_runtime_extra_volume_mounts_json = '[{"name":"tools","mountPath":"/app/Tools","readOnly":true}]'

    provider = K8sRuntimeProvider(
        _ConfiguredK8sSettings(),
        core_v1_api=_FakeCoreV1Api(),
        api_exception_cls=_FakeApiException,
        config_loader=lambda: None,
    )

    pod = provider._build_pod_manifest("rpaclaw-sess-demo-abcd1234", "sess-demo", "user-1")

    metadata = pod["metadata"]
    container = pod["spec"]["containers"][0]

    assert metadata["labels"]["team"] == "beta"
    assert metadata["labels"]["track"] == "session-runtime"
    assert metadata["annotations"]["owner"] == "rpaclaw"
    assert pod["spec"]["serviceAccountName"] == "rpaclaw-runtime"
    assert pod["spec"]["imagePullSecrets"] == [{"name": "regcred"}, {"name": "another-secret"}]
    assert pod["spec"]["nodeSelector"] == {
        "pool": "runtime",
        "topology.kubernetes.io/zone": "cn-beijing-a",
    }
    assert pod["spec"]["tolerations"] == [
        {"key": "dedicated", "operator": "Equal", "value": "runtime", "effect": "NoSchedule"}
    ]
    assert pod["spec"]["volumes"] == [
        {
            "name": "workspace-data",
            "persistentVolumeClaim": {"claimName": "rpaclaw-workspace"},
        },
        {
            "name": "tools",
            "persistentVolumeClaim": {"claimName": "rpaclaw-tools"},
        },
    ]
    assert container["imagePullPolicy"] == "Always"
    assert container["env"] == [
        {"name": "TZ", "value": "Asia/Shanghai"},
        {"name": "PLAYWRIGHT_BROWSERS_PATH", "value": "/ms-playwright"},
    ]
    assert container["volumeMounts"] == [
        {"name": "workspace-data", "mountPath": "/home/rpaclaw"},
        {"name": "tools", "mountPath": "/app/Tools", "readOnly": True},
    ]
    assert container["resources"] == {
        "requests": {"cpu": "500m", "memory": "1Gi"},
        "limits": {"cpu": "2", "memory": "4Gi"},
    }


def test_k8s_runtime_provider_defaults_workspace_to_empty_dir():
    provider = K8sRuntimeProvider(
        _K8sSettings(),
        core_v1_api=_FakeCoreV1Api(),
        api_exception_cls=_FakeApiException,
        config_loader=lambda: None,
    )

    pod = provider._build_pod_manifest("rpaclaw-sess-demo-abcd1234", "sess-demo", "user-1")

    assert pod["spec"]["volumes"][0] == {"name": "workspace", "emptyDir": {}}
    assert pod["spec"]["containers"][0]["volumeMounts"][0] == {
        "name": "workspace",
        "mountPath": "/home/rpaclaw",
    }


def test_get_session_runtime_manager_is_singleton():
    reset_session_runtime_manager()
    repository = _FakeRepository(existing=None)
    provider = _FakeProvider()
    manager1 = get_session_runtime_manager(
        settings=_Settings("shared"),
        provider=provider,
        repository=repository,
    )
    manager2 = get_session_runtime_manager(settings=_Settings("docker"))

    assert manager1 is manager2


@pytest.mark.asyncio
async def test_destroy_runtime_deletes_record_and_calls_provider():
    existing = {
        "session_id": "sess-9",
        "user_id": "user-9",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-9",
        "service_name": "rpaclaw-sess-sess-9-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-9-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": None,
    }
    provider = _FakeProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    destroyed = await manager.destroy_runtime("sess-9")

    assert destroyed is True
    assert len(provider.delete_calls) == 1
    assert provider.delete_calls[0].session_id == "sess-9"
    assert repository.deleted == [{"session_id": "sess-9"}]


@pytest.mark.asyncio
async def test_destroy_runtime_returns_false_when_missing():
    provider = _FakeProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    destroyed = await manager.destroy_runtime("sess-missing")

    assert destroyed is False
    assert provider.delete_calls == []
    assert repository.deleted == []


@pytest.mark.asyncio
async def test_ensure_runtime_refreshes_expiration_window(monkeypatch):
    import backend.runtime.session_runtime_manager as runtime_manager_module

    existing = {
        "session_id": "sess-ttl",
        "user_id": "user-ttl",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-ttl",
        "service_name": "rpaclaw-sess-sess-ttl-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-ttl-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": 2,
    }
    provider = _FakeProvider()
    repository = _FakeRepository(existing=existing)
    settings = _Settings("shared")
    settings.runtime_idle_ttl_seconds = 120
    manager = SessionRuntimeManager(
        provider=provider,
        repository=repository,
        settings=settings,
    )
    monkeypatch.setattr(runtime_manager_module.time, "time", lambda: 100)

    runtime = await manager.ensure_runtime("sess-ttl", "user-ttl")

    assert runtime.last_used_at == 100
    assert runtime.expires_at == 220
    assert repository.updated == [
        (
            {"session_id": "sess-ttl"},
            {"$set": {"status": "ready", "last_used_at": 100, "expires_at": 220}},
        )
    ]


@pytest.mark.asyncio
async def test_cleanup_orphans_deletes_only_unowned_runtime_records():
    provider = _FakeProvider()
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-1",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-1",
                "service_name": "rpaclaw-sess-sess-1-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
            {
                "session_id": "sess-2",
                "user_id": "user-2",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-2",
                "service_name": "rpaclaw-sess-sess-2-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-2-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
        ]
    )
    async def _owner_checker(record):
        return record.session_id == "sess-1"

    manager = SessionRuntimeManager(
        provider=provider,
        repository=repository,
        owner_checker=_owner_checker,
    )

    cleaned = await manager.cleanup_orphans()

    assert cleaned == 1
    assert [record.session_id for record in provider.delete_calls] == ["sess-2"]
    assert repository.deleted == [{"session_id": "sess-2"}]


@pytest.mark.asyncio
async def test_cleanup_expired_deletes_only_expired_records():
    provider = _FakeProvider()
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-expired",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-expired",
                "service_name": "rpaclaw-sess-sess-expired-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-expired-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 50,
            },
            {
                "session_id": "sess-active",
                "user_id": "user-2",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-active",
                "service_name": "rpaclaw-sess-sess-active-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-active-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 90,
                "expires_at": 200,
            },
        ]
    )
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    cleaned = await manager.cleanup_expired(now_ts=100)

    assert cleaned == 1
    assert [record.session_id for record in provider.delete_calls] == ["sess-expired"]
    assert repository.deleted == [{"session_id": "sess-expired"}]


@pytest.mark.asyncio
async def test_get_runtime_returns_none_when_missing():
    provider = _FakeProvider()
    repository = _FakeRepository(existing=None)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.get_runtime("sess-missing")

    assert runtime is None
    assert provider.refresh_calls == []


@pytest.mark.asyncio
async def test_get_runtime_can_refresh_status_and_persist():
    existing = {
        "session_id": "sess-refresh",
        "user_id": "user-refresh",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-refresh",
        "service_name": "rpaclaw-sess-sess-refresh-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-refresh-svc:8080",
        "status": "creating",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": 10,
    }

    class _RefreshingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "ready"
            return runtime_record

    provider = _RefreshingProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.get_runtime("sess-refresh", refresh=True)

    assert runtime is not None
    assert runtime.status == "ready"
    assert len(provider.refresh_calls) == 1
    assert repository.updated == [
        (
            {"session_id": "sess-refresh"},
            {"$set": {"status": "ready"}},
        )
    ]


@pytest.mark.asyncio
async def test_get_runtime_refresh_deletes_missing_runtime_record():
    existing = {
        "session_id": "sess-missing-refresh",
        "user_id": "user-refresh",
        "namespace": "beta",
        "pod_name": "rpaclaw-sess-sess-missing-refresh",
        "service_name": "rpaclaw-sess-sess-missing-refresh-svc",
        "rest_base_url": "http://rpaclaw-sess-sess-missing-refresh-svc:8080",
        "status": "ready",
        "created_at": 1,
        "last_used_at": 1,
        "expires_at": 10,
    }

    class _MissingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "missing"
            return runtime_record

    provider = _MissingProvider()
    repository = _FakeRepository(existing=existing)
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtime = await manager.get_runtime("sess-missing-refresh", refresh=True)

    assert runtime is None
    assert len(provider.refresh_calls) == 1
    assert repository.updated == []
    assert repository.deleted == [{"session_id": "sess-missing-refresh"}]


@pytest.mark.asyncio
async def test_list_runtimes_filters_by_user_without_side_effects():
    provider = _FakeProvider()
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-1",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-1",
                "service_name": "rpaclaw-sess-sess-1-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
            {
                "session_id": "sess-2",
                "user_id": "user-2",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-2",
                "service_name": "rpaclaw-sess-sess-2-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-2-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
        ]
    )
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtimes = await manager.list_runtimes(user_id="user-1")

    assert [runtime.session_id for runtime in runtimes] == ["sess-1"]
    assert provider.refresh_calls == []


@pytest.mark.asyncio
async def test_list_runtimes_refreshes_and_persists_updates():
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-1",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-1",
                "service_name": "rpaclaw-sess-sess-1-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-1-svc:8080",
                "status": "creating",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            }
        ]
    )

    class _RefreshingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "ready"
            return runtime_record

    provider = _RefreshingProvider()
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtimes = await manager.list_runtimes(refresh=True)

    assert len(runtimes) == 1
    assert runtimes[0].status == "ready"
    assert len(provider.refresh_calls) == 1
    assert repository.updated == [
        (
            {"session_id": "sess-1"},
            {"$set": {"status": "ready"}},
        )
    ]


@pytest.mark.asyncio
async def test_list_runtimes_refresh_omits_missing_records_and_deletes_them():
    repository = _FakeRepository(
        records=[
            {
                "session_id": "sess-stale",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-stale",
                "service_name": "rpaclaw-sess-sess-stale-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-stale-svc:8080",
                "status": "ready",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
            {
                "session_id": "sess-live",
                "user_id": "user-1",
                "namespace": "beta",
                "pod_name": "rpaclaw-sess-sess-live",
                "service_name": "rpaclaw-sess-sess-live-svc",
                "rest_base_url": "http://rpaclaw-sess-sess-live-svc:8080",
                "status": "creating",
                "created_at": 1,
                "last_used_at": 1,
                "expires_at": 10,
            },
        ]
    )

    class _RefreshingProvider(_FakeProvider):
        async def refresh_runtime(self, runtime_record):
            self.refresh_calls.append(runtime_record)
            runtime_record.status = "missing" if runtime_record.session_id == "sess-stale" else "ready"
            return runtime_record

    provider = _RefreshingProvider()
    manager = SessionRuntimeManager(provider=provider, repository=repository)

    runtimes = await manager.list_runtimes(refresh=True)

    assert [runtime.session_id for runtime in runtimes] == ["sess-live"]
    assert len(provider.refresh_calls) == 2
    assert repository.deleted == [{"session_id": "sess-stale"}]
    assert repository.updated == [
        (
            {"session_id": "sess-live"},
            {"$set": {"status": "ready"}},
        )
    ]
