# RPA MCP Gateway 设计

## 背景

当前 RPA 录制流程可以把用户操作导出为技能描述文件和 Playwright 脚本。这个产物适合录制者本人复用，但不适合直接共享给其他 agent 调用，主要原因是：

- 录制脚本可能包含登录流程、账号输入、密码输入或绑定到录制人的 credential_id。
- 导出的技能默认以录制者的执行环境为中心，不具备调用方身份注入能力。
- 外部 agent 更适合通过 MCP tool 调用明确的自动化能力，而不是理解和执行一个 RPA 技能目录。

本设计新增一个集中式 RPA MCP Gateway。录制技能可以转换为 Gateway 中的一个 MCP tool。转换后的 tool 不包含登录步骤，也不保存录制人的账号、密码、cookies 或 storage state。调用方每次调用 tool 时传入自己的 cookies，Gateway 注入 cookies 后执行后续业务步骤，使工具以调用方权限完成操作。

## 目标

1. 支持把录制生成的 RPA 技能转换为集中式 Gateway 中的 MCP tool。
2. 自动识别并删除登录相关步骤，避免共享录制人的登录流程和凭据。
3. MCP tool 调用方每次直接传入 cookies，Gateway 不长期保存 cookies。
4. 通过 allowed domains 校验限制 cookie 注入范围。
5. 复用现有 RPA 录制、脚本生成、参数配置、MCP 注册和 MCP tool bridge 基础设施。
6. 给用户提供清洗预览和确认流程，避免自动识别错误导致工具不可用或泄露敏感步骤。

## 非目标

- 第一版不做 cookie 托管 session，也不生成 auth_session_id。
- 第一版不处理账号密码登录、验证码、2FA、扫码登录等认证流程。调用方必须提供已登录 cookies。
- 第一版不把每个 RPA 工具导出为独立 MCP server 包。
- 第一版不默认返回页面截图。调试截图必须显式开启，并且需要经过脱敏策略。
- 第一版不做跨机器分布式执行队列。并发限制先在单个 Gateway 进程内实现。

## 架构

新增集中式 Gateway 模块：

```text
RpaClaw/backend/
├── rpa/
│   ├── mcp_converter.py       # 录制步骤 -> RPA MCP 工具定义
│   ├── mcp_gateway.py         # MCP streamable_http endpoint runtime
│   ├── mcp_tool_registry.py   # 工具定义存取、启用、禁用、发现
│   └── mcp_executor.py        # cookies 校验、注入、Playwright 执行
├── route/
│   └── rpa_mcp.py             # 转换预览、保存、管理、Gateway endpoint
└── tests/
    ├── test_rpa_mcp_converter.py
    ├── test_rpa_mcp_executor.py
    └── test_rpa_mcp_route.py
```

Gateway 作为一个 HTTP MCP server 对外暴露，建议在系统 MCP 配置里注册为：

```yaml
servers:
  - id: rpa_gateway
    name: RPA MCP Gateway
    description: Converted RPA automations exposed as cookie-authorized MCP tools
    transport: streamable_http
    enabled: true
    default_enabled: false
    url: http://localhost:12001/api/v1/rpa-mcp/mcp
```

Gateway 的 `list_tools()` 动态读取已启用的 RPA MCP 工具定义。每个工具在外部 agent 看来是普通 MCP tool，例如：

```text
rpa_download_invoice(cookies, month)
rpa_fetch_order_status(cookies, order_id)
```

## 数据模型

新增 RPA MCP 工具定义。local 模式保存到本地配置目录，Docker/MongoDB 模式保存到 MongoDB collection `rpa_mcp_tools`。

```json
{
  "id": "rpa_mcp_abc123",
  "user_id": "user_xxx",
  "name": "download_invoice",
  "tool_name": "rpa_download_invoice",
  "description": "使用调用方 cookies 下载发票",
  "enabled": true,
  "source": {
    "type": "rpa_skill",
    "session_id": "session_xxx",
    "skill_name": "invoice_skill"
  },
  "allowed_domains": ["example.com"],
  "post_auth_start_url": "https://example.com/dashboard",
  "steps": [],
  "params": {},
  "input_schema": {
    "type": "object",
    "properties": {
      "cookies": {
        "type": "array",
        "description": "Playwright-compatible browser cookies for allowed domains"
      },
      "month": {
        "type": "string",
        "description": "Invoice month"
      }
    },
    "required": ["cookies", "month"]
  },
  "sanitize_report": {
    "removed_steps": [0, 1, 2, 3],
    "removed_params": ["username", "password"],
    "warnings": []
  },
  "created_at": "2026-04-18T00:00:00Z",
  "updated_at": "2026-04-18T00:00:00Z"
}
```

`steps` 保存清洗后的步骤结构，而不是保存生成后的完整 Python 文件。这样后续 locator 修复、参数调整和脚本生成逻辑可以继续复用 `PlaywrightGenerator`。

## 转换流程

### 1. 转换预览

用户在 RPA 保存或测试通过后点击“转换为 MCP 工具”。后端读取 session steps 和 params，执行 `RpaMcpConverter.preview()`：

1. 归一化步骤，复用 generator 里的去重、tab 推断和信号归一化能力。
2. 识别登录段。
3. 删除登录段中的 steps。
4. 删除或脱敏登录相关 params。
5. 推断 `post_auth_start_url`。
6. 推断 `allowed_domains`。
7. 生成 MCP input schema。
8. 返回 sanitize report 和预览数据。

前端展示预览，不立即保存。

### 2. 用户确认

前端允许用户调整：

- MCP tool 名称和描述。
- 登录段删除范围。
- `post_auth_start_url`。
- `allowed_domains`。
- 哪些参数保留为业务输入。

用户确认后调用保存 API。后端再次执行清洗和校验，保存工具定义。

### 3. Gateway 暴露

工具定义启用后，Gateway 的 `list_tools()` 返回对应 MCP tool。`call_tool()` 收到调用后根据 tool name 查找定义并执行。

## 登录段识别

登录步骤删除必须在结构化 steps 层完成，不在生成后的 Python 源码里做字符串删除。

第一版采用启发式规则和用户确认结合：

- 强信号：
  - `fill` step 带 `sensitive=true`。
  - `fill` step 的 value 为 `{{credential}}`。
  - locator 或 label 指向 password 字段。
- 中信号：
  - 同一阶段出现 username、email、phone、account 等输入字段。
  - click 目标文本或 role name 包含 login、sign in、登录、提交、继续。
  - 登录前后 URL host 相同但 path 从 `/login`、`/signin`、`/auth` 跳转到业务页。
- 弱信号：
  - 登录后出现 dashboard、home、console、workspace 等业务路径。
  - 密码输入后的第一个 navigation 或明显页面切换。

默认删除范围：

1. 从登录表单前最近一次登录页 navigation 开始。
2. 到登录成功后的第一个业务 URL 之前结束。
3. 如果无法可靠确定起止点，只标记 warnings，要求用户手动确认。

不允许静默删除不确定步骤。只要存在歧义，前端必须展示 warning。

## Cookie 调用契约

每个转换后的 MCP tool 都必须要求 `cookies` 参数。格式采用 Playwright `BrowserContext.add_cookies()` 兼容对象数组：

```json
[
  {
    "name": "sessionid",
    "value": "redacted",
    "domain": ".example.com",
    "path": "/",
    "httpOnly": true,
    "secure": true,
    "sameSite": "Lax"
  }
]
```

Gateway 执行前必须校验：

- `cookies` 必须是非空数组。
- 每个 cookie 必须包含 `name`、`value`，并且必须有 `domain` 或 `url`。
- cookie domain 必须属于工具定义的 `allowed_domains` 或其子域。
- 不允许把 cookie 注入到与 `post_auth_start_url` 无关的域名。
- 日志、错误信息、审计记录不得包含 cookie value。

校验失败时返回 MCP tool error，错误信息只说明字段或 domain 不合法，不回显敏感值。

## 执行流程

`RpaMcpExecutor.call(tool_def, arguments)`：

1. 从 arguments 中取出 `cookies`，其余字段作为业务参数。
2. 校验 cookie domain 和 `post_auth_start_url`。
3. 获取 Playwright browser。
   - local 模式优先复用现有 `LocalCDPConnector` 或直接 launch headless browser。
   - Docker 模式连接 sandbox CDP。
4. 创建全新 browser context。
5. `context.add_cookies(cookies)`。
6. 新建 page，goto `post_auth_start_url`。
7. 使用清洗后的 steps 和业务 params 生成执行脚本。
8. 执行 `execute_skill(page, **kwargs)`。
9. 返回 `_results`、下载文件信息或结构化错误。
10. 关闭 context，释放本次 cookies。

每次调用必须使用独立 context，不允许在不同调用之间复用 cookies、storage state 或 page。

## MCP Gateway Runtime

Gateway endpoint 需要支持 MCP streamable_http。实现可以复用项目当前依赖的 `mcp` Python SDK，或者在 FastAPI route 中实现最小化 MCP 协议适配。优先选择 SDK，避免自写协议细节。

运行时职责：

- `list_tools`: 读取当前用户或系统可见的 enabled RPA MCP tools。
- `call_tool`: 根据 tool name 分发到 `RpaMcpExecutor`。
- 输入 schema: 直接来自工具定义。
- 返回值: 使用 MCP content 和 structuredContent 表达。

如果当前 MCP SDK 的 server 端与 FastAPI 集成成本过高，第一版可以采用 Gateway 子应用挂载方式，而不是在普通 REST route 中手写协议。

## API 设计

新增 `backend/route/rpa_mcp.py`。

管理 API：

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/api/v1/rpa-mcp/session/{session_id}/preview` | 生成转换预览 |
| POST | `/api/v1/rpa-mcp/session/{session_id}/tools` | 确认并保存工具定义 |
| GET | `/api/v1/rpa-mcp/tools` | 列出 RPA MCP 工具 |
| GET | `/api/v1/rpa-mcp/tools/{tool_id}` | 查看工具定义和清洗报告 |
| PUT | `/api/v1/rpa-mcp/tools/{tool_id}` | 更新名称、描述、启用状态、allowed domains |
| DELETE | `/api/v1/rpa-mcp/tools/{tool_id}` | 删除工具 |
| POST | `/api/v1/rpa-mcp/tools/{tool_id}/test` | 使用手动传入 cookies 测试执行 |

MCP endpoint：

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/api/v1/rpa-mcp/mcp` | RPA MCP Gateway streamable_http endpoint |

管理 API 必须要求当前 RpaClaw 用户登录。MCP endpoint 的鉴权策略需要和现有 MCP server 注册方式匹配：本地私有使用可以先依赖 localhost；远程共享前必须增加 Gateway token 或反向代理鉴权。

## 前端设计

新增或扩展 RPA 配置流程：

1. 在 RPA Test/Save 成功后增加“转换为 MCP 工具”按钮。
2. 新增转换确认页或弹窗，展示：
   - 工具名和描述。
   - 将暴露给 MCP 的 input schema。
   - 将删除的登录步骤。
   - 保留的业务步骤。
   - `post_auth_start_url`。
   - `allowed_domains`。
   - warnings。
3. 用户可手动调整登录段范围和 allowed domains。
4. 保存后跳转到 Tools 页面或 RPA MCP 工具管理分区。

Tools 页面增加 RPA MCP Gateway 区域：

- 查看 Gateway endpoint。
- 查看已转换工具列表。
- 启用、禁用、删除工具。
- 查看 schema 和 sanitize report。
- 使用 cookies JSON 做测试调用。

## 安全与隐私

必须满足：

- 工具定义不保存调用方 cookies。
- 工具定义不保存录制人的 cookies、storage state、账号、密码或 credential_id。
- 清洗后的 params 不包含 sensitive 登录参数。
- 日志中不打印 cookies、Authorization header、密码字段。
- 调用错误不回显 cookie value。
- 每次调用独立 browser context。
- allowed domains 是强制校验，不是提示。
- 下载文件路径只返回当前 workspace 或沙盒允许目录下的文件。

审计日志可以记录：

- tool id。
- 调用时间。
- 执行耗时。
- 成功或失败。
- cookie 数量。
- cookie domain 列表。
- 错误类型。

审计日志不得记录：

- cookie value。
- 完整请求体。
- 页面 HTML。
- 截图。
- 录制人 credential_id。

## 错误处理

常见错误：

- `missing_cookies`: 调用方未传 cookies。
- `invalid_cookie_schema`: cookies 不是 Playwright 兼容格式。
- `cookie_domain_not_allowed`: cookie domain 不在 allowed domains。
- `auth_not_effective`: cookies 注入后仍停留在登录页。
- `step_failed`: 某个业务步骤执行失败。
- `download_failed`: 下载动作失败或文件不可访问。
- `timeout`: 执行超时。

`auth_not_effective` 的判定方式：

- goto `post_auth_start_url` 后 URL 被重定向到登录路径。
- 页面存在明显登录表单。
- 业务流程第一步 locator 不可见，同时检测到登录信号。

返回错误应包含可操作信息，但不包含敏感数据。

## 测试策略

后端单元测试：

- 登录段识别：包含 username/password/login click/navigation 的录制步骤应被删除。
- 非登录表单：普通搜索框、业务表单不应被误删。
- 参数清洗：sensitive params、credential_id、登录账号默认值应被删除。
- allowed domains：从 URL 和 cookies 正确推断与校验。
- input schema：cookies 必填，业务参数保留，登录参数删除。
- executor：每次调用创建独立 context，并关闭 context。
- 日志脱敏：错误路径不输出 cookie value。

路由测试：

- preview 需要登录并只允许 session owner。
- 保存工具定义后 list tools 可见。
- 禁用工具后 Gateway 不再暴露。
- 测试调用缺少 cookies 返回明确错误。

集成测试：

- 使用 mock Playwright context 验证 add_cookies 在 goto 前执行。
- 使用 mock MCP runtime 验证 Gateway list_tools 和 call_tool 输出。
- 使用包含下载步骤的 RPA 工具验证返回下载元数据。

前端测试：

- 转换预览正确展示删除步骤和 warnings。
- 用户调整登录段后保存 payload 正确。
- Tools 页面能展示 Gateway endpoint、tool schema 和 sanitize report。

## 迁移与兼容

- 现有 RPA 技能导出不变。
- 新能力作为额外转换路径添加，不影响 `SkillExporter` 当前行为。
- 现有用户 MCP server 管理不变，只新增一个可注册的 Gateway server。
- 现有 credential vault 仍服务普通 RPA 技能；转换后的 MCP tool 不依赖录制人的 credential vault。

## 分阶段交付

### Phase 1: 后端核心

- 增加 RPA MCP 工具定义模型和 registry。
- 增加 converter preview/save。
- 增加登录段识别、参数清洗、allowed domains 推断。
- 增加 executor 的 cookie 校验和隔离 context 执行。
- 增加 Gateway list_tools/call_tool。

### Phase 2: 前端管理

- 增加转换确认 UI。
- 增加 RPA MCP 工具列表、启用禁用、查看 schema 和清洗报告。
- 增加测试调用入口。

### Phase 3: 安全和运维增强

- Gateway token 或共享密钥鉴权。
- 并发限制和执行队列。
- 审计日志页面。
- 可选的 auth_session_id cookie 托管模式。

## 第一版决策

1. Gateway endpoint 第一版加入 `mcp_servers.yaml`，`enabled=true`，`default_enabled=false`。用户需要在具体 session 中手动启用。
2. local 模式下 Gateway 执行浏览器默认 headless。测试调用可以通过管理 API 显式请求 headed，但 MCP tool 正式调用不暴露 headed 参数。
3. 单个 RPA MCP tool 允许配置多个 allowed domain。自动推断只来自录制 URL，额外 domain 必须由用户在转换确认页手动添加。
4. 第一版 Gateway 仅声明为 localhost 私有使用。远程共享必须经过反向代理或后续 Gateway token 鉴权能力，不能直接裸露到公网。
