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

    async def _try_nearest_single_editable():
        try:
            field_scope = (
                "self::label or self::fieldset or @role='group' or "
                "@data-field or @data-form-item or "
                "contains(concat(' ', normalize-space(@class), ' '), ' form-item ') or "
                "contains(concat(' ', normalize-space(@class), ' '), ' form-field ') or "
                "contains(concat(' ', normalize-space(@class), ' '), ' field ')"
            )
            ancestors = locator.locator(
                "xpath=ancestor::*[(" + field_scope + ") and "
                "(.//input or .//textarea or .//select or .//*[@contenteditable='true'] or .//*[@role='textbox'])]"
                "[position() <= 4]"
            )
            for ancestor_index in range(min(await ancestors.count(), 4)):
                ancestor = ancestors.nth(ancestor_index)
                editables = ancestor.locator(editable_selector)
                visible_editables = []
                for index in range(min(await editables.count(), 12)):
                    editable = editables.nth(index)
                    try:
                        if await editable.is_visible() and await editable.is_enabled() and await _is_editable(editable):
                            visible_editables.append(editable)
                    except Exception:
                        continue
                if len(visible_editables) == 1:
                    return await _try_fill(visible_editables[0])
            return False
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
    if await _try_nearest_single_editable():
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


_RPA_COMBOBOX_CLICK_HELPER = """async def _rpa_click_combobox(locator, *, timeout=5000):
    try:
        await locator.click(timeout=timeout)
        return
    except Exception:
        pass
    try:
        opened = await locator.evaluate(\"\"\"el => {
            const target = el.closest('[role=combobox], [aria-haspopup=listbox]') || el;
            if (!target) return false;
            for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            }
            return true;
        }\"\"\")
        if opened:
            return
    except Exception:
        pass
    raise RuntimeError("Combobox could not be opened")

"""


_RPA_FORM_CONTROL_HELPER = r'''async def _rpa_form_control_by_semantic_name(page, semantic_name, *, timeout=5000):
    import re as _re
    import time as _time

    raw_tokens = _re.split(r'[^0-9A-Za-z\u4e00-\u9fff]+', str(semantic_name or ''))
    ignored = {
        'input', 'field', 'control', 'locator', 'element', 'textbox', 'text', 'value',
        'select', 'combobox', 'combo', 'box', 'btn', 'button',
    }
    aliases = {
        'dept': 'department',
        'deptno': 'department',
        'deptid': 'department',
        'req': 'request',
        'pr': 'request',
        'po': 'order',
        'no': 'number',
        'num': 'number',
        'tel': 'phone',
        'mobile': 'phone',
        'mail': 'email',
    }
    tokens = [aliases.get(token.lower(), token.lower()) for token in raw_tokens if token and token.lower() not in ignored]
    if not tokens:
        raise RuntimeError(f'No semantic tokens for form control {semantic_name!r}')

    selector = "input:not([type=hidden]), textarea, select, [contenteditable=true], [role=textbox], [role=spinbutton], [role=combobox]"

    async def _candidate_text(control):
        try:
            return await control.evaluate("""el => {
                const normalize = value => String(value || '').replace(/\\s+/g, ' ').trim();
                const values = [];
                const push = value => { value = normalize(value); if (value) values.push(value); };
                push(el.getAttribute('aria-label'));
                push(el.getAttribute('placeholder'));
                push(el.getAttribute('name'));
                push(el.getAttribute('id'));
                push(el.getAttribute('data-testid'));
                push(el.getAttribute('data-test'));
                push(el.getAttribute('title'));
                if (el.labels) {
                    for (const label of Array.from(el.labels)) push(label.textContent);
                }
                const wrappingLabel = el.closest('label');
                if (wrappingLabel) push(wrappingLabel.textContent);
                const labelledBy = el.getAttribute('aria-labelledby');
                if (labelledBy) {
                    for (const id of labelledBy.split(/\\s+/)) {
                        const node = document.getElementById(id);
                        if (node) push(node.textContent);
                    }
                }
                const controlSelector = "input:not([type=hidden]), textarea, select, [contenteditable=true], [role=textbox], [role=spinbutton], [role=combobox]";
                const group = el.closest('[role=group], fieldset, [data-field], [data-form-item], .form-item, .form-field, .field, .control');
                if (group) {
                    const controls = Array.from(group.querySelectorAll(controlSelector))
                        .filter(node => node.offsetParent !== null || node === el);
                    const isFieldset = (group.tagName || '').toLowerCase() === 'fieldset';
                    if (isFieldset || controls.length <= 2) {
                    for (const node of Array.from(group.querySelectorAll('label,legend,[role=heading],dt,[aria-label]')).slice(0, 8)) {
                        push(node.textContent);
                        push(node.getAttribute && node.getAttribute('aria-label'));
                    }
                    }
                }
                return values.join(' ');
            }""")
        except Exception:
            return ""

    async def _usable(control):
        try:
            return await control.count() and await control.is_visible() and await control.is_enabled()
        except Exception:
            return False

    deadline = _time.perf_counter() + max(timeout, 1000) / 1000
    while _time.perf_counter() < deadline:
        controls = page.locator(selector)
        count = min(await controls.count(), 120)
        matches = []
        for index in range(count):
            control = controls.nth(index)
            if not await _usable(control):
                continue
            text = (await _candidate_text(control)).lower()
            if not text:
                continue
            if all(token in text for token in tokens):
                matches.append((index, control))
        if len(matches) == 1:
            return matches[0][1]
        if len(matches) > 1:
            raise RuntimeError(f'Ambiguous form control for semantic name {semantic_name!r}')
        try:
            await page.wait_for_timeout(250)
        except Exception:
            pass
    raise RuntimeError(f'Form control not found for semantic name {semantic_name!r}')

'''


_RPA_SUBMIT_TEXT_INPUT_HELPER = """async def _rpa_submit_text_input(locator, *, timeout=5000):
    press_error = None
    try:
        await locator.press("Enter", timeout=timeout)
        await locator.page.wait_for_timeout(300)
        return
    except Exception as exc:
        press_error = exc

    clicked_submit = False
    submit_error = None
    try:
        await locator.wait_for(state="visible", timeout=timeout)
    except Exception:
        pass

    try:
        form = locator.locator("xpath=ancestor::form[1]")
        if await form.count():
            buttons = form.first.locator("button[type=submit], input[type=submit]")
            for index in range(min(await buttons.count(), 8)):
                button = buttons.nth(index)
                try:
                    if not await button.is_visible() or not await button.is_enabled():
                        continue
                    await button.click(timeout=timeout)
                    await locator.page.wait_for_timeout(500)
                    clicked_submit = True
                    break
                except Exception as exc:
                    submit_error = exc
                    continue
    except Exception as exc:
        submit_error = exc
    if clicked_submit:
        return
    if press_error is not None:
        raise RuntimeError(f"Enter submission failed: {press_error}")
    if submit_error is not None:
        raise RuntimeError(f"Submit button click failed: {submit_error}")
    raise RuntimeError("Enter submission did not trigger a verified key press or structural submit")
"""


_RPA_TEXT_PRESENT_HELPER = """async def _rpa_text_present(page, *args, timeout=10000, **kwargs):
    locator = page.get_by_text(*args, **kwargs).first
    try:
        await locator.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        try:
            return bool(await locator.count() and await locator.is_visible())
        except Exception:
            return False

"""


def stabilize_bare_text_clicks(code: str) -> str:
    """Keep Playwright strict-mode text click failures visible to repair."""
    return str(code or "")


def stabilize_combobox_clicks(code: str) -> str:
    """Open combobox locators through a helper that tolerates decorated controls."""
    lines = str(code or "").splitlines()
    normalized = []
    changed = False
    combobox_vars: set[str] = set()
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        assignment = re.match(r"^(?P<var>[A-Za-z_]\w*)\s*=\s*(?P<expr>.+\.get_by_role\([^)]*['\"]combobox['\"][^)]*\).*)$", stripped)
        if assignment:
            combobox_vars.add(assignment.group("var"))
            normalized.append(line)
            continue
        direct_combobox_click = re.match(
            r"^await\s+(?P<locator>.+\.get_by_role\([^)]*['\"]combobox['\"][^)]*\).*?)\.click\((?P<args>.*)\)\s*$",
            stripped,
        )
        if direct_combobox_click:
            locator_expr = direct_combobox_click.group("locator")
            args = direct_combobox_click.group("args").strip()
            timeout = _click_timeout_arg(args)
            normalized.append(f"{indent}await _rpa_click_combobox({locator_expr}, timeout={timeout})")
            changed = True
            continue
        click_call = re.match(r"^await\s+(?P<locator>.+)\.click\((?P<args>.*)\)\s*$", stripped)
        if click_call:
            locator_expr = click_call.group("locator").strip()
            if locator_expr in combobox_vars:
                timeout = _click_timeout_arg(click_call.group("args").strip())
                normalized.append(f"{indent}await _rpa_click_combobox({locator_expr}, timeout={timeout})")
                changed = True
                continue
        normalized.append(line)
    result = "\n".join(normalized)
    if changed and "async def _rpa_click_combobox(" not in result:
        result = _RPA_COMBOBOX_CLICK_HELPER + result
    return result


def _click_timeout_arg(args: str) -> str:
    match = re.search(r"(?:^|,\s*)timeout\s*=\s*([^,\)]+)", str(args or ""))
    return match.group(1).strip() if match else "5000"


def stabilize_text_input_submissions(code: str) -> str:
    """Make Enter-submitted text filters also trigger nearby structural submit controls."""
    lines = str(code or "").splitlines()
    normalized = []
    changed = False
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("await ") and ".press(" in stripped and "Enter" in stripped:
            locator_expr = stripped[len("await ") :].split(".press(", 1)[0].strip()
            if locator_expr and not locator_expr.startswith("_rpa_"):
                normalized.append(f"{indent}await _rpa_submit_text_input({locator_expr})")
                changed = True
                continue
        normalized.append(line)
    result = "\n".join(normalized)
    if changed and "async def _rpa_submit_text_input(" not in result:
        result = _RPA_SUBMIT_TEXT_INPUT_HELPER + result
    return result


_TEXT_COUNT_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)(?P<var>[A-Za-z_]\w*)\s*=\s*await\s+page\.get_by_text\((?P<args>.+)\)\.count\(\)\s*>\s*0\s*$"
)
_DIRECT_TEXT_WAIT_RE = re.compile(
    r"^(?P<indent>\s*)await\s+(?P<scope>[A-Za-z_]\w*)\.get_by_text\((?P<args>.+)\)\.wait_for\((?P<wait_args>.*)\)\s*$"
)
_EXPECT_TEXT_VISIBLE_RE = re.compile(
    r"^(?P<indent>\s*)await\s+expect\(\s*(?P<scope>[A-Za-z_]\w*)\.get_by_text\((?P<args>.+)\)\s*\)\.to_be_visible\((?P<expect_args>.*)\)\s*$"
)


def stabilize_immediate_text_count_assertions(code: str) -> str:
    """Turn instantaneous get_by_text count checks into bounded visibility waits."""

    lines = str(code or "").splitlines()
    normalized = []
    changed = False
    for line in lines:
        match = _TEXT_COUNT_ASSIGN_RE.match(line)
        if not match:
            normalized.append(line)
            continue
        normalized.append(
            f"{match.group('indent')}{match.group('var')} = "
            f"await _rpa_text_present(page, {match.group('args')})"
        )
        changed = True
    result = "\n".join(normalized)
    if changed and "async def _rpa_text_present(" not in result:
        result = _RPA_TEXT_PRESENT_HELPER + result
    return result


def stabilize_direct_text_waits(code: str) -> str:
    """Avoid strict-mode failures when waiting on non-unique text locators."""

    lines = str(code or "").splitlines()
    normalized = []
    changed = False
    for line in lines:
        match = _DIRECT_TEXT_WAIT_RE.match(line)
        if match:
            normalized.append(
                f"{match.group('indent')}await {match.group('scope')}.get_by_text("
                f"{match.group('args')}).first.wait_for({match.group('wait_args')})"
            )
            changed = True
            continue
        match = _EXPECT_TEXT_VISIBLE_RE.match(line)
        if match:
            normalized.append(
                f"{match.group('indent')}await expect({match.group('scope')}.get_by_text("
                f"{match.group('args')}).first).to_be_visible({match.group('expect_args')})"
            )
            changed = True
            continue
        normalized.append(line)
    return "\n".join(normalized) if changed else str(code or "")


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
            dialog_expr = match.group("dialog").strip()
            if dialog_expr.startswith("await ") or "(" in dialog_expr:
                normalized.append(line)
                continue
            assignments[match.group("var")] = (dialog_expr, match.group("name").strip())
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
    r"\.get_by_role\((?P<role>['\"][^'\"]+['\"])\s*,\s*name\s*=\s*lambda\s+(?:\*\s*)?"
    r"(?P<arg>[A-Za-z_]\w*)\s*:\s*(?P<text>['\"][^'\"]+['\"])\s+in\s*(?P=arg)\s*\)"
)
_CALLABLE_FALSE_ROLE_NAME_RE = re.compile(
    r"(?P<scope>\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*(?:\([^)\n]*\))?)*)"
    r"\.get_by_role\((?P<role>['\"][^'\"]+['\"])\s*,\s*name\s*=\s*lambda\s+(?:\*\s*)?"
    r"(?P<arg>[A-Za-z_]\w*)\s*:\s*False\s*\)"
)

_UNSUPPORTED_VISIBLE_OPTION_RE = re.compile(
    r"(?P<prefix>\.get_by_(?:role|text|label|placeholder|alt_text|title|test_id)\()"
    r"(?P<args>[^()\n]*?)"
    r"(?P<comma_before>,\s*)?visible\s*=\s*(?P<visible>True|False)"
    r"(?P<comma_after>\s*,\s*)?"
    r"(?P<suffix>\))"
)
_CELL_SINGLE_ACTION_LOCATOR_RE = re.compile(
    r"(?P<prefix>\.locator\(\s*)(?P<quote>['\"])(?P<cell>td:nth-child\(\d+\))\s+"
    r"(?P<target>a|button|\[role=button\]|\[role=link\])(?P=quote)\s*\)"
)
_LOCATOR_ASSIGNMENT_RE = re.compile(
    r"^(?P<indent>\s*)(?P<var>[A-Za-z_]\w*)\s*=\s*(?P<expr>.+(?:get_by_|locator\().*)$"
)
_NEAREST_REGION_CONTAINER_XPATH_RE = re.compile(
    r"ancestor(?:-or-self)?::\*\[(?:self::section|self::article|self::div)(?:\s+or\s+(?:self::section|self::article|self::div))*\]\[1\]"
)
_TBODY_INPUT_CHECKBOX_RE = re.compile(
    r"(?P<prefix>\.locator\(\s*)(?P<quote>['\"])tbody\s+input\[type=(?P<inner>['\"]?)checkbox(?P=inner)\](?P=quote)\s*\)"
)
_COUNT_ZERO_GUARD_RE = re.compile(
    r"^(?P<indent>\s*)if\s+await\s+(?P<var>[A-Za-z_]\w*)\.count\(\)\s*==\s*0\s*:\s*$"
)


def stabilize_callable_locator_filters(code: str) -> str:
    """Replace unsupported Playwright callable name filters with text filters."""
    normalized = _CALLABLE_FALSE_ROLE_NAME_RE.sub(
        lambda match: f"{match.group('scope')}.locator('__rpa_no_match__')",
        str(code or ""),
    )
    return _CALLABLE_ROLE_NAME_RE.sub(
        lambda match: f"{match.group('scope')}.get_by_role({match.group('role')}).filter(has_text={match.group('text')})",
        normalized,
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


def stabilize_cell_action_locators(code: str) -> str:
    """Broaden row-cell action locators without leaving the cell scope."""

    def replace(match: re.Match[str]) -> str:
        cell = match.group("cell")
        quote = match.group("quote")
        selector = (
            f"{cell} a, {cell} button, "
            f"{cell} [role=button], {cell} [role=link]"
        )
        return f"{match.group('prefix')}{quote}{selector}{quote})"

    return _CELL_SINGLE_ACTION_LOCATOR_RE.sub(replace, str(code or ""))


def stabilize_required_locator_guards(code: str) -> str:
    """Wait for positive locator guards that immediately raise on absence."""

    lines = str(code or "").splitlines()
    normalized: list[str] = []
    assignments: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        assign = _LOCATOR_ASSIGNMENT_RE.match(line)
        if assign:
            assignments[assign.group("var")] = assign.group("indent")
            normalized.append(line)
            index += 1
            continue
        guard = _COUNT_ZERO_GUARD_RE.match(line)
        if guard and guard.group("var") in assignments:
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and lines[next_index].lstrip().startswith("raise "):
                indent = guard.group("indent")
                var_name = guard.group("var")
                normalized.extend(
                    [
                        f"{indent}try:",
                        f"{indent}    await {var_name}.wait_for(state=\"visible\", timeout=10000)",
                        f"{indent}except Exception:",
                        f"{indent}    pass",
                    ]
                )
        normalized.append(line)
        index += 1
    return "\n".join(normalized)


def stabilize_region_container_locators(code: str) -> str:
    """Prefer region ancestors that contain interactive or tabular content."""

    replacement = (
        "ancestor::*[(self::section or self::article or self::main or self::div) "
        "and (.//table or .//*[@role=\\\"table\\\" or @role=\\\"grid\\\" or @role=\\\"list\\\"] "
        "or .//a or .//button or .//*[@role=\\\"button\\\" or @role=\\\"link\\\"])]"
        "[1]"
    )
    return _NEAREST_REGION_CONTAINER_XPATH_RE.sub(replacement, str(code or ""))


def stabilize_table_checkbox_locators(code: str) -> str:
    """Support native and ARIA checkbox controls inside table bodies."""

    def replace(match: re.Match[str]) -> str:
        quote = match.group("quote")
        selector = 'tbody input[type="checkbox"], tbody [role="checkbox"]'
        return f"{match.group('prefix')}{quote}{selector}{quote})"

    return _TBODY_INPUT_CHECKBOX_RE.sub(replace, str(code or ""))


_NAMED_TABLE_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)(?P<var>[A-Za-z_]\w*)\s*=\s*page\.get_by_role\(\s*['\"]table['\"]\s*,\s*name\s*=\s*(?P<name>['\"][^'\"]+['\"]).*?\)\s*$"
)


def stabilize_named_table_locators(code: str) -> str:
    """Resolve named tables through accessible name, caption text, then heading scope."""

    lines = []
    changed = False
    for line in str(code or "").splitlines():
        match = _NAMED_TABLE_ASSIGN_RE.match(line)
        if not match:
            lines.append(line)
            continue
        lines.append(f"{match.group('indent')}{match.group('var')} = await _rpa_named_table(page, {match.group('name').strip()})")
        changed = True
    result = "\n".join(lines)
    if changed and "async def _rpa_named_table(" not in result:
        result = _NAMED_TABLE_HELPER + "\n\n" + result
    return result


_ORDINAL_ROW_NAMED_LINK_RE = re.compile(
    r"^(?P<indent>\s*)(?P<link_var>[A-Za-z_]\w*)\s*=\s*"
    r"(?P<row_var>[A-Za-z_]\w*)\.get_by_role\(\s*['\"]link['\"]\s*,\s*name\s*=\s*(?P<name>[A-Za-z_]\w*)\s*\)\s*$"
)


def stabilize_ordinal_row_dynamic_links(code: str) -> str:
    """Do not use recorded row-link text when row position already identifies the target.

    Playwright/codegen and LLMs often record "first row" actions as:
    `row = table.get_by_role("row").first; link = row.get_by_role("link", name=recorded_text)`.
    In that shape the row ordinal is the stable locator and the link text is data. Keeping the
    recorded name breaks replay when the first row changes, so the link lookup is scoped to the
    row and made text-agnostic.
    """

    text = str(code or "")
    ordinal_rows: set[str] = set()
    for line in text.splitlines():
        if re.search(r"=\s*.*\.get_by_role\(\s*['\"]row['\"]", line) and re.search(
            r"\.(first|last)\s*$|\.nth\(\s*\d+\s*\)\s*$", line
        ):
            var = line.split("=", 1)[0].strip()
            if re.match(r"^[A-Za-z_]\w*$", var):
                ordinal_rows.add(var)

    if not ordinal_rows:
        return text

    lines = []
    for line in text.splitlines():
        match = _ORDINAL_ROW_NAMED_LINK_RE.match(line)
        if match and match.group("row_var") in ordinal_rows:
            lines.append(f"{match.group('indent')}{match.group('link_var')} = {match.group('row_var')}.get_by_role('link').first")
            continue
        lines.append(line)
    return "\n".join(lines)


_ASYNC_LOCATOR_VALUE_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)(?P<lhs>[A-Za-z_]\w*(?:\s*:\s*[^=]+)?\s*=\s*)"
    r"(?P<expr>.+\.(?:inner_text|text_content|input_value|count|is_visible|is_enabled|is_checked)\([^#\n]*\))"
    r"(?P<suffix>\s*(?:#.*)?)$"
)

_AWAIT_PLAYWRIGHT_PROPERTY_RE = re.compile(
    r"\bawait\s+(?P<expr>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\.(?:suggested_filename|url))\b"
)

_TESTID_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)(?P<var>[A-Za-z_]\w*)\s*=\s*page\.get_by_test_id\(\s*(?P<quote>['\"])(?P<testid>[^'\"]+)(?P=quote)\s*\)\s*$"
)

_INPUT_HAS_TEXT_FILTER_RE = re.compile(
    r"^(?P<indent>\s*)(?P<var>[A-Za-z_]\w*)\s*=\s*page\.locator\(\s*(['\"])input\3\s*\)\.filter\(\s*has\s*=\s*page\.get_by_text\(\s*(?P<label>[^)]+)\)\s*\)\s*$"
)

_ROW_HAS_TEXT_FIRST_RE = re.compile(
    r"page\.locator\(\s*(?P<selector>['\"][^'\"]*(?:tbody\s+tr|\[role=row\]|(?<![A-Za-z])tr(?![A-Za-z]))[^'\"]*['\"])\s*,\s*has_text\s*=\s*(?P<text>[^)]+)\)\.first"
)
_ROW_FILTER_HAS_TEXT_FIRST_RE = re.compile(
    r"(?:page|[A-Za-z_]\w*)\.locator\(\s*(?P<selector>['\"][^'\"]*(?:tbody\s+tr|\[role=row\]|(?<![A-Za-z])tr(?![A-Za-z]))[^'\"]*['\"])\s*\)"
    r"\.filter\(\s*has_text\s*=\s*(?P<text>[^)]+)\)\.first"
)
_SCOPED_ROW_FILTER_HAS_TEXT_FIRST_RE = re.compile(
    r"page\.locator\(\s*['\"]table['\"]\s*\)\.filter\(\s*has_text\s*=\s*[^)]+\)\.first"
    r"\.locator\(\s*['\"]tbody tr['\"]\s*\)\.filter\(\s*has_text\s*=\s*(?P<text>[^)]+)\)\.first"
)

_ROWS_BODY_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)rows\s*=\s*.+\.locator\(\s*(['\"])tbody tr\2\s*\)\s*$"
)

_ROW_INNER_TEXT_CONTAINS_RE = re.compile(
    r"if\s+(?P<text>[A-Za-z_]\w*|['\"][^'\"]+['\"])\s+in\s+await\s+row\.inner_text\(\)"
)

_BODY_TEXT_NAV_GUARD_RE = re.compile(
    r"^(?P<indent>\s*)if\s+.+\s+not\s+in\s+await\s+page\.locator\(\s*['\"]body['\"]\s*\)\.inner_text\(\s*\)\s*:\s*$"
)


def stabilize_playwright_async_api_usage(code: str) -> str:
    """Fix common Playwright async API shape mistakes without changing intent."""

    lines = []
    for line in str(code or "").splitlines():
        stripped = line.lstrip()
        if "await " not in line:
            match = _ASYNC_LOCATOR_VALUE_ASSIGN_RE.match(line)
            if match:
                line = f"{match.group('indent')}{match.group('lhs')}await {match.group('expr')}{match.group('suffix')}"
        line = _AWAIT_PLAYWRIGHT_PROPERTY_RE.sub(lambda match: match.group("expr"), line)
        lines.append(line)
    return "\n".join(lines)


def _semantic_tokens(value: str) -> list[str]:
    ignored = {
        "input",
        "field",
        "control",
        "locator",
        "element",
        "textbox",
        "value",
        "select",
        "combobox",
        "combo",
        "box",
        "btn",
        "button",
    }
    aliases = {
        "dept": "department",
        "deptno": "department",
        "deptid": "department",
        "req": "request",
        "pr": "request",
        "po": "order",
        "no": "number",
        "num": "number",
        "tel": "phone",
        "mobile": "phone",
        "mail": "email",
    }
    return [
        aliases.get(token.lower(), token.lower())
        for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", str(value or ""))
        if token and token.lower() not in ignored
    ]


def stabilize_semantic_form_control_locators(code: str) -> str:
    """Repair form-control locator choices when code-local semantic evidence contradicts itself."""

    text = str(code or "")
    fill_vars = set(re.findall(r"(?:_rpa_fill|\.fill)\(\s*([A-Za-z_]\w*)\b", text))
    lines: list[str] = []
    changed = False
    for line in text.splitlines():
        match = _TESTID_ASSIGN_RE.match(line)
        if match:
            var_name = match.group("var")
            var_tokens = _semantic_tokens(match.group("var"))
            testid_tokens = _semantic_tokens(match.group("testid"))
            if (
                var_name in fill_vars
                and var_tokens
                and testid_tokens
                and not all(token in testid_tokens for token in var_tokens)
            ):
                line = (
                    f"{match.group('indent')}{match.group('var')} = "
                    f"await _rpa_form_control_by_semantic_name(page, {match.group('var')!r})"
                )
                changed = True
        match = _INPUT_HAS_TEXT_FILTER_RE.match(line)
        if match:
            line = (
                f"{match.group('indent')}{match.group('var')} = "
                f"await _rpa_form_control_by_semantic_name(page, {match.group('label')})"
            )
            changed = True
        lines.append(line)
    normalized = "\n".join(lines)
    if changed and "async def _rpa_form_control_by_semantic_name(" not in normalized:
        normalized = _RPA_FORM_CONTROL_HELPER + "\n\n" + normalized
    return normalized


def stabilize_table_row_text_locators(code: str) -> str:
    """Replace brittle CSS table-row text lookups with semantic row lookup."""

    original = str(code or "")
    normalized = _ROW_HAS_TEXT_FIRST_RE.sub(
        lambda match: f"_rpa_find_row_by_text(page, {match.group('text')})",
        original,
    )
    normalized = _SCOPED_ROW_FILTER_HAS_TEXT_FIRST_RE.sub(
        lambda match: f"_rpa_find_row_by_text(page, {match.group('text')})",
        normalized,
    )
    normalized = _ROW_FILTER_HAS_TEXT_FIRST_RE.sub(
        lambda match: f"_rpa_find_row_by_text(page, {match.group('text')})",
        normalized,
    )
    contains_match = _ROW_INNER_TEXT_CONTAINS_RE.search(normalized)
    if contains_match:
        lines: list[str] = []
        replaced = False
        for line in normalized.splitlines():
            assign_match = _ROWS_BODY_ASSIGN_RE.match(line)
            if assign_match and not replaced:
                line = f"{assign_match.group('indent')}rows = _rpa_rows_containing_text(page, {contains_match.group('text')})"
                replaced = True
            lines.append(line)
        normalized = "\n".join(lines)
    if normalized != original and "def _rpa_find_row_by_text(" not in normalized:
        normalized = _RPA_ROW_TEXT_HELPER + "\n\n" + normalized
    return normalized


def stabilize_initial_dialog_visibility(code: str) -> str:
    """Keep dialog preconditions explicit; opener recovery belongs to recording plans."""

    return str(code or "")


def _guarded_block_has_navigation_evidence(lines: list[str]) -> bool:
    block = "\n".join(lines)
    if "wait_for_load_state" in block or "expect_navigation" in block or "page.goto(" in block:
        return True
    return bool(re.search(r"get_by_role\(\s*['\"](?:link|menuitem|tab)['\"]", block))


def stabilize_body_text_navigation_guards(code: str) -> str:
    """Do not use whole-page body text as proof that navigation already happened."""

    original = str(code or "")
    lines = original.splitlines()
    result: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        match = _BODY_TEXT_NAV_GUARD_RE.match(line)
        if not match:
            result.append(line)
            index += 1
            continue
        guard_indent = len(match.group("indent"))
        guarded: list[str] = []
        probe = index + 1
        while probe < len(lines):
            stripped = lines[probe].lstrip()
            indent = len(lines[probe]) - len(stripped)
            if stripped and indent <= guard_indent:
                break
            guarded.append(lines[probe])
            probe += 1
        if not any(".click(" in item for item in guarded[:8]):
            result.append(line)
            index += 1
            continue
        if not _guarded_block_has_navigation_evidence(guarded):
            result.append(line)
            index += 1
            continue
        changed = True
        for guarded_line in guarded:
            if guarded_line.startswith(" " * (guard_indent + 4)):
                result.append(guarded_line[4:])
            else:
                result.append(guarded_line)
        index = probe
    return "\n".join(result) if changed else original


_BODY_TEXT_ASSIGN_RE = re.compile(
    r"^(?P<indent>\s*)(?P<var>[A-Za-z_]\w*)\s*=\s*await\s+page\.locator\(\s*['\"]body['\"]\s*\)\.inner_text\(\)\s*$"
)
_BODY_TEXT_ASSERT_RE = re.compile(
    r"^(?P<indent>\s*)if\s+(?P<literal>['\"][^'\"]+['\"])\s+not\s+in\s+(?P<var>[A-Za-z_]\w*)\s*:\s*$"
)
_UNSCOPED_EMPTY_TEXTBOX_RE = re.compile(
    r"page\.get_by_role\(\s*(['\"])textbox\1\s*\)\.filter\(\s*has_not_text\s*=\s*(['\"])\2\s*\)\.first"
)


def stabilize_unscoped_textbox_fallbacks(code: str) -> str:
    """Disable page-wide empty textbox fallbacks that cannot prove field ownership."""

    return _UNSCOPED_EMPTY_TEXTBOX_RE.sub(
        "page.locator('__rpa_rejected_unscoped_textbox_fallback__')",
        str(code or ""),
    )


def stabilize_body_text_post_navigation_assertions(code: str) -> str:
    """Drop brittle whole-page text self-assertions after explicit navigation actions."""

    original = str(code or "")
    if not _guarded_block_has_navigation_evidence(original.splitlines()):
        return original
    lines = original.splitlines()
    body_text_vars: set[str] = set()
    result: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        assign = _BODY_TEXT_ASSIGN_RE.match(line)
        if assign:
            body_text_vars.add(assign.group("var"))
            result.append(line)
            index += 1
            continue
        assertion = _BODY_TEXT_ASSERT_RE.match(line)
        if assertion and assertion.group("var") in body_text_vars:
            probe = index + 1
            assertion_indent = len(assertion.group("indent"))
            block: list[str] = []
            while probe < len(lines):
                stripped = lines[probe].lstrip()
                indent = len(lines[probe]) - len(stripped)
                if stripped and indent <= assertion_indent:
                    break
                block.append(lines[probe])
                probe += 1
            if any(item.lstrip().startswith("raise ") for item in block):
                changed = True
                index = probe
                continue
        result.append(line)
        index += 1
    return "\n".join(result) if changed else original


def normalize_generated_playwright_code(code: str) -> str:
    """Apply generic Playwright code stabilizers used by both recording and export."""

    normalized = str(code or "").replace(".get_by_testid(", ".get_by_test_id(")
    normalized = stabilize_playwright_async_api_usage(normalized)
    normalized = stabilize_body_text_navigation_guards(normalized)
    normalized = stabilize_body_text_post_navigation_assertions(normalized)
    normalized = stabilize_immediate_text_count_assertions(normalized)
    normalized = stabilize_direct_text_waits(normalized)
    normalized = stabilize_callable_locator_filters(normalized)
    normalized = stabilize_unsupported_locator_options(normalized)
    normalized = stabilize_cell_action_locators(normalized)
    normalized = stabilize_required_locator_guards(normalized)
    normalized = stabilize_table_row_text_locators(normalized)
    normalized = stabilize_region_container_locators(normalized)
    normalized = stabilize_table_checkbox_locators(normalized)
    normalized = stabilize_named_table_locators(normalized)
    normalized = stabilize_ordinal_row_dynamic_links(normalized)
    normalized = stabilize_bare_text_clicks(normalized)
    normalized = stabilize_combobox_clicks(normalized)
    normalized = stabilize_text_input_submissions(normalized)
    normalized = stabilize_semantic_form_control_locators(normalized)
    normalized = stabilize_fill_targets(normalized)
    normalized = stabilize_unscoped_textbox_fallbacks(normalized)
    normalized = stabilize_initial_dialog_visibility(normalized)
    normalized = stabilize_dialog_button_actions(normalized)
    normalized = stabilize_download_contexts(normalized)
    return normalized


def stabilize_download_contexts(code: str) -> str:
    """Normalize generated Playwright download contexts without hiding failures."""
    text = str(code or "")
    if "expect_download" not in text:
        return text

    lines = []
    in_download_context_indent: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("with ") and ".expect_download(" in stripped:
            lines.append(f"{indent}async {stripped}")
            in_download_context_indent = indent
            continue
        if stripped.startswith("async with ") and ".expect_download(" in stripped:
            lines.append(line)
            in_download_context_indent = indent
            continue
        if in_download_context_indent is not None:
            if stripped and len(indent) <= len(in_download_context_indent):
                in_download_context_indent = None
            elif stripped.startswith("await ") and ".click(" in stripped:
                target = stripped[len("await ") : stripped.index(".click(")].strip()
                if target:
                    lines.append(f"{indent}await _rpa_prepare_download_control(page, {target})")
        lines.append(line)
    normalized = "\n".join(lines)
    if "_rpa_prepare_download_control(" not in normalized:
        return normalized
    return _DOWNLOAD_CONTROL_HELPER + "\n\n" + normalized


_DOWNLOAD_CONTROL_HELPER = r'''async def _rpa_prepare_download_control(page, control, timeout_ms=45000):
    import re as _re
    import time as _time

    async def _is_ready():
        try:
            if not await control.count():
                return False
            first = control.first
            return await first.is_visible() and await first.is_enabled()
        except Exception:
            return False

    async def _click_first(pattern):
        candidates = page.get_by_role('button', name=pattern)
        count = min(await candidates.count(), 20)
        for index in range(count):
            candidate = candidates.nth(index)
            try:
                if await candidate.is_visible() and await candidate.is_enabled():
                    await candidate.click(timeout=5000)
                    return True
            except Exception:
                continue
        return False

    if await _is_ready():
        return
    await _click_first(_re.compile(r'(generate|create|start|request|build|prepare|生成|创建|发起|开始|准备)', _re.I))
    deadline = _time.perf_counter() + max(timeout_ms, 1000) / 1000
    while _time.perf_counter() < deadline:
        if await _is_ready():
            return
        await _click_first(_re.compile(r'(refresh|reload|poll|刷新|重新加载|轮询)', _re.I))
        try:
            await page.wait_for_timeout(800)
        except Exception:
            pass
'''


_RPA_ROW_TEXT_HELPER = r'''def _rpa_find_row_by_text(page, text):
    target = str(text or "")
    rows = page.locator("tbody tr, [role=row]").filter(has_text=target)
    text_node = page.get_by_text(target, exact=True).first
    ancestor = text_node.locator("xpath=ancestor::*[self::tr or @role='row'][1]")
    return rows.or_(ancestor).first


def _rpa_rows_containing_text(page, text):
    target = str(text or "")
    rows = page.locator("tbody tr, [role=row]").filter(has_text=target)
    text_node = page.get_by_text(target, exact=True).first
    ancestor = text_node.locator("xpath=ancestor::*[self::tr or @role='row'][1]")
    return rows.or_(ancestor)

'''


_NAMED_TABLE_HELPER = r'''async def _rpa_named_table(page, name, *, timeout=10000):
    import time as _time

    async def _rows(candidate):
        return candidate.locator('tbody tr, [role=row]')

    async def _usable(candidate):
        try:
            if not await candidate.count():
                return False
            return await candidate.first.is_visible()
        except Exception:
            return False

    async def _best_table(candidates):
        best = None
        best_score = -1
        count = min(await candidates.count(), 8)
        for index in range(count):
            candidate = candidates.nth(index)
            try:
                if not await _usable(candidate):
                    continue
                header_cells = candidate.locator('thead th, thead td, [role=columnheader]')
                score = await header_cells.count()
                if score <= 0:
                    rows = await _rows(candidate)
                    if await rows.count():
                        score = await rows.first.locator('td, th, [role=cell], [role=gridcell], [role=columnheader]').count()
                if score > best_score:
                    best = candidate
                    best_score = score
            except Exception:
                continue
        return best

    deadline = _time.perf_counter() + max(timeout, 1000) / 1000
    while _time.perf_counter() < deadline:
        for candidate in (
            page.get_by_role('table', name=name),
            page.locator('table').filter(has_text=str(name)),
        ):
            found = await _best_table(candidate)
            if found is not None:
                return found
        heading = page.get_by_text(str(name), exact=True).first
        if not await heading.count():
            heading = page.get_by_text(str(name), exact=False).first
        if await heading.count():
            found = await _best_table(heading.locator("xpath=following::table[1]"))
            if found is not None:
                return found
        await page.wait_for_timeout(250)
    return page.get_by_role('table', name=name)
'''
