"""OCR table geometry must remain review-only and preserve raw detections."""

from __future__ import annotations

import json
import math
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest

import govparse.pdf_table_ocr as table_ocr


@pytest.fixture(autouse=True)
def isolated_model_directory(tmp_path, monkeypatch):
    """OCR is mocked below; never depend on a developer's installed model folder.

    Author: chatgpt_gpt6. This directory contains no real model weights.
    """
    model_dir = tmp_path / "mock-models"
    model_dir.mkdir()
    monkeypatch.setenv("GOVPARSE_OCR_MODEL_DIR", str(model_dir))


def _rotated_table_pdf(path):
    """Create a simple 2x2 table on a page displayed with a 90-degree rotation."""
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=300)
    page.draw_rect(pymupdf.Rect(40, 50, 360, 250), width=1)
    page.draw_line(pymupdf.Point(200, 50), pymupdf.Point(200, 250), width=1)
    page.draw_line(pymupdf.Point(40, 150), pymupdf.Point(360, 150), width=1)
    page.insert_text((60, 100), "alpha")
    page.insert_text((220, 100), "beta")
    page.insert_text((60, 200), "gamma")
    page.insert_text((220, 200), "delta")
    page.set_rotation(90)
    doc.save(path)
    doc.close()


def _png_size(image_bytes):
    return struct.unpack(">II", image_bytes[16:24])


def _det(box, text):
    return {"box": box, "text": text, "score": 0.91}


def _fake_ocr(detections):
    def run(image_bytes, model_dir):
        width, height = _png_size(image_bytes)
        converted = []
        for detection in detections:
            box, text, score = (
                detection["box"], detection["text"], detection["score"]
            )
            points = [[x * width, y * height] for x, y in box]
            converted.append((points, text, score))
        return {
            "boxes": [item[0] for item in converted],
            "texts": [item[1] for item in converted],
            "scores": [item[2] for item in converted],
            "runtime": {"engine": "synthetic-test"},
        }

    return run


def test_rotated_page_maps_ocr_centers_into_table_cells_without_approving(tmp_path, monkeypatch):
    pdf = tmp_path / "rotated.pdf"
    _rotated_table_pdf(pdf)
    monkeypatch.setattr(
        table_ocr, "_run_ocr",
        _fake_ocr([
            _det([[0.20, 0.20], [0.30, 0.20], [0.30, 0.30], [0.20, 0.30]], "alpha"),
            _det([[0.70, 0.20], [0.80, 0.20], [0.80, 0.30], [0.70, 0.30]], "beta"),
            _det([[0.20, 0.70], [0.30, 0.70], [0.30, 0.80], [0.20, 0.80]], "gamma"),
            _det([[0.70, 0.70], [0.80, 0.70], [0.80, 0.80], [0.70, 0.80]], "delta"),
        ]),
    )

    result = table_ocr.recover_pdf_table_text(pdf, page=1, dpi=120)

    assert result["page_rotation"] == 90
    assert len(result["cells"]) == 4
    assert {(d["cell"]["row"], d["cell"]["col"]) for d in result["detections"]} == {
        (0, 0), (0, 1), (1, 0), (1, 1)
    }
    assert [d["raw_text"] for d in result["detections"]] == [
        "alpha", "beta", "gamma", "delta"
    ]
    assert result["target_match"] == "unverified"
    assert result["held_for_review"] is True
    assert result["embedding_approved"] is False
    assert result["production_applied"] is False


def test_text_is_not_normalized_and_outside_or_boundary_detections_are_unassigned(
    tmp_path, monkeypatch
):
    pdf = tmp_path / "rotated.pdf"
    _rotated_table_pdf(pdf)
    raw_values = [" A\u00b7B ", "boundary", "outside"]
    monkeypatch.setattr(
        table_ocr, "_run_ocr",
        _fake_ocr([
            _det([[0.20, 0.20], [0.30, 0.20], [0.30, 0.30], [0.20, 0.30]], raw_values[0]),
            # Center lies exactly on the center grid line; it must not be assigned twice.
            _det([[0.49, 0.20], [0.51, 0.20], [0.51, 0.30], [0.49, 0.30]], raw_values[1]),
            # The polygon is partly outside the crop and its center is outside the table.
            _det([[-0.04, 0.45], [0.01, 0.45], [0.01, 0.50], [-0.04, 0.50]], raw_values[2]),
        ]),
    )

    result = table_ocr.recover_pdf_table_text(pdf, page=1, dpi=120)

    assert [d["raw_text"] for d in result["detections"]] == raw_values
    assert result["detections"][0]["cell"] == {"row": 0, "col": 0}
    assert result["detections"][1]["cell"] is None
    assert result["detections"][2]["cell"] is None
    assert any(
        warning["code"] == "detection_not_uniquely_assigned_to_cell"
        and warning["candidate_cell_count"] == 2
        for warning in result["warnings"]
    )
    assert any(
        warning["code"] == "detection_not_uniquely_assigned_to_cell"
        and warning["candidate_cell_count"] == 0
        for warning in result["warnings"]
    )
    assert result["embedding_approved"] is False


def test_empty_ocr_is_review_only_and_returns_no_fabricated_cell_text(tmp_path, monkeypatch):
    pdf = tmp_path / "rotated.pdf"
    _rotated_table_pdf(pdf)
    monkeypatch.setattr(table_ocr, "_run_ocr", lambda image_bytes, model_dir: {
        "boxes": [], "texts": [], "scores": [], "runtime": {"engine": "synthetic-test"}
    })

    result = table_ocr.recover_pdf_table_text(pdf, page=1)

    assert result["detections"] == []
    assert all(cell["joined_raw_text"] == "" for cell in result["cells"])
    assert result["embedding_approved"] is False
    assert result["production_applied"] is False
    assert result["target_match"] == "unverified"


def test_malformed_ocr_geometry_is_preserved_as_json_safe_review_evidence(
    tmp_path, monkeypatch
):
    pdf = tmp_path / "rotated.pdf"
    _rotated_table_pdf(pdf)
    monkeypatch.setattr(table_ocr, "_run_ocr", lambda image_bytes, model_dir: {
        "boxes": [[[math.nan, 0], [1, 0], [1, 1]]],
        "texts": ["raw"],
        "scores": [math.nan],
        "runtime": {"engine": "synthetic-test"},
    })

    result = table_ocr.recover_pdf_table_text(pdf, page=1)

    detection = result["detections"][0]
    assert detection["raw_text"] == "raw"
    assert detection["bbox_page_top_left"] is None
    assert detection["raw_polygon"][0][0] == "nan"
    assert detection["confidence"] == "nan"
    assert result["warnings"][0]["code"] == "invalid_ocr_detection"
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_same_line_tokens_sort_left_to_right_despite_small_y_jitter(tmp_path, monkeypatch):
    pdf = tmp_path / "rotated.pdf"
    _rotated_table_pdf(pdf)
    monkeypatch.setattr(
        table_ocr, "_run_ocr",
        _fake_ocr([
            _det([[0.29, 0.242], [0.34, 0.242], [0.34, 0.258], [0.29, 0.258]], "right"),
            _det([[0.17, 0.240], [0.22, 0.240], [0.22, 0.256], [0.17, 0.256]], "left"),
        ]),
    )

    result = table_ocr.recover_pdf_table_text(pdf, page=1)

    cell = next(cell for cell in result["cells"] if (cell["row"], cell["col"]) == (0, 0))
    assert [item["raw_text"] for item in cell["detections"]] == ["left", "right"]


def test_rapidocr_numpy_arrays_are_serialized_without_truth_value_checks(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "detector.onnx").write_bytes(b"test model bytes")
    expected = SimpleNamespace(
        boxes=np.array([[[1.0, 2.0], [4.0, 2.0], [4.0, 6.0], [1.0, 6.0]]]),
        txts=np.array(["sample"]),
        scores=np.array([0.9]),
    )
    rapidocr = SimpleNamespace(
        EngineType=SimpleNamespace(ONNXRUNTIME="onnx"),
        LangDet=SimpleNamespace(MULTI="multi"),
        LangRec=SimpleNamespace(KOREAN="ko"),
        ModelType=SimpleNamespace(MOBILE="mobile"),
        OCRVersion=SimpleNamespace(PPOCRV5="v5"),
        RapidOCR=lambda params: lambda image: expected,
    )
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr)
    monkeypatch.setitem(
        sys.modules,
        "cv2",
        SimpleNamespace(
            IMREAD_COLOR=1,
            imdecode=lambda encoded, mode: object(),
        ),
    )
    monkeypatch.setattr(table_ocr.importlib.metadata, "version", lambda name: "test-version")

    result = table_ocr._run_ocr(b"fake image", model_dir)

    assert result["boxes"] == [[[1.0, 2.0], [4.0, 2.0], [4.0, 6.0], [1.0, 6.0]]]
    assert result["texts"] == ["sample"]
    assert result["scores"] == [0.9]
    assert result["runtime"]["model_sha256"]["detector.onnx"]


@pytest.mark.parametrize("page,dpi", [(0, 120), (2, 120), (1, 0), (1, -1)])
def test_invalid_page_or_dpi_is_rejected(tmp_path, page, dpi):
    pdf = tmp_path / "rotated.pdf"
    _rotated_table_pdf(pdf)

    with pytest.raises(ValueError):
        table_ocr.recover_pdf_table_text(pdf, page=page, dpi=dpi)


def test_invalid_pdf_is_rejected(tmp_path):
    pdf = tmp_path / "not-a-pdf.pdf"
    pdf.write_bytes(b"not a pdf")

    with pytest.raises((ValueError, RuntimeError)):
        table_ocr.recover_pdf_table_text(pdf, page=1)
