# RPA 数据提取结果自动收集与返回

## Overview

AI 录制助手生成的数据提取代码（如 `title = await page.locator("h1").inner_text()`）在录制时能正确执行，但生成的最终 `skill.py` 中，这些赋值变量在 `execute_skill()` 内部被丢弃——函数没有 return，runner 模板也不捕获返回值。导致技能执行时数据提取操作白做了。

本方案在脚本生成阶段自动检测数据提取赋值语句，将结果收集到 `_results` 字典中，最终 return 并输出。

---

## Step 1: 修改 `generator.py` — 添加 `_results` 收集和返回机制

**File:** `ScienceClaw/backend/rpa/generator.py`

### 改动点

1. **顶部添加 `import re`**

2. **`generate_script()` 方法**：
   - `execute_skill()` 函数开头加 `_results = {}`
   - 函数末尾加 `return _results`
   - `ai_script` 步骤处理时，对转换后的代码调用 `_inject_result_capture()`

3. **新增类属性 `_ACTION_METHODS`**：动作方法黑名单（click/fill/goto 等不产生数据的方法），用于区分数据提取和纯操作

4. **新增类属性 `_ASSIGN_RE`**：匹配 `var = [await] page.xxx(...)` 赋值模式的正则

5. **新增 `_inject_result_capture(cls, code)` 类方法**：
   - 扫描每行代码，匹配 `var = await page.xxx()` 赋值
   - 检查末尾方法是否在 `_ACTION_METHODS` 黑名单中
   - 不在黑名单中的赋值行后插入 `_results["var"] = var`

6. **更新 `RUNNER_TEMPLATE_DOCKER` 和 `RUNNER_TEMPLATE_LOCAL`**：
   - 添加 `import json as _json`
   - 捕获 `execute_skill()` 返回值
   - 有数据时输出 `SKILL_DATA:{json}` 行
   - `SKILL_SUCCESS` 保持不变确保向后兼容

### 输出格式

```
SKILL_DATA:{"title":"Hello World","rows":["row1","row2"]}
SKILL_SUCCESS
```

### 生成示例

输入步骤包含 ai_script：
```python
title = await page.locator("h1").inner_text()
rows = await page.locator("table tr").all_inner_texts()
```

生成的 `execute_skill()` 函数：
```python
async def execute_skill(page, **kwargs):
    _results = {}
    # ...
    title = await page.locator("h1").inner_text()
    _results["title"] = title
    rows = await page.locator("table tr").all_inner_texts()
    _results["rows"] = rows
    # ...
    return _results
```

---

## Step 2: 修改 `executor.py` — 捕获 execute_skill 返回值

**File:** `ScienceClaw/backend/rpa/executor.py`

### 改动点

- `execute()` 方法中捕获 `execute_skill()` 的返回值
- 有数据时在 output 中包含 `SKILL_DATA:` 行
- 返回结果增加 `data` 字段

```python
_result = await asyncio.wait_for(namespace["execute_skill"](page), timeout=timeout)
if _result:
    output = "SKILL_DATA:" + json.dumps(_result, ...) + "\nSKILL_SUCCESS"
else:
    output = "SKILL_SUCCESS"
return {"success": True, "output": output, "data": _result or {}}
```

---

## Step 3: 修改 `rpa.py` route — 本地模式测试捕获返回值

**File:** `ScienceClaw/backend/route/rpa.py`

### 改动点

- `test_script` 端点的本地模式分支捕获 `execute_skill()` 返回值
- 有数据时在 result 中包含 `data` 字段和 `SKILL_DATA:` 输出行

---

## Step 4: 修改 `local_preview_backend.py` — 捕获返回值

**File:** `ScienceClaw/backend/deepagent/local_preview_backend.py`

### 改动点

- 捕获 `execute_skill()` 返回值
- 有数据时在 output 中包含 `SKILL_DATA:` 行

---

## 向后兼容

- 无 ai_script 步骤的技能：`_results` 为空字典，不输出 `SKILL_DATA:` 行
- 纯操作的 ai_script（只有 click/fill 等）：不匹配提取模式，`_results` 为空
- 只有包含数据提取的技能才会多一行 `SKILL_DATA:` 输出
- `SKILL_SUCCESS` 始终输出，现有解析逻辑不受影响

## 判断逻辑

只捕获满足以下条件的赋值：
1. 形如 `var = [await] page.xxx()` 的赋值语句
2. 末尾调用的方法不在动作方法黑名单中（`_ACTION_METHODS`）

黑名单包括：click, fill, goto, hover, press, select_option, wait_for_timeout 等纯操作方法。
