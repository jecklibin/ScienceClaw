---
name: recording-creator
description: "Use when the user wants to record an RPA/browser workflow, create a business workflow skill, create an MCP tool from recorded actions, add another recording segment, add a script/data-processing segment, bind one segment output or artifact to another segment input, run a full recording test, repair a recorded workflow, or publish a recorded workflow as a skill or MCP tool."
---

# recording-creator

主对话中的录制编排技能。它负责把用户的自然语言诉求转换成 recording lifecycle tool 调用，并把多段录制沉淀为可测试、可发布的 workflow。

## 何时使用

使用本技能：

- 用户要录制业务流程技能、RPA 流程、浏览器操作或 MCP 工具。
- 用户要继续当前录制，例如“再录一段”“补一个片段”“接着处理刚才下载的文件”。
- 用户要把上一段输出、artifact、文件或 JSON 作为下一段输入。
- 用户要执行完整测试、修复失败步骤、准备发布或保存录制结果。

不要使用本技能：

- 用户是在执行已经保存的技能或 MCP 工具。
- 用户只是临时浏览网页、普通调用工具，未表达要沉淀为录制 workflow。
- 用户是在编辑普通 `SKILL.md`，且没有录制、续录、测试或发布意图。

## 快速决策

| 用户意图 | 应调用 |
| --- | --- |
| 新录一个流程、技能或工具 | `inspect_recording_runs` 后调用 `start_recording_run` |
| 继续已有流程、再录一段 | `inspect_recording_runs` 后调用 `continue_recording_run` |
| 追加脚本或文件处理段 | `add_script_recording_segment` |
| 绑定前后段输入输出 | `bind_recording_segment_io` |
| 开始测试、完整测试、验证一下 | `begin_recording_test` |
| 准备发布、保存录制结果 | 先确认测试通过，再调用 `prepare_recording_publish` |

## 执行流程

1. 先调用 `inspect_recording_runs`，判断当前会话是否已有可继续的 run。
2. 如果用户明确要新建，调用 `start_recording_run`；如果用户是在补充前序流程，调用 `continue_recording_run`。
3. 如果当前段不是浏览器/RPA 操作，而是文件转换、数据清洗、JSON 处理等，调用 `add_script_recording_segment`。
4. 如果用户表达“用上一段结果”“把标题传给搜索”“处理刚才下载的文件”，调用 `bind_recording_segment_io` 保存结构化绑定。
5. 用户要求测试时调用 `begin_recording_test`，读取返回的成功状态、日志和错误信息后直接向用户汇报。
6. 测试失败时优先修复定位器、参数、绑定或脚本；只有局部修复无效时才建议重录。
7. 发布前确认目标是 `skill` 还是 `tool`，再调用 `prepare_recording_publish`。

## 创建或续录

优先续录：

- 用户说“继续”“再录一段”“接着处理”。
- 当前会话存在未保存或可继续的 run。
- 当前 run 已测试通过，但用户又要求补充片段。此时继续同一个 run，并在新增片段后重新完整测试。

优先新建：

- 用户明确说“新录一个”“另外录一个”。
- 当前会话没有可继续的 run。
- 现有 run 已保存，且用户没有表达要修改它。

`start_recording_run` / `continue_recording_run` 参数规则：

- `kind` 用 `rpa`、`mcp` 或 `mixed`，根据用户目标判断。
- `publish_target` 只在目标明确时传 `skill` 或 `tool`。
- 工具/MCP 录制目标传 `tool`，不要保存成 skill。

## Segment 规则

交互录制段：

- 用 `start_recording_run` 或 `continue_recording_run` 打开录制。
- 用户完成录制后，总结本段标题、用途、步骤数、输入、输出和 artifact。
- 如果本段输出可供后续使用，明确提示用户可以绑定到下一段。

脚本处理段：

- 用 `add_script_recording_segment`，不要只在回复里描述脚本。
- `title` 写清这段做什么。
- `purpose` 写清业务用途。
- `script` 必须是可执行处理逻辑。
- `params_json`、`inputs_json`、`outputs_json` 必须结构化描述参数、输入和输出。

参数与凭证：

- 每个参数都要有 `description`。
- 可由用户调用时覆盖的值应暴露为输入。
- 敏感值标记为 `sensitive: true`，并在已选择凭证时保存 `credential_id`。
- 不要把参数、凭证或绑定只写在自然语言回复里。

## 输入输出绑定

用户表达以下含义时必须绑定：

- 第一段输出作为第二段输入。
- 用刚才下载的文件继续处理。
- 把提取到的标题、名称、JSON、文件路径传给后续步骤。
- 搜索词、表单值、文件路径来自历史 segment 或 artifact。

绑定时优先使用明确的 `source_segment_id`、`output_name`、`target_segment_id`、`input_name`。如果有多个候选输出或 artifact，先向用户确认，不要猜。

绑定完成后告诉用户：

- 哪个来源绑定到了哪个输入。
- 这个绑定会用于完整测试、发布和最终执行。

## 测试与修复

调用 `begin_recording_test` 后，必须阅读返回结果再回复用户：

- `run.status`
- `run.testing.status`
- `test_payload.execution.result.success`
- 关键 `logs`、`stderr` 或错误原因

测试通过：

- 告诉用户可以继续录下一段，或准备发布。

测试失败：

- 说明失败点和原因。
- 优先建议切换定位器、补充参数、修复输入输出绑定、修正脚本。
- 不要在没有定位原因时要求用户全部重录。

## 发布规则

发布前必须确认：

- 发布哪一个 run。
- 目标是 skill 还是 tool / MCP 工具。
- 名称、描述、输入输出是否清楚。
- 所有需要发布的 segment 都已包含并测试通过。

目标是 skill：

- 调用 `prepare_recording_publish`。
- 然后进入 `propose_skill_save` 保存确认。

目标是 tool / MCP 工具：

- 调用 `prepare_recording_publish`。
- 如果返回结果表示 MCP 工具已保存，直接向用户说明工具名称和结果。
- 不要再调用普通 `propose_tool_save`。
- 不要把工具目标保存成技能。

## 回复要求

- 基于当前 run 状态给下一步，不要输出固定话术。
- 每次完成重要动作后，给出简洁摘要和可选下一步。
- 如果当前会话有多个未发布 run 且用户没有指定对象，先问要操作哪一个。
- 如果用户要求执行已保存技能或工具，退出录制流程，按执行请求处理。

## 成功标准

一次完成良好的录制会话应包含：

- 一个明确的 Recording Run。
- 一个或多个已配置、已测试的 segment。
- 结构化输入、输出、参数和凭证信息。
- 必要的跨段绑定。
- 完整测试结果。
- 最终发布为 skill 或 MCP 工具的结果。
