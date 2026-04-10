import json
import logging
import re
from typing import List, Dict, Any, Optional

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


def _rpa_log(message):
    try:
        __rpa_log__(message)
    except NameError:
        print(message)


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v

    cdp_url = await _get_cdp_url()
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = await browser.new_context(no_viewport=True, accept_downloads=True)
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


def _rpa_log(message):
    try:
        __rpa_log__(message)
    except NameError:
        print(message)


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context(no_viewport=True, accept_downloads=True)
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
        deduped = self._deduplicate_steps(steps)
        deduped = self._infer_missing_tab_transitions(deduped)
        deduped = self._infer_missing_navigation_clicks(deduped)
        root_tab_id = deduped[0].get("tab_id") if deduped else None
        root_tab_id = root_tab_id or "tab-1"
        used_result_keys: Dict[str, int] = {}

        lines = [
            "",
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA recording."""',
            "    _results = {}",
            f'    tabs = {{"{root_tab_id}": page}}',
            "    current_page = page",
            "    async def _rpa_debug_locator(label, locator_ref, page_ref):",
            "        try:",
            "            count = await locator_ref.count()",
            "        except Exception as exc:",
            '            _rpa_log(f"[RPA_DEBUG] {label} count_error={exc}")',
            "            count = None",
            "        try:",
            "            visible = await locator_ref.first.is_visible() if count else False",
            "        except Exception as exc:",
            '            _rpa_log(f"[RPA_DEBUG] {label} visible_error={exc}")',
            "            visible = None",
            "        try:",
            "            box = await locator_ref.first.bounding_box() if count else None",
            "        except Exception as exc:",
            '            _rpa_log(f"[RPA_DEBUG] {label} box_error={exc}")',
            "            box = None",
            "        try:",
            "            ready_state = await page_ref.evaluate(\"document.readyState\")",
            "        except Exception as exc:",
            '            _rpa_log(f"[RPA_DEBUG] {label} ready_state_error={exc}")',
            "            ready_state = None",
            "        try:",
            "            loading_count = await page_ref.locator('[aria-busy=\"true\"], [data-loading=\"true\"], .loading, .spinner, .ant-spin-spinning, .el-loading-mask').count()",
            "        except Exception as exc:",
            '            _rpa_log(f"[RPA_DEBUG] {label} loading_error={exc}")',
            "            loading_count = None",
            "        top_element = None",
            "        if box:",
            "            try:",
            "                center_x = box['x'] + box['width'] / 2",
            "                center_y = box['y'] + box['height'] / 2",
            "                top_element = await page_ref.evaluate(",
            "                    '''([x, y]) => {",
            "                        const el = document.elementFromPoint(x, y);",
            "                        if (!el) return null;",
            "                        return {",
            "                            tag: el.tagName.toLowerCase(),",
            "                            text: (el.textContent || '').trim().slice(0, 80),",
            "                            classes: (typeof el.className === 'string' ? el.className.trim().split(/\\s+/).filter(Boolean).slice(0, 4) : []),",
            "                            ariaBusy: el.getAttribute('aria-busy') || '',",
            "                        };",
            "                    }''',",
            "                    [center_x, center_y],",
            "                )",
            "            except Exception as exc:",
            '                _rpa_log(f"[RPA_DEBUG] {label} top_element_error={exc}")',
            "        _rpa_log(",
            "            '[RPA_DEBUG] ' + _json.dumps({",
            "                'label': label,",
            "                'url': page_ref.url,",
            "                'readyState': ready_state,",
            "                'count': count,",
            "                'visible': visible,",
            "                'box': box,",
            "                'loadingCount': loading_count,",
            "                'topElement': top_element,",
            "            }, ensure_ascii=False, default=str)",
            "        )",
        ]

        current_tab_id = root_tab_id
        prev_url = None
        prev_action = None
        # Add initial navigation if first step isn't a navigate action
        if deduped and deduped[0].get("action") not in ("navigate", "goto"):
            first_url = deduped[0].get("url", "")
            if first_url:
                lines.append(f'    await current_page.goto("{first_url}")')
                lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                lines.append("")
                prev_url = first_url

        for step_index, step in enumerate(deduped, 1):
            action = step.get("action", "")
            target = step.get("target", "")
            value = step.get("value", "")
            url = step.get("url", "")
            desc = step.get("description", "")
            frame_path = step.get("frame_path") or []

            if desc:
                lines.append(f"    # {desc}")

            # AI-generated script — embed directly with sync→async conversion
            if action == "ai_script":
                ai_code = step.get("value", "")
                if ai_code:
                    converted = self._sync_to_async(ai_code)
                    converted = self._inject_result_capture(converted)
                    converted = self._strip_locator_result_capture(converted)
                    for code_line in converted.split("\n"):
                        lines.append(f"    {code_line}" if code_line.strip() else "")
                lines.append("")
                continue

            # Navigation
            if action == "navigate" or (action == "goto" and url):
                lines.append(f'    await current_page.goto("{url}")')
                lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                prev_url = url
                prev_action = "navigate"
                lines.append("")
                continue

            if action == "switch_tab":
                target_tab_id = step.get("target_tab_id") or step.get("tab_id") or root_tab_id
                lines.append(f'    current_page = tabs["{target_tab_id}"]')
                lines.append("    await current_page.bring_to_front()")
                lines.append("")
                current_tab_id = target_tab_id
                prev_action = action
                continue

            if action == "close_tab":
                closing_tab_id = step.get("tab_id") or step.get("source_tab_id")
                fallback_tab_id = step.get("target_tab_id")
                if closing_tab_id:
                    lines.append(f'    closing_page = tabs.pop("{closing_tab_id}", current_page)')
                else:
                    lines.append("    closing_page = current_page")
                lines.append("    await closing_page.close()")
                if closing_tab_id == current_tab_id:
                    if fallback_tab_id:
                        lines.append(f'    current_page = tabs["{fallback_tab_id}"]')
                        lines.append("    await current_page.bring_to_front()")
                        current_tab_id = fallback_tab_id
                    else:
                        current_tab_id = closing_tab_id
                lines.append("")
                prev_action = action
                continue

            # Standalone download step has no locator — handle before _build_locator
            if action == "download":
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', (value or "file").split('.')[0]) or "file"
                lines.append(f'    # NOTE: download of "{value}" was triggered by a previous action')
                lines.append(f'    # If this step appears, manually wrap the triggering click with expect_download()')
                lines.append("")
                continue

            scope_var = "current_page"
            if frame_path:
                scope_var = "frame_scope"
                frame_parent = "current_page"
                for frame_selector in frame_path:
                    lines.append(
                        f'    frame_scope = {frame_parent}.frame_locator("{self._escape(frame_selector)}")'
                    )
                    frame_parent = "frame_scope"

            # Prefer adaptive collection locators for AI steps like "click the first item".
            locator = self._build_adaptive_locator_for_step(step, scope_var)
            if not locator:
                locator_meta = self._resolve_replay_locator(step)
                if locator_meta["used_first_fallback"]:
                    lines.append("    # Fallback: recorder candidates were ambiguous, using first matching locator")
                locator = self._build_locator_for_page(locator_meta["payload"], scope_var)
                if locator_meta["used_first_fallback"]:
                    locator = f"{locator}.first"

            if action == "open_tab_click":
                target_tab_id = step.get("target_tab_id") or step.get("tab_id") or "tab-new"
                lines.extend(self._build_locator_ready_steps(locator))
                lines.append(f'    await _rpa_debug_locator("open_tab_click.before", {locator}, current_page)')
                lines.append("    try:")
                lines.append("        async with current_page.expect_popup() as popup_info:")
                lines.append(f"            await {locator}.click()")
                lines.append("    except Exception:")
                lines.append(f'        await _rpa_debug_locator("open_tab_click.error", {locator}, current_page)')
                lines.append("        raise")
                lines.append("    new_page = await popup_info.value")
                lines.append('    await new_page.wait_for_load_state("domcontentloaded")')
                lines.append(f'    tabs["{target_tab_id}"] = new_page')
                lines.append("    current_page = new_page")
                lines.append("")
                current_tab_id = target_tab_id
                prev_action = action
                continue

            if action == "navigate_click":
                # TTI-like layered waiting with navigation verification.
                # NOTE: avoid expect_navigation — it can capture spurious navigation events
                # (e.g. iframe reloads, pushState) and never resolve for the real one.
                expected_url = self._escape(step.get("expected_url") or step.get("url") or "")
                lines.extend(self._build_locator_ready_steps(locator))
                lines.append(f'    await _rpa_debug_locator("navigate_click.before", {locator}, current_page)')
                lines.append("    try:")
                lines.append(f"        _nav_before_url = current_page.url")
                lines.append(f"        await {locator}.click()")
                if expected_url:
                    lines.append("        try:")
                    lines.append(f'            await current_page.wait_for_url("{expected_url}", timeout={RPA_NAVIGATION_TIMEOUT_MS // 2})')
                    lines.append("        except Exception:")
                    lines.append(f"            await current_page.wait_for_load_state('domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS // 2})")
                else:
                    lines.append(f"        await current_page.wait_for_load_state('domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS // 2})")
                lines.append("        try:")
                lines.append(f"            await current_page.wait_for_load_state('networkidle', timeout=15000)")
                lines.append("        except Exception:")
                lines.append("            pass  # best-effort TTI: some pages never reach networkidle")
                lines.append("        if current_page.url == _nav_before_url:")
                lines.extend(self._build_locator_ready_steps(locator, indent_level=3))
                lines.append(f"            await {locator}.click()")
                if expected_url:
                    lines.append("            try:")
                    lines.append(f'                await current_page.wait_for_url("{expected_url}", timeout={RPA_NAVIGATION_TIMEOUT_MS // 2})')
                    lines.append("            except Exception:")
                    lines.append(f"                await current_page.wait_for_load_state('domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS // 2})")
                    lines.append(f'            if current_page.url != "{expected_url}":')
                    lines.append(f'                raise RuntimeError("Click did not reach expected URL: {expected_url}")')
                else:
                    lines.append(f"            await current_page.wait_for_load_state('domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS // 2})")
                lines.append("    except Exception:")
                lines.append(f'        await _rpa_debug_locator("navigate_click.error", {locator}, current_page)')
                lines.append('        _rpa_log(f"[RPA_DEBUG] navigate_click url={current_page.url}")')
                lines.append("        raise")
            elif action == "click":
                lines.extend(self._build_locator_ready_steps(locator))
                lines.append(f'    await _rpa_debug_locator("click.before", {locator}, current_page)')
                lines.append("    try:")
                lines.append(f"        await {locator}.click()")
                lines.append("        await current_page.wait_for_timeout(500)")
                lines.append("    except Exception:")
                lines.append(f'        await _rpa_debug_locator("click.error", {locator}, current_page)')
                lines.append('        _rpa_log(f"[RPA_DEBUG] click url={current_page.url}")')
                lines.append("        raise")
            elif action == "download_click":
                # Click that triggers a file download — wrap with expect_download
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', (value or "file").split('.')[0]) or "file"
                lines.extend(self._build_locator_ready_steps(locator))
                lines.append(f'    await _rpa_debug_locator("download_click.before", {locator}, current_page)')
                lines.append("    try:")
                lines.append(f"        async with current_page.expect_download() as _dl_info:")
                lines.append(f"            await {locator}.click()")
                lines.append("    except Exception:")
                lines.append(f'        await _rpa_debug_locator("download_click.error", {locator}, current_page)')
                lines.append("        raise")
                lines.append(f"    _dl = await _dl_info.value")
                lines.append(f"    _dl_dir = kwargs.get('_downloads_dir', '.')")
                lines.append(f"    import os as _os; _os.makedirs(_dl_dir, exist_ok=True)")
                lines.append(f"    _dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)")
                lines.append(f"    await _dl.save_as(_dl_dest)")
                lines.append(f'    _results["download_{safe_name}"] = {{"filename": _dl.suggested_filename, "path": _dl_dest}}')
            elif action == "fill":
                fill_value = self._maybe_parameterize(value, params)
                lines.append(f"    await {locator}.fill({fill_value})")
            elif action == "extract_text":
                result_var = f"extract_text_value_{step_index}"
                result_key = self._build_extract_result_key(step, used_result_keys)
                lines.append(f"    {result_var} = await {locator}.inner_text()")
                lines.append(f'    _results["{result_key}"] = {result_var}')
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

    def _build_extract_result_key(self, step: Dict[str, Any], used_result_keys: Dict[str, int]) -> str:
        key = self._normalize_result_key(step.get("result_key"))
        if not key:
            fallback_count = used_result_keys.get("extract_text", 0) + 1
            used_result_keys["extract_text"] = fallback_count
            return f"extract_text_{fallback_count}"

        count = used_result_keys.get(key, 0) + 1
        used_result_keys[key] = count
        if count == 1:
            return key
        return f"{key}_{count}"

    @staticmethod
    def _build_locator_ready_steps(locator: str, indent_level: int = 1) -> List[str]:
        indent = "    " * indent_level
        return [
            f'{indent}await {locator}.wait_for(state="visible", timeout={RPA_NAVIGATION_TIMEOUT_MS // 2})',
            f"{indent}await {locator}.scroll_into_view_if_needed()",
        ]

    def _normalize_result_key(self, raw_key: Any) -> str:
        text = str(raw_key or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"[^a-z0-9_]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        if not text:
            return ""
        if text[0].isdigit():
            text = f"extract_{text}"
        return text[:64]

    @staticmethod
    def _infer_missing_tab_transitions(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Backfill tab open/switch semantics for older recordings that only carry tab_id."""
        if not steps:
            return steps

        normalized: List[Dict[str, Any]] = []
        current_tab_id = steps[0].get("tab_id") or "tab-1"
        known_tabs = {current_tab_id}

        for original_step in steps:
            step = dict(original_step)
            step_tab_id = step.get("tab_id") or current_tab_id

            if step_tab_id != current_tab_id:
                previous_step = normalized[-1] if normalized else None
                if step_tab_id not in known_tabs and previous_step and previous_step.get("action") == "click":
                    previous_step["action"] = "open_tab_click"
                    previous_step["source_tab_id"] = current_tab_id
                    previous_step["target_tab_id"] = step_tab_id
                    known_tabs.add(step_tab_id)
                elif step_tab_id in known_tabs:
                    normalized.append(
                        {
                            "action": "switch_tab",
                            "tab_id": current_tab_id,
                            "target_tab_id": step_tab_id,
                            "description": "Switch tab",
                            "url": step.get("url", ""),
                        }
                    )
                else:
                    known_tabs.add(step_tab_id)

                current_tab_id = step_tab_id

            previous_step = normalized[-1] if normalized else None
            if (
                step.get("action") == "navigate"
                and previous_step
                and previous_step.get("action") == "open_tab_click"
                and previous_step.get("target_tab_id") == step_tab_id
            ):
                continue

            normalized.append(step)

        return normalized

    @staticmethod
    def _infer_missing_navigation_clicks(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Upgrade plain clicks to navigate_click when later evidence shows same-tab navigation."""
        if not steps:
            return steps

        normalized: List[Dict[str, Any]] = []
        total = len(steps)
        for index, original_step in enumerate(steps):
            step = dict(original_step)
            if step.get("action") != "click":
                normalized.append(step)
                continue

            current_url = str(step.get("url") or "").strip()
            expected_url = PlaywrightGenerator._expected_url_for_click(step)

            if not expected_url and index + 1 < total:
                next_step = steps[index + 1]
                same_tab = (next_step.get("tab_id") or step.get("tab_id")) == step.get("tab_id")
                next_action = next_step.get("action")
                next_url = str(next_step.get("url") or "").strip()
                if (
                    same_tab
                    and next_url
                    and next_url != current_url
                    and next_action not in ("navigate", "goto", "switch_tab", "close_tab")
                ):
                    expected_url = next_url

            if expected_url and expected_url != current_url:
                step["action"] = "navigate_click"
                step["expected_url"] = expected_url

            normalized.append(step)

        return normalized

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

        if method == "collection_item":
            collection = loc.get("collection", {"method": "css", "value": "body"})
            item = loc.get("item", {"method": "css", "value": "body"})
            ordinal = str(loc.get("ordinal") or "first")
            collection_loc = self._build_locator(json.dumps(collection) if isinstance(collection, dict) else str(collection))
            scoped_collection = self._apply_ordinal_to_locator(collection_loc, ordinal)
            item_loc = self._build_locator(json.dumps(item) if isinstance(item, dict) else str(item))
            if item_loc.startswith("page."):
                return f'{scoped_collection}{item_loc[len("page"):]}'
            return f'{scoped_collection}.locator("{self._escape(str(item))}")'

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
    def _expected_url_for_click(step: Dict[str, Any]) -> str:
        snapshot = step.get("element_snapshot") or {}
        tag = str((snapshot.get("tag") or step.get("tag") or "")).strip().lower()
        href = str(snapshot.get("href") or "").strip()
        target_attr = str(snapshot.get("target_attr") or "").strip().lower()
        if tag != "a" or not href:
            return ""
        if href.startswith("#") or href.startswith("javascript:"):
            return ""
        if target_attr == "_blank":
            return ""
        return href

    def _parse_locator_payload(self, target: Any) -> Dict[str, Any]:
        try:
            payload = json.loads(target) if isinstance(target, str) else target
        except (json.JSONDecodeError, TypeError):
            payload = {"method": "css", "value": str(target or "body")}
        if not isinstance(payload, dict):
            return {"method": "css", "value": str(target or "body")}
        return payload

    def _resolve_replay_locator(self, step: Dict[str, Any]) -> Dict[str, Any]:
        primary_payload = self._parse_locator_payload(step.get("target", ""))
        validation = step.get("validation") or {}
        candidates = step.get("locator_candidates") or []

        if validation.get("status") == "ok":
            return {"payload": primary_payload, "used_first_fallback": False}

        unique_candidates = [
            candidate
            for candidate in candidates
            if candidate.get("strict_match_count") == 1 and isinstance(candidate.get("locator"), dict)
        ]
        if unique_candidates:
            return {
                "payload": self._choose_best_replay_candidate(unique_candidates).get("locator", primary_payload),
                "used_first_fallback": False,
            }

        structured_candidates = [
            candidate
            for candidate in candidates
            if candidate.get("kind") == "nested" and isinstance(candidate.get("locator"), dict)
        ]
        if structured_candidates:
            return {
                "payload": self._choose_best_replay_candidate(structured_candidates).get("locator", primary_payload),
                "used_first_fallback": False,
            }

        ambiguous_candidates = [
            candidate for candidate in candidates if isinstance(candidate.get("locator"), dict)
        ]
        if ambiguous_candidates:
            return {
                "payload": self._choose_best_replay_candidate(ambiguous_candidates).get("locator", primary_payload),
                "used_first_fallback": True,
            }

        return {"payload": primary_payload, "used_first_fallback": False}

    @staticmethod
    def _choose_best_replay_candidate(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        priority = {
            "nested": 0,
            "role": 1,
            "testid": 2,
            "label": 3,
            "placeholder": 4,
            "alt": 5,
            "text": 6,
            "title": 7,
            "css": 8,
        }
        return sorted(
            candidates,
            key=lambda candidate: (
                priority.get(candidate.get("kind", ""), 99),
                candidate.get("score", 999),
            ),
        )[0]

    def _build_locator_for_page(self, target: str, page_var: str) -> str:
        locator = self._build_locator(target)
        if page_var == "page":
            return locator
        if locator.startswith("page."):
            return f"{page_var}.{locator[len('page.'):]}"
        return locator

    def _build_adaptive_locator_for_step(self, step: Dict[str, Any], page_var: str) -> Optional[str]:
        ordinal = step.get("ordinal")
        if not ordinal:
            return None

        collection_hint = step.get("collection_hint") or {}
        item_hint = step.get("item_hint") or {}
        collection_locator = (collection_hint.get("container_hint") or {}).get("locator")
        item_locator = item_hint.get("locator")
        adaptive_target: Optional[Dict[str, Any]] = None
        if collection_locator and item_locator:
            adaptive_target = {
                "method": "collection_item",
                "collection": collection_locator,
                "item": item_locator,
                "ordinal": str(ordinal),
            }
        elif item_locator:
            adaptive_target = item_locator
        elif item_hint.get("role"):
            adaptive_target = {"method": "role", "role": item_hint["role"]}

        if not adaptive_target:
            return None

        locator = self._build_locator_for_page(json.dumps(adaptive_target), page_var)
        if adaptive_target.get("method") == "collection_item":
            return locator
        return self._apply_ordinal_to_locator(locator, str(ordinal))

    @staticmethod
    def _apply_ordinal_to_locator(locator: str, ordinal: str) -> str:
        normalized = (ordinal or "first").strip().lower()
        if normalized == "first":
            return f"{locator}.first"
        if normalized == "last":
            return f"{locator}.last"
        try:
            index = max(int(normalized) - 1, 0)
        except Exception:
            index = 0
        return f"{locator}.nth({index})"

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
                if stripped.startswith("page.") or _re.match(r'^(\w[\w\s,]*=\s*)(await\s+)?(page\..+)$', stripped):
                    if not stripped.startswith("await "):
                        # Check for assignment: var = page.xxx or var = await page.xxx
                        assign_match = _re.match(r'^(\w[\w\s,]*=\s*)(await\s+)?(page\..+)$', stripped)
                        if assign_match:
                            last_call = _re.search(r'\.(\w+)\([^)]*\)\s*$', assign_match.group(3))
                            if last_call and last_call.group(1) in PlaywrightGenerator._LOCATOR_BUILDER_METHODS:
                                result.append(f"{indent}{assign_match.group(1)}{assign_match.group(3)}")
                                continue
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

    _LOCATOR_BUILDER_METHODS = frozenset({
        'locator', 'frame_locator',
        'get_by_role', 'get_by_text', 'get_by_label', 'get_by_placeholder',
        'get_by_alt_text', 'get_by_title', 'get_by_test_id',
        'nth', 'first', 'last', 'filter',
    })

    _ASSIGN_RE = re.compile(r'^(\w+)\s*=\s*(?:await\s+)?page\.')
    _GENERIC_ASSIGN_RE = re.compile(r'^(?P<var>\w+)\s*=\s*(?:await\s+)?(?P<rhs>.+)$')
    _RESULT_CAPTURE_RE = re.compile(r'^_results\["[^"]+"\]\s*=\s*(?P<var>\w+)\s*$')

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
            if last_call and last_call.group(1) in cls._LOCATOR_BUILDER_METHODS:
                continue
            if last_call and last_call.group(1) in cls._ACTION_METHODS:
                continue
            indent = line[:len(line) - len(line.lstrip())]
            result.append(f'{indent}_results["{var_name}"] = {var_name}')
        return '\n'.join(result)

    @classmethod
    def _strip_locator_result_capture(cls, code: str) -> str:
        """Drop `_results[...] = var` lines when `var` still points to a locator builder."""
        lines = code.split('\n')
        result = []
        locator_vars = set()
        for line in lines:
            stripped = line.strip()
            assign_match = cls._GENERIC_ASSIGN_RE.match(stripped)
            if assign_match:
                var_name = assign_match.group("var")
                rhs = assign_match.group("rhs")
                last_call = re.search(r'\.(\w+)\([^)]*\)\s*$', rhs)
                if last_call and last_call.group(1) in cls._LOCATOR_BUILDER_METHODS:
                    locator_vars.add(var_name)
                else:
                    locator_vars.discard(var_name)

            capture_match = cls._RESULT_CAPTURE_RE.match(stripped)
            if capture_match and capture_match.group("var") in locator_vars:
                continue

            result.append(line)
        return '\n'.join(result)
