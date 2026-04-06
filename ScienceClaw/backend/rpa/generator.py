import json
import logging
import re
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

RPA_PLAYWRIGHT_TIMEOUT_MS = 60000
RPA_NAVIGATION_TIMEOUT_MS = 60000


class PlaywrightGenerator:
    """Generate Playwright Python scripts from recorded RPA steps.

    Locators are pre-computed in the browser using a Playwright-codegen-style
    algorithm (role > testid > label > placeholder > alt > title > css).
    The generator simply translates the locator objects into Playwright API calls.
    """

    # Docker mode: connects to sandbox's browser via CDP
    RUNNER_TEMPLATE_DOCKER = '''\
import asyncio
import json as _json
import sys
import httpx
from playwright.async_api import async_playwright


async def _get_cdp_url() -> str:
    """Fetch CDP WebSocket URL from the local sandbox API."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("http://127.0.0.1:8080/v1/browser/info")
        resp.raise_for_status()
        return resp.json()["data"]["cdp_url"]


{execute_skill_func}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v

    cdp_url = await _get_cdp_url()
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = await browser.new_context(no_viewport=True)
    page = await context.new_page()
    page.set_default_timeout({default_timeout_ms})
    page.set_default_navigation_timeout({navigation_timeout_ms})
    try:
        _result = await execute_skill(page, **kwargs)
        if _result:
            print("SKILL_DATA:" + _json.dumps(_result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as e:
        print(f"SKILL_ERROR: {{e}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''

    # Local mode: launches local browser directly
    RUNNER_TEMPLATE_LOCAL = '''\
import asyncio
import json as _json
import sys
from playwright.async_api import async_playwright


{execute_skill_func}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context(no_viewport=True)
    page = await context.new_page()
    page.set_default_timeout({default_timeout_ms})
    page.set_default_navigation_timeout({navigation_timeout_ms})
    try:
        _result = await execute_skill(page, **kwargs)
        if _result:
            print("SKILL_DATA:" + _json.dumps(_result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as e:
        print(f"SKILL_ERROR: {{e}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''

    def generate_script(self, steps: List[Dict[str, Any]], params: Dict[str, Any] = None, is_local: bool = False) -> str:
        params = params or {}

        lines = [
            "",
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA recording."""',
            "    _results = {}",
        ]

        # Deduplicate consecutive identical actions
        deduped = self._deduplicate_steps(steps)

        prev_url = None
        prev_action = None
        # Add initial navigation if first step isn't a navigate action
        if deduped and deduped[0].get("action") not in ("navigate", "goto"):
            first_url = deduped[0].get("url", "")
            if first_url:
                lines.append(f'    await page.goto("{first_url}")')
                lines.append('    await page.wait_for_load_state("domcontentloaded")')
                lines.append("")
                prev_url = first_url

        for step in deduped:
            action = step.get("action", "")
            target = step.get("target", "")
            value = step.get("value", "")
            url = step.get("url", "")
            desc = step.get("description", "")

            if desc:
                lines.append(f"    # {desc}")

            # AI-generated script — embed directly with sync→async conversion
            if action == "ai_script":
                ai_code = step.get("value", "")
                if ai_code:
                    converted = self._sync_to_async(ai_code)
                    converted = self._inject_result_capture(converted)
                    for code_line in converted.split("\n"):
                        lines.append(f"    {code_line}" if code_line.strip() else "")
                lines.append("")
                continue

            # Navigation
            if action == "navigate" or (action == "goto" and url):
                lines.append(f'    await page.goto("{url}")')
                lines.append('    await page.wait_for_load_state("domcontentloaded")')
                prev_url = url
                prev_action = "navigate"
                lines.append("")
                continue

            # Parse the locator object from target (stored as JSON string)
            locator = self._build_locator(target)

            if action == "click":
                tag = step.get("tag", "")
                # Check if this click is on a link (may trigger navigation)
                is_link = tag.upper() == "A"
                # Also check if the locator itself indicates a link
                try:
                    loc_obj = json.loads(target) if isinstance(target, str) else target
                    if isinstance(loc_obj, dict) and loc_obj.get("role") == "link":
                        is_link = True
                except (json.JSONDecodeError, TypeError):
                    pass

                if is_link:
                    # Use expect_navigation pattern for link clicks
                    lines.append(f"    async with page.expect_navigation(wait_until='domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS}):")
                    lines.append(f"        await {locator}.click()")
                else:
                    lines.append(f"    await {locator}.click()")
                    # After non-navigation click, wait briefly for UI changes
                    lines.append("    await page.wait_for_timeout(500)")
            elif action == "fill":
                fill_value = self._maybe_parameterize(value, params)
                lines.append(f"    await {locator}.fill({fill_value})")
            elif action == "press":
                lines.append(f'    await {locator}.press("{value}")')
            elif action == "select":
                lines.append(f'    await {locator}.select_option("{value}")')

            prev_action = action
            lines.append("")

        lines.append("    return _results")

        # Wrap execute_skill function with the runner boilerplate
        execute_skill_func = "\n".join(lines)
        template = self.RUNNER_TEMPLATE_LOCAL if is_local else self.RUNNER_TEMPLATE_DOCKER
        return template.format(
            execute_skill_func=execute_skill_func,
            default_timeout_ms=RPA_PLAYWRIGHT_TIMEOUT_MS,
            navigation_timeout_ms=RPA_NAVIGATION_TIMEOUT_MS,
        )

    @staticmethod
    def _deduplicate_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove consecutive duplicate actions (same action + same target).

        For fill actions on the same target, keep the LAST one (final typed value).
        """
        if not steps:
            return steps
        result = [steps[0]]
        for step in steps[1:]:
            prev = result[-1]
            # Same action and same target → replace with the later one
            # (keeps the final/complete value for fill actions)
            # BUT: never deduplicate AI steps (each AI instruction is unique)
            if (step.get("action") == prev.get("action")
                    and step.get("target") == prev.get("target")
                    and step.get("action") not in ("navigate", "ai_script")):
                result[-1] = step  # Replace previous with current (keep last)
                continue
            result.append(step)
        return result

    def _build_locator(self, target: str) -> str:
        """Convert a locator JSON object (from browser capture) to Playwright API call.

        The locator object has a 'method' field indicating the strategy:
          role     → page.get_by_role(role, name=name, exact=True)
          testid   → page.get_by_test_id(value)
          label    → page.get_by_label(value, exact=True)
          placeholder → page.get_by_placeholder(value, exact=True)
          alt      → page.get_by_alt_text(value, exact=True)
          title    → page.get_by_title(value, exact=True)
          css      → page.locator(css_selector)
        """
        try:
            loc = json.loads(target) if isinstance(target, str) else target
        except (json.JSONDecodeError, TypeError):
            # Fallback: treat as raw CSS selector
            if target:
                return f'page.locator("{self._escape(target)}")'
            return 'page.locator("body")'

        if not isinstance(loc, dict):
            return f'page.locator("{self._escape(str(target))}")'

        method = loc.get("method", "css")

        if method == "role":
            role = loc.get("role", "button")
            name = self._escape(loc.get("name", ""))
            if name:
                return f'page.get_by_role("{role}", name="{name}", exact=True)'
            return f'page.get_by_role("{role}")'

        if method == "testid":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_test_id("{val}")'

        if method == "label":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_label("{val}", exact=True)'

        if method == "placeholder":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_placeholder("{val}", exact=True)'

        if method == "alt":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_alt_text("{val}", exact=True)'

        if method == "title":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_title("{val}", exact=True)'

        if method == "text":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_text("{val}", exact=True)'

        if method == "nested":
            # parent >> child locator chaining
            parent = loc.get("parent", {})
            child = loc.get("child", {})
            parent_loc = self._build_locator(json.dumps(parent) if isinstance(parent, dict) else str(parent))
            child_loc = self._build_locator(json.dumps(child) if isinstance(child, dict) else str(child))
            # Chain the child query directly on the parent locator.
            # Examples:
            #   page.locator("button")        -> parent.locator("button")
            #   page.get_by_role("link")      -> parent.get_by_role("link")
            # Wrapping everything in .locator(...) breaks non-CSS child locators.
            if child_loc.startswith("page."):
                return f'{parent_loc}{child_loc[len("page"):]}'
            return f'{parent_loc}.locator("{self._escape(str(child))}")'

        # css (default)
        val = self._escape(loc.get("value", "body"))
        return f'page.locator("{val}")'

    @staticmethod
    def _escape(s: str) -> str:
        """Escape and normalize a string for embedding in Python source code."""
        # Collapse all whitespace (newlines, tabs, multiple spaces) into single space
        import re
        s = re.sub(r'\s+', ' ', s).strip()
        return s.replace('\\', '\\\\').replace('"', '\\"')

    def _maybe_parameterize(self, value: str, params: Dict[str, Any]) -> str:
        """Check if value should be a parameter."""
        for param_name, param_info in params.items():
            if param_info.get("original_value") == value:
                if param_info.get("sensitive"):
                    # No default value for sensitive params
                    return f"kwargs['{param_name}']"
                return f"kwargs.get('{param_name}', '{value}')"
        safe = value.replace("'", "\\'")
        return f"'{safe}'"

    @staticmethod
    def _sync_to_async(code: str) -> str:
        """Convert Playwright sync API code to async by adding await."""
        import re as _re
        lines = code.split("\n")
        result = []
        for line in lines:
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]
            if stripped and not stripped.startswith("#") and not stripped.startswith("def "):
                if _re.search(r'\bpage\.', stripped):
                    if not stripped.startswith("await "):
                        # Check for assignment: var = page.xxx or var = await page.xxx
                        assign_match = _re.match(r'^(\w[\w\s,]*=\s*)(await\s+)?(page\..+)$', stripped)
                        if assign_match:
                            # If already has await, keep as-is; otherwise add await
                            if assign_match.group(2):  # already has await
                                result.append(line)
                            else:
                                result.append(f"{indent}{assign_match.group(1)}await {assign_match.group(3)}")
                            continue
                        result.append(f"{indent}await {stripped}")
                        continue
            result.append(line)
        return "\n".join(result)

    # Methods whose return value is not data (actions, not queries)
    _ACTION_METHODS = frozenset({
        'click', 'dblclick', 'fill', 'press', 'type', 'check', 'uncheck',
        'select_option', 'set_input_files', 'hover', 'focus', 'blur',
        'dispatch_event', 'scroll_into_view_if_needed',
        'goto', 'go_back', 'go_forward', 'reload',
        'wait_for_timeout', 'wait_for_load_state', 'wait_for_selector',
        'wait_for_url', 'wait_for_event', 'wait_for_function',
        'bring_to_front', 'close', 'set_content',
        'set_default_timeout', 'set_default_navigation_timeout',
        'add_init_script', 'expose_function', 'route', 'unroute',
    })

    _ASSIGN_RE = re.compile(r'^(\w+)\s*=\s*(?:await\s+)?page\.')

    @classmethod
    def _inject_result_capture(cls, code: str) -> str:
        """After data-extraction assignments, inject _results[var] = var."""
        lines = code.split('\n')
        result = []
        for line in lines:
            result.append(line)
            stripped = line.strip()
            m = cls._ASSIGN_RE.match(stripped)
            if not m:
                continue
            var_name = m.group(1)
            # Find the last method call in the line
            last_call = re.search(r'\.(\w+)\([^)]*\)\s*$', stripped)
            if last_call and last_call.group(1) in cls._ACTION_METHODS:
                continue
            indent = line[:len(line) - len(line.lstrip())]
            result.append(f'{indent}_results["{var_name}"] = {var_name}')
        return '\n'.join(result)
