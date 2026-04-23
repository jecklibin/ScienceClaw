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
| 追加脚本、转换下载文件或数据处理段 | `add_script_recording_segment`，不要打开录制框 |
| 绑定前后段输入输出 | `bind_recording_segment_io` |
| 开始测试、完整测试、验证一下 | `begin_recording_test` |
| 准备发布、保存录制结果 | 先确认测试通过，再调用 `prepare_recording_publish` |

## 执行流程

1. 先调用 `inspect_recording_runs`，判断当前会话是否已有可继续的 run。
2. 如果用户明确要新建，调用 `start_recording_run`；如果用户是在补充前序浏览器/RPA 流程，调用 `continue_recording_run`。
3. 如果当前段不是浏览器/RPA 操作，而是文件转换、数据清洗、JSON 处理等，调用 `add_script_recording_segment`，不要调用 `continue_recording_run`。
4. 如果用户表达“用上一段结果”“把标题传给搜索”“处理刚才下载的文件”，调用 `bind_recording_segment_io` 保存结构化绑定。
5. 用户要求测试时调用 `begin_recording_test`，读取返回的成功状态、日志和错误信息后直接向用户汇报。
6. 测试失败时优先修复定位器、参数、绑定或脚本；只有局部修复无效时才建议重录。
7. 发布前确认目标是 `skill` 还是 `tool`，再调用 `prepare_recording_publish`。

## 创建或续录

优先沿用同一个 run：

- 用户说“继续”“再录一段”，且下一段仍是浏览器/RPA/MCP 交互操作时，用 `continue_recording_run`。
- 用户说“接着处理刚才下载的文件”“转换文件”“清洗数据”时，仍沿用同一个 run，但用 `add_script_recording_segment`，不要打开录制框。
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
- 文件处理、数据解析、格式转换、内容清洗等纯脚本步骤不属于交互录制，不要用 `continue_recording_run` 打开 workbench。
- `continue_recording_run` 只用于需要用户继续操作浏览器或 MCP 录制工作台的片段。

## Segment 规则

交互录制段：

- 用 `start_recording_run` 或 `continue_recording_run` 打开录制。
- 用户完成录制后，总结本段标题、用途、步骤数、输入、输出和 artifact。
- 如果本段输出可供后续使用，明确提示用户可以绑定到下一段。

脚本处理段：

- 用 `add_script_recording_segment`，不要只在回复里描述脚本。
- 处理上一段下载的文件、artifact 或结构化输出时，留在对话中生成脚本并保存为脚本处理段，不要弹出录制工作台。
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

## 执行一致性要求

登录和凭据：

- 如果录制步骤包含登录、密码、Token、账号密钥等敏感输入，必须把对应参数标记为 `sensitive: true`，并在用户已选择凭据时保存 `credential_id`。
- 不要要求用户在对话里重新输入或复制密码。完整测试、发布后的技能执行、发布后的 MCP 工具执行都会从保存的参数配置中注入凭据。
- 非敏感默认值可以保存在参数配置中；如果调用时需要覆盖，再把它定义为可暴露输入。

下载文件再处理：

- 浏览器下载产生的文件路径必须视为运行期 artifact，不要把 Playwright 捕获到的临时路径当成可复用路径，也不要要求用户复制该路径。
- RPA 下载段应输出 file artifact；后续脚本段的文件输入应通过 `bind_recording_segment_io` 绑定到该 artifact，使用 `source_type: "artifact"` 和 `source_ref: "artifact:<artifact_id>"`。
- 脚本段应从绑定输入读取文件路径，例如 `input_path`，而不是硬编码录制时看到的路径。
- 完整测试和最终执行会把下载文件保存到运行期 `_downloads_dir`，artifact 绑定会解析为该运行期真实文件路径。

纯数据处理片段：

- “处理刚才下载的文件”“继续加工前一段产物”“做解析/转换/清洗”等不是浏览器操作，应使用 `add_script_recording_segment` 生成脚本片段，不要打开录制工作台。
- 添加脚本片段后，如果它依赖历史下载文件或历史输出，立即建立结构化绑定；不要只在自然语言回复中描述依赖关系。

## 测试失败后的行为约束

- 调用 `begin_recording_test` 后如果失败，不要只回复“我会继续帮你修复”然后停住。
- 必须先读取并总结 `summary.error`、`summary.stderr`、`summary.logs` 或 `test_payload.execution.result` 中的具体失败信息。
- 如果已经能判断失败原因，直接给出明确结论与下一步修复动作；不要只给模糊承诺。
- 如果失败原因还不够明确，明确告诉用户你接下来要查看哪一层：参数、凭据注入、输入输出绑定、脚本逻辑、定位器或工作流拼接。
- 当用户要求“保存技能/保存工具”时，即使当前测试失败，也可以继续准备发布草稿，但必须明确告知当前测试状态仍为失败，保存的是当前版本而不是已验证通过版本。

## 脚本片段上下文约束

- 当用户说“处理刚下载的文件”“继续处理上一段结果”“把前面产物再转换/解析/清洗一下”时，必须把这段脚本看作“基于已有 workflow 上下文的后处理段”，而不是脱离上下文的通用模板。
- 在调用 `add_script_recording_segment` 之前，必须先通过 `inspect_recording_runs` 读取当前 run 的 `next_segment_context`，确认：
  1. 用户这一步的目标是什么。
  2. 前面有哪些可复用的 outputs / artifacts。
  3. 哪个来源才是这段脚本真正依赖的上游输入。
- 生成脚本段时，先规划再保存。规划至少要明确：
  1. 这段脚本要完成什么业务目标。
  2. 它依赖哪些上游输入，来源是 `segment_output`、`artifact` 还是新的用户参数。
  3. 它要产出哪些结构化 outputs / artifacts，供后续测试、发布和执行使用。
- 如果上游来源是下载文件或其他 file artifact，下游输入代表的是“实际文件路径”，不是下载目录。只有当上游片段明确产出目录时，才能把下游输入建模为目录。
- `add_script_recording_segment` 的 `entry` 通常留空即可；只有在确实需要自定义脚本文件名时，才传合法的相对 `.py` 路径。自然语言目标、片段描述、业务说明只能放在 `purpose`，不能放进 `entry`。
- 如果脚本段依赖前面下载或生成的文件，不要把录制时看到的本地临时路径写进脚本，也不要要求用户复制该路径。必须设计一个显式文件输入，并通过 `artifact:<id>` 绑定到对应 artifact，由运行期在 `_downloads_dir` 下解析真实执行路径。
- 只暴露“调用方必须控制”的参数。来源于上游 outputs / artifacts 的值应该通过绑定传递，而不是退化成让用户手填的普通参数。
- 不要根据某个具体文件类型、某种目标格式或某个示例模板去硬编码脚本段结构。脚本段的输入、参数和输出必须由“当前用户意图 + 已有 workflow 上下文 + 运行期约束”共同决定。
- 如果需要输出落盘文件，优先让脚本基于绑定输入自行推导默认输出位置；只有当用户明确要求自定义输出路径或文件名时，才把它暴露为参数。
- 脚本片段一律按 `run(context, **kwargs)` 生成。不要生成 `main(...)`，也不要生成依赖全局 `params/inputs/outputs` 的脚本。
- `kwargs` 只接收这段脚本真正需要的输入；`context.runtime` 用来读取运行期路径与执行环境；输出必须 `return { ... }`。
- `context.runtime` 中默认可用的关键字段包括：`downloads_dir`、`workspace_dir`、`skill_dir`、`workflow_path`、`segments_dir`。其中：
  - `downloads_dir` / `_downloads_dir`：完整测试或最终执行时用于保存下载文件的运行期目录。
  - `workspace_dir` / `_workspace_dir`：当前会话或本次测试对应的工作区根目录；如果用户要求把处理结果额外落盘到工作区，应基于这个目录推导。
  - `skill_dir` / `_skill_dir`：当前 workflow skill 的生成目录；可用于读取同 skill 一起发布的静态资源，但不应用它代替运行期下载目录。
- 对于来自上游 `artifact:<id>` 的文件输入，`kwargs["input_path"]` 应视为“已经解析好的运行期真实文件路径”。不要再把它当成下载目录，也不要再次去扫描临时目录寻找“最新文件”。
- 如果用户没有明确要求自定义输出路径，优先根据 `input_path` 推导默认输出文件位置；如果输出不需要落盘，则直接返回结构化文本/JSON 结果即可。只有当用户明确要求指定输出路径、文件名或输出目录时，才暴露 `output_path` 之类的参数。

### 脚本片段参考实现

参考实现 A：处理上游文件 artifact，直接返回结构化结果

```python
from pathlib import Path


def run(context, **kwargs):
    input_path = Path(kwargs["input_path"])
    content = input_path.read_text(encoding="utf-8")
    return {
        "summary": content[:200],
        "source_file": str(input_path),
    }
```

参考实现 B：处理上游文件，并在运行期生成默认输出文件

```python
from pathlib import Path


def run(context, **kwargs):
    input_path = Path(kwargs["input_path"])
    workspace_dir = Path(context.runtime["workspace_dir"])
    output_path = Path(kwargs.get("output_path") or workspace_dir / f"{input_path.stem}.md")

    transformed = f"# Converted from {input_path.name}\n"
    output_path.write_text(transformed, encoding="utf-8")

    return {
        "output_file": {
            "path": str(output_path),
            "filename": output_path.name,
        },
        "markdown_content": transformed,
    }
```

参考实现 C：消费上游输出和用户参数，不暴露多余输入

```python
def run(context, **kwargs):
    title = kwargs["title"]
    limit = int(kwargs.get("limit", 5))
    return {
        "preview": title[:limit],
    }
```

生成脚本片段前先按下面的问题完成规划，再调用 `add_script_recording_segment`：

1. 这段脚本的直接上游输入是谁，是 `segment_output`、`artifact` 还是新的用户参数。
2. 这段脚本是否真的需要 `output_path`；如果不需要，就不要暴露。
3. 如果需要落盘，默认输出应落在 `workspace_dir` 还是与 `input_path` 同目录；只有用户明确要求时才让调用方决定。
4. 完整测试时是否可以直接复用真实 artifact 绑定；如果答案是否，就说明建模还不正确，应先修正输入输出设计。

## 录制测试与修复闭环

生成或修改脚本片段后，不能只保存脚本就结束。录制阶段也要像正常技能执行一样完成“执行完整 workflow、校验契约、定位失败、修复、重测”的闭环。

完整测试必须直接调用 `begin_recording_test`：

- 用户说“完整测试”“执行完整测试”“验证整个流程”“测试这个录制流程”时，立即调用 `begin_recording_test`，不要先做其他本地预检。
- `begin_recording_test` 会按 workflow 顺序执行所有必要的上游 segment，包括前置下载步骤、浏览器/RPA 步骤以及已有 artifact 绑定，然后再执行后续脚本片段。
- 不要为了验证脚本在会话工作区创建 sample 文件、sample 目录、伪造下载结果、手工复制录制文件或额外 helper 脚本。
- 不要用 `write_file`、`file_write`、`terminal_execute`、`execute python`、`python some_script.py --help` 或单独运行某个脚本来替代完整测试。
- 不要先补测试样例，再跑完整链路。只要 workflow 中已有真实上游 segment，就必须依赖真实上游输出和 artifact 绑定。

读取 `begin_recording_test` 返回结果后再回复用户：

- 必须检查 `run.status`、`run.testing.status`、`summary.error`、`summary.stderr`、`summary.logs`、`summary.repair_context` 和 `test_payload.execution.result.contract`。
- 如果声明的 output 或 artifact 没有产出，即使进程退出成功，也要视为测试失败。
- 如果测试通过，说明哪些 segment 已验证、产出了哪些关键 output / artifact，并提示可以继续录制或准备发布。
- 如果测试失败，先定位失败来自参数、凭据注入、输入输出绑定、脚本逻辑、定位器还是 workflow 拼接，不要只给“我可以帮你修复”的空泛回复。

测试失败且返回 `repair_context` 时，直接进入修复闭环：

1. 读取 `repair_context.context_path`。
2. 检查生成的 `workflow.json`。
3. 检查 `repair_context.skill_dir` 下的 segment 脚本文件。
4. 在修复工作区中修正脚本或 workflow contract。
5. 调用 `apply_recording_test_repairs` 把修复后的脚本片段同步回当前 recording run。
6. 再次调用 `begin_recording_test`。
7. 只有重测通过后，才能向用户报告完整测试成功。
