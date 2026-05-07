# RpaClaw 模型认证测试报告

日期：2026-04-30

追加说明（2026-05-06）：按最新产品决策，第一阶段静态 Token 仅保留 Header 配置，静态 Body 配置已移除；动态 Token 运行面结构和 resolver 扩展点保留，但管理面入口先灰显禁用。

## 测试范围

本轮按照 `docs/rpaclaw-model-auth-design.md` 的设计，针对当前实现补充并执行了以下测试：

- 后端 `ModelAuthResolver / ResolvedModelAuth`
  - 静态 Header 模板解析。
  - 缺失 credential。
  - 未知 alias。
  - query 模板渲染。
  - 动态 token 首次请求。
  - 动态 token 缓存命中。
  - 动态 token 完整响应体缓存。
  - 动态 token 的 headers/query/body 模板渲染。
  - form body token 请求。
  - 嵌套数组响应字段提取。
  - token 注入 headers/query/body。
- 后端模型保存逻辑
  - 创建静态 Header 时将明文写入 Credential Vault。
  - `ModelConfig.auth_config` 只保存 credential 引用和模板，不保存明文。
  - 编辑时 Header 值留空保留旧 credential。
  - 替换 Header 值时创建新 credential，并清理旧 credential。
  - 删除 Header / 切换为无认证后清理旧 credential，不再写入注入模板。
- LLM 构造链路
  - `_SafeChatOpenAI` 构造参数能接收 `default_headers/default_query`。
  - `get_llm_model_for_user` 兼容没有 `auth_config` 的老模型配置。
- 前端模型配置页
  - 默认“无额外认证”创建 payload。
  - 静态 Header 表格添加、删除、保存 payload。
  - Header JSON 导入合法对象。
  - 拒绝非法 JSON 和非对象根节点。
  - 编辑静态 Header 时不回显 secret，只显示已配置占位。
  - 动态 Token 入口展示但禁用。

## 新增测试文件

- `RpaClaw/backend/tests/test_model_auth.py`
- `RpaClaw/backend/tests/test_model_auth_routes.py`
- `RpaClaw/frontend/src/components/settings/ModelSettings.test.ts`

## 执行命令与结果

### 后端目标测试

命令：

```bash
uv run --with pytest --with pytest-asyncio --with-requirements RpaClaw/backend/requirements.txt pytest RpaClaw/backend/tests/test_model_auth.py RpaClaw/backend/tests/test_model_auth_routes.py
```

结果：

```text
13 passed, 1 warning in 2.60s
```

说明：

- 使用 `uv run` 临时加载后端依赖和 pytest。
- 警告来自 Python 3.14 下 `langchain_core` 对 Pydantic V1 兼容层的提示，与本功能测试无关。

### 前端目标组件测试

命令：

```bash
npm run test -- src/components/settings/ModelSettings.test.ts
```

结果：

```text
5 passed
```

说明：

- 测试过程中有 i18n key 缺失警告，示例包括 `Custom Models`、`Add Model`、`Provider` 等。
- 测试过程中有 Dialog 可访问性警告：`Missing Description or aria-describedby`。
- 上述警告未导致目标组件测试失败。

### Computer Use 管理面冒烟测试

目标页面：

```text
http://127.0.0.1:5173/
```

结果：

```text
passed
```

验证路径：

- 打开设置弹窗并进入“模型”页。
- 点击“添加模型”。
- 滚动到“认证配置”区域。
- 切换到“静态 Header”。
- 点击“添加 Header”，确认可以新增 Header 行。
- 在 JSON 输入框导入测试数据：`{"Authorization":"Bearer demo-token","X-Tenant":"tenant-a"}`。
- 点击“导入 Header JSON”，确认导入后生成两行 Header，并且 Header 值以密码框形式遮挡显示。

说明：

- 本次仅做管理面交互冒烟测试，没有点击“验证并保存”，避免创建测试模型或触发真实模型服务请求。
- 测试使用的是虚拟 token，没有使用真实凭据。

### 变更后补充校验

命令：

```bash
python3 -m compileall RpaClaw/backend/model_auth.py RpaClaw/backend/models.py RpaClaw/backend/route/models.py RpaClaw/backend/deepagent/engine.py RpaClaw/backend/deepagent/agent.py RpaClaw/backend/route/chat.py RpaClaw/backend/route/rpa.py RpaClaw/backend/route/sessions.py RpaClaw/backend/rpa/assistant.py RpaClaw/backend/rpa/mcp_semantic_inferer.py RpaClaw/backend/rpa/recording_runtime_agent.py
```

结果：

```text
passed
```

命令：

```bash
git diff --check
```

结果：

```text
passed
```

说明：

- 主流程补充调整后，重新做了 Python 语法编译和 diff 空白检查。
- 后端目标 pytest 的二次重跑因为 `uv` 需要重新下载大量依赖而停止；对应下载进程已终止，`/private/tmp/uv-cache` 依赖缓存已清理。

### 动态 Token 补充校验

命令：

```bash
backend/.venv/bin/python -m compileall backend/model_auth.py backend/models.py backend/route/models.py backend/deepagent/engine.py
```

结果：

```text
passed
```

补充脚本验证：

```text
passed
```

覆盖点：

- token_request.url 可以引用凭据变量。
- token_request.headers 可以引用凭据变量。
- token_request.body_type=form 时，body 会以 form data 发送。
- inject.headers 和 inject.body 可以使用 `{ $.path }` 引用 token 响应字段，例如 `{ $.items[0].access.token }`。
- 动态 Token 凭据保存时真实 password 进入 Credential Vault，auth_config 只保存 credential_id 和模板。

说明：

- 当前 `.venv` 没有安装 pytest，因此本轮没有触发 `uv` 重新下载依赖。
- 后端目标 pytest 文件已补充动态 Token 用例；完整 pytest 可在依赖环境准备好后运行。

### 前端类型检查

命令：

```bash
npm run type-check
```

结果：

```text
failed
```

失败集中在既有非模型认证文件，例如：

- `src/components/ActivityPanel.vue`
- `src/components/ChatMessage.vue`
- `src/components/DesktopTitleBar.vue`
- `src/components/SessionItem.vue`
- `src/pages/ChatPage.vue`
- `src/utils/desktopWindow.ts`

本轮新增的 `ModelSettings.test.ts` 未出现在 `vue-tsc` 报错列表中。

## 发现的问题

1. 前端 locale 缺少多个模型设置页使用的英文 key，导致测试运行时出现 i18n warning。
   - 影响：不阻塞功能，但会污染测试输出，也可能让英文界面显示原始 key。
   - 本轮未修改产品 locale 文件，避免越过测试 worker 边界。

2. `ModelSettings.vue` 的 DialogContent 缺少描述或 `aria-describedby`，Vitest/jsdom 下输出可访问性警告。
   - 影响：不阻塞功能，但建议后续由实现 worker 补充 DialogDescription 或显式 aria 配置。

3. `npm run type-check` 在仓库既有文件中失败。
   - 影响：无法用全量 type-check 作为本轮模型认证变更的通过信号。
   - 失败项与模型认证新增测试无直接关联，本报告按“既有失败”记录。

## 未覆盖风险

- Computer Use 已覆盖静态 Header 管理面的新增行和 JSON 导入冒烟路径，但未点击“验证并保存”，因此未写入真实模型配置。
- 未做真实 LLM 网关请求，动态 token 使用 fake HTTP client 验证请求渲染、缓存和注入行为。
- 动态 Token 管理面入口当前已灰显，JSON 管理面和 OAuth2 Client Credentials 表单化简单模式留到后续阶段。
- `verify_model_connection` 的真实 provider SDK 行为未做端到端验证，本轮通过构造链路单测确认 `default_headers/default_query` 被传递。
