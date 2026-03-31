# RPA CDP 复用浏览器设计

## 概述

将 RPA 系统从「在 sandbox 中通过 Playwright 启动独立浏览器」改为「通过 CDP 协议连接 sandbox 已有浏览器」。去掉 `playwright install chromium` 的浏览器二进制安装，保留 playwright Python 库作为 CDP 客户端。

## 目标

1. 去掉 sandbox 中 Playwright 浏览器二进制安装（减少镜像 300-500MB）
2. 运行时复用 sandbox 已有浏览器实例，不再独立启动浏览器进程
3. 支持多用户并发，BrowserContext 级隔离
4. 录制时保持 VNC 可视化能力
5. 保留现有的事件监听（expose_function）和 locator API

## 方案

通过 sandbox 的 `GET /v1/browser/info` 获取 `cdp_url`，用 `playwright.chromium.connect_over_cdp(cdp_url)` 连接已有浏览器。每个用户会话创建独立的 BrowserContext，实现 cookie/localStorage/session 级隔离。

## 架构设计

### CDP 连接层（新增 `backend/rpa/cdp_connector.py`）

单例模式管理 CDP 连接：

```python
class CDPConnector:
    _browser: Browser | None = None
    _playwright: Playwright | None = None

    async def get_browser(self) -> Browser:
        if self._browser and self._browser.is_connected():
            return self._browser
        cdp_url = await self._fetch_cdp_url()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        return self._browser

    async def _fetch_cdp_url(self) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{sandbox_base_url}/v1/browser/info")
            return resp.json()["data"]["cdp_url"]
```

- 连接断开时自动重连
- 提供 `get_browser()` 返回已连接的 browser 对象
- `sandbox_base_url` 从 `SANDBOX_MCP_URL` 推导（去掉 `/mcp` 后缀）

### 录制流程改造（`backend/rpa/manager.py`）

**旧流程：**
1. `supervisorctl stop browser` 停掉 sandbox 浏览器
2. 把 BROWSER_SCRIPT 写入 sandbox `/tmp/rpa_browser.py`
3. nohup 在 sandbox 中启动脚本，脚本内 `chromium.launch()` 启动新浏览器
4. 事件写入 `/tmp/rpa_events.jsonl`，backend 每 2s 通过 MCP 轮询

**新流程：**
1. 通过 CDPConnector 获取 browser 对象
2. `context = await browser.new_context()` — 用户独立 context
3. `page = await context.new_page()`
4. `await page.expose_function("__rpa_emit", rpa_emit)` — 事件回调直接到 Python 内存
5. `await page.evaluate(CAPTURE_JS)` — 注入事件捕获 JS
6. `page.on("load", on_load)` — 页面导航时重新注入
7. `page.on("framenavigated", on_navigated)` — 捕获 URL 变化
8. 事件直接进入 backend 内存队列

**关键变化：**

| 维度 | 旧 | 新 |
|------|-----|-----|
| 浏览器启动 | sandbox 内 launch() | CDP connect_over_cdp() |
| 事件传输 | 文件 → MCP 轮询 | expose_function → 内存回调 |
| 脚本运行位置 | sandbox 内 (nohup) | backend 进程内 (async) |
| API 风格 | sync Playwright | async Playwright |
| 浏览器生命周期 | 录制独占，stop/start | 共享，context 级隔离 |
| BROWSER_SCRIPT | 需要（约 500 行） | 完全移除 |

**事件存储：**
- `rpa_emit` 回调直接在 backend 进程中执行，事件存入 RPASession 的内存列表
- `_poll_events` 简化为从内存队列取数据，或改为回调驱动直接追加到 session.steps

**VNC 交互：**
- `context.new_page()` 在浏览器中打开新 tab，用户通过 VNC 操作
- 录制结束时 `context.close()` 关闭该 tab，不影响其他用户

### 执行/测试流程改造（`backend/rpa/executor.py`）

**旧流程：**
1. 通过 MCP 把脚本写入 sandbox `/tmp/rpa_test_script.py`
2. `supervisorctl stop browser`
3. `nohup bash -c 'python3 rpa_test_script.py > output.txt; echo DONE > done.txt' &`
4. 每 2s 轮询 done.txt（最多 90s）
5. 读取 output.txt 判断 SKILL_SUCCESS / SKILL_ERROR
6. `supervisorctl start browser`

**新流程：**
1. 通过 CDPConnector 获取 browser 对象
2. `context = await browser.new_context()` — 隔离的执行环境
3. `page = await context.new_page()`
4. 直接在 backend 进程中执行 `await execute_skill(page, **kwargs)`
5. `await context.close()`
6. 返回结果

**脚本生成改造（generator.py）：**
- 只生成 `execute_skill(page, **kwargs)` 函数体
- 去掉 `main()` 包装（launch browser、new_context、new_page、close）
- executor 负责 context/page 的创建和销毁

**导出的技能脚本（skill_exporter.py）：**
- 导出给用户的 `skill.py` 仍包含完整 `main()` 函数
- `main()` 中的 launch 改为 `connect_over_cdp`：

```python
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await execute_skill(page)
            await page.wait_for_timeout(5000)
            print('SKILL_SUCCESS')
        except Exception as e:
            await page.wait_for_timeout(3000)
            print(f'SKILL_ERROR: {e}')
        finally:
            await context.close()
```

### AI 助手改造（`backend/rpa/assistant.py`）

**旧流程：**
1. 通过 MCP 在 sandbox 中执行 JS 提取页面元素
2. LLM 生成 Python 代码
3. 代码写入 sandbox `/tmp/rpa_command.py`
4. BROWSER_SCRIPT 检测到文件，执行 `ns["run"](page)`
5. 结果写入 `/tmp/rpa_command_result.json`
6. backend 轮询读取结果（60 × 0.5s = 30s）

**新流程：**
1. 直接 `await page.evaluate(EXTRACT_ELEMENTS_JS)` 提取页面元素
2. LLM 生成 Python 代码（不变）
3. 直接在 backend 进程中执行：
   ```python
   await page.evaluate("window.__rpa_paused = true")
   exec(code, namespace)
   await namespace["run"](page)
   await page.evaluate("window.__rpa_paused = false")
   ```
4. 直接拿到执行结果

**关键变化：**
- 去掉文件写入 + 轮询的间接通信
- AI 生成的代码从 sync 改为 async（`_sync_to_async` 转换逻辑保留）
- 超时控制用 `asyncio.wait_for()` 包裹

### Dockerfile 与基础设施变更

**sandbox/Dockerfile：**
- 删除 `python3 -m playwright install --with-deps chromium`
- 保留 `pip install playwright`（作为 CDP 客户端库，如果 sandbox 内仍需要）
- 预计镜像体积减少 300-500MB

**docker-compose.yml：**
- sandbox 服务配置不变
- 端口映射不变（18080 → 8080 MCP, 16080 → 6080 VNC）

**backend 依赖：**
- 新增 `playwright` Python 包依赖
- 新增 `httpx`（如果还没有）用于调用 sandbox HTTP API
- backend 不需要 `playwright install chromium` — 只用 CDP 连接

**环境变量：**
- 从 `SANDBOX_MCP_URL`（`http://localhost:18080/mcp`）推导 base URL `http://localhost:18080`
- 无需新增环境变量

**supervisorctl 相关代码清理：**
- manager.py 中所有 `supervisorctl stop/start browser` 调用移除
- executor.py 中同样移除
- 不再需要 `pkill -f chromium`、`pkill -f rpa_browser.py` 等进程清理逻辑

## 影响范围

| 文件 | 改动程度 | 说明 |
|------|----------|------|
| `backend/rpa/cdp_connector.py` | 新增 | CDP 连接管理单例 |
| `backend/rpa/manager.py` | 大改 | 删除 BROWSER_SCRIPT，录制逻辑改为 async CDP |
| `backend/rpa/executor.py` | 中改 | 去掉 nohup/轮询，改为直接执行 |
| `backend/rpa/generator.py` | 小改 | 生成脚本去掉 main() 包装 |
| `backend/rpa/assistant.py` | 中改 | 去掉文件通信，直接执行 AI 代码 |
| `backend/rpa/skill_exporter.py` | 小改 | 导出脚本 launch 改为 connect_over_cdp |
| `sandbox/Dockerfile` | 小改 | 删除 playwright install chromium |
| `backend/requirements` | 小改 | 新增 playwright 依赖 |

## 风险与缓解

1. **expose_function 在 CDP 连接模式下的行为** — Playwright 文档确认 connect_over_cdp 支持 expose_function，但需验证跨网络回调是否正常。缓解：实现后第一步验证，不可用则回退到文件 + 轮询。

2. **VNC 多 tab 体验** — 多用户共享同一个 X11 display，VNC 看到所有 tab。缓解：录制开始时 `await page.bring_to_front()` 聚焦到用户自己的 tab。

3. **浏览器崩溃影响面** — 单浏览器实例崩溃中断所有用户。缓解：CDPConnector 加重连逻辑，浏览器崩溃后自动重连。

4. **网络延迟** — CDP WebSocket 跨容器通信比 sandbox 内本地 Playwright 略慢。对 RPA 录制场景影响可忽略，无需特殊处理。
