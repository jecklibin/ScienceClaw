# RpaClaw 合并后全量回归测试报告

日期：2026-05-07  
仓库：`/Users/samdediannao/rpa-agent/rpaclaw/ScienceClaw`  
分支：`master`，已对齐 `origin/master`

## 总结

这轮按“主体功能是否受合并影响”重新扩大了范围，不只测模型认证。覆盖了后端全量单测、前端构建/测试/type-check、后端服务启动、核心 HTTP API、模型验证保存、动态 token 测试、桌面端构建、task-service 编译、评测前端构建入口。

当前状态：不建议直接发公司网络做最终验收。

可以确认的正向结果：

- 后端服务能以 local storage 模式启动。
- `/health`、`/ready`、认证状态、模型列表、凭据列表、会话创建/详情/列表、技能列表、工具列表均返回 200。
- 静态 token 和动态 token 的真实 mock 链路能跑通。
- 新增静态认证模型能通过“验证并保存”落库。
- 动态 token 测试接口能返回 token 响应和字段池。
- 新增动态 token 模型能通过“验证并保存”落库。
- 主前端 `npm run build` 通过，构建产物 `vite preview` 可访问。
- 桌面壳 `electron-app npm run build` 通过。
- task-service 关键 Python 文件编译通过。

主要阻塞和风险：

- 后端全量测试仍有 `18 failed`，其中 RPA、CDP/browser 参数、默认模型选择、local tool stdout 都涉及主体功能，不只是模型认证。
- 前端 dev server 和 Vitest 在当前 Node 25 下无法启动，原因是 `vite-plugin-monaco-editor` 使用了 Node 25 不支持的 `fs.rmdirSync(..., { recursive: true })`。
- 前端 `type-check` 基线失败较多，说明当前无法把类型检查当作绿色门禁。
- 追加修复：动态 token 已删除旧的 `token_path/expires_in_path/expires_at_path` 字段，改为缓存完整响应体并在注入时用 `{ $.path }` 取字段。

## 测试环境

- macOS 本机
- 后端 Python：`RpaClaw/backend/.venv/bin/python`，Python 3.14.2
- 后端 `.venv` 未安装 pytest；全量测试使用 `uv run --with pytest --with pytest-asyncio`
- 前端 Node：v25.5.0
- 前端 npm：11.8.0
- 主前端依赖已安装
- `rpa-eval-app/frontend` 依赖未安装
- 模型认证 mock：`RpaClaw/backend/tests/fixtures/mock_model_auth_server.py`

## 后端全量测试

命令：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/private/tmp/uv-cache uv run --no-project --with pytest --with pytest-asyncio python -m pytest tests -q
```

结果：

```text
620 passed, 18 failed, 1 warning
```

失败清单：

- `tests/deepagent/test_tool_execution.py::test_local_tool_executor_runs_tool_runner_locally`
- `tests/runtime/test_cdp_connector.py::test_local_launch_uses_relaxed_security_browser_args[asyncio]`
- `tests/test_model_static_auth_http.py::test_context_window_probe_sends_static_auth_headers_over_http`
- `tests/test_model_static_auth_http.py::test_chat_model_sends_static_auth_headers_over_http`
- `tests/test_rpa_assistant.py` 5 个失败
- `tests/test_rpa_generator.py::PlaywrightGeneratorTests::test_generate_script_local_runner_uses_relaxed_browser_security_settings`
- `tests/test_rpa_mcp_semantic_inferer.py` 2 个失败
- `tests/test_rpa_recording_runtime_agent.py` 5 个失败
- `tests/test_user_asset_ownership.py::test_resolve_default_model_config_reports_user_model_resolution[asyncio]`

归因：

- `local tool executor`：Python 3.14 下 LangChain/Pydantic v1 warning 混入 stdout，导致断言输出不等。
- CDP/RPA generator：浏览器启动参数和测试期望不一致，属于 RPA/browser 主链路风险。
- 静态模型认证 HTTP：测试期望静态 token 覆盖 `Authorization`，实际当前设计是 `Authorization: Bearer sk-test`，额外 header 正常传递。
- RPA assistant / semantic inferer：代码从 `get_llm_model` 迁移到 `get_llm_model_for_user` 后，测试 patch 点没同步。
- recording runtime agent：planner 诊断结构、lazy import、token floor 相关行为与测试期望不一致。
- user asset ownership：`resolve_default_model_config("user-1")` 返回 `None`，需要确认新增模型认证过滤条件是否影响用户默认模型解析。

## 后端服务和核心 API 冒烟

启动方式：

```bash
PYTHONPATH=RpaClaw \
STORAGE_BACKEND=local \
AUTH_PROVIDER=none \
RPA_CLAW_HOME=/private/tmp/rpaclaw-full-smoke-20260507 \
RpaClaw/backend/.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 19001
```

启动结果：通过。

启动日志确认：

- local storage 初始化成功。
- `DS_API_KEY` 未设置时跳过系统模型创建。
- 默认 admin 用户 bootstrap 成功。
- FastAPI startup complete。

核心接口冒烟结果：

| 功能 | 接口 | 结果 |
| --- | --- | --- |
| 健康检查 | `GET /health` | 200，`{"status":"ok"}` |
| ready 检查 | `GET /ready` | 200，`{"status":"ready","storage":"ok"}` |
| 前端配置 | `GET /api/v1/client-config` | 200 |
| 认证状态 | `GET /api/v1/auth/status` | 200，local none 模式认证为 admin |
| 模型列表 | `GET /api/v1/models` | 200 |
| 凭据列表 | `GET /api/v1/credentials` | 200 |
| 创建会话 | `PUT /api/v1/sessions` | 200，返回 `session_id` |
| 会话列表 | `GET /api/v1/sessions` | 200，能看到新会话 |
| 会话详情 | `GET /api/v1/sessions/{session_id}` | 200 |
| 技能列表 | `GET /api/v1/sessions/skills` | 200 |
| 工具列表 | `GET /api/v1/sessions/tools` | 200 |

结论：基础后端服务、认证状态、本地存储、模型/凭据/会话/技能/工具 API 没有启动级阻断。

## 模型验证保存主流程

### 静态 Token 模型

请求配置：

- Base URL：`http://127.0.0.1:18080/v1`
- API Key：`sk-test`
- Model：`mock-static-model`
- 额外 Header：
  - `X-Gateway-Token: static-token`
  - `X-Tenant: tenant-a`

结果：`POST /api/v1/models` 返回 200，模型保存成功。

落库结果：

- 模型 `api_key` 保存为 `null`
- 模型关联 `auth_credential_id`
- 凭据中创建 `kind: model_auth`
- 认证配置中包含：
  - `Authorization: Bearer {{ api_key }}`
  - `X-Gateway-Token: {{ x_gateway_token }}`
  - `X-Tenant: {{ x_tenant }}`
- 敏感值以 `has_value: true` 方式返回，不明文返回

### 动态 Token 测试接口

请求配置：

- Token URL：`http://127.0.0.1:18080/token`
- Method：`POST`
- Header：`X-Client-Id: {{ client.username }}`
- Query：`aud={{ client.domain }}`
- Body：

```json
{
  "client_id": "{{ client.username }}",
  "client_secret": "{{ client.password }}",
  "tenant": "{{ client.domain }}"
}
```

结果：`POST /api/v1/models/test-dynamic-token` 返回 200。

响应包含：

- `ok: true`
- token 响应 body
- 字段池：
  - `$.data.access_token`
  - `$.data.token_type`
  - `$.data.expires_in`
  - `$.data.tenant.id`
  - `$.data.tenant.name`
  - `$.data.client.id`
  - `$.trace_id`

### 动态 Token 模型

请求配置：

- Base URL：`http://127.0.0.1:18080/v1`
- API Key：`base-api-key`
- Model：`mock-dynamic-model`
- Token 请求同上
- 模型请求 Header 注入：
  - `Authorization: Bearer {$.data.access_token}`
  - `X-Tenant-Id: { $.data.tenant.id }`

结果：`POST /api/v1/models` 返回 200，模型保存成功。

落库结果：

- 模型关联 `auth_credential_id`
- 凭据中创建 `kind: model_auth`
- 动态 token 变量中 `client_password` 为敏感值
- `client_username/client_domain` 非敏感值

追加修复：

旧的 `token_path/expires_in_path/expires_at_path` 字段已从前后端数据结构中删除。动态 token 不再单独映射 token 字段，而是缓存完整响应体，在模型请求 Header 注入阶段用 `{ $.path }` 引用响应字段。

## 模型认证 mock 真实调用链路

手工 mock 冒烟结果：通过。

实际请求路径：

```text
['/v1/chat/completions', '/token', '/v1/chat/completions']
```

静态请求成功返回：

```text
static auth ok
```

动态请求成功返回：

```text
dynamic auth ok
```

说明：

- 静态额外 header 能传到模型服务。
- 动态 token 能先获取响应体，再用 `{ $.path }` 形式从响应体注入模型请求 Header。
- 动态 token 当前能用完整响应体字段，不依赖固定 token 映射卡片。

## 前端主体测试

### 主前端构建

命令：

```bash
cd RpaClaw/frontend
npm run build
```

结果：通过。

warning：

- Browserslist 数据过期
- `SessionItem.vue` duplicate key `bg-amber-400`
- CSS minify 语法 warning
- chunk size 过大

### 构建产物预览

命令：

```bash
npm run preview -- --host 127.0.0.1 --port 5177
```

结果：通过。

`GET /` 返回 200，能返回构建后的 `index.html`。

### 前端 dev server

命令：

```bash
npm run dev -- --host 127.0.0.1 --port 5176
```

结果：失败。

错误：

```text
TypeError [ERR_INVALID_ARG_VALUE]: The property 'options.recursive' is no longer supported.
at vite-plugin-monaco-editor/dist/workerMiddleware.js
```

判断：当前 Node 25 与 `vite-plugin-monaco-editor` 不兼容。生产构建可过，但开发态和测试态都受影响。

### 前端组件测试

命令：

```bash
npm test -- ModelSettings.test.ts --run
```

结果：失败，未进入测试用例。

原因同 dev server：`vite-plugin-monaco-editor` 在 Node 25 下启动失败。

### 前端类型检查

命令：

```bash
npm run type-check
```

结果：失败。

与本次改动直接相关的错误：

```text
src/components/settings/ModelSettings.vue(1169,62): error TS6133: 'label' is declared but its value is never read.
```

其他错误大量分布在 `ActivityPanel.vue`、`ChatMessage.vue`、`SessionItem.vue`、`ChatPage.vue`、`desktopWindow.ts` 等文件，说明当前 type-check 基线本身不是绿色。

## 桌面端和其他子项目

### Electron 桌面壳

命令：

```bash
cd electron-app
npm run build
```

结果：通过。

说明：TypeScript 编译和 wizard 静态资源复制均成功。

### task-service

命令：

```bash
RpaClaw/task-service/.venv/bin/python -m py_compile \
  RpaClaw/task-service/app/main.py \
  RpaClaw/task-service/app/scheduler.py
```

结果：通过。

说明：没有发现 task-service 测试文件；本轮只做了关键入口编译检查。

### rpa-eval-app frontend

命令：

```bash
cd rpa-eval-app/frontend
npm run build
```

结果：失败。

错误：

```text
sh: vue-tsc: command not found
```

判断：该子项目依赖未安装，无法判断是否受本次合并影响。

## 风险分级

### P0/P1：发公司网络前建议先处理

1. 默认模型解析回归风险。
   `resolve_default_model_config("user-1")` 在测试中返回 `None`。这可能影响 chat/session/RPA 自动选默认模型，是主体功能风险。

2. RPA 主链路测试失败。
   RPA assistant、semantic inferer、recording runtime、browser launch 参数相关测试均有失败。即使部分只是测试 patch 点没同步，也需要修到可确认行为。

3. 动态 token 持久化 `"None"` 问题。
   未填写的 token path 字段保存为字符串 `"None"`，后续编辑、兼容和显示都可能混乱。

4. 前端 dev/test 在当前 Node 25 下不可用。
   这会影响你本机和公司网络现场调试。生产构建能过，但开发态不能起。

### P2：需要同步或清理

1. 静态 token `Authorization` 测试期望需要跟设计定稿。
   如果 API Key 固定负责 `Authorization`，静态 token 只做额外 header，则应更新测试；如果企业网关要静态 token 占用 `Authorization`，则实现需要改。

2. 前端 `type-check` 基线不绿。
   不一定是本次改动造成，但会降低发版信心。

3. `SessionItem.vue` duplicate key 和 CSS warning。
   构建不阻断，但最好清理。

## 建议下一步

1. 修 `resolve_default_model_config` 测试失败，优先确认用户模型/系统模型默认选择逻辑。

2. 修动态 token 持久化 `"None"` 问题。

3. 同步 RPA 测试 patch 点和诊断结构，确认 RPA 真实模型调用仍走认证后的模型配置。

4. 固定 Node 到 20/22 LTS 后重跑前端 dev、Vitest；或者升级 Monaco 插件以支持 Node 25。

5. 再跑一次回归：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/private/tmp/uv-cache uv run --no-project --with pytest --with pytest-asyncio python -m pytest tests -q

cd ../frontend
npm run build
npm run dev -- --host 127.0.0.1 --port 5176
npm test -- ModelSettings.test.ts --run
```
