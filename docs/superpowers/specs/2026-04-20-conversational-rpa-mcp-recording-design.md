# 会话内 RPA / MCP 对话式录制设计

## 目标

在不要求用户先进入技能库或工具库页面的前提下，让用户能够直接从主对话入口发起 RPA 技能录制和 MCP 工具录制，并在同一会话中按段落继续后处理流程。

本设计需要同时满足以下目标：

- 用户可以在主聊天中用自然语言发起“录制一个流程”
- 录制过程复用现有 RPA 浏览器能力，而不是重新建设另一套浏览器运行时
- 录制结果可以以“段”为单位沉淀，支持下一段继续引用上一段产物
- 下一段不必总是重新打开浏览器；纯文件或工具处理应直接在主聊天中完成
- 用户至少可以对已录步骤进行轻量修复，尤其是切换步骤定位器
- 最终结果可以保存为 skill 或 tool，复用现有 `skill-creator` / `tool-creator` 收口

## 范围

纳入本期设计范围：

- 从主聊天识别并发起 RPA / MCP 录制
- 右侧固定大面板 `Recording Workbench` 作为录制操作区
- `Recording Run` / `Segment` / `Artifact` 的统一数据模型
- 多段处理的显式产物传递
- 录制完成后的段落摘要卡片
- 步骤级轻量修复：
  - 查看当前定位器
  - 查看候选定位器
  - 在候选定位器之间切换
  - 立即重校验
  - 单步重放
- 第一段录制后继续在主聊天中处理文件或调用工具
- 保存整条流程或部分流程为 skill / tool

不纳入本期设计范围：

- 完整可视化工作流画布
- 拖拽重排步骤
- 手工自由编辑 Playwright 代码
- 用户在页面上重新点选元素以生成全新定位器
- 多分支控制流编辑器
- 循环、条件、批量文件映射的通用 DSL
- 多个并行活动录制工作台

## 问题总结

当前产品已经具备三类有价值但彼此割裂的能力：

1. 主会话已经支持内置 `skill-creator` / `tool-creator`，说明“从聊天入口直接发起能力创建”是成立的产品路径。
2. RPA 录制页面已经具备可操作浏览器、聊天式助手、Agent 模式、确认机制、步骤列表和导出能力。
3. 主会话已有浏览器预览和本地模式 screencast 通道，但当前 `BrowserToolView` / `SandboxPreview` 主要用于结果预览，不适合精确录制。

当前缺失的是一层统一的“会话内录制编排”能力，导致：

- 用户要先跳转到技能库或工具库页面
- RPA / MCP 录制不能自然接入主聊天工作流
- “第一段下载文件，第二段继续处理文件”只能依赖模型记忆，而不是显式产物绑定
- 浏览器预览尺寸过小，不适合真实录制
- 已录步骤即使存在定位器候选，也没有在主会话里以轻量方式修复

## 推荐方案

采用“主聊天发起 + 右侧固定大面板录制工作台 + 左侧会话编排”的架构，而不是纯聊天内微型预览，也不是跳转到独立子页面。

该方案的核心原则如下：

- 主聊天负责理解意图、编排段落、串联产物
- 右侧 `Recording Workbench` 负责真实可操作录制
- RPA 与 MCP 录制统一抽象为 `Action Recording`
- 每段结束后都必须产出结构化 `Artifact`
- 下一段只能显式引用已注册的 `Artifact`，不能依赖模糊聊天记忆
- 非交互型后处理默认留在主聊天中执行
- 只有需要人工交互录制时，才自动再次展开 `Recording Workbench`

## 用户体验设计

### 入口策略

系统仅对“明确录制意图”自动触发录制，例如：

- “帮我录一个下载流程”
- “录制这个 MCP 操作”
- “把这个网页上的步骤录成技能”

对普通浏览或一次性工具执行请求，不自动进入录制态。

### 主界面布局

主界面采用左右分栏：

- 左侧 `35% ~ 45%`
  - 主聊天消息流
  - `Segment Summary Card`
  - 当前 `Recording Run` 状态
  - 当前可用 `Artifact` 列表
  - “继续下一段” / “保存为 skill/tool” 入口
- 右侧 `55% ~ 65%`
  - 固定大面板 `Recording Workbench`
  - 最小宽度不低于 `880px`
  - 支持拖拽调宽

### Recording Workbench

`Recording Workbench` 是录制时的唯一真实操作面板，不使用 `BrowserToolView` 作为录制主界面。

其内部结构分三层：

- 顶部控制栏
  - 地址栏
  - Tab 切换
  - 录制状态标识
  - 暂停录制
  - 结束本段
  - 收起工作台
- 中间浏览器画布
  - 复用现有 `RecorderPage.vue` 的 canvas、输入转发、坐标映射、screencast 逻辑
  - 这是录制主操作区
- 底部辅助区
  - 最近步骤
  - 定位提示
  - 风险确认
  - 下载结果提示

### 自动展开与自动收起

当用户在主聊天里发起第 1 段录制时：

- 创建 `Recording Run`
- 右侧 `Recording Workbench` 自动展开
- 直接切换到 `recording` 态
- 左侧聊天插入系统卡片：“已开始第 1 段录制”

当一段录制结束时：

- 右侧 `Recording Workbench` 自动收起
- 左侧插入该段的 `Segment Summary Card`
- 用户再次说“继续下一段”时，再由系统判断是否需要重新展开工作台

### 非交互段策略

如果下一段只是文件处理、文本提取、结构化转换或 MCP 工具处理，不重新展开右侧工作台，而是直接在主聊天中执行。

只有在下一段仍需要人工录制或浏览器/MCP 实时交互时，才再次自动展开 `Recording Workbench`。

## 统一录制抽象

RPA 录制与 MCP 录制统一抽象为 `Action Recording`，共享同一条编排链路。

### Recording Run

`Recording Run` 表示一次从主聊天发起的录制任务，归属于一个主会话。

建议字段：

- `id`
- `session_id`
- `user_id`
- `type`
  - `rpa`
  - `mcp`
  - `mixed`
- `status`
- `active_segment_id`
- `segments`
- `artifact_index`
- `save_intent`
- `created_at`
- `updated_at`

### Segment

`Segment` 表示一段录制或处理。

建议字段：

- `id`
- `run_id`
- `kind`
  - `rpa`
  - `mcp`
  - `chat_process`
  - `mixed`
- `intent`
- `status`
- `steps`
- `imports`
- `exports`
- `artifacts`
- `started_at`
- `ended_at`

### Step

统一步骤分为两类：

- `ui_step`
  - 导航
  - 点击
  - 输入
  - 下载
  - 文本提取
- `tool_step`
  - MCP tool 调用
  - 本地工具调用
  - 文档转换
  - 结构化处理

推荐保留“双层表示”：

- 底层保存原始执行信息
  - RPA 原始事件
  - 定位器候选
  - frame path
  - tool args / result
- 上层保存语义步骤
  - “打开 PubMed”
  - “下载第一篇结果的 PDF”
  - “把 PDF 转成 Markdown”

后续多段编排、摘要展示、保存为 skill/tool，依赖语义步骤而不是原始事件流。

### Artifact

`Artifact` 是多段编排的核心，必须显式注册。

建议字段：

- `id`
- `run_id`
- `segment_id`
- `name`
- `type`
  - `file`
  - `text`
  - `json`
  - `table`
- `path`
- `value`
- `mime_type`
- `labels`
- `producer_step_id`
- `created_at`

典型例子：

- `downloaded_pdf`
- `paper_markdown`
- `latest_issue_title`
- `extracted_financial_fields`

## 多段编排设计

### 显式输入输出绑定

每个 `Segment` 都必须定义：

- `intent`
- `imports`
- `exports`
- `artifacts`

第一段示例：

```json
{
  "segment_id": "seg_1",
  "intent": "下载目标网页中的 PDF 文件",
  "exports": {
    "downloaded_pdf_path": "{{artifacts.downloaded_pdf.path}}"
  }
}
```

第二段示例：

```json
{
  "segment_id": "seg_2",
  "intent": "把刚下载的 PDF 转成 Markdown 并提取摘要",
  "imports": {
    "input_pdf": "{{seg_1.exports.downloaded_pdf_path}}"
  }
}
```

### 编排规则

第一期编排规则固定如下：

1. 每段必须有明确输入和输出。
2. 每段结束后必须执行 `artifact extraction`。
3. 下一段只能引用已注册 artifact。
4. 如果引用不唯一，系统必须回问。
5. 如果 artifact 不存在、已失效或文件丢失，该段进入 `blocked`。

### Artifact Registry

需要新增统一的 `Artifact Registry`，挂在 `Recording Run` 上。

它至少支持：

- 注册 artifact
- 查询最近 artifact
- 按类型过滤
- 按标签过滤
- 生成稳定变量名
- 检查文件是否仍存在
- 为下一段生成 `imports`

第一期只支持三种跨段引用：

- 上一段下载的单个文件
- 上一段提取的单个文本值
- 上一段输出的单个结构化 JSON

不支持：

- 多文件批量映射
- 条件分支绑定
- 通用表达式编辑

## 轻量步骤编辑设计

第一期必须支持轻量步骤编辑，但只做“定位器修复”，不做完整工作流编辑器。

### 支持的能力

- 查看当前定位器
- 查看 `frame_path`
- 查看校验状态
- 查看候选定位器列表
- 在候选定位器之间切换
- 切换后立即重校验
- 单步重放
- 重录本步
- 重录本段

### 不支持的能力

- 手工自由输入定位器
- 直接编辑 Playwright 代码
- 拖拽重排步骤
- 任意改步骤语义
- 拆分 / 合并步骤

### 校验状态

定位器校验状态沿用现有语义：

- `ok`
- `ambiguous`
- `fallback`
- `warning`
- `broken`

### 修复失败时的策略

如果一个步骤不存在可用候选定位器，或所有候选校验失败：

- 标记该步骤为“无法自动修复”
- 第一期开启两种恢复路径：
  - `重录本步`
  - `重录本段`

不做页面重新选点生成新定位器。

## 状态机设计

### Recording Run 状态

`Recording Run` 建议状态如下：

- `draft`
- `recording`
- `waiting_user`
- `processing_artifacts`
- `ready_for_next_segment`
- `blocked`
- `failed`
- `completed`
- `saved`

### Segment 状态

`Segment` 建议状态如下：

- `draft`
- `recording`
- `running`
- `validating`
- `ready`
- `blocked`
- `failed`
- `completed`
- `aborted`

设计要求：

- 第一段成功、第二段失败时，不能把整条 `Recording Run` 一起打废
- `Segment` 必须可以单独重录或修复

## 错误处理与恢复

### 错误分类

错误分为四类：

1. 环境类
   - screencast 断开
   - 浏览器进程异常
   - MCP server 不可用
2. 定位类
   - locator 失效
   - frame 不匹配
   - 页面结构变化
3. 产物类
   - 文件未实际生成
   - 路径失效
   - artifact 注册不完整
4. 语义类
   - “刚才那个文件”指代不清
   - 引用不唯一

### 恢复策略

- 环境类：优先 `resume`
- 定位类：进入 `needs_repair` 语义路径，允许切换候选定位器、单步重放、重录本步或本段
- 产物类：阻断下一段执行，不允许模型自行猜测
- 语义类：必须回问

### 事件日志与检查点

需要采用“事件日志 + 检查点”模式，而不是只依赖聊天消息。

建议新增：

- `recording_runs`
- `recording_events`

检查点至少包含：

- 当前 `Run` 状态
- 当前 `Segment`
- 已注册 `Artifact`
- 待确认风险动作
- 当前是否可继续下一段

## 后端架构

不建议把新能力继续堆进 `backend/route/rpa.py` 的页面录制语义中，而是新增独立的 `recording` 编排域。

### 推荐目录

```text
backend/recording/
├── models.py
├── orchestrator.py
├── artifact_registry.py
├── step_repair_service.py
└── adapters/
    ├── rpa_adapter.py
    └── mcp_adapter.py
```

### 职责拆分

#### recording_orchestrator

负责：

- 创建 `Recording Run`
- 开启 / 结束 `Segment`
- 判断下一段是否需要展开工作台
- 驱动 `artifact extraction`
- 将段落摘要回灌到主会话

#### rpa_adapter

复用现有：

- `backend/rpa/assistant.py`
- `backend/rpa/generator.py`
- `backend/route/rpa.py`
- `frontend/src/pages/rpa/RecorderPage.vue`

负责产出：

- `ui_step`
- 下载类 artifact
- 定位器候选与验证结果

#### mcp_adapter

负责：

- 将 MCP 调用记录成 `tool_step`
- 保存 `args` / `result`
- 将输出文件或结构化结果注册为 artifact

#### artifact_registry

负责：

- artifact 注册
- artifact 查询
- artifact 绑定
- 文件存在性校验

#### step_repair_service

负责：

- 提供候选定位器列表
- 切换候选定位器
- 重新校验
- 单步重放

## API 设计

建议挂在主会话下，而不是另开孤立的录制空间。

### 建议接口

- `POST /api/v1/sessions/{session_id}/recordings`
  - 创建 `Recording Run`
- `GET /api/v1/sessions/{session_id}/recordings/{run_id}`
  - 获取 run 当前状态
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/segments`
  - 创建下一段
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/segments/{segment_id}/complete`
  - 完成当前段并提取 artifact
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/resume`
  - 恢复 run
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/save`
  - 保存为 skill / tool
- `GET /api/v1/sessions/{session_id}/recordings/{run_id}/segments/{segment_id}/steps/{step_id}/locators`
  - 获取定位器候选与当前校验状态
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/segments/{segment_id}/steps/{step_id}/locators/select`
  - 切换候选定位器
- `POST /api/v1/sessions/{session_id}/recordings/{run_id}/segments/{segment_id}/steps/{step_id}/replay`
  - 单步重放

## 前端设计

### 新组件

建议新增：

- `RecordingWorkbench.vue`
- `RecordingSegmentCard.vue`
- `RecordingArtifactList.vue`
- `RecordingStepRepairPanel.vue`

### 复用与抽取

不建议直接把 `BrowserToolView.vue` 改造成录制界面。

建议：

- `BrowserToolView.vue`
  - 继续承担结果预览
- `RecorderPage.vue`
  - 抽出可复用的 workbench 逻辑：
    - screencast 连接
    - 输入事件转发
    - 地址栏
    - tab 管理
    - confirm 流

### 左侧卡片设计

每个 `Segment Summary Card` 至少展示：

- 段目标
- 段类型：`RPA` / `MCP` / `Mixed` / `Chat Process`
- 输入引用
- 输出 artifact
- 当前状态
- 操作：
  - 查看步骤
  - 修复定位器
  - 重录本段
  - 继续下一段
  - 保存为 skill/tool

## 与现有能力的关系

### 保留并复用的能力

- 主会话 `skill-creator` / `tool-creator` 入口策略
- `backend/rpa/assistant.py` 的聊天式执行与确认机制
- `backend/rpa/generator.py` 的 Playwright 代码生成
- `backend/route/sessions.py` 的主会话 browser screencast
- `frontend/src/pages/rpa/RecorderPage.vue` 的录制交互能力

### 明确替换的职责

- `BrowserToolView` 不再承担录制入口
- 主会话中的小预览仅用于查看结果，不用于精确录制

## 第一阶段 MVP

第一期只追求一条闭环：

`主聊天发起 -> 右侧工作台录第 1 段 -> 自动收起 -> 主聊天继续第 2 段 -> 修复定位器 -> 保存为 skill/tool`

### 第一阶段必须完成

1. 主聊天中识别录制意图并创建 `Recording Run`
2. 右侧 `Recording Workbench` 自动展开并进入录制态
3. 第 1 段完成后自动收起，并生成 `Segment Summary Card`
4. `Artifact Registry` 能注册第一段产物
5. “继续处理刚才下载的文件”能显式绑定 artifact
6. 非交互处理留在主聊天中执行
7. 步骤支持候选定位器切换与立即校验
8. 支持单步重放
9. 所有候选失败时，只允许重录本步或本段
10. 最终能保存为 skill 或 tool

### 第一阶段不做

- 条件分支
- 循环
- 批量多文件映射
- 页面重新选点换定位器
- 拖拽编辑整段流程

## 验收标准

第一期以以下四条主链路为验收标准：

1. 用户在主聊天中说“帮我录一个下载流程”后，右侧工作台自动展开并进入录制态。
2. 第一段结束后，右侧自动收起，左侧生成 `Segment Summary Card`，并展示可用 artifact。
3. 用户继续说“处理刚才下载的文件”时，系统能显式绑定上一段 artifact，并在聊天内完成非交互处理。
4. 某一步定位器失效时，用户可以在候选定位器之间切换，系统立即返回校验结果，并支持单步重放；若全部失败，只允许重录本步或本段。

## 实施顺序

推荐实施顺序如下：

1. 建立 `Recording Run` / `Segment` / `Artifact Registry` 数据模型与编排接口
2. 抽取 `RecorderPage.vue` 的录制交互能力，落成 `RecordingWorkbench`
3. 打通主聊天入口与段落卡片回灌
4. 接入 `step_repair_service`
5. 最后接入 `skill-creator` / `tool-creator` 保存闭环

## 结论

本设计不追求把所有录制行为都塞进聊天消息流，而是将“聊天入口的自然语言编排”和“右侧固定大面板的真实录制操作”明确分层。

第一期的核心不是做一个庞大的流程编辑器，而是先把以下三件事做稳：

- 从主聊天自然发起录制
- 用 `Artifact` 让多段流程稳定衔接
- 用轻量步骤修复解决定位器失效的高频问题

这条路径最大程度复用现有 RPA、主会话预览和 skill/tool 保存能力，同时避免把 `BrowserToolView` 这种结果预览组件强行升级成录制主界面。
