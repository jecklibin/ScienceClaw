# 对话式多段 MCP 工具录制实现说明

## 文档状态

- 状态：已按当前实现更新
- 语言：中文
- 适用范围：主对话中的“录制工具 / 录制 MCP 工具”能力
- 当前入口技能：`recording-creator`
- 当前发布目标：`publish_target=tool` 的 `RecordingRun` 最终保存为 RPA MCP 工具定义

## 背景

对话式录制不再是“录完一段立即保存成工具”。当前模型把录制过程拆成三层：

- `segment`：workflow 中的一段，可来自 RPA 录制、脚本处理或后续扩展的 MCP/LLM 元数据段。
- `RecordingRun`：一次对话内可持续追加的多段草稿，负责保存 segment 顺序、输入输出、artifact 和测试状态。
- `RPA MCP Tool`：用户最终确认发布后写入 MCP 工具注册表的可调用工具。

这样可以支持“先录一段下载/提取，再把输出传给下一段搜索/转换”的多段场景，也避免把片段级配置误当成最终工具配置。

## 当前架构

### 1. 主入口由内置技能接管

用户在主对话中表达以下诉求时，应由 `recording-creator` 接管：

- 录制工具、录制 MCP 工具、录制业务流程技能。
- 继续录下一段、补一个片段、追加脚本处理。
- 把上一段输出、artifact 或参数绑定到下一段输入。
- 执行完整测试、修复、准备发布。

后端 `sessions.py` 不再负责用关键词或正则判断“是不是录制诉求”。后端工具只执行内置技能已经决定好的动作。

### 2. 状态机保持现有 RecordingRun 状态

当前实现没有引入新的 `segment_configuring`、`workflow_testing`、`publishing` 状态名，而是继续使用 `backend/recording/models.py` 中的现有状态：

- `draft`
- `recording`
- `waiting_user`
- `processing_artifacts`
- `ready_for_next_segment`
- `testing`
- `needs_repair`
- `ready_to_publish`
- `blocked`
- `failed`
- `completed`
- `saved`

关键规则：

- 完成一个 segment 后进入 `ready_for_next_segment`。
- 完整测试开始时进入 `testing`。
- 测试成功后进入 `ready_to_publish`，测试失败进入 `needs_repair`。
- `ready_to_publish` 后仍允许用户继续录制新片段，新片段会使测试结论失效并重置测试状态。
- 发布目标必须保留在 `publish_target` 中，tool run 不能被默认参数覆盖成 skill。

### 3. 前端页面职责

当前没有单独实现最终 `ToolPublishPage`。实际页面职责如下：

- `RecorderPage`：在大弹窗中完成浏览器/RPA 录制。
- `SegmentConfigurePage`：当前是片段配置入口，内部复用 `McpToolEditorPage` 的 segment configure mode，用于配置步骤、输入输出、参数、预览测试并完成当前片段。
- `McpToolEditorPage`：仍同时服务独立 MCP 工具编辑、已保存工具查看，以及对话录制片段配置。后续如果继续演进，应优先抽取共享编辑组件，而不是复制页面逻辑。
- `RecordingPublishDraftModal`：主对话聊天框上方的发布确认弹窗，用于最终收集名称、描述、触发示例、输入输出说明并提交发布。
- `ChatPage`：展示紧凑 segment 卡片、run 级 action prompt，并处理录制、测试、发布事件。

当前选择复用 `McpToolEditorPage` 是为了避免短期重复实现 MCP schema、参数、步骤、预览测试等复杂表单。设计上的约束是：片段配置模式不能显示“保存为 MCP 工具”的最终语义，只能保存当前 segment 并返回对话。

### 4. 发布与执行

skill 和 tool 的发布链路分流：

- skill 目标：`prepare_recording_publish` 生成 save-ready skill artifacts，最终继续走 `propose_skill_save`。
- tool / MCP 工具目标：`prepare_recording_publish` 或发布接口构建并保存 `RpaMcpToolDefinition`，不再走普通 `propose_tool_save`，也不生成普通技能目录。

MCP 工具保存时包含两类数据：

- `steps` / `params` / `input_schema` / `output_schema`：用于 MCP 工具列表、详情页、schema 展示和独立 MCP 工具兼容。
- `workflow_package`：对话多段工具执行的事实来源，包含 `workflow.json`、`params` 和每个 segment 的可执行源文件。

运行已保存的对话式多段 MCP 工具时，`RpaMcpExecutor` 优先执行 `workflow_package`。如果工具没有 `workflow_package`，才回退到传统单段 `steps + params` 生成 Playwright 脚本。

### 5. 输入输出绑定

多段依赖必须结构化保存，不能只写在自然语言回复里。当前绑定来源包括：

- 任意历史 segment output。
- 任意历史 artifact。
- workflow 级参数。
- 用户显式输入。

绑定会沉淀到 segment `inputs[].source_ref`，完整测试、发布和最终 MCP 工具运行都会使用同一份绑定。

### 6. 参数与凭证

当前参数事实来源是 `params.json` 或 MCP 工具定义中的 `params` 字段：

- 普通参数保留 `type`、`description`、`original_value`、`required`。
- 敏感参数保留 `sensitive` 和 `credential_id`，默认值使用 `{{credential}}` 占位。
- 应用内执行时默认值先从参数配置加载，再由调用时传入参数覆盖。
- 当前设计不生成 `params.schema.json` 或空的 `credentials.example.json`，避免出现多个参数事实来源。

MCP 工具的公开 `input_schema` 只暴露最终调用方需要传入的非敏感参数。敏感值应通过凭证绑定和执行链路注入，不应暴露给 MCP 调用方。

## 当前反模式

以下做法不应再引入：

- 在 `sessions.py` 中用关键词、正则或硬编码分支旁路处理录制诉求。
- 录完单个 segment 后立即显示“保存为 MCP 工具”。
- tool / MCP 目标调用普通 `propose_tool_save` 或生成普通工具脚本占位文件。
- 只保存最后一个 segment，丢失前面片段或跨段绑定。
- 只依赖 `steps` 执行多段 MCP 工具，而不执行 `workflow_package`。
- 生成 `params.schema.json`、`credentials.example.json` 作为并行配置源。

## 已知折中

- `SegmentConfigurePage` 目前是 `McpToolEditorPage` 的包装入口，真实表单逻辑仍在 `McpToolEditorPage`。这与最理想的页面拆分不同，但能复用成熟 MCP 编辑能力。后续重构应抽取共享的 steps/schema/params 编辑组件，再让独立 MCP 编辑页和 segment 配置页分别组合使用。
- `workflow_package` 当前存储为 `dict[str, Any]`，便于兼容现有 MCP 工具注册表。后续如果 schema 稳定，可以抽成 Pydantic 模型，减少字段漂移风险。
- `steps` 仍会随 MCP 工具保存，用于展示和兼容；多段工具执行以 `workflow_package` 为准。

## 验收标准

- 主对话中“录制工具”最终发布为 MCP 工具，不被保存成 skill。
- 工具录制支持多个 segment，最终保存包含全部 segment。
- 跨段输入输出绑定在完整测试、保存后执行中都生效。
- 完整测试由 `begin_recording_test` 直接执行 workflow，不打开录制/测试弹窗。
- 已保存 MCP 工具运行时返回内容必须满足 `output_schema`，不能返回额外 `status` / `outputs` 导致 MCP SDK 校验失败。
- 切换会话再切回后，用户消息、agent 回复、segment 卡片和发布提示都能从历史事件恢复。
