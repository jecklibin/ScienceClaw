# RPA Skill / MCP Tool 双发布形态与 Tool Studio 信息架构设计

## 背景

当前 RPA 录制链路默认产出的是录制会话、参数化配置、测试脚本和最终 Skill 导出物。随着 RPA MCP Gateway 能力引入，录制产物又可以进一步发布为 MCP Tool。

现在的问题不是“能不能转”，而是“入口和对象模型是否合理”：

- 如果把 MCP Tool 的完整定义过程长期堆在 RPA 配置页里，页面会同时承担录制调试、Skill 发布、MCP 发布、Schema 定义、Auth 策略、共享发布，职责过重。
- 如果把 MCP Tool 完全挪到独立 Tool 页面里重新录制，又会割裂已经存在的 RPA 录制体验。
- Skill 与 MCP Tool 在概念上并不是同一种产物，不应在 UI 和数据模型上混成一个东西。

本补充设计用于明确三件事：

1. RPA 录制的定位
2. Skill 与 MCP Tool 的边界
3. 录制完成后发布为 Skill / MCP Tool 的合理产品路径

## 设计结论

推荐将产品模型明确为：

- `RPA Recording`：自动化流程的生成方式
- `Skill`：面向本地或团队 agent 的工作流能力包
- `MCP Tool`：面向外部 agent 的标准化调用接口

也就是说：

**同一份 RPA 录制产物，可以发布为 Skill，也可以发布为 MCP Tool。**

录制不是 Skill，也不是 MCP Tool。录制只是 authoring mode。

## 为什么这样划分

### 1. 概念边界更清楚

Skill 的重点是：

- instructions
- scripts
- local resources
- workflow reuse

MCP Tool 的重点是：

- stable input schema
- stable output schema
- auth contract
- runtime invocation
- external sharing

两者可以共用底层自动化逻辑，但不应该在产品层被视为同一个对象。

### 2. 更贴近 MCP 的行业语义

MCP 关注的是标准化工具调用接口，而不是录制过程本身。录制更像一种“生成工具实现”的手段，而不是 MCP 领域对象。

因此，RPA 录制页面不适合作为 MCP Tool 的长期主编辑界面；它更适合作为 MCP Tool 的来源入口之一。

### 3. 用户路径更自然

录制完成后，用户最自然的问题是：

- 这个自动化我要保存成内部 Skill 吗？
- 还是要发布成可共享的 MCP Tool？

所以录制完成后提供双发布出口是合理的。

但一旦进入 MCP Tool 的定义阶段，用户关注点会转向：

- name / description
- input schema / output schema
- cookies / auth policy
- preview test
- publish / enable / share

这一阶段应进入独立 Tool 编辑域，而不是继续挤在录制配置页里。

## 推荐信息架构

### 1. RPA 域

`RPA`

- Record
- Sessions / Drafts
- Configure / Test

职责：

- 录制与回放
- 参数抽取
- 登录段清洗
- 脚本测试
- 选择发布形态

### 2. Skills 域

`Skills`

- Skill 列表
- 从录制发布 Skill
- Skill 编辑与管理

职责：

- 管理工作流型能力包
- 面向本地或团队 agent 的复用

### 3. Tools 域

`Tools`

- MCP Tools 列表
- New MCP Tool
- Tool Editor
- Tool Test / Publish / Enable / Disable

职责：

- 管理标准化工具接口
- 面向 Gateway 和外部 agent 调用

## 推荐用户流

### 路径 A：录制驱动发布

1. 用户完成 RPA 录制
2. 在 Configure / Test 阶段验证流程
3. 页面提供两个明确动作：
   - `发布为 Skill`
   - `发布为 MCP Tool`
4. 选择 `发布为 MCP Tool` 后，跳转到独立的 `MCP Tool Editor`
5. 编辑器自动带入当前录制草稿的：
   - steps
   - sanitize report
   - suggested input schema
   - suggested output schema
   - auth policy defaults

这是推荐的主路径。

### 路径 B：工具驱动创建

1. 用户进入 `Tools -> MCP Tools`
2. 点击 `New MCP Tool`
3. 选择来源：
   - From RPA Recording
   - From Existing Skill
   - From API
   - Blank
4. 如果选择 `From RPA Recording`，则选择某个录制草稿进入 `MCP Tool Editor`

这是推荐的管理路径。

## 页面职责边界

### RPA Configure / Test 页面保留的能力

保留：

- 录制步骤查看和编辑
- 参数抽取
- 登录段识别与清洗
- RPA 测试
- 发布出口选择

不建议长期保留：

- 完整 MCP schema 编辑
- 长期工具启停管理
- 发布状态管理
- 调用说明和共享说明

### MCP Tool Editor 负责的能力

- tool metadata
- input schema
- output schema
- auth policy
- preview test
- publish / enable / disable
- integration snippets（后续）

### Skill Editor 负责的能力

- skill metadata
- instructions
- skill assets / scripts
- team reuse

## 入口策略

产品上采用双入口：

### 入口 1：RPA 录制完成后的快捷入口

优点：

- 路径最短
- 符合用户完成录制后的即时意图

要求：

- 这里只提供“去发布”的入口
- 不把完整 MCP 编辑流程永久留在录制页内

### 入口 2：Tools 域里的正式入口

优点：

- 工具管理集中
- 可以从多种来源创建 MCP Tool
- 更符合后续扩展

要求：

- Tools 是 MCP Tool 的长期主入口
- 录制只是其中一种来源

## 数据模型建议

建议将底层自动化草稿与发布产物分离：

### Automation Draft（底层）

表示录制得到的可执行自动化定义，供多个发布出口复用。

字段示例：

- source session
- normalized steps
- params
- sanitize report
- post-auth execution hints

### Skill Publication（发布态）

表示从 draft 派生出的 Skill。

### MCP Tool Publication（发布态）

表示从 draft 派生出的 MCP Tool。

这样可以避免未来出现：

- “改 Skill 是否影响 MCP Tool”
- “改 MCP Tool 的 schema 是否改坏录制草稿”

三者之间的职责污染。

## 对当前实现的指导

当前实现可以继续作为过渡态，但建议演进方向明确为：

1. 保留 RPA 配置页上的 `发布为 MCP Tool` 按钮
2. 逐步把 MCP Tool 的完整编辑体验迁移到独立 Tool Editor
3. Tools 页成为 MCP Tool 的主管理域
4. RPA 页只保留“录制和准备发布”的职责

## 分阶段建议

### Phase 1：入口校正

- RPA Configure 页面保留双按钮：
  - 发布为 Skill
  - 发布为 MCP Tool
- 发布为 MCP Tool 时跳转到独立 Tool 编辑页面，而不是继续在当前页堆叠逻辑

### Phase 2：Tool Studio 成型

- 在 `Tools` 中增加 `MCP Tools` 主入口
- 支持从录制草稿创建
- 支持查看、编辑、测试、启停已发布工具

### Phase 3：模型拆分

- 抽离统一 automation draft
- Skill 和 MCP Tool 都从 draft 派生
- 支持后续更多发布形态

## 最终结论

最终推荐的产品模型是：

**RPA 录制既可以发布为 Skill，也可以发布为 MCP Tool。**

并且：

- `RPA 页面`负责生成和验证自动化草稿
- `Skills 页面`负责管理 Skill 发布物
- `Tools 页面`负责管理 MCP Tool 发布物
- `发布为 MCP Tool` 既可以从录制后快捷进入，也可以从 Tools 页面正式进入

这个模型比“单纯把 MCP 转换堆在录制配置页里”更稳定，也更贴合 Skill 与 MCP Tool 在实际使用中的职责边界。
