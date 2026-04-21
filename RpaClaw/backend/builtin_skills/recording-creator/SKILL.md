---
name: recording-creator
description: "通过对话发起并编排 RPA 技能录制或 MCP 工具录制。凡是用户提到“录制流程”“录个业务流程技能”“录制一个 MCP 工具”“把网页操作录成技能”“先下载再继续处理文件”这类诉求时，都应触发本技能。Use this whenever the user wants to record a browser workflow, capture an MCP operation, or build a multi-segment recorded automation from chat."
---

# recording-creator

用于把主对话入口里的录制需求转成结构化的生成、测试验证、修复和发布流程。这个技能是录制类需求的第一入口，职责等同于 `skill-creator` / `tool-creator` 对普通技能和工具创建的入口职责。

## 何时使用

在这些场景下优先使用本技能：

- 用户要录制一个网页/RPA 流程。
- 用户要录制一个业务流程技能。
- 用户要录制一个 MCP 操作或 MCP 工具。
- 用户明确提到“把这个流程录下来”“录个自动化”“录成技能/工具”。
- 用户要做多段处理，例如第一段下载文件，第二段继续处理文件。

## 生命周期

1. 识别录制目标，确认本次要生成 `skill`、`tool`，还是先保持为草稿。
2. 调用 `start_recording_run` 创建 `Recording Run`，不要依赖聊天路由的关键词旁路。
3. 在调用 `start_recording_run` 之前，先用 `inspect_recording_runs` 判断当前会话是否已有未发布的 run。
4. 如果用户在说“继续下一段”“把上一步输出作为下一步输入”“接着处理刚才下载的文件”，优先调用 `continue_recording_run` 或 `add_script_recording_segment`，不要新开 run。
5. 为当前目标创建或继续一个 `segment`。
6. 如果当前段需要浏览器或人工交互，打开录制工作台并指导用户完成录制。
7. 如果当前段是文件处理或 MCP 调用，直接在聊天中完成，并把调用过程记录为工具步骤；脚本处理段必须调用 `add_script_recording_segment` 落到当前 run。
8. 每段完成后必须提取并注册 `artifacts`，例如下载文件、文本、JSON 或结构化字段。
9. 用户继续下一段时，优先显式绑定上一段 artifact 或变量；用 `bind_recording_segment_io` 把前一段输出写成后一段输入，不要只在自然语言里口头说明。
10. 用户准备保存前，必须调用 `begin_recording_test` 进入测试验证。
11. 测试失败时进入修复流程，优先使用候选定位器切换和单步重放；全部失败时再建议重录本步或本段。
12. 测试通过后调用 `prepare_recording_publish` 进入发布准备。

## 发布规则

- 目标是 skill 时，准备 staging 结果，并调用 `propose_skill_save`，不要直接写入外部 Skills 目录。
- 目标是 tool 或 MCP 工具时，准备 staging 结果，并调用 `propose_tool_save`，不要直接写入 Tools 目录。
- 发布前要向用户说明测试结果、产物摘要、保存目标和需要确认的名称。
- 如果用户尚未明确保存目标，先询问是保存为业务流程技能还是 MCP 工具。
- 默认发布当前会话里最近一个仍在继续的 run；只有在 `inspect_recording_runs` 显示存在多个未发布 run 且用户意图不明确时，才回问用户要发布哪一个。

## 交互约束

- 只有明确的录制诉求才进入录制模式。
- 需要人工交互时打开录制工作台；纯文件处理段可以直接在聊天里继续。
- 不要把录制理解为单段动作。默认允许多段：`segment -> artifact -> next segment`。
- 如果用户说“把第一段输出作为第二段输入”“引用上一步标题/文件/JSON”，必须调用 `bind_recording_segment_io` 显式建立 `source_ref`。
- 每段结束后都要总结 `intent`、`steps`、`artifacts` 和下一段可用输入。
- 指代不清时必须回问，例如“刚才那个文件”对应多个 artifact。

## 输出目标

你应帮助系统沉淀以下结果：

- 一个 `Recording Run`。
- 一个或多个 `segment`。
- 可供下一段引用的 `artifact`。
- 可测试的录制脚本或工具步骤。
- 测试验证结果和必要的修复记录。
- 最终可通过 `propose_skill_save` 或 `propose_tool_save` 发布的录制产物。
