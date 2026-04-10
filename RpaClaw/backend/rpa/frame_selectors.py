from __future__ import annotations

from typing import List


async def build_frame_path(frame) -> List[str]:
    path: List[str] = []
    current_frame = frame
    while current_frame:
        selector = await build_frame_selector(current_frame)
        if selector:
            path.append(selector)
        current_frame = getattr(current_frame, "parent_frame", None)
    path.reverse()
    return path


async def build_frame_selector(frame) -> str:
    try:
        frame_element = await frame.frame_element()
    except Exception:
        page = getattr(frame, "page", None)
        if page is not None and getattr(page, "main_frame", None) is frame:
            return ""
        return _fallback_frame_selector(frame)

    try:
        tag_name = str(await frame_element.evaluate("el => el.tagName.toLowerCase()")).lower()
        name_attr = await frame_element.get_attribute("name")
        if name_attr:
            return f"{tag_name}[name='{_escape_css_attr_value(name_attr)}']"

        title_attr = await frame_element.get_attribute("title")
        if title_attr:
            return f"{tag_name}[title='{_escape_css_attr_value(title_attr)}']"

        test_id_attr = await frame_element.get_attribute("data-testid")
        if test_id_attr:
            return f'{tag_name}[data-testid="{_escape_css_double_quoted_attr_value(test_id_attr)}"]'

        element_id = await frame_element.get_attribute("id")
        if element_id and not _is_guid_like(element_id):
            return f"{tag_name}#{_escape_css_identifier(element_id)}"

        src_attr = await frame_element.get_attribute("src")
        if src_attr:
            return f'{tag_name}[src="{_escape_css_double_quoted_attr_value(src_attr)}"]'

        selector = await frame_element.evaluate(
            """
            el => {
                const tag = el.tagName.toLowerCase();
                if (!el.parentElement) return tag;
                const siblings = Array.from(el.parentElement.children)
                    .filter(child => child.tagName === el.tagName);
                if (siblings.length <= 1) return tag;
                const index = siblings.indexOf(el) + 1;
                return `${tag}:nth-of-type(${index})`;
            }
            """
        )
        if isinstance(selector, str) and selector.strip():
            return selector
    except Exception:
        pass

    return _fallback_frame_selector(frame)


def _fallback_frame_selector(frame) -> str:
    frame_name = getattr(frame, "name", "")
    if callable(frame_name):
        try:
            frame_name = frame_name()
        except Exception:
            frame_name = ""
    if frame_name:
        return f"iframe[name='{_escape_css_attr_value(str(frame_name))}']"

    frame_url = getattr(frame, "url", "")
    if callable(frame_url):
        try:
            frame_url = frame_url()
        except Exception:
            frame_url = ""
    if frame_url:
        return f"iframe[src='{_escape_css_attr_value(str(frame_url))}']"

    return "iframe"


def _escape_css_attr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _escape_css_double_quoted_attr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_css_identifier(value: str) -> str:
    escaped = []
    for char in value:
        if char.isalnum() or char in {"-", "_"}:
            escaped.append(char)
        else:
            escaped.append(f"\\{char}")
    return "".join(escaped)


def _is_guid_like(value: str) -> bool:
    transitions = 0
    previous_type = ""
    for char in value:
        if char.islower():
            current_type = "lower"
        elif char.isupper():
            current_type = "upper"
        elif char.isdigit():
            current_type = "digit"
        else:
            current_type = "other"
        if previous_type and current_type != previous_type:
            transitions += 1
        previous_type = current_type
    return bool(value) and transitions >= len(value) / 4
