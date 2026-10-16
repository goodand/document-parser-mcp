"""Render PDF evidence without claiming that a detected crop matches a target.

The page image preserves titles, units, footnotes, and nearby content that a
table detector's tight bounding box can omit. Callers must verify a crop's
identity before using it as evidence for a particular HWP table or diagram.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from pathlib import Path

import pymupdf

from .normalize import sha256_of

SNAP_TOLERANCE = 1  # Match the existing MCP table detector setting.


def _bbox(values: list[float] | tuple[float, ...], label: str) -> list[float]:
    if len(values) != 4 or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values
    ):
        raise ValueError(f"{label} must contain four finite coordinates")
    box = [float(value) for value in values]
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError(f"{label} must have positive width and height")
    return box


def _name(source_hash: str, page: int, dpi: int, role: str, box: list[float]) -> str:
    # Include the exact coordinates in the identity, without a lengthy filename.
    encoded = json.dumps(box, separators=(",", ":"), allow_nan=False).encode("ascii")
    box_hash = hashlib.sha256(encoded).hexdigest()[:20]
    return f"{source_hash[:24]}_p{page:05d}_dpi{dpi}_{role}_{box_hash}.png"


def _write_bytes_atomic(dst: Path, payload: bytes) -> None:
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _render(
    pdf_page: pymupdf.Page,
    out_dir: Path,
    source_hash: str,
    page: int,
    dpi: int,
    role: str,
    box: list[float],
    coverage: str,
    *,
    detected_index: int | None = None,
) -> dict:
    requested = pymupdf.Rect(*box)
    clipped = requested & pdf_page.rect
    if clipped.is_empty or clipped.width <= 0 or clipped.height <= 0:
        raise ValueError(f"{role} lies outside PDF page {page}")
    rendered_box = [float(v) for v in clipped]
    filename = _name(source_hash, page, dpi, role, rendered_box)
    pix = pdf_page.get_pixmap(clip=clipped, dpi=dpi, alpha=False)
    # PNG bytes are complete before the destination is published.
    image_bytes = pix.tobytes("png")
    _write_bytes_atomic(out_dir / filename, image_bytes)
    item = {
        "filename": filename,
        "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "source_pdf_sha256": source_hash,
        "page": page,
        "dpi": dpi,
        "coverage": coverage,
        "source": role,
        "bbox_top_left": box,
        "rendered_bbox_top_left": rendered_box,
        "bbox_coordinates": "PDF top-left points",
        "target_match": "unverified",
        "status": "판단불가",
    }
    if detected_index is not None:
        item["detected_index"] = detected_index
    return item


def render_visual_evidence(
    pdf_path: Path,
    out_dir: Path,
    page: int,
    dpi: int = 190,
    bbox_top_left: list[float] | None = None,
    odl_out_dir: Path | None = None,
    odl_run_id: str = "",
) -> dict:
    """Render one whole page, all detector crops, and an optional requested crop.

    Every crop remains an unverified *candidate*. A caller may identify a target
    only after independent source or visual checks. This function does not infer
    titles, units, continuation, or diagram identity from page proximity.
    """
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive 1-based integer")
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 1 <= dpi <= 600:
        raise ValueError("dpi must be an integer between 1 and 600")
    requested_box = _bbox(bbox_top_left, "bbox_top_left") if bbox_top_left is not None else None
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    source_hash = sha256_of(pdf_path)

    with pymupdf.open(str(pdf_path)) as pdf:
        if page > pdf.page_count:
            raise ValueError(f"page {page} exceeds PDF page count {pdf.page_count}")
        pdf_page = pdf[page - 1]
        out_dir.mkdir(parents=True, exist_ok=True)
        page_box = [float(value) for value in pdf_page.rect]
        full_page = _render(
            pdf_page, out_dir, source_hash, page, dpi, "page", page_box, "page"
        )

        detected_crops: list[dict] = []
        skipped_crops: list[dict] = []
        detector_error = None
        try:
            found = pdf_page.find_tables(snap_tolerance=SNAP_TOLERANCE).tables
            for index, table in enumerate(found):
                try:
                    box = _bbox(table.bbox, "detected table bbox")
                    detected_crops.append(
                        _render(
                            pdf_page,
                            out_dir,
                            source_hash,
                            page,
                            dpi,
                            "detector",
                            box,
                            "detected_region",
                            detected_index=index,
                        )
                    )
                except (TypeError, ValueError) as exc:
                    skipped_crops.append({"detected_index": index, "reason": str(exc)})
        except Exception as exc:  # Keep the source page available if detection fails.
            detector_error = f"{type(exc).__name__}: {exc}"

        requested_region = None
        if requested_box is not None:
            requested_region = _render(
                pdf_page,
                out_dir,
                source_hash,
                page,
                dpi,
                "requested",
                requested_box,
                "requested_region",
            )

        odl_candidates = []
        odl_skipped = []
        odl_status = "not_available"
        if odl_out_dir is not None:
            from .c3_odl_figures import _selected_files, _walk
            from .parsed_store import page_offset

            selected = _selected_files(Path(odl_out_dir), odl_run_id)
            active_manifest = Path(odl_out_dir) / "_active_parse.json"
            if not odl_run_id and active_manifest.is_file():
                active = json.loads(active_manifest.read_text(encoding="utf-8"))
                if (active.get("source") or {}).get("sha256") != source_hash:
                    raise ValueError("Active ODL output belongs to a different PDF")
            if not selected:
                odl_status = "not_available"
            elif odl_run_id:
                odl_status = "legacy_run_unverified"
            elif active_manifest.is_file():
                odl_status = "active_manifest_verified"
            else:
                odl_status = "legacy_selection_unverified"
            for jpath in selected:
                offset = page_offset(jpath)
                if jpath.stem.startswith("p"):
                    parts = jpath.stem.split("-", 1)
                    if len(parts) != 2 or not parts[1].isdigit():
                        raise ValueError(f"Invalid ODL page range filename: {jpath}")
                    if not offset < page <= int(parts[1]):
                        continue
                data = json.loads(jpath.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("kids"), list):
                    raise ValueError(f"Invalid ODL JSON tree: {jpath}")
                for node in _walk(data):
                    if node.get("type") not in ("image", "table"):
                        continue
                    if node.get("page number") != page - offset:
                        continue
                    if len(odl_candidates) >= 80:
                        odl_skipped.append({"reason": "candidate_limit_80"})
                        continue
                    raw = node.get("bounding box")
                    try:
                        bottom_box = _bbox(raw, "ODL bbox")
                        top_box = [
                            bottom_box[0], pdf_page.rect.height - bottom_box[3],
                            bottom_box[2], pdf_page.rect.height - bottom_box[1],
                        ]
                        clipped = pymupdf.Rect(*top_box) & pdf_page.rect
                        if clipped.is_empty or clipped.width < 24 or clipped.height < 24:
                            odl_skipped.append({
                                "element_id": node.get("id"), "type": node["type"],
                                "reason": "small_or_outside_page_component",
                            })
                            continue
                        candidate = _render(
                            pdf_page, out_dir, source_hash, page, dpi,
                            f"odl_{node['type'].replace(' ', '_')}_{len(odl_candidates)}",
                            top_box, "odl_candidate_region",
                        )
                    except (TypeError, ValueError) as exc:
                        odl_skipped.append({
                            "element_id": node.get("id"), "type": node["type"],
                            "reason": f"invalid_bbox: {exc}",
                        })
                        continue
                    candidate.update({
                        "odl_element_id": node.get("id"), "odl_type": node["type"],
                        "odl_bbox_bottom_left": bottom_box,
                        "odl_json": str(jpath.resolve()),
                        "odl_json_sha256": sha256_of(jpath),
                        "bbox_clipped_to_page": any(
                            abs(a - b) > 0.5
                            for a, b in zip(
                                top_box, candidate["rendered_bbox_top_left"], strict=True
                            )
                        ),
                        "review_required": True,
                    })
                    odl_candidates.append(candidate)

    result = {
        "status": "판단불가",
        "reason": "Image coverage is available; target identity and content are unverified.",
        "source_pdf": str(pdf_path.resolve()),
        "source_pdf_sha256": source_hash,
        "page": page,
        "dpi": dpi,
        "bbox_coordinates": "PDF top-left points",
        "target_match": "unverified",
        "full_page": full_page,
        "detected_crops": detected_crops,
        "odl_candidates": odl_candidates,
        "odl_skipped": odl_skipped,
        "odl_status": odl_status,
        "requested_region": requested_region,
        "skipped_crops": skipped_crops,
        "detector_error": detector_error,
        "outside_crop_context": {
            "title": "unverified",
            "unit": "unverified",
            "footnote": "unverified",
            "continuation": "unverified",
        },
    }
    manifest_bytes = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    request_fingerprint = hashlib.sha256(
        json.dumps(
            [source_hash, page, dpi, requested_box],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    manifest_name = (
        f"{source_hash[:24]}_p{page:05d}_dpi{dpi}_manifest_{request_fingerprint}.json"
    )
    _write_bytes_atomic(out_dir / manifest_name, manifest_bytes)
    result["manifest_filename"] = manifest_name
    return result
