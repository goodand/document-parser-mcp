"""Contract tests for native IR to editable-table candidate conversion."""
from __future__ import annotations

from copy import deepcopy

import pytest

from govparse.table_edit import build_native_table_candidate
from govparse.table_images import table_index


def _paragraph(text):
    return {"kind": "paragraph", "text": text}


def _table(rows, cols, cells, *, section=0):
    return {"kind": "table", "rows": rows, "cols": cols, "cells": cells,
            "prov": {"section_idx": section}}


def test_native_candidate_preserves_multiline_text_spans_and_asset_link():
    table = _table(2, 3, [
        {"row": 0, "col": 0, "row_span": 2, "col_span": 1,
         "blocks": [_paragraph("첫 줄\n계속"), _paragraph("둘째 문단")]},
        {"row": 0, "col": 1, "row_span": 1, "col_span": 2,
         "blocks": [_paragraph("합친 셀")]},
        {"row": 1, "col": 1, "blocks": [_paragraph("")]},
    ])
    ir = {"body": [_paragraph("앞 문단"), table]}
    before = deepcopy(ir)
    table_id = "s0/b1/t0"

    result = build_native_table_candidate(
        ir, table_id, asset_id="asset-17", image_sha256="a" * 64
    )

    assert result == {
        "schema_version": "1.0",
        "source_kind": "native",
        "table_id": table_id,
        "asset_id": "asset-17",
        "image_sha256": "a" * 64,
        "rows": 2,
        "cols": 3,
        "cells": [
            {"row": 0, "col": 0, "row_span": 2, "col_span": 1,
             "text": "첫 줄\n계속\n둘째 문단", "bbox_px": None, "nested_table_ids": []},
            {"row": 0, "col": 1, "row_span": 1, "col_span": 2,
             "text": "합친 셀", "bbox_px": None, "nested_table_ids": []},
            {"row": 1, "col": 1, "row_span": 1, "col_span": 1,
             "text": "", "bbox_px": None, "nested_table_ids": []},
        ],
        "status": "ready",
    }
    assert ir == before


def test_native_candidate_links_direct_nested_children_with_stable_ids():
    grandchild = _table(1, 1, [
        {"row": 0, "col": 0, "blocks": [_paragraph("깊은 표")]}])
    child_a = _table(1, 2, [
        {"row": 0, "col": 0, "blocks": [_paragraph("하위 A"), grandchild]},
        {"row": 0, "col": 1, "blocks": [_paragraph("하위 B")]},
    ])
    child_b = _table(1, 1, [
        {"row": 0, "col": 0, "blocks": [_paragraph("하위 C")]}
    ])
    root = _table(1, 2, [
        {"row": 0, "col": 0, "blocks": [_paragraph("상위"), child_a, child_b]},
        {"row": 0, "col": 1, "blocks": [_paragraph("외부")]}])
    ir = {"body": [root]}
    index = table_index(ir)
    root_id = "s0/b0/t0"
    child_a_id = f"{root_id}/c0_0/t0"
    child_b_id = f"{root_id}/c0_0/t1"
    grandchild_id = f"{child_a_id}/c0_0/t0"

    root_result = build_native_table_candidate(ir, root_id)
    child_result = build_native_table_candidate(ir, child_a_id)

    assert set(index) == {root_id, child_a_id, child_b_id, grandchild_id}
    assert root_result["cells"][0]["nested_table_ids"] == [child_a_id, child_b_id]
    assert root_result["cells"][1]["nested_table_ids"] == []
    assert child_result["cells"][0]["text"] == "하위 A"
    assert child_result["cells"][0]["nested_table_ids"] == [grandchild_id]


def test_bbox_px_is_passed_only_when_explicitly_valid():
    table = _table(1, 2, [
        {"row": 0, "col": 0, "bbox_px": [1, 2, 30, 40], "blocks": []},
        {"row": 0, "col": 1, "bbox_px": [1, 2, float("nan"), 40], "blocks": []},
    ])
    result = build_native_table_candidate({"body": [table]}, "s0/b0/t0")
    assert result["cells"][0]["bbox_px"] == [1.0, 2.0, 30.0, 40.0]
    assert result["cells"][1]["bbox_px"] is None


def test_missing_table_id_and_invalid_native_shape_fail_clearly():
    with pytest.raises(ValueError, match="not present"):
        build_native_table_candidate({"body": []}, "s0/b0/t0")

    bad_table = _table(0, 1, [])
    with pytest.raises(ValueError, match="rows must be a positive integer"):
        build_native_table_candidate({"body": [bad_table]}, "s0/b0/t0")


@pytest.mark.parametrize("row,col_span", [(0.5, 1), (True, 1), (0, 2)])
def test_invalid_native_address_cannot_become_editable(row, col_span):
    table = _table(1, 1, [{"row": row, "col": 0, "col_span": col_span}])
    with pytest.raises(ValueError):
        build_native_table_candidate({"body": [table]}, "s0/b0/t0")


def test_inverted_pixel_box_is_not_exposed_to_editor():
    table = _table(1, 1, [{"row": 0, "col": 0, "bbox_px": [4, 0, 2, 3]}])
    result = build_native_table_candidate({"body": [table]}, "s0/b0/t0")
    assert result["cells"][0]["bbox_px"] is None
