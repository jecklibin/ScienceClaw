from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .recording_ordinal_preplans import _instruction_region_anchors, _normalize_instruction_text


def build_download_preplanned_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    filename = _download_filename(instruction)
    if not filename:
        return None
    text = _normalize_instruction_text(instruction)
    if any(marker in text for marker in ("popup", "new tab", "new window", "新标签", "新窗口", "弹窗")):
        return _build_popup_download_plan(instruction, filename)
    if not _instruction_requests_download(text):
        return None
    if not _instruction_requests_async_generation(text):
        return None
    return _build_same_page_async_download_plan(instruction, filename)


def _download_filename(instruction: str) -> str:
    match = re.search(
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_.-]{2,}\.(?:csv|xlsx|xls|pdf|zip|json|txt))(?=$|[^A-Za-z0-9_-])",
        str(instruction or ""),
        re.I,
    )
    return match.group(1) if match else ""


def _instruction_requests_download(normalized_instruction: str) -> bool:
    return any(
        marker in normalized_instruction
        for marker in (
            "download",
            "下载",
        )
    )


def _instruction_requests_async_generation(normalized_instruction: str) -> bool:
    return any(
        marker in normalized_instruction
        for marker in (
            "generate",
            "create",
            "start",
            "request",
            "build",
            "prepare",
            "poll",
            "ready",
            "refresh",
            "生成",
            "创建",
            "发起",
            "开始",
            "准备",
            "轮询",
            "就绪",
            "刷新",
        )
    )


def _terminal_download_contract() -> Dict[str, Any]:
    return {
        "required": True,
        "kind": "download_created",
        "success_evidence": [{"type": "download_created"}],
        "allow_semantic_judge": False,
    }


def _build_popup_download_plan(instruction: str, filename: str) -> Dict[str, Any]:
    anchors = _instruction_region_anchors(instruction)
    code = (
        "async def run(page, results):\n"
        "    import re as _re\n"
        f"    _filename = {filename!r}\n"
        f"    _region_anchors = {anchors!r}\n"
        "    async def _first_visible(locator, limit=20, require_enabled=True):\n"
        "        count = min(await locator.count(), limit)\n"
        "        for index in range(count):\n"
        "            item = locator.nth(index)\n"
        "            try:\n"
        "                if not await item.is_visible():\n"
        "                    continue\n"
        "                if require_enabled and hasattr(item, 'is_enabled') and not await item.is_enabled():\n"
        "                    continue\n"
        "                return item\n"
        "            except Exception:\n"
        "                continue\n"
        "        return None\n"
        "    async def _region_scope():\n"
        "        for anchor in _region_anchors:\n"
        "            heading = page.get_by_text(anchor, exact=True).first\n"
        "            try:\n"
        "                if await heading.count():\n"
        "                    scope = heading.locator(\"xpath=ancestor-or-self::*[self::section or self::article or self::div][1]\")\n"
        "                    if await scope.count():\n"
        "                        return scope\n"
        "            except Exception:\n"
        "                continue\n"
        "        return page.locator('body')\n"
        "    _scope = await _region_scope()\n"
        "    _open_pattern = _re.compile(r'(popup|new\\s+(tab|window)|open|report|打开|新标签|新窗口|弹窗)', _re.I)\n"
        "    _open_control = await _first_visible(_scope.get_by_role('button', name=_open_pattern))\n"
        "    if _open_control is None:\n"
        "        _open_control = await _first_visible(_scope.get_by_role('link', name=_open_pattern))\n"
        "    if _open_control is None:\n"
        "        controls = _scope.locator('a, button, [role=button], [role=link]')\n"
        "        if await controls.count() == 1:\n"
        "            _open_control = controls.first\n"
        "    if _open_control is None:\n"
        "        raise RuntimeError('No visible popup opener found')\n"
        "    _context = page.context\n"
        "    _before_pages = set(_context.pages)\n"
        "    _popup = None\n"
        "    try:\n"
        "        async with page.expect_popup(timeout=12000) as _popup_info:\n"
        "            await _open_control.click(timeout=5000)\n"
        "        _popup = await _popup_info.value\n"
        "    except Exception:\n"
        "        try:\n"
        "            await _open_control.click(timeout=5000)\n"
        "        except Exception:\n"
        "            pass\n"
        "        for _ in range(30):\n"
        "            _new_pages = [item for item in _context.pages if item not in _before_pages]\n"
        "            if _new_pages:\n"
        "                _popup = _new_pages[-1]\n"
        "                break\n"
        "            await page.wait_for_timeout(300)\n"
        "    if _popup is None:\n"
        "        raise RuntimeError('Popup page did not open')\n"
        "    await _popup.wait_for_load_state('domcontentloaded')\n"
        "    _download_pattern = _re.compile(r'(download|下载|' + _re.escape(_filename) + r')', _re.I)\n"
        "    _download_control = await _first_visible(_popup.get_by_role('button', name=_download_pattern))\n"
        "    if _download_control is None:\n"
        "        _download_control = await _first_visible(_popup.get_by_role('link', name=_download_pattern))\n"
        "    if _download_control is None:\n"
        "        _download_control = await _first_visible(_popup.get_by_text(_filename, exact=False), require_enabled=False)\n"
        "    if _download_control is None:\n"
        "        raise RuntimeError('No visible download control found in popup')\n"
        "    async with _popup.expect_download() as _download_info:\n"
        "        await _download_control.click(timeout=5000)\n"
        "    _download = await _download_info.value\n"
        "    if _download.suggested_filename != _filename:\n"
        "        raise RuntimeError(f'Unexpected downloaded filename: {_download.suggested_filename}')\n"
        "    return {'action_performed': True, 'action_type': 'popup_download', 'filename': _download.suggested_filename, 'download_created': True, 'downloaded': True}\n"
    )
    return {
        "description": "Open popup tab and download requested file",
        "action_type": "run_python",
        "expected_effect": "state_change",
        "output_key": "popup_download",
        "code": code,
        "terminal_contract": _terminal_download_contract(),
        "popup_download": True,
    }


def _build_same_page_async_download_plan(instruction: str, filename: str) -> Dict[str, Any]:
    anchors = _instruction_region_anchors(instruction)
    code = (
        "async def run(page, results):\n"
        "    import re as _re\n"
        "    import time as _time\n"
        f"    _filename = {filename!r}\n"
        f"    _region_anchors = {anchors!r}\n"
        "    async def _first_visible(locator, limit=30, require_enabled=True):\n"
        "        count = min(await locator.count(), limit)\n"
        "        for index in range(count):\n"
        "            item = locator.nth(index)\n"
        "            try:\n"
        "                if not await item.is_visible():\n"
        "                    continue\n"
        "                if require_enabled and hasattr(item, 'is_enabled') and not await item.is_enabled():\n"
        "                    continue\n"
        "                return item\n"
        "            except Exception:\n"
        "                continue\n"
        "        return None\n"
        "    async def _region_scope():\n"
        "        for anchor in _region_anchors:\n"
        "            heading = page.get_by_text(anchor, exact=True).first\n"
        "            try:\n"
        "                if await heading.count():\n"
        "                    scope = heading.locator(\"xpath=ancestor-or-self::*[self::section or self::article or self::div][1]\")\n"
        "                    if await scope.count():\n"
        "                        return scope\n"
        "            except Exception:\n"
        "                continue\n"
        "        return page.locator('body')\n"
        "    async def _find_control(scope, pattern, *, require_enabled=True):\n"
        "        for root in (scope, page):\n"
        "            for role in ('button', 'link'):\n"
        "                try:\n"
        "                    found = await _first_visible(root.get_by_role(role, name=pattern), require_enabled=require_enabled)\n"
        "                    if found is not None:\n"
        "                        return found\n"
        "                except Exception:\n"
        "                    continue\n"
        "        try:\n"
        "            return await _first_visible(scope.get_by_text(pattern), require_enabled=require_enabled)\n"
        "        except Exception:\n"
        "            return None\n"
        "    async def _is_enabled(control):\n"
        "        try:\n"
        "            return bool(control and await control.count() and await control.is_visible() and await control.is_enabled())\n"
        "        except Exception:\n"
        "            return False\n"
        "    _scope = await _region_scope()\n"
        "    _download_pattern = _re.compile(r'(download|下载|' + _re.escape(_filename) + r')', _re.I)\n"
        "    _generate_pattern = _re.compile(r'(generate|create|start|request|build|prepare|run|生成|创建|发起|开始|准备)', _re.I)\n"
        "    _refresh_pattern = _re.compile(r'(refresh|reload|poll|check|刷新|重新加载|轮询|检查)', _re.I)\n"
        "    _download_control = await _find_control(_scope, _download_pattern, require_enabled=False)\n"
        "    if _download_control is None or not await _is_enabled(_download_control):\n"
        "        _generate_control = await _find_control(_scope, _generate_pattern, require_enabled=True)\n"
        "        if _generate_control is not None:\n"
        "            await _generate_control.click(timeout=8000)\n"
        "    _deadline = _time.perf_counter() + 45\n"
        "    _last_state = ''\n"
        "    while _time.perf_counter() < _deadline:\n"
        "        _download_control = await _find_control(_scope, _download_pattern, require_enabled=False)\n"
        "        if _download_control is not None and await _is_enabled(_download_control):\n"
        "            async with page.expect_download() as _download_info:\n"
        "                await _download_control.click(timeout=8000)\n"
        "            _download = await _download_info.value\n"
        "            if _download.suggested_filename != _filename:\n"
        "                raise RuntimeError(f'Unexpected downloaded filename: {_download.suggested_filename}')\n"
        "            return {'action_performed': True, 'action_type': 'async_download', 'filename': _download.suggested_filename, 'download_created': True, 'downloaded': True}\n"
        "        _refresh_control = await _find_control(_scope, _refresh_pattern, require_enabled=True)\n"
        "        if _refresh_control is not None:\n"
        "            await _refresh_control.click(timeout=5000)\n"
        "        try:\n"
        "            _last_state = (await _scope.inner_text(timeout=2000)).strip()[:500]\n"
        "        except Exception:\n"
        "            pass\n"
        "        await page.wait_for_timeout(900)\n"
        "    raise RuntimeError(f'Download control did not become ready for {_filename}: {_last_state}')\n"
    )
    return {
        "description": "Generate or refresh same-page download and save requested file",
        "action_type": "run_python",
        "expected_effect": "state_change",
        "output_key": "same_page_download",
        "code": code,
        "terminal_contract": _terminal_download_contract(),
        "same_page_async_download": True,
    }
