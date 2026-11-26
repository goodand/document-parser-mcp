"""Check a detected table crop against native cell extents and the rendered source PDF."""

from __future__ import annotations

import base64
import hashlib
import io
import math
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pymupdf
from PIL import Image, ImageChops

from .geometry import geometry_check


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def _bbox(*records):
    for record in records:
        if isinstance(record, dict):
            for key in ("bbox_top_left", "bbox"):
                value = record.get(key)
                if isinstance(value, (list, tuple)) and len(value) == 4:
                    try:
                        rect = pymupdf.Rect(*(float(x) for x in value))
                    except (TypeError, ValueError):
                        continue
                    if not rect.is_empty and all(math.isfinite(x) for x in rect):
                        return rect
    return None


def _blocks(table):
    """Yield table and descendants, preserving direct-cell scopes."""
    yield table
    for cell in table.get("cells", ()):
        for block in cell.get("blocks", cell.get("paragraphs", ())):
            if block.get("kind") == "table":
                yield from _blocks(block)


def _text_witnesses(table):
    witnesses = []
    for node in _blocks(table):
        for cell in node.get("cells", ()):
            value = "".join(
                block.get("text") or ""
                for block in cell.get("blocks", cell.get("paragraphs", ()))
                if block.get("kind", "paragraph") in ("paragraph", "list_item")
            )
            normalized = _norm(value)
            # Single Hangul/Latin letters and numbers are useful witnesses too;
            # only whitespace/punctuation-only strings are omitted.
            if normalized:
                witnesses.append(
                    {
                        "cell": [cell.get("row"), cell.get("col")],
                        "text": value,
                        "normalized": normalized,
                    }
                )
    return witnesses


def _asset_ids(table):
    ids = []
    for node in _blocks(table):
        for cell in node.get("cells", ()):
            for block in cell.get("blocks", cell.get("paragraphs", ())):
                if block.get("kind") != "picture":
                    continue
                native = block.get("native_picture", "")
                found_native = False
                if native:
                    try:
                        root = ET.fromstring(native)
                        for element in root.iter():
                            ref = element.attrib.get("binaryItemIDRef")
                            if ref:
                                ids.append(ref)
                                found_native = True
                    except ET.ParseError:
                        # Missing/invalid native placement cannot be silently
                        # treated as an image-free table.
                        ids.append("!unparseable-picture-reference!")
                uri = (block.get("image") or {}).get("uri", "")
                if uri.startswith("bin://") and not found_native:
                    ids.append("image" + uri.removeprefix("bin://"))
    return Counter(ids)


def _picture_placements(table, owner="root"):
    """Yield native picture asset key + owning cell for overlap analysis."""
    for cell in table.get("cells", ()):
        owner_key = (owner, cell.get("row"), cell.get("col"))
        for index, block in enumerate(cell.get("blocks", cell.get("paragraphs", ()))):
            if block.get("kind") == "picture":
                uri = (block.get("image") or {}).get("uri", "")
                if uri.startswith("bin://"):
                    yield "image" + uri.removeprefix("bin://"), owner_key, index
            elif block.get("kind") == "table":
                yield from _picture_placements(
                    block, f"{owner}/c{cell.get('row')}_{cell.get('col')}/t{index}"
                )


def _graphic_overlap_check(pdf, page, table, ir):
    """Reject substantial overlap of source raster placements owned by distinct cells.

    PDF xrefs preserve the actual transformed image draw rectangles. The owner
    comes from the native table cell, so unrelated page images are ignored.
    """
    placements = list(_picture_placements(table))
    if not placements:
        return True, {
            "expected_picture_placements": 0,
            "matched_picture_placements": 0,
            "overlaps": [],
        }
    assets = (ir or {}).get("picture_assets") or {}
    wanted = {}
    for asset_id, owner, block_index in placements:
        asset = assets.get(asset_id) or {}
        raw = asset.get("bytes")
        if not raw:
            continue
        try:
            im = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
            fingerprint = (im.size, hashlib.sha256(im.tobytes()).digest())
        except Exception:
            continue
        wanted.setdefault(fingerprint, []).append(
            {"asset_id": asset_id, "owner": list(owner), "block_index": block_index}
        )
    draws = []
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref")
        if not xref:
            continue
        try:
            blob = pdf.extract_image(xref)["image"]
            im = Image.open(io.BytesIO(blob)).convert("RGB")
            fingerprint = (im.size, hashlib.sha256(im.tobytes()).digest())
        except Exception:
            continue
        owners = wanted.get(fingerprint)
        if owners:
            # One PDF image draw can serve a repeated placement only once.
            owner = owners.pop(0)
            draws.append({**owner, "bbox": pymupdf.Rect(info["bbox"])})
    overlaps = []
    for i, left in enumerate(draws):
        for right in draws[i + 1 :]:
            if left["owner"] == right["owner"]:
                continue
            a, b = left["bbox"], right["bbox"]
            intersection = a & b
            if intersection.is_empty:
                continue
            smaller = min(a.get_area(), b.get_area())
            ratio = intersection.get_area() / smaller if smaller else 0
            if ratio >= 0.05:
                overlaps.append(
                    {
                        "left_asset": left["asset_id"],
                        "left_owner": left["owner"],
                        "left_bbox": [round(v, 2) for v in a],
                        "right_asset": right["asset_id"],
                        "right_owner": right["owner"],
                        "right_bbox": [round(v, 2) for v in b],
                        "intersection_over_smaller": round(ratio, 4),
                    }
                )
    return not overlaps, {
        "expected_picture_placements": len(placements),
        "matched_picture_placements": len(draws),
        "overlaps": overlaps,
    }


def _native_size(table):
    geom = table.get("native_geometry") or {}
    size = geom.get("sz") or {}
    # HWP units are 1/7200 inch = 0.01 PDF point.
    try:
        width = float(size["width"]) / 100.0
        height = float(size["height"]) / 100.0
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _raster_fidelity(page, bbox, candidate):
    """Compare candidate to a slightly padded source-page crop.

    PDF table crops include a small safety border around detector boxes. Search
    that border as an isotropic PDF-point margin; requiring one consistent
    scale for both axes avoids stretching a partial crop into a match.
    """
    best = None
    for margin in (0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12):
        area = (bbox + (-margin, -margin, margin, margin)) & page.rect
        sx = candidate.width / area.width
        sy = candidate.height / area.height
        if abs(sx - sy) / max(sx, sy) > 0.012:
            continue
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix((sx + sy) / 2, (sx + sy) / 2), clip=area, alpha=False
        )
        reference = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if reference.size != candidate.size:
            reference = reference.resize(candidate.size, Image.Resampling.BILINEAR)
        diff = ImageChops.difference(candidate, reference).convert("L")
        values = list(diff.getdata())
        mean = sum(values) / max(1, len(values))
        close = sum(v <= 12 for v in values) / max(1, len(values))
        # A 64 px tile cap catches a missing arrow or small icon that would be
        # diluted by a whole-page mean, while allowing normal antialiasing.
        tile_max = 0.0
        for y in range(0, diff.height, 64):
            for x in range(0, diff.width, 64):
                tile = diff.crop((x, y, min(x + 64, diff.width), min(y + 64, diff.height)))
                tile_mean = sum(tile.getdata()) / max(1, tile.width * tile.height)
                tile_max = max(tile_max, tile_mean)
        record = (mean, -close, margin, close, tile_max)
        if best is None or record < best:
            best = record
    if best is None:
        return False, {"matches": False, "reason": "crop_aspect_ratio_unresolved"}
    mean, _, margin, close, tile_max = best
    ok = mean <= 6.0 and close >= 0.93 and tile_max <= 26.0
    return ok, {
        "mean_abs_error": round(mean, 3),
        "pixels_with_error_le_12_fraction": round(close, 5),
        "maximum_64px_tile_mean_error": round(tile_max, 3),
        "padding_pt": margin,
        "matches": ok,
    }


def _image_asset_check(pdf, page, bbox, candidate_path, ir, asset_counts):
    if not asset_counts:
        return True, {"expected_assets": 0, "matched_assets": 0, "placements": []}
    assets = (ir or {}).get("picture_assets") or {}
    expected = {}
    for asset_id, count in asset_counts.items():
        if asset_id.startswith("!"):
            return False, {
                "reason": "unparseable_native_picture",
                "expected_assets": sum(asset_counts.values()),
            }
        asset = assets.get(asset_id)
        if not asset:
            return False, {
                "reason": "missing_ir_picture_asset",
                "asset_id": asset_id,
                "expected_assets": sum(asset_counts.values()),
            }
        raw = asset.get("bytes")
        if not raw:
            return False, {
                "reason": "asset_has_no_bytes",
                "asset_id": asset_id,
                "expected_assets": sum(asset_counts.values()),
            }
        try:
            expected[asset_id] = (
                count,
                Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB"),
            )
        except Exception as exc:
            return False, {
                "reason": "asset_decode_failed",
                "asset_id": asset_id,
                "detail": str(exc),
            }

    # Locate source PDF images by pixels, then compare each projected image
    # rectangle to the corresponding pixels in the proposed crop.
    try:
        candidate = Image.open(candidate_path).convert("RGB")
    except Exception as exc:
        return False, {"reason": "candidate_image_unreadable", "detail": str(exc)}
    sx, sy = candidate.width / bbox.width, candidate.height / bbox.height
    matrix = pymupdf.Matrix(sx, sy)
    origin = (bbox * matrix).irect
    seen = Counter()
    placements = []
    for info in page.get_image_info(xrefs=True):
        rect = pymupdf.Rect(info.get("bbox", (0, 0, 0, 0)))
        if not bbox.contains(rect) or not info.get("xref"):
            continue
        try:
            source = Image.open(io.BytesIO(pdf.extract_image(info["xref"])["image"])).convert("RGB")
        except Exception:
            continue
        matched_id = None
        for asset_id, (_count, asset_img) in expected.items():
            if (
                source.size == asset_img.size
                and hashlib.sha256(source.tobytes()).digest()
                == hashlib.sha256(asset_img.tobytes()).digest()
            ):
                matched_id = asset_id
                break
        if not matched_id:
            continue
        projected = (rect * matrix).irect
        local = (
            projected.x0 - origin.x0,
            projected.y0 - origin.y0,
            projected.x1 - origin.x0,
            projected.y1 - origin.y0,
        )
        if (
            local[0] < 0
            or local[1] < 0
            or local[2] > candidate.width
            or local[3] > candidate.height
        ):
            continue
        tile = candidate.crop(local)
        asset_img = expected[matched_id][1]
        # PDF placement can scale the original; compare small, scale-normalized
        # thumbnails so the test is about retained content, not DPI.
        side = (48, 48)
        delta = ImageChops.difference(tile.resize(side), asset_img.resize(side))
        mean = sum(delta.convert("L").getdata()) / (side[0] * side[1])
        if mean <= 18:
            seen[matched_id] += 1
            placements.append(
                {"asset_id": matched_id, "bbox": list(local), "mean_error": round(mean, 2)}
            )
    missing = {
        key: max(0, need - seen[key]) for key, (need, _) in expected.items() if seen[key] < need
    }
    return not missing, {
        "expected_assets": sum(asset_counts.values()),
        "matched_assets": sum(seen.values()),
        "missing": missing,
        "placements": placements,
    }


def recover(pdf, table, page_entry, candidate_row, candidate_png, ir):
    """Return whether a detected source-PDF crop passes the table fidelity checks.

    The function is deliberately fail-closed. It only accepts when the caller
    provides source-page geometry and the candidate is consistent with source
    text, native dimensions, referenced images, and the original PDF crop.
    """
    evidence = {"eligible": False, "checks": {}, "failures": []}
    own_pdf = not isinstance(pdf, pymupdf.Document)
    doc = None
    try:
        if own_pdf:
            doc = pymupdf.open(pdf)
            pdf_doc = doc
        else:
            pdf_doc = pdf
        page_number = int(page_entry.get("page") or candidate_row.get("page") or 0)
        if page_number < 1 or page_number > len(pdf_doc):
            evidence["failures"].append("invalid_source_page")
            return False, evidence
        page = pdf_doc[page_number - 1]
        bbox = _bbox(candidate_row, page_entry)
        if bbox is None:
            evidence["failures"].append("missing_pdf_bbox")
            return False, evidence
        bbox &= page.rect
        if bbox.is_empty:
            evidence["failures"].append("empty_pdf_bbox")
            return False, evidence
        evidence["page"] = page_number
        evidence["bbox_top_left"] = list(bbox)
        if candidate_row.get("match_status") not in (None, "matched"):
            evidence["failures"].append("candidate_identity_not_verified")
            return False, evidence

        # Preferred extent proof comes from the independent page locator: it
        # unions the original visible bounds of every native cell (including
        # picture cells) on this source page. The legacy native table size is
        # retained as a conservative fallback for older callers.
        page_bounds = page_entry.get("bounds_by_page")
        if isinstance(page_bounds, dict):
            page_bounds = page_bounds.get(str(page_number))
        if page_bounds is not None:
            try:
                union = pymupdf.Rect(*(float(x) for x in page_bounds))
                all_visible = (page_entry.get("evidence") or {}).get("all_cells_fully_visible")
                contains = (
                    bbox.x0 <= union.x0 + 1
                    and bbox.y0 <= union.y0 + 1
                    and bbox.x1 >= union.x1 - 1
                    and bbox.y1 >= union.y1 - 1
                )
                evidence["checks"]["cell_extents"] = {
                    "union_bounds": list(union),
                    "all_cells_fully_visible": all_visible,
                    "candidate_contains_union": contains,
                }
                if all_visible is not True:
                    evidence["failures"].append("source_page_does_not_show_all_native_cells")
                if not contains:
                    evidence["failures"].append("crop_excludes_native_cell_extent")
            except (TypeError, ValueError):
                evidence["failures"].append("invalid_native_cell_union_bounds")
        else:
            evidence["checks"]["cell_extents"] = {
                "verified": False,
                "reason": "locator cell union bounds unavailable",
            }

        # Verify image is decodable and its crop has plausible dimensions.
        try:
            with Image.open(candidate_png) as im:
                candidate = im.convert("RGB")
                candidate_size = candidate.size
                evidence["candidate_sha256"] = hashlib.sha256(
                    Path(candidate_png).read_bytes()
                ).hexdigest()
        except Exception as exc:
            evidence["failures"].append("candidate_png_unreadable")
            evidence["detail"] = str(exc)
            return False, evidence
        evidence["candidate_size"] = list(candidate_size)

        # Detect source placements whose transformed raster rectangles overlap
        # while belonging to distinct native cells. The geometry check below
        # also checks native text overflow, isolated symbol loss, and the
        # candidate's registration against this original page.
        overlap_ok, overlap_evidence = _graphic_overlap_check(pdf_doc, page, table, ir)
        evidence["checks"]["graphic_overlap"] = overlap_evidence
        if not overlap_ok:
            evidence["failures"].append("overlapping_graphics_from_distinct_cell_owners")

        geometry_verdict, geometry_evidence = geometry_check(
            pdf_doc, [page_number], table, [candidate_png], page_entry=page_entry
        )
        evidence["checks"]["target_geometry"] = {
            "verdict": geometry_verdict,
            "detail": geometry_evidence,
        }
        if geometry_verdict is not True:
            evidence["failures"].append("target_geometry_incomplete_or_unverified")

        # All native visible cell text, including nested tables, must remain
        # present in the original page region represented by this crop.
        raw_pdf_text = page.get_text("text", clip=bbox, sort=True)
        text = _norm(raw_pdf_text)
        witnesses = _text_witnesses(table)
        missing_text = [w for w in witnesses if w["normalized"] not in text]
        evidence["checks"]["cell_text"] = {
            "expected": len(witnesses),
            "matched": len(witnesses) - len(missing_text),
            "missing": [{"cell": x["cell"], "text": x["text"][:100]} for x in missing_text[:20]],
        }
        # Some source PDFs contain broken legacy Korean CMaps or rasterized
        # table text. An exact source-raster match below is then the visual
        # witness; only fail text here when the PDF text layer is demonstrably
        # usable (at least one native witness can be found).
        replacement_fraction = raw_pdf_text.count("\ufffd") / max(1, len(raw_pdf_text))
        text_layer_usable = (
            len(witnesses) > 0
            and (len(witnesses) - len(missing_text)) / len(witnesses) >= 0.80
            and replacement_fraction < 0.005
        )
        evidence["checks"]["cell_text"]["text_layer_usable"] = text_layer_usable
        evidence["checks"]["cell_text"]["replacement_character_fraction"] = round(
            replacement_fraction, 5
        )
        if not witnesses:
            evidence["failures"].append("no_native_text_witnesses")
        elif text_layer_usable and missing_text:
            evidence["failures"].append("source_region_omits_cell_text")

        # Native table dimensions guard against accepting a narrow sliver even
        # if it contains a few distinctive words. Allow modest layout/render
        # rounding; larger discrepancies require an explicit caller extent.
        size = _native_size(table)
        if size:
            width, height = size
            dims_ok = bbox.width >= width * 0.94 and bbox.height >= height * 0.94
            evidence["checks"]["native_footprint"] = {
                "expected_pt": [round(width, 2), round(height, 2)],
                "crop_pt": [round(bbox.width, 2), round(bbox.height, 2)],
                "area_occupancy": round((width * height) / max(1, bbox.width * bbox.height), 4),
                "fits": dims_ok,
            }
            if not dims_ok:
                evidence["failures"].append("crop_smaller_than_native_table")
            if (width * height) / max(1, bbox.width * bbox.height) < 0.30:
                evidence["failures"].append("candidate_region_too_broad_for_table")
        else:
            evidence["checks"]["native_footprint"] = {
                "fits": None,
                "reason": "native size unavailable",
            }
            evidence["failures"].append("native_table_size_unavailable")

        # Validate all embedded picture assets referenced by the table against
        # both the PDF's image placement and the same location in candidate PNG.
        image_ok, image_evidence = _image_asset_check(
            pdf_doc, page, bbox, candidate_png, ir, _asset_ids(table)
        )
        evidence["checks"]["images"] = image_evidence
        if not image_ok:
            # A few renderers flatten or transform HWP image assets so their
            # original bytes no longer occur as PDF xrefs. If the complete
            # source-region raster matches, the crop still demonstrably keeps
            # those visible pixels. Record the weaker provenance explicitly.
            evidence["checks"]["images"]["asset_match_fallback"] = "awaiting_source_raster_check"

        # A direct source-crop raster check catches graphical content (arrows,
        # lines, diagrams, screenshots) that may not be exposed as text/assets.
        raster_ok, raster_evidence = _raster_fidelity(page, bbox, candidate)
        evidence["checks"]["source_raster"] = raster_evidence
        if not raster_ok:
            evidence["failures"].append("candidate_raster_differs_from_source_region")
        elif not image_ok:
            evidence["checks"]["images"]["asset_match_fallback"] = "source_raster_exact_match"

        evidence["eligible"] = not evidence["failures"]
        return evidence["eligible"], evidence
    except Exception as exc:
        evidence["failures"].append("verification_error")
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        return False, evidence
    finally:
        if doc is not None:
            doc.close()
