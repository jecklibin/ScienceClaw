from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_TIER1_EXPAND_CAP = 3
_TIER1_NEAR_TIE_DELTA = 0.08


def ngram_overlap(left: str, right: str) -> float:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text or not right_text:
        return 0.0
    if left_text == right_text:
        return 1.0

    left_grams = _char_ngrams(left_text)
    right_grams = _char_ngrams(right_text)
    if not left_grams or not right_grams:
        return 0.0

    shared = len(left_grams & right_grams)
    total = len(left_grams | right_grams)
    return shared / total if total else 0.0


def build_structured_regions(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    content_nodes = list(snapshot.get("content_nodes") or [])
    actionable_nodes = list(snapshot.get("actionable_nodes") or [])
    containers = _index_containers(snapshot.get("containers") or [])
    frame_actions = _collect_frame_actions(snapshot.get("frames") or [])

    grouped_nodes: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for node in content_nodes:
        grouped_nodes[str(node.get("container_id") or "")].append(node)
    for node in actionable_nodes:
        grouped_nodes[str(node.get("container_id") or "")].append(node)
    for node in frame_actions:
        grouped_nodes[str(node.get("container_id") or "")].append(node)

    regions: List[Dict[str, Any]] = []
    fallback_counts: Dict[str, int] = defaultdict(int)
    for container_id, nodes in grouped_nodes.items():
        region = _build_region(container_id, containers.get(container_id, {}), nodes, fallback_counts)
        if region:
            regions.append(region)

    for container_id, container in containers.items():
        if container_id not in grouped_nodes:
            region = _build_region(container_id, container, [], fallback_counts)
            if region:
                regions.append(region)

    regions.sort(key=lambda item: (_region_rank(item), item.get("title", ""), item.get("region_id", "")))
    return regions


def tier_regions(regions: Sequence[Dict[str, Any]], instruction: str) -> Dict[str, List[Dict[str, Any]]]:
    scored = [
        {
            "region": region,
            "score": _region_relevance(region, instruction),
        }
        for region in regions
    ]
    scored.sort(key=lambda item: (-item["score"], _region_rank(item["region"]), item["region"].get("title", "")))

    tier1: List[Dict[str, Any]] = []
    tier2: List[Dict[str, Any]] = []
    tier3: List[Dict[str, Any]] = []

    if scored:
        top_score = scored[0]["score"]
        near_tie_threshold = max(0.0, top_score - _TIER1_NEAR_TIE_DELTA)
        for index, item in enumerate(scored):
            if index == 0:
                tier1.append(item["region"])
                continue
            if len(tier1) >= _TIER1_EXPAND_CAP:
                break
            if item["score"] > 0.0 and item["score"] >= near_tie_threshold:
                tier1.append(item["region"])
                continue
            break

        remaining = [item for item in scored if item["region"] not in tier1]
        for item in remaining:
            if item["score"] >= 0.15 and len(tier2) < 2:
                tier2.append(item["region"])
            else:
                tier3.append(item["region"])

        if not tier2 and remaining:
            tier2.append(remaining[0]["region"])
        if not tier3 and len(remaining) > 1:
            tier3.extend(item["region"] for item in remaining[1:])

        tier3 = [region for region in tier3 if region not in tier2]

    return {
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
        "region_catalogue": [_summary_region(region) for region in regions],
    }


def compact_recording_snapshot(snapshot: Dict[str, Any], instruction: str, *, char_budget: int = 12000) -> Dict[str, Any]:
    regions = build_structured_regions(snapshot)
    tiers = tier_regions(regions, instruction)
    clean_payload = _build_clean_payload(snapshot, regions, tiers["region_catalogue"])
    clean_size = len(json.dumps(clean_payload, ensure_ascii=False, sort_keys=True))

    if clean_size <= char_budget:
        return {"mode": "clean_snapshot", **clean_payload}

    expanded_regions = [_expanded_region(region) for region in tiers["tier1"]]
    sampled_regions = [_sampled_region(region) for region in tiers["tier2"]]
    return {
        "mode": "tiered_snapshot",
        "url": snapshot.get("url", ""),
        "title": snapshot.get("title", ""),
        "expanded_regions": expanded_regions,
        "sampled_regions": sampled_regions,
        "region_catalogue": tiers["region_catalogue"],
    }


def _build_clean_payload(
    snapshot: Dict[str, Any],
    regions: Sequence[Dict[str, Any]],
    region_catalogue: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "url": snapshot.get("url", ""),
        "title": snapshot.get("title", ""),
        "expanded_regions": [_expanded_region(region) for region in regions],
        "sampled_regions": [],
        "region_catalogue": list(region_catalogue),
    }


def _build_region(
    container_id: str,
    container: Dict[str, Any],
    nodes: List[Dict[str, Any]],
    fallback_counts: Optional[Dict[str, int]] = None,
) -> Optional[Dict[str, Any]]:
    nodes = _sort_nodes(nodes)
    if not container and not nodes:
        return None

    title_node = _find_title_node(nodes, container)
    intro_text = _extract_intro_text(nodes)
    title = _clean_text(intro_text or container.get("name") or (title_node or {}).get("text") or "")
    summary = _build_summary(container, nodes, title_node)
    frame_path = list(container.get("frame_path") or _first_frame_path(nodes))

    if _looks_like_label_value_group(container, nodes):
        return {
            "region_id": container_id or _region_id("label_value_group", _next_fallback_index(fallback_counts, "label_value_group"), title, summary),
            "container_id": container_id,
            "kind": "label_value_group",
            "title": title or _extract_label_value_title(nodes) or "label_value_group",
            "summary": summary,
            "pairs": _extract_label_value_pairs(nodes),
            "frame_path": frame_path,
            "container_kind": container.get("container_kind", ""),
        }

    if _looks_like_table(container, nodes):
        headers, sample_rows = _extract_table_data(nodes)
        return {
            "region_id": container_id or _region_id("table", _next_fallback_index(fallback_counts, "table"), title, summary),
            "container_id": container_id,
            "kind": "table",
            "title": title or _extract_table_title(nodes) or "table",
            "summary": summary,
            "headers": headers,
            "sample_rows": sample_rows,
            "frame_path": frame_path,
            "container_kind": container.get("container_kind", ""),
        }

    if _looks_like_action_group(container, nodes):
        return {
            "region_id": container_id or _region_id("action_group", _next_fallback_index(fallback_counts, "action_group"), title, summary),
            "container_id": container_id,
            "kind": "action_group",
            "title": title or container.get("name") or "actions",
            "summary": summary,
            "actions": _extract_actions(nodes),
            "frame_path": frame_path,
            "container_kind": container.get("container_kind", ""),
        }

    return {
        "region_id": container_id or _region_id("text_section", _next_fallback_index(fallback_counts, "text_section"), title, summary),
        "container_id": container_id,
        "kind": "text_section",
        "title": title or container.get("name") or "text_section",
        "summary": summary,
        "frame_path": frame_path,
        "container_kind": container.get("container_kind", ""),
    }


def _index_containers(containers: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for container in containers:
        container_id = str(container.get("container_id") or "")
        if container_id:
            indexed[container_id] = dict(container)
    return indexed


def _collect_frame_actions(frames: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for frame in frames:
        frame_path = list(frame.get("frame_path") or [])
        for element in frame.get("elements") or []:
            role = str(element.get("role") or "").lower()
            tag = str(element.get("tag") or "").lower()
            if role in {"link", "button"} or tag in {"a", "button"}:
                text = _clean_text(element.get("name") or element.get("text") or "")
                actions.append(
                    {
                        "container_id": str(element.get("container_id") or frame.get("frame_hint") or "frame-actions"),
                        "semantic_kind": "action",
                        "role": role or tag,
                        "text": text,
                        "name": text,
                        "frame_path": frame_path,
                        "element_snapshot": {"tag": tag, "text": text},
                        "locator": element.get("locator") or _best_locator({"role": role or tag, "text": text, "element_snapshot": {"tag": tag}}),
                    }
                )
    return actions


def _looks_like_label_value_group(container: Dict[str, Any], nodes: Sequence[Dict[str, Any]]) -> bool:
    labels = [_ for _ in nodes if _is_label_node(_)]
    values = [_ for _ in nodes if _is_value_node(_)]
    return bool(labels and values and len(values) >= len(labels))


def _looks_like_table(container: Dict[str, Any], nodes: Sequence[Dict[str, Any]]) -> bool:
    if _normalize_kind(container.get("container_kind")) == "table":
        return True
    header_count = sum(1 for node in nodes if _is_table_header(node))
    body_count = sum(1 for node in nodes if _is_table_body_cell(node))
    row_groups = _group_nodes_by_y_tolerance([node for node in nodes if _is_table_header(node) or _is_table_body_cell(node)])
    header_groups = [group for group in row_groups if sum(1 for node in group if _is_table_header(node)) >= 2]
    body_groups = [group for group in row_groups if sum(1 for node in group if _is_table_body_cell(node)) >= 2]
    return header_count >= 2 and body_count >= 2 and bool(header_groups) and bool(body_groups)


def _looks_like_action_group(container: Dict[str, Any], nodes: Sequence[Dict[str, Any]]) -> bool:
    if _normalize_kind(container.get("container_kind")) in {"toolbar", "menu", "action_group"}:
        return True
    return any(_is_action_node(node) for node in nodes)


def _extract_label_value_pairs(nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = _sort_nodes(nodes)
    pairs: List[Dict[str, Any]] = []
    labels = [node for node in ordered if _is_label_node(node)]
    labels.sort(key=lambda node: (_node_y(node), _node_x(node), str(node.get("node_id") or "")))
    used_values: set[int] = set()
    value_entries = [(index, node) for index, node in enumerate(ordered) if _is_value_node(node)]

    for label_node in labels:
        value_entry = _find_value_for_label_by_position(label_node, value_entries, used_values)
        if not value_entry:
            continue
        value_index, value_node = value_entry
        used_values.add(value_index)
        pairs.append(
            {
                "label": _clean_text(label_node.get("text") or ""),
                "value": _clean_text(value_node.get("text") or ""),
                "label_locator": label_node.get("locator") or _best_locator(label_node),
                "value_locator": value_node.get("locator") or _best_locator(value_node),
                "value_hint": value_node.get("data_field") or value_node.get("semantic_kind") or "",
            }
        )
    return pairs


def _extract_table_data(nodes: Sequence[Dict[str, Any]]) -> Tuple[List[str], List[List[str]]]:
    ordered = _sort_nodes(nodes)
    headers: List[str] = []
    table_nodes = [node for node in ordered if _is_table_header(node) or _is_table_body_cell(node)]
    grouped = _group_nodes_by_y_tolerance(table_nodes)
    if grouped:
        header_group = grouped[0]
        if any(_is_table_header(node) for node in header_group):
            headers = [_clean_text(node.get("text") or "") for node in sorted(header_group, key=lambda node: (int((node.get("bbox") or {}).get("x", 0) or 0), str(node.get("node_id") or ""))) if _is_table_header(node)]

    sample_rows: List[List[str]] = []
    body_groups = grouped[1:] if grouped and any(_is_table_header(node) for node in grouped[0]) else grouped
    for group in body_groups:
        row_cells = [node for node in group if _is_table_body_cell(node)]
        if not row_cells:
            continue
        sample_rows.append(
            [
                _clean_text(node.get("text") or "")
                for node in sorted(
                    row_cells,
                    key=lambda node: (int((node.get("bbox") or {}).get("x", 0) or 0), str(node.get("node_id") or "")),
                )
            ]
        )
    return headers, sample_rows[:3]


def _extract_actions(nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for node in _sort_nodes(nodes):
        if _is_action_node(node):
            actions.append(
                {
                    "label": _clean_text(node.get("name") or node.get("text") or ""),
                    "locator": node.get("locator") or _best_locator(node),
                }
            )
    return actions


def _build_summary(container: Dict[str, Any], nodes: Sequence[Dict[str, Any]], title_node: Optional[Dict[str, Any]]) -> str:
    title = _clean_text(container.get("summary") or container.get("name") or (title_node or {}).get("text") or "")
    if _normalize_kind(container.get("container_kind")) == "table":
        headers, rows = _extract_table_data(nodes)
        parts = [title] if title else []
        if headers:
            parts.append("headers=" + ",".join(headers))
        if rows:
            parts.append("sample=" + "; ".join(" / ".join(row) for row in rows[:2]))
        return " | ".join(parts) if parts else "table"
    if _looks_like_label_value_group(container, nodes):
        pairs = _extract_label_value_pairs(nodes)
        if pairs:
            return " | ".join(f"{pair['label']}={pair['value']}" for pair in pairs[:4])
    if _looks_like_action_group(container, nodes):
        actions = _extract_actions(nodes)
        if actions:
            return " | ".join(action["label"] for action in actions[:4])
    if title:
        return title
    texts = [_clean_text(node.get("text") or "") for node in nodes if _clean_text(node.get("text") or "")]
    return " | ".join(texts[:4]) if texts else "empty region"


def _summary_region(region: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "region_id": region.get("region_id", ""),
        "container_id": region.get("container_id", ""),
        "kind": region.get("kind", ""),
        "title": region.get("title", ""),
        "summary": region.get("summary", ""),
        "frame_path": list(region.get("frame_path") or []),
        "container_kind": region.get("container_kind", ""),
    }


def _expanded_region(region: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(region)
    result["mode"] = "expanded"
    return result


def _sampled_region(region: Dict[str, Any]) -> Dict[str, Any]:
    sampled = _summary_region(region)
    sampled["mode"] = "sampled"
    kind = _normalize_kind(region.get("kind"))
    if kind == "label_value_group":
        sampled["pairs"] = []
        for pair in list(region.get("pairs") or [])[:3]:
            sampled["pairs"].append(
                {
                    "label": pair.get("label", ""),
                    "value": pair.get("value", ""),
                    "label_locator": pair.get("label_locator") or {},
                    "value_locator": pair.get("value_locator") or {},
                }
            )
        return sampled

    if kind == "table":
        sampled["headers"] = list(region.get("headers") or [])[:6]
        sampled["sample_rows"] = [list(row)[:6] for row in list(region.get("sample_rows") or [])[:3]]
        return sampled

    if kind == "action_group":
        sampled["actions"] = []
        for action in list(region.get("actions") or [])[:3]:
            sampled["actions"].append(
                {
                    "label": action.get("label", ""),
                    "locator": action.get("locator") or {},
                }
            )
        return sampled

    excerpt = _clean_text(region.get("summary") or region.get("title") or "")
    if excerpt:
        sampled["excerpt"] = excerpt[:160]
    return sampled


def _region_relevance(region: Dict[str, Any], instruction: str) -> float:
    title_overlap = ngram_overlap(str(instruction or ""), str(region.get("title") or ""))
    summary_overlap = ngram_overlap(str(instruction or ""), str(region.get("summary") or ""))
    return max(title_overlap, summary_overlap)


def _find_title_node(nodes: Sequence[Dict[str, Any]], container: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for node in nodes:
        if _looks_like_title_node(node):
            return node
    container_name = _clean_text(container.get("name") or "")
    if container_name:
        for node in nodes:
            if _clean_text(node.get("text") or "") == container_name:
                return node
    return None


def _extract_label_value_title(nodes: Sequence[Dict[str, Any]]) -> str:
    for node in nodes:
        if _is_label_node(node):
            return _clean_text(node.get("text") or "")
    return ""


def _extract_table_title(nodes: Sequence[Dict[str, Any]]) -> str:
    for node in nodes:
        if _looks_like_title_node(node):
            return _clean_text(node.get("text") or "")
    return ""


def _find_value_for_label(nodes: Sequence[Dict[str, Any]], start_index: int, used_value_indexes: set[int]) -> Optional[Dict[str, Any]]:
    for index in range(start_index, len(nodes)):
        node = nodes[index]
        if index in used_value_indexes:
            continue
        if _is_label_node(node) or _is_table_header(node) or _looks_like_title_node(node):
            break
        if _is_value_node(node):
            return node
    return None


def _find_value_for_label_by_position(
    label_node: Dict[str, Any],
    values: Sequence[Tuple[int, Dict[str, Any]]],
    used_value_indexes: set[int],
) -> Optional[Tuple[int, Dict[str, Any]]]:
    label_x = _node_x(label_node)
    label_y = _node_y(label_node)
    best_entry: Optional[Tuple[int, Dict[str, Any]]] = None
    best_score: Optional[Tuple[int, int, int]] = None

    for ordered_index, value_node in values:
        if ordered_index in used_value_indexes:
            continue
        value_x = _node_x(value_node)
        value_y = _node_y(value_node)
        if value_y < label_y - 4:
            continue
        x_gap = abs(value_x - label_x)
        y_gap = max(value_y - label_y, 0)
        score = (x_gap, y_gap, abs(value_y - label_y), ordered_index)
        if best_score is None or score < best_score:
            best_score = score
            best_entry = (ordered_index, value_node)

    return best_entry


def _is_label_node(node: Dict[str, Any]) -> bool:
    return _normalize_kind(node.get("semantic_kind")) == "label" or _normalize_kind(node.get("element_snapshot", {}).get("tag")) == "label"


def _is_value_node(node: Dict[str, Any]) -> bool:
    semantic_kind = _normalize_kind(node.get("semantic_kind"))
    tag = _normalize_kind(node.get("element_snapshot", {}).get("tag"))
    classes = str(node.get("element_snapshot", {}).get("class") or "")
    if semantic_kind == "field_value":
        return True
    if "field-value" in classes or "value" in classes:
        return True
    if node.get("data_field"):
        return True
    if node.get("data_value") is not None:
        return True
    return False


def _is_table_header(node: Dict[str, Any]) -> bool:
    return _normalize_kind(node.get("semantic_kind")) == "header_cell" or _normalize_kind(node.get("element_snapshot", {}).get("tag")) == "th"


def _is_table_body_cell(node: Dict[str, Any]) -> bool:
    semantic_kind = _normalize_kind(node.get("semantic_kind"))
    tag = _normalize_kind(node.get("element_snapshot", {}).get("tag"))
    return semantic_kind in {"cell", "text"} and tag not in {"th", "h1", "h2", "h3", "h4", "h5", "h6", "label"}


def _is_action_node(node: Dict[str, Any]) -> bool:
    role = _normalize_kind(node.get("role"))
    tag = _normalize_kind(node.get("element_snapshot", {}).get("tag"))
    return role in {"button", "link"} or tag in {"a", "button"} or _normalize_kind(node.get("semantic_kind")) == "action"


def _looks_like_title_node(node: Dict[str, Any]) -> bool:
    semantic_kind = _normalize_kind(node.get("semantic_kind"))
    tag = _normalize_kind(node.get("element_snapshot", {}).get("tag"))
    return semantic_kind == "heading" or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}


def _node_row_key(node: Dict[str, Any]) -> int:
    return int((node.get("bbox") or {}).get("y", 0) or 0)


def _first_frame_path(nodes: Sequence[Dict[str, Any]]) -> List[str]:
    for node in nodes:
        frame_path = node.get("frame_path")
        if isinstance(frame_path, list) and frame_path:
            return list(frame_path)
    return []


def _node_y(node: Dict[str, Any]) -> int:
    return int((node.get("bbox") or {}).get("y", 0) or 0)


def _node_x(node: Dict[str, Any]) -> int:
    return int((node.get("bbox") or {}).get("x", 0) or 0)


def _sort_nodes(nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        (dict(node) for node in nodes),
        key=lambda node: (
            int((node.get("bbox") or {}).get("y", 0) or 0),
            int((node.get("bbox") or {}).get("x", 0) or 0),
            str(node.get("node_id") or ""),
        ),
    )


def _group_nodes_by_y_tolerance(nodes: Sequence[Dict[str, Any]], tolerance: int = 8) -> List[List[Dict[str, Any]]]:
    ordered = _sort_nodes(nodes)
    groups: List[List[Dict[str, Any]]] = []
    current_group: List[Dict[str, Any]] = []
    last_y: Optional[int] = None

    for node in ordered:
        y = _node_y(node)
        if last_y is None or abs(y - last_y) > tolerance:
            if current_group:
                groups.append(current_group)
            current_group = [node]
            last_y = y
            continue
        current_group.append(node)
        last_y = y
    if current_group:
        groups.append(current_group)
    return groups


def _best_locator(node: Dict[str, Any]) -> Dict[str, Any]:
    locator = node.get("locator")
    if isinstance(locator, dict) and locator:
        return locator
    candidates = node.get("locator_candidates") or []
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("selected") and isinstance(candidate.get("locator"), dict):
            return candidate["locator"]
    if candidates and isinstance(candidates[0], dict) and isinstance(candidates[0].get("locator"), dict):
        return candidates[0]["locator"]
    text = _clean_text(node.get("text") or node.get("name") or "")
    if text:
        return {"method": "text", "value": text}
    role = _clean_text(node.get("role") or "")
    if role:
        return {"method": "role", "role": role, "name": text}
    return {"method": "css", "value": node.get("element_snapshot", {}).get("tag") or "body"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_kind(value: Any) -> str:
    return str(value or "").strip().lower()


def _region_rank(region: Dict[str, Any]) -> int:
    kind = _normalize_kind(region.get("kind"))
    if kind == "label_value_group":
        return 0
    if kind == "table":
        return 1
    if kind == "action_group":
        return 2
    return 3


def _next_fallback_index(fallback_counts: Optional[Dict[str, int]], prefix: str) -> int:
    if fallback_counts is None:
        return 1
    fallback_counts[prefix] += 1
    return fallback_counts[prefix]


def _region_id(prefix: str, ordinal: Optional[int] = None, *seed_parts: Any) -> str:
    slug_parts = [_stable_slug(part) for part in seed_parts if _stable_slug(part)]
    suffix_parts = [prefix]
    if slug_parts:
        suffix_parts.append("-".join(slug_parts[:2]))
    if ordinal is not None:
        suffix_parts.append(str(int(ordinal)))
    return "-".join(part for part in suffix_parts if part)


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    compact = "".join(ch for ch in text if not ch.isspace())
    if not compact:
        return set()
    if len(compact) <= n:
        return {compact}
    return {compact[index : index + n] for index in range(len(compact) - n + 1)}


def _stable_slug(value: Any) -> str:
    text = _clean_text(value).lower()
    if not text:
        return ""
    chars: List[str] = []
    for char in text:
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    slug = "".join(chars).strip("-")
    return slug[:32]


def _extract_intro_text(nodes: Sequence[Dict[str, Any]]) -> str:
    ordered = _sort_nodes(nodes)
    first_label_y: Optional[int] = None
    for node in ordered:
        if _is_label_node(node):
            first_label_y = _node_y(node)
            break
    if first_label_y is None:
        return ""

    for node in ordered:
        if _node_y(node) >= first_label_y:
            break
        if _looks_like_title_node(node):
            continue
        if _is_label_node(node) or _is_value_node(node):
            continue
        text = _clean_text(node.get("text") or "")
        if text:
            return text
    return ""
