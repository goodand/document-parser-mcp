"""Verify original HWP picture pixels in their projected PDF positions inside a saved crop."""

import base64
import hashlib
from collections import Counter
from io import BytesIO

import numpy as np
import pymupdf
from PIL import Image


def _rgb(data):
    with Image.open(BytesIO(data)) as image:
        return image.convert("RGB")


def _fingerprint(image):
    return (image.size, hashlib.sha256(image.tobytes()).digest())


def _thumbnail_pixels(image):
    return np.asarray(image.resize((32, 32), Image.Resampling.BOX), dtype=float)


def verify(pdf, page_number, bbox, candidate, ir, placements, *, dpi=190, padding=6):
    """Verify native picture pixels at their projected positions in a legacy crop.

    The page number is one-based. The bounding box and padding are measured in
    PDF points, and dpi must match the resolution used to render the crop.
    """
    if page_number < 1 or page_number > len(pdf) or dpi <= 0 or padding < 0:
        return False, []
    page = pdf[page_number - 1]
    matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
    area = (pymupdf.Rect(bbox) + (-padding, -padding, padding, padding)) & page.rect
    crop_origin = (area * matrix).irect
    expected = Counter(p["asset_id"] for p in placements)
    candidates = {}
    for key in expected:
        asset = (ir.get("picture_assets") or {}).get(key)
        if not asset or not asset.get("bytes"):
            return False, []
        try:
            original = _rgb(base64.b64decode(asset["bytes"], validate=True))
        except (ValueError, OSError, TypeError):
            return False, []
        candidates[_fingerprint(original)] = (key, original)
    found = []
    with Image.open(candidate) as png:
        if png.size != (crop_origin.width, crop_origin.height):
            return False, []
        for obj in page.get_image_info(xrefs=True):
            box = pymupdf.Rect(obj["bbox"])
            if not area.contains(box) or not obj["xref"]:
                continue
            pic = _rgb(pdf.extract_image(obj["xref"])["image"])
            item = candidates.get(_fingerprint(pic))
            if not item:
                continue
            key, source = item
            rect = (box * matrix).irect
            local = (
                rect.x0 - crop_origin.x0,
                rect.y0 - crop_origin.y0,
                rect.x1 - crop_origin.x0,
                rect.y1 - crop_origin.y0,
            )
            tile = png.crop(local).convert("RGB")

            error = float(np.abs(_thumbnail_pixels(source) - _thumbnail_pixels(tile)).mean())
            if error <= 12:
                found.append(dict(asset=key, mean_pixel_error=round(error, 2), box=list(local)))
    actual = Counter(p["asset"] for p in found)
    return bool(expected) and all(actual[k] >= n for k, n in expected.items()), found
