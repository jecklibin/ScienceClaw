# 对话式 Recording Segment 输入输出映射设计

## 文档状态

- 状态：设计稿
- 语言：中文
- 适用范围：主对话中的多段 recording / workflow segment 输入输出关联配置
- 关联能力：`recording-creator`、聊天页 segment 卡片、发布前 workflow draft

## 背景

当前多段录制已经支持：

- 一个会话里录制多个 `segment`
- `segment` 之间存在输入输出关系
- 通过 `bind_recording_segment_io` 建立结构化绑定
- 通过整体测试执行多段 workflow

但用户体验仍存在明显问题：

- 关联关系主要依赖对话表达，例如“把第一段输出作为第二段输入”
- 聊天页上难以直接看出某个 `segment` 的输入是否已绑定、绑定到了哪里
- 绑定后的结果缺少清晰的回看与修改入口
- 用户只能“通过语言驱动配置”，而不能“通过界面理解并编辑配置”

这会使多段场景变得脆弱，尤其是在以下情境：

- 多个历史 `segment` 都输出了相似字段
- 同一会话里存在多个 artifact
- 用户需要反复调整输入来源
- 用户准备发布前需要确认 workflow 的真实依赖关系

## 目标

本设计要解决的问题是：在不把主聊天页做成复杂工作流编辑器的前提下，为 `segment` 之间的输入输出关系提供可视、低摩擦、可回看、可编辑的配置体验。

具体目标如下：

- 用户可以直接在聊天页看见一个 `segment` 的输入、输出、已绑定状态
- 用户可以不用继续靠自然语言，就完成大多数常见绑定
- 用户可以在复杂场景下进入更完整的映射编辑界面
- 支持绑定任意历史 `segment` 输出或任意历史 `artifact`，不局限于上一段
- 绑定关系要能自然衔接测试、发布草稿和最终 skill/tool 执行

## 非目标

本期不做以下内容：

- DAG 画布
- 可视化拖线编辑器
- 条件分支 / 循环映射
- 一个输入绑定多个来源的合并表达式
- 在聊天页里直接展示全局流程图

本期只聚焦于“顺序多段 workflow 的输入输出映射”。

## 设计结论

采用 **Hybrid Mapping** 方案：

1. 聊天页 `segment` 卡片内提供快速绑定能力，覆盖 80% 常见场景
2. 同时提供一个 `I/O Mapping Drawer`，用于复杂关系的完整编辑
3. 聊天页显示“关系摘要”，但不做复杂的可视化连线

这是在以下三种方案里最平衡的方案：

### 方案 A：Inline First

所有配置都在聊天页卡片内完成。

优点：

- 入口最短
- 用户不用切换上下文

缺点：

- 一旦多段变多，聊天页会快速膨胀
- 不适合复杂来源选择、过滤和回溯

### 方案 B：Drawer First

聊天页只展示摘要，所有映射都必须进入抽屉。

优点：

- 主聊天页更干净
- 模型边界更清楚

缺点：

- 简单场景也要多一步
- 频繁编辑时操作成本偏高

### 方案 C：Hybrid Mapping

聊天页卡片内解决高频简单操作，复杂情况进入抽屉。

优点：

- 对常见路径更快
- 对复杂路径仍保留足够表达力
- 不会把聊天页演变成半个 workflow 画布

缺点：

- 需要维护两层交互，但两层职责清楚，可控

## 交互设计

### 一、聊天页卡片摘要层

每个 `segment` 卡片头部新增一行结构化摘要：

- `步骤数`
- `输入数`
- `输出数`
- `已绑定数`
- `待绑定数`

示例：

```text
5 步 · 2 输入 · 1 输出 · 已绑定 1 · 待绑定 1
```

如果存在关键绑定关系，在头部或副标题位置显示 1~2 条高信号摘要：

```text
search <- 获取项目名称.project_name
```

如果所有输入都已绑定，显示绿色状态；如果存在未绑定输入，显示橙色状态。

### 二、卡片内快速绑定层

展开 `segment` 卡片后，增加一个 `输入 / 输出` 模块。

#### 输入区

每个输入项显示：

- 输入名
- 类型
- 来源状态
- 来源摘要
- 操作按钮

示例：

```text
search  string  已绑定
来源：获取项目名称.project_name
[更换来源] [解绑]
```

未绑定时：

```text
search  string  待绑定
[选择来源]
```

#### 输出区

每个输出项显示：

- 输出名
- 类型
- 预览摘要
- 被哪些后续段引用

示例：

```text
project_name  string
已被：搜索项目名称.search
```

### 三、轻量来源选择器

点击 `选择来源` / `更换来源` 时，弹出一个轻量来源选择器，不依赖继续输入对话。

来源选择器分四组：

1. `推荐来源`
2. `历史 Segment 输出`
3. `历史 Artifact`
4. `手动参数`

#### 推荐来源

优先显示最可能的候选：

- 最近一个已完成 `segment` 的输出
- 最近生成且类型匹配的 artifact
- 与输入名或描述语义接近的来源

这里是“推荐”，不是自动绑定。用户需要显式点击确认。

#### 历史 Segment 输出

按 `segment` 分组显示：

- `segment` 标题
- 输出变量名
- 类型
- 值预览

示例：

```text
获取项目名称
- project_name  string  "OpenAI / openai-python"
```

#### 历史 Artifact

按 artifact 类型分组显示：

- 文件：文件名、扩展名、路径片段
- 文本：首行或摘要
- JSON：键名摘要

#### 手动参数

允许用户把某个输入标记为 workflow 级用户参数，而不是绑定到历史输出。

例如：

```text
将 search 暴露为用户输入参数
```

### 四、I/O Mapping Drawer

对于以下情况，用户进入右侧抽屉做完整编辑：

- 一个 `segment` 有多个输入需要同时处理
- 需要跨很早的历史段取值
- 来源很多，需要搜索和筛选
- 用户要决定某个输入应暴露为最终 workflow 参数
- 用户要系统性检查该段全部绑定关系

抽屉建议分为三栏：

#### 左栏：当前 Segment 输入列表

显示：

- 输入名
- 类型
- 当前状态
- 是否必填

点击某一输入后，中栏切换到对应的来源浏览状态。

#### 中栏：来源池浏览器

支持：

- 按来源类型筛选：`segment output / artifact / workflow param`
- 按 `segment` 标题搜索
- 按变量名 / 文件名 / artifact 名搜索
- 按类型过滤：`string / file / json / secret`

#### 右栏：映射结果预览

显示当前输入的最终结构化结果：

- `input_name`
- `type`
- `source`
- `source_ref`
- 来源预览
- 类型校验结果

并提供：

- 保存
- 解绑
- 改为 workflow 参数

## 映射规则

### 一、允许的来源

每个输入可以绑定到以下四类来源之一：

- `segment_output`
- `artifact`
- `workflow_param`
- `credential`

本期不支持一个输入绑定多个来源，也不支持表达式拼接。

### 二、允许绑定任意历史来源

默认允许绑定：

- 任意历史 `segment` 的输出
- 任意历史 `artifact`

但 UI 上优先推荐：

- 最近一段的输出
- 最近生成的同类型 artifact

### 三、类型匹配规则

绑定时必须校验类型兼容性：

- `file` 只能绑定 `file`
- `string` 可绑定 `string`
- `json` 只能绑定 `json`
- `secret` 只能绑定 `credential` 或 workflow secret param

类型不匹配时，不允许静默提交，应即时给出错误提示。

### 四、推荐而不自动提交

系统可以推荐候选来源，但不应完全自动绑定。

原因：

- 用户对自动推断的信任成本高
- 多段场景里误绑定的代价大
- 推荐 + 一次点击确认的 UX 更稳

## 数据模型设计

### Segment Input

沿用当前结构，并补充前端展示需要的解释字段：

```ts
type WorkflowIO = {
  name: string
  type: 'string' | 'number' | 'boolean' | 'file' | 'json' | 'secret'
  required?: boolean
  source?: 'user' | 'workflow_param' | 'segment_output' | 'artifact' | 'credential'
  source_ref?: string | null
  description?: string
  default?: unknown
}
```

### 来源池视图模型

前端增加统一来源池视图模型：

```ts
type MappingSourceOption = {
  id: string
  sourceType: 'segment_output' | 'artifact' | 'workflow_param'
  sourceRef: string
  segmentId?: string
  segmentTitle?: string
  name: string
  valueType: 'string' | 'number' | 'boolean' | 'file' | 'json' | 'secret'
  preview?: string
  recommended?: boolean
}
```

这样聊天页卡片和抽屉可以共用同一份来源池数据，而不是各自拼装。

## 前后端边界

### 前端负责

- 渲染 `segment` 输入输出摘要
- 生成来源池选择 UI
- 执行即时绑定 / 解绑交互
- 在卡片和抽屉之间共享映射状态

### 后端负责

- 持久化 `inputs` / `outputs`
- 接收绑定更新
- 在测试、发布、执行阶段消费 `source_ref`
- 返回足够完整的历史 `segment` 输出和 artifact 信息，供前端构建来源池

### recording-creator 的角色

`recording-creator` 仍然保留以下职责：

- 当用户通过自然语言表达绑定需求时，仍可用工具建立关系
- 在用户没有打开 UI 进行显式编辑时，继续作为语言入口
- 在发布前或测试失败时，提示用户检查映射关系

但“绑定配置”不再只依赖对话，是 UI 与技能协同。

## 与发布流程的关系

发布前的 `Skill Publish Draft` 中，应复用这些映射结果：

- 哪些输入已经来自上游 `segment`
- 哪些输入被暴露为 workflow 参数
- 哪些输入仍未绑定，应产生 warning

发布页中不需要重新发明一套独立映射逻辑，而应复用同一结构化数据。

## 状态提示设计

为了让用户在聊天页中快速理解映射是否健康，每个 `segment` 卡片应支持以下状态：

- `全部已绑定`
- `部分已绑定`
- `待绑定`
- `类型冲突`
- `来源失效`

其中：

- `类型冲突`：来源类型不匹配
- `来源失效`：引用的 segment 输出或 artifact 已不存在

这些状态应直接影响：

- 卡片角标颜色
- 是否允许开始整体测试
- 发布草稿中的 warning

## 推荐实施顺序

1. 为 `segment` 卡片增加输入输出摘要与绑定状态展示
2. 增加轻量来源选择器，支持快速绑定 / 更换 / 解绑
3. 抽象统一来源池数据结构
4. 增加 `I/O Mapping Drawer`
5. 在发布草稿里接入映射 warning 和 workflow param 暴露逻辑

## 验收标准

满足以下场景可视为通过：

1. 用户录制两段 workflow
2. 第二段存在一个未绑定输入
3. 用户无需继续通过聊天表达，在卡片内点击 `选择来源`
4. 用户可以从任意历史 `segment` 输出或 artifact 中选择一个来源
5. 绑定后卡片立即显示关系摘要
6. 用户进入抽屉后可以看见所有输入及当前映射
7. 用户修改映射后，整体测试和发布都使用最新绑定关系
8. 未绑定输入在聊天页和发布页都能被明确提示

## 结论

多段 `segment` 的输入输出关联，不应继续只靠对话表达，也不应直接升级成复杂流程画布。

最佳路径是：

- 聊天页卡片承担“看见关系、快速改关系”
- 抽屉承担“完整编辑关系”
- `recording-creator` 继续承担语言编排与兜底入口

这样既保留主对话作为主舞台，又让多段 workflow 的依赖关系真正变成可视、可编辑、可验证的产品能力。
