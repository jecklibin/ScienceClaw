import json
import logging
import re
import asyncio
from typing import Dict, List, Any, AsyncGenerator, Optional

from playwright.async_api import Page
from backend.deepagent.engine import get_llm_model

logger = logging.getLogger(__name__)

ELEMENT_EXTRACTION_TIMEOUT_S = 5.0
EXECUTION_TIMEOUT_S = 60.0

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

# JS to extract interactive elements from the page
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
        # Try async def run pattern
        pattern2 = r"(async def run\(page\):.*)"
        match2 = re.search(pattern2, text, re.DOTALL)
        if match2:
            return match2.group(1).strip()
        # Fallback: sync pattern
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
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=2000)
            except Exception:
                # Best-effort only. We still try to inspect the current execution context.
                pass

            result = await asyncio.wait_for(
                page.evaluate(EXTRACT_ELEMENTS_JS),
                timeout=ELEMENT_EXTRACTION_TIMEOUT_S,
            )
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as e:
            logger.warning(f"Failed to extract elements from {page.url!r}: {e}")
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
                    timeout=EXECUTION_TIMEOUT_S,
                )
                return {"success": True, "output": str(ret) if ret else "ok", "error": None}
            else:
                return {"success": False, "output": "", "error": "No run(page) function defined"}

        except asyncio.TimeoutError:
            return {"success": False, "output": "", "error": f"Command execution timed out ({EXECUTION_TIMEOUT_S:.0f}s)"}
        except Exception as e:
            import traceback
            return {"success": False, "output": "", "error": traceback.format_exc()}
        finally:
            # Resume event capture
            try:
                await page.evaluate("window.__rpa_paused = false")
            except Exception:
                pass
