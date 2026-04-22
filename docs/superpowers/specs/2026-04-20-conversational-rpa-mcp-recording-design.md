# 会话内技能化 RPA / MCP 录制最终方案

## 文档状态

- 状态：已按当前实现更新
- 适用范围：主对话中的对话式录制、segment 编排、测试与发布
- 当前入口技能：`recording-creator`
- 当前交互形态：大弹窗录制/测试 + 聊天流内联 segment 卡片

## 目标

把原本依赖独立录制页面、技能库/工具库入口的录制能力，改造成和 `skill-creator` / `tool-creator` 同等级的主对话内置能力。

本方案要求：

- 用户可以直接在主对话中发起 RPA、MCP 或多段 workflow 录制。
- 入口由 `recording-creator` 接管，而不是 `sessions.py` 做关键词旁路。
- 录制结果以 `segment -> artifact -> next segment` 的方式显式编排。
- 非交互段可以直接在聊天里生成脚本片段，不强制重新打开浏览器。
- 录制能力具备完整生命周期：生成、测试、修复、发布。
- 发布收口按目标分流：skill 继续复用 `propose_skill_save`；tool / MCP 工具由 recording 发布链路保存到 RPA MCP 工具注册表，不再走普通 `propose_tool_save`。

## 已落地架构

### 1. 入口层：内置技能接管

主 agent 在识别到以下意图时读取 `recording-creator/SKILL.md`：

- “我要录制个业务流程技能”
- “录制一个 MCP 工具”
- “再录一段”
- “把上一段输出作为下一段输入”
- “开始整体测试”
- “准备发布”

已不再依赖聊天路由中的关键词短路逻辑。

### 2. 生命周期工具层

`backend/deepagent/tools.py` 提供录制生命周期工具：

- `inspect_recording_runs`
- `start_recording_run`
- `continue_recording_run`
- `add_script_recording_segment`
- `bind_recording_segment_io`
- `begin_recording_test`
- `prepare_recording_publish`

边界约束：

- 技能负责判断何时调用这些工具。
- 工具负责执行，不再自行重新判断“这是不是录制意图”。

### 3. 状态编排层

`backend/recording/*` 维护 `RecordingRun`、`RecordingSegment`、`RecordingArtifact`。

当前运行状态包含：

- `draft`
- `recording`
- `waiting_user`
- `processing_artifacts`
- `ready_for_next_segment`
- `testing`
- `needs_repair`
- `ready_to_publish`
- `saved`
- `failed`

### 4. 发布层

发布前会把 recording run 转换为 workflow 结构，再生成：

- `workflow.json`
- `params.json`
- `SKILL.md`
- `skill.py`
- `segments/*`

其中 `params.json` 与普通 RPA 技能录制保持一致，是技能页面默认参数、参数说明、敏感参数 `credential_id` 以及应用内执行时自动注入凭证的唯一事实来源。workflow runner 也直接读取 `params.json` 的 `original_value` 作为默认值，再用调用时传入的参数覆盖。当前实现不再生成 `params.schema.json` 或空的 `credentials.example.json`。

最终仍通过已有保存确认机制进入正式技能库或工具库。

## 交互设计

### 录制交互

当前实现不再使用“聊天右侧固定大面板”作为最终交互形态，而是：

- 在主对话中触发录制
- 自动打开大弹窗录制页
- 在弹窗内完成录制、配置与测试
- 完成后返回主对话

这样可以直接复用 `/rpa/recorder`、`/rpa/configure`、`/rpa/test`、`/rpa/workflow-test` 的共享录制核心，而不是再维护一套缩水版 workbench。

### 聊天区展示

录制完成后，segment 不再统一堆在输入框上方，而是以 `recording_segment` 消息内联插入聊天流：

- 位置和触发该段录制的用户消息相邻
- 默认折叠
- 展开后可查看步骤、参数、输入、输出、候选定位器

历史会话恢复时只恢复状态和卡片，不会再次自动弹出录制/测试/发布弹窗。

### 测试交互

测试分两类：

- 单段测试：进入 `/rpa/test`
- 多段整体测试：进入 `/rpa/workflow-test`

二者都通过录制模态承载，不再跳到完全脱离聊天上下文的新流程页。

### 发布交互

当 run 进入可发布状态后：

- 前端展示发布草稿弹窗
- 用户确认名称、描述、输入输出、纳入的 segments
- 确认后按目标分流：skill 走 `propose_skill_save`，tool / MCP 工具走 recording 发布链路并保存到 RPA MCP 工具注册表

不会要求用户通过固定话术再次输入“开始测试”“准备发布”才能继续。

## 数据模型

### Recording Run

```json
{
  "id": "run_xxx",
  "session_id": "chat_session_id",
  "user_id": "user_id",
  "type": "rpa | mcp | mixed",
  "status": "draft | recording | waiting_user | processing_artifacts | ready_for_next_segment | testing | needs_repair | ready_to_publish | saved | failed",
  "active_segment_id": "seg_xxx",
  "segments": [],
  "artifact_index": [],
  "publish_target": "skill | tool | null"
}
```

### Recording Segment

录制域当前支持：

- `rpa`
- `mcp`
- `script`
- `mixed`

```json
{
  "id": "seg_xxx",
  "run_id": "run_xxx",
  "kind": "rpa | mcp | script | mixed",
  "intent": "下载报表",
  "status": "recording | completed | failed | blocked",
  "imports": {},
  "exports": {},
  "steps": [],
  "artifacts": []
}
```

### Artifact

```json
{
  "id": "artifact_xxx",
  "run_id": "run_xxx",
  "segment_id": "seg_xxx",
  "name": "downloaded_file",
  "type": "file | text | json | table",
  "path": "/workspace/xxx/report.xlsx",
  "value": null
}
```

## 多段编排规则

### 输入输出必须结构化

每个 segment 都应沉淀：

- `inputs`
- `outputs`
- `artifacts`

跨段依赖不能只靠自然语言记忆，必须通过 `bind_recording_segment_io` 建立 `source_ref`。

### 支持的典型场景

- 第一段下载文件，第二段脚本转换文件
- 第一段提取文本，第二段把文本作为搜索词
- 第一段输出 JSON，第二段继续用 MCP 工具处理 JSON

### 当前不做

- 条件分支
- 循环
- 可视化 DAG 画布
- 任意拖拽重排

## 修复与定位器编辑

当前支持的轻量修复能力：

- 查看步骤和当前定位器
- 查看候选定位器
- 切换候选定位器
- 立即重校验

当前不支持：

- 完整 Playwright 手写编辑器
- 页面重新选点生成任意新定位器
- 通用流程图式编辑

## 与 `skill-creator` / `tool-creator` 的关系

三者职责分工如下：

- `recording-creator`：录制、编排、测试、修复、发布准备
- `skill-creator`：普通技能创建与保存规范
- `tool-creator`：普通工具创建与保存规范

录制能力不是替代后二者，而是在“录制型工作流”场景下作为上游入口，并在发布阶段复用它们的保存机制。

## 已清理的旧方案

以下方案不再是当前实现：

- `sessions.py` 里的录制关键词旁路
- 聊天页自研缩水版 `RecordingWorkbench`
- 录制完成后把所有 segment 固定堆在输入框上方
- 录制后要求用户通过固定文案回复才能继续测试/发布
- 用 run id 或第一个 segment 名直接生成技能名称并立即落库
