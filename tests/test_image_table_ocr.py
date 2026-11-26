from copy import deepcopy

import pytest

from govparse.image_table_ocr import add_ocr_to_table_candidate


def _candidate():
    return {
        "source_kind": "image",
        "status": "review",
        "image": {"width_px": 100, "height_px": 40},
        "cells": [
            {"row": 0, "col": 0, "bbox_px": [0, 0, 50, 20], "text": ""},
            {"row": 0, "col": 1, "bbox_px": [50, 0, 100, 20], "text": ""},
            {"row": 1, "col": 0, "bbox_px": None, "text": ""},
        ],
        "warnings": [],
    }


def _ocr():
    return {
        "coordinates": "original_image_pixels",
        "image_width": 100,
        "image_height": 40,
        "boxes": [
            [[5, 8], [25, 8], [25, 18], [5, 18]],
            [[56, 8], [84, 8], [84, 18], [56, 18]],
            [[5, 25], [22, 25], [22, 35], [5, 35]],
        ],
        "texts": ["가", "나", "다"],
        "scores": [0.9, 0.8, 0.7],
        "runtime": {"engine": "RapidOCR"},
    }


def test_assigns_words_by_cell_overlap_and_preserves_review_status():
    candidate = _candidate()
    original = deepcopy(candidate)

    result = add_ocr_to_table_candidate(candidate, _ocr())

    assert result["cells"][0]["text"] == "가"
    assert result["cells"][1]["ocr_words"][0]["score"] == 0.8
    assert result["cells"][2]["text_status"] == "not_detected"
    assert result["status"] == "review"
    assert candidate == original


def test_words_for_cells_without_boxes_remain_unassigned():
    candidate = _candidate()
    ocr = _ocr()
    ocr["boxes"] = [ocr["boxes"][2]]
    ocr["texts"] = ["다"]
    ocr["scores"] = [0.7]

    result = add_ocr_to_table_candidate(candidate, ocr)

    assert result["ocr"]["unassigned_words"][0]["reason"] == "outside_cells"
    assert all(cell["text"] == "" for cell in result["cells"])
    assert all(cell["text_status"] == "not_detected" for cell in result["cells"])


def test_rejects_coordinate_mismatch():
    ocr = _ocr()
    ocr["coordinates"] = "scaled_image_pixels"
    with pytest.raises(ValueError, match="original_image_pixels"):
        add_ocr_to_table_candidate(_candidate(), ocr)


def test_rejects_dimension_mismatch():
    ocr = _ocr()
    ocr["image_width"] = 50
    with pytest.raises(ValueError, match="dimensions differ"):
        add_ocr_to_table_candidate(_candidate(), ocr)
