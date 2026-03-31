# RPA CDP 复用浏览器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 RPA 系统从 sandbox 内启动独立 Playwright 浏览器改为通过 CDP 连接 sandbox 已有浏览器，去掉浏览器二进制安装，支持多用户 BrowserContext 级隔离。

**Architecture:** backend 通过 sandbox 的 `/v1/browser/info` API 获取 CDP URL，用 `playwright.chromium.connect_over_cdp()` 连接已有浏览器。每个录制/执行会话创建独立的 BrowserContext。事件捕获从文件轮询改为 `expose_function` 内存回调。

**Tech Stack:** Playwright async API (connect_over_cdp), httpx, asyncio

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/rpa/cdp_connector.py` | Create | CDP 连接管理单例，获取 cdp_url，维护连接 |
| `backend/rpa/manager.py` | Modify | 删除 BROWSER_SCRIPT，录制改为 CDP + 内存回调 |
| `backend/rpa/executor.py` | Modify | 去掉 nohup/轮询，直接通过 CDP 执行 |
| `backend/rpa/generator.py` | Modify | 去掉 main() 包装，只生成 execute_skill() |
| `backend/rpa/assistant.py` | Modify | 去掉文件通信，直接通过 page 对象执行 |
| `backend/rpa/skill_exporter.py` | Modify | 导出脚本 launch 改为 connect_over_cdp |
| `backend/rpa/__init__.py` | Modify | 导出 CDPConnector |
| `backend/config.py` | No change | sandbox_base_url 从 sandbox_mcp_url 推导，无需修改 |
| `backend/route/rpa.py` | Modify | 适配新的 async 接口 |
| `sandbox/Dockerfile` | Modify | 删除 playwright install chromium |
| `backend/requirements.txt` | Modify | 新增 playwright 依赖 |

---

### Task 1: 新增 CDP 连接管理器 (`cdp_connector.py`)

**Files:**
- Create: `ScienceClaw/backend/rpa/cdp_connector.py`

- [ ] **Step 1: Create cdp_connector.py**

```python
import logging
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser, Playwright

from backend.config import settings

logger = logging.getLogger(__name__)


class CDPConnector:
    """Singleton CDP connection manager.

    Connects to the sandbox's existing browser via CDP protocol.
    Provides get_browser() for shared access across recording/execution sessions.
    """

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._sandbox_base_url = settings.sandbox_mcp_url.replace("/mcp", "")

    async def get_browser(self) -> Browser:
        """Get or create a CDP browser connection."""
        if self._browser and self._browser.is_connected():
            return self._browser

        cdp_url = await self._fetch_cdp_url()
        logger.info(f"Connecting to browser via CDP: {cdp_url}")

        if not self._playwright:
            self._playwright = await async_playwright().start()

        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        logger.info("CDP browser connection established")
        return self._browser

    async def _fetch_cdp_url(self) -> str:
        """Fetch CDP WebSocket URL from sandbox API."""
        url = f"{self._sandbox_base_url}/v1/browser/info"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            cdp_url = data.get("data", {}).get("cdp_url", "")
            if not cdp_url:
                raise RuntimeError(f"No cdp_url in response from {url}: {data}")
            return cdp_url

    async def close(self):
        """Clean up connections."""
        if self._browser:
            try:
                self._browser = None
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
                self._playwright = None
            except Exception:
                pass


# Global singleton
cdp_connector = CDPConnector()
```

- [ ] **Step 2: Verify file created**

Run: `python -c "import ast; ast.parse(open('ScienceClaw/backend/rpa/cdp_connector.py').read()); print('OK')"`
Expected: OK

- [ ] **Step 3: Update `__init__.py` to export CDPConnector**

Replace the content of `ScienceClaw/backend/rpa/__init__.py` with:

```python
from .manager import rpa_manager, RPASession, RPAStep
from .cdp_connector import cdp_connector
```

- [ ] **Step 4: Add playwright to backend requirements.txt**

Add `playwright>=1.40` to `ScienceClaw/backend/requirements.txt` (after the existing `websockets>=11.0` line).

- [ ] **Step 5: Commit**

```bash
git add ScienceClaw/backend/rpa/cdp_connector.py ScienceClaw/backend/rpa/__init__.py ScienceClaw/backend/requirements.txt
git commit -m "feat: 新增 CDP 连接管理器，通过 sandbox 已有浏览器的 CDP 端口连接"
```

---

### Task 2: 改造 generator.py — 去掉 main() 包装

**Files:**
- Modify: `ScienceClaw/backend/rpa/generator.py:106-141`

- [ ] **Step 1: Modify generate_script() to remove main() wrapper**

In `generator.py`, replace lines 106-141 (the `# Main runner` section through end of method) with just the return statement. The `generate_script()` method should only generate the `execute_skill()` function, not the `main()` wrapper.

Replace this block (lines 106-141):
```python
        # Main runner
        lines.extend([
            "",
            "async def main():",
            "    async with async_playwright() as p:",
            "        browser = await p.chromium.launch(",
            '            headless=False,',
            '            executable_path="/usr/bin/chromium-browser",',
            '            args=["--no-sandbox", "--disable-gpu", "--start-maximized",',
            '                  "--window-size=1280,720", "--disable-dev-shm-usage"]',
            "        )",
            "        context = await browser.new_context(no_viewport=True)",
            "        page = await context.new_page()",
            "        # Set shorter timeout so failures are reported quickly",
            "        page.set_default_timeout(15000)",
            "        try:",
            "            await execute_skill(page)",
            "            # Keep browser open so user can see the result in VNC",
            "            await page.wait_for_timeout(5000)",
            "            print('SKILL_SUCCESS')",
            "        except Exception as e:",
            "            # Keep browser open on error too so user can see the state",
            "            try:",
            "                await page.wait_for_timeout(3000)",
            "            except Exception:",
            "                pass",
            "            print(f'SKILL_ERROR: {e}')",
            "        finally:",
            "            await browser.close()",
            "",
            "",
            'if __name__ == "__main__":',
            "    asyncio.run(main())",
        ])
```

With:
```python
        # No main() wrapper — executor handles browser/context lifecycle
```

Also update the imports at lines 19-23. Remove the `async_playwright` import and `os.environ["DISPLAY"]` since the script no longer runs standalone in sandbox:

Replace lines 19-27:
```python
        lines = [
            "import os, asyncio",
            'os.environ["DISPLAY"] = ":99"',
            "from playwright.async_api import async_playwright",
            "",
            "",
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA recording."""',
        ]
```

With:
```python
        lines = [
            "",
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA recording."""',
        ]
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('ScienceClaw/backend/rpa/generator.py').read()); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/rpa/generator.py
git commit -m "refactor: generator 只生成 execute_skill()，去掉 main() 包装"
```

---

### Task 3: 改造 executor.py — CDP 直接执行

**Files:**
- Modify: `ScienceClaw/backend/rpa/executor.py` (full rewrite)

- [ ] **Step 1: Rewrite executor.py**

Replace the entire file with:

```python
import logging
import asyncio
from typing import Dict, Any, Callable, Optional

from playwright.async_api import Browser

logger = logging.getLogger(__name__)


class ScriptExecutor:
    """Execute generated Playwright scripts via CDP browser connection."""

    async def execute(
        self,
        browser: Browser,
        script: str,
        on_log: Optional[Callable[[str], None]] = None,
        timeout: float = 90.0,
    ) -> Dict[str, Any]:
        """Execute script in a new BrowserContext via CDP.

        Args:
            browser: CDP-connected browser instance
            script: Python source containing async def execute_skill(page, **kwargs)
            on_log: Optional callback for progress messages
            timeout: Max execution time in seconds
        """
        context = None
        try:
            if on_log:
                on_log("Creating browser context...")

            context = await browser.new_context(no_viewport=True)
            page = await context.new_page()
            page.set_default_timeout(15000)

            if on_log:
                on_log("Executing script...")

            # Compile and extract execute_skill function
            namespace: Dict[str, Any] = {}
            exec(compile(script, "<rpa_script>", "exec"), namespace)

            if "execute_skill" not in namespace:
                return {"success": False, "output": "", "error": "No execute_skill() function in script"}

            # Run with timeout
            await asyncio.wait_for(
                namespace["execute_skill"](page),
                timeout=timeout,
            )

            # Brief pause so user can see result in VNC
            await page.wait_for_timeout(3000)

            output = "SKILL_SUCCESS"
            if on_log:
                on_log("Execution completed successfully")

            return {"success": True, "output": output}

        except asyncio.TimeoutError:
            output = f"SKILL_ERROR: Script did not complete within {timeout}s"
            if on_log:
                on_log(output)
            return {"success": False, "output": output, "error": f"Timeout after {timeout}s"}

        except Exception as e:
            output = f"SKILL_ERROR: {e}"
            if on_log:
                on_log(f"Execution failed: {e}")
            return {"success": False, "output": output, "error": str(e)}

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('ScienceClaw/backend/rpa/executor.py').read()); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/rpa/executor.py
git commit -m "refactor: executor 改为通过 CDP 直接执行，去掉 nohup/轮询/supervisorctl"
```

---

### Task 4: 改造 manager.py — CDP 录制 + 内存回调

**Files:**
- Modify: `ScienceClaw/backend/rpa/manager.py` (major rewrite)

- [ ] **Step 1: Replace imports and remove BROWSER_SCRIPT**

Replace lines 1-559 (everything before `class RPASessionManager`) with:

```python
import json
import logging
import uuid
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime

from pydantic import BaseModel, Field
from playwright.async_api import Page, BrowserContext

from .cdp_connector import cdp_connector

logger = logging.getLogger(__name__)


class RPAStep(BaseModel):
    id: str
    action: str
    target: Optional[str] = None
    value: Optional[str] = None
    screenshot_url: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    description: Optional[str] = None
    tag: Optional[str] = None
    label: Optional[str] = None
    url: Optional[str] = None
    source: str = "record"  # "record" or "ai"
    prompt: Optional[str] = None  # original user instruction for AI steps


class RPASession(BaseModel):
    id: str
    user_id: str
    start_time: datetime = Field(default_factory=datetime.now)
    status: str = "recording"  # recording, stopped, testing, saved
    steps: List[RPAStep] = []
    sandbox_session_id: str


# ── CAPTURE_JS: injected into pages to capture user events ──────────
# This is the same JS from the old BROWSER_SCRIPT, extracted as a standalone constant.
# It calls window.__rpa_emit(JSON.stringify(evt)) which is bridged to Python
# via page.expose_function().
```

Note: The `CAPTURE_JS` constant (the JavaScript between lines 54-447 of the old file) must be preserved exactly as-is. Extract it from the old `BROWSER_SCRIPT` and place it here as a top-level constant:

```python
CAPTURE_JS = r"""
(() => {
    if (window.__rpa_injected) return;
    window.__rpa_injected = true;
    window.__rpa_paused = false;
    // ... (entire JS body from old lines 54-447, unchanged)
    console.log('[RPA] Event capture injected');
})();
"""
```

The JS content is ~400 lines and must be copied verbatim from the old BROWSER_SCRIPT's CAPTURE_JS variable (lines 54-447).

- [ ] **Step 2: Rewrite RPASessionManager class**

Replace the entire `RPASessionManager` class (old lines 562-848) with:

```python
class RPASessionManager:
    def __init__(self):
        self.sessions: Dict[str, RPASession] = {}
        self.ws_connections: Dict[str, List] = {}
        # Per-session Playwright objects for cleanup
        self._contexts: Dict[str, BrowserContext] = {}
        self._pages: Dict[str, Page] = {}

    # ── Session lifecycle ────────────────────────────────────────────

    async def create_session(self, user_id: str, sandbox_session_id: str) -> RPASession:
        session_id = str(uuid.uuid4())
        session = RPASession(
            id=session_id,
            user_id=user_id,
            sandbox_session_id=sandbox_session_id,
        )
        self.sessions[session_id] = session

        # Connect to sandbox browser via CDP
        browser = await cdp_connector.get_browser()

        # Create isolated BrowserContext for this user session
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()

        self._contexts[session_id] = context
        self._pages[session_id] = page

        # Bridge JS events to Python memory via expose_function
        async def rpa_emit(event_json: str):
            try:
                evt = json.loads(event_json)
                await self._handle_event(session_id, evt)
            except Exception as e:
                logger.error(f"[RPA] emit error: {e}")

        await page.expose_function("__rpa_emit", rpa_emit)

        # Inject event capture JS
        await page.evaluate(CAPTURE_JS)

        # Track URL changes for address-bar navigation
        last_url = {"value": ""}

        def on_navigated(frame):
            if frame != page.main_frame:
                return
            new_url = frame.url
            if new_url and new_url != last_url["value"] and new_url != "about:blank":
                last_url["value"] = new_url
                evt = {
                    "action": "navigate",
                    "url": new_url,
                    "timestamp": int(datetime.now().timestamp() * 1000),
                }
                asyncio.create_task(self._handle_event(session_id, evt))

        page.on("framenavigated", on_navigated)

        # Re-inject JS on page load (navigation resets JS state)
        async def on_load(loaded_page):
            try:
                await loaded_page.evaluate(CAPTURE_JS)
            except Exception:
                pass

        page.on("load", on_load)

        # Navigate to about:blank — let user decide where to go
        await page.goto("about:blank")
        await page.bring_to_front()

        logger.info(f"[RPA] Session {session_id} started via CDP")
        return session

    async def stop_session(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].status = "stopped"

        # Close the BrowserContext (closes the tab)
        context = self._contexts.pop(session_id, None)
        self._pages.pop(session_id, None)
        if context:
            try:
                await context.close()
            except Exception as e:
                logger.warning(f"[RPA] Error closing context: {e}")

        logger.info(f"[RPA] Session {session_id} stopped")

    async def get_session(self, session_id: str) -> Optional[RPASession]:
        return self.sessions.get(session_id)

    def get_page(self, session_id: str) -> Optional[Page]:
        """Get the Playwright page for a session (used by assistant)."""
        return self._pages.get(session_id)

    # ── Event handling (replaces file polling) ───────────────────────

    async def _handle_event(self, session_id: str, evt: dict):
        """Process a single event from the browser capture JS."""
        if session_id not in self.sessions:
            return
        if self.sessions[session_id].status != "recording":
            return

        # Deduplicate: drop navigate events that follow a click/press/fill
        # within 5 seconds (navigation is a side-effect of the user action)
        if evt.get("action") == "navigate":
            nav_ts = evt.get("timestamp", 0)
            steps = self.sessions[session_id].steps
            if steps:
                last_step = steps[-1]
                if last_step.action in ("click", "press", "fill"):
                    last_ts = last_step.timestamp.timestamp() * 1000
                    if nav_ts - last_ts < 5000:
                        logger.debug(f"[RPA] Skipping nav (side-effect): {evt.get('url', '')[:60]}")
                        return

        locator_info = evt.get("locator", {})
        step_data = {
            "action": evt.get("action", "unknown"),
            "target": json.dumps(locator_info) if locator_info else "",
            "value": evt.get("value", ""),
            "label": "",
            "tag": evt.get("tag", ""),
            "url": evt.get("url", ""),
            "description": self._make_description(evt),
        }
        await self.add_step(session_id, step_data)
        logger.debug(f"[RPA] Step: {step_data['description'][:60]}")

    @staticmethod
    def _make_description(evt: dict) -> str:
        action = evt.get("action", "")
        value = evt.get("value", "")
        locator = evt.get("locator", {})

        method = locator.get("method", "") if isinstance(locator, dict) else ""
        if method == "role":
            name = locator.get("name", "")
            target = f'{locator.get("role", "")}("{name}")' if name else locator.get("role", "")
        elif method in ("testid", "label", "placeholder", "alt", "title", "text"):
            target = f'{method}("{locator.get("value", "")}")'
        elif method == "nested":
            parent = locator.get("parent", {})
            child = locator.get("child", {})
            p_name = parent.get("name", parent.get("value", ""))
            c_name = child.get("name", child.get("value", ""))
            target = f'{p_name} >> {c_name}'
        elif method == "css":
            target = locator.get("value", "")
        else:
            target = str(locator)

        if action == "fill":
            return f'输入 "{value}" 到 {target}'
        if action == "click":
            return f"点击 {target}"
        if action == "press":
            return f"按下 {value} 在 {target}"
        if action == "select":
            return f"选择 {value} 在 {target}"
        if action == "navigate":
            return f"导航到 {evt.get('url', '')}"
        return f"{action} on {target}"

    # ── Step management ──────────────────────────────────────────────

    async def add_step(self, session_id: str, step_data: Dict[str, Any]) -> RPAStep:
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.sessions[session_id]
        step = RPAStep(id=str(uuid.uuid4()), **step_data)
        session.steps.append(step)

        await self._broadcast_step(session_id, step)
        return step

    async def _broadcast_step(self, session_id: str, step: RPAStep):
        if session_id in self.ws_connections:
            message = {"type": "step", "data": step.model_dump()}
            for ws in self.ws_connections[session_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    def register_ws(self, session_id: str, websocket):
        if session_id not in self.ws_connections:
            self.ws_connections[session_id] = []
        self.ws_connections[session_id].append(websocket)

    def unregister_ws(self, session_id: str, websocket):
        if session_id in self.ws_connections:
            try:
                self.ws_connections[session_id].remove(websocket)
            except ValueError:
                pass


# ── Global instance ──────────────────────────────────────────────────
rpa_manager = RPASessionManager()
```

Key changes:
- `__init__` no longer takes `sandbox_url` / `vlm_api_key` / `vlm_base_url` params
- `create_session` uses `cdp_connector.get_browser()` instead of writing BROWSER_SCRIPT to sandbox
- `_poll_events` is completely removed — replaced by `_handle_event` callback
- `stop_session` closes the BrowserContext instead of killing processes
- All `_exec_sandbox_cmd` / `_exec_sandbox_code` / `_write_browser_script` methods removed
- New `get_page()` method for assistant to access the page object
- `_make_description` and step management methods preserved unchanged

- [ ] **Step 3: Verify syntax**

Run: `python -c "import ast; ast.parse(open('ScienceClaw/backend/rpa/manager.py').read()); print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add ScienceClaw/backend/rpa/manager.py
git commit -m "refactor: manager 改为 CDP 连接 + expose_function 内存回调，删除 BROWSER_SCRIPT"
```

---

### Task 5: 改造 assistant.py — 直接通过 page 对象执行

**Files:**
- Modify: `ScienceClaw/backend/rpa/assistant.py` (major rewrite)

- [ ] **Step 1: Rewrite assistant.py**

Replace the entire file with:

```python
import json
import logging
import re
import asyncio
from typing import Dict, List, Any, AsyncGenerator, Optional

from playwright.async_api import Page
from backend.deepagent.engine import get_llm_model

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个 RPA 录制助手。用户正在录制浏览器自动化技能，你需要根据用户的自然语言描述，结合当前页面状态和历史操作，生成 Playwright 异步 API 代码片段。

规则：
1. 生成的代码必须使用 Playwright 异步 API（await page.locator().click()）
2. 代码必须定义 async def run(page): 函数
3. 使用动态适应的选择器：
   - "点击第一个搜索结果" → await page.locator("h3").first.click() 或 await page.locator("[data-result]").first.click()
   - "获取表格数据" → await page.locator("table").first.inner_text()
   - 不要硬编码具体文本内容，用位置/结构/角色选择
4. 操作之间加 await page.wait_for_timeout(500) 等待 UI 响应
5. 如果操作可能触发页面导航，在 click 后加 await page.wait_for_load_state("load")
6. 用 ```python 代码块包裹代码
7. 代码之外可以附带简短说明"""

EXTRACT_ELEMENTS_JS = r"""() => {
    const INTERACTIVE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]';
    const els = document.querySelectorAll(INTERACTIVE);
    const results = [];
    let index = 1;
    const seen = new Set();
    for (const el of els) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (el.disabled) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const name = (el.getAttribute('aria-label') || el.innerText || '').trim().substring(0, 80);
        const placeholder = el.getAttribute('placeholder') || '';
        const href = el.getAttribute('href') || '';
        const value = el.value || '';
        const type = el.getAttribute('type') || '';

        const key = tag + role + name + placeholder + href;
        if (seen.has(key)) continue;
        seen.add(key);

        const info = { index, tag };
        if (role) info.role = role;
        if (name) info.name = name.replace(/\s+/g, ' ');
        if (placeholder) info.placeholder = placeholder;
        if (href) info.href = href.substring(0, 120);
        if (value && tag !== 'input') info.value = value.substring(0, 80);
        if (type) info.type = type;
        const checked = el.checked;
        if (checked !== undefined) info.checked = checked;

        results.push(info);
        index++;
        if (index > 150) break;
    }
    return JSON.stringify(results);
}"""


class RPAAssistant:
    """AI recording assistant: takes natural language, generates and executes Playwright code."""

    def __init__(self):
        self._histories: Dict[str, List[Dict[str, str]]] = {}

    def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self._histories:
            self._histories[session_id] = []
        return self._histories[session_id]

    def _trim_history(self, session_id: str, max_rounds: int = 10):
        hist = self._get_history(session_id)
        max_msgs = max_rounds * 2
        if len(hist) > max_msgs:
            self._histories[session_id] = hist[-max_msgs:]

    async def chat(
        self,
        session_id: str,
        page: Page,
        message: str,
        steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream AI assistant response. Yields SSE event dicts.

        Args:
            session_id: RPA session ID
            page: The Playwright page object for this session
            message: User's natural language instruction
            steps: Current recorded steps
            model_config: Optional LLM model config override
        """
        # 1. Get page elements directly via page.evaluate
        yield {"event": "message_chunk", "data": {"text": "正在分析当前页面...\n\n"}}
        elements_json = await self._get_page_elements(page)

        # 2. Build prompt
        history = self._get_history(session_id)
        messages = self._build_messages(message, steps, elements_json, history)

        # 3. Stream LLM response
        full_response = ""
        async for chunk_text in self._stream_llm(messages, model_config):
            full_response += chunk_text
            yield {"event": "message_chunk", "data": {"text": chunk_text}}

        # 4. Extract code
        code = self._extract_code(full_response)
        if not code:
            yield {"event": "error", "data": {"message": "未能从 AI 响应中提取到代码"}}
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": full_response})
            self._trim_history(session_id)
            yield {"event": "done", "data": {}}
            return

        yield {"event": "script", "data": {"code": code}}

        # 5. Execute directly on the page object
        yield {"event": "executing", "data": {}}
        result = await self._execute_on_page(page, code)

        if not result["success"]:
            # 6. Retry once with error context
            yield {"event": "message_chunk", "data": {"text": "\n\n执行失败，正在重试...\n\n"}}
            retry_messages = messages + [
                {"role": "assistant", "content": full_response},
                {"role": "user", "content": f"执行报错：{result['error']}\n请修正代码重试。"},
            ]
            retry_response = ""
            async for chunk_text in self._stream_llm(retry_messages, model_config):
                retry_response += chunk_text
                yield {"event": "message_chunk", "data": {"text": chunk_text}}

            retry_code = self._extract_code(retry_response)
            if retry_code:
                yield {"event": "script", "data": {"code": retry_code}}
                yield {"event": "executing", "data": {}}
                result = await self._execute_on_page(page, retry_code)
                if result["success"]:
                    code = retry_code
                    full_response = retry_response

        # 7. Save to history
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": full_response})
        self._trim_history(session_id)

        # 8. Build step data if successful
        step_data = None
        if result["success"]:
            body = self._extract_function_body(code)
            step_data = {
                "action": "ai_script",
                "source": "ai",
                "value": body,
                "description": message,
                "prompt": message,
            }

        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "error": result.get("error"),
                "step": step_data,
            },
        }
        yield {"event": "done", "data": {}}

    def _build_messages(
        self,
        user_message: str,
        steps: List[Dict[str, Any]],
        elements_json: str,
        history: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        steps_text = ""
        if steps:
            lines = []
            for i, s in enumerate(steps, 1):
                source = s.get("source", "record")
                desc = s.get("description", s.get("action", ""))
                lines.append(f"{i}. [{source}] {desc}")
            steps_text = "\n".join(lines)

        elements_text = ""
        try:
            els = json.loads(elements_json) if elements_json else []
            lines = []
            for el in els:
                parts = [f"[{el['index']}]"]
                if el.get("role"):
                    parts.append(el["role"])
                parts.append(el["tag"])
                if el.get("name"):
                    parts.append(f'"{el["name"]}"')
                if el.get("placeholder"):
                    parts.append(f'placeholder="{el["placeholder"]}"')
                if el.get("href"):
                    parts.append(f'href="{el["href"]}"')
                if el.get("type"):
                    parts.append(f'type={el["type"]}')
                lines.append(" ".join(parts))
            elements_text = "\n".join(lines)
        except (json.JSONDecodeError, TypeError):
            elements_text = "(无法获取页面元素)"

        context = f"""## 历史操作步骤
{steps_text or "(暂无步骤)"}

## 当前页面可交互元素
{elements_text or "(无法获取)"}

## 用户指令
{user_message}"""

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": context})
        return messages

    async def _stream_llm(self, messages: List[Dict[str, str]], model_config: Optional[Dict[str, Any]] = None) -> AsyncGenerator[str, None]:
        """Stream LLM response chunks."""
        model = get_llm_model(config=model_config, streaming=True)
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

        lc_messages = []
        for m in messages:
            if m["role"] == "system":
                lc_messages.append(SystemMessage(content=m["content"]))
            elif m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))

        async for chunk in model.astream(lc_messages):
            if chunk.content:
                yield chunk.content

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        """Extract python code block from LLM response."""
        pattern = r"```python\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        pattern2 = r"(async def run\(page\):.*)"
        match2 = re.search(pattern2, text, re.DOTALL)
        if match2:
            return match2.group(1).strip()
        # Fallback: try sync pattern too
        pattern3 = r"(def run\(page\):.*)"
        match3 = re.search(pattern3, text, re.DOTALL)
        if match3:
            return match3.group(1).strip()
        return None

    @staticmethod
    def _extract_function_body(code: str) -> str:
        """Extract the body of async def run(page): for storage in step.value."""
        lines = code.split("\n")
        body_lines = []
        in_body = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("async def run(") or stripped.startswith("def run("):
                in_body = True
                continue
            if in_body:
                if line.startswith("    "):
                    body_lines.append(line[4:])
                elif line.strip() == "":
                    body_lines.append("")
                else:
                    body_lines.append(line)
        return "\n".join(body_lines).strip()

    async def _get_page_elements(self, page: Page) -> str:
        """Extract interactive elements directly from the page."""
        try:
            result = await page.evaluate(EXTRACT_ELEMENTS_JS)
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as e:
            logger.warning(f"Failed to extract elements: {e}")
            return "[]"

    async def _execute_on_page(self, page: Page, code: str) -> Dict[str, Any]:
        """Execute AI-generated code directly on the page object."""
        # Pause event capture during AI script execution
        try:
            await page.evaluate("window.__rpa_paused = true")
        except Exception:
            pass

        try:
            namespace: Dict[str, Any] = {"page": page}
            exec(compile(code, "<rpa_assistant>", "exec"), namespace)

            if "run" in namespace and callable(namespace["run"]):
                ret = await asyncio.wait_for(
                    namespace["run"](page),
                    timeout=30.0,
                )
                return {"success": True, "output": str(ret) if ret else "ok", "error": None}
            else:
                return {"success": False, "output": "", "error": "No run(page) function defined"}

        except asyncio.TimeoutError:
            return {"success": False, "output": "", "error": "Command execution timed out (30s)"}
        except Exception as e:
            import traceback
            return {"success": False, "output": "", "error": traceback.format_exc()}
        finally:
            # Resume event capture
            try:
                await page.evaluate("window.__rpa_paused = false")
            except Exception:
                pass
```

Key changes from old assistant.py:
- `__init__` no longer takes `sandbox_url` — no MCP communication needed
- `chat()` takes a `page: Page` parameter instead of `sandbox_session_id`
- `_get_page_elements()` uses `await page.evaluate()` directly instead of MCP file-based command
- `_execute_on_page()` replaces `_execute_command()` — executes directly in backend process, no file writing/polling
- `_exec_cmd` and `_exec_code` MCP helper methods removed entirely
- SYSTEM_PROMPT updated: sync API → async API, `def run(page)` → `async def run(page)`
- `_extract_code` updated to also match `async def run(page):`
- `_extract_function_body` updated to handle both `async def run(` and `def run(`

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('ScienceClaw/backend/rpa/assistant.py').read()); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/rpa/assistant.py
git commit -m "refactor: assistant 直接通过 page 对象执行，去掉 MCP 文件通信"
```

---

### Task 6: 改造 route/rpa.py 和 skill_exporter.py — 适配新接口

**Files:**
- Modify: `ScienceClaw/backend/route/rpa.py`
- Modify: `ScienceClaw/backend/rpa/skill_exporter.py`

- [ ] **Step 1: Update route/rpa.py imports and global instances**

Replace lines 1-23 of `rpa.py`:

```python
import json
import logging
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.rpa.manager import rpa_manager
from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.executor import ScriptExecutor
from backend.rpa.skill_exporter import SkillExporter
from backend.rpa.assistant import RPAAssistant
from backend.user.dependencies import get_current_user, User
from backend.config import settings
from backend.mongodb.db import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["RPA"])
generator = PlaywrightGenerator()
executor = ScriptExecutor(rpa_manager.sandbox_url)
exporter = SkillExporter()
assistant = RPAAssistant(rpa_manager.sandbox_url)
```

With:

```python
import json
import logging
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.rpa.manager import rpa_manager
from backend.rpa.cdp_connector import cdp_connector
from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.executor import ScriptExecutor
from backend.rpa.skill_exporter import SkillExporter
from backend.rpa.assistant import RPAAssistant
from backend.user.dependencies import get_current_user, User
from backend.config import settings
from backend.mongodb.db import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["RPA"])
generator = PlaywrightGenerator()
executor = ScriptExecutor()
exporter = SkillExporter()
assistant = RPAAssistant()
```

- [ ] **Step 2: Update test_script endpoint**

Replace the `test_script` endpoint body (the `result = await executor.execute(...)` call) to pass browser instead of sandbox_session_id:

Find in `test_script`:
```python
    result = await executor.execute(
        session.sandbox_session_id,
        script,
        on_log=lambda msg: logs.append(msg),
    )
```

Replace with:
```python
    browser = await cdp_connector.get_browser()
    result = await executor.execute(
        browser,
        script,
        on_log=lambda msg: logs.append(msg),
    )
```

- [ ] **Step 3: Update chat_with_assistant endpoint**

In the `chat_with_assistant` endpoint, the `assistant.chat()` call needs to pass the page object instead of sandbox_session_id.

Find:
```python
                async for event in assistant.chat(
                    session_id=session_id,
                    sandbox_session_id=session.sandbox_session_id,
                    message=request.message,
                    steps=steps,
                    model_config=model_config,
                ):
```

Replace with:
```python
                page = rpa_manager.get_page(session_id)
                if not page:
                    yield {
                        "event": "error",
                        "data": json.dumps({"message": "Recording session not active"}, ensure_ascii=False),
                    }
                    yield {"event": "done", "data": "{}"}
                    return

                async for event in assistant.chat(
                    session_id=session_id,
                    page=page,
                    message=request.message,
                    steps=steps,
                    model_config=model_config,
                ):
```

- [ ] **Step 4: Update skill_exporter.py — add connect_over_cdp wrapper for exported scripts**

In `skill_exporter.py`, the `export_skill` method stores the script as-is. Since the generator no longer produces a `main()` wrapper, we need to add one for exported skills so they can run standalone.

Add a method to `SkillExporter` and call it before storing:

After line 20 (`script: str,`), the `export_skill` method should wrap the script:

```python
    async def export_skill(
        self,
        user_id: str,
        skill_name: str,
        description: str,
        script: str,
        params: Dict[str, Any],
    ) -> str:
        # Wrap script with standalone runner for exported skills
        standalone_script = self._wrap_standalone(script)

        # ... rest of method uses standalone_script instead of script ...
```

Add this method to the class:

```python
    @staticmethod
    def _wrap_standalone(script: str) -> str:
        """Wrap execute_skill() with a standalone main() for exported skills."""
        wrapper = '''import asyncio
from playwright.async_api import async_playwright


{script}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("ws://localhost:9222")
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()
        page.set_default_timeout(15000)
        try:
            await execute_skill(page)
            await page.wait_for_timeout(5000)
            print("SKILL_SUCCESS")
        except Exception as e:
            try:
                await page.wait_for_timeout(3000)
            except Exception:
                pass
            print(f"SKILL_ERROR: {{e}}")
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
'''
        return wrapper.format(script=script)
```

And in `export_skill`, change the MongoDB `$set` to use `standalone_script`:

```python
                    "files": {
                        "SKILL.md": skill_md,
                        "skill.py": standalone_script,
                    },
```

- [ ] **Step 5: Verify syntax of both files**

Run: `python -c "import ast; ast.parse(open('ScienceClaw/backend/route/rpa.py').read()); print('rpa.py OK')"`
Run: `python -c "import ast; ast.parse(open('ScienceClaw/backend/rpa/skill_exporter.py').read()); print('exporter OK')"`
Expected: both OK

- [ ] **Step 6: Commit**

```bash
git add ScienceClaw/backend/route/rpa.py ScienceClaw/backend/rpa/skill_exporter.py
git commit -m "refactor: route 和 exporter 适配 CDP 新接口"
```

---

### Task 7: 改造 sandbox/Dockerfile — 去掉 playwright install chromium

**Files:**
- Modify: `ScienceClaw/sandbox/Dockerfile:28-34`

- [ ] **Step 1: Remove playwright install chromium from Dockerfile**

In `ScienceClaw/sandbox/Dockerfile`, replace lines 28-34:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip config set timeout 600 \
    && python3 -m pip install -r /tmp/requirements.txt \
    && python3 -m playwright install --with-deps chromium \
    && rm -f /tmp/requirements.txt \
    && python3 -c "import matplotlib; matplotlib.get_cachedir()" \
    && python3 -c "import matplotlib.font_manager; matplotlib.font_manager._load_fontmanager(try_read_cache=False)"
```

With:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip config set timeout 600 \
    && python3 -m pip install -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt \
    && python3 -c "import matplotlib; matplotlib.get_cachedir()" \
    && python3 -c "import matplotlib.font_manager; matplotlib.font_manager._load_fontmanager(try_read_cache=False)"
```

The only change is removing `&& python3 -m playwright install --with-deps chromium \`.

- [ ] **Step 2: Commit**

```bash
git add ScienceClaw/sandbox/Dockerfile
git commit -m "chore: sandbox Dockerfile 去掉 playwright install chromium，减少镜像体积"
```

---

### Task 8: 清理 generator.py 中的 _sync_to_async 方法

**Files:**
- Modify: `ScienceClaw/backend/rpa/generator.py`

由于 assistant.py 现在生成的是 async 代码，`_sync_to_async` 方法在 generator 中仍然需要保留（用于处理 `ai_script` 类型的 step，其中 `step.value` 可能包含旧的 sync 代码）。但需要确认 generator 中对 `ai_script` 的处理逻辑是否需要更新。

- [ ] **Step 1: Review ai_script handling in generator**

在 `generator.py` 的 `generate_script()` 方法中，lines 54-61 处理 `ai_script` action：

```python
            if action == "ai_script":
                ai_code = step.get("value", "")
                if ai_code:
                    converted = self._sync_to_async(ai_code)
                    for code_line in converted.split("\n"):
                        lines.append(f"    {code_line}" if code_line.strip() else "")
                lines.append("")
                continue
```

这段逻辑仍然正确：
- 旧录制的 step 中 `value` 可能是 sync 代码 → `_sync_to_async` 转换
- 新录制的 step 中 `value` 已经是 async 代码 → `_sync_to_async` 不会重复添加 `await`（因为它检查 `if not stripped.startswith("await ")`）

所以 `_sync_to_async` 方法保留不变，无需修改。

- [ ] **Step 2: Final verification — all modified files syntax check**

Run all syntax checks:
```bash
cd ScienceClaw && python -c "
import ast, sys
files = [
    'backend/rpa/cdp_connector.py',
    'backend/rpa/manager.py',
    'backend/rpa/executor.py',
    'backend/rpa/generator.py',
    'backend/rpa/assistant.py',
    'backend/rpa/skill_exporter.py',
    'backend/rpa/__init__.py',
    'backend/route/rpa.py',
]
ok = True
for f in files:
    try:
        ast.parse(open(f).read())
        print(f'OK: {f}')
    except SyntaxError as e:
        print(f'FAIL: {f} — {e}')
        ok = False
sys.exit(0 if ok else 1)
"
```
Expected: all OK

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "refactor: RPA 系统改为 CDP 复用 sandbox 已有浏览器，去掉 playwright install chromium"
```
