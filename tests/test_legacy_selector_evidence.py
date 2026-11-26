from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageStat

from govparse.selector_evidence.legacy import generate_legacy_candidates


def _pdf_with_table(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=420, height=300)
    shape = page.new_shape()
    for x in (60, 210, 360):
        shape.draw_line((x, 80), (x, 160))
    for y in (80, 120, 160):
        shape.draw_line((60, y), (360, y))
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()
    for point, text in (
        ((70, 104), "Export Program"),
        ((220, 104), "Unique Recipient"),
        ((70, 144), "Budget 420000"),
        ((220, 144), "Deadline 2026-12"),
    ):
        page.insert_text(point, text, fontsize=9)
    document.save(path)
    document.close()


def _ir() -> dict:
    return {
        "body": [{
            "kind": "table", "rows": 2, "cols": 2,
            "prov": {"section_idx": 0},
            "cells": [
                {"row": 0, "col": 0, "blocks": [{"kind": "paragraph", "text": "Export Program"}]},
                {"row": 0, "col": 1, "blocks": [{"kind": "paragraph", "text": "Unique Recipient"}]},
                {"row": 1, "col": 0, "blocks": [{"kind": "paragraph", "text": "Budget 420000"}]},
                {"row": 1, "col": 1, "blocks": [{"kind": "paragraph", "text": "Deadline 2026-12"}]},
            ],
        }],
    }


def test_legacy_candidates_crop_and_map_native_table(tmp_path: Path) -> None:
    pdf = tmp_path / "source.pdf"
    _pdf_with_table(pdf)

    result = generate_legacy_candidates(pdf, _ir(), tmp_path / "png", dpi=96)

    assert result["candidate_count"] == 1
    assert result["matched_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["candidate_id"] == "p0001_t0"
    assert candidate["match_status"] == "matched"
    assert candidate["native_table_id"] == "s0/b0/t0"
    assert candidate["detected_rows"] == 2
    assert candidate["detected_cols"] == 2
    assert Path(candidate["png_path"]).is_file()
    with Image.open(candidate["png_path"]) as image:
        assert image.width > 300
        assert ImageStat.Stat(image.convert("L")).stddev[0] > 1


def test_legacy_candidates_hold_when_native_text_does_not_match(tmp_path: Path) -> None:
    pdf = tmp_path / "source.pdf"
    _pdf_with_table(pdf)
    ir = _ir()
    for cell in ir["body"][0]["cells"]:
        cell["blocks"][0]["text"] = "unrelated value"

    result = generate_legacy_candidates(pdf, ir, tmp_path / "png", dpi=96)

    assert result["candidate_count"] == 1
    assert result["matched_count"] == 0
    candidate = result["candidates"][0]
    assert candidate["match_status"] == "held"
    assert candidate["native_table_id"] is None
    assert Path(candidate["png_path"]).is_file()

