from backend.rpa.recording_runtime_agent import _build_preplanned_plan
from backend.rpa.recording_search_preplans import build_search_preplanned_plan
from backend.rpa.recording_download_preplans import build_download_preplanned_plan
from backend.rpa.recording_modal_preplans import build_modal_form_preplanned_plan


def _ordinal_snapshot():
    repos = ["alpha / one", "beta / two", "gamma / three"]
    actionable_nodes = []
    for index, repo in enumerate(repos):
        actionable_nodes.append(
            {
                "node_id": f"title-{index}",
                "container_id": f"repo-{index}",
                "role": "link",
                "name": repo,
                "text": repo,
                "collection_item_selector": "h2.lh-condensed a",
                "collection_item_count": len(repos),
                "bbox": {"x": 10, "y": 100 + index * 90},
            }
        )
    return {
        "url": "https://github.com/trending",
        "title": "Trending repositories",
        "frames": [],
        "content_nodes": [],
        "containers": [],
        "actionable_nodes": actionable_nodes,
    }


def test_ordinal_click_preplan_uses_current_index_not_recorded_label():
    plan = _build_preplanned_plan("点击第一个项目", _ordinal_snapshot())

    assert plan is not None
    assert plan["expected_effect"] == "click"
    assert "page.locator('h2.lh-condensed a').nth(0)" in plan["code"]
    assert ".click()" in plan["code"]
    assert "alpha / one" not in plan["code"]


def test_semantic_selection_does_not_use_ordinal_preplan():
    plan = _build_preplanned_plan("open the project most related to python", _ordinal_snapshot())

    assert plan is None


def test_table_entity_action_preplan_clicks_row_action_by_identifier():
    snapshot = {
        "table_views": [
            {
                "columns": [
                    {"header": "Contract No", "index": 0},
                    {"header": "Status", "index": 1},
                    {"header": "Action", "index": 2, "role": "action"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Contract No", "text": "CT-001"},
                            {"column_header": "Status", "text": "effective"},
                            {
                                "column_header": "Action",
                                "text": "View details",
                                "actions": [{"locator": {"scope": "row", "value": "td:nth-child(3) button"}}],
                            },
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(0)"}],
                    }
                ],
            }
        ]
    }

    plan = _build_preplanned_plan("open contract CT-001 details", snapshot)

    assert plan is not None
    assert plan["expected_effect"] == "click"
    assert "_rows.filter(has_text=_target_text).first" in plan["code"]
    assert "td:nth-child(3) a" in plan["code"]
    assert plan["input_bindings"]["target_text"]["default"] == "CT-001"


def test_visible_entity_action_preplan_clicks_visible_row_identifier_without_table_schema():
    snapshot = {
        "content_nodes": [
            {"text": "Contract CT-001 active"},
            {"text": "Open details"},
        ],
        "actionable_nodes": [
            {"role": "button", "name": "Open details", "text": "Open details"},
        ],
    }

    plan = _build_preplanned_plan("open contract CT-001 details", snapshot)

    assert plan is not None
    assert plan["visible_entity_action"] is True
    assert "_row_scopes" in plan["code"]
    assert "_target_text = 'CT-001'" in plan["code"]
    assert "Contract CT-001 active" not in plan["code"]
    assert plan["input_bindings"]["target_text"]["default"] == "CT-001"


def test_visible_entity_action_preplan_requires_snapshot_evidence_for_identifier():
    snapshot = {
        "content_nodes": [{"text": "Contract CT-001 active"}],
        "actionable_nodes": [{"role": "button", "name": "Open details"}],
    }

    plan = _build_preplanned_plan("open contract CT-999 details", snapshot)

    assert plan is None


def test_visible_entity_action_preplan_does_not_handle_search_empty_result_tasks():
    snapshot = {
        "content_nodes": [{"text": "Search contracts"}, {"text": "CT-NOT-FOUND"}],
        "actionable_nodes": [{"role": "button", "name": "Search"}],
    }

    plan = _build_preplanned_plan("search contract CT-NOT-FOUND and confirm no matching result", snapshot)

    assert plan is None


def test_visible_entity_action_preplan_does_not_claim_multi_entity_workflow():
    snapshot = {
        "content_nodes": [
            {"text": "Contract CT-2026-RPA-001"},
            {"text": "Purchase request PR-2026-RPA-NEW-001"},
        ],
        "actionable_nodes": [{"role": "button", "name": "Open details"}],
    }

    plan = _build_preplanned_plan(
        "Open CT-2026-RPA-001, create PR-2026-RPA-NEW-001, and submit it.",
        snapshot,
    )

    assert plan is None


def test_search_empty_result_preplan_handles_negated_open_instruction():
    plan = build_search_preplanned_plan(
        "当前在合同管理页面。请搜索不存在的合同编号 CT-2026-RPA-NOT-FOUND，确认列表为空，不要误打开其他合同。",
        {},
    )

    assert plan is not None
    assert plan["search_empty_result"] is True
    assert plan["terminal_contract"]["kind"] == "empty_result"
    assert "CT-2026-RPA-NOT-FOUND" in plan["code"]


def test_search_empty_result_preplan_supports_status_filter_action_without_entity_id():
    plan = build_search_preplanned_plan(
        "在 Legitimate empty extraction 区域，按 failed 状态筛选审计记录。请确认筛选后的 failed 审计记录列表为空。",
        {},
    )

    assert plan is not None
    assert plan["search_empty_result"] is True
    assert plan["input_bindings"]["query"]["default"] == "failed"
    assert "_find_filter_action" in plan["code"]
    assert "No visible search input or query-specific filter action found" in plan["code"]
    assert "|empty|" not in plan["code"]
    assert "no\\s+[^\\n]{0,60}\\s+found" in plan["code"]


def test_search_then_open_preplan_uses_query_binding_and_region_scope():
    plan = build_search_preplanned_plan(
        "在 Parameterized contract target 区域，搜索合同 CT-2026-RPA-ALT-001，并打开这个 alternate contract。不要打开 CT-2026-RPA-001。",
        {},
    )

    assert plan is not None
    assert plan["search_then_open"] is True
    assert plan["input_bindings"]["query"]["default"] == "CT-2026-RPA-ALT-001"
    assert "parameterized contract target" in plan["code"]
    assert "CT-2026-RPA-001" not in plan["code"]


def test_popup_download_preplan_uses_filename_and_popup_flow():
    plan = build_download_preplanned_plan(
        "在 Popup tab download 区域，打开 popup report 新标签页，并下载 popup_report_2026.csv。",
        {},
    )

    assert plan is not None
    assert plan["popup_download"] is True
    assert "expect_popup" in plan["code"]
    assert "expect_download" in plan["code"]
    assert "popup_report_2026.csv" in plan["code"]
    assert plan["terminal_contract"]["kind"] == "download_created"


def test_same_page_download_preplan_uses_generate_refresh_download_flow():
    plan = build_download_preplanned_plan(
        "Generate the async report, poll until ready, and download supplier_purchase_summary_2026.xlsx.",
        {},
    )

    assert plan is not None
    assert plan["same_page_async_download"] is True
    assert "expect_download" in plan["code"]
    assert "generate|create|start|request|build|prepare|run" in plan["code"]
    assert "refresh|reload|poll|check" in plan["code"]
    assert "supplier_purchase_summary_2026.xlsx" in plan["code"]


def test_same_page_download_preplan_does_not_claim_plain_export_without_async_evidence():
    plan = build_download_preplanned_plan(
        "Export the contracts ledger Excel and confirm contracts_2026.xlsx was downloaded.",
        {},
    )

    assert plan is None


def test_modal_form_preplan_uses_entity_and_quoted_text_without_business_terms():
    plan = build_modal_form_preplanned_plan(
        "Find row TASK-12345, add comment “Looks good”, and submit.",
        {
            "content_nodes": [{"text": "TASK-12345 Pending"}],
            "table_views": [
                {
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Task", "text": "TASK-12345"},
                                {
                                    "column_header": "Action",
                                    "text": "Review",
                                    "actions": [{"locator": {"scope": "row", "value": "button"}}],
                                },
                            ]
                        }
                    ]
                }
            ],
        },
    )

    assert plan is not None
    assert plan["modal_form_submit"] is True
    assert "TASK-12345" in plan["code"]
    assert "Looks good" in plan["code"]
    assert "[role='dialog']" in plan["code"]


def test_modal_form_preplan_requires_current_page_entity_evidence():
    plan = build_modal_form_preplanned_plan(
        "Fill request PR-NEW-12345 using “source-RPA-2026” and submit.",
        {"content_nodes": [{"text": "Source details"}]},
    )

    assert plan is None


def test_modal_form_preplan_requires_row_action_evidence():
    plan = build_modal_form_preplanned_plan(
        "Fill request PR-NEW-12345 using “source-RPA-2026” and submit.",
        {
            "content_nodes": [{"text": "PR-NEW-12345 Source details"}],
            "table_views": [
                {
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Request", "text": "PR-NEW-12345"},
                                {"column_header": "Status", "text": "draft"},
                            ]
                        }
                    ]
                }
            ],
        },
    )

    assert plan is None


def test_modal_form_preplan_accepts_same_row_action_evidence():
    plan = build_modal_form_preplanned_plan(
        "Find row TASK-12345, add comment “Looks good” and submit.",
        {
            "content_nodes": [{"text": "TASK-12345 Pending"}],
            "table_views": [
                {
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Task", "text": "TASK-12345"},
                                {
                                    "column_header": "Action",
                                    "text": "Review",
                                    "actions": [{"locator": {"scope": "row", "value": "button"}}],
                                },
                            ]
                        }
                    ]
                }
            ],
        },
    )

    assert plan is not None
    assert plan["modal_form_submit"] is True

 
def test_modal_form_preplan_does_not_claim_create_form_with_quoted_formula():
    plan = build_modal_form_preplanned_plan(
        "Open contract CT-001, create new purchase request PR-001, use cost center “supplier-RPA-2026” and submit.",
        {
            "content_nodes": [{"text": "CT-001 Active"}],
            "table_views": [
                {
                    "rows": [
                        {
                            "cells": [
                                {"column_header": "Contract", "text": "CT-001"},
                                {
                                    "column_header": "Action",
                                    "text": "Open",
                                    "actions": [{"locator": {"scope": "row", "value": "button"}}],
                                },
                            ]
                        }
                    ]
                }
            ],
        },
    )

    assert plan is None


def test_ordinal_click_preplan_supports_second_row_action_in_chinese():
    snapshot = {
        "table_views": [
            {
                "title": "Scoped collection",
                "columns": [
                    {"header": "Row key", "index": 0},
                    {"header": "Name", "index": 1},
                    {"header": "Action", "index": 2, "role": "action"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Row key", "text": "ROW-001"},
                            {"column_header": "Name", "text": "First"},
                            {
                                "column_header": "Action",
                                "text": "Open",
                                "actions": [
                                    {
                                        "locator": {
                                            "scope": "row",
                                            "value": "td:nth-child(3) button",
                                        }
                                    }
                                ],
                            },
                        ],
                        "locator_hints": [
                            {"expression": "page.locator('tbody tr').nth(0)"}
                        ],
                    },
                    {
                        "cells": [
                            {"column_header": "Row key", "text": "ROW-002"},
                            {"column_header": "Name", "text": "Second"},
                            {
                                "column_header": "Action",
                                "text": "Open",
                                "actions": [
                                    {
                                        "locator": {
                                            "scope": "row",
                                            "value": "td:nth-child(3) button",
                                        }
                                    }
                                ],
                            },
                        ],
                        "locator_hints": [
                            {"expression": "page.locator('tbody tr').nth(1)"}
                        ],
                    },
                ],
            }
        ]
    }

    plan = _build_preplanned_plan("在 Scoped collection 区域，打开第二行里的操作按钮", snapshot)

    assert plan is not None
    assert "_rows.nth(1)" in plan["code"]
    assert "td:nth-child(3) a" in plan["code"]
    assert "td:nth-child(3) button" in plan["code"]


def test_ordinal_click_preplan_uses_multilingual_table_headers_for_project_item():
    snapshot = {
        "table_views": [
            {
                "columns": [
                    {"header": "Task ID", "index": 0},
                    {"header": "File", "index": 1},
                    {"header": "Status", "index": 2},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Task ID", "text": "EXPORT-001"},
                            {"column_header": "File", "text": "a.csv"},
                            {"column_header": "Status", "text": "ready"},
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(0)"}],
                    }
                ],
            },
            {
                "columns": [
                    {"header": "Project ID", "index": 0},
                    {"header": "Project name", "index": 1},
                    {"header": "Action", "index": 2, "role": "action"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Project ID", "text": "DYN-001"},
                            {
                                "column_header": "Project name",
                                "text": "Current first",
                                "actions": [
                                    {"locator": {"scope": "row", "value": "td:nth-child(2) a"}}
                                ],
                            },
                            {"column_header": "Action", "text": "Open project"},
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(1)"}],
                    }
                ],
            },
        ]
    }

    plan = _build_preplanned_plan("点击第一个项目", snapshot)

    assert plan is not None
    assert "td:nth-child(2) a" in plan["code"]
    assert "EXPORT-001" not in plan["code"]


def test_ordinal_click_preplan_prefers_explicit_region_title():
    snapshot = {
        "table_views": [
            {
                "title": "Collection locator rewrite guard",
                "columns": [
                    {"header": "Row key", "index": 0},
                    {"header": "Name", "index": 1},
                    {"header": "Action", "index": 2, "role": "action"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Row key", "text": "COLLECTION-ROW-001"},
                            {"column_header": "Name", "text": "First scoped row"},
                            {"column_header": "Action", "text": "Open row action"},
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(0)"}],
                    }
                ],
            },
            {
                "title": "Dynamic first item list",
                "columns": [
                    {"header": "Project ID", "index": 0},
                    {"header": "Project name", "index": 1},
                    {"header": "Action", "index": 2, "role": "action"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Project ID", "text": "DYN-REC-A"},
                            {"column_header": "Project name", "text": "Recorded first project"},
                            {"column_header": "Action", "text": "Open project"},
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(1)"}],
                    }
                ],
            },
        ]
    }

    plan = _build_preplanned_plan("在 Dynamic first item list 区域点击第一个项目", snapshot)

    assert plan is not None
    assert "Dynamic first item list" in plan["code"]
    assert "Collection locator rewrite guard" not in plan["code"]


def test_ordinal_click_preplan_prefers_real_chinese_region_title():
    snapshot = {
        "table_views": [
            {
                "title": "Collection locator rewrite guard",
                "columns": [
                    {"header": "Row key", "index": 0},
                    {"header": "Name", "index": 1},
                    {"header": "Action", "index": 2, "role": "action"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Row key", "text": "COLLECTION-ROW-001"},
                            {"column_header": "Name", "text": "First scoped row"},
                            {"column_header": "Action", "text": "Open row action"},
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(0)"}],
                    }
                ],
            },
            {
                "title": "Dynamic first item list",
                "columns": [
                    {"header": "Project ID", "index": 0},
                    {"header": "Project name", "index": 1},
                    {"header": "Action", "index": 2, "role": "action"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Project ID", "text": "DYN-REC-A"},
                            {"column_header": "Project name", "text": "Recorded first project"},
                            {"column_header": "Action", "text": "Open project"},
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(1)"}],
                    }
                ],
            },
        ]
    }

    plan = _build_preplanned_plan(
        "在 Dynamic first item list 区域，按照当前列表顺序点击第一个项目。",
        snapshot,
    )

    assert plan is not None
    assert "Dynamic first item list" in plan["code"]
    assert "Collection locator rewrite guard" not in plan["code"]


def test_table_rows_setup_uses_headers_when_region_contains_multiple_tables():
    snapshot = {
        "table_views": [
            {
                "title": "Split header/body grid",
                "columns": [
                    {"header": "File ID", "index": 0},
                    {"header": "File name", "index": 1, "role": "file_link"},
                    {"header": "Owner", "index": 2},
                    {"header": "Status", "index": 3},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "File ID", "text": "FILE-001"},
                            {
                                "column_header": "File name",
                                "text": "first.csv",
                                "actions": [{"locator": {"scope": "row", "value": "td:nth-child(2) a"}}],
                            },
                            {"column_header": "Owner", "text": "Lab"},
                            {"column_header": "Status", "text": "ready"},
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(0)"}],
                    }
                ],
            }
        ]
    }

    plan = _build_preplanned_plan(
        "在 Split header/body grid 区域，打开第一行文件链接。",
        snapshot,
    )

    assert plan is not None
    assert "_tables_after_heading" in plan["code"]
    assert "get_by_role('table', name=_title)" in plan["code"]
    assert "_first_row_markers" in plan["code"]
    assert "File ID" in plan["code"]
    assert "following::table[.//tbody/tr][1]" not in plan["code"]


def test_ordinal_click_preplan_does_not_fallback_when_explicit_region_is_missing():
    snapshot = {
        "table_views": [
            {
                "title": "Collection locator rewrite guard",
                "columns": [
                    {"header": "Row key", "index": 0},
                    {"header": "Name", "index": 1},
                    {"header": "Action", "index": 2, "role": "action"},
                ],
                "rows": [
                    {
                        "cells": [
                            {"column_header": "Row key", "text": "COLLECTION-ROW-001"},
                            {"column_header": "Name", "text": "First scoped row"},
                            {"column_header": "Action", "text": "Open row action"},
                        ],
                        "locator_hints": [{"expression": "page.locator('tbody tr').nth(0)"}],
                    }
                ],
            }
        ]
    }

    plan = _build_preplanned_plan(
        "在 Dynamic first item list 区域，按照当前列表顺序点击第一个项目。",
        snapshot,
    )

    assert plan is None


def test_ordinal_click_preplan_does_not_use_page_global_collection_for_explicit_region():
    snapshot = _ordinal_snapshot()

    plan = _build_preplanned_plan(
        "在 Dynamic first item list 区域，按照当前列表顺序点击第一个项目。",
        snapshot,
    )

    assert plan is None


def test_ordinal_click_preplan_ignores_toolbar_collection_without_menu_intent():
    snapshot = _ordinal_snapshot()
    snapshot["actionable_nodes"] = [
        {
            "node_id": "toolbar-0",
            "role": "button",
            "name": "Export menu",
            "text": "Export menu",
            "collection_item_selector": "div.toolbar button",
            "collection_item_count": 2,
            "bbox": {"x": 10, "y": 10},
        },
        {
            "node_id": "toolbar-1",
            "role": "button",
            "name": "Refresh",
            "text": "Refresh",
            "collection_item_selector": "div.toolbar button",
            "collection_item_count": 2,
            "bbox": {"x": 90, "y": 10},
        },
        *snapshot["actionable_nodes"],
    ]

    plan = _build_preplanned_plan("点击第一个项目", snapshot)

    assert plan is not None
    assert "div.toolbar button" not in plan["code"]
    assert "h2.lh-condensed a" in plan["code"]
