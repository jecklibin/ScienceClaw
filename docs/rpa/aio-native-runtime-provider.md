# AIO Native Runtime Provider 本地验证说明

## 背景

原有 `aio_fixed` / `aio` 路线假设 AIO 沙箱内运行 RpaClaw Runtime Adapter 服务，Host Backend 通过 adapter semantic API 访问浏览器、文件、脚本执行和诊断能力。实际验证 `agent-infra/sandbox` 后，AIO 原生 API 已能直接提供 `/v1/browser/info`、CDP、VNC、文件与代码执行等能力。因此本地验证 RPA 录制主链路时，可以先走更短路径：Host Backend 直接连接固定原生 AIO 沙箱，不要求沙箱内启动 Runtime Adapter。

该模式用于验证“现有技能录制产品链路能否把执行面从本机 browser 切换到 AIO browser”，不是验证真实内网 AIO create/status/delete 生命周期。

## 使用方式

先启动一个本机 AIO sandbox，例如：

```powershell
docker run -d --security-opt seccomp=unconfined --name aio-native-manual -p 18090:8080 enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

然后启动 Host Backend 时配置：

```powershell
$env:STORAGE_BACKEND = "local"
$env:RUNTIME_MODE = "aio_native"
$env:AIO_BASE_URL = "http://127.0.0.1:18090"
$env:AIO_RUNTIME_SANDBOX_ID = "aio-native-manual"
```

`AIO_BASE_URL` 也可以写作 `AIO_NATIVE_BASE_URL`。`RUNTIME_MODE=aio_native` 会优先于 `STORAGE_BACKEND=local`，使 RPA CDP connector 连接 AIO sandbox，而不是启动本机 Chromium。

## 当前实现边界

`AioNativeRuntimeProvider` 返回固定 `SessionRuntimeRecord`：

- `namespace=aio-native`
- `rest_base_url` / `route_base_url` 指向 `AIO_BASE_URL`
- `sandbox_id` 来自 `AIO_RUNTIME_SANDBOX_ID`，未配置时使用本地默认值
- `refresh_runtime()` 调用 `{AIO_BASE_URL}/v1/browser/info`
- `browser_view_url` 从 AIO 返回的 `vnc_url` 归一化为 Host 可访问地址
- metadata 仅记录 `runtime_contract=aio_native`、`browser_info_ok`、`cdp_url_available` 等非敏感诊断

该 provider 不创建、不销毁 AIO sandbox，也不调用 adapter `/health`。

如果配置了真实内网生命周期 API，`AioNativeRuntimeProvider` 会从本地固定沙箱模式切换为真实 lifecycle 模式：

```powershell
$env:RUNTIME_MODE = "aio_native"
$env:AIO_NATIVE_API_BASE_URL = "https://{APIG-Endpoint}"
$env:AIO_NATIVE_API_TOKEN = "<host-to-aio-token-if-needed>"
$env:AIO_NATIVE_TEMPLATE_ID = "lf-jsdklalfdan5sf1a1dd1"
$env:AIO_NATIVE_REFRESH_DURATION_SECONDS = "300"
$env:AIO_NATIVE_BASE_URL = "https://{host-reachable-browser-route}/{sandbox_id}"
```

默认生命周期路径为：

- create: `POST /api/livefunction/sandboxes`
- status: `GET /api/livefunction/sandboxes/{sandbox_id}`
- refresh: `POST /api/livefunction/sandboxes/refresh/{sandbox_id}`
- delete: `DELETE /api/livefunction/sandboxes/{sandbox_id}`

真实 lifecycle 模式下，create payload 只包含 AIO 模板字段：

```json
{"templateId": "lf-jsdklalfdan5sf1a1dd1"}
```

Host 会把 AIO 返回的 `data.sandboxId`、`data.templateId`、`data.status`、`data.cpu`、`data.memory`、`data.timeout`、`data.startAt`、`data.endAt` 映射到 `SessionRuntimeRecord` 和脱敏 metadata。`running` 映射为 `ready`，`stopped/error/404` 映射为 `missing`。

`AIO_NATIVE_BASE_URL` 可以包含 `{sandbox_id}` 占位符，用于把内网 APIG 或 browser/CDP 路由模板归一成 Host 可访问的 `rest_base_url` / `route_base_url`。低层 URL 拼接应留在 provider/CDP connector 层，不要散落到 RPA recorder、Skill compiler 或前端。

## 待产品级验证

该模式接入后，优先验证：

1. 手动点击/输入是否经现有 recorder bridge 生成 accepted trace。
2. `framenavigated`、下载、新标签页、iframe 归因是否与本机 CDP 模式一致。
3. 自然语言操作是否能在 AIO page 上完成 snapshot、planner、executor、repair 和 accepted trace。
4. 区域选择的前端画布坐标、后端 `element-bounds`、`region/analyze` 与 region-scoped natural language 是否对齐。
5. trace 编译后的 Skill 是否能在 AIO browser 上回放。

如果这些产品能力都成立，内网优先适配 AIO 原生 API；Runtime Adapter 保留为原生 API 缺口出现时的第二阶段方案。
