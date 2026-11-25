"""Standalone local OCR for image files, with padded OCR by default."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .pdf_table_ocr import _run_ocr

DEFAULT_MAX_SIDE_PX = 1024
DEFAULT_PADDING_PX = 60
MAX_PADDING_PX = 256
MAX_IMAGE_BYTES = 25 * 1024 * 1024
SUPPORTED_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def _model_directory(model_dir: Path | None = None) -> Path:
    if model_dir is not None:
        resolved = Path(model_dir)
    elif configured := os.environ.get("GOVPARSE_OCR_MODEL_DIR"):
        resolved = Path(configured)
    else:
        from .config import DATA

        root = Path(os.environ.get("GOVPARSE_DATA", str(DATA)))
        resolved = root / "models" / "rapidocr"
    if not resolved.is_dir():
        raise FileNotFoundError(f"RapidOCR 모델 디렉토리가 없습니다: {resolved}")
    return resolved


def _translate_boxes(boxes: list[Any], padding_px: int, width: int, height: int,
                     scaled_width: int, scaled_height: int) -> list[Any]:
    if not padding_px and (width, height) == (scaled_width, scaled_height):
        return boxes
    for polygon in boxes:
        if not isinstance(polygon, list):
            continue
        for point in polygon:
            if not isinstance(point, list) or len(point) < 2:
                continue
            try:
                point[0] = min(max((float(point[0]) - padding_px) * width / scaled_width, 0.0), float(width))
                point[1] = min(max((float(point[1]) - padding_px) * height / scaled_height, 0.0), float(height))
            except (TypeError, ValueError):
                continue
    return boxes


def recognize_image(
    image_path: Path | str,
    *,
    raw: bool = False,
    padding_px: int = DEFAULT_PADDING_PX,
    max_side_px: int | None = DEFAULT_MAX_SIDE_PX,
    model_dir: Path | None = None,
) -> dict[str, Any]:
    """Read a local image with RapidOCR and return boxes in original-image pixels."""
    path = Path(image_path).expanduser().resolve()
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"지원하지 않는 이미지 형식입니다: {path.suffix}")
    if not path.is_file():
        raise FileNotFoundError(f"이미지 파일이 없습니다: {path}")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"이미지는 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 이하여야 합니다")
    if type(raw) is not bool:
        raise ValueError("raw는 true 또는 false여야 합니다")
    if type(padding_px) is not int or not 0 <= padding_px <= MAX_PADDING_PX:
        raise ValueError(f"padding_px는 0~{MAX_PADDING_PX} 정수여야 합니다")

    if max_side_px is not None and (type(max_side_px) is not int or max_side_px < 1):
        raise ValueError('max_side_px는 양의 정수 또는 null이어야 합니다')

    image_bytes = path.read_bytes()
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("이미지 파일을 디코딩하지 못했습니다")
    height, width = image.shape[:2]
    used_padding = 0 if raw else padding_px
    scaled_width, scaled_height = width, height
    if not raw and max_side_px is not None and max(width, height) > max_side_px:
        scale = max_side_px / max(width, height)
        scaled_width = max(1, round(width * scale))
        scaled_height = max(1, round(height * scale))
        image = cv2.resize(image, (scaled_width, scaled_height), interpolation=cv2.INTER_AREA)
    ocr_bytes = image_bytes
    if used_padding:
        image = cv2.copyMakeBorder(
            image,
            used_padding,
            used_padding,
            used_padding,
            used_padding,
            cv2.BORDER_CONSTANT,
            value=(255, 255, 255),
        )
    if used_padding or (width, height) != (scaled_width, scaled_height):
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("전처리한 이미지를 인코딩하지 못했습니다")
        ocr_bytes = encoded.tobytes()

    result = _run_ocr(ocr_bytes, _model_directory(model_dir))
    result["boxes"] = _translate_boxes(result["boxes"], used_padding, width, height, scaled_width, scaled_height)
    return {
        "image_path": str(path),
        "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "image_width": width,
        "image_height": height,
        "scaled_width": scaled_width,
        "scaled_height": scaled_height,
        "coordinates": "original_image_pixels",
        "padding_px": used_padding,
        **result,
    }


def recognize_image_json(
    image_path: Path | str,
    *,
    raw: bool = False,
    padding_px: int = DEFAULT_PADDING_PX,
    max_side_px: int | None = DEFAULT_MAX_SIDE_PX,
    model_dir: Path | None = None,
) -> str:
    return json.dumps(
        recognize_image(image_path, raw=raw, padding_px=padding_px, max_side_px=max_side_px, model_dir=model_dir),
        ensure_ascii=False,
    )
