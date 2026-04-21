# 对话式 Workflow Segment Creator 设计方案

## 背景

当前对话式录制能力已经可以从主对话启动 RPA 录制，并以弹窗方式完成录制、配置、测试和返回对话。但在多段场景下暴露出结构性问题：

- segment 被默认理解为 RPA 录制片段，无法自然表达“下载文件后继续用脚本处理文件”的场景。
- 片段配置阶段仍在配置“技能名称和描述”，实际应该配置“这个片段是做什么的”。
- 保存阶段没有让用户确认最终技能名称、描述、参数和片段顺序。
- 发布产物只生成了极简 `SKILL.md` 和单个 `skill.py`，缺少合格的技能说明、参数 schema、认证说明和 workflow 元数据。
- 多段发布时脚本内容不完整，容易只保留某一段，无法表达 segment 之间的输入输出依赖。

这些问题不能只通过修改文案或修补发布模板解决。根因是当前模型把“录制片段”“工作流草稿”“最终技能发布配置”混为一体，并且把 RPA 录制当作整个系统中心。

本设计将能力升级为对话式 Workflow Segment Creator。RPA 录制只是 segment 类型之一，系统还需要支持脚本处理、MCP 工具调用、LLM 结构化处理，以及未来的混合片段。

## 目标

本次重构的目标如下：

- 在主对话中通过内置技能接管工作流构建，不再依赖 `sessions.py` 的正则或关键词旁路逻辑。
- 支持多类型 segment，包括 RPA、script、MCP、LLM 和 mixed。
- 支持多段 workflow，后续片段可以显式消费前序片段的文件、数据和上下文。
- 片段配置只配置片段本身，最终技能名称和技能描述只在发布阶段配置。
- 发布前必须生成可编辑的 Skill Publish Draft，由用户确认最终技能名称、描述、输入参数、认证要求、输出结果和片段顺序。
- 最终保存的技能必须包含合格的 `SKILL.md`、可执行 runner、workflow 元数据、参数 schema 和认证示例。
- 复用现有 RPA recorder、configure、test 能力，但不要把所有 segment 都塞进 RPA 模型。

## 非目标

本阶段不做以下内容：

- 可视化拖拽式 workflow 编排画布。
- 完整通用 DAG 执行引擎。
- 任意复杂条件分支和循环 DSL。
- 在线多人协同编辑 workflow。
- 对所有已有 RPA 技能做自动迁移。
- 替代现有 `skill-creator` 和 `tool-creator`。

本阶段只实现顺序多段 workflow，并为后续 DAG、条件分支和循环留出模型空间。

## 核心原则

### 1. segment 是工作流片段，不是录制片段

segment 的含义应调整为“workflow 中的一段可测试、可配置、可复用执行单元”。

典型类型包括：

- `rpa`: 浏览器操作录制，例如登录网站、下载文件、进入某个页面。
- `script`: 数据处理脚本，例如转换 Excel、解析 PDF、清洗 CSV、生成报告。
- `mcp`: MCP 工具调用，例如调用外部服务、数据库查询、工具库工具。
- `llm`: LLM 结构化处理，例如提取字段、分类、总结。
- `mixed`: 组合型片段，用于暂时承载无法拆得更细的场景。

### 2. workflow 是多段组合，不是单段脚本

最终技能不应该只是一个扁平的 Playwright 脚本。它应该是一个 workflow runner，按顺序执行多个 segment，并把每段输出写入上下文，供后续片段消费。

### 3. 发布配置与片段配置分离

片段配置阶段只回答：

- 这段做什么？
- 这段需要哪些输入？
- 这段会产出什么？
- 这段如何测试？
- 这段需要哪些认证？

发布配置阶段才回答：

- 最终技能叫什么？
- 用户什么时候应该使用这个技能？
- 技能整体需要哪些输入参数？
- 技能整体输出是什么？
- 哪些片段被纳入最终技能？
- 参数冲突如何合并？

### 4. workflow.json 是事实来源

`skill.py` 可以由生成器生成，但不应成为唯一事实来源。最终技能目录必须包含 `workflow.json`，用于保存片段类型、片段顺序、输入输出、脚本路径、RPA 步骤、MCP 调用和测试信息。

## 用户体验设计

### 入口

用户可以在主对话中表达：

- “我要录制个业务流程技能”
- “先帮我录一个下载文件的流程”
- “接下来把下载下来的文件转换成 CSV”
- “继续调用 MCP 工具上传转换后的文件”
- “把这个流程保存成技能”

内置 `workflow-creator` 技能负责接管这些请求。它不应该通过后端 session 路由做固定关键词匹配，而应该像 `skill-creator` 和 `tool-creator` 一样，由主 agent 选择并执行。

### RPA segment 流程

当用户新增 RPA 片段时：

1. 主对话创建或恢复 workflow run。
2. 打开大弹窗，进入 RPA 录制态。
3. 用户在弹窗中完成浏览器操作。
4. 用户点击完成后，仍留在弹窗内进入片段配置。
5. 配置项为片段名称、片段用途、输入参数、认证绑定、输出产物。
6. 用户在弹窗内测试该片段。
7. 测试通过或用户确认后，点击完成返回主对话。
8. 主对话底部显示一个默认折叠的 segment 卡片。
9. 对话提示用户可以继续新增片段或准备发布。

### script segment 流程

当用户说“接下来处理下载下来的文件”时：

1. `workflow-creator` 判断这是一个 script segment。
2. 系统读取 workflow run 中最近的文件 artifact。
3. 如果只有一个明确候选文件，自动作为输入。
4. 如果有多个候选文件，要求用户选择或说明。
5. agent 生成处理脚本，例如 Python 脚本。
6. 打开脚本片段配置弹窗，展示脚本、输入文件、输出文件和参数。
7. 用户可以编辑说明、参数和输出格式。
8. 系统运行脚本测试。
9. 测试通过后，保存为下一个 segment。
10. 返回主对话，显示 script segment 卡片。

### 发布流程

当用户选择“准备发布”或“保存为技能”时：

1. 后端生成 Skill Publish Draft。
2. 前端在聊天框上方或居中弹窗展示发布配置。
3. 用户填写或确认最终技能名称。
4. 用户填写或确认技能描述。
5. 用户确认使用示例、输入参数、认证要求、输出结果。
6. 用户确认 segment 顺序和纳入范围。
7. 用户点击保存。
8. 后端生成完整技能目录。
9. 主对话返回保存结果和技能入口。

发布前不能直接用 run id 或第一个 segment 名称保存技能。

## 数据模型

### WorkflowSegment

统一 segment 模型：

```ts
type WorkflowSegmentKind = 'rpa' | 'script' | 'mcp' | 'llm' | 'mixed'

type WorkflowSegment = {
  id: string
  runId: string
  kind: WorkflowSegmentKind
  order: number
  title: string
  purpose: string
  status: 'draft' | 'configured' | 'testing' | 'tested' | 'failed'
  inputs: SegmentInput[]
  outputs: SegmentOutput[]
  artifacts: ArtifactRef[]
  config: Record<string, unknown>
  testResult?: SegmentTestResult
  createdAt: string
  updatedAt: string
}
```

`config` 按类型分发：

- `rpa.config.steps`: RPA 录制步骤。
- `rpa.config.browser`: 浏览器上下文、起始 URL、locator 信息。
- `script.config.language`: 脚本语言。
- `script.config.entry`: 脚本路径。
- `script.config.requirements`: 脚本依赖。
- `mcp.config.server`: MCP server 标识。
- `mcp.config.tool`: MCP tool 名称。
- `llm.config.schema`: 结构化输出 schema。

### SegmentInput

```ts
type SegmentInput = {
  name: string
  type: 'string' | 'number' | 'boolean' | 'file' | 'json' | 'secret'
  required: boolean
  source: 'user' | 'workflow_param' | 'segment_output' | 'artifact' | 'credential'
  sourceRef?: string
  description: string
  default?: unknown
}
```

示例：

```json
{
  "name": "source_file",
  "type": "file",
  "required": true,
  "source": "segment_output",
  "sourceRef": "segment_1.outputs.downloaded_file",
  "description": "第一段 RPA 下载得到的文件"
}
```

### SegmentOutput

```ts
type SegmentOutput = {
  name: string
  type: 'string' | 'number' | 'boolean' | 'file' | 'json'
  description: string
  artifactRef?: string
}
```

### WorkflowRun

```ts
type WorkflowRun = {
  id: string
  sessionId: string
  intent: string
  status: 'draft' | 'recording' | 'configuring' | 'testing' | 'ready_to_publish' | 'published'
  segments: WorkflowSegment[]
  artifacts: ArtifactRef[]
  context: Record<string, unknown>
  publishDraft?: SkillPublishDraft
  createdAt: string
  updatedAt: string
}
```

### SkillPublishDraft

```ts
type SkillPublishDraft = {
  id: string
  runId: string
  publishTarget: 'skill' | 'tool' | 'mcp'
  skillName: string
  displayTitle: string
  description: string
  useCases: string[]
  triggerExamples: string[]
  inputs: PublishInput[]
  outputs: PublishOutput[]
  credentials: CredentialRequirement[]
  segments: PublishSegmentSummary[]
  warnings: PublishWarning[]
}
```

## API 设计

### 创建或恢复 workflow run

```http
POST /api/v1/sessions/{session_id}/workflow-runs
```

请求：

```json
{
  "intent": "录制一个下载并转换文件的业务流程技能"
}
```

响应：

```json
{
  "run_id": "run_123",
  "status": "draft"
}
```

### 完成 segment

```http
POST /api/v1/sessions/{session_id}/workflow-runs/{run_id}/segments
```

请求：

```json
{
  "kind": "script",
  "title": "转换下载文件",
  "purpose": "将上一步下载的 Excel 文件转换成 CSV",
  "inputs": [],
  "outputs": [],
  "artifacts": [],
  "config": {
    "language": "python",
    "entry": "segments/segment_2_transform.py"
  },
  "test_result": {
    "status": "passed"
  }
}
```

### 生成发布草稿

```http
POST /api/v1/sessions/{session_id}/workflow-runs/{run_id}/publish-draft
```

请求：

```json
{
  "publish_target": "skill"
}
```

响应返回 `SkillPublishDraft`。后端会根据 segments 自动生成推荐名称、描述、参数、认证和警告，但前端必须允许用户编辑。

### 保存发布草稿

```http
POST /api/v1/sessions/{session_id}/workflow-runs/{run_id}/publish
```

请求：

```json
{
  "draft": {
    "publishTarget": "skill",
    "skillName": "download_and_convert_report",
    "displayTitle": "下载并转换业务报表",
    "description": "自动打开业务系统下载报表，并将下载文件转换为标准 CSV。",
    "triggerExamples": [
      "帮我下载并转换业务报表",
      "执行报表下载转换流程"
    ],
    "inputs": [],
    "outputs": [],
    "credentials": [],
    "segments": []
  }
}
```

## 技能产物结构

最终保存到技能库的目录结构：

```text
download_and_convert_report/
├── SKILL.md
├── skill.py
├── workflow.json
├── params.schema.json
├── credentials.example.json
├── segments/
│   ├── segment_1_rpa.json
│   └── segment_2_transform.py
└── README.md
```

### SKILL.md

`SKILL.md` 必须是 agent 可理解的技能说明，不允许只写生成占位文案。

示例：

```markdown
---
name: download_and_convert_report
description: 自动打开业务系统下载报表，并将下载文件转换为标准 CSV。
---

# 下载并转换业务报表

## 何时使用

当用户需要从业务系统下载报表，并将下载文件转换成标准 CSV 时使用本技能。

## 输入参数

- `report_date`: 报表日期。

## 认证要求

- 需要业务系统登录态。

## 工作流片段

1. 打开业务系统并下载报表文件。
2. 将下载得到的 Excel 文件转换成 CSV。

## 输出

- `converted_csv`: 转换后的 CSV 文件路径。

## 失败处理

如果下载文件不存在，技能应返回明确错误，并提示用户检查登录态或下载页面是否变化。
```

### workflow.json

`workflow.json` 保存完整结构化 workflow：

```json
{
  "schema_version": "1.0",
  "name": "download_and_convert_report",
  "title": "下载并转换业务报表",
  "description": "自动打开业务系统下载报表，并将下载文件转换为标准 CSV。",
  "segments": [
    {
      "id": "segment_1",
      "kind": "rpa",
      "title": "下载业务报表",
      "purpose": "打开业务系统并下载报表文件",
      "config_path": "segments/segment_1_rpa.json",
      "outputs": [
        {
          "name": "downloaded_file",
          "type": "file"
        }
      ]
    },
    {
      "id": "segment_2",
      "kind": "script",
      "title": "转换报表文件",
      "purpose": "将下载文件转换为标准 CSV",
      "entry": "segments/segment_2_transform.py",
      "inputs": [
        {
          "name": "source_file",
          "sourceRef": "segment_1.outputs.downloaded_file"
        }
      ],
      "outputs": [
        {
          "name": "converted_csv",
          "type": "file"
        }
      ]
    }
  ]
}
```

### skill.py

`skill.py` 是通用 workflow runner，职责是读取 `workflow.json`，按顺序调度 segment。

关键行为：

- 加载 `params.schema.json`。
- 合并用户输入参数和默认值。
- 创建 workflow context。
- 按顺序执行每个 segment。
- 将每段输出写入 context。
- 支持 segment 之间通过 `sourceRef` 引用输出。
- 返回最终 outputs。

伪代码：

```python
def run(**kwargs):
    workflow = load_workflow()
    params = load_params(kwargs)
    context = WorkflowContext(params=params)

    for segment in workflow["segments"]:
        if segment["kind"] == "rpa":
            result = run_rpa_segment(segment, context)
        elif segment["kind"] == "script":
            result = run_script_segment(segment, context)
        elif segment["kind"] == "mcp":
            result = run_mcp_segment(segment, context)
        elif segment["kind"] == "llm":
            result = run_llm_segment(segment, context)
        else:
            raise ValueError(f"Unsupported segment kind: {segment['kind']}")

        context.store_segment_outputs(segment["id"], result)

    return context.final_outputs()
```

### params.schema.json

参数 schema 必须由所有 segment 的用户输入参数合并生成。

规则：

- 同名同类型参数自动合并。
- 同名不同类型参数必须在发布草稿中产生 warning，让用户重命名或确认。
- `secret` 类型不写入默认值。
- 来自上游 segment output 的参数不暴露给最终用户，除非用户显式选择暴露。

### credentials.example.json

认证配置不能硬编码真实凭据。

示例：

```json
{
  "business_system": {
    "type": "browser_session",
    "description": "业务系统登录态，由用户在本地浏览器或凭据库中提供。"
  }
}
```

## 前端设计

### Segment 卡片

主对话中的 segment 卡片默认折叠，显示：

- 片段名称。
- 片段类型。
- 步骤数或脚本状态。
- 参数数。
- 测试状态。
- 主要输出。

展开后显示：

- 片段用途说明。
- 输入参数。
- 输出产物。
- RPA 步骤摘要或脚本摘要。
- 测试日志入口。
- 编辑按钮。

### 片段编辑弹窗

统一命名为“片段编辑器”，根据 `kind` 切换内容：

- RPA 类型显示录制、步骤、定位器、测试视图。
- Script 类型显示脚本、依赖、输入输出、测试运行结果。
- MCP 类型显示工具选择、参数映射、测试调用结果。
- LLM 类型显示 prompt、schema、样例输入输出。

RPA 弹窗可以复用现有 recorder/configure/test 页面能力，但外层概念不再叫“技能配置”。

### 发布弹窗

发布弹窗必须在保存前出现，包含：

- 技能名称。
- 技能标题。
- 技能描述。
- 触发示例。
- 输入参数列表。
- 认证要求。
- 输出结果。
- 片段列表和顺序。
- 发布 warning。

没有最终技能名称时，不允许保存。

## 后端设计

### 模块边界

建议新增或演进以下模块：

- `backend/workflow/models.py`: workflow run、segment、publish draft 模型。
- `backend/workflow/orchestrator.py`: run 生命周期、segment 添加、状态迁移。
- `backend/workflow/publishing.py`: 发布草稿生成和技能产物生成。
- `backend/workflow/runners.py`: 生成 skill runner 所需模板和运行协议。
- `backend/workflow/adapters/rpa.py`: RPA segment 适配。
- `backend/workflow/adapters/script.py`: script segment 适配。
- `backend/workflow/adapters/mcp.py`: MCP segment 适配。

现有 `backend/recording` 可以保留作为兼容层，但新的多类型能力应逐步迁移到 `workflow` 域。

### 与现有 RPA 模块关系

RPA 模块继续负责：

- 浏览器启动。
- 动作录制。
- locator 生成。
- Playwright 步骤生成。
- RPA 测试执行。

Workflow 模块负责：

- 将 RPA 结果包装成 `WorkflowSegment`。
- 管理 RPA segment 与其他 segment 的输入输出。
- 生成最终技能产物。

### 内置技能关系

新增内置技能建议命名为 `workflow-creator`。

职责：

- 在主对话中识别用户是否要创建、继续、测试或发布 workflow。
- 根据用户自然语言选择 segment 类型。
- 调用后端 workflow API 创建 run、添加 segment、生成发布草稿。
- 在需要用户确认时，触发前端 action prompt 或弹窗。

`workflow-creator` 不负责直接写最终技能文件。最终保存仍由后端 publishing 服务完成，并与 `skill-creator` 的保存标准保持一致。

## 状态机

### WorkflowRun 状态

```text
draft
  -> recording
  -> configuring
  -> testing
  -> ready_to_publish
  -> published
```

允许从 `ready_to_publish` 回到 `draft`，用于用户继续添加片段。

### WorkflowSegment 状态

```text
draft
  -> configured
  -> testing
  -> tested
  -> failed
```

失败后允许回到 `configured` 或 `draft`。

## 错误处理

### RPA segment 错误

- locator 不稳定时，片段测试结果标记为 failed，并提示用户重新选择定位器。
- 下载文件未出现时，输出 artifact 标记为 missing。
- 登录态缺失时，生成 credential warning。

### Script segment 错误

- 找不到输入 artifact 时，阻止测试并提示选择上游输出。
- 脚本执行失败时，保存 stderr 和失败行号。
- 依赖缺失时，提示添加 requirements。
- 输出文件未生成时，测试失败。

### 发布错误

- 没有 segment 时不允许发布。
- 有未测试 segment 时允许生成草稿，但必须显示 warning。
- 参数冲突时不允许直接保存，必须由用户确认。
- 技能名称非法时前端即时校验，后端再次校验。

## 测试策略

### 后端测试

必须新增以下测试：

- 多段 workflow 发布时，`workflow.json` 包含所有 segment 且顺序正确。
- RPA segment 和 script segment 能同时出现在同一个 workflow 中。
- `SKILL.md` front matter 使用用户确认的最终名称和描述。
- `params.schema.json` 正确合并多个 segment 的用户输入参数。
- secret 参数不会写入默认值。
- `skill.py` 不再只包含最后一个 segment，而是按 workflow 调度所有 segment。
- 发布草稿接口返回 warning，例如未测试 segment、参数冲突、缺少技能名称。

### 前端测试

必须覆盖：

- RPA 片段配置页不再显示“技能名称”。
- Script 片段显示脚本、输入、输出和测试结果。
- Segment 卡片默认折叠，展开后显示输入输出和步骤摘要。
- 发布弹窗必须允许用户编辑最终技能名称和描述。
- 保存时传递完整 Skill Publish Draft，而不是只传 `publish_target`。

### 端到端场景

核心验收场景：

1. 用户在对话中说“我要录制一个下载并转换文件的技能”。
2. 系统打开 RPA segment 弹窗。
3. 用户录制下载文件流程。
4. 用户配置片段为“下载报表”。
5. 用户回到对话说“接下来把下载的文件转成 CSV”。
6. 系统生成 script segment。
7. 用户测试 script segment。
8. 用户点击准备发布。
9. 用户填写最终技能名称和描述。
10. 系统保存技能。
11. 技能目录包含 `SKILL.md`、`skill.py`、`workflow.json`、`params.schema.json`、`credentials.example.json` 和 `segments/`。
12. `workflow.json` 包含两个 segment。
13. `skill.py` 能按顺序执行两个 segment。

## 迁移策略

### 第一阶段

在现有 `recording` 基础上引入 workflow 数据结构和发布草稿，不立即删除旧 API。

### 第二阶段

前端将对话式录制流程切换到 workflow API。旧 RPA recorder 页面继续可用。

### 第三阶段

发布器从 `recording.publishing` 迁移到 `workflow.publishing`。旧发布器只作为兼容入口调用新发布器。

### 第四阶段

移除 `sessions.py` 中与录制意图相关的旁路处理。主对话只通过内置 `workflow-creator` 技能进入该流程。

## 推荐实现顺序

1. 新增 workflow 模型和发布草稿模型。
2. 重写发布器，让它基于 `SkillPublishDraft` 和 `workflow.json` 生成产物。
3. 增加 script segment 的最小能力，支持从上游 artifact 生成和测试 Python 脚本。
4. 修改 RPA 片段配置 UI，把技能名称和描述改为片段名称和片段用途。
5. 新增发布弹窗，保存前要求用户确认最终技能信息。
6. 修改主对话 segment 卡片，默认折叠并展示类型、步骤数、参数数和测试状态。
7. 用 `workflow-creator` 替换 session 旁路触发逻辑。
8. 补齐后端、前端和端到端测试。

## 结论

最终能力不应继续定位为“对话式 RPA 录制”，而应定位为“对话式 Workflow Segment Creator”。

这个设计能同时覆盖：

- 录制浏览器下载文件。
- 继续生成脚本处理下载文件。
- 多段片段按顺序组合。
- 参数和认证在发布前统一确认。
- 保存为合格的 Codex/RpaClaw 技能。

RPA 录制仍然重要，但它只是 workflow 的一种 segment 类型。发布产物必须围绕 workflow，而不是围绕单个 RPA 脚本。
