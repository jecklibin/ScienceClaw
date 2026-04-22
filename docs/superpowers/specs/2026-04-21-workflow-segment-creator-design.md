# 对话式 Workflow Segment Creator 设计方案

## 文档状态

- 状态：已按当前代码实现修订
- 当前技能入口：`recording-creator`
- 当前主模型：`RecordingRun` 经 `recording_adapter` 转换为 `WorkflowRun`
- 当前发布事实来源：`workflow.json`

## 背景

在对话式录制能力落地后，系统已经不再是“录一段浏览器脚本然后导出”的单一模型，而是一个多段 workflow 构建器：

- 第一段可以是 RPA 浏览器录制
- 第二段可以是脚本处理
- 第三段可以是 MCP 调用
- 发布结果必须表达 segment 顺序、输入输出依赖和最终 runner

因此 segment 不能再被理解为“仅仅是录制片段”，而应被理解为“workflow 中的一段可配置、可测试、可发布执行单元”。

## 最终结论

### 1. 不新增 `workflow-creator` 技能

当前实现没有再引入新的 `workflow-creator` 内置技能，而是把该职责收敛到 `recording-creator`：

- 用户在主对话中表达录制/续录/绑定/测试/发布需求
- agent 命中 `recording-creator`
- `recording-creator` 决定调用哪些 recording lifecycle tools

这样可以避免把“录制型 workflow”再拆成第二个近义技能入口。

### 2. segment 是 workflow 片段

当前 workflow 域的标准 segment 类型是：

- `rpa`
- `script`
- `mcp`
- `llm`
- `mixed`

录制域目前也以 `script` 作为正式的非交互片段类型。

### 3. 发布阶段以 workflow 为中心

最终保存产物不是单个扁平 Playwright 脚本，而是：

- `workflow.json`
- `skill.py`
- `params.json`
- `SKILL.md`
- `segments/` 下的每段脚本或元数据

`workflow.json` 是事实来源，`skill.py` 是由模板生成的 runner。

`params.json` 与普通技能录制保持同一语义：保存参数类型、参数说明、默认参数、敏感参数标记和已绑定的 `credential_id`，供技能页面配置、workflow runner 默认值加载和应用内执行链路注入。当前设计不生成 `params.schema.json` 或 `credentials.example.json`，避免参数配置出现多个事实来源。

## 数据模型

### RecordingRun -> WorkflowRun

录制阶段维护 `RecordingRun`，发布阶段通过 `backend/workflow/recording_adapter.py` 转为 `WorkflowRun`。

转换后的目标模型：

```ts
type WorkflowRun = {
  id: string
  sessionId: string
  intent: string
  status: 'draft' | 'ready_to_publish' | 'published'
  segments: WorkflowSegment[]
  artifacts: ArtifactRef[]
  publishDraft?: SkillPublishDraft
}
```

### WorkflowSegment

```ts
type WorkflowSegment = {
  id: string
  runId: string
  kind: 'rpa' | 'script' | 'mcp' | 'llm' | 'mixed'
  order: number
  title: string
  purpose: string
  status: 'configured' | 'tested' | 'failed'
  inputs: SegmentInput[]
  outputs: SegmentOutput[]
  artifacts: ArtifactRef[]
  config: Record<string, unknown>
}
```

### SegmentInput / SegmentOutput

每个 segment 都应沉淀结构化输入输出：

- `inputs`: 这段需要哪些值，来源是用户、workflow 参数、上一段输出还是 artifact
- `outputs`: 这段会导出哪些值，供后续段或最终技能输出使用

如果前端未显式传入，当前实现会尽量从：

- `params`
- `extract_text.result_key`
- `artifacts`

中做合理推断，但推断只是兜底，不是最终目标。

## RPA Segment

RPA 段保留：

- 浏览器步骤
- locator 候选
- 参数占位
- 测试与修复信息

发布后写入：

- `segments/<segment_id>_rpa.py`
- workflow 中对应 segment 的 `config.steps`

## Script Segment

脚本段用于承载非交互后处理，例如：

- 文件转换
- 文本抽取
- JSON 清洗
- CSV/Excel/PDF 二次处理

脚本段发布后写入：

- `segments/<custom_entry>.py`
- workflow 中对应 segment 的 `config.language`
- `config.entry`
- `config.source`

## MCP / LLM Segment

这两类 segment 目前模型空间已经预留，但当前实现主要先打通：

- `rpa`
- `script`
- 多段输入输出绑定
- 整体测试
- 发布

`mcp` / `llm` 在 runner 中仍以 metadata 承载为主，后续可以继续扩展为真实执行器。

## 主交互流程

### 1. 新建录制

- 用户在主对话提出录制需求
- `recording-creator` 调用 `inspect_recording_runs`
- 新开 run 时调用 `start_recording_run`
- 前端打开录制弹窗

### 2. 追加脚本段

- 用户在录完一段后说“接下来把下载下来的文件转换一下”
- `recording-creator` 应判断这是 `script segment`
- 生成脚本内容和输入输出定义
- 调用 `add_script_recording_segment`
- 如果依赖上一段输出，再调用 `bind_recording_segment_io`

### 3. 测试

- 用户要求“开始整体测试”
- 调用 `begin_recording_test`
- 单段打开 `/rpa/test`
- 多段打开 `/rpa/workflow-test`

### 4. 发布

- 调用 `prepare_recording_publish`
- 生成发布草稿
- 弹出发布确认 UI
- 用户确认后保存

## 设计约束

### 需要坚持的边界

- 技能层决定是否进入录制，不在后端工具层重复做意图识别。
- 多段依赖必须结构化绑定，不允许只写自然语言说明。
- `workflow.json` 是事实来源，不能只靠生成的 `skill.py` 反推结构。
- 发布名称、描述、输入输出在发布阶段确认，不在 segment 配置阶段混入。

## 已不再采用的旧设计

以下描述不再代表当前方案：

- “新增 `workflow-creator` 作为独立技能入口”
- “segment 配置阶段配置最终技能名称和技能描述”
- “录制完成后直接根据 run id 或第一段标题保存技能”
- “最终技能只需要一个极简 `SKILL.md` + 一个扁平 `skill.py`”

## 后续建议

如果继续演进，优先顺序应是：

1. 把 `mcp` / `llm` segment 也补齐真实执行器，而不只是元数据占位
2. 在发布弹窗中支持更明确地编辑 workflow 级输入输出
3. 让 segment 卡片和配置页共享同一套输入输出编辑模型
