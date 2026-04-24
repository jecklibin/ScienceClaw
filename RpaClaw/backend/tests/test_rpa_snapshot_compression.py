from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "rpa" / "snapshot_compression.py"
_SPEC = spec_from_file_location("snapshot_compression_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

build_structured_regions = _MODULE.build_structured_regions
compact_recording_snapshot = _MODULE.compact_recording_snapshot
ngram_overlap = _MODULE.ngram_overlap
tier_regions = _MODULE.tier_regions
_sampled_region = _MODULE._sampled_region


def _build_snapshot() -> dict:
    return {
        "url": "https://example.com/detail",
        "title": "采购单据详情",
        "content_nodes": [
            {
                "node_id": "title-1",
                "container_id": "basic-section",
                "semantic_kind": "heading",
                "role": "heading",
                "text": "单据基本信息",
                "bbox": {"x": 24, "y": 24, "width": 160, "height": 24},
                "locator": {"method": "text", "value": "单据基本信息"},
                "element_snapshot": {"tag": "h2", "text": "单据基本信息"},
            },
            {
                "node_id": "label-1",
                "container_id": "basic-section",
                "semantic_kind": "label",
                "role": "label",
                "text": "购买人",
                "bbox": {"x": 24, "y": 68, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "购买人"},
                "element_snapshot": {"tag": "label", "text": "购买人"},
            },
            {
                "node_id": "value-1",
                "container_id": "basic-section",
                "semantic_kind": "text",
                "role": "",
                "text": "李雨晨",
                "bbox": {"x": 140, "y": 68, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "李雨晨"},
                "element_snapshot": {"tag": "span", "text": "李雨晨", "class": "field-value"},
                "data_field": "buyer",
            },
            {
                "node_id": "label-2",
                "container_id": "basic-section",
                "semantic_kind": "label",
                "role": "label",
                "text": "使用部门",
                "bbox": {"x": 24, "y": 98, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "使用部门"},
                "element_snapshot": {"tag": "label", "text": "使用部门"},
            },
            {
                "node_id": "value-2",
                "container_id": "basic-section",
                "semantic_kind": "text",
                "role": "",
                "text": "研发效能组",
                "bbox": {"x": 140, "y": 98, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "研发效能组"},
                "element_snapshot": {"tag": "span", "text": "研发效能组", "class": "field-value"},
                "data_field": "department",
            },
            {
                "node_id": "table-title",
                "container_id": "table-section",
                "semantic_kind": "heading",
                "role": "heading",
                "text": "采购明细",
                "bbox": {"x": 24, "y": 160, "width": 120, "height": 24},
                "locator": {"method": "text", "value": "采购明细"},
                "element_snapshot": {"tag": "h2", "text": "采购明细"},
            },
            {
                "node_id": "th-1",
                "container_id": "table-section",
                "semantic_kind": "header_cell",
                "role": "columnheader",
                "text": "物品名称",
                "bbox": {"x": 24, "y": 202, "width": 120, "height": 24},
                "locator": {"method": "text", "value": "物品名称"},
                "element_snapshot": {"tag": "th", "text": "物品名称"},
            },
            {
                "node_id": "th-2",
                "container_id": "table-section",
                "semantic_kind": "header_cell",
                "role": "columnheader",
                "text": "数量",
                "bbox": {"x": 160, "y": 202, "width": 80, "height": 24},
                "locator": {"method": "text", "value": "数量"},
                "element_snapshot": {"tag": "th", "text": "数量"},
            },
            {
                "node_id": "td-1",
                "container_id": "table-section",
                "semantic_kind": "cell",
                "role": "cell",
                "text": "显示器",
                "bbox": {"x": 24, "y": 232, "width": 120, "height": 24},
                "locator": {"method": "text", "value": "显示器"},
                "element_snapshot": {"tag": "td", "text": "显示器"},
            },
            {
                "node_id": "td-2",
                "container_id": "table-section",
                "semantic_kind": "cell",
                "role": "cell",
                "text": "2",
                "bbox": {"x": 160, "y": 232, "width": 80, "height": 24},
                "locator": {"method": "text", "value": "2"},
                "element_snapshot": {"tag": "td", "text": "2"},
            },
            {
                "node_id": "link-1",
                "container_id": "action-section",
                "semantic_kind": "text",
                "role": "link",
                "text": "返回列表",
                "bbox": {"x": 24, "y": 288, "width": 80, "height": 20},
                "locator": {"method": "role", "role": "link", "name": "返回列表"},
                "element_snapshot": {"tag": "a", "text": "返回列表"},
            },
            {
                "node_id": "button-1",
                "container_id": "action-section",
                "semantic_kind": "text",
                "role": "button",
                "text": "编辑",
                "bbox": {"x": 120, "y": 288, "width": 64, "height": 20},
                "locator": {"method": "role", "role": "button", "name": "编辑"},
                "element_snapshot": {"tag": "button", "text": "编辑"},
            },
        ],
        "actionable_nodes": [
            {
                "node_id": "link-1",
                "container_id": "action-section",
                "frame_path": [],
                "role": "link",
                "name": "返回列表",
                "text": "返回列表",
                "locator": {"method": "role", "role": "link", "name": "返回列表"},
                "locator_candidates": [
                    {"kind": "role", "selected": True, "locator": {"method": "role", "role": "link", "name": "返回列表"}}
                ],
                "element_snapshot": {"tag": "a", "text": "返回列表"},
            },
            {
                "node_id": "button-1",
                "container_id": "action-section",
                "frame_path": [],
                "role": "button",
                "name": "编辑",
                "text": "编辑",
                "locator": {"method": "role", "role": "button", "name": "编辑"},
                "locator_candidates": [
                    {"kind": "role", "selected": True, "locator": {"method": "role", "role": "button", "name": "编辑"}}
                ],
                "element_snapshot": {"tag": "button", "text": "编辑"},
            },
        ],
        "containers": [
            {
                "container_id": "basic-section",
                "frame_path": [],
                "container_kind": "form_section",
                "name": "单据基本信息",
                "summary": "购买人 李雨晨 使用部门 研发效能组",
                "child_actionable_ids": [],
                "child_content_ids": ["title-1", "label-1", "value-1", "label-2", "value-2"],
            },
            {
                "container_id": "table-section",
                "frame_path": [],
                "container_kind": "table",
                "name": "采购明细",
                "summary": "物品名称 数量 显示器 2",
                "child_actionable_ids": [],
                "child_content_ids": ["table-title", "th-1", "th-2", "td-1", "td-2"],
            },
            {
                "container_id": "action-section",
                "frame_path": [],
                "container_kind": "toolbar",
                "name": "操作",
                "summary": "返回列表 编辑",
                "child_actionable_ids": ["link-1", "button-1"],
                "child_content_ids": ["link-1", "button-1"],
            },
        ],
        "frames": [
            {
                "frame_path": [],
                "frame_hint": "main document",
                "elements": [
                    {"tag": "a", "role": "link", "name": "返回列表"},
                    {"tag": "button", "role": "button", "name": "编辑"},
                ],
                "collections": [],
            }
        ],
    }


def test_compact_recording_snapshot_uses_clean_mode_when_full_details_fit_budget():
    snapshot = _build_snapshot()
    regions = build_structured_regions(snapshot)

    compact = compact_recording_snapshot(snapshot, "提取单据基本信息中的信息", char_budget=100000)

    assert compact["mode"] == "clean_snapshot"
    assert compact["sampled_regions"] == []
    assert len(compact["expanded_regions"]) == len(regions)
    assert all("internal_ref" not in region for region in compact["expanded_regions"])
    assert all("region_id" not in region for region in compact["expanded_regions"])
    assert all("container_id" not in region for region in compact["expanded_regions"])

    label_value_region = next(region for region in compact["expanded_regions"] if region["kind"] == "label_value_group")
    assert label_value_region["title"] == "单据基本信息"
    pairs = label_value_region["evidence"]["pairs"]
    assert any(pair["label"] == "购买人" and pair["value"] == "李雨晨" for pair in pairs)
    assert any(pair["label"] == "使用部门" and pair["value"] == "研发效能组" for pair in pairs)
    assert all("value_locator" in pair for pair in pairs)
    assert any(region["kind"] == "action_group" for region in compact["region_catalogue"])

    table_region = next(region for region in compact["expanded_regions"] if region["kind"] == "table")
    assert table_region["evidence"]["headers"] == ["物品名称", "数量"]
    assert table_region["evidence"]["sample_rows"] == [["显示器", "2"]]
    assert table_region["locator_hints"]
    assert table_region["locator_hints"][0]["source"] == "table_header"
    assert "page.locator" in table_region["locator_hints"][0]["expression"]
    assert "region_id" not in table_region
    assert "container_id" not in table_region


def test_compact_snapshot_hides_internal_ids_from_llm_regions():
    compact = compact_recording_snapshot(_build_snapshot(), "extract details", char_budget=100000)

    for bucket in ("expanded_regions", "sampled_regions", "region_catalogue"):
        for region in compact[bucket]:
            assert "internal_ref" not in region
            assert "region_id" not in region
            assert "container_id" not in region
            assert "basic-section" not in str(region)
            assert "table-section" not in str(region)


def test_default_char_budget_keeps_medium_detail_snapshot_clean():
    snapshot = _build_snapshot()
    for index in range(50):
        y = 330 + index * 30
        snapshot["content_nodes"].extend(
            [
                {
                    "node_id": f"label-extra-{index}",
                    "container_id": "basic-section",
                    "semantic_kind": "label",
                    "role": "label",
                    "text": f"扩展字段{index}",
                    "bbox": {"x": 24, "y": y, "width": 80, "height": 20},
                    "locator": {"method": "text", "value": f"扩展字段{index}"},
                    "element_snapshot": {"tag": "label", "text": f"扩展字段{index}"},
                },
                {
                    "node_id": f"value-extra-{index}",
                    "container_id": "basic-section",
                    "semantic_kind": "field_value",
                    "role": "",
                    "text": "这是一个用于测试默认快照预算的中等长度字段值",
                    "bbox": {"x": 140, "y": y, "width": 280, "height": 20},
                    "locator": {"method": "text", "value": f"值{index}"},
                    "element_snapshot": {
                        "tag": "span",
                        "text": "这是一个用于测试默认快照预算的中等长度字段值",
                        "class": "field-value",
                    },
                    "data_field": f"extra_{index}",
                },
            ]
        )

    assert compact_recording_snapshot(snapshot, "提取单据基本信息中的信息", char_budget=10000)["mode"] == "tiered_snapshot"
    assert compact_recording_snapshot(snapshot, "提取单据基本信息中的信息")["mode"] == "clean_snapshot"


def test_sampled_region_keeps_kind_specific_details():
    label_value_region = {
        "region_id": "detail-1",
        "container_id": "detail-1",
        "kind": "label_value_group",
        "title": "鍗曟嵁鍩烘湰淇℃伅",
        "summary": "璐拱浜?鏉庨洦鏅?浣跨敤閮ㄩ棬 鐮斿彂鏁堣兘缁?",
        "pairs": [
            {
                "label": "璐拱浜?",
                "value": "鏉庨洦鏅?",
                "label_locator": {"method": "text", "value": "璐拱浜?"},
                "value_locator": {"method": "css", "value": '[data-field="buyer"]'},
            },
            {
                "label": "浣跨敤閮ㄩ棬",
                "value": "鐮斿彂鏁堣兘缁?",
                "label_locator": {"method": "text", "value": "浣跨敤閮ㄩ棬"},
                "value_locator": {"method": "css", "value": '[data-field="department"]'},
            },
            {
                "label": "楠屾敹浜?",
                "value": "寮犻洩",
                "label_locator": {"method": "text", "value": "楠屾敹浜?"},
                "value_locator": {"method": "css", "value": '[data-field="acceptor"]'},
            },
        ],
    }
    table_region = {
        "region_id": "table-1",
        "container_id": "table-1",
        "kind": "table",
        "title": "閲囪喘鏄庣粏",
        "summary": "鐗╁搧鍚嶇О 鏁伴噺 鏄剧ず鍣?2",
        "headers": ["鐗╁搧鍚嶇О", "鏁伴噺", "鍗曚綅"],
        "sample_rows": [["鏄剧ず鍣?", "2", "鍙?"]],
    }
    action_region = {
        "region_id": "action-1",
        "container_id": "action-1",
        "kind": "action_group",
        "title": "鎿嶄綔",
        "summary": "杩斿洖鍒楄〃 缂栬緫",
        "actions": [
            {"label": "杩斿洖鍒楄〃", "locator": {"method": "role", "role": "link", "name": "杩斿洖鍒楄〃"}},
            {"label": "缂栬緫", "locator": {"method": "role", "role": "button", "name": "缂栬緫"}},
        ],
    }
    text_region = {
        "region_id": "text-1",
        "container_id": "text-1",
        "kind": "text_section",
        "title": "鎻愮ず",
        "summary": "杩欐槸涓€涓櫘閫氭枃鏈钀?",
    }

    sampled_label_value = _sampled_region(label_value_region)
    sampled_table = _sampled_region(table_region)
    sampled_action = _sampled_region(action_region)
    sampled_text = _sampled_region(text_region)

    assert sampled_label_value["evidence"]["pairs"][0]["value_locator"]["method"] == "css"
    assert sampled_label_value["evidence"]["pairs"][0]["value_locator"]["value"] == '[data-field="buyer"]'
    assert sampled_table["evidence"]["headers"] == ["鐗╁搧鍚嶇О", "鏁伴噺", "鍗曚綅"]
    assert sampled_table["evidence"]["sample_rows"] == [["鏄剧ず鍣?", "2", "鍙?"]]
    assert sampled_table["locator_hints"][0]["source"] == "table_header"
    assert sampled_action["evidence"]["actions"][0]["locator"]["name"] == "杩斿洖鍒楄〃"
    assert sampled_text["evidence"]["excerpt"] == "杩欐槸涓€涓櫘閫氭枃鏈钀?"


def test_region_catalogue_keeps_all_regions_as_summaries():
    regions = build_structured_regions(_build_snapshot())
    tiers = tier_regions(regions, "提取单据基本信息中的信息")

    assert len(tiers["tier1"]) >= 1
    assert len(tiers["tier2"]) >= 1
    assert len(tiers["tier3"]) >= 1
    assert len(regions) == len(tiers["region_catalogue"])
    assert all("summary" in region for region in tiers["region_catalogue"])


def test_build_structured_regions_emits_text_section_for_plain_text_content():
    snapshot = {
        "content_nodes": [
            {
                "node_id": "text-1",
                "container_id": "notes-section",
                "semantic_kind": "text",
                "role": "",
                "text": "这是一个普通文本段落",
                "bbox": {"x": 20, "y": 20, "width": 240, "height": 20},
                "locator": {"method": "text", "value": "这是一个普通文本段落"},
                "element_snapshot": {"tag": "p", "text": "这是一个普通文本段落"},
            }
        ],
        "containers": [
            {
                "container_id": "notes-section",
                "frame_path": [],
                "container_kind": "",
                "name": "",
                "summary": "",
                "child_actionable_ids": [],
                "child_content_ids": ["text-1"],
            }
        ],
        "actionable_nodes": [],
        "frames": [],
    }

    regions = build_structured_regions(snapshot)

    assert regions[0]["kind"] == "text_section"
    assert regions[0]["summary"] == "这是一个普通文本段落"


def test_label_value_detection_requires_explicit_value_evidence():
    label_only_section = {
        "content_nodes": [
            {
                "node_id": "label-1",
                "container_id": "mixed-form",
                "semantic_kind": "label",
                "role": "label",
                "text": "采购人",
                "bbox": {"x": 20, "y": 20, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "采购人"},
                "element_snapshot": {"tag": "label", "text": "采购人"},
            },
            {
                "node_id": "paragraph-1",
                "container_id": "mixed-form",
                "semantic_kind": "text",
                "role": "",
                "text": "这里是说明文字，不是字段值",
                "bbox": {"x": 120, "y": 20, "width": 220, "height": 20},
                "locator": {"method": "text", "value": "这里是说明文字，不是字段值"},
                "element_snapshot": {"tag": "p", "text": "这里是说明文字，不是字段值"},
            },
            {
                "node_id": "table-head-1",
                "container_id": "mixed-form",
                "semantic_kind": "header_cell",
                "role": "columnheader",
                "text": "名称",
                "bbox": {"x": 20, "y": 56, "width": 100, "height": 20},
                "locator": {"method": "text", "value": "名称"},
                "element_snapshot": {"tag": "th", "text": "名称"},
            },
            {
                "node_id": "table-cell-1",
                "container_id": "mixed-form",
                "semantic_kind": "cell",
                "role": "cell",
                "text": "显示器",
                "bbox": {"x": 20, "y": 84, "width": 100, "height": 20},
                "locator": {"method": "text", "value": "显示器"},
                "element_snapshot": {"tag": "td", "text": "显示器"},
            },
        ],
        "containers": [
            {
                "container_id": "mixed-form",
                "frame_path": [],
                "container_kind": "form_section",
                "name": "混合内容",
                "summary": "",
                "child_actionable_ids": [],
                "child_content_ids": ["label-1", "paragraph-1", "table-head-1", "table-cell-1"],
            }
        ],
        "actionable_nodes": [],
        "frames": [],
    }

    regions = build_structured_regions(label_only_section)

    assert regions[0]["kind"] == "text_section"
    assert "pairs" not in regions[0]


def test_label_value_detection_accepts_explicit_field_value_evidence():
    snapshot = _build_snapshot()
    regions = build_structured_regions(snapshot)

    basic = next(region for region in regions if region["container_id"] == "basic-section")
    assert basic["kind"] == "label_value_group"
    assert any(pair["value_locator"]["method"] == "text" for pair in basic["pairs"])


def test_label_value_pairs_follow_visual_columns_in_two_column_layout():
    snapshot = {
        "content_nodes": [
            {
                "node_id": "intro-1",
                "container_id": "two-column-form",
                "semantic_kind": "text",
                "role": "",
                "text": "单据基本信息",
                "bbox": {"x": 24, "y": 24, "width": 160, "height": 24},
                "locator": {"method": "text", "value": "单据基本信息"},
                "element_snapshot": {"tag": "p", "text": "单据基本信息"},
            },
            {
                "node_id": "label-1",
                "container_id": "two-column-form",
                "semantic_kind": "label",
                "role": "label",
                "text": "购买人",
                "bbox": {"x": 90, "y": 528, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "购买人"},
                "element_snapshot": {"tag": "label", "text": "购买人"},
            },
            {
                "node_id": "label-2",
                "container_id": "two-column-form",
                "semantic_kind": "label",
                "role": "label",
                "text": "使用部门",
                "bbox": {"x": 824, "y": 528, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "使用部门"},
                "element_snapshot": {"tag": "label", "text": "使用部门"},
            },
            {
                "node_id": "value-1",
                "container_id": "two-column-form",
                "semantic_kind": "field_value",
                "role": "",
                "text": "李雨晨",
                "bbox": {"x": 90, "y": 564, "width": 160, "height": 20},
                "locator": {"method": "text", "value": "李雨晨"},
                "element_snapshot": {"tag": "span", "text": "李雨晨", "class": "field-value"},
                "data_field": "buyer",
            },
            {
                "node_id": "value-2",
                "container_id": "two-column-form",
                "semantic_kind": "field_value",
                "role": "",
                "text": "研发效能组",
                "bbox": {"x": 824, "y": 564, "width": 160, "height": 20},
                "locator": {"method": "text", "value": "研发效能组"},
                "element_snapshot": {"tag": "span", "text": "研发效能组", "class": "field-value"},
                "data_field": "department",
            },
            {
                "node_id": "label-3",
                "container_id": "two-column-form",
                "semantic_kind": "label",
                "role": "label",
                "text": "验收人",
                "bbox": {"x": 90, "y": 600, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "验收人"},
                "element_snapshot": {"tag": "label", "text": "验收人"},
            },
            {
                "node_id": "label-4",
                "container_id": "two-column-form",
                "semantic_kind": "label",
                "role": "label",
                "text": "供应商",
                "bbox": {"x": 824, "y": 600, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "供应商"},
                "element_snapshot": {"tag": "label", "text": "供应商"},
            },
            {
                "node_id": "value-3",
                "container_id": "two-column-form",
                "semantic_kind": "field_value",
                "role": "",
                "text": "张雪",
                "bbox": {"x": 90, "y": 636, "width": 160, "height": 20},
                "locator": {"method": "text", "value": "张雪"},
                "element_snapshot": {"tag": "span", "text": "张雪", "class": "field-value"},
                "data_field": "acceptor",
            },
            {
                "node_id": "value-4",
                "container_id": "two-column-form",
                "semantic_kind": "field_value",
                "role": "",
                "text": "联想华南直营服务中心",
                "bbox": {"x": 824, "y": 636, "width": 220, "height": 20},
                "locator": {"method": "text", "value": "联想华南直营服务中心"},
                "element_snapshot": {"tag": "span", "text": "联想华南直营服务中心", "class": "field-value"},
                "data_field": "supplier",
            },
        ],
        "containers": [
            {
                "container_id": "two-column-form",
                "frame_path": [],
                "container_kind": "form_section",
                "name": "单据核心字段",
                "summary": "",
                "child_actionable_ids": [],
                "child_content_ids": ["intro-1", "label-1", "label-2", "value-1", "value-2", "label-3", "label-4", "value-3", "value-4"],
            }
        ],
        "actionable_nodes": [],
        "frames": [],
    }

    regions = build_structured_regions(snapshot)
    basic = next(region for region in regions if region["container_id"] == "two-column-form")

    assert basic["kind"] == "label_value_group"
    assert basic["title"] == "单据基本信息"
    assert [pair["label"] for pair in basic["pairs"]] == ["购买人", "使用部门", "验收人", "供应商"]
    assert [pair["value"] for pair in basic["pairs"]] == ["李雨晨", "研发效能组", "张雪", "联想华南直营服务中心"]


def test_tiered_compaction_keeps_full_tier1_details():
    snapshot = _build_snapshot()
    regions = build_structured_regions(snapshot)
    tiers = tier_regions(regions, "提取单据基本信息中的信息")

    compact = compact_recording_snapshot(snapshot, "提取单据基本信息中的信息", char_budget=1)

    assert compact["mode"] == "tiered_snapshot"
    expanded = next(region for region in compact["expanded_regions"] if region["kind"] == "label_value_group")
    assert expanded["evidence"]["pairs"]
    assert any(pair["label"] == "购买人" and pair["value"] == "李雨晨" for pair in expanded["evidence"]["pairs"])
    assert any(pair["label"] == "使用部门" and pair["value"] == "研发效能组" for pair in expanded["evidence"]["pairs"])
    assert compact["sampled_regions"]
    assert any(
        {"headers", "pairs", "actions", "excerpt"} & set((region.get("evidence") or {}).keys())
        for region in compact["sampled_regions"]
    )
    assert all("internal_ref" not in region for region in compact["sampled_regions"])
    assert all("summary" in region for region in compact["region_catalogue"])
    assert "tiers" not in compact


def test_tiering_expands_multiple_near_tied_relevant_regions():
    regions = [
        {
            "region_id": "r1",
            "kind": "label_value_group",
            "title": "单据基本信息",
            "summary": "购买人 使用部门",
            "pairs": [{"label": "购买人", "value": "李雨晨", "value_locator": {"method": "text", "value": "李雨晨"}}],
        },
        {
            "region_id": "r2",
            "kind": "table",
            "title": "单据基本信息",
            "summary": "购买人 使用部门 显示器",
            "headers": ["物品名称", "数量"],
            "sample_rows": [["显示器", "2"]],
        },
        {
            "region_id": "r3",
            "kind": "action_group",
            "title": "操作",
            "summary": "返回列表 编辑",
            "actions": [{"label": "返回列表", "locator": {"method": "role", "role": "link", "name": "返回列表"}}],
        },
    ]

    tiers = tier_regions(regions, "提取单据基本信息")

    assert [region["region_id"] for region in tiers["tier1"]] == ["r1", "r2"]
    assert all("pairs" in region or "sample_rows" in region or "actions" in region for region in tiers["tier1"])
    assert tiers["tier2"] or tiers["tier3"]


def test_table_detection_tolerates_slight_row_misalignment_and_requires_shape():
    snapshot = {
        "content_nodes": [
            {
                "node_id": "table-title",
                "container_id": "table-section",
                "semantic_kind": "heading",
                "role": "heading",
                "text": "采购明细",
                "bbox": {"x": 24, "y": 20, "width": 120, "height": 24},
                "locator": {"method": "text", "value": "采购明细"},
                "element_snapshot": {"tag": "h2", "text": "采购明细"},
            },
            {
                "node_id": "th-1",
                "container_id": "table-section",
                "semantic_kind": "header_cell",
                "role": "columnheader",
                "text": "物品名称",
                "bbox": {"x": 24, "y": 60, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "物品名称"},
                "element_snapshot": {"tag": "th", "text": "物品名称"},
            },
            {
                "node_id": "th-2",
                "container_id": "table-section",
                "semantic_kind": "header_cell",
                "role": "columnheader",
                "text": "数量",
                "bbox": {"x": 170, "y": 61, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "数量"},
                "element_snapshot": {"tag": "th", "text": "数量"},
            },
            {
                "node_id": "td-1",
                "container_id": "table-section",
                "semantic_kind": "cell",
                "role": "cell",
                "text": "显示器",
                "bbox": {"x": 24, "y": 90, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "显示器"},
                "element_snapshot": {"tag": "td", "text": "显示器"},
            },
            {
                "node_id": "td-2",
                "container_id": "table-section",
                "semantic_kind": "cell",
                "role": "cell",
                "text": "2",
                "bbox": {"x": 170, "y": 97, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "2"},
                "element_snapshot": {"tag": "td", "text": "2"},
            },
        ],
        "containers": [
            {
                "container_id": "table-section",
                "frame_path": [],
                "container_kind": "",
                "name": "采购明细",
                "summary": "",
                "child_actionable_ids": [],
                "child_content_ids": ["table-title", "th-1", "th-2", "td-1", "td-2"],
            }
        ],
        "actionable_nodes": [],
        "frames": [],
    }

    regions = build_structured_regions(snapshot)

    assert regions[0]["kind"] == "table"
    assert regions[0]["sample_rows"] == [["显示器", "2"]]


def test_table_row_grouping_handles_cumulative_y_drift():
    snapshot = {
        "content_nodes": [
            {
                "node_id": "th-1",
                "container_id": "drift-table",
                "semantic_kind": "header_cell",
                "role": "columnheader",
                "text": "名称",
                "bbox": {"x": 24, "y": 20, "width": 100, "height": 20},
                "locator": {"method": "text", "value": "名称"},
                "element_snapshot": {"tag": "th", "text": "名称"},
            },
            {
                "node_id": "th-2",
                "container_id": "drift-table",
                "semantic_kind": "header_cell",
                "role": "columnheader",
                "text": "数量",
                "bbox": {"x": 170, "y": 22, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "数量"},
                "element_snapshot": {"tag": "th", "text": "数量"},
            },
            {
                "node_id": "td-1",
                "container_id": "drift-table",
                "semantic_kind": "cell",
                "role": "cell",
                "text": "显示器",
                "bbox": {"x": 24, "y": 100, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "显示器"},
                "element_snapshot": {"tag": "td", "text": "显示器"},
            },
            {
                "node_id": "td-2",
                "container_id": "drift-table",
                "semantic_kind": "cell",
                "role": "cell",
                "text": "2",
                "bbox": {"x": 170, "y": 107, "width": 80, "height": 20},
                "locator": {"method": "text", "value": "2"},
                "element_snapshot": {"tag": "td", "text": "2"},
            },
            {
                "node_id": "td-3",
                "container_id": "drift-table",
                "semantic_kind": "cell",
                "role": "cell",
                "text": "台",
                "bbox": {"x": 250, "y": 114, "width": 40, "height": 20},
                "locator": {"method": "text", "value": "台"},
                "element_snapshot": {"tag": "td", "text": "台"},
            },
        ],
        "containers": [
            {
                "container_id": "drift-table",
                "frame_path": [],
                "container_kind": "table",
                "name": "明细",
                "summary": "",
                "child_actionable_ids": [],
                "child_content_ids": ["th-1", "th-2", "td-1", "td-2", "td-3"],
            }
        ],
        "actionable_nodes": [],
        "frames": [],
    }

    regions = build_structured_regions(snapshot)

    assert regions[0]["kind"] == "table"
    assert regions[0]["sample_rows"] == [["显示器", "2", "台"]]


def test_fallback_region_ids_are_deterministic_for_same_input():
    snapshot = {
        "content_nodes": [
            {
                "node_id": "text-1",
                "container_id": "",
                "semantic_kind": "text",
                "role": "",
                "text": "纯文本",
                "bbox": {"x": 20, "y": 20, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "纯文本"},
                "element_snapshot": {"tag": "p", "text": "纯文本"},
            }
        ],
        "containers": [
            {
                "container_id": "",
                "frame_path": [],
                "container_kind": "",
                "name": "",
                "summary": "",
                "child_actionable_ids": [],
                "child_content_ids": ["text-1"],
            }
        ],
        "actionable_nodes": [],
        "frames": [],
    }

    first = build_structured_regions(snapshot)
    second = build_structured_regions(snapshot)

    assert first[0]["region_id"] == second[0]["region_id"]
    assert first[0]["region_id"].startswith("text_section-")


def test_tiering_prefers_detail_section_by_title_and_summary_overlap():
    regions = build_structured_regions(_build_snapshot())
    tiers = tier_regions(regions, "提取单据信心")

    assert tiers["tier1"][0]["kind"] == "label_value_group"
    assert tiers["tier1"][0]["title"] == "单据基本信息"


def test_tiering_prefers_record_list_for_list_collection_instructions():
    snapshot = {
        "url": "https://example.com/repo/pulls",
        "title": "Pull requests - example/repo",
        "content_nodes": [
            {
                "node_id": "item-1-title",
                "container_id": "pr-list",
                "semantic_kind": "item",
                "role": "",
                "text": "Add auth badge",
                "bbox": {"x": 24, "y": 120, "width": 240, "height": 20},
                "locator": {"method": "text", "value": "Add auth badge"},
                "element_snapshot": {"tag": "span", "text": "Add auth badge"},
            },
            {
                "node_id": "item-1-meta",
                "container_id": "pr-list",
                "semantic_kind": "text",
                "role": "",
                "text": "#101 opened by alice",
                "bbox": {"x": 24, "y": 144, "width": 220, "height": 18},
                "locator": {"method": "text", "value": "#101 opened by alice"},
                "element_snapshot": {"tag": "span", "text": "#101 opened by alice"},
            },
            {
                "node_id": "item-2-title",
                "container_id": "pr-list",
                "semantic_kind": "item",
                "role": "",
                "text": "Fix flaky pull request selector",
                "bbox": {"x": 24, "y": 192, "width": 280, "height": 20},
                "locator": {"method": "text", "value": "Fix flaky pull request selector"},
                "element_snapshot": {"tag": "span", "text": "Fix flaky pull request selector"},
            },
            {
                "node_id": "item-2-meta",
                "container_id": "pr-list",
                "semantic_kind": "text",
                "role": "",
                "text": "#102 opened by bob",
                "bbox": {"x": 24, "y": 216, "width": 220, "height": 18},
                "locator": {"method": "text", "value": "#102 opened by bob"},
                "element_snapshot": {"tag": "span", "text": "#102 opened by bob"},
            },
            {
                "node_id": "item-3-title",
                "container_id": "pr-list",
                "semantic_kind": "item",
                "role": "",
                "text": "Refactor PR list pagination",
                "bbox": {"x": 24, "y": 264, "width": 260, "height": 20},
                "locator": {"method": "text", "value": "Refactor PR list pagination"},
                "element_snapshot": {"tag": "span", "text": "Refactor PR list pagination"},
            },
            {
                "node_id": "item-3-meta",
                "container_id": "pr-list",
                "semantic_kind": "text",
                "role": "",
                "text": "#103 opened by carol",
                "bbox": {"x": 24, "y": 288, "width": 220, "height": 18},
                "locator": {"method": "text", "value": "#103 opened by carol"},
                "element_snapshot": {"tag": "span", "text": "#103 opened by carol"},
            },
        ],
        "actionable_nodes": [
            {
                "node_id": "link-1",
                "container_id": "pr-list",
                "frame_path": [],
                "role": "link",
                "name": "Add auth badge",
                "text": "Add auth badge",
                "bbox": {"x": 24, "y": 120, "width": 240, "height": 20},
                "locator": {"method": "role", "role": "link", "name": "Add auth badge"},
                "element_snapshot": {"tag": "a", "text": "Add auth badge", "href": "/repo/pull/101"},
            },
            {
                "node_id": "author-1",
                "container_id": "pr-list",
                "frame_path": [],
                "role": "link",
                "name": "alice",
                "text": "alice",
                "bbox": {"x": 170, "y": 144, "width": 50, "height": 18},
                "locator": {"method": "role", "role": "link", "name": "alice"},
                "element_snapshot": {"tag": "a", "text": "alice", "href": "/repo/pulls?q=author%3Aalice"},
            },
            {
                "node_id": "link-2",
                "container_id": "pr-list",
                "frame_path": [],
                "role": "link",
                "name": "Fix flaky pull request selector",
                "text": "Fix flaky pull request selector",
                "bbox": {"x": 24, "y": 192, "width": 280, "height": 20},
                "locator": {"method": "role", "role": "link", "name": "Fix flaky pull request selector"},
                "element_snapshot": {"tag": "a", "text": "Fix flaky pull request selector", "href": "/repo/pull/102"},
            },
            {
                "node_id": "author-2",
                "container_id": "pr-list",
                "frame_path": [],
                "role": "link",
                "name": "bob",
                "text": "bob",
                "bbox": {"x": 170, "y": 216, "width": 40, "height": 18},
                "locator": {"method": "role", "role": "link", "name": "bob"},
                "element_snapshot": {"tag": "a", "text": "bob", "href": "/repo/pulls?q=author%3Abob"},
            },
            {
                "node_id": "link-3",
                "container_id": "pr-list",
                "frame_path": [],
                "role": "link",
                "name": "Refactor PR list pagination",
                "text": "Refactor PR list pagination",
                "bbox": {"x": 24, "y": 264, "width": 260, "height": 20},
                "locator": {"method": "role", "role": "link", "name": "Refactor PR list pagination"},
                "element_snapshot": {"tag": "a", "text": "Refactor PR list pagination", "href": "/repo/pull/103"},
            },
            {
                "node_id": "author-3",
                "container_id": "pr-list",
                "frame_path": [],
                "role": "link",
                "name": "carol",
                "text": "carol",
                "bbox": {"x": 170, "y": 288, "width": 45, "height": 18},
                "locator": {"method": "role", "role": "link", "name": "carol"},
                "element_snapshot": {"tag": "a", "text": "carol", "href": "/repo/pulls?q=author%3Acarol"},
            },
            {
                "node_id": "repo-tab",
                "container_id": "repo-nav",
                "frame_path": [],
                "role": "link",
                "name": "Repository",
                "text": "Repository",
                "bbox": {"x": 24, "y": 24, "width": 90, "height": 20},
                "locator": {"method": "role", "role": "link", "name": "Repository"},
                "element_snapshot": {"tag": "a", "text": "Repository", "href": "/repo"},
            },
        ],
        "containers": [
            {
                "container_id": "pr-list",
                "frame_path": [],
                "container_kind": "list",
                "name": "Pull requests list",
                "summary": "Add auth badge alice Fix flaky pull request selector bob Refactor PR list pagination carol",
                "child_actionable_ids": ["link-1", "author-1", "link-2", "author-2", "link-3", "author-3"],
                "child_content_ids": [
                    "item-1-title",
                    "item-1-meta",
                    "item-2-title",
                    "item-2-meta",
                    "item-3-title",
                    "item-3-meta",
                ],
            },
            {
                "container_id": "repo-nav",
                "frame_path": [],
                "container_kind": "toolbar",
                "name": "Repository",
                "summary": "Repository",
                "child_actionable_ids": ["repo-tab"],
                "child_content_ids": [],
            },
        ],
        "frames": [],
    }

    regions = build_structured_regions(snapshot)
    tiers = tier_regions(regions, "Collect pull request titles and authors from the first page as an array")

    assert tiers["tier1"][0]["kind"] == "record_list"
    assert tiers["tier1"][0]["title"] == "Pull requests list"

    compact = compact_recording_snapshot(snapshot, "Collect pull request titles and authors from the first page as an array", char_budget=600)
    record_list = next(region for region in compact["expanded_regions"] if region["kind"] == "record_list")
    assert [item["primary_text"] for item in record_list["evidence"]["items"]] == [
        "Add auth badge",
        "Fix flaky pull request selector",
        "Refactor PR list pagination",
    ]
    assert record_list["evidence"]["items"][0]["secondary_text"] == "#101 opened by alice"
    assert record_list["locator_hints"][0]["source"] == "list_item"


def test_actionable_tab_creates_sectioned_label_value_region_from_nearby_fields():
    snapshot = {
        "content_nodes": [
            {
                "node_id": "label-amount",
                "container_id": "detail-container",
                "semantic_kind": "label",
                "role": "",
                "text": "预计总金额 (含税）",
                "bbox": {"x": 89, "y": 574, "width": 120, "height": 20},
                "locator": {"method": "text", "value": "预计总金额 (含税）"},
                "element_snapshot": {"tag": "span", "text": "预计总金额 (含税）", "class": "label"},
            },
            {
                "node_id": "value-amount",
                "container_id": "detail-container",
                "semantic_kind": "text",
                "role": "",
                "text": "100.00",
                "bbox": {"x": 80, "y": 596, "width": 41, "height": 19},
                "locator": {"method": "text", "value": "100.00"},
                "element_snapshot": {"tag": "span", "text": "100.00", "class": "aui-numeric-display-only"},
            },
            {
                "node_id": "label-currency-full",
                "container_id": "detail-container",
                "semantic_kind": "label",
                "role": "",
                "text": "* 币种",
                "bbox": {"x": 455, "y": 574, "width": 37, "height": 24},
                "locator": {"method": "text", "value": "* 币种"},
                "element_snapshot": {"tag": "label", "text": "* 币种", "class": "aui-form-item__label"},
            },
            {
                "node_id": "required-currency",
                "container_id": "detail-container",
                "semantic_kind": "text",
                "role": "",
                "text": "*",
                "bbox": {"x": 455, "y": 574, "width": 5, "height": 20},
                "locator": {"method": "text", "value": "*"},
                "element_snapshot": {"tag": "span", "text": "*", "class": "required"},
            },
            {
                "node_id": "label-currency",
                "container_id": "detail-container",
                "semantic_kind": "label",
                "role": "",
                "text": "币种",
                "bbox": {"x": 464, "y": 574, "width": 28, "height": 20},
                "locator": {"method": "text", "value": "币种"},
                "element_snapshot": {"tag": "span", "text": "币种", "class": "label"},
            },
            {
                "node_id": "value-currency",
                "container_id": "detail-container",
                "semantic_kind": "text",
                "role": "",
                "text": "USD",
                "bbox": {"x": 455, "y": 598, "width": 27, "height": 19},
                "locator": {"method": "text", "value": "USD"},
                "element_snapshot": {"tag": "span", "text": "USD", "class": "aui-input-display-only"},
            },
            {
                "node_id": "label-type",
                "container_id": "detail-container",
                "semantic_kind": "label",
                "role": "",
                "text": "物品/服务",
                "bbox": {"x": 840, "y": 574, "width": 61, "height": 20},
                "locator": {"method": "text", "value": "物品/服务"},
                "element_snapshot": {"tag": "span", "text": "物品/服务", "class": "label"},
            },
            {
                "node_id": "value-type",
                "container_id": "detail-container",
                "semantic_kind": "text",
                "role": "",
                "text": "服务",
                "bbox": {"x": 831, "y": 598, "width": 28, "height": 19},
                "locator": {"method": "text", "value": "服务"},
                "element_snapshot": {"tag": "span", "text": "服务", "class": "aui-input-display-only"},
            },
        ],
        "actionable_nodes": [
            {
                "node_id": "tab-purchase",
                "container_id": "detail-container",
                "frame_path": [],
                "role": "tab",
                "name": "采购信息",
                "text": "采购信息",
                "bbox": {"x": 64, "y": 534, "width": 1156, "height": 40},
                "locator": {"method": "role", "role": "tab", "name": "采购信息"},
                "element_snapshot": {"tag": "div", "text": "采购信息"},
            }
        ],
        "containers": [
            {
                "container_id": "detail-container",
                "frame_path": [],
                "container_kind": "form_section",
                "name": "基础信息",
                "summary": "基础信息 采购信息 预计总金额 100.00 币种 USD 物品/服务 服务",
            }
        ],
        "frames": [],
    }

    compact = compact_recording_snapshot(snapshot, "提取采购信息中的内容", char_budget=100000)

    purchase = next(region for region in compact["expanded_regions"] if region["title"] == "采购信息")
    assert purchase["kind"] == "label_value_group"
    assert {pair["label"]: pair["value"] for pair in purchase["evidence"]["pairs"]} == {
        "预计总金额 (含税）": "100.00",
        "币种": "USD",
        "物品/服务": "服务",
    }
    assert purchase["locator_hints"][0]["source"] == "section_header"
    assert "get_by_role" in purchase["locator_hints"][0]["expression"]


def test_ngram_overlap_handles_partial_typo_and_unrelated_text():
    assert ngram_overlap("提取单据基本信心", "单据基本信息") > 0.4
    assert ngram_overlap("提取单据基本信息", "采购明细") < 0.2
