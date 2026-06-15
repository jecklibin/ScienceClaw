# AIO Runtime Adapter 内网适配与交接指南

本文给内网 Agent 使用，目标是在同步当前分支后，尽快把外网已完成的 AIO Runtime Adapter 底座接入真实 AIO 服务。

不要把本文理解为重新设计 AIO 平台。外网阶段已经完成 Host Backend 与不可信执行面之间的 runtime record、adapter semantic API、runtime proxy、CDP ready gate、workspace/skill/artifact 闭环、token/error 脱敏、file policy、Docker image 与本机 fake AIO container smoke。内网阶段只需要替换真实 AIO create/status/delete、发布 adapter 镜像、跑真实 AIO smoke，并根据真实环境差异少量收口。

## 1. 接手前先读

按这个顺序读即可：

1. `docs/rpa/aio-session-sandbox-runtime-adapter-design.md`
2. `RpaClaw/runtime-adapter/README.md`
3. `docs/features/F025-aio-session-sandbox-runtime-adapter.md`
4. `docs/evidence/EV-025-aio-session-sandbox-runtime-adapter.md`
5. 本文

关键代码入口：

| 模块 | 作用 |
| --- | --- |
| `backend.runtime.aio_runtime_provider.AioApiRuntimeProvider` | Host 侧真实 AIO create/status/delete provider。 |
| `backend.runtime.adapter_client.RuntimeAdapterClient` | Host 调用 adapter semantic API 的客户端，默认不走系统代理。 |
| `backend.runtime.adapter_app` | adapter 镜像内运行的 FastAPI 执行面服务。 |
| `backend.runtime.adapter_smoke` | 本机、fake AIO、container fake AIO、真实 AIO smoke 入口。 |
| `backend.runtime.local_fake_aio_service` | 外网 fake AIO service，只用于本机 Docker 验证。 |
| `backend.route.runtime_proxy` | Host 到 adapter 的 HTTP/WebSocket proxy。 |
| `backend.rpa.cdp_connector` | Host 侧 CDP connector ready/error 边界。 |

## 2. 内网要完成什么

内网验收只聚焦这些事项：

1. 发布 `rpaclaw-runtime-adapter` 镜像到内网镜像仓库。
2. 配置真实 AIO create/status/delete endpoint 和路径。
3. 确认 create payload 中的 `session_id`、`user_id`、`image`、`ttl_seconds`、`env`、resources、labels、network、mounts 等字段被真实 AIO 接受。
4. 确认真实 AIO response 能映射出 `sandbox_id` 与 `route_base_url`，可选 `browser_view_url`、`runtime_token`、`status`、`expires_at`。
5. 确认 Host Backend 多实例共享 runtime record，同一 `session_id` 不会创建多个可用沙箱。
6. 确认 adapter `/health`、`/v1/browser/info`、CDP route、browser view、workspace/skill/download file API 在真实 AIO 中可达。
7. 确认默认 cleanup/delete 可释放真实 AIO 资源；排障时才用 `--keep-runtime`。

不需要在内网重做：

- 重写 Host/RPA 主链路。
- 重写 Runtime Adapter semantic API。
- 让 adapter 定义 `AcceptedTrace`、Skill 真源、artifact 归因或 Harness expected signals。
- 用 fake AIO service 代替真实 AIO 验收。
- 把完整 Host Backend 搬进 AIO sandbox。

## 3. 推荐接入步骤

### 3.1 发布 adapter 镜像

在外网已验证的本机命令是：

```powershell
cd .\RpaClaw
docker build `
  --build-arg BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.57.0-noble `
  --build-arg INSTALL_CHROMIUM=false `
  -f runtime-adapter/Dockerfile `
  -t rpaclaw-runtime-adapter:dev .
```

内网可按实际基础镜像策略调整：

- 如果内网 apt 源稳定，可以使用默认 `python:3.13-slim` 并安装 Debian `chromium`。
- 如果希望减少 apt 不确定性，优先使用已经带浏览器的 Playwright Python base image。
- 发布时必须设置 `RUNTIME_ADAPTER_VERSION` 为镜像 tag 或 git revision，便于 `/health` 和 smoke 对齐版本。

镜像只启动：

```text
python -m uvicorn backend.runtime.adapter_app:app --host 0.0.0.0 --port 8080
```

它不启动 `backend.main:app`，不应该承担 Host Backend 职责。

### 3.2 配置 Host Backend

最小配置形态如下：

```powershell
$env:RUNTIME_MODE = "aio"
$env:AIO_RUNTIME_API_BASE_URL = "https://<real-aio-service>"
$env:AIO_RUNTIME_API_TOKEN = "<host-to-aio-token>"
$env:AIO_RUNTIME_IMAGE = "<internal-registry>/rpaclaw-runtime-adapter:<tag>"
$env:AIO_RUNTIME_CREATE_PATH = "/v1/sandboxes"
$env:AIO_RUNTIME_STATUS_PATH_TEMPLATE = "/v1/sandboxes/{sandbox_id}"
$env:AIO_RUNTIME_DELETE_PATH_TEMPLATE = "/v1/sandboxes/{sandbox_id}"
$env:AIO_RUNTIME_NAMESPACE = "aio"
$env:AIO_RUNTIME_TTL_SECONDS = "7200"
$env:AIO_RUNTIME_CREATE_EXTRA_JSON = '{"resources":{"cpu":"1","memory":"2Gi"},"labels":{"app":"rpaclaw"}}'
$env:AIO_RUNTIME_ADAPTER_ENV = "RUNTIME_ADAPTER_TOKEN=<session-adapter-token>,RUNTIME_ADAPTER_DOWNLOADS_DIR=downloads,RUNTIME_ADAPTER_ENABLE_LOCAL_EXECUTION=true,RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH=true"
```

`AIO_RUNTIME_ADAPTER_ENV` 会进入 create payload 的 `env` object。真实 AIO response 可以不回显 adapter token；Host 会复用注入的 `RUNTIME_ADAPTER_TOKEN` 作为 `SessionRuntimeRecord.runtime_token`。

如果真实 AIO 字段名不同，优先适配 `AioApiRuntimeProvider._record_from_payload()` 的薄映射，不要把差异扩散到 RPA recorder、compiler、proxy 或前端。

当前 response 兼容：

| Host 需要 | 兼容字段 |
| --- | --- |
| `sandbox_id` | `sandbox_id`、`id` |
| `route_base_url` | `route_base_url`、`adapter_url`、`rest_base_url` |
| `browser_view_url` | `browser_view_url`、`view_url` |
| `runtime_token` | `runtime_token`、`adapter_token`、create payload 中的 `env.RUNTIME_ADAPTER_TOKEN` |
| `status` | `ok/ready/running` -> `ready`；`creating/pending/provisioning/starting` -> `creating`；terminal/error 状态 -> `missing` |

response 包装兼容 `{data:{...}}`、`{sandbox:{...}}`、`{data:{sandbox:{...}}}`、`{runtime:{...}}`。

### 3.3 先做配置诊断

不要一上来跑完整 smoke，先诊断配置和 response 映射：

```powershell
cd .\RpaClaw
$env:PYTHONPATH = "."
python -m backend.runtime.aio_runtime_provider --diagnose
```

如果已有真实 AIO create/status response 样例，保存为 JSON 后离线验证：

```powershell
python -m backend.runtime.aio_runtime_provider `
  --diagnose `
  --sample-response .\tmp-real-aio-response.json
```

诊断输出不打印 token 原文，只给出 `api_token_configured`、endpoint 预览、create payload 脱敏摘要和 response 映射结果。

### 3.4 跑真实 AIO smoke

真实 AIO 可达后运行：

```powershell
cd .\RpaClaw
$env:PYTHONPATH = "."
python -m backend.runtime.adapter_smoke `
  --mode aio_real `
  --workspace-root .runtime-adapter-smoke-aio-real
```

成功标准：

```text
status=ok
mode=aio_real
runtime.status=ready
runtime.sandbox_id 非空
runtime.route_base_url 非空
adapter_self_check.status=ok
adapter_self_check.contract_version=v1
health.status=ok
health.config.token_required=true|false
health.config.adapter_version=<image tag or git revision>
skill.run.status=success
token 原文不出现在 smoke 输出
```

排障时才保留沙箱：

```powershell
python -m backend.runtime.adapter_smoke `
  --mode aio_real `
  --workspace-root .runtime-adapter-smoke-aio-real `
  --keep-runtime
```

`--keep-runtime` 只用于保留失败现场查看 AIO 日志、adapter logs、CDP、browser view 或网络路由；定位完成后要手工 delete。

### 3.5 验证浏览器与 CDP

真实 AIO smoke 通过后，单独确认：

1. Host 访问 `GET {route_base_url}/health` 返回 `contract_version=v1`。
2. Host 访问 `GET {route_base_url}/v1/browser/info` 能触发浏览器启动。
3. 返回的 `cdp_url` 是 Host Backend 可达地址，而不是只在容器内可达的 `127.0.0.1`。
4. `listener.status=injected`，marker 是 `__rpaclawRuntimeAdapterListener`。
5. Runtime proxy WebSocket 不转发前端 `Sec-WebSocket-*` 握手头，也不把用户 Authorization/Cookie 传给 adapter。

如果 AIO 使用独立 CDP route，优先让 AIO 或 adapter 设置：

```text
RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL=ws://<host-reachable-cdp-route>
```

只有真实平台无法保留动态 `/devtools/browser/<id>` path 时，才考虑完整覆盖：

```text
RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_URL=ws://<full-host-reachable-cdp-url>
```

## 4. 常见失败与归因

| 现象 | 优先看哪里 | 解释 |
| --- | --- | --- |
| create 失败，Host 没有 runtime record | `aio_runtime_provider --diagnose`、AIO create logs | create 阶段还没有可信 runtime，Host 会抛脱敏 acquisition error。 |
| runtime 一直 `creating` | AIO status response | Host 不应重复 create；应等待 status 变 `ready` 或 terminal。 |
| runtime `missing`，reason=`aio_status_unavailable` | AIO status endpoint、token、path | status 不可达，不代表 adapter 本身一定坏。 |
| runtime `missing`，reason=`adapter_health_unavailable` | AIO route、adapter service、network policy | 平台 ready 但执行面不可达。 |
| reason=`adapter_health_unauthorized` | `RUNTIME_ADAPTER_TOKEN` 与 Host runtime token | AIO create 注入的 token 和 Host 访问 token 不一致。 |
| reason=`adapter_contract_mismatch` | Host 与 adapter image 版本 | adapter `/health.contract_version` 不是 `v1`。 |
| `/v1/browser/info` 503 | `RUNTIME_ADAPTER_ENABLE_BROWSER_LAUNCH`、`RUNTIME_ADAPTER_CDP_URL`、browser executable | 没有配置固定 CDP，也没有启用浏览器启动。 |
| `cdp_url` 返回但 Host 连不上 | AIO CDP route、`RUNTIME_ADAPTER_BROWSER_CDP_PUBLIC_BASE_URL` | 返回了容器内地址或未暴露的端口。 |
| `skill.run.status` 失败 | adapter logs、workspace、file policy、子进程环境 | 不要让 adapter 子进程看到 Host/AIO token。 |
| cleanup 覆盖主错误 | smoke 已避免 | `aio_real` 会优先保留主失败；delete 失败不会遮蔽 health/skill root cause。 |

外网曾遇到一个本机问题：Windows 系统代理会让 `httpx` 访问 `127.0.0.1` adapter route 返回 502。当前 `RuntimeAdapterClient` 默认 `trust_env=False`，内网若改写 HTTP client factory，必须保留这个内部控制通道不走外部代理的语义。

## 5. EKS 多实例注意事项

内网 Host Backend、前端等服务是 EKS 多实例部署。Host 侧必须依赖共享 runtime record，而不是进程内状态：

- `SessionRuntimeManager.ensure_runtime()` 以 `session_id` 复用 runtime。
- duplicate create 时，后写入失败的实例要 cleanup 自己刚创建的 AIO 沙箱，并复用已有 record。
- `get_runtime(refresh=True)` 和 `list_runtimes(refresh=True)` 要把 provider refresh 后的 route/status/metadata 写回共享存储。
- adapter health 诊断只写非敏感 metadata，例如 `adapter_health_status`、`adapter_contract_version`、`adapter_version`、`runtime_status_reason`、`adapter_file_policy`。
- metadata 不得写入 token、页面内容、AcceptedTrace、expected signals 或 artifact 真源。

相关回归在 `RpaClaw/backend/tests/runtime/test_runtime_manager.py`。

## 6. 内网完成标准

可以认为内网 AIO 适配完成的条件：

1. `aio_runtime_provider --diagnose` 对真实配置返回 `ready=true`。
2. 真实 response sample 能映射出 runtime 摘要。
3. adapter 镜像发布并设置 `RUNTIME_ADAPTER_VERSION`。
4. `adapter_smoke --mode aio_real` 返回 `status=ok`，默认 cleanup 后 AIO 不残留 sandbox。
5. `--keep-runtime` 模式下能查看 AIO logs、adapter logs、browser/CDP 状态，且手工 delete 可释放资源。
6. Host runtime status/list 能看到 adapter health/file policy metadata，但不泄漏 token。
7. CDP connector 和 runtime proxy 只在 runtime `ready` 且 adapter health ok 后放行。
8. RPA recorder、compiler、Harness expected signals 没有被 adapter 或 fake AIO 改写。

若上述全部满足，内网 Agent 的后续工作应转向真实业务场景 smoke，而不是继续扩展 AIO Runtime Adapter 抽象。
