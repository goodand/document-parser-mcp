"""Only local, unique source witnesses may establish a table structure defect."""

from __future__ import annotations

from govparse.parse_diagnostics import diagnose_tables


def _table(rows):
    cells = []
    for row, line in enumerate(rows):
        for col, value in enumerate(line):
            cells.append({
                "row": row, "col": col, "row_span": 1, "col_span": 1,
                "blocks": [{"kind": "paragraph", "text": value}],
            })
    return {"kind": "table", "rows": len(rows), "cols": len(rows[0]), "cells": cells}


def test_two_unique_source_rows_in_one_pdf_cell_are_a_local_defect():
    ir = {"body": [_table([
        ["Domestic patent review", "33,180"],
        ["International patent review", "16,769"],
        ["Traditional knowledge database", "2,081"],
    ])]}
    pdf = [{"page": 2, "source": "opendataloader", "rows": [
        ["Domestic patent review International patent review", "33,180 16,769"],
        ["Traditional knowledge database", "2,081"],
    ]}]

    result = diagnose_tables(ir, pdf, verified_links=[{
        "source_body_index": 0, "pdf_table_index": 0,
        "evidence": "Source table and PDF region independently verified",
    }])

    defects = [d for d in result["diagnostics"] if d["code"] == "row_boundary_collapse"]
    assert len(defects) == 1
    assert defects[0]["status"] == "문제있음"
    assert defects[0]["source_locator"]["body_index"] == 0
    assert defects[0]["pdf_locator"]["page"] == 2
    assert defects[0]["pdf_locator"]["cell"] == [0, 0]
    assert result["use_decision"]["source_hwp_retrieval"] == "usable"
    assert result["use_decision"]["pdf_table_structure"] == "held"
    assert result["use_decision"]["automatic_judgment"] == "held"


def test_normal_table_gets_no_defect_from_legacy_review_flag():
    ir = {"body": [_table([
        ["Patent support program", "63,443"],
        ["Trademark support program", "10,681"],
    ])]}
    pdf = [{"page": 8, "source": "recovered", "review_required": True, "rows": [
        ["Patent support program", "63,443"],
        ["Trademark support program", "10,681"],
    ]}]

    result = diagnose_tables(ir, pdf)

    assert not [d for d in result["diagnostics"] if d["status"] == "문제있음"]
    assert result["use_decision"]["source_hwp_retrieval"] == "usable"
    assert result["use_decision"]["automatic_judgment"] == "held"  # unit/context unverified


def test_shared_witness_across_source_tables_cannot_prove_identity():
    repeated = "Shared activity description"
    ir = {"body": [
        _table([[repeated], ["Alpha distinct activity"]]),
        _table([[repeated], ["Beta distinct activity"]]),
    ]}
    pdf = [{"page": 3, "source": "opendataloader", "rows": [
        [f"{repeated} Alpha distinct activity Beta distinct activity"]
    ]}]

    result = diagnose_tables(ir, pdf)

    assert not [d for d in result["diagnostics"] if d["status"] == "문제있음"]
    # Repeated words alone must not produce an alert on an unrelated table.
    assert result["use_decision"]["automatic_judgment"] == "held"


def test_text_witness_without_independent_link_is_not_a_confirmed_defect():
    ir = {"body": [_table([
        ["Domestic patent review"], ["International patent review"]
    ])]}
    pdf = [{"page": 2, "source": "opendataloader", "rows": [[
        "Domestic patent review International patent review"
    ]]}]

    result = diagnose_tables(ir, pdf)

    assert not [d for d in result["diagnostics"] if d["status"] == "문제있음"]
    assert any(d["code"] == "table_match_ambiguous" for d in result["diagnostics"])
    assert result["use_decision"]["pdf_table_structure"] == "held"
