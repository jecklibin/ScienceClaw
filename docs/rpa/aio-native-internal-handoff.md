# AIO 原生 API 内网接入 Handoff

本文给内网 Agent 使用。当前技术路线已经明确为：

```text
AIO 原生 API + Host Backend 控制面适配
```

Runtime Adapter 暂缓，不作为第一阶段上线依赖。不要把本任务理解为继续发布 adapter 镜像或在 AIO 沙箱内启动 RpaClaw adapter 服务；第一阶段目标是让 Host Backend 直接通过 AIO 原生 API 管理会话级 sandbox，并把现有 RPA 录制、自然语言操作、脚本生成和 Skill replay 切到 AIO browser/执行面。

## 1. 接手前先读

建议按顺序阅读：

1. `docs/decisions/ADR-006-aio-native-api-first-runtime-strategy.md`
2. `docs/rpa/aio-native-runtime-provider.md`
3. `docs/rpa/aio-native-functional-smoke-checklist.md`
4. `docs/rpa/aio-session-sandbox-runtime-adapter-design.md`
5. `docs/rpa/aio-runtime-adapter-internal-handoff.md`

其中第 4、5 份文档包含早期 Runtime Adapter 路线的边界和验证沉淀。它们仍可作为备选路线参考，但第一阶段不要继续沿 adapter 作为默认路线扩展。

## 2. 内网 AIO 生命周期接口

API 遵从 AIO 沙箱官网接口形态：https://sandbox.agent-infra.com/api/

### 2.1 创建沙箱

根据沙箱服务模板 ID 创建实例。

```http
POST https://{APIG-Endpoint}/api/livefunction/sandboxes
Content-Type: application/json

{
  "templateId": "lf-jsdklalfdan5sf1a1dd1"
}
```

响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "templateId": "lf-xxxxxx",
    "sandboxId": "6v8s62vtbsxlvup8",
    "cpu": 2000,
    "memory": 4096,
    "timeout": 300,
    "status": "running",
    "startAt": "2026-05-08T18:50:31.493441369"
  }
}
```

### 2.2 查询沙箱

根据沙箱实例 ID 获取实例信息和状态。

```http
GET https://{APIG-Endpoint}/api/livefunction/sandboxes/{sandboxId}
```

状态码 `404` 表示实例不存在。

状态码 `200` 响应示例：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "templateId": "lf-xxxxxxxx",
    "sandboxId": "ef4xmtttfzi6owox",
    "cpu": 2000,
    "memory": 4096,
    "timeout": 1200,
    "status": "stopped",
    "startAt": "2026-05-06T17:48:28.076110466",
    "endAt": "2026-05-06T18:08:28.146305101"
  }
}
```

状态语义：

- `running`：可继续 refresh，并可进入 browser/CDP ready 检查。
- `stopped`：实例启动时间大于超时时间，Host 应标记 runtime missing/expired。
- `error`：实例启动失败，Host 应标记 runtime missing/error。
- `404`：实例不存在，Host 应标记 runtime missing。

### 2.3 删除沙箱

```http
DELETE https://{APIG-Endpoint}/api/livefunction/sandboxes/{sandboxId}
```

响应示例：

```json
{
  "code": 200,
  "message": "Delete sandbox success"
}
```

### 2.4 刷新沙箱存活时间

```http
POST https://{APIG-Endpoint}/api/livefunction/sandboxes/refresh/{sandboxId}
Content-Type: application/json

{
  "duration": 300
}
```

响应示例：

```json
{
  "code": 200,
  "message": "success"
}
```

## 3. Host Runtime Record 映射

内网 provider 应将 AIO response 映射为 Host 侧 `SessionRuntimeRecord`，RPA 主链路不直接依赖 AIO 原始字段。

建议映射：

| Host 字段 | AIO 字段或来源 |
| --- | --- |
| `sandbox_id` | `data.sandboxId` |
| `namespace` | 固定为 `aio-native` 或内网约定 namespace |
| `status` | `running -> ready/creating`，`stopped/error/404 -> missing` |
| `metadata.template_id` | `data.templateId` |
| `metadata.cpu` | `data.cpu` |
| `metadata.memory` | `data.memory` |
| `metadata.timeout` | `data.timeout` |
| `metadata.start_at` | `data.startAt` |
| `metadata.end_at` | `data.endAt` |
| `metadata.aio_status` | AIO 原始 status，非敏感 |

`rest_base_url` / `route_base_url` / `browser_view_url` / `cdp_url` 需要结合 AIO 官方 browser/file/shell API 或内网 APIG 路由规则补齐。不要把这些 URL 拼接逻辑散落到 RPA recorder、frontend 或 Skill compiler 中，应收敛在 runtime provider / CDP connector / runtime client 层。

## 4. Host Backend 多实例约束

内网前端、Host Backend 等模块是 EKS 多实例部署，因此 runtime lifecycle 必须以共享持久化 record 为准。

必须实现：

- `ensure_runtime(session_id, user_id)` 先读共享 record，再决定是否 create。
- 同一 `session_id` 并发 create 时只能绑定一个 sandbox。
- 如果两个 Host 实例并发创建，未赢得 record 绑定的一方必须删除自己刚创建的 sandbox。
- `refresh_runtime(record)` 查询 AIO 后，需要把最新 status、timeout、start/end time、browser/CDP ready 诊断写回共享 record。
- `delete_runtime(record)` 成功或 404 都应让 Host record 进入已释放/不可用状态。
- 后端进程重启后，能够从共享 record 恢复并继续查询/刷新已有 sandbox。

禁止：

- 依赖进程内 dict 作为 runtime 真源。
- 每次自然语言操作或每个 RPA step 都创建新 sandbox。
- 把完整页面内容、AcceptedTrace、expected signals、用户 token、AIO token 写入 runtime metadata。

## 5. 第一阶段实现任务

### 5.1 Provider 接入

新增或改造 native AIO provider，使其支持：

- create：`POST /api/livefunction/sandboxes`，payload 至少包含 `templateId`。
- status：`GET /api/livefunction/sandboxes/{sandboxId}`。
- refresh：`POST /api/livefunction/sandboxes/refresh/{sandboxId}`，payload 包含 `duration`。
- delete：`DELETE /api/livefunction/sandboxes/{sandboxId}`。
- response unwrap：兼容 `{code,message,data}`。
- status mapping：`running`、`stopped`、`error`、`404`。
- sanitized diagnostics：不输出 token 和敏感请求头。

建议配置项：

```powershell
$env:RUNTIME_MODE = "aio_native"
$env:AIO_NATIVE_API_BASE_URL = "https://{APIG-Endpoint}"
$env:AIO_NATIVE_TEMPLATE_ID = "lf-jsdklalfdan5sf1a1dd1"
$env:AIO_NATIVE_REFRESH_DURATION_SECONDS = "300"
$env:AIO_NATIVE_API_TOKEN = "<host-to-aio-token-if-needed>"
```

如果内网鉴权通过 APIG header 完成，应只在 provider HTTP client 层注入，不要进入 RPA trace、frontend payload 或 runtime metadata。

### 5.2 Browser/CDP 接入

确认 AIO 原生 API 如何从 sandbox ID 获取：

- browser info。
- CDP WebSocket URL。
- VNC/noVNC/browser view URL。
- 文件上传/下载 API。
- 脚本执行或 shell 执行 API。

本地已验证 `/v1/browser/info` 可以返回 `cdp_url` 和 `vnc_url`。内网需要确认这些 URL 对 Host Backend Pod 是否可达；如果返回的是 sandbox 内部 `127.0.0.1`，必须通过 APIG 或 AIO 路由改写成 Host 可达地址。

### 5.3 RPA 主链路 smoke

真实 AIO 可达后，按以下顺序验证：

1. Host create sandbox，拿到 `sandboxId`。
2. Host 查询 status，直到 `running`。
3. Host 获取 browser info，拿到 Host 可达 CDP URL。
4. `CDPConnector` 连接 AIO browser。
5. 前端打开 RPA recorder 页面，浏览器画面可访问。
6. 注入现有 recorder listener JS。
7. 手动点击/输入/导航能产生 raw event，并进入 accepted trace。
8. 自然语言操作浏览器成功。
9. 生成脚本成功。
10. 生成脚本在 AIO browser 中执行成功。
11. 删除 sandbox 后查询返回 404 或不可用状态。

## 6. 第一阶段验收标准

可以认为 native AIO 第一阶段完成，当且仅当：

- 同一用户会话绑定一个 AIO sandbox，且多实例下不会重复创建有效 sandbox。
- sandbox create/status/refresh/delete 都有脱敏诊断。
- 浏览器/CDP 连接不依赖本机 Chromium。
- recorder listener JS 能注入 AIO browser 并捕获用户操作。
- 自然语言操作、区域选择、脚本生成、脚本执行在 AIO browser 中形成闭环。
- 生成 Skill 的执行不依赖录制现场本机文件系统。
- 删除或超时后的 sandbox 不会被继续复用。
- runtime metadata 不泄露 token、页面内容或产品事实真源。

## 7. 暂缓 Runtime Adapter 的边界

第一阶段不要做：

- 发布 `rpaclaw-runtime-adapter` 镜像作为上线必要依赖。
- 在 AIO sandbox 内启动 RpaClaw adapter 服务。
- 把 Host Backend 搬进 AIO sandbox。
- 让 AIO 或 adapter 定义 AcceptedTrace、Skill 真源、artifact 归因或 Harness expected signals。
- 为了模拟 AIO 平台而在外网重写一套完整调度服务。

Runtime Adapter 只有在原生 API 明确无法覆盖时再恢复，例如：

- AIO 原生 file/shell/browser API 不稳定或不满足权限隔离。
- 内网 APIG 无法安全暴露 CDP 或文件能力。
- 多版本 AIO API 差异需要一层沙箱内语义适配。
- 需要在执行面统一封装复杂工具链或 MCP server 生命周期。

## 8. 已知待观察项

- Windows 本机后端使用 `uvicorn --reload` 时，Playwright 当前事件循环可能不支持 subprocess；本地 workaround 是不加 `--reload` 启动后端。内网 Linux/EKS 环境应重新验证。
- AIO native + 区域选择 + 自然语言操作在一次 GitHub Trending 场景下出现过 planner 等待较慢，但本地模式不复现。该问题暂不阻塞 native AIO 路线，后续可通过 timing 日志定位 snapshot/planner/executor 耗时。
- GitHub Trending 页面中的项目 504 问题已在不使用 `--reload` 后消失，本地验证不再视为 AIO 原生能力阻塞。
