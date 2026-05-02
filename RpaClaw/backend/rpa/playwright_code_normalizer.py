from __future__ import annotations

import re


_BARE_TEXT_CLICK_RE = re.compile(
    r"(?P<locator>\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*(?:\([^)\n]*\))?)*"
    r"\.get_by_text\([^)\n]*\))\.click\("
)

_RPA_FILL_HELPER = """async def _rpa_fill(locator, value, *args, **kwargs):
    editable_selector = "input, textarea, select, [contenteditable=true], [role=textbox], [role=spinbutton]"
    fill_timeout = kwargs.pop("timeout", 5000)

    async def _is_editable(node):
        try:
            return await node.evaluate(\"\"\"el => {
                const tag = (el.tagName || '').toLowerCase();
                const role = (el.getAttribute('role') || '').toLowerCase();
                const contentEditable = (el.getAttribute('contenteditable') || '').toLowerCase();
                if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
                if (contentEditable === 'true') return true;
                if (tag === 'select') return true;
                if ((tag === 'input' || tag === 'textarea') && !el.readOnly) return true;
                return (role === 'textbox' || role === 'spinbutton') && !el.readOnly;
            }\"\"\")
        except Exception:
            return False

    async def _sync_framework_input_events(node):
        try:
            await node.evaluate(\"\"\"el => {
                el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                if (typeof el.blur === 'function') el.blur();
            }\"\"\")
        except Exception:
            pass

    async def _try_fill(node):
        try:
            if not await node.count() or not await node.is_visible() or not await node.is_enabled():
                return False
            if not await _is_editable(node):
                return False
            tag_name = await node.evaluate("el => (el.tagName || '').toLowerCase()")
            if tag_name == "select":
                try:
                    await node.select_option(label=str(value), timeout=fill_timeout)
                except Exception:
                    await node.select_option(value=str(value), timeout=fill_timeout)
            else:
                await node.fill(str(value), *args, timeout=fill_timeout, **kwargs)
            await _sync_framework_input_events(node)
            return True
        except Exception:
            return False

    async def _is_combobox(node):
        try:
            return await node.evaluate(\"\"\"el => {
                const role = (el.getAttribute('role') || '').toLowerCase();
                const popup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
                return role === 'combobox' || popup === 'listbox';
            }\"\"\")
        except Exception:
            return False

    async def _try_select_combobox(scope):
        async def _open_combobox(combobox):
            try:
                await combobox.click(timeout=fill_timeout)
                return True
            except Exception:
                pass
            try:
                return await combobox.evaluate(\"\"\"el => {
                    const target = el.closest('[role=combobox], [aria-haspopup=listbox]') || el;
                    for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                        target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
                    }
                    return true;
                }\"\"\")
            except Exception:
                return False

        async def _combobox_matches_value(combobox):
            try:
                candidates = await combobox.evaluate(\"\"\"(el, expected) => {
                    const normalize = value => String(value || '').replace(/\\\\s+/g, ' ').trim();
                    const root = el.closest('[role=combobox], [aria-haspopup=listbox]') || el;
                    const values = [];
                    for (const node of [el, root]) {
                        if (!node) continue;
                        if ('value' in node) values.push(node.value);
                        values.push(node.getAttribute('aria-label'));
                        values.push(node.getAttribute('title'));
                        values.push(node.textContent);
                        const selected = node.querySelector && node.querySelector('option:checked');
                        if (selected) {
                            values.push(selected.textContent);
                            values.push(selected.value);
                        }
                    }
                    const expectedText = normalize(expected).toLowerCase();
                    return values
                        .map(normalize)
                        .filter(Boolean)
                        .some(value => {
                            const text = value.toLowerCase();
                            return text === expectedText || text.includes(expectedText);
                        });
                }\"\"\", str(value))
                return bool(candidates)
            except Exception:
                return False

        try:
            combobox = scope
            if not await combobox.count() or not await _is_combobox(combobox):
                combobox = scope.locator("[role=combobox], [aria-haspopup=listbox]").first
            if not await combobox.count() or not await combobox.is_visible() or not await combobox.is_enabled():
                return False
            if not await _open_combobox(combobox):
                return False
            page = getattr(combobox, "page", None) or getattr(scope, "page", None)
            if page is not None:
                for exact in (True, False):
                    option = page.get_by_role("option", name=str(value), exact=exact).first
                    try:
                        if await option.count() and await option.is_visible():
                            await option.click(timeout=fill_timeout)
                            return True
                    except Exception:
                        continue
                option = page.get_by_text(str(value), exact=True).first
                try:
                    if await option.count() and await option.is_visible():
                        await option.click(timeout=fill_timeout)
                        return True
                except Exception:
                    pass
            try:
                await combobox.press("Enter", timeout=fill_timeout)
                return await _combobox_matches_value(combobox)
            except Exception:
                return False
        except Exception:
            return False

    async def _try_active_dialog_single_editable():
        page = getattr(locator, "page", None)
        if page is None:
            return False
        try:
            dialogs = page.locator("[role=dialog], [aria-modal=true]")
            visible_dialogs = []
            for index in range(min(await dialogs.count(), 5)):
                dialog = dialogs.nth(index)
                try:
                    if await dialog.is_visible():
                        visible_dialogs.append(dialog)
                except Exception:
                    continue
            if len(visible_dialogs) != 1:
                return False
            editables = visible_dialogs[0].locator(editable_selector)
            visible_editables = []
            for index in range(min(await editables.count(), 12)):
                editable = editables.nth(index)
                try:
                    if await editable.is_visible() and await editable.is_enabled() and await _is_editable(editable):
                        visible_editables.append(editable)
                except Exception:
                    continue
            if len(visible_editables) != 1:
                return False
            return await _try_fill(visible_editables[0])
        except Exception:
            return False

    try:
        editable = locator.locator(editable_selector).first
        try:
            await editable.wait_for(state="visible", timeout=fill_timeout)
        except Exception:
            pass
        if await _try_fill(editable):
            return
    except Exception:
        pass
    try:
        await locator.wait_for(state="visible", timeout=fill_timeout)
    except Exception:
        pass
    if await _try_fill(locator):
        return
    if await _try_select_combobox(locator):
        return
    if await _try_active_dialog_single_editable():
        return
    raise RuntimeError(f"Locator is not editable for fill value {value!r}")

"""


_RPA_DIALOG_ACTION_HELPER = """async def _rpa_click_dialog_primary_action(dialog, preferred_name=None, *, timeout=5000):
    async def _click_candidate(candidate):
        try:
            if not await candidate.count() or not await candidate.is_visible() or not await candidate.is_enabled():
                return False
            await candidate.click(timeout=timeout)
            return True
        except Exception:
            return False

    if preferred_name:
        for exact in (True, False):
            try:
                candidate = dialog.get_by_role("button", name=str(preferred_name), exact=exact).first
                if await _click_candidate(candidate):
                    return
            except Exception:
                pass

    structural_selectors = [
        "button[type=submit]",
        "[role=button][type=submit]",
        "[data-testid$='-submit']",
        "[data-testid*='submit']",
        "[data-test$='-submit']",
        "[data-test*='submit']",
    ]
    for selector in structural_selectors:
        try:
            candidate = dialog.locator(selector).first
            if await _click_candidate(candidate):
                return
        except Exception:
            pass

    raise RuntimeError("No enabled dialog action button was available")

"""


def stabilize_bare_text_clicks(code: str) -> str:
    """Keep Playwright strict-mode text click failures visible to repair."""
    return str(code or "")


def stabilize_fill_targets(code: str) -> str:
    """Route simple one-line fill calls through an editable-descendant helper."""
    lines = str(code or "").splitlines()
    changed = False
    normalized = []
    inside_rpa_fill = False
    rpa_fill_indent = 0
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if inside_rpa_fill:
            current_indent = len(indent)
            if stripped and current_indent <= rpa_fill_indent and not stripped.startswith("#"):
                inside_rpa_fill = False
            else:
                normalized.append(line)
                continue
        if stripped.startswith("async def _rpa_fill("):
            inside_rpa_fill = True
            rpa_fill_indent = len(indent)
            normalized.append(line)
            continue
        if stripped.startswith("await ") and ".fill(" in stripped and "_rpa_fill(" not in stripped:
            call = stripped[len("await ") :]
            fill_index = call.rfind(".fill(")
            if fill_index > 0 and call.endswith(")"):
                locator_expr = call[:fill_index]
                args = call[fill_index + len(".fill(") : -1]
                normalized.append(f"{indent}await _rpa_fill({locator_expr}, {args})")
                changed = True
                continue
        normalized.append(line)
    result = "\n".join(normalized)
    if changed and "async def _rpa_fill(" not in result:
        result = _RPA_FILL_HELPER + result
    return result


_DIALOG_BUTTON_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)(?P<var>[A-Za-z_]\w*)\s*=\s*"
    r"(?P<dialog>.+?)\.get_by_role\(\s*['\"]button['\"]\s*,\s*name\s*=\s*(?P<name>[^,\)]+).*?\)"
    r"(?:\.first)?\s*$"
)


def stabilize_dialog_button_actions(code: str) -> str:
    """Fallback dialog action clicks to structural dialog-local submit controls."""
    lines = str(code or "").splitlines()
    assignments: dict[str, tuple[str, str]] = {}
    changed = False
    normalized = []
    for line in lines:
        stripped = line.lstrip()
        match = _DIALOG_BUTTON_ASSIGN_RE.match(line)
        if match and "dialog" in match.group("dialog").lower():
            assignments[match.group("var")] = (match.group("dialog").strip(), match.group("name").strip())
            normalized.append(line)
            continue
        if stripped.startswith("await ") and ".click(" in stripped:
            indent = line[: len(line) - len(stripped)]
            call = stripped[len("await ") :]
            var_name = call.split(".", 1)[0].strip()
            if var_name in assignments:
                dialog_expr, preferred_name = assignments[var_name]
                normalized.append(
                    f"{indent}await _rpa_click_dialog_primary_action({dialog_expr}, preferred_name={preferred_name})"
                )
                changed = True
                continue
        normalized.append(line)
    result = "\n".join(normalized)
    if changed and "async def _rpa_click_dialog_primary_action(" not in result:
        result = _RPA_DIALOG_ACTION_HELPER + result
    return result


_CALLABLE_ROLE_NAME_RE = re.compile(
    r"(?P<scope>\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*(?:\([^)\n]*\))?)*)"
    r"\.get_by_role\((?P<role>['\"][^'\"]+['\"])\s*,\s*name\s*=\s*lambda\s+"
    r"(?P<arg>[A-Za-z_]\w*)\s*:\s*(?P<text>['\"][^'\"]+['\"])\s+in\s*(?P=arg)\s*\)"
)

_UNSUPPORTED_VISIBLE_OPTION_RE = re.compile(
    r"(?P<prefix>\.get_by_(?:role|text|label|placeholder|alt_text|title|test_id)\()"
    r"(?P<args>[^()\n]*?)"
    r"(?P<comma_before>,\s*)?visible\s*=\s*(?P<visible>True|False)"
    r"(?P<comma_after>\s*,\s*)?"
    r"(?P<suffix>\))"
)


def stabilize_callable_locator_filters(code: str) -> str:
    """Replace unsupported Playwright callable name filters with text filters."""
    return _CALLABLE_ROLE_NAME_RE.sub(
        lambda match: f"{match.group('scope')}.get_by_role({match.group('role')}).filter(has_text={match.group('text')})",
        str(code or ""),
    )


def stabilize_unsupported_locator_options(code: str) -> str:
    """Move selector-engine options unsupported by get_by_* calls to Locator.filter()."""

    def replace(match: re.Match[str]) -> str:
        args = str(match.group("args") or "")
        comma_before = str(match.group("comma_before") or "")
        comma_after = str(match.group("comma_after") or "")
        if comma_before and comma_after:
            normalized_args = args + comma_before
        else:
            normalized_args = args.rstrip()
            if normalized_args.endswith(","):
                normalized_args = normalized_args[:-1].rstrip()
        return f"{match.group('prefix')}{normalized_args}{match.group('suffix')}.filter(visible={match.group('visible')})"

    return _UNSUPPORTED_VISIBLE_OPTION_RE.sub(replace, str(code or ""))


def stabilize_download_contexts(code: str) -> str:
    """Normalize generated Playwright download contexts without hiding failures."""
    text = str(code or "")
    if "expect_download" not in text:
        return text

    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("with ") and ".expect_download(" in stripped:
            lines.append(f"{indent}async {stripped}")
            continue
        lines.append(line)
    return "\n".join(lines)
