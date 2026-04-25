from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import inspect
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "rpa" / "snapshot_compression.py"
_SPEC = spec_from_file_location("snapshot_compression_structured_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

compact_recording_snapshot = _MODULE.compact_recording_snapshot


def _structured_view_snapshot() -> dict:
    return {
        "url": "https://example.test/grid",
        "title": "Grid",
        "content_nodes": [],
        "actionable_nodes": [],
        "containers": [],
        "frames": [],
        "table_views": [
            {
                "kind": "table_view",
                "title": "EDM Request",
                "title_source": "nearest_preceding_heading",
                "nearby_headings": ["EDM Request"],
                "framework_hint": "aui-grid",
                "row_count_observed": 10,
                "columns": [
                    {"index": 0, "column_id": "col_23", "header": "", "role": "row_index", "sample_values": ["1", "2"]},
                    {"index": 1, "column_id": "col_24", "header": "", "role": "selection", "sample_values": []},
                    {"index": 2, "column_id": "col_25", "header": "文件名称", "role": "file_link", "sample_values": ["File_189.xlsx"]},
                ],
                "rows": [
                    {
                        "index": 0,
                        "cells": [
                            {
                                "column_id": "col_23",
                                "column_index": 0,
                                "column_header": "",
                                "text": "1",
                                "value_kind": "number",
                                "actions": [],
                            },
                            {
                                "column_id": "col_25",
                                "column_index": 2,
                                "column_header": "文件名称",
                                "text": "File_189.xlsx",
                                "value_kind": "text",
                                "actions": [
                                    {
                                        "kind": "link",
                                        "label": "File_189.xlsx",
                                        "locator": {
                                            "method": "relative_css",
                                            "scope": "row",
                                            "value": "td[data-colid='col_25'] a",
                                        },
                                    }
                                ],
                            },
                        ],
                        "locator_hints": [{"kind": "playwright", "expression": "page.locator('tbody tr').nth(0)"}],
                    }
                ],
                "auxiliary_text": [{"kind": "empty_state", "text": "暂无数据", "outside_rows": True}],
            }
        ],
        "detail_views": [
            {
                "kind": "detail_view",
                "section_title": "采购信息",
                "fields": [
                    {
                        "label": "预计总金额(含税)",
                        "value": "100.00",
                        "data_prop": "amount",
                        "required": True,
                        "visible": True,
                        "value_kind": "number",
                    },
                    {
                        "label": "隐藏字段",
                        "value": "secret",
                        "data_prop": "hidden",
                        "required": False,
                        "visible": False,
                        "hidden_reason": "display_none",
                        "value_kind": "text",
                    },
                ],
            }
        ],
    }


def test_compact_recording_snapshot_preserves_structured_views():
    compact = compact_recording_snapshot(_structured_view_snapshot(), "点击第一行的文件名称", char_budget=100000)

    assert compact["mode"] == "clean_snapshot"
    assert compact["table_views"][0]["title"] == "EDM Request"
    assert compact["table_views"][0]["title_source"] == "nearest_preceding_heading"
    assert compact["table_views"][0]["nearby_headings"] == ["EDM Request"]
    assert compact["table_views"][0]["columns"][2]["header"] == "文件名称"
    assert compact["table_views"][0]["rows"][0]["cells"][1]["actions"][0]["locator"]["scope"] == "row"
    assert compact["table_views"][0]["auxiliary_text"][0]["outside_rows"] is True
    assert compact["detail_views"][0]["section_title"] == "采购信息"
    assert compact["detail_views"][0]["fields"][0]["data_prop"] == "amount"


def test_default_structured_snapshot_budget_is_60000():
    signature = inspect.signature(compact_recording_snapshot)
    assert signature.parameters["char_budget"].default == 60000

    snapshot = _structured_view_snapshot()
    snapshot["detail_views"][0]["fields"].extend(
        {
            "label": f"扩展字段{index}",
            "value": "这是一个用于验证默认结构化快照预算的中等长度字段值",
            "data_prop": f"extra_{index}",
            "required": False,
            "visible": True,
            "value_kind": "text",
        }
        for index in range(120)
    )

    compact = compact_recording_snapshot(snapshot, "提取采购信息")

    assert compact["mode"] == "clean_snapshot"
