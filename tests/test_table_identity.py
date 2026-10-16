"""Multi-cell witnesses narrow review targets, without certifying PDF identity."""

from __future__ import annotations

from govparse.table_identity import propose_links, suspect_row_collapses


def _table(label: str, nums: tuple[str, str, str]):
    return {"kind": "table", "rows": 1, "cols": 4, "cells": [
        {"row": 0, "col": col, "blocks": [{"kind": "paragraph", "text": value}]}
        for col, value in enumerate((label, *nums))
    ]}


def test_unique_multi_cell_witness_is_candidate_only():
    ir = {"body": [
        _table("Domestic patent review", ("33,180", "35,772", "42,998")),
        _table("International patent review", ("16,769", "16,627", "18,063")),
    ]}
    pdf = [{"page": 2, "rows": [[
        "Domestic patent review Other content", "33,180 10", "35,772 20", "42,998 30"
    ]]}]

    result = propose_links(ir, pdf)[0]

    assert result["status"] == "unique_identity_witness"
    assert result["source_body_index"] == 0
    assert result["identity_confirmed"] is False
    assert result["row_witnesses"][0]["source_row"] == 0
    assert len(result["row_witnesses"][0]["numeric_witnesses"]) >= 3


def test_changed_number_or_competing_table_is_undecidable():
    source = _table("Domestic patent review", ("33,180", "35,772", "42,998"))
    pdf = [{"page": 2, "rows": [[
        "Domestic patent review", "33,180", "35,772", "42,998"
    ]]}]
    altered = [{"page": 2, "rows": [[
        "Domestic patent review", "33,180", "35,773", "42,998"
    ]]}]

    assert propose_links({"body": [source]}, altered)[0]["status"] == "undecidable"
    duplicate = propose_links({"body": [source, source]}, pdf)[0]
    assert duplicate["status"] == "undecidable"
    assert duplicate["source_body_index"] is None


def test_unique_candidate_can_surface_a_local_suspicion_without_certifying_it():
    ir = {"body": [{"kind": "table", "rows": 2, "cols": 4, "cells": [
        {"row": row, "col": col, "blocks": [{"kind": "paragraph", "text": value}]}
        for row, values in enumerate([
            ("Domestic patent review", "33,180", "35,772", "42,998"),
            ("International patent review", "16,769", "16,627", "18,063"),
        ]) for col, value in enumerate(values)
    ]}]}
    pdf = [{"page": 2, "rows": [[
        "Domestic patent review International patent review",
        "33,180 16,769", "35,772 16,627", "42,998 18,063",
    ]]}]

    proposals = propose_links(ir, pdf)
    risks = suspect_row_collapses(ir, pdf, proposals)

    assert proposals[0]["status"] == "unique_identity_witness"
    assert len(risks) == 1
    assert risks[0]["code"] == "row_boundary_collapse_suspected"
    assert risks[0]["status"] == "판단불가"
    assert risks[0]["source_locator"]["body_index"] == 0
    assert risks[0]["pdf_locator"]["cell"] == [0, 0]
