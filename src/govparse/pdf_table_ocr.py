"""OCR candidate extraction for a table region in a PDF.

OCR output is evidence for review only. This module never edits parsed source
JSON, normalizes recognized text, or approves OCR output for retrieval.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import uuid
from pathlib import Path
from typing import Any

import pymupdf

from .normalize import sha256_of


def _run_ocr(image_bytes: bytes, model_dir: Path) -> dict[str, Any]:
    """Run optional local RapidOCR. Kept separate for deterministic unit tests."""
    import cv2
    import numpy as np
    from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

    params = {
        "Global.model_root_dir": str(model_dir),
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Det.lang_type": LangDet.MULTI,
        "Det.model_type": ModelType.MOBILE,
        "Det.ocr_version": OCRVersion.PPOCRV5,
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.lang_type": LangRec.KOREAN,
        "Rec.model_type": ModelType.MOBILE,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Cls.engine_type": EngineType.ONNXRUNTIME,
        "Cls.lang_type": LangDet.MULTI,
        "Cls.model_type": ModelType.MOBILE,
        "Cls.ocr_version": OCRVersion.PPOCRV5,
    }
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("PDF 표 크롭을 이미지로 디코딩하지 못했습니다")
    result = RapidOCR(params=params)(image)
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    boxes = [] if boxes is None else boxes.tolist() if hasattr(boxes, "tolist") else list(boxes)
    texts = [] if texts is None else texts.tolist() if hasattr(texts, "tolist") else list(texts)
    scores = [] if scores is None else scores.tolist() if hasattr(scores, "tolist") else list(scores)
    model_hashes = {}
    for model_file in sorted(model_dir.rglob("*.onnx")):
        model_hashes[model_file.name] = sha256_of(model_file)
    return {
        "boxes": boxes,
        "texts": texts,
        "scores": scores,
        "runtime": {
            "engine": "RapidOCR",
            "rapidocr_version": importlib.metadata.version("rapidocr"),
            "onnxruntime_version": importlib.metadata.version("onnxruntime"),
            "model_root_dir": str(model_dir.resolve()),
            "detector": "PP-OCRv5 mobile multilingual",
            "recognizer": "PP-OCRv5 mobile Korean",
            "inference_provider": "ONNX Runtime",
            "model_sha256": model_hashes,
        },
    }


def _finite_polygon(raw: Any) -> list[list[float]] | None:
    try:
        polygon = [[float(point[0]), float(point[1])] for point in raw]
    except (TypeError, ValueError, IndexError):
        return None
    if len(polygon) < 3 or any(not math.isfinite(v) for p in polygon for v in p):
        return None
    return polygon


def _jsonable(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    return list(value)


def _reading_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group near-equal baseline centers, then order each OCR line left to right."""
    if len(items) < 2:
        return list(items)
    heights = [
        item["bbox_page_top_left"][3] - item["bbox_page_top_left"][1]
        for item in items
    ]
    line_tolerance = max(2.0, sorted(heights)[len(heights) // 2] * 0.55)
    lines: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda entry: entry["center_page_top_left"][1]):
        center_y = item["center_page_top_left"][1]
        line = next(
            (candidate for candidate in lines
             if abs(center_y - candidate["center_y"]) <= line_tolerance),
            None,
        )
        if line is None:
            lines.append({"center_y": center_y, "items": [item]})
        else:
            line["items"].append(item)
            line["center_y"] = sum(
                entry["center_page_top_left"][1] for entry in line["items"]
            ) / len(line["items"])
    lines.sort(key=lambda line: line["center_y"])
    return [
        item
        for line in lines
        for item in sorted(line["items"], key=lambda entry: entry["center_page_top_left"][0])
    ]


def _write_atomic(path: Path, payload: bytes) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes(payload)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def recover_pdf_table_text(
    pdf_path: Path,
    page: int,
    table_index: int = 0,
    dpi: int = 190,
    model_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Return an unapproved OCR candidate for one detected PDF table.

    Args:
        pdf_path: PDF to inspect.
        page: 1-based PDF file page.
        table_index: zero-based PyMuPDF ``find_tables(snap_tolerance=1)`` index.
        dpi: render resolution, from 72 to 600.
        model_dir: RapidOCR model directory. Defaults to GOVPARSE_OCR_MODEL_DIR,
            then ``GOVPARSE_DATA/models/rapidocr`` (or configured DATA root).
        output_dir: optional directory for the immutable crop and result JSON.

    Coordinates are PDF page top-left points, matching PyMuPDF table geometry.
    OCR pixel coordinates are retained alongside mapped page coordinates.
    Nothing is written back to the parsed document or approved for RAG.
    """
    pdf_path = Path(pdf_path)
    if type(page) is not int or page < 1:
        raise ValueError("page는 1 이상의 정수여야 합니다")
    if type(table_index) is not int or table_index < 0:
        raise ValueError("table_index는 0 이상의 정수여야 합니다")
    if type(dpi) is not int or not 72 <= dpi <= 600:
        raise ValueError("dpi는 72~600 정수여야 합니다")
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF가 없습니다: {pdf_path}")

    if model_dir is None:
        configured = os.environ.get("GOVPARSE_OCR_MODEL_DIR")
        if configured:
            model_dir = Path(configured)
        else:
            from .config import DATA

            root = Path(os.environ.get("GOVPARSE_DATA", str(DATA)))
            model_dir = root / "models" / "rapidocr"
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise FileNotFoundError(f"RapidOCR 모델 디렉토리가 없습니다: {model_dir}")

    source_hash = sha256_of(pdf_path)
    warnings: list[dict[str, Any]] = []
    with pymupdf.open(str(pdf_path)) as pdf:
        if page > pdf.page_count:
            raise ValueError(f"page {page} exceeds PDF page count {pdf.page_count}")
        pdf_page = pdf[page - 1]
        tables = pdf_page.find_tables(snap_tolerance=1).tables
        if table_index >= len(tables):
            raise ValueError(
                f"table_index {table_index} exceeds detected table count {len(tables)} "
                f"on page {page}"
            )
        table = tables[table_index]
        table_bbox = [float(v) for v in table.bbox]
        clip = pymupdf.Rect(*table_bbox) & pdf_page.rect
        if clip.is_empty or clip.width <= 0 or clip.height <= 0:
            raise ValueError("검출된 표 영역이 PDF 페이지 밖에 있습니다")
        pix = pdf_page.get_pixmap(clip=clip, dpi=dpi, alpha=False)
        image_bytes = pix.tobytes("png")
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        scale = dpi / 72.0
        page_rotation = int(pdf_page.rotation)

        cells: list[dict[str, Any]] = []
        for row_index, row in enumerate(table.rows):
            for col_index, cell_bbox in enumerate(row.cells):
                if cell_bbox is None:
                    continue
                box = [float(v) for v in cell_bbox]
                cells.append({
                    "row": row_index,
                    "col": col_index,
                    "bbox_page_top_left": box,
                    "detections": [],
                    "joined_raw_text": "",
                })

    # The model is imported only when this explicit recovery route is requested.
    raw = _run_ocr(image_bytes, model_dir)
    boxes = _as_list(raw.get("boxes"))
    texts = _as_list(raw.get("texts"))
    scores = _as_list(raw.get("scores"))
    if not (len(boxes) == len(texts) == len(scores)):
        raise ValueError("OCR 결과의 boxes/texts/scores 길이가 서로 다릅니다")

    detections: list[dict[str, Any]] = []
    for index, (raw_box, raw_text, raw_score) in enumerate(zip(boxes, texts, scores, strict=True)):
        polygon = _finite_polygon(raw_box)
        try:
            score = float(raw_score)
            if not math.isfinite(score):
                raise ValueError
        except (TypeError, ValueError):
            score = None
        if polygon is None or score is None:
            warnings.append({
                "code": "invalid_ocr_detection",
                "detection_index": index,
                "polygon_valid": polygon is not None,
                "confidence_valid": score is not None,
            })
            detections.append({
                "detection_index": index,
                "raw_text": _jsonable(raw_text),
                "confidence": _jsonable(raw_score),
                "raw_polygon": _jsonable(raw_box),
                "bbox_pixel": None,
                "bbox_page_top_left": None,
                "center_page_top_left": None,
                "cell": None,
            })
            continue
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        center_px = [sum(xs) / len(xs), sum(ys) / len(ys)]
        center_page = [(center_px[0] + pix.x) / scale, (center_px[1] + pix.y) / scale]
        bbox_px = [min(xs), min(ys), max(xs), max(ys)]
        page_bbox = [
            (bbox_px[0] + pix.x) / scale,
            (bbox_px[1] + pix.y) / scale,
            (bbox_px[2] + pix.x) / scale,
            (bbox_px[3] + pix.y) / scale,
        ]
        matches = [
            cell for cell in cells
            if cell["bbox_page_top_left"][0] <= center_page[0] <= cell["bbox_page_top_left"][2]
            and cell["bbox_page_top_left"][1] <= center_page[1] <= cell["bbox_page_top_left"][3]
        ]
        cell_ref = {"row": matches[0]["row"], "col": matches[0]["col"]} if len(matches) == 1 else None
        detection = {
            "detection_index": index,
            "raw_text": _jsonable(raw_text),
            "confidence": score,
            "bbox_pixel": bbox_px,
            "polygon_pixel": polygon,
            "bbox_page_top_left": page_bbox,
            "center_page_top_left": center_page,
            "cell": cell_ref,
        }
        detections.append(detection)
        if cell_ref is None:
            warnings.append({
                "code": "detection_not_uniquely_assigned_to_cell",
                "detection_index": index,
                "candidate_cell_count": len(matches),
            })
        else:
            matches[0]["detections"].append(detection)

    for cell in cells:
        cell["detections"] = _reading_order(cell["detections"])
        # Preserve OCR strings verbatim; line grouping/spacing remains reviewable.
        cell["joined_raw_text"] = " ".join(item["raw_text"] for item in cell["detections"])

    result: dict[str, Any] = {
        "status": "candidate",
        "source_pdf": str(pdf_path.resolve()),
        "source_pdf_sha256": source_hash,
        "page": page,
        "page_rotation": page_rotation,
        "dpi": dpi,
        "table_index": table_index,
        "table_bbox_page_top_left": table_bbox,
        "coordinate_system": "PDF page top-left points; pixel origin from PyMuPDF pixmap",
        "cell_assignment_method": "OCR polygon center must be contained in exactly one non-null cell",
        "crop": {
            "bbox_page_top_left": [float(v) for v in clip],
            "pixel_origin": [int(pix.x), int(pix.y)],
            "pixel_size_px": [int(pix.width), int(pix.height)],
            "scale_px_per_point": scale,
            "image_sha256": image_hash,
        },
        "cells": cells,
        "detections": detections,
        "warnings": warnings,
        "runtime": raw.get("runtime", {"engine": "injected/test"}),
        "target_match": "unverified",
        "held_for_review": True,
        "embedding_approved": False,
        "production_applied": False,
    }
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = f"{source_hash[:16]}_p{page:05d}_t{table_index}_{uuid.uuid4().hex[:12]}"
        image_name = f"{artifact_id}.png"
        json_name = f"{artifact_id}.json"
        _write_atomic(output_dir / image_name, image_bytes)
        result["crop"]["image_path"] = str((output_dir / image_name).resolve())
        result["artifact_json"] = str((output_dir / json_name).resolve())
        # The sidecar records the crop path before it records its own path.
        _write_atomic(
            output_dir / json_name,
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
        )
    return result
