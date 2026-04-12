# AI 录制助手 Snapshot V2 设计文档

## 1. 背景与问题

当前 RPA 录制系统里，手动录制与 AI 录制助手走的是两条质量明显不同的数据链路：

- 手动录制使用浏览器侧 vendored Playwright recorder/runtime，能够生成较准确的 locator candidates，并且在回放时大多数可以正常命中。
- AI 录制助手使用 [assistant_runtime.py](/D:/code/MyScienceClaw/.worktrees/fix-ai-command/RpaClaw/backend/rpa/assistant_runtime.py) 中的简化页面快照与简化 locator 构造逻辑，在复杂页面上容易把目标理解错或定位错。

现有 AI 快照方案存在几个根本问题：

1. 页面抽象过粗  
   当前 `EXTRACT_ELEMENTS_JS` 只抓少量交互元素，且字段非常有限，复杂组件、自定义控件、表格内重复操作区、卡片式布局都容易信息不足。

2. locator 真值与手动录制不一致  
   AI 侧仍在使用 `_build_locator_candidates_for_element()` 这套简化候选逻辑，而不是复用手动录制已经对齐 Playwright 的 locator bundle。结果是 AI 选中的 target 与最终回放语义经常不一致。

3. 缺少空间信息  
   目前 AI 快照没有系统性提供可操作元素的 `bbox`、中心点、可见性、命中测试结果。对于“点击第一个文件下载”“右上角操作按钮”“这一行的编辑”这类复杂指令，模型很难可靠 disambiguate。

4. 操作与提取共用一份低质量快照  
   数据提取类指令和交互类指令需要的信息不同。现在用同一份简化元素列表同时服务 `click/fill/press` 和 `extract_text`，两边都不理想。

5. 缺少逐层收敛机制  
   AI 现在基本是在“全页平铺元素列表”里直接猜目标。复杂页面里同类元素很多时，没有“先选容器，再选局部节点”的过程，因此命中率不稳定。

## 2. 设计目标

本次设计目标如下：

1. 统一 AI 录制助手与手动录制的 locator 真值来源  
   AI 侧不再维护第二套简化 locator 语义，尽量复用当前已 vendored 的 Playwright locator/runtime。

2. 提升复杂页面操作的命中率  
   尤其针对表格、列表、树形控件、卡片区、工具栏、表单分区等重复结构页面。

3. 同时兼顾数据提取场景  
   不只优化 `click/fill/press/select`，还要让 `extract_text` 能稳定命中标题、单元格、标签值对等非纯交互节点。

4. 支持“局部二次展开”  
   当 AI 第一轮判断不确定时，允许多花约 `200ms-800ms` 做一次局部细化，以换取更高准确率。

5. 保持回放稳定性  
   坐标信息只能作为辅助 disambiguation 信号，不能替代 locator 成为最终回放真值。

## 3. 非目标

本次设计不包括以下内容：

1. 不改造现有 RecorderPage/TestPage 的整体交互结构
2. 不引入纯视觉坐标回放作为主执行路径
3. 不把整个 AI 录制助手重写成独立服务
4. 不要求一次性替换所有现有 assistant runtime 逻辑，只聚焦页面抽象、定位决策与局部展开机制

## 4. 方案比较

### 方案 A：单次全页 Snapshot V2

一次性抓取全页更丰富的节点信息，包括可操作节点、可读内容节点、容器信息、bbox 和 locator bundle，然后由 AI 直接在全页做决策。

优点：

- 实现直观
- 一次请求即可完成大多数简单页面场景
- 便于替换当前简化 `EXTRACT_ELEMENTS_JS`

缺点：

- 复杂页面中同类元素依然很多
- 表格/列表/卡片区中的相对描述仍然难以稳定命中
- token 噪音和模型负担较大

### 方案 B：单次全页 Snapshot V2 + 不确定时局部二次展开

先抓取全页 Snapshot V2；若第一轮匹配不确定，则对高分候选容器做局部细化，返回更细粒度的操作节点和内容节点，再做第二轮判定。

优点：

- 在复杂页面中显著提高准确率
- 延迟仍可控，因为局部展开只针对一两个容器，不重新扫描全页
- 同时适用于操作类和提取类指令
- 与当前系统结构兼容性最好

缺点：

- 需要额外设计“不确定”的判定规则
- assistant runtime 需要维护容器级状态与二次查询接口

### 方案 C：视觉/坐标优先

给 AI 更多截图、坐标和可点击区域，让模型按视觉位置做点击和提取。

优点：

- 对人类语言中的“左上角”“右边第一个”这类描述较直观

缺点：

- 回放稳定性差
- 布局变化、缩放、滚动会降低鲁棒性
- 对数据提取的帮助有限
- 会与现有 locator-based 执行体系冲突

### 选型结论

选择 **方案 B：单次全页 Snapshot V2 + 不确定时局部二次展开**。

原因：

- 它能最大化复用当前系统的 locator-based 回放优势；
- 能同时解决复杂操作页面和数据提取页面的理解问题；
- 用户已接受为复杂页面多花 `200ms-800ms` 做局部展开；
- 相比纯视觉或纯单次全页方案，它在准确率与复杂度之间最平衡。

## 5. 总体架构

### 5.1 快照分层

Snapshot V2 不再是一份简单的“元素列表”，而拆成三类结构：

1. `actionable_nodes`  
   面向 `click / fill / press / select` 等交互操作。

2. `content_nodes`  
   面向 `extract_text` 等数据提取操作。

3. `containers`  
   面向复杂页面的局部理解与二次展开。

### 5.2 总体流程

1. AI 执行前先获取全页 `snapshot_v2`
2. 根据意图类型选择主要候选池
   - 操作类优先 `actionable_nodes`
   - 提取类优先 `content_nodes`
3. 第一轮匹配打分
4. 若命中足够明确，直接执行
5. 若不确定，则对高分 `container` 做局部二次展开
6. 在局部结果中重新打分并执行
7. 最终记录 step 时，使用真实 locator bundle 作为 target/candidates/validation

## 6. Snapshot V2 数据结构

### 6.1 顶层结构

顶层建议包含：

- `url`
- `title`
- `viewport`
- `frames`
- `actionable_nodes`
- `content_nodes`
- `containers`

### 6.2 actionable_nodes

每个可操作节点至少包含：

- `node_id`
- `frame_path`
- `container_id`
- `tag`
- `role`
- `name`
- `text`
- `type`
- `placeholder`
- `title`
- `bbox`
- `center_point`
- `is_visible`
- `is_enabled`
- `hit_test_ok`
- `action_kinds`
- `locator`
- `locator_candidates`
- `validation`
- `element_snapshot`

其中：

- `locator / locator_candidates / validation` 必须复用当前手动录制已 vendored 的 Playwright locator runtime，而不是继续使用 `_build_locator_candidates_for_element()` 的简化逻辑。
- `hit_test_ok` 用于判断节点中心点是否真的命中该节点或其合理交互祖先。
- `action_kinds` 用于告诉 AI 该节点适合哪些动作，例如 `["click"]`、`["click","fill"]`、`["press"]`。

### 6.3 content_nodes

每个可提取节点至少包含：

- `node_id`
- `frame_path`
- `container_id`
- `semantic_kind`
- `role`
- `text`
- `bbox`
- `locator`
- `element_snapshot`

`content_nodes` 不要求每个节点都可交互，但要适合提取：

- 标题
- 表格单元格
- 列表项标题
- 标签/值对
- 摘要段落
- 卡片主文本

### 6.4 containers

每个容器至少包含：

- `container_id`
- `frame_path`
- `container_kind`
- `name`
- `bbox`
- `summary`
- `child_actionable_ids`
- `child_content_ids`

容器类型包括但不限于：

- `table`
- `grid`
- `list`
- `tree`
- `card_group`
- `toolbar`
- `form_section`

容器的作用不是直接执行，而是为复杂页面提供“先缩小范围，再细选目标”的锚点。

## 7. Locator 真值统一

这是本次设计的核心约束。

### 7.1 当前问题

AI 侧在 [assistant_runtime.py](/D:/code/MyScienceClaw/.worktrees/fix-ai-command/RpaClaw/backend/rpa/assistant_runtime.py) 中使用 `_build_locator_candidates_for_element()` 基于 `role/name/placeholder/href/tag` 生成简化候选。它与手动录制使用的 vendored Playwright runtime 语义不一致，因此：

- AI 选中的 target 未必是回放最稳的 locator
- AI step 的 `locator_candidates` 没有真实 strict 信息
- configure/test 页面上 AI step 和手动 step 的可靠性差距很大

### 7.2 设计要求

Snapshot V2 中的 `actionable_nodes` 必须直接复用手动录制当前使用的浏览器侧 locator bundle 构造逻辑。  
也就是说，AI 看到的 locator 真值要和手动录制一致：

- 相同页面、相同目标，应生成相同或等价的 locator candidate 集合
- candidate 的 strict/selected/validation 语义也必须一致

这样 AI 与手动录制最终都基于同一套 Playwright 语义做定位决策。

## 8. 不确定性判定

局部二次展开不能靠模型“感觉不确定”，而要靠显式规则触发。

满足任一条件即可触发：

1. top1 与 top2 候选分差过小
2. 候选位于复杂容器中，如 `table/list/tree/grid/card_group/toolbar/form_section`
3. 指令包含相对描述，如“第一个”“最后一个”“这一行”“右边那个”“标题旁边”
4. 候选节点缺少高质量 locator 或 `hit_test_ok == false`
5. 用户意图与候选语义不一致，例如：
   - 用户要“点击下载按钮”，候选更像纯文本
   - 用户要“提取标题”，候选更像操作按钮

## 9. 局部二次展开

### 9.1 输入

局部展开接口输入至少包括：

- `container_id`
- `intent_type`（操作类/提取类）
- 用户指令中的约束信息，例如：
  - 目标动作
  - ordinal（first/last/nth）
  - 关键词
  - 相对位置描述

### 9.2 输出

局部展开返回：

- 该容器内部更细粒度的 `actionable_nodes`
- 该容器内部更细粒度的 `content_nodes`
- 若容器具备结构信息，则附加：
  - `row_index`
  - `column_index`
  - `card_index`
  - `label_anchor`
  - `header_text`

### 9.3 行为原则

1. 不重新抓全页  
   只对单个或极少数高分容器展开。

2. 仍以 locator 为最终执行真值  
   bbox 和坐标只作为辅助 disambiguation 信号。

3. 可同时服务操作与提取  
   对于表格容器，局部展开既要能找到“这一行的下载按钮”，也要能找到“这一行的标题文本”。

## 10. AI 侧决策流程

### 10.1 第一轮

AI 接收 `snapshot_v2` 后：

1. 根据 `action` 判断是操作类还是提取类
2. 在对应节点池中做第一轮候选打分
3. 打分依据至少包括：
   - 文字匹配
   - role/placeholder/title 匹配
   - 容器语义匹配
   - locator 质量
   - visibility / hit test
   - ordinal / relative constraints

### 10.2 第二轮

若触发不确定条件：

1. 选择 top 容器
2. 调用局部二次展开
3. 在局部节点上重新打分
4. 选择最终目标

### 10.3 Step 记录

最终生成 step 时：

- `target` 来自真实 locator
- `locator_candidates` 来自真实 locator bundle
- `validation` 保留真实 strict 结果
- `assistant_diagnostics` 记录：
  - 是否走了局部展开
  - 选择了哪个容器
  - 第一轮与第二轮的候选分差

## 11. 对数据提取场景的兼容

数据提取类指令不能继续只依赖交互节点。

设计要求：

1. `extract_text` 优先匹配 `content_nodes`
2. 只有当目标明显是交互控件本身的文本时，才回退到 `actionable_nodes`
3. 对于表格/列表/卡片，局部二次展开要返回结构化文本节点，而不是只返回按钮和链接

这样才能支持诸如：

- “提取第一个 issue 的标题”
- “读取这一行状态列的文本”
- “获取卡片里的摘要”

## 12. 与现有代码的对应关系

### 12.1 需要替换/升级的部分

- [assistant_runtime.py](/D:/code/MyScienceClaw/.worktrees/fix-ai-command/RpaClaw/backend/rpa/assistant_runtime.py)
  - 替换现有 `EXTRACT_ELEMENTS_JS`
  - 替换简化 locator candidate 构造
  - 新增 `snapshot_v2`
  - 新增局部二次展开入口

- [assistant.py](/D:/code/MyScienceClaw/.worktrees/fix-ai-command/RpaClaw/backend/rpa/assistant.py)
  - 让 AI 结构化动作解析消费新的 snapshot 与 diagnostics

- [generator.py](/D:/code/MyScienceClaw/.worktrees/fix-ai-command/RpaClaw/backend/rpa/generator.py)
  - 原则上不需要大改，只要继续消费更真实的 AI step target/candidates 即可

### 12.2 可复用的部分

- 当前手动录制使用的 vendored Playwright locator runtime
- 现有 `frame_path` 处理逻辑
- 现有 `collection_item` 与 `ordinal` 结构，可继续作为局部结构化目标的一部分

## 13. 风险与缓解

### 风险 1：快照体积变大

全页 `snapshot_v2` 可能显著大于当前元素列表。

缓解：

- 对节点数量设上限
- 对文本长度做截断
- 对重复节点做合理聚合
- 不在第一轮返回过细的行列内部全部节点

### 风险 2：局部展开增加延迟

复杂页面的 AI 操作将比现在多一次局部查询。

缓解：

- 只在显式不确定条件下触发
- 一次只展开 top 容器
- 结果中尽量返回结构化而非冗余文本

### 风险 3：AI 与手动录制数据结构逐步分叉

如果 Snapshot V2 再维护第三套 locator/节点语义，问题会重演。

缓解：

- 严格规定 locator 真值复用手动录制 runtime
- 节点结构只做 AI 理解增强，不重新定义 locator 语义

## 14. 实施分期建议

### 第一期

先做最小可落地版本：

1. 引入 `snapshot_v2`
2. `actionable_nodes` 改为复用真实 locator bundle
3. 增加 `bbox/is_visible/is_enabled/hit_test_ok/container_id`
4. 新增 `content_nodes`
5. 新增局部二次展开接口
6. AI 在 `click/fill/extract_text` 上接入新快照

### 第二期

进一步增强：

1. 更准确的容器识别
2. 行/列/卡片层级结构抽取
3. 更强的相对描述解析
4. 更稳定的不确定性评分模型

## 15. 验收标准

满足以下条件即认为设计达标：

1. AI 在复杂表格/列表页面中，执行“点击第一个文件下载”“点击这一行的操作按钮”命中率明显提升
2. AI step 的 locator candidates 与手动录制语义一致
3. `extract_text` 不再局限于交互元素，能稳定提取标题、单元格和标签值对
4. 局部二次展开只针对目标容器触发，不重新抓全页
5. 额外延迟控制在用户可接受范围内

## 16. 结论

本次设计的核心不是“再给 AI 更多元素”，而是把 AI 录制助手从“全页粗糙元素列表 + 简化 locator”升级为：

- **统一 locator 真值**
- **区分操作节点与内容节点**
- **引入容器级理解**
- **在不确定时做局部二次展开**

这样既能提升复杂页面交互命中率，也能兼顾数据提取类指令，并且不会破坏现有 locator-based 回放稳定性。
