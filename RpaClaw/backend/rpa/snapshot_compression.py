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
        record_list_region = _build_record_list_region(container_id, containers.get(container_id, {}), nodes, fallback_counts)
        if record_list_region:
            regions.append(record_list_region)
        regions.extend(_build_sectioned_label_value_regions(container_id, containers.get(container_id, {}), nodes, fallback_counts))

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


def compact_recording_snapshot(snapshot: Dict[str, Any], instruction: str, *, char_budget: int = 60000) -> Dict[str, Any]:
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
        "table_views": _compact_table_views(snapshot),
        "detail_views": _compact_detail_views(snapshot),
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
        "table_views": _compact_table_views(snapshot),
        "detail_views": _compact_detail_views(snapshot),
        "expanded_regions": [_expanded_region(region) for region in regions],
        "sampled_regions": [],
        "region_catalogue": list(region_catalogue),
    }


def _compact_table_views(snapshot: Dict[str, Any], *, row_limit: int = 10, cell_limit: int = 12) -> List[Dict[str, Any]]:
    views: List[Dict[str, Any]] = []
    for view in list(snapshot.get("table_views") or [])[:8]:
        rows = []
        for row in list(view.get("rows") or [])[:row_limit]:
            cells = []
            for cell in list(row.get("cells") or [])[:cell_limit]:
                cells.append(
                    {
                        "column_id": cell.get("column_id", ""),
                        "column_index": cell.get("column_index"),
                        "column_header": cell.get("column_header", ""),
                        "text": cell.get("text", ""),
                        "value_kind": cell.get("value_kind", ""),
                        "actions": list(cell.get("actions") or cell.get("row_local_actions") or [])[:4],
                    }
                )
            rows.append(
                {
                    "index": row.get("index"),
                    "cells": cells,
                    "locator_hints": list(row.get("locator_hints") or [])[:2],
                }
            )
        views.append(
            {
                "kind": "table_view",
                "title": view.get("title", ""),
                "title_source": view.get("title_source", ""),
                "nearby_headings": list(view.get("nearby_headings") or [])[:4],
                "framework_hint": view.get("framework_hint", ""),
                "frame_path": list(view.get("frame_path") or []),
                "row_count_observed": view.get("row_count_observed", 0),
                "columns": list(view.get("columns") or [])[:cell_limit],
                "rows": rows,
                "auxiliary_text": list(view.get("auxiliary_text") or [])[:5],
            }
        )
    return views


def _compact_detail_views(snapshot: Dict[str, Any], *, field_limit: int = 40) -> List[Dict[str, Any]]:
    views: List[Dict[str, Any]] = []
    for view in list(snapshot.get("detail_views") or [])[:12]:
        fields = []
        for field in list(view.get("fields") or [])[:field_limit]:
            fields.append(
                {
                    "label": field.get("label", ""),
                    "value": field.get("value", ""),
                    "data_prop": field.get("data_prop", ""),
                    "required": bool(field.get("required")),
                    "visible": bool(field.get("visible", True)),
                    "hidden_reason": field.get("hidden_reason", ""),
                    "value_kind": field.get("value_kind", ""),
                    "locator_hints": list(field.get("locator_hints") or [])[:2],
                }
            )
        views.append(
            {
                "kind": "detail_view",
                "section_title": view.get("section_title", ""),
                "section_locator": view.get("section_locator") or {},
                "frame_path": list(view.get("frame_path") or []),
                "fields": fields,
            }
        )
    return views


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


def _build_sectioned_label_value_regions(
    container_id: str,
    container: Dict[str, Any],
    nodes: Sequence[Dict[str, Any]],
    fallback_counts: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    ordered = _sort_nodes(nodes)
    headers = [node for node in ordered if _is_section_header_node(node)]
    if not headers:
        return []

    regions: List[Dict[str, Any]] = []
    for index, header in enumerate(headers):
        title = _clean_text(header.get("name") or header.get("text") or "")
        if not title:
            continue
        top = _node_y(header)
        next_top = _node_y(headers[index + 1]) if index + 1 < len(headers) else None
        section_nodes = [
            node
            for node in ordered
            if node is not header
            and not _is_section_header_node(node)
            and _node_y(node) > top
            and (next_top is None or _node_y(node) < next_top)
        ]
        pairs = _extract_label_value_pairs(section_nodes)
        if len(pairs) < 2:
            continue
        summary = " | ".join(f"{pair['label']}={pair['value']}" for pair in pairs[:4])
        regions.append(
            {
                "region_id": _region_id("section", _next_fallback_index(fallback_counts, "section"), container_id, title),
                "container_id": container_id,
                "kind": "label_value_group",
                "title": title,
                "summary": summary or title,
                "pairs": pairs,
                "frame_path": list(header.get("frame_path") or container.get("frame_path") or _first_frame_path(section_nodes)),
                "container_kind": "sectioned_form",
                "section_header": {
                    "label": title,
                    "locator": header.get("locator") or _best_locator(header),
                    "role": header.get("role") or "",
                },
            }
        )
    return regions


def _build_record_list_region(
    container_id: str,
    container: Dict[str, Any],
    nodes: Sequence[Dict[str, Any]],
    fallback_counts: Optional[Dict[str, int]] = None,
) -> Optional[Dict[str, Any]]:
    items = _extract_record_list_items(container, nodes)
    if len(items) < 3:
        return None

    title_node = _find_title_node(nodes, container)
    title = _clean_text(container.get("name") or (title_node or {}).get("text") or "")
    summary_parts: List[str] = []
    for item in items[:3]:
        primary = _clean_text(item.get("primary_text") or "")
        secondary = _clean_text(item.get("secondary_text") or "")
        if primary and secondary:
            summary_parts.append(f"{primary} ({secondary})")
        elif primary:
            summary_parts.append(primary)
    summary = " | ".join(summary_parts)
    frame_path = list(container.get("frame_path") or _first_frame_path(nodes))
    return {
        "region_id": container_id or _region_id("record_list", _next_fallback_index(fallback_counts, "record_list"), title, summary),
        "container_id": container_id,
        "kind": "record_list",
        "title": title or "record_list",
        "summary": summary or title or "record_list",
        "items": items,
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
    return bool(_extract_label_value_pairs(nodes))


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
    labels = [node for node in ordered if _is_label_node(node) and _clean_label_text(node.get("text") or "")]
    labels.sort(key=lambda node: (_node_y(node), _node_x(node), str(node.get("node_id") or "")))
    used_values: set[int] = set()
    value_entries = [(index, node) for index, node in enumerate(ordered) if _is_value_node(node)]
    seen_labels: set[str] = set()

    for label_node in labels:
        label_text = _clean_label_text(label_node.get("text") or "")
        if not label_text or label_text in seen_labels:
            continue
        value_entry = _find_value_for_label_by_position(label_node, value_entries, used_values)
        if not value_entry:
            continue
        value_index, value_node = value_entry
        used_values.add(value_index)
        seen_labels.add(label_text)
        pairs.append(
            {
                "label": label_text,
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


def _extract_record_list_items(container: Dict[str, Any], nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = _sort_nodes(nodes)
    anchor_nodes = _record_item_anchor_nodes(container, ordered)
    if len(anchor_nodes) < 3:
        return []

    items: List[Dict[str, Any]] = []
    for index, anchor in enumerate(anchor_nodes):
        top = _node_y(anchor) - 4
        next_top = _node_y(anchor_nodes[index + 1]) - 4 if index + 1 < len(anchor_nodes) else None
        section_nodes = [
            node
            for node in ordered
            if node is not anchor
            and _node_y(node) >= top
            and (next_top is None or _node_y(node) < next_top)
        ]
        primary_text = _clean_text(anchor.get("text") or anchor.get("name") or "")
        if not primary_text:
            continue
        secondary_text = _best_record_secondary_text(section_nodes, primary_text)
        item = {
            "primary_text": primary_text,
            "secondary_text": secondary_text,
            "locator": anchor.get("locator") or _best_locator(anchor),
        }
        href = _node_href(anchor)
        if href:
            item["href"] = href
        items.append(item)
    return items


def _record_item_anchor_nodes(container: Dict[str, Any], nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    item_nodes = [node for node in nodes if _normalize_kind(node.get("semantic_kind")) == "item" and _clean_text(node.get("text") or node.get("name") or "")]
    item_anchors = _dedupe_nodes_by_row(item_nodes)
    if len(item_anchors) >= 3:
        return item_anchors

    if _normalize_kind(container.get("container_kind")) != "list":
        return []

    grouped_links = _group_nodes_by_y_tolerance(
        [
            node
            for node in nodes
            if _is_action_node(node)
            and _normalize_kind(node.get("role")) == "link"
            and _clean_text(node.get("text") or node.get("name") or "")
        ],
        tolerance=20,
    )
    anchors: List[Dict[str, Any]] = []
    for group in grouped_links:
        preferred = max(
            group,
            key=lambda node: (
                len(_clean_text(node.get("text") or node.get("name") or "")),
                1 if _node_href(node) else 0,
                -_node_x(node),
            ),
        )
        if _clean_text(preferred.get("text") or preferred.get("name") or ""):
            anchors.append(preferred)
    return _dedupe_nodes_by_row(anchors)


def _dedupe_nodes_by_row(nodes: Sequence[Dict[str, Any]], tolerance: int = 20) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    for group in _group_nodes_by_y_tolerance(nodes, tolerance=tolerance):
        if not group:
            continue
        deduped.append(
            min(
                group,
                key=lambda node: (
                    _node_x(node),
                    str(node.get("node_id") or ""),
                ),
            )
        )
    return deduped


def _best_record_secondary_text(nodes: Sequence[Dict[str, Any]], primary_text: str) -> str:
    candidates = []
    for node in _sort_nodes(nodes):
        text = _clean_text(node.get("text") or node.get("name") or "")
        if not text or text == primary_text:
            continue
        if _normalize_kind(node.get("semantic_kind")) == "item":
            continue
        if _normalize_kind(node.get("role")) in {"link", "button"} and len(text) <= 4:
            continue
        candidates.append(
            (
                0 if _normalize_kind(node.get("semantic_kind")) == "text" else 1,
                abs(_node_y(node) - (_node_y(nodes[0]) if nodes else 0)),
                -len(text),
                text,
            )
        )
    return candidates[0][3] if candidates else ""


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
    return _llm_region_base(region)


def _expanded_region(region: Dict[str, Any]) -> Dict[str, Any]:
    result = _llm_region_base(region)
    result["evidence"] = _region_evidence(region, limit=None)
    locator_hints = _locator_hints(region)
    if locator_hints:
        result["locator_hints"] = locator_hints
    result["mode"] = "expanded"
    return result


def _sampled_region(region: Dict[str, Any]) -> Dict[str, Any]:
    sampled = _llm_region_base(region)
    sampled["evidence"] = _region_evidence(region, limit=3)
    locator_hints = _locator_hints(region)
    if locator_hints:
        sampled["locator_hints"] = locator_hints[:2]
    sampled["mode"] = "sampled"
    return sampled


def _llm_region_base(region: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": region.get("kind", ""),
        "title": region.get("title", ""),
        "summary": region.get("summary", ""),
        "frame_path": list(region.get("frame_path") or []),
    }


def _region_evidence(region: Dict[str, Any], *, limit: Optional[int]) -> Dict[str, Any]:
    kind = _normalize_kind(region.get("kind"))
    if kind == "label_value_group":
        pairs = []
        pair_items = list(region.get("pairs") or [])
        if limit is not None:
            pair_items = pair_items[:limit]
        for pair in pair_items:
            pairs.append(
                {
                    "label": pair.get("label", ""),
                    "value": pair.get("value", ""),
                    "label_locator": pair.get("label_locator") or {},
                    "value_locator": pair.get("value_locator") or {},
                }
            )
        return {"pairs": pairs}

    if kind == "record_list":
        item_entries = list(region.get("items") or [])
        if limit is not None:
            item_entries = item_entries[:limit]
        items = []
        for item in item_entries:
            record = {
                "primary_text": item.get("primary_text", ""),
                "secondary_text": item.get("secondary_text", ""),
                "locator": item.get("locator") or {},
            }
            if item.get("href"):
                record["href"] = item.get("href")
            items.append(record)
        return {"items": items}

    if kind == "table":
        row_items = list(region.get("sample_rows") or [])
        if limit is not None:
            row_items = row_items[:limit]
        return {
            "headers": list(region.get("headers") or [])[:6],
            "sample_rows": [list(row)[:6] for row in row_items],
        }

    if kind == "action_group":
        actions = []
        action_items = list(region.get("actions") or [])
        if limit is not None:
            action_items = action_items[:limit]
        for action in action_items:
            actions.append(
                {
                    "label": action.get("label", ""),
                    "locator": action.get("locator") or {},
                }
            )
        return {"actions": actions}

    excerpt = _clean_text(region.get("summary") or region.get("title") or "")
    if excerpt:
        return {"excerpt": excerpt[:160]}
    return {}


def _locator_hints(region: Dict[str, Any]) -> List[Dict[str, Any]]:
    kind = _normalize_kind(region.get("kind"))
    if kind == "label_value_group":
        return _label_value_locator_hints(region)
    if kind == "record_list":
        return _record_list_locator_hints(region)
    if kind == "table":
        return _table_locator_hints(region)
    return []


def _label_value_locator_hints(region: Dict[str, Any]) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []
    section_header = region.get("section_header") if isinstance(region.get("section_header"), dict) else {}
    locator = section_header.get("locator") if isinstance(section_header, dict) else None
    if isinstance(locator, dict) and locator:
        expression = _playwright_locator_expression("page", locator)
        if expression:
            hints.append(
                {
                    "kind": "playwright",
                    "method": locator.get("method") or "locator",
                    "expression": expression,
                    "source": "section_header",
                    "purpose": "identify the visible section header that scopes the following readonly fields",
                }
            )
    return hints


def _record_list_locator_hints(region: Dict[str, Any]) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []
    for item in list(region.get("items") or [])[:2]:
        locator = item.get("locator") if isinstance(item, dict) else None
        if not isinstance(locator, dict) or not locator:
            continue
        expression = _playwright_locator_expression("page", locator)
        if not expression:
            continue
        hints.append(
            {
                "kind": "playwright",
                "method": locator.get("method") or "locator",
                "expression": expression,
                "source": "list_item",
                "purpose": "identify representative visible list items before extracting repeated records",
            }
        )
    return hints


def _table_locator_hints(region: Dict[str, Any]) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []
    headers = [_clean_text(header) for header in list(region.get("headers") or []) if _clean_text(header)]
    title = _clean_text(region.get("title") or "")

    if headers:
        primary_header = headers[0]
        hints.append(
            {
                "kind": "playwright",
                "method": "locator_filter",
                "expression": f"page.locator('table').filter(has_text={primary_header!r})",
                "source": "table_header",
                "purpose": "scope a native table by visible header text",
            }
        )
        if len(headers) > 1:
            hints.append(
                {
                    "kind": "playwright",
                    "method": "locator_filter",
                    "expression": f"page.locator('table').filter(has_text={headers[1]!r})",
                    "source": "table_header",
                    "purpose": "secondary header fallback for similar tables",
                }
            )

    if title and title.lower() not in {"table", "text_section"}:
        hints.append(
            {
                "kind": "playwright",
                "method": "role",
                "expression": f"page.get_by_role('table', name={title!r})",
                "source": "region_title",
                "purpose": "prefer an accessible table name when the page exposes one",
            }
        )

    return hints


def _region_relevance(region: Dict[str, Any], instruction: str) -> float:
    title_overlap = ngram_overlap(str(instruction or ""), str(region.get("title") or ""))
    summary_overlap = ngram_overlap(str(instruction or ""), str(region.get("summary") or ""))
    score = max(title_overlap, summary_overlap)
    if _looks_like_record_list_task(instruction) and _normalize_kind(region.get("kind")) == "record_list":
        score += 0.35
    return score


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
    text = _clean_text(node.get("text") or "")
    if not text or text == "*":
        return False
    semantic_kind = _normalize_kind(node.get("semantic_kind"))
    tag = _normalize_kind(node.get("element_snapshot", {}).get("tag"))
    classes = str(node.get("element_snapshot", {}).get("class") or "").lower()
    if semantic_kind == "field_value":
        return True
    if "field-value" in classes or "value" in classes:
        return True
    if "display-only" in classes or "no-value" in classes:
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


def _is_section_header_node(node: Dict[str, Any]) -> bool:
    text = _clean_text(node.get("name") or node.get("text") or "")
    if not text:
        return False
    role = _normalize_kind(node.get("role"))
    tag = _normalize_kind(node.get("element_snapshot", {}).get("tag"))
    if role == "tab" or tag == "summary":
        return True
    width = int((node.get("bbox") or {}).get("width", 0) or 0)
    action_kinds = {str(item).lower() for item in (node.get("action_kinds") or [])}
    return role == "button" and "click" in action_kinds and width >= 160


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


def _playwright_locator_expression(scope: str, locator: Dict[str, Any]) -> str:
    method = locator.get("method")
    if method == "role" or (method is None and locator.get("role")):
        role = locator.get("role", "")
        name = locator.get("name")
        args = [repr(role)]
        if name:
            args.append(f"name={name!r}")
        return f"{scope}.get_by_role({', '.join(args)})"
    if method == "text":
        return f"{scope}.get_by_text({locator.get('value', '')!r})"
    if method == "label":
        return f"{scope}.get_by_label({locator.get('value', '')!r})"
    if method == "css":
        return f"{scope}.locator({locator.get('value', 'body')!r})"
    return ""


def _node_href(node: Dict[str, Any]) -> str:
    element_snapshot = node.get("element_snapshot") if isinstance(node.get("element_snapshot"), dict) else {}
    href = element_snapshot.get("href") if isinstance(element_snapshot, dict) else ""
    return _clean_text(href)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_label_text(value: Any) -> str:
    text = _clean_text(value)
    while text.startswith("*"):
        text = _clean_text(text[1:])
    return text


def _normalize_kind(value: Any) -> str:
    return str(value or "").strip().lower()


def _looks_like_record_list_task(instruction: str) -> bool:
    text = str(instruction or "").lower()
    patterns = (
        "collect",
        "list",
        "array",
        "each",
        "every",
        "titles and authors",
        "records",
        "收集",
        "列表",
        "数组",
        "每个",
        "前两页",
        "标题",
        "创建人",
    )
    return any(pattern in text for pattern in patterns)


def _region_rank(region: Dict[str, Any]) -> int:
    kind = _normalize_kind(region.get("kind"))
    if kind == "label_value_group":
        return 0
    if kind == "table":
        return 1
    if kind == "record_list":
        return 2
    if kind == "action_group":
        return 3
    return 4


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
        if _looks_like_title_node(node) or _is_section_header_node(node):
            continue
        if _is_label_node(node) or _is_value_node(node):
            continue
        text = _clean_text(node.get("text") or "")
        if text:
            return text
    return ""
