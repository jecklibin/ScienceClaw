# 会话内技能化 RPA / MCP 录制最终方案

## 目标

把当前“独立录制页 + 工具库/技能库入口”的 RPA / MCP 录制能力，彻底改造成和 `skill-creator` / `tool-creator` 同等级的主对话内置能力。

本方案必须同时满足以下目标：

- 用户可以直接在主对话中说“我要录制个业务流程技能”“帮我录一个 MCP 工具”“先下载文件再继续处理”。
- 录制入口由内置技能接管，而不是 `sessions.py` 基于正则或关键词旁路处理。
- 聊天右侧录制台与 `/rpa/recorder`、`/rpa/test` 共享同一套录制核心，不再维护缩水版 workbench。
- 录制结果支持多段编排：`segment -> artifact -> next segment`。
- 录制能力具备完整生命周期：生成、测试验证、修复、发布。
- 发布路径与 `skill-creator` / `tool-creator` 对齐，最终走统一的保存确认与发布机制。

## 非目标

本期不做以下内容：

- 可视化流程画布
- 任意拖拽重排步骤
- 手写 Playwright 代码编辑器
- 通用循环/条件 DSL
- 自由拾取新元素生成任意新定位器
- 多个并行录制 workbench

## 当前问题

当前实现存在三类结构性问题：

### 1. 入口错误

当前主对话内的录制触发依赖 `sessions.py` 中的显式意图识别与短路逻辑。这种模式：

- 强侵入
- 强定制
- 不可扩展
- 与 `skill-creator` / `tool-creator` 的技能化入口不一致

这会导致录制能力不是“主对话内置技能”，而只是“聊天路由上的特判分支”。

### 2. 前端分叉

当前聊天右侧使用了单独实现的 `RecordingWorkbench.vue`，它只保留了：

- 大画布
- 地址栏
- 标签页
- 输入转发

但没有复用现有录制页的关键能力：

- 步骤列表
- 录制助手
- Agent/确认流
- 录制状态同步
- 诊断与定位信息
- 测试验证与失败修复

结果就是右侧录制台和 `/rpa/recorder`、`/rpa/test` 成为两套能力完全不对齐的系统。

### 3. 生命周期缺失

当前实现只打通了“创建 run -> 录一段 -> 回灌摘要”的局部链路，没有形成类似 `skill-creator` / `tool-creator` 的完整闭环：

- 生成
- 测试验证
- 失败修复
- 用户确认发布
- 保存到正式技能/工具库

这意味着它还不是一个“产品级录制能力”，只是一个录制原型。

## 最终方案概述

采用“三层统一”架构：

1. 入口层：由新的内置技能 `recording-creator` 完全接管主对话中的录制诉求。
2. 编排层：由 `recording` 域负责 run / segment / artifact / lifecycle 状态机。
3. 交互层：由共享录制内核同时支撑聊天右侧录制台、录制页、测试页。

这三层的边界如下：

- `recording-creator` 负责理解意图、驱动录制流程、决定何时进入录制/测试/发布阶段。
- `recording orchestrator` 负责结构化状态与跨段产物。
- `rpa` / `mcp` adapter 负责具体录制执行。
- 前端共享录制内核负责所有真实录制交互，不再由独立页面和聊天面板各自实现。

## 一、入口改成内置技能

### 核心原则

彻底删除 `sessions.py` 中任何“识别到录制意图后直接短路聊天”的逻辑。

主对话中的录制能力必须像 `skill-creator` / `tool-creator` 一样工作：

- agent 在主对话中命中 `recording-creator`
- 读取技能说明
- 按技能工作流决定创建录制 run、推进 segment、测试、发布

### recording-creator 的定位

`recording-creator` 是一个新的内置技能，职责不是“只识别一句话”，而是完整管理会话内录制生命周期。

它的职责包括：

- 识别用户是在要求录制技能、录制工具、录制 MCP 工作流，还是继续已有录制 run
- 创建或恢复 `Recording Run`
- 决定当前段是交互录制段还是非交互处理段
- 将上一段 artifact 显式绑定到下一段
- 在用户准备发布时，切换到测试验证阶段
- 测试通过后，引导进入保存确认与发布

### 触发范围

它必须覆盖的典型表达包括：

- “我要录制个业务流程技能”
- “帮我录一个下载流程”
- “录制一个 MCP 工具”
- “把这个网页操作录成技能”
- “继续处理刚下载的文件”
- “把这个录制流程发布成技能”

### 与 skill-creator / tool-creator 的关系

`recording-creator` 不是二者的替代品，而是上游入口。

关系如下：

- 用户要通过对话录一个可复用流程时，先进入 `recording-creator`
- 当录制产物已经准备发布成 skill 时，发布阶段复用 `skill-creator` 的保存要求与出口
- 当录制产物本质上是 MCP / 工具能力时，发布阶段复用 `tool-creator` 的保存要求与出口

也就是：

- `recording-creator` 负责“录”
- `skill-creator` / `tool-creator` 负责“收口发布标准”

## 二、统一录制生命周期

录制能力必须拥有和 `skill-creator` / `tool-creator` 同等级的完整生命周期。

### 生命周期阶段

定义如下状态链：

1. `draft`
2. `recording`
3. `processing_artifacts`
4. `ready_for_next_segment`
5. `testing`
6. `needs_repair`
7. `ready_to_publish`
8. `saved`
9. `failed`

### 1. 生成阶段

生成阶段负责：

- 创建 `Recording Run`
- 创建第一个 `Segment`
- 打开录制工作台
- 捕获步骤、信号、产物
- 在段落结束后生成结构化摘要

### 2. 测试验证阶段

测试验证阶段必须成为最终方案的一部分，而不是后补。

测试验证需复用现有 `/rpa/test` 核心能力，至少包括：

- 生成可测试脚本
- 在测试环境执行
- 展示执行结果
- 显示失败步骤
- 给出失败候选定位器
- 支持切换候选定位器后重试

也就是当前 `TestPage` 里已有的“失败步骤 + 候选定位器 + 重试”逻辑要被纳入共享内核。

### 3. 修复阶段

当测试失败或录制步骤失效时，进入 `needs_repair`。

第一期修复能力只做轻量版本：

- 查看当前定位器
- 查看候选定位器
- 切换候选定位器
- 立即重新校验
- 单步重放
- 重录本步
- 重录本段

不做：

- 手工自由编写定位器
- 页面重新选点
- 任意改步骤语义

### 4. 发布阶段

发布阶段必须对齐现有主对话里的保存机制。

录制 run 在进入 `ready_to_publish` 后：

- 先把产物整理到 session workspace/staging
- 如果目标是 skill，则走 `propose_skill_save`
- 如果目标是 tool / MCP tool，则走 `propose_tool_save`
- 前端继续使用现有保存确认条
- 最终落到现有 `save_skill_from_session` / `save_tool_from_session`

这保证录制能力不是另造一套发布机制，而是复用成熟的保存出口。

## 三、统一数据模型

### Recording Run

```json
{
  "id": "run_xxx",
  "session_id": "chat_session_id",
  "user_id": "user_id",
  "type": "rpa | mcp | mixed",
  "status": "draft | recording | processing_artifacts | ready_for_next_segment | testing | needs_repair | ready_to_publish | saved | failed",
  "active_segment_id": "seg_xxx",
  "segments": [],
  "artifact_index": [],
  "publish_target": "skill | tool | null"
}
```

### Segment

```json
{
  "id": "seg_xxx",
  "run_id": "run_xxx",
  "kind": "rpa | mcp | chat_process | mixed",
  "intent": "下载论文 PDF",
  "status": "recording | completed | failed | blocked",
  "imports": {},
  "exports": {},
  "steps": [],
  "artifacts": []
}
```

### Step

分为：

- `ui_step`
- `tool_step`

并保留两层表示：

- 底层原始执行信息
- 上层语义步骤

语义步骤用于摘要、编排、测试、发布，不直接依赖原始事件流。

### Artifact

```json
{
  "id": "artifact_xxx",
  "run_id": "run_xxx",
  "segment_id": "seg_xxx",
  "name": "downloaded_pdf",
  "type": "file | text | json | table",
  "path": "/workspace/xxx/paper.pdf",
  "value": null,
  "labels": ["download", "pdf"]
}
```

## 四、多段编排

### 核心原则

下一段不能依赖模型记忆“刚才那个文件”，而要依赖显式 artifact 绑定。

### 规则

1. 每个 segment 必须定义输入、输出和产物。
2. 每个 segment 结束后必须完成 artifact 注册。
3. 下一段只能引用已注册 artifact。
4. 若引用不唯一，必须回问。
5. 若 artifact 丢失或失效，进入 `blocked`。

### 例子

第一段：

```json
{
  "intent": "下载财报 PDF",
  "exports": {
    "report_pdf": "{{artifacts.downloaded_pdf.path}}"
  }
}
```

第二段：

```json
{
  "intent": "把刚下载的 PDF 转成 Markdown",
  "imports": {
    "input_pdf": "{{seg_1.exports.report_pdf}}"
  }
}
```

### 第一阶段支持的跨段输入

- 单个文件
- 单个文本
- 单个 JSON

暂不支持：

- 多文件批量映射
- 条件分支
- 循环
- 自由表达式编辑器

## 五、共享录制内核

### 目标

聊天右侧录制台和录制页、测试页必须共用同一套录制核心。

### 当前问题

当前的 `RecordingWorkbench.vue` 是单独实现，能力远少于：

- `RecorderPage.vue`
- `TestPage.vue`

这导致：

- 功能缺失
- 显示效果差
- 修复和测试能力断裂
- 两套逻辑分叉

### 共享内核拆分方案

从现有 `RecorderPage.vue` 与 `TestPage.vue` 抽出以下共享模块：

- `useRecorderSession`
- `useRecorderScreencast`
- `useRecorderTabs`
- `useRecorderSteps`
- `useRecorderAssistant`
- `useRecorderValidation`
- `useRecorderTesting`
- `RecorderWorkbenchShell.vue`
- `RecorderSidebar.vue`
- `RecorderCanvasStage.vue`

### 页面与面板的关系

- `/rpa/recorder` 使用完整页面壳 + 共享内核
- `/rpa/test` 使用测试页面壳 + 共享内核
- 聊天右侧录制台使用右侧面板壳 + 同一共享内核

也就是：

- 页面不同
- 核心同一份

### 聊天右侧录制台要求

右侧录制台不是“预览面板”，而是嵌入式完整录制台。

必须至少具备以下能力：

- 步骤列表
- 录制助手
- 地址栏
- tab 管理
- 大尺寸画布
- 状态提示
- 风险确认
- 本段结束
- 测试入口
- 修复入口

### 布局要求

- 左侧主对话：`35% ~ 45%`
- 右侧录制台：`55% ~ 65%`
- 最小宽度不低于 `880px`
- 支持拖拽调宽

录制中自动展开，段落结束自动收起。

## 六、后端架构

### 保留

- `backend/rpa/*`
- 现有 Playwright 录制与测试逻辑
- 现有 skill/tool 保存接口

### 新增

```text
backend/recording/
├── models.py
├── orchestrator.py
├── artifact_registry.py
├── lifecycle.py
├── publishing.py
├── testing.py
├── step_repair_service.py
└── adapters/
    ├── rpa_adapter.py
    └── mcp_adapter.py
```

### 职责

#### orchestrator

- 创建/恢复 run
- 推进 segment
- 产物注册
- 生命周期切换

#### testing

- 从 run / segment 构建测试输入
- 触发共享测试流程
- 汇总失败点与修复状态

#### publishing

- 根据 `publish_target` 生成 staging 产物
- 触发保存提示
- 调用现有保存出口

#### step_repair_service

- 候选定位器切换
- 重新校验
- 单步重放

## 七、API 设计

录制 API 仍然保留在 `sessions` 下，但只负责“状态操作”，不再负责“意图识别”。

### 核心接口

- `POST /api/v1/sessions/{session_id}/recordings`
  - 创建 run
- `GET /api/v1/sessions/{session_id}/recordings/{run_id}`
  - 获取 run
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/segments`
  - 创建 segment
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/segments/{segment_id}/complete`
  - 完成本段
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/test`
  - 进入测试验证
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/repair`
  - 执行修复动作
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/publish`
  - 生成 staging 并触发发布
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/resume`
  - 恢复 run

### 删除项

删除或停用：

- `sessions.py` 中对录制意图的聊天短路逻辑

## 八、发布机制

### Skill 发布

当目标是 skill：

1. 录制 run 汇总为 skill 工作流描述与所需文件
2. 生成到 session workspace/staging
3. 触发 `propose_skill_save`
4. 前端显示保存确认
5. 调用 `save_skill_from_session`

### Tool / MCP 发布

当目标是 tool：

1. 录制 run 汇总为工具封装或 MCP wrapper 描述
2. 生成到 session workspace/staging
3. 触发 `propose_tool_save`
4. 前端显示保存确认
5. 调用 `save_tool_from_session`

### 与测试的关系

发布前必须至少满足以下条件之一：

- 测试通过
- 用户明确接受未完全验证的发布

默认要求通过测试后再发布。

## 九、迁移策略

### Step 1

先把 spec、plan 和最终边界确定，不再继续扩展当前旁路方案。

### Step 2

拆 `RecorderPage` / `TestPage` 共享内核，但暂不改变页面行为。

### Step 3

让聊天右侧录制台切到共享内核，删除当前简化 `RecordingWorkbench` 实现。

### Step 4

删除 `sessions.py` 中录制短路，把入口完全切到 `recording-creator`。

### Step 5

接通测试验证与发布闭环。

## 十、验收标准

### 入口

- 输入“我要录制个业务流程技能”时，不依赖 `sessions.py` 旁路，也能进入录制流程。
- 输入“录制一个 MCP 工具”时，能进入 MCP 录制路径。

### 录制体验

- 聊天右侧录制台和 `/rpa/recorder` 共享同一套核心能力。
- 右侧录制台必须具备步骤区、录制助手、地址栏、tab、状态与确认流。
- 不再出现当前这种“只有大画布”的缩水版工作台。

### 多段处理

- 第一段下载文件后，第二段可以在主对话中显式引用该文件继续处理。

### 测试验证

- 录制结果可以进入测试验证。
- 测试失败时可以定位到失败步骤并切换候选定位器重试。

### 发布

- 测试通过后，可走现有 `propose_skill_save` / `propose_tool_save` 发布。
- 最终成功保存到技能库或工具库。

## 结论

最终实现不再接受以下过渡方案继续扩展：

- `sessions.py` 正则/关键词旁路
- 独立简化版 `RecordingWorkbench`
- 录制页和聊天页两套分叉逻辑

必须一次性收束为：

- 技能化入口
- 共享录制内核
- 完整生成/测试/修复/发布闭环

只有这样，这项能力才真正和 `skill-creator` / `tool-creator` 处于同一产品层级。
