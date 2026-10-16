"""Evidence images retain page context and never assert a detector's target match."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pymupdf

from govparse.visual_evidence import render_visual_evidence


def _pdf(path):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((30, 30), "UNIT: million")
    page.draw_rect(pymupdf.Rect(30, 100, 250, 250))
    doc.save(str(path))
    doc.close()


def test_no_detected_table_still_returns_source_page(tmp_path, monkeypatch):
    pdf = tmp_path / "input.pdf"
    _pdf(pdf)
    monkeypatch.setattr(
        pymupdf.Page, "find_tables", lambda self, **kw: SimpleNamespace(tables=[])
    )

    result = render_visual_evidence(pdf, tmp_path / "images", page=1, dpi=96)

    assert result["status"] == "판단불가"
    assert result["source_pdf_sha256"]
    assert result["page"] == 1
    assert result["full_page"]["coverage"] == "page"
    assert (tmp_path / "images" / result["full_page"]["filename"]).is_file()
    assert result["detected_crops"] == []
    assert result["target_match"] == "unverified"


def test_detected_crop_and_requested_region_keep_provenance(tmp_path, monkeypatch):
    pdf = tmp_path / "input.pdf"
    _pdf(pdf)
    monkeypatch.setattr(
        pymupdf.Page,
        "find_tables",
        lambda self, **kw: SimpleNamespace(tables=[SimpleNamespace(bbox=(30, 100, 250, 250))]),
    )

    result = render_visual_evidence(
        pdf, tmp_path / "images", page=1, dpi=96, bbox_top_left=[20, 80, 260, 270]
    )

    assert len(result["detected_crops"]) == 1
    assert result["detected_crops"][0]["target_match"] == "unverified"
    assert result["requested_region"]["coverage"] == "requested_region"
    assert result["requested_region"]["bbox_top_left"] == [20.0, 80.0, 260.0, 270.0]
    assert result["full_page"]["filename"] != result["detected_crops"][0]["filename"]
    assert result["source_pdf_sha256"][:12] in result["full_page"]["filename"]


def test_changed_pdf_does_not_reuse_old_image_name(tmp_path, monkeypatch):
    pdf = tmp_path / "input.pdf"
    _pdf(pdf)
    monkeypatch.setattr(
        pymupdf.Page, "find_tables", lambda self, **kw: SimpleNamespace(tables=[])
    )
    first = render_visual_evidence(pdf, tmp_path / "images", page=1, dpi=96)
    doc = pymupdf.open(str(pdf))
    doc.set_metadata({"title": "changed"})
    changed = tmp_path / "changed.pdf"
    doc.save(str(changed))
    doc.close()
    second = render_visual_evidence(changed, tmp_path / "images", page=1, dpi=96)

    assert first["source_pdf_sha256"] != second["source_pdf_sha256"]
    assert first["full_page"]["filename"] != second["full_page"]["filename"]


def test_detector_failure_keeps_page_and_invalid_request_is_rejected(tmp_path, monkeypatch):
    pdf = tmp_path / "input.pdf"
    _pdf(pdf)

    def failed_detector(self, **kw):
        raise RuntimeError("detector failed")

    monkeypatch.setattr(pymupdf.Page, "find_tables", failed_detector)
    result = render_visual_evidence(pdf, tmp_path / "images", page=1, dpi=96)
    assert result["detector_error"] == "RuntimeError: detector failed"
    assert result["detected_crops"] == []
    assert (tmp_path / "images" / result["full_page"]["filename"]).is_file()

    import pytest

    with pytest.raises(ValueError):
        render_visual_evidence(pdf, tmp_path / "images", page=2)
    with pytest.raises(ValueError):
        render_visual_evidence(pdf, tmp_path / "images", page=1, dpi=0)
    with pytest.raises(ValueError):
        render_visual_evidence(pdf, tmp_path / "images", page=1, bbox_top_left=[0, 0, 0, 1])


def test_odl_bottom_left_region_renders_pdf_composite_and_keeps_hold(tmp_path, monkeypatch):
    pdf = tmp_path / "input.pdf"
    _pdf(pdf)
    monkeypatch.setattr(
        pymupdf.Page, "find_tables", lambda self, **kw: SimpleNamespace(tables=[])
    )
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    (parsed / "p0-1.json").write_text(json.dumps({
        "kids": [{
            "type": "image", "id": "diagram-1", "page number": 1,
            "bounding box": [30, 150, 250, 300],
        }, {
            "type": "image", "id": "glyph-1", "page number": 1,
            "bounding box": [10, 10, 15, 15],
        }],
    }), encoding="utf-8")

    result = render_visual_evidence(
        pdf, tmp_path / "images", page=1, dpi=96, odl_out_dir=parsed
    )

    assert result["status"] == "판단불가"
    assert len(result["odl_candidates"]) == 1
    candidate = result["odl_candidates"][0]
    assert candidate["odl_bbox_bottom_left"] == [30.0, 150.0, 250.0, 300.0]
    assert candidate["rendered_bbox_top_left"] == [30.0, 100.0, 250.0, 250.0]
    assert candidate["review_required"] is True
    assert (tmp_path / "images" / candidate["filename"]).is_file()
    assert result["odl_skipped"][0]["reason"] == "small_or_outside_page_component"
