---
name: recording-creator
description: "通过对话发起并编排 RPA 技能录制、业务流程技能录制、MCP 工具录制，以及多段 workflow 录制。凡是用户提到“录制流程”“录个业务流程技能”“录制一个 MCP 工具”“把网页操作录成技能”“先下载再继续处理文件”“把上一段输出作为下一段输入”“准备发布录制技能/工具”这类诉求时，都应触发本技能。Use this whenever the user wants to record a browser workflow, capture an MCP operation, build a multi-segment recorded automation from chat, bind one segment's output to another segment's input, test a recorded workflow, repair it, or publish it as a reusable skill/tool."
---

# recording-creator

一个用于主对话内 workflow 录制的内置技能。它的职责与 `skill-creator` / `tool-creator` 同级，但面向“先录制、再验证、再发布”的可复用自动化流程。

> **ENVIRONMENT: RpaClaw** — 录制入口由本技能接管，不要依赖 `sessions.py` 的关键词或正则旁路。由本技能决定什么时候开始 run、什么时候继续 segment、什么时候做整体测试、什么时候准备发布；后端工具只负责执行本技能已经决定好的动作。

## 角色边界

### 本技能负责什么

- 识别用户是在创建新的录制 run，还是继续已有 run。
- 判断当前段是交互录制段，还是纯脚本/文件处理段。
- 明确多段 workflow 的输入、输出、artifact 和测试意图。
- 驱动生成、测试、修复、发布四个阶段。
- 在发布阶段复用 `propose_skill_save` / `propose_tool_save` 完成最终收口。

### 本技能不负责什么

- 不直接把录制请求短路到聊天路由。
- 不把“是不是录制诉求”再交回后端工具做二次判定。
- 不直接写入正式 `Skills` / `Tools` 目录。
- 不把多段输入输出关系只写在自然语言里而不做结构化绑定。

## 何时使用

在这些场景必须优先使用本技能：

- 用户要录制网页/RPA 流程。
- 用户要录制业务流程技能。
- 用户要录制 MCP 操作或 MCP 工具。
- 用户要继续已有录制，例如“再录一段”“接着处理刚才下载的文件”。
- 用户要把前一段输出作为后一段输入。
- 用户要开始整体测试、修复录制、准备发布。

不要在这些场景触发本技能：

- 用户是在执行已经保存的技能或工具。
- 用户是在查看、编辑、优化一个普通 `SKILL.md`，但并不需要录制流程。
- 用户只是普通浏览网页或临时调用工具，并不想沉淀为录制工作流。

## 标准工作流

本技能默认推进如下生命周期：

1. `inspect_recording_runs`
2. `start_recording_run` 或 `continue_recording_run`
3. `add_script_recording_segment`（当当前段不是浏览器录制，而是脚本/文件处理/MCP 后处理时）
4. `bind_recording_segment_io`（当用户要求跨段传递输出/文件/JSON/文本时）
5. `begin_recording_test`
6. 修复或补录
7. `prepare_recording_publish`
8. `propose_skill_save` 或 `propose_tool_save`

## Phase 1：创建或恢复 Recording Run

### 先检查，再决定新开还是续录

在调用 `start_recording_run` 之前，先用 `inspect_recording_runs` 判断当前会话是否已经存在未发布的 run。

优先续录的场景：

- 用户说“继续下一段”“再录一段”“接着处理”。
- 当前会话里已有一个活跃 run，且用户显然是在补充前序流程。

优先新开 run 的场景：

- 用户明确说“新录一个流程”“另外录一个技能/工具”。
- 当前会话没有可继续的 run。

### start_recording_run 的使用约束

- 只有在你已经判断用户要开始新的录制 run 时才调用。
- `kind` 由你决定并显式传入，常见值为 `rpa`、`mcp`、`mixed`。
- `publish_target` 只有在用户已经明确要保存为 `skill` 或 `tool` 时才传；否则可以留空，稍后再决定。
- 调用后前端会自动打开录制弹窗或录制工作台。

## Phase 2：Segment 录制与配置

### 交互录制段

当当前段需要浏览器操作或 MCP 实时交互时：

- 使用 `start_recording_run` 或 `continue_recording_run`
- 让用户在录制弹窗中完成操作
- 段完成后总结：
  - 这段做了什么
  - 录到了哪些步骤
  - 产生了哪些 artifact
  - 下一段可以复用哪些输出

### 非交互段

当用户说的是“把下载下来的文件转换一下”“再处理刚才得到的 JSON”“继续做脚本转换”这类诉求时，不要强制再次打开录制台。

这类场景应优先使用 `add_script_recording_segment`：

- `title`: 片段标题
- `purpose`: 片段用途
- `script`: 实际处理脚本
- `entry`: 片段脚本路径
- `params_json`: 参数定义
- `inputs_json`: 结构化输入定义
- `outputs_json`: 结构化输出定义

脚本段是正式 workflow segment，不是临时聊天说明。必须沉淀到当前 run。

### 参数和凭证配置

每个 segment 的参数必须进入结构化 `params` / `inputs`，不要只写在自然语言回复里。

- 普通默认值写入参数的 `original_value`。
- 每个参数必须有清晰 `description`，优先来自用户说明、segment input 描述或录制步骤描述。
- 需要用户调用时覆盖的值，应暴露为 workflow 级输入。
- 敏感参数应标记 `sensitive: true`，并在用户选择已保存凭证后保存 `credential_id`。
- 发布后的技能会生成与普通 RPA 录制一致的 `params.json`，用于技能页面默认参数配置、凭证引用、workflow runner 默认值加载和应用内执行时自动注入。

不要生成或依赖 `params.schema.json` / `credentials.example.json` 这类并行参数或示例文件；参数和凭证引用的唯一事实来源是 `params.json`。

## Phase 3：多段输入输出绑定

当用户表达以下语义时，必须做结构化绑定，而不是只口头描述：

- “把第一段输出作为第二段输入”
- “用刚才下载的文件继续处理”
- “把提取的标题传给下一段搜索”
- “第二段输入就是上一段导出的 JSON”

这时必须调用 `bind_recording_segment_io`，显式建立：

- `source_segment_id`
- `output_name`
- `output_type`
- `target_segment_id`
- `input_name`
- `input_type`

绑定后，要在回复里明确告诉用户：

- 哪个输出被绑定到了哪个输入
- 这个绑定会用于测试、发布和最终执行

如果 artifact 或输出不唯一，必须回问，不要猜。

## Phase 4：测试与修复

### 开始测试

用户说“开始测试”“整体测试”“验证一下”时：

- 调用 `begin_recording_test`
- 单段录制进入单段测试
- 多段 workflow 进入整体测试

### 测试失败时

优先推进轻量修复，不要直接建议全部重录：

- 切换候选定位器
- 单步重放
- 补充缺失的输入/输出绑定
- 修正脚本段参数或输出

只有当局部修复无效时，才建议：

- 重录本步
- 重录本段

## Phase 5：发布准备与保存

### 准备发布

测试通过后，再调用 `prepare_recording_publish`。不要跳过测试直接发布。

发布前你应明确四件事：

- 当前要发布哪一个 run
- 保存目标是 `skill` 还是 `tool`
- 最终名称、描述、输入输出是否合理
- 当前多段 workflow 是否都纳入发布

### 收口规则

- 目标是 skill：调用 `prepare_recording_publish` 后，进入 `propose_skill_save`
- 目标是 tool / MCP 工具：调用 `prepare_recording_publish` 后，进入 `propose_tool_save`
- 发布提示应与 `skill-creator` / `tool-creator` 的保存确认体验一致

不要直接写正式库目录，也不要绕开保存确认条。

## 沟通规则

- 默认把录制理解为“可继续扩展的多段 workflow”，不是一次性单段脚本。
- 每段完成后都给出高信号摘要：标题、步骤数、artifact、输入输出、下一步建议。
- 对用户说“可以继续录下一段、开始测试、或者准备发布”时，要基于当前 run 状态给出，不要输出一套固定话术。
- 如果会话里存在多个未发布 run，且用户意图不明确，必须回问要操作哪一个。

## 反模式

以下做法是错误的：

- 在 `sessions.py` 里写关键词/正则特判来旁路聊天。
- 让 `start_recording_run` 再次自行判断“这是不是录制意图”。
- 把脚本处理段只保留在回复文字里，不写入当前 run。
- 已有跨段依赖时，不做 `bind_recording_segment_io`。
- 未测试就直接发布。
- 跳过 `propose_skill_save` / `propose_tool_save` 直接写正式目录。

## 成功结果

一个完成良好的录制会话，最终应沉淀出：

- 一个 `Recording Run`
- 一个或多个已配置、已测试的 `segment`
- 可跨段引用的 `artifact`
- 结构化的输入输出绑定
- 与普通录制技能一致的 `params.json` 参数/凭证配置
- 测试验证结果和必要修复记录
- 最终可发布的 skill/tool staging 产物
