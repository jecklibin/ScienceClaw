# RPA AI 录制助手设计文档

## 概述

为 RPA 录制页面的"AI 录制助手"聊天面板实现完整功能。用户可以通过自然语言描述操作意图（如"点击第一个搜索结果"、"获取表单数据"），AI 助手结合历史操作上下文、当前页面可交互元素树和用户描述，生成动态适应的 Playwright 代码片段并在录制浏览器中实时执行。

## 核心需求

1. 用户在录制过程中随时可以通过聊天发送自然语言指令
2. AI 生成的代码必须动态适应（如"点击第一个结果"→ `.first`，而非硬编码具体元素文本）
3. 执行过程在 VNC 中实时可见
4. AI 执行期间暂停事件捕获，只记录 AI 生成的代码本身作为步骤（避免重复且保证可复用性）
5. 最终技能脚本中同时包含录制步骤和 AI 步骤
6. 流式 SSE 输出 AI 响应

## 架构方案：沙箱内 Playwright 直接执行

AI 生成 Playwright 同步代码片段，通过命令文件机制注入到正在运行的录制浏览器进程中执行。

### 数据流

```
用户输入 "点击第一个搜索结果"
        ↓
前端 POST /rpa/session/{id}/chat (SSE)
        ↓
后端 rpa/assistant.py:
  1. sandbox_execute_code → 录制浏览器执行 JS 提取可交互元素树
  2. 构建 prompt: 系统提示 + 历史步骤 + 页面元素树 + 用户消息
  3. 调用 LLM (get_llm_model, streaming) → 流式返回代码片段
  4. 执行前: page.evaluate("window.__rpa_paused = true") 暂停事件捕获
  5. 写入 /tmp/rpa_command.py → 录制浏览器轮询检测并执行
  6. 执行后: page.evaluate("window.__rpa_paused = false") 恢复捕获
  7. 后端将 AI 代码片段作为 ai_script 步骤直接插入 session.steps
        ↓
SSE 流式返回: thinking → script → executing → result/error → done
```

### 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| RPAAssistant | `rpa/assistant.py`（新增） | 对话管理、prompt 构建、LLM 调用、命令执行 |
| Chat 端点 | `route/rpa.py`（修改） | SSE 流式端点 |
| 命令执行器 | `rpa/manager.py`（修改） | BROWSER_SCRIPT 中命令文件轮询 |
| 事件暂停 | `rpa/manager.py`（修改） | CAPTURE_JS 中 `__rpa_paused` 检查 |
| 元素树提取 | `rpa/manager.py`（修改） | BROWSER_SCRIPT 中 expose_function |
| 前端聊天 | `RecorderPage.vue`（修改） | SSE 接入、消息展示 |
| 脚本生成 | `rpa/generator.py`（修改） | ai_script 步骤处理 |

## Section 1：页面元素树提取

### 提取的元素类型

button, a, input, textarea, select, `[role=button]`, `[role=link]`, `[role=menuitem]`, `[role=tab]`, `[role=checkbox]`, `[role=radio]`, `[contenteditable]`

### 每个元素提取的信息

```json
{
  "index": 1,
  "tag": "a",
  "role": "link",
  "name": "Playwright documentation",
  "text": "Playwright documentation",
  "href": "https://playwright.dev/docs",
  "placeholder": null,
  "value": null,
  "checked": null,
  "disabled": false,
  "visible": true
}
```

### 设计决策

- 每个元素分配 `index` 编号，LLM 可通过编号引用，但生成的代码使用语义化 locator
- 只提取可见且非禁用的元素
- 列表/表格中的重复结构只展示前 3 项 + 总数提示，避免 token 爆炸
- 复用 CAPTURE_JS 中的 `generateLocator()` 生成 locator 格式

### 实现方式

通过命令文件机制实现。后端写入一个特殊的元素提取命令到 `/tmp/rpa_command.py`，其 `run(page)` 函数执行 `page.evaluate(EXTRACT_JS)` 提取元素树，将结果写入 `/tmp/rpa_command_result.json`。与普通命令执行共用同一套文件轮询机制。

## Section 2：命令执行机制

### 命令文件轮询

BROWSER_SCRIPT 主循环中每 500ms 检查 `/tmp/rpa_command.py`：

1. 检测到文件 → 读取内容 → 删除文件（防重复）
2. 执行前：`page.evaluate("window.__rpa_paused = true")`
3. `exec()` 执行代码，代码中定义 `def run(page):` 函数
4. 调用 `run(page)` 获取结果
5. 执行后：`page.evaluate("window.__rpa_paused = false")`
6. 结果写入 `/tmp/rpa_command_result.json`

### 命令文件格式

`/tmp/rpa_command.py`：
```python
def run(page):
    first_result = page.locator("h3").first
    first_result.click()
    page.wait_for_timeout(500)
```

注意：使用 Playwright 同步 API（BROWSER_SCRIPT 基于 `sync_playwright`）。

### 结果文件格式

`/tmp/rpa_command_result.json`：
```json
{"success": true, "output": "ok", "error": null}
```
或：
```json
{"success": false, "output": "", "error": "TimeoutError: locator.click: Timeout 15000ms exceeded"}
```

### 事件捕获暂停

CAPTURE_JS 中所有事件监听器开头检查 `window.__rpa_paused`：

```javascript
function handleClick(e) {
    if (window.__rpa_paused) return;
    // ... 原有逻辑
}
```

BROWSER_SCRIPT 在执行命令前后切换该标志。这样 AI 脚本执行产生的 DOM 事件不会被录制为步骤，避免重复。

### 后端轮询

后端写入命令文件后，通过 `sandbox_execute_bash` 每 500ms 轮询 `/tmp/rpa_command_result.json`，最长等待 30s。

## Section 3：LLM Prompt 设计

### System Prompt

```
你是一个 RPA 录制助手。用户正在录制浏览器自动化技能，你需要根据用户的自然语言描述，
结合当前页面状态和历史操作，生成 Playwright 同步 API 代码片段。

规则：
1. 生成的代码必须使用 Playwright 同步 API（page.locator().click()，不是 await）
2. 代码必须定义 def run(page): 函数
3. 使用动态适应的选择器：
   - "点击第一个搜索结果" → page.locator(".result-item").first.click()
   - "获取表格数据" → page.locator("table").first.inner_text()
   - 不要硬编码具体文本内容，用位置/结构选择
4. 操作之间加 page.wait_for_timeout(500) 等待 UI 响应
5. 如果操作可能触发导航，在 click 后加 page.wait_for_load_state("load")
6. 用 ```python 代码块包裹代码
7. 代码之外可以附带简短说明
```

### 每次请求的上下文结构

```
[System Prompt]

## 历史操作步骤
1. [record] 导航到 https://github.com
2. [record] 在搜索框中输入 "playwright"
3. [record] 按下 Enter

## 当前页面信息
URL: https://github.com/search?q=playwright
可交互元素:
[1] link "microsoft/playwright" href="/microsoft/playwright"
[2] link "playwright-community/..." href="/playwright-community/..."
[3] button "Sort by"
[4] input[type=text] placeholder="Search"
...（共 47 个可交互元素）

## 用户指令
点击第一个搜索结果
```

### 对话历史管理

- 每个 RPA session 维护独立的 `chat_history: List[{role, content}]`
- 只保留最近 10 轮对话
- 历史步骤和页面元素树每次实时获取（页面状态在变）

### 错误重试

执行失败时，自动将错误信息追加到对话，让 LLM 生成修正代码，最多重试 1 次。仍失败则展示错误给用户。

## Section 4：步骤数据结构扩展

### 普通录制步骤（现有）

```python
{
    "action": "click",
    "target": "{\"method\":\"role\",\"role\":\"button\",\"name\":\"Search\"}",
    "source": "record",
    "description": "点击 Search 按钮"
}
```

### AI 生成步骤（新增）

```python
{
    "action": "ai_script",
    "source": "ai",
    "value": "first_result = page.locator('h3').first\nfirst_result.click()\npage.wait_for_timeout(500)",
    "description": "点击第一个搜索结果",
    "prompt": "点击第一个搜索结果"
}
```

- `action: "ai_script"` 标识这是 AI 生成的步骤
- `source: "ai"` 区分来源
- `value` 存储 Playwright 同步代码（`run(page)` 函数体）
- `description` 为用户原始指令
- `prompt` 保留原始指令用于回溯

## Section 5：前端交互与 SSE 协议

### SSE 事件类型

| 事件 | 数据 | 前端处理 |
|------|------|---------|
| `message_chunk` | `{"text": "分析页面..."}` | 追加到 assistant 消息的 text |
| `script` | `{"code": "def run(page):..."}` | 设置 script 字段，折叠展示代码 |
| `executing` | `{}` | status → executing，显示加载动画 |
| `result` | `{"success": true, "step": {...}}` | status → success/error，步骤列表新增 AI 步骤 |
| `error` | `{"message": "Timeout..."}` | 显示错误 |
| `done` | `{}` | 流结束 |

### 聊天消息类型

```typescript
interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  script?: string        // AI 生成的代码片段（折叠展示）
  status?: 'thinking' | 'executing' | 'success' | 'error'
  error?: string
  time: string
}
```

### 用户体验流程

1. 用户输入指令，发送按钮禁用
2. assistant 气泡出现，状态 thinking，文字流式出现
3. 代码块折叠展示
4. 状态切换 executing，VNC 中可见操作
5. 状态切换 success，左侧步骤列表新增 AI 步骤（紫色圆点 + AI 图标）
6. 发送按钮恢复

### 步骤列表中 AI 步骤展示

- 普通步骤：蓝色圆点 + "点击 xxx"
- AI 步骤：紫色圆点 + AI 图标 + 用户原始指令作为描述

## Section 6：generator.py 适配

### 录制步骤（source: "record"）

走现有逻辑：locator JSON → Playwright async API 调用。

### AI 步骤（source: "ai"）

将 `value` 中的同步代码转换为异步代码后嵌入。

转换规则（`_sync_to_async(code)` 方法）：
- 所有 `page.` 开头的调用链前加 `await`
- 去掉 `def run(page):` 函数签名，只取函数体

### 生成的脚本示例

```python
async def execute_skill(page, **kwargs):
    # Step 1: [录制] 导航到 GitHub
    await page.goto("https://github.com")
    await page.wait_for_load_state("load")

    # Step 2: [录制] 搜索框输入
    await page.get_by_placeholder("Search", exact=True).fill("playwright")

    # Step 3: [AI] 点击第一个搜索结果
    first_result = page.locator("h3").first
    await first_result.click()
    await page.wait_for_timeout(500)

    # Step 4: [录制] 点击 Star 按钮
    await page.get_by_role("button", name="Star", exact=True).click()
    await page.wait_for_timeout(500)
```

## Section 7：后端新增文件与端点

### 新增 `rpa/assistant.py`

```python
class RPAAssistant:
    def __init__(self, session_id: str, sandbox_url: str):
        self.session_id = session_id
        self.sandbox_url = sandbox_url
        self.chat_history = []  # 最近 10 轮

    async def chat(self, message: str, steps: list) -> AsyncGenerator:
        """流式处理用户消息，yield SSE 事件"""
        # 1. 获取页面元素树
        # 2. 构建 prompt
        # 3. 调用 LLM 流式生成
        # 4. 提取代码
        # 5. 暂停捕获 + 执行 + 恢复捕获
        # 6. 失败则重试一次
        # 7. 将 AI 步骤插入 session.steps

    async def _get_page_elements(self) -> str:
        """通过命令文件在录制浏览器中执行 JS 提取可交互元素树"""

    async def _execute_command(self, code: str) -> dict:
        """写入命令文件，轮询结果"""

    async def _call_llm(self, messages) -> AsyncGenerator:
        """复用 get_llm_model() 流式调用"""
```

### route/rpa.py 新增端点

```python
@router.post("/session/{session_id}/chat")
async def chat_with_assistant(session_id: str, body: ChatRequest):
    """SSE 流式端点"""
    return EventSourceResponse(assistant.chat(body.message, session.steps))
```

### BROWSER_SCRIPT 修改

1. 主循环中加入命令文件轮询（每 500ms）
2. 元素树提取通过命令文件机制（与普通命令共用）
3. 执行前后设置 `window.__rpa_paused`

### 改动文件清单

| 文件 | 改动类型 | 内容 |
|------|---------|------|
| `rpa/assistant.py` | 新增 | AI 助手核心类 |
| `rpa/manager.py` | 修改 | BROWSER_SCRIPT 命令轮询 + CAPTURE_JS 暂停机制 |
| `rpa/generator.py` | 修改 | ai_script 步骤处理 + sync→async 转换 |
| `route/rpa.py` | 修改 | 新增 chat SSE 端点 |
| `RecorderPage.vue` | 修改 | sendMessage 接入 SSE |

## 错误处理

- LLM 调用失败：返回 error 事件，提示用户重试
- 命令执行超时（30s）：返回 timeout 错误
- 命令执行失败：自动重试一次（带错误上下文），仍失败则返回错误
- 页面元素树提取失败：使用空元素树继续（LLM 仍可根据历史步骤推断）
- 录制浏览器进程不存在：返回错误提示用户重新开始录制
