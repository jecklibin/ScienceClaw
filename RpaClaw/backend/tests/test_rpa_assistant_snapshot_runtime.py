from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "rpa" / "assistant_snapshot_runtime.py"
_SPEC = spec_from_file_location("assistant_snapshot_runtime_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

SNAPSHOT_V2_JS = _MODULE.SNAPSHOT_V2_JS
SNAPSHOT_V2_TEMPLATE = _MODULE.SNAPSHOT_V2_TEMPLATE


def test_snapshot_v2_js_captures_visible_business_fields():
    assert "span" in SNAPSHOT_V2_JS
    assert "[data-field]" in SNAPSHOT_V2_JS
    assert "[data-label]" in SNAPSHOT_V2_JS
    assert "[data-value]" in SNAPSHOT_V2_JS
    assert "function buildContentLocator(el, role, name, text, placeholder, title)" in SNAPSHOT_V2_JS
    assert "stable data-field locator" in SNAPSHOT_V2_JS
    assert "stable data-label locator" in SNAPSHOT_V2_JS
    assert "stable data-value locator" in SNAPSHOT_V2_JS
    assert "const className = normalizeText(el.className || '', 80);" in SNAPSHOT_V2_JS
    assert "const dataLabel = normalizeText(el.getAttribute('data-label') || '', 80);" in SNAPSHOT_V2_JS
    assert "data_field: normalizeText(el.getAttribute('data-field') || '', 80)" in SNAPSHOT_V2_JS
    assert "data_label: normalizeText(el.getAttribute('data-label') || '', 80)" in SNAPSHOT_V2_JS
    assert "data_value: normalizeText(el.getAttribute('data-value') || '', 80)" in SNAPSHOT_V2_JS
    assert "if (tag === 'label' || /label/i.test(className) || dataLabel)" in SNAPSHOT_V2_JS
    assert "if (dataField || dataValue || /value/i.test(className))" in SNAPSHOT_V2_JS
    assert "function isMeaningfulBusinessContainer(el)" in SNAPSHOT_V2_JS
    assert "data-section" in SNAPSHOT_V2_JS
    assert "data-region" in SNAPSHOT_V2_JS
    assert "detail_section" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_collects_structured_table_and_detail_views():
    assert "table_views: []" in SNAPSHOT_V2_JS
    assert "detail_views: []" in SNAPSHOT_V2_JS
    assert "function collectTableViews()" in SNAPSHOT_V2_JS
    assert "function collectDetailViews()" in SNAPSHOT_V2_JS
    assert "data-colid" in SNAPSHOT_V2_JS
    assert "row_count_observed" in SNAPSHOT_V2_JS
    assert "column_header" in SNAPSHOT_V2_JS
    assert "row_local_actions" in SNAPSHOT_V2_JS
    assert "const detailViewAdapters = [" in SNAPSHOT_V2_JS
    assert "name: 'aui'" not in SNAPSHOT_V2_JS
    assert "aui-input-display-only" not in SNAPSHOT_V2_JS
    assert "aui-form-item" not in SNAPSHOT_V2_JS
    assert "data_prop" in SNAPSHOT_V2_JS
    assert "value_kind" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_marks_non_row_table_text_as_auxiliary():
    assert "auxiliary_text" in SNAPSHOT_V2_JS
    assert "empty_state" in SNAPSHOT_V2_JS
    assert "tooltip" in SNAPSHOT_V2_JS
    assert "outside_rows" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_assigns_nearby_heading_to_table_view_title():
    assert "function nearestTableTitle(root)" in SNAPSHOT_V2_JS
    assert "previousElementSibling" in SNAPSHOT_V2_JS
    assert "nearest_preceding_heading" in SNAPSHOT_V2_JS
    assert "title_source" in SNAPSHOT_V2_JS


def test_snapshot_v2_js_collects_jalor_grid_as_scoped_table_view():
    assert "const tableViewAdapters = [" in SNAPSHOT_V2_JS
    assert "name: 'jalor-igrid'" in SNAPSHOT_V2_JS
    assert "adapter.collect(root)" in SNAPSHOT_V2_JS
    assert "function collectJalorGridTableView(root)" in SNAPSHOT_V2_JS
    assert ".jalor-igrid" in SNAPSHOT_V2_JS
    assert ".jalor-igrid-head tbody.igrid-head td" in SNAPSHOT_V2_JS
    assert ".jalor-igrid-body tbody.igrid-data tr.grid-row" in SNAPSHOT_V2_JS
    assert "tr.grid-row-group" in SNAPSHOT_V2_JS
    assert "field=\"tmpName\"" not in SNAPSHOT_V2_JS
    assert "fieldName || colNumber || `index:${index}`" in SNAPSHOT_V2_JS
    assert "bodyTableId ? `#${escapeCssIdentifier(bodyTableId)} tbody.igrid-data tr.grid-row`" in SNAPSHOT_V2_JS
    assert "td[field=\"" in SNAPSHOT_V2_JS
    assert "framework_hint: 'jalor-igrid'" in SNAPSHOT_V2_JS
    assert "const jalorViews = Array.from" not in SNAPSHOT_V2_JS


def test_snapshot_v2_template_keeps_framework_adapters_out_of_main_collector():
    assert "__RPA_TABLE_VIEW_ADAPTERS__" in SNAPSHOT_V2_TEMPLATE
    assert "__RPA_MODAL_VIEW_ADAPTERS__" in SNAPSHOT_V2_TEMPLATE
    assert "collectJalorGridTableView" not in SNAPSHOT_V2_TEMPLATE
    assert "rootSelector: '.el-overlay-dialog'" not in SNAPSHOT_V2_TEMPLATE


def test_snapshot_v2_js_uses_modal_adapter_registry_for_framework_dialogs():
    assert "const modalViewAdapters = [" in SNAPSHOT_V2_JS
    assert "name: 'semantic'" in SNAPSHOT_V2_JS
    assert "rootSelector: '[role=\"dialog\"],[aria-modal=\"true\"]'" in SNAPSHOT_V2_JS
    assert "name: 'element'" in SNAPSHOT_V2_JS
    assert "rootSelector: '.el-overlay-dialog'" in SNAPSHOT_V2_JS
    assert "name: 'ant'" in SNAPSHOT_V2_JS
    assert "rootSelector: '.ant-modal'" in SNAPSHOT_V2_JS
    assert "for (const adapter of modalViewAdapters)" in SNAPSHOT_V2_JS
    assert "document.querySelectorAll('[role=\"dialog\"],[aria-modal=\"true\"],.el-overlay-dialog" not in SNAPSHOT_V2_JS
