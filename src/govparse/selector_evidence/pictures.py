"""Score whether HWP-embedded picture assets appear in rendered table PNGs.

The scorer is source-agnostic: it decodes ``picture_assets`` emitted by
``govparse.hwp_source.extract_source`` and searches candidate table PNGs for
scaled matches using normalized RGB, luminance, and edge features.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from ..table_images import iter_table_nodes

_ASSET_URI = re.compile(r"bin://(\d+)$")
_PATCH_SIZE = (24, 24)


def _picture_blocks(table: dict[str, Any], table_id: str, include_descendants: bool):
    """Yield image placements with their owning table, not repeated IDs alone."""
    for cell in table.get("cells", []):
        for block_index, block in enumerate(cell.get("blocks", [])):
            kind = block.get("kind")
            if kind == "picture":
                yield table_id, cell, block_index, block
            elif kind == "table" and include_descendants:
                nested_id = f"{table_id}/c{cell.get('row', 0)}_{cell.get('col', 0)}/t{
                    sum(
                        1
                        for prior in cell.get('blocks', [])[:block_index]
                        if prior.get('kind') == 'table'
                    )
                }"
                yield from _picture_blocks(block, nested_id, include_descendants)


def _open_rgb(value: bytes | bytearray | memoryview | str | Path | Image.Image) -> Image.Image:
    if isinstance(value, Image.Image):
        image = value.copy()
    elif isinstance(value, (str, Path)):
        image = Image.open(value)
    else:
        image = Image.open(io.BytesIO(bytes(value)))
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        image = Image.alpha_composite(background, rgba)
    return image.convert("RGB")


def _correlation(left: np.ndarray, right: np.ndarray) -> float:
    a = left.astype(np.float32, copy=False).ravel()
    b = right.astype(np.float32, copy=False).ravel()
    a = a - float(a.mean())
    b = b - float(b.mean())
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator < 1e-6:
        return 1.0 if float(np.mean(np.abs(left - right))) < 0.01 else 0.0
    return max(-1.0, min(1.0, float(np.dot(a, b) / denominator)))


def _features(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    rgb = np.asarray(image.resize(_PATCH_SIZE, Image.Resampling.LANCZOS), dtype=np.float32) / 255.0
    gray = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    gx = np.diff(gray, axis=1, append=gray[:, -1:])
    gy = np.diff(gray, axis=0, append=gray[-1:, :])
    edges = np.stack((gx, gy), axis=2)
    return rgb, edges


def _feature_score(source: Image.Image, patch: Image.Image) -> dict[str, float]:
    source_rgb, source_edges = _features(source)
    patch_rgb, patch_edges = _features(patch)
    source_gray = (
        source_rgb[:, :, 0] * 0.299 + source_rgb[:, :, 1] * 0.587 + source_rgb[:, :, 2] * 0.114
    )
    patch_gray = (
        patch_rgb[:, :, 0] * 0.299 + patch_rgb[:, :, 1] * 0.587 + patch_rgb[:, :, 2] * 0.114
    )
    gray_corr = _correlation(source_gray, patch_gray)
    edge_corr = _correlation(source_edges, patch_edges)
    color_similarity = max(0.0, 1.0 - float(np.mean(np.abs(source_rgb - patch_rgb))))
    # Correlations are remapped to [0,1]. The color term distinguishes otherwise
    # similar monochrome placeholders while edges help with resized/antialiased art.
    score = (
        0.50 * ((gray_corr + 1.0) / 2.0)
        + 0.30 * color_similarity
        + 0.20 * ((edge_corr + 1.0) / 2.0)
    )
    return {
        "score": float(max(0.0, min(1.0, score))),
        "luminance_correlation": float(gray_corr),
        "edge_correlation": float(edge_corr),
        "color_similarity": float(color_similarity),
    }


def score_picture_match(
    source_image: bytes | bytearray | memoryview | str | Path | Image.Image,
    candidate_png: bytes | bytearray | memoryview | str | Path | Image.Image,
    *,
    threshold: float = 0.82,
    max_candidate_dimension: int = 600,
    target_aspect: float | None = None,
) -> dict[str, Any]:
    """Search candidate table PNG for a scaled embedded image.

    The returned score is evidence, not a binary guarantee. ``likely_present``
    uses the caller's threshold and each result includes the candidate bbox.
    """
    source = _open_rgb(source_image)
    candidate_full = _open_rgb(candidate_png)
    original_size = candidate_full.size
    scale = min(1.0, max_candidate_dimension / max(original_size))
    candidate = (
        candidate_full.resize(
            (max(1, round(original_size[0] * scale)), max(1, round(original_size[1] * scale))),
            Image.Resampling.LANCZOS,
        )
        if scale < 1.0
        else candidate_full
    )
    width, height = candidate.size
    asset_aspect = source.width / max(1, source.height)
    aspect = target_aspect if target_aspect and target_aspect > 0 else asset_aspect
    source_variation = float(np.asarray(source, dtype=np.float32).std())
    if source_variation < 4.0:
        return {
            "score": 0.0,
            "likely_present": False,
            "bbox": None,
            "reason": "low_information_source_image",
            "source_size": list(source.size),
            "candidate_size": list(original_size),
            "source_pixel_stddev": source_variation,
        }
    max_patch_width = min(width, int(height * aspect))
    max_patch_height = min(height, int(width / aspect))
    if max_patch_width < 18 or max_patch_height < 18:
        return {
            "score": 0.0,
            "likely_present": False,
            "bbox": None,
            "reason": "candidate_too_small_for_source_aspect",
            "source_size": list(source.size),
            "candidate_size": list(original_size),
        }

    try:
        import cv2
    except ImportError:
        cv2 = None
    if cv2 is None:
        return _score_picture_match_numpy(
            source, candidate, original_size, scale, threshold, aspect
        )

    candidate_bgr = cv2.cvtColor(np.asarray(candidate, dtype=np.uint8), cv2.COLOR_RGB2BGR)
    source_bgr = cv2.cvtColor(np.asarray(source, dtype=np.uint8), cv2.COLOR_RGB2BGR)
    best: dict[str, Any] | None = None
    tested = 0
    aspect_multipliers = (0.96, 1.0, 1.04)
    for aspect_multiplier in aspect_multipliers:
        target_aspect = aspect * aspect_multiplier
        max_width = min(width, int(height * target_aspect))
        min_width = min(max_width, max(20, round(max_width * 0.07)))
        if max_width < 18:
            continue
        widths = sorted(
            {
                max(18, min(max_width, int(round(v))))
                for v in np.geomspace(min_width, max_width, num=40)
            }
        )
        for patch_width in widths:
            patch_height = max(18, round(patch_width / target_aspect))
            if patch_height > height or patch_width > width:
                continue
            template_bgr = cv2.resize(
                source_bgr,
                (patch_width, patch_height),
                interpolation=cv2.INTER_AREA if patch_width < source.width else cv2.INTER_CUBIC,
            )
            template_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
            if float(template_gray.std()) < 2.0:
                continue
            rgb_map = cv2.matchTemplate(candidate_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
            _, rgb_correlation, _, location = cv2.minMaxLoc(rgb_map)
            tested += 1
            x, y = location
            patch = candidate_bgr[y : y + patch_height, x : x + patch_width]
            resized_patch = cv2.resize(patch, _PATCH_SIZE, interpolation=cv2.INTER_AREA)
            resized_template = cv2.resize(template_bgr, _PATCH_SIZE, interpolation=cv2.INTER_AREA)
            patch_rgb = cv2.cvtColor(resized_patch, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            template_rgb = (
                cv2.cvtColor(resized_template, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            )
            color_similarity = max(0.0, 1.0 - float(np.mean(np.abs(patch_rgb - template_rgb))))
            patch_gray = cv2.cvtColor(resized_patch, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
            template_gray_small = (
                cv2.cvtColor(resized_template, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
            )
            luminance_correlation = _correlation(template_gray_small, patch_gray)
            edge_correlation = _correlation(
                cv2.Canny((template_gray_small * 255).astype(np.uint8), 45, 130),
                cv2.Canny((patch_gray * 255).astype(np.uint8), 45, 130),
            )
            score = (
                0.70 * float(rgb_correlation)
                + 0.20 * max(0.0, luminance_correlation)
                + 0.10 * max(0.0, edge_correlation)
            )
            if best is None or score > best["score"]:
                bbox = (
                    round(x / scale),
                    round(y / scale),
                    round((x + patch_width) / scale),
                    round((y + patch_height) / scale),
                )
                best = {
                    "score": float(max(0.0, min(1.0, score))),
                    "rgb_correlation": float(rgb_correlation),
                    "luminance_correlation": float(luminance_correlation),
                    "edge_correlation": float(edge_correlation),
                    "color_similarity": color_similarity,
                    "bbox": list(bbox),
                    "patch_size": [patch_width, patch_height],
                }
    if best is None:
        return {
            "score": 0.0,
            "likely_present": False,
            "bbox": None,
            "reason": "no_candidate_region",
            "source_size": list(source.size),
            "candidate_size": list(original_size),
        }
    return {
        **best,
        "likely_present": best["score"] >= threshold,
        "threshold": threshold,
        "backend": "opencv_multiscale_template_match",
        "source_size": list(source.size),
        "candidate_size": list(original_size),
        "candidate_search_size": list(candidate.size),
        "regions_tested": tested,
    }


def _window_sums(integral, height, width):
    """Sum every valid window using a top- and left-padded integral image."""
    return (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )


def _score_picture_match_numpy(source, candidate, original_size, scale, threshold, aspect):
    """Locate templates by normalized grayscale correlation, then check RGB and edges."""
    # Bound the FFT size independently of the input PNG's render resolution.
    search = candidate.copy()
    search.thumbnail((300, 300), Image.Resampling.LANCZOS)
    width, height = search.size
    gray = np.asarray(search.convert("L"), dtype=np.float64)
    spectrum = np.fft.rfft2(gray)
    summed = np.pad(gray.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    squared = np.pad((gray * gray).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    max_width = min(width, int(height * aspect))
    if max_width < 1:
        return {
            "score": 0.0,
            "likely_present": False,
            "bbox": None,
            "reason": "no_candidate_region",
        }
    min_width = min(max_width, max(9, round(max_width * 0.06)))
    # Thin borders lose correlation when opposite edges miss by several pixels.
    # At this bounded search resolution, stepping by two pixels covers both edges.
    widths = sorted({*range(min_width, max_width + 1, 2), max_width})
    source_gray = source.convert("L")
    best = None
    tested = 0
    for w in widths:
        h = max(1, round(w / aspect))
        if h > height:
            continue
        template = np.array(source_gray.resize((w, h), Image.Resampling.BOX), dtype=np.float64)
        template -= template.mean()
        norm = np.linalg.norm(template)
        if norm < 2 * np.sqrt(w * h):
            continue
        windows = _window_sums(summed, h, w)
        variance = _window_sums(squared, h, w) - windows * windows / (h * w)
        denominator = norm * np.sqrt(np.maximum(variance, 0))
        # Circular correlation equals linear correlation at all fully contained positions.
        template_fft = np.fft.rfft2(template, s=gray.shape, axes=(0, 1))
        raw = np.fft.irfft2(spectrum * template_fft.conjugate(), s=gray.shape, axes=(0, 1))
        valid = raw[: height - h + 1, : width - w + 1]
        corr = np.divide(valid, denominator, out=np.zeros_like(valid), where=denominator > 1e-6)
        y, x = np.unravel_index(np.argmax(corr), corr.shape)
        coarse = (x, y, x + w, y + h)
        ratios = (candidate.width / width, candidate.height / height) * 2
        box = tuple(round(v * ratio) for v, ratio in zip(coarse, ratios, strict=True))
        metrics = _feature_score(source, candidate.crop(box))
        correlation = float(np.clip(corr[y, x], 0, 1))
        score = 0.7 * correlation + 0.3 * metrics["score"]
        tested += 1
        if best is None or score > best["score"]:
            best = {
                **metrics,
                "score": score,
                "template_correlation": correlation,
                "bbox": [round(v / scale) for v in box],
                "patch_size": [w, h],
            }
    if best is None:
        return {
            "score": 0.0,
            "likely_present": False,
            "bbox": None,
            "reason": "no_candidate_region",
        }
    return {
        **best,
        "likely_present": best["score"] >= threshold,
        "threshold": threshold,
        "backend": "numpy_multiscale_template_match",
        "source_size": list(source.size),
        "candidate_size": list(original_size),
        "candidate_search_size": list(search.size),
        "regions_tested": tested,
    }


def _asset_bytes(ir: dict[str, Any], uri: str) -> tuple[str | None, bytes | None]:
    match = _ASSET_URI.fullmatch(uri or "")
    if not match:
        return None, None
    key = f"image{match.group(1)}"
    record = (ir.get("picture_assets") or {}).get(key)
    if not isinstance(record, dict) or not isinstance(record.get("bytes"), str):
        return key, None
    try:
        return key, base64.b64decode(record["bytes"], validate=True)
    except (ValueError, TypeError):
        return key, None


def _placement_aspect(picture: dict[str, Any], source_size: Sequence[int]) -> float | None:
    """Use native picture frame ratio when present, bounded by the asset ratio."""
    native = picture.get("native_picture")
    if not isinstance(native, str):
        return None
    try:
        root = ET.fromstring(native.encode("utf-8"))
        size = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "sz"), None)
        if size is None:
            return None
        width, height = float(size.get("width", 0)), float(size.get("height", 0))
        ratio = width / height if width > 0 and height > 0 else None
        source_ratio = source_size[0] / max(1, source_size[1])
        return ratio if ratio and 0.4 <= ratio / source_ratio <= 2.5 else None
    except (ET.ParseError, ValueError, ZeroDivisionError):
        return None


def _candidate_paths(record: dict[str, Any], image_dir: Path) -> list[Path]:
    paths = record.get("paths") or ([record["path"]] if record.get("path") else [])
    return [Path(path) if Path(path).is_absolute() else image_dir / path for path in paths]


def score_table_png_fidelity(
    ir: dict[str, Any],
    table_id: str,
    candidate_pngs: Sequence[bytes | str | Path | Image.Image] | bytes | str | Path | Image.Image,
    *,
    threshold: float = 0.82,
    include_descendants: bool = True,
) -> dict[str, Any]:
    """Score all native picture placements in one table against candidate PNGs.

    Nested table pictures are included by default so a parent table render can
    be checked for lost pictures anywhere in its descendant cells.
    """
    table_map = dict(iter_table_nodes(ir))
    table = table_map.get(table_id)
    if table is None:
        return {
            "table_id": table_id,
            "status": "unknown_table",
            "expected_asset_count": 0,
            "matched_asset_count": 0,
            "missing_source_asset_count": 0,
            "placements": [],
        }
    if isinstance(candidate_pngs, (bytes, bytearray, memoryview, str, Path, Image.Image)):
        candidates = [candidate_pngs]
    else:
        candidates = list(candidate_pngs)
    placements = []
    for owner_table_id, cell, index, picture in _picture_blocks(
        table, table_id, include_descendants
    ):
        uri = (picture.get("image") or {}).get("uri", "")
        asset_id, asset = _asset_bytes(ir, uri)
        placement = {
            "owner_table_id": owner_table_id,
            "cell": [cell.get("row"), cell.get("col")],
            "block_index": index,
            "asset_uri": uri,
            "asset_id": asset_id,
        }
        if not asset:
            placement.update({"status": "missing_source_asset", "score": None, "candidate": None})
            placements.append(placement)
            continue
        placement["source_asset_sha256"] = hashlib.sha256(asset).hexdigest()
        try:
            source_image = _open_rgb(asset)
            placement["source_size"] = list(source_image.size)
        except Exception as exc:
            placement.update(
                {
                    "status": "invalid_source_asset",
                    "reason": f"{type(exc).__name__}:{exc}",
                    "score": None,
                }
            )
            placements.append(placement)
            continue
        best = None
        target_aspect = _placement_aspect(picture, source_image.size)
        for candidate in candidates:
            try:
                match = score_picture_match(
                    source_image, candidate, threshold=threshold, target_aspect=target_aspect
                )
            except (OSError, ValueError):
                continue
            if best is None or match["score"] > best["score"]:
                path = str(candidate) if isinstance(candidate, (str, Path)) else None
                best = {**match, "candidate": path}
        if best is None:
            placement.update({"status": "no_candidate_png", "score": None, "candidate": None})
        else:
            placement.update(best)
            placement["status"] = "matched" if best["likely_present"] else "not_detected"
        placements.append(placement)
    expected = sum(
        p.get("status") not in {"missing_source_asset", "invalid_source_asset"} for p in placements
    )
    matched = sum(p.get("status") == "matched" for p in placements)
    missing_assets = sum(p.get("status") == "missing_source_asset" for p in placements)
    return {
        "table_id": table_id,
        "status": "scored" if candidates else "no_candidate_png",
        "include_descendants": include_descendants,
        "expected_asset_count": expected,
        "matched_asset_count": matched,
        "missing_source_asset_count": missing_assets,
        "placements": placements,
    }


def score_document_table_pngs(
    ir: dict[str, Any],
    table_manifest: dict[str, Any],
    image_dir: str | Path,
    *,
    threshold: float = 0.82,
    include_descendants: bool = True,
) -> dict[str, Any]:
    """Score every manifest table PNG against its native IR picture assets."""
    image_dir = Path(image_dir)
    records = table_manifest.get("tables", {})
    results = {}
    for table_id, record in records.items():
        paths = _candidate_paths(record, image_dir)
        available = [path for path in paths if path.is_file()]
        result = score_table_png_fidelity(
            ir,
            table_id,
            available,
            threshold=threshold,
            include_descendants=include_descendants,
        )
        result["candidate_png_count"] = len(available)
        result["render_status"] = record.get("status")
        results[table_id] = result
    expected = sum(result["expected_asset_count"] for result in results.values())
    matched = sum(result["matched_asset_count"] for result in results.values())
    return {
        "method": "multiscale_rgb_luminance_edge_template_match",
        "threshold": threshold,
        "include_descendants": include_descendants,
        "table_count": len(results),
        "tables_with_expected_assets": sum(r["expected_asset_count"] > 0 for r in results.values()),
        "expected_asset_count": expected,
        "matched_asset_count": matched,
        "not_detected_asset_count": sum(
            p.get("status") == "not_detected" for r in results.values() for p in r["placements"]
        ),
        "missing_source_asset_count": sum(
            r["missing_source_asset_count"] for r in results.values()
        ),
        "tables": results,
    }
