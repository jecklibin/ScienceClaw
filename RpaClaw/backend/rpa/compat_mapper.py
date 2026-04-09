from __future__ import annotations

from typing import Any


def _candidate_kind(candidate: dict[str, Any]) -> str:
    locator_ast = candidate.get("locatorAst") or {}
    kind = locator_ast.get("kind")
    if kind:
        return str(kind)
    return str(candidate.get("kind") or "css")


def to_legacy_locator_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": _candidate_kind(candidate),
        "score": candidate.get("score", 0),
        "strict_match_count": candidate.get("matchCount", 0),
        "visible_match_count": candidate.get("visibleMatchCount", 0),
        "selected": bool(candidate.get("isSelected", False)),
        "locator": candidate.get("selector"),
        "reason": candidate.get("reason", ""),
        "engine": candidate.get("engine", ""),
    }


def to_legacy_step(action: dict[str, Any]) -> dict[str, Any]:
    popup = (action.get("signals") or {}).get("popup") or {}
    input_payload = action.get("input") or {}
    snapshot = action.get("snapshot") or {}
    return {
        "id": action["id"],
        "action": action["kind"],
        "target": (action.get("locator") or {}).get("selector"),
        "frame_path": action.get("framePath", []),
        "locator_candidates": [
            to_legacy_locator_candidate(candidate)
            for candidate in action.get("locatorAlternatives", [])
        ],
        "validation": action.get("validation", {}),
        "signals": action.get("signals", {}),
        "element_snapshot": snapshot,
        "value": input_payload.get("value") or input_payload.get("text"),
        "screenshot_url": snapshot.get("screenshotUrl"),
        "description": action.get("description"),
        "tag": snapshot.get("tag"),
        "label": snapshot.get("label"),
        "url": snapshot.get("url") or action.get("url"),
        "source": "record",
        "tab_id": action.get("pageAlias"),
        "source_tab_id": action.get("pageAlias"),
        "target_tab_id": popup.get("targetPageAlias"),
    }


def to_legacy_tab(page: dict[str, Any], active_tab_id: str | None) -> dict[str, Any]:
    tab_id = page.get("alias") or page.get("id") or ""
    return {
        "tab_id": tab_id,
        "title": page.get("title", ""),
        "url": page.get("url", ""),
        "opener_tab_id": page.get("openerPageAlias"),
        "status": page.get("status", "open"),
        "active": tab_id == active_tab_id,
    }


def to_legacy_session(session: dict[str, Any]) -> dict[str, Any]:
    active_tab_id = session.get("activePageAlias") or session.get("active_tab_id")
    return {
        "id": session["id"],
        "user_id": session.get("userId") or session.get("user_id") or "",
        "status": session.get("status") or session.get("mode") or "recording",
        "steps": [to_legacy_step(action) for action in session.get("actions", [])],
        "sandbox_session_id": session.get("sandboxSessionId") or session.get("sandbox_session_id") or "",
        "paused": bool(session.get("paused", False)),
        "active_tab_id": active_tab_id,
    }


def to_legacy_tabs(session: dict[str, Any]) -> list[dict[str, Any]]:
    active_tab_id = session.get("activePageAlias") or session.get("active_tab_id")
    return [to_legacy_tab(page, active_tab_id) for page in session.get("pages", [])]
