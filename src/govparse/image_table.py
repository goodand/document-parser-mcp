"""Visual table-grid extraction with RapidTable SLANETPLUS.

The model predicts geometry only. It does not perform OCR and every result
requires review because exact topology is not sufficiently reliable yet.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import math
import os
from pathlib import Path
from typing import Any

MODEL_NAME = "RapidTable SLANETPLUS"
MODEL_SHA256 = "d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b"


def _resolve_model(model_path: str | Path | None) -> Path:
    configured = model_path if model_path is not None else os.environ.get("GOVPARSE_TABLE_MODEL_PATH")
    if configured is not None:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"SLANetPlus model file not found: {path}")
        return path.resolve()
    package_root = Path(__file__).resolve().parents[2]
    candidates = (
        package_root / "models" / "table-structure" / "slanet-plus.onnx",
        package_root / "models" / "slanet-plus.onnx",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    looked = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "SLANetPlus model file not found. Pass model_path or set "
        f"GOVPARSE_TABLE_MODEL_PATH. Checked: {looked}"
    )


def _inclusive_span(start: int, end: int) -> tuple[int, int]:
    if start < 0 or end < start:
        raise ValueError(f"invalid SLANetPlus cell span: {start}..{end}")
    return start, end - start + 1


def _cell_bbox(value: Any, width: int, height: int) -> list[int] | None:
    try:
        points = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if len(points) not in (4, 8) or not all(math.isfinite(v) for v in points):
        return None
    xs, ys = points[0::2], points[1::2]
    x1 = max(0, min(width, int(round(min(xs)))))
    x2 = max(0, min(width, int(round(max(xs)))))
    y1 = max(0, min(height, int(round(min(ys)))))
    y2 = max(0, min(height, int(round(max(ys)))))
    if x1 == x2 or y1 == y2:
        return None
    return [x1, y1, x2, y2]


def extract_table_from_image(
    image_path: str | Path,
    *,
    model_path: str | Path | None = None,
    include_ocr: bool = False,
    ocr_model_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Predict one PNG/JPEG grid and return JSON-ready cell geometry.

    OCR is opt-in. Cell geometry is always marked for review and OCR text is
    never represented as verified.
    """
    from PIL import Image
    from rapid_table import ModelType, RapidTable, RapidTableInput

    if type(include_ocr) is not bool:
        raise TypeError("include_ocr must be a boolean")
    path = Path(image_path).expanduser().resolve(strict=True)
    model = _resolve_model(model_path)
    model_sha = hashlib.sha256(model.read_bytes()).hexdigest()
    if model_sha != MODEL_SHA256:
        raise ValueError(f"unexpected SLANetPlus model SHA-256: {model_sha}")
    with Image.open(path) as image:
        width, height = image.size
        image.load()

    engine = RapidTable(RapidTableInput(
        model_type=ModelType.SLANETPLUS,
        model_dir_or_path=str(model),
        use_ocr=False,
    ))
    output = engine(str(path))
    points = output.logic_points[0] if output.logic_points else []
    boxes = output.cell_bboxes[0] if output.cell_bboxes else []
    cells = []
    for index, point in enumerate(points):
        if len(point) != 4:
            continue
        row, row_span = _inclusive_span(int(point[0]), int(point[1]))
        col, col_span = _inclusive_span(int(point[2]), int(point[3]))
        bbox = _cell_bbox(boxes[index], width, height) if index < len(boxes) else None
        cells.append({
            "row": row,
            "col": col,
            "row_span": row_span,
            "col_span": col_span,
            "text": "",
            "text_check": "not_checked",
            "bbox_px": bbox,
        })

    rows = max((cell["row"] + cell["row_span"] for cell in cells), default=0)
    cols = max((cell["col"] + cell["col_span"] for cell in cells), default=0)
    warnings = [] if include_ocr else ["Cell text was not extracted. Run OCR or enter text manually."]
    if cells and any(cell["bbox_px"] is None for cell in cells):
        warnings.append("Some detected cells have no usable pixel bounds.")
    if not cells:
        warnings.append("No table grid was detected.")
    warnings.append("Review predicted rows, columns, and merged cells against the image.")
    candidate = {
        "schema_version": "1.0",
        "source_kind": "image",
        "status": "review",
        "structure_check": "review",
        "image": {
            "path": str(path),
            "width_px": width,
            "height_px": height,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        "model": {
            "name": MODEL_NAME,
            "version": importlib.metadata.version("rapid-table"),
            "sha256": model_sha,
        },
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "warnings": warnings,
    }
    if include_ocr:
        from .image_ocr import recognize_image
        from .image_table_ocr import add_ocr_to_table_candidate

        ocr_result = recognize_image(path, model_dir=Path(ocr_model_dir) if ocr_model_dir else None)
        candidate = add_ocr_to_table_candidate(candidate, ocr_result)
    return candidate
