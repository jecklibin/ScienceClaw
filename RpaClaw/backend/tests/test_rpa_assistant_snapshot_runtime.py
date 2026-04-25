from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "rpa" / "assistant_snapshot_runtime.py"
_SPEC = spec_from_file_location("assistant_snapshot_runtime_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

SNAPSHOT_V2_JS = _MODULE.SNAPSHOT_V2_JS


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
    assert "aui-form-item" in SNAPSHOT_V2_JS
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
