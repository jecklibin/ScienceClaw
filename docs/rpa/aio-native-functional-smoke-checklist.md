# AIO Native RPA 功能闭环 Smoke Checklist

本文用于外网/本机和内网 Agent 共同验收第一阶段路线：

```text
AIO 原生 API + Host Backend 控制面适配
```

目标不是证明真实内网 AIO 调度平台已经可用，而是证明除真实 `create/status/refresh/delete` 接入外，录制技能主功能已经能在 AIO browser 执行面下形成闭环。Runtime Adapter 暂缓，不作为本阶段上线依赖。

## 0. 验收边界

本阶段必须证明：

- Host Backend 使用 `RUNTIME_MODE=aio_native` 后连接 AIO browser/CDP，而不是启动本机 Chromium。
- 录制技能、手动事件、自然语言操作、区域选择、多 tab、脚本生成执行、Skill 保存和 replay 主链路可用。
- 真实 AIO lifecycle API 可以在外网用固定 sandbox 或 fake lifecycle 代替，但 provider 字段、路径、状态映射和脱敏诊断必须已经预留。
- 下载/文件/产物能力不能阻塞主链路；没有下载场景时不强行构造完整文件闭环。

本阶段不要求：

- 在 AIO sandbox 内启动 RpaClaw Runtime Adapter。
- 发布 adapter 镜像。
- 外网模拟完整内网 AIO 调度平台。
- 用 Harness expected signals 或历史报告替代真实产品录制事实。

## 1. 本机启动前置条件

启动一个本机 AIO sandbox：

```powershell
docker run -d --security-opt seccomp=unconfined --name aio-native-manual -p 18090:8080 enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

确认 browser API 可用：

```powershell
Invoke-RestMethod http://127.0.0.1:18090/v1/browser/info
```

后端使用 `aio_native`，Windows 本机不要加 `--reload`：

```powershell
$env:PYTHONPATH = "RpaClaw"
$env:STORAGE_BACKEND = "local"
$env:RUNTIME_MODE = "aio_native"
$env:AIO_BASE_URL = "http://127.0.0.1:18090"
$env:AIO_RUNTIME_SANDBOX_ID = "aio-native-manual"
python -m uvicorn backend.main:app --app-dir .\RpaClaw --host 0.0.0.0 --port 8000
```

前端：

```powershell
cd .\RpaClaw\frontend
$env:BACKEND_URL = "http://localhost:8000"
npm run dev
```

## 2. 功能闭环 Smoke 步骤

建议用一个公开页面验证，例如 GitHub Trending。不要把 GitHub 当成架构特殊规则；它只是一个包含列表、导航、多 tab 和可读页面状态的验证样本。

| 序号 | 验收项 | 操作 | 通过条件 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | AIO 执行面接入 | 前端点击“录制技能”创建 RPA session | session 创建成功；后端日志显示通过 CDP 连接；AIO browser 画面可访问 | `/api/v1/rpa/session/start` 返回 `status=success`；`session.sandbox_session_id` 可映射到 runtime record；后端日志包含 CDP URL |
| 2 | 不启动本机 Chromium | 在同一环境下启动录制 | Host Backend 不应拉起本机 Chromium 作为录制浏览器 | `RUNTIME_MODE=aio_native`；`CDPConnector` 使用 runtime `route_base_url/rest_base_url` 获取 `/v1/browser/info` |
| 3 | listener JS 注入 | 在 AIO browser 页面点击、输入、导航 | manual event 进入后端并成为 accepted trace 或可诊断 manual diagnostic | timeline 中出现 click/fill/navigate；trace 带 page URL/title 与 tab 证据 |
| 4 | 多 tab | 从页面打开新 tab，再切回旧 tab 操作 | tab 列表更新；active tab 正确；事件不归到旧 tab | `/api/v1/rpa/session/{session_id}/tabs` 返回多个 tab；trace `signals.tab` 或 `signals.popup` 有 source/target tab |
| 5 | 自然语言点击 | 输入“点击列表第二个项目”一类指令 | AIO page 被真实操作；成功后产生 accepted trace | `/chat` SSE 有 `agent_result`；新增 trace `source=ai` 或等价 AI 录制证据 |
| 6 | 自然语言输入 | 在输入框页面让 Agent 输入文本 | 页面状态改变；trace 中有 fill/action 证据 | timeline 中出现 fill；input value 不只存在于页面状态 |
| 7 | 自然语言导航 | 让 Agent 打开指定 URL 或进入页面链接 | AIO browser URL 改变；trace 保留导航动作或 post navigation 证据 | trace `action=navigate` 或 `signals.navigation/post_navigation` |
| 8 | 自然语言读取页面 | 让 Agent 读取页面标题、列表项或某块文本 | 结果写入 runtime results 或 data capture trace | `runtime_results` 或 accepted trace 中有可诊断输出 |
| 9 | 区域选择 | 前端框选列表区域，随后输入“点击列表第二个项目” | 后端能解析区域上下文，并基于选区完成操作 | `region/analyze` 返回 `region_id`；对应 trace 保留 `region_context` / `region_scope` |
| 10 | 脚本生成 | 结束录制后生成脚本 | 生成成功，且脚本通过 CDP/runtime context 面向 AIO browser 执行 | `/generate` 返回 `status=success`；脚本不写死本机 Chromium 启动 |
| 11 | 脚本执行 | 点击测试/执行生成脚本 | 至少一个真实网页场景 replay 成功；失败时有可诊断 error/log | `/test` 返回 success 或结构化失败；失败包含 trace/step/error |
| 12 | Skill 保存 | 保存为 Skill | 生成 `SKILL.md` 和 `skill.py`；Skill 元数据包含录制来源 | `/save` 返回 success；Skill 目录中有 `skill.py` |
| 13 | 下载/文件不阻塞 | 如果场景无下载，不额外构造下载；如果有下载，确认 Host 可见 | 无下载时主链路不失败；有下载时可定位到 Host 可拉回路径 | `downloads_dir`、artifact metadata 或明确的待适配项 |
| 14 | 资源清理 | 停止录制或释放 runtime | session 清理后不会继续复用已删除/过期 sandbox | `/session/{session_id}/stop` 成功；真实内网阶段再验证 DELETE/404 |

## 3. 需要记录的最小证据

外网/本机 smoke 完成后，至少记录以下信息，方便内网 Agent 对照：

- 启动命令和关键环境变量，尤其是 `RUNTIME_MODE=aio_native`、`AIO_BASE_URL`、`AIO_RUNTIME_SANDBOX_ID`。
- AIO `/v1/browser/info` 返回中是否有 `cdp_url` 和 `vnc_url`，不要记录 token。
- 录制 session id、runtime sandbox id、active tab id。
- `/tabs` 返回的 tab 数量、URL/title、active tab。
- timeline 中手动 trace、AI trace、区域 trace 的数量和关键 action。
- `/generate`、`/test`、`/save` 的结果摘要。
- 失败时记录错误阶段：lifecycle、browser info、CDP connect、listener injection、manual event、AI planner/executor、region analyze、compile、replay、save。

## 4. 内网适配 Smoke 顺序

内网 Agent 不应从 Runtime Adapter 入手，优先按以下顺序替换真实 AIO 服务：

1. 配置 `AIO_NATIVE_API_BASE_URL`、`AIO_NATIVE_TEMPLATE_ID`、鉴权 token/header。
2. 验证 `POST /api/livefunction/sandboxes` 能返回 `data.sandboxId` 和 `status=running`。
3. 验证 `GET /api/livefunction/sandboxes/{sandboxId}` 的 `running/stopped/error/404` 能映射到 Host runtime `ready/missing`。
4. 确认 Host Backend Pod 能访问 browser/CDP/file/shell 路由；如果 AIO 返回的是 sandbox 内部 `127.0.0.1`，必须在 runtime provider/CDP connector 层改写为 Host 可达地址。
5. 在 EKS 多实例下验证同一 `session_id` 并发只绑定一个 sandbox；重复 create 的落败方必须删除自己刚创建但未绑定的 sandbox。
6. 跑第 2 节功能闭环 smoke。
7. 最后验证 `refresh` 和 `delete`，删除成功或 404 后 Host record 不应继续复用。

## 5. 当前已知观察项

- Windows 本机后端使用 `uvicorn --reload` 时，Playwright 可能命中 `asyncio.create_subprocess_exec()` 的事件循环限制；本机 smoke 请不加 `--reload`。内网 Linux/EKS 仍需重新验证。
- AIO native + 区域选择 + 自然语言在 GitHub Trending 场景中出现过执行较慢，但最终可成功；该问题暂不阻塞第一阶段路线，后续如需定位，应优先补 timing 日志，区分 snapshot、planner、executor、页面网络和 CDP 延迟。
- 如果内网 AIO 原生 file/shell/download API 的路由或权限与本机不同，先保证不阻塞录制/生成/replay 主链路，再补文件产物闭环。

## 6. 完成判定

可以声明本阶段本机可交接，当且仅当：

- 第 2 节中 1-12 项至少有一次本机 AIO native 真实操作证据。
- 第 13 项不阻塞主链路，若未覆盖下载，已明确列入内网/后续验证。
- 第 14 项在本机固定 sandbox 下可停止 session；真实 DELETE/404 由内网 smoke 最终确认。
- `docs/rpa/aio-native-internal-handoff.md` 已同步真实内网 API 字段、EKS 多实例约束和 Runtime Adapter 暂缓边界。
