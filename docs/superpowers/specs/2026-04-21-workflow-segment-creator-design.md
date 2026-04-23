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

前端与 Agent 应在保存 segment 前显式给出 `inputs` / `outputs` / `artifacts` 契约。
当前实现的原则是：

- 后端只做结构校验与最小归一化，不根据文件类型、参数名、示例模板或特定业务场景改写 segment 契约。
- 上下游依赖应通过结构化 `source_type` / `source_ref` 明确绑定，而不是依赖自然语言描述或后端猜测。
- 规划下一段时，Agent 应先读取当前 run 的上下文，再决定这段需要哪些输入、输出与参数。

### NextSegmentContext

`inspect_recording_runs` 会返回 `next_segment_context`，供 `recording-creator` 规划下一段 workflow 片段。该结构至少包含：

- `run_goal`: 当前 run 的发布目标或运行目标
- `latest_segment`: 最近一个已存在片段的摘要
- `available_sources`: 当前可复用的 `segment_outputs`、`artifacts` 与推荐来源集合
- `runtime_path_policy`: 运行期路径策略说明
- `planning_hints`: Agent 在生成下一段前必须遵循的规划提示

其中与文件处理相关的关键约束是：

- 如果前一段产生了下载文件或其他文件产物，后续脚本段必须通过 `artifact:<id>` 绑定该产物，而不是复用录制时看到的临时本地路径。
- 完整测试与最终执行阶段会在运行期 `_downloads_dir` 下解析这些 artifact 对应的真实文件路径。
- runner 会把 `downloads_dir`、`workspace_dir`、`skill_dir` 等运行期路径显式注入 `context.runtime`，供脚本段在测试与正式执行时统一推导输入输出路径。
- 因此，文件型输入的设计重点是“声明输入并绑定 artifact”，而不是让用户复制路径或让后端猜路径；输出路径则优先基于 `input_path` 或 `context.runtime["workspace_dir"]` 推导。

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

脚本段的推荐执行协议固定为：

```python
def run(context, **kwargs) -> dict:
    ...
    return {...}
```

其中：

- `kwargs` 只包含这段脚本真实需要的输入。
- `context.runtime` 用来读取运行期路径与执行上下文。
- runner 会在 `context.runtime` 中提供 `downloads_dir`、`workspace_dir`、`skill_dir`、`workflow_path`、`segments_dir`。
- 不再把 `main(...)` 或全局 `params/inputs/outputs` 作为首选生成模式。

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
- 先通过 `inspect_recording_runs` 读取 `next_segment_context`
- 根据用户当前意图 + 已有 outputs/artifacts 规划脚本目标、输入、输出与参数
- 调用 `add_script_recording_segment`
- 如果依赖上一段输出或 artifact，再调用 `bind_recording_segment_io`

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

## 录制测试修复闭环

当前实现已经补齐“生成-测试-修复-重测”的录制期闭环，行为规则如下：

1. `add_script_recording_segment` 只负责把脚本片段写入 recording run，并将 `testing_status` 初始化为 `idle`。
2. `begin_recording_test` 不再只看脚本退出码或 `SKILL_SUCCESS`，还会校验 `workflow.json` 中声明的 segment outputs / artifacts 是否在实际执行结果中兑现。
3. 只要声明输出缺失，即使进程退出成功，也必须判定为测试失败，并把 run 状态置为 `needs_repair`，不能直接进入 `ready_to_publish`。
4. 每次完整测试都会生成 repair workspace，上下文通过 `repair_context` 返回，至少包含：
   - `skill_dir`
   - `workflow_path`
   - `context_path`
   - `missing_outputs`
   - `missing_artifacts`
5. `context_path` 对应的 JSON 文件会把本次测试的执行结果、契约缺失信息、当前 run/segments 摘要一并落盘，供 `recording-creator` 后续定位与修复。
6. Agent 修复时必须优先编辑 repair workspace 内的 workflow/script 文件，而不是等保存成 skill 后再修。
7. 修复完成后调用 `apply_recording_test_repairs`，把 repair workspace 里的脚本片段、输入输出契约和入口配置回写到 recording run。
8. 回写后 segment 会重置为 `testing_status=idle`，然后再次执行 `begin_recording_test`。只有整条 workflow 再次通过，才允许进入发布阶段。

这套机制的目的不是在后端写死某个 Excel、Markdown 或特定文件类型的修复逻辑，而是让录制阶段拥有与普通技能执行阶段一致的可调试、可修复、可重跑能力。
