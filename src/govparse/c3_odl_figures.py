"""Source-bound C3 structure and ODL figure candidates for human review.

Author: chatggpt_gpt6_extrahigh. A PDF page/image is never assigned to an
HWP paragraph by proximity alone. This module only joins *documents* whose
bytes match; figure-to-paragraph identity remains held.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from .normalize import sha256_of
from .parsed_store import page_offset, selected_json_files

_RUN_ID = re.compile(r"[0-9a-f]{32}")
_CHUNK = re.compile(r"p(\d+)-(\d+)\.json")
_CHILD_KEYS = ("kids", "rows", "cells", "list items")


def _walk(node):
    if not isinstance(node, dict):
        return
    yield node
    for key in _CHILD_KEYS:
        for child in node.get(key) or []:
            yield from _walk(child)


def _png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as fh:
        header = fh.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"ODL image is not PNG: {path}")
    width, height = struct.unpack(">II", header[16:24])
    if not 0 < width <= 100000 or not 0 < height <= 100000:
        raise ValueError(f"Invalid PNG dimensions: {path}")
    return width, height


def _selected_files(out_dir: Path, run_id: str) -> list[Path]:
    if run_id:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be a 32-character lowercase hex identifier")
        run = out_dir / "_runs" / run_id
        if not run.is_dir():
            raise FileNotFoundError(run)
        files = sorted(run.glob("p*-*.json"))
        ranges = []
        for path in files:
            match = _CHUNK.fullmatch(path.name)
            if match:
                ranges.append((int(match[1]), int(match[2]), path))
        ranges.sort()
        if not ranges or any(end <= start for start, end, _ in ranges):
            raise ValueError("run has no valid ODL chunks")
        if any(ranges[i][0] < ranges[i - 1][1] for i in range(1, len(ranges))):
            raise ValueError("run contains overlapping ODL chunks")
        return [path for _, _, path in ranges]
    return selected_json_files(out_dir)


def inspect_c3_odl_figures(
    hwp_path: Path, pdf_path: Path, c3_path: Path, out_dir: Path,
    page: int, run_id: str = "", min_side_pt: float = 24.0,
    c3_element_id: str = "",
) -> dict:
    """Return bounded image candidates with hashes and explicit hold signals.

    The C3 JSON is read to verify its source fingerprints. Image files are only
    read from the selected ODL generation; unsafe relative paths are rejected.
    A missing conversion lineage is surfaced, not inferred from matching names.
    """
    if type(page) is not int or page < 1:
        raise ValueError("page must be a positive 1-based PDF file page")
    if not 0 < min_side_pt <= 1000:
        raise ValueError("min_side_pt must be within (0, 1000]")
    hwp_path, pdf_path = Path(hwp_path).resolve(), Path(pdf_path).resolve()
    c3_path, out_dir = Path(c3_path).resolve(), Path(out_dir).resolve()
    for source in (hwp_path, pdf_path, c3_path):
        if not source.is_file():
            raise FileNotFoundError(source)
    hwp_hash, pdf_hash = sha256_of(hwp_path), sha256_of(pdf_path)
    with c3_path.open(encoding="utf-8") as fh:
        c3 = json.load(fh)
    meta = c3.get("meta") or {}
    source = meta.get("source") or {}
    c3_hwp = (source.get("source_hwp") or {}).get("sha256")
    c3_pdf = (source.get("source_pdf") or {}).get("sha256")
    evidence = c3.get("pdf_evidence") or {}
    if (
        meta.get("kind") != "generic_c3_core"
        or c3_hwp != hwp_hash
        or c3_pdf != pdf_hash
        or evidence.get("sha256") != pdf_hash
    ):
        raise ValueError("C3 source HWP/PDF fingerprints do not match current files")
    selected_element = None
    if c3_element_id:
        kind = "paragraphs" if ":P" in c3_element_id else "tables" if ":T" in c3_element_id else ""
        selected_element = (c3.get(kind) or {}).get(c3_element_id)
        if not isinstance(selected_element, dict):
            raise ValueError(f"C3 element ID not found: {c3_element_id}")
        selected_element = {
            "id": c3_element_id,
            "kind": kind,
            "anchor": selected_element.get("anchor"),
            "plain_text_projection": (
                selected_element.get("plain_text_projection")
                if kind == "paragraphs" else None
            ),
            "rows": selected_element.get("rows") if kind == "tables" else None,
            "cols": selected_element.get("cols") if kind == "tables" else None,
            "text_verification": selected_element.get("text_verification"),
            "structure_verified": selected_element.get("structure_verified"),
            "pdf_page_relation": "unverified_user_selected_anchor",
        }
    page_count = evidence.get("file_page_count")
    if type(page_count) is not int or page > page_count:
        raise ValueError("page exceeds C3 PDF page count")
    from .conversion_lineage import verify_conversion

    lineage = verify_conversion(hwp_path, pdf_path, pdf_path.with_suffix(".source.json"))
    if lineage["status"] == "mismatch":
        raise ValueError("HWP/PDF conversion lineage mismatch")

    files = _selected_files(out_dir, run_id)
    candidates = []
    chunks_on_page = 0
    for path in files:
        match = _CHUNK.fullmatch(path.name)
        if match and not int(match[1]) < page <= int(match[2]):
            continue
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        offset = page_offset(path)
        if not isinstance(data, dict) or not isinstance(data.get("kids"), list):
            raise ValueError(f"Invalid ODL JSON tree: {path}")
        chunks_on_page += 1
        for node in _walk(data):
            if node.get("type") != "image" or node.get("page number") != page - offset:
                continue
            relative = node.get("source")
            if not isinstance(relative, str) or not relative.lower().endswith(".png"):
                raise ValueError(f"ODL image has no PNG source: {path}")
            image_path = (path.parent / relative).resolve()
            if not image_path.is_relative_to(path.parent.resolve()) or not image_path.is_file():
                raise ValueError(f"ODL image source escapes or is missing: {relative}")
            box = node.get("bounding box")
            flags = []
            if not (isinstance(box, list) and len(box) == 4 and
                    all(type(v) in (int, float) for v in box)):
                flags.append("invalid_bbox")
            else:
                width, height = box[2] - box[0], box[3] - box[1]
                if width <= 0 or height <= 0:
                    flags.append("invalid_bbox")
                elif width < min_side_pt or height < min_side_pt:
                    flags.append("small_object_candidate")
                if box[0] < 0 or box[1] < 0:
                    flags.append("bbox_outside_page_candidate")
            px = _png_size(image_path)
            candidates.append({
                "odl_element_id": node.get("id"),
                "odl_json": str(path),
                "image_path": str(image_path),
                "image_sha256": sha256_of(image_path),
                "pixels": list(px),
                "bbox_pt_as_reported_by_odl": box,
                "flags": flags,
                "figure_identity": "판단불가",
                "crop_completeness": "판단불가",
                "hwp_anchor": None,
            })
    return {
        "author": "chatggpt_gpt6_extrahigh",
        "source_hwp_sha256": hwp_hash,
        "source_pdf_sha256": pdf_hash,
        "c3_document": str(c3_path),
        "c3_document_sha256": sha256_of(c3_path),
        "c3_counts": meta.get("counts"),
        "conversion_lineage": lineage,
        "odl_run_id": run_id or None,
        "odl_pdf_lineage": (
            "unverified_legacy_run" if run_id else "verified_by_active_parse_manifest"
        ),
        "pdf_file_page": page,
        "selected_c3_element": selected_element,
        "odl_chunks_read": chunks_on_page,
        "image_candidates": candidates,
        "status": "판단불가",
        "reason": (
            "C3 HWP/PDF fingerprints match; figure-to-HWP-anchor identity, "
            "crop completeness, and nearby title/unit/source are not verified. "
            "An explicit legacy ODL run also lacks a source-bound PDF manifest."
        ),
        "next_review": (
            "Open candidate PNGs beside the original PDF page; reject small "
            "glyphs, check clipping, then bind a C3 anchor with local evidence."
        ),
    }
