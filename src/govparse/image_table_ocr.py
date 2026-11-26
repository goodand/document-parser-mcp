"""Map OCR word boxes onto a predicted image-table grid for human review."""

from __future__ import annotations

import copy
import math
from typing import Any


def _polygon_box(raw: Any) -> tuple[float, float, float, float] | None:
    try:
        points = [(float(point[0]), float(point[1])) for point in raw]
    except (TypeError, ValueError, IndexError):
        return None
    if len(points) < 3 or not all(math.isfinite(v) for point in points for v in point):
        return None
    xs, ys = zip(*points, strict=True)
    left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _intersection_area(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    return max(0.0, right - left) * max(0.0, bottom - top)


def add_ocr_to_table_candidate(
    candidate: dict[str, Any],
    ocr_result: dict[str, Any],
) -> dict[str, Any]:
    """Return a copy with OCR words assigned to cells by maximum bbox overlap.

    OCR boxes and table cell boxes must use original-image pixels. Cells without
    a predicted bbox and words that do not overlap a cell remain unassigned.
    OCR output stays explicitly unverified. A cell with no recognized word is
    marked not_detected rather than treated as a confirmed blank.
    """
    if not isinstance(candidate, dict) or not isinstance(ocr_result, dict):
        raise TypeError("candidate and ocr_result must be objects")
    if candidate.get("source_kind") != "image":
        raise ValueError("OCR assignment requires an image table candidate")
    image = candidate.get("image")
    if not isinstance(image, dict):
        raise ValueError("candidate image metadata is required")
    if ocr_result.get("coordinates") != "original_image_pixels":
        raise ValueError("OCR boxes must use original_image_pixels")
    if (ocr_result.get("image_width"), ocr_result.get("image_height")) != (
        image.get("width_px"), image.get("height_px")
    ):
        raise ValueError("OCR and table candidate image dimensions differ")

    result = copy.deepcopy(candidate)
    cells = result.get("cells")
    if not isinstance(cells, list):
        raise ValueError("candidate cells must be a list")
    cell_boxes = []
    for cell in cells:
        raw = cell.get("bbox_px") if isinstance(cell, dict) else None
        box = _polygon_box([[raw[0], raw[1]], [raw[2], raw[1]],
                            [raw[2], raw[3]], [raw[0], raw[3]]]) if (
            isinstance(raw, (list, tuple)) and len(raw) == 4
        ) else None
        cell_boxes.append(box)

    assigned: list[list[dict[str, Any]]] = [[] for _ in cells]
    unassigned = []
    boxes = ocr_result.get("boxes", [])
    texts = ocr_result.get("texts", [])
    scores = ocr_result.get("scores", [])
    if not isinstance(boxes, list) or not isinstance(texts, list) or not isinstance(scores, list):
        raise ValueError("OCR boxes, texts, and scores must be arrays")

    for index, raw_box in enumerate(boxes):
        text = texts[index] if index < len(texts) else ""
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            continue
        word_box = _polygon_box(raw_box)
        score = scores[index] if index < len(scores) else None
        try:
            score = float(score)
            if not math.isfinite(score):
                score = None
        except (TypeError, ValueError):
            score = None
        if word_box is None:
            unassigned.append({"text": text, "score": score, "reason": "invalid_box"})
            continue

        overlaps = [
            (_intersection_area(word_box, cell_box), cell_index)
            for cell_index, cell_box in enumerate(cell_boxes)
            if cell_box is not None
        ]
        if not overlaps:
            unassigned.append({"text": text, "score": score, "reason": "no_cell_boxes"})
            continue
        area, cell_index = max(overlaps)
        if area <= 0:
            unassigned.append({"text": text, "score": score, "reason": "outside_cells"})
            continue
        assigned[cell_index].append({
            "text": text,
            "score": score,
            "bbox_px": [round(value, 2) for value in word_box],
        })

    for cell, words in zip(cells, assigned, strict=True):
        words.sort(key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))
        cell["ocr_words"] = words
        cell["ocr_text"] = " ".join(item["text"] for item in words)
        cell["text"] = cell["ocr_text"]
        cell["text_status"] = "review" if words else "not_detected"
        cell["text_check"] = cell["text_status"]

    result["ocr"] = {
        "engine": ocr_result.get("runtime", {}).get("engine", "unknown"),
        "coordinates": "original_image_pixels",
        "unassigned_words": unassigned,
        "status": "review",
    }
    result.setdefault("warnings", []).append(
        "OCR text and cell assignment require human review; empty cells are not confirmed blank."
    )
    return result
