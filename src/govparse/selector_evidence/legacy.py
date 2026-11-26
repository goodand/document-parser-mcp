"""Generate and map the legacy October PDF table-crop candidates.

The implementation preserves the frozen selector's method: PyMuPDF table
detection with ``snap_tolerance=1``, a six-point clipped border at 190 DPI by
default, and scoring against native IR cell text plus table dimensions and
cell coordinates. A returned match is an identity hypothesis, not a claim that
the candidate is visually complete; callers should run the separate geometry
and picture checks before selecting it.
"""

from __future__ import annotations

import hashlib
import math
import os
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf

METHOD = "pdf_grid_cell_text_rarity_and_native_row_column_alignment_v1"
SNAP_TOLERANCE = 1
CROP_MARGIN_PT = 6.0


def _canonical(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold()
        if char.isalnum()
    )


def _native_tables(ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten top-level and nested table nodes using the engine stable-ID scheme."""
    tables: list[dict[str, Any]] = []

    def visit(table: dict[str, Any], stable_id: str) -> None:
        cells = []
        children = []
        for cell in table.get("cells", ()):
            text = "".join(
                str(block.get("text") or "")
                for block in cell.get("blocks", cell.get("paragraphs", ()))
                if block.get("kind", "paragraph") == "paragraph"
            )
            if value := _canonical(text):
                cells.append((cell.get("row", 0), cell.get("col", 0), value))
            nested = (
                block for block in cell.get("blocks", cell.get("paragraphs", ()))
                if block.get("kind") == "table"
            )
            for child_index, child in enumerate(nested):
                children.append((cell.get("row", 0), cell.get("col", 0), child_index, child))
        tables.append({
            "id": stable_id,
            "rows": int(table.get("rows", 0)),
            "cols": int(table.get("cols", 0)),
            "cells": cells,
        })
        for row, col, child_index, child in children:
            visit(child, f"{stable_id}/c{row}_{col}/t{child_index}")

    ordinal = 0
    for body_index, block in enumerate(ir.get("body", ())):
        if not isinstance(block, dict) or block.get("kind") != "table":
            continue
        section = (block.get("prov") or {}).get("section_idx", 0)
        visit(block, f"s{section}/b{body_index}/t{ordinal}")
        ordinal += 1
    return tables


def _candidate_scorer(tables: list[dict[str, Any]]):
    @cache
    def lookup(query: str):
        matches = {}
        for index, table in enumerate(tables):
            best = 0.0
            positions = []
            for row, col, text in table["cells"]:
                if query == text:
                    strength = 1.0
                elif len(query) >= 3 and query in text:
                    strength = 0.65
                else:
                    continue
                if strength > best:
                    best, positions = strength, []
                if strength == best:
                    positions.append((row, col))
            if positions:
                matches[index] = (best, positions)
        return matches

    def score(detected: list[list[str | None]], rows: int, cols: int) -> dict[str, Any]:
        entries = []
        seen = set()
        for row_index, row in enumerate(detected):
            for col_index, raw in enumerate(row):
                text = _canonical(raw or "")
                if text and text not in seen:
                    seen.add(text)
                    entries.append((row_index, col_index, text, raw or ""))

        scores = [0.0] * len(tables)
        evidence: list[list[tuple[float, str, int, float, int, int]]] = [
            [] for _ in tables
        ]
        for row_index, col_index, text, raw in entries:
            hits = lookup(text)
            specificity = math.log((len(tables) + 1) / (len(hits) + 1)) + 0.25
            weight = min(6.0, math.sqrt(len(text))) * specificity
            for index, (strength, positions) in hits.items():
                table = tables[index]
                col_aligned = (table["cols"] == cols
                               and any(native_col == col_index for _, native_col in positions))
                row_aligned = (table["rows"] == rows
                               and any(native_row == row_index for native_row, _ in positions))
                votes = weight * strength * (1 + 0.12 * col_aligned + 0.04 * row_aligned)
                scores[index] += votes
                evidence[index].append(
                    (votes, raw, len(hits), strength, row_index, col_index)
                )

        order = sorted(range(len(tables)), key=scores.__getitem__, reverse=True)
        if not order:
            return {
                "native_table_id": None, "native_rows": None, "native_cols": None,
                "match_status": "held", "relative_margin": 0.0,
                "matched_distinct_cell_texts": 0, "distinct_detected_cell_texts": len(entries),
                "matched_text_fraction": 0.0, "evidence": [], "alternatives": [],
            }
        top = order[0]
        second_score = scores[order[1]] if len(order) > 1 else 0.0
        ratio = (scores[top] - second_score) / scores[top] if scores[top] else 0.0
        coverage = sum(bool(item) for item in evidence[top]) / max(1, len(entries))
        unique = any(
            hit_count == 1 and len(_canonical(raw)) >= 3
            for _, raw, hit_count, *_ in evidence[top]
        )
        confident = (scores[top] > 0 and ratio >= 0.15
                     and (unique or len(evidence[top]) >= 2))
        ranked = [
            {"native_table_id": tables[index]["id"], "score": round(scores[index], 3),
             "native_rows": tables[index]["rows"], "native_cols": tables[index]["cols"]}
            for index in order[:3] if scores[index] > 0
        ]
        quotes = [
            {"text": raw[:100], "detected_row": row_index, "detected_col": col_index,
             "tables_containing_text": hit_count,
             "comparison": "whole_cell" if strength == 1 else "cell_substring"}
            for _, raw, hit_count, strength, row_index, col_index
            in sorted(evidence[top], reverse=True)[:5]
        ]
        table = tables[top]
        return {
            "native_table_id": table["id"] if confident else None,
            "native_rows": table["rows"] if confident else None,
            "native_cols": table["cols"] if confident else None,
            "match_status": "matched" if confident else "held",
            "relative_margin": round(ratio, 4),
            "matched_distinct_cell_texts": len(evidence[top]),
            "distinct_detected_cell_texts": len(entries),
            "matched_text_fraction": round(coverage, 4),
            "evidence": quotes, "alternatives": ranked,
        }

    return score


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def generate_legacy_candidates(
    pdf_path: str | Path,
    ir: dict[str, Any],
    output_dir: str | Path,
    *,
    dpi: int = 190,
) -> dict[str, Any]:
    """Generate and map all legacy October table crops from one PDF and native IR.

    The function does not read repository fixtures or call MCP. It emits one
    record for every PyMuPDF-detected table, including held identity matches.
    Stable PNG names are ``pNNNN_tN.png``; the returned evidence includes both
    PDF-page bounding boxes in points and the native table stable ID when the
    frozen v1 scorer has enough distinguishing cell-text evidence.
    """
    source_pdf = Path(pdf_path)
    destination = Path(output_dir)
    if not source_pdf.is_file():
        raise FileNotFoundError(source_pdf)
    if not isinstance(ir, dict) or not isinstance(ir.get("body"), list):
        raise TypeError("ir must be a document object with a body list")
    if type(dpi) is not int or not 72 <= dpi <= 600:
        raise ValueError("dpi must be an integer from 72 through 600")

    native_tables = _native_tables(ir)
    scorer = _candidate_scorer(native_tables)
    pdf_sha256 = _sha256(source_pdf)
    destination.mkdir(parents=True, exist_ok=True)
    candidates = []

    with pymupdf.open(source_pdf) as document:
        for page_number, page in enumerate(document, start=1):
            detected_tables = page.find_tables(snap_tolerance=SNAP_TOLERANCE).tables
            for detected_index, detected_table in enumerate(detected_tables):
                candidate_id = f"p{page_number:04d}_t{detected_index}"
                bbox = pymupdf.Rect(*detected_table.bbox)
                crop = (bbox + (-CROP_MARGIN_PT, -CROP_MARGIN_PT,
                                CROP_MARGIN_PT, CROP_MARGIN_PT)) & page.rect
                record: dict[str, Any] = {
                    "candidate_id": candidate_id,
                    "page": page_number,
                    "detected_index": detected_index,
                    "bbox": [round(value, 3) for value in bbox],
                    "bbox_coordinates": "top_left_pdf_points",
                    "crop_bbox": [round(value, 3) for value in crop],
                    "detected_rows": int(detected_table.row_count),
                    "detected_cols": int(detected_table.col_count),
                    "pdf_sha256": pdf_sha256,
                }
                if crop.width < 30 or crop.height < 20:
                    record.update(status="held", reason="crop_smaller_than_legacy_minimum")
                    candidates.append(record)
                    continue

                png_path = destination / f"{candidate_id}.png"
                temporary = destination / f".{candidate_id}.{os.getpid()}.{uuid4().hex}.tmp.png"
                try:
                    page.get_pixmap(clip=crop, dpi=dpi, alpha=False).save(str(temporary))
                    temporary.replace(png_path)
                finally:
                    if temporary.exists():
                        temporary.unlink()

                try:
                    detected_grid = detected_table.extract()
                except (RuntimeError, ValueError, TypeError) as exc:
                    mapping = {
                        "native_table_id": None, "native_rows": None, "native_cols": None,
                        "match_status": "held",
                        "reason": f"grid_extract_failed:{type(exc).__name__}",
                        "relative_margin": 0.0, "matched_distinct_cell_texts": 0,
                        "distinct_detected_cell_texts": 0, "matched_text_fraction": 0.0,
                        "evidence": [], "alternatives": [],
                    }
                else:
                    mapping = scorer(detected_grid, detected_table.row_count,
                                     detected_table.col_count)
                record.update(mapping)
                record.update(status="generated", dpi=dpi, png_path=str(png_path.resolve()),
                              png_sha256=_sha256(png_path))
                candidates.append(record)

    return {
        "schema_version": 1,
        "method": METHOD,
        "pdf_path": str(source_pdf.resolve()),
        "pdf_sha256": pdf_sha256,
        "ir_table_count": len(native_tables),
        "dpi": dpi,
        "snap_tolerance": SNAP_TOLERANCE,
        "crop_margin_points": CROP_MARGIN_PT,
        "candidate_count": len(candidates),
        "matched_count": sum(item.get("match_status") == "matched" for item in candidates),
        "candidates": candidates,
    }

