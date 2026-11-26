"""Build an editable-table candidate from source-native HWP/HWPX IR.

This conversion is deliberately in-memory: it neither renders assets nor writes
to the parsed source document. Table IDs and nested child references use the
stable IDs from :mod:`govparse.table_images`.
"""
from __future__ import annotations

import math
from typing import Any

from .table_images import table_index


def _positive_grid_size(table: dict[str, Any], key: str) -> int:
    value = table.get(key)
    if type(value) is not int or value < 1:
        raise ValueError(f"native table {key} must be a positive integer")
    return value


def _cell_text(cell: dict[str, Any]) -> str:
    """Preserve paragraph order and line breaks; nested tables are linked separately."""
    paragraphs = []
    for block in cell.get("blocks", []):
        if not isinstance(block, dict) or block.get("kind") != "paragraph":
            continue
        value = block.get("text", "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            value = str(value)
        paragraphs.append(value)
    return "\n".join(paragraphs)


def _cell_span(cell: dict[str, Any], key: str) -> int:
    value = cell.get(key, 1)
    if type(value) is not int or value < 1:
        raise ValueError(f"native table cell {key} must be a positive integer")
    return value


def _known_bbox_px(cell: dict[str, Any]) -> list[float] | None:
    """Pass through only an explicitly pixel-labeled cell box."""
    raw = cell.get("bbox_px")
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        box = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in box):
        return None
    left, top, right, bottom = box
    return box if 0 <= left < right and 0 <= top < bottom else None


def build_native_table_candidate(
    ir: dict[str, Any],
    table_id: str,
    *,
    asset_id: str | None = None,
    image_sha256: str | None = None,
) -> dict[str, Any]:
    """Build an editable table candidate from one native IR table block.

    Args:
        ir: source-native document IR containing a ``body`` array.
        table_id: stable ID returned by ``table_images.iter_table_nodes``.
        asset_id: optional associated source image/table asset identifier.
        image_sha256: optional SHA-256 for the associated asset.

    Returns:
        A JSON-serializable schema v1.0 candidate. Pixel boxes remain ``None``
        unless the source cell explicitly carries a valid ``bbox_px`` value.

    Raises:
        TypeError: when the document or table identifier has the wrong type.
        ValueError: when the identifier is not present or native dimensions are invalid.
    """
    if not isinstance(ir, dict):
        raise TypeError("ir must be a dictionary")
    if not isinstance(table_id, str) or not table_id:
        raise TypeError("table_id must be a non-empty string")

    index = table_index(ir)
    table = index.get(table_id)
    if table is None:
        raise ValueError(f"table_id is not present in native IR: {table_id}")

    rows = _positive_grid_size(table, "rows")
    cols = _positive_grid_size(table, "cols")
    if not isinstance(table.get("cells"), list):
        raise ValueError("native table cells must be a list")

    id_by_object = {id(block): key for key, block in index.items()}
    candidate_cells = []
    for cell in table["cells"]:
        if not isinstance(cell, dict):
            raise ValueError("native table cell must be an object")
        row, col = cell.get("row"), cell.get("col")
        if type(row) is not int or type(col) is not int:
            raise ValueError("native table cell requires integer row and col")
        if row < 0 or col < 0:
            raise ValueError("native table cell row and col must be non-negative")
        row_span = _cell_span(cell, "row_span")
        col_span = _cell_span(cell, "col_span")
        if row + row_span > rows or col + col_span > cols:
            raise ValueError("native table cell extends beyond the declared grid")
        blocks = cell.get("blocks", [])
        if not isinstance(blocks, list):
            raise ValueError("native table cell blocks must be a list")
        nested_ids = [
            id_by_object[id(block)]
            for block in blocks
            if isinstance(block, dict)
            and block.get("kind") == "table"
            and id(block) in id_by_object
        ]
        candidate_cells.append({
            "row": row,
            "col": col,
            "row_span": row_span,
            "col_span": col_span,
            "text": _cell_text(cell),
            "bbox_px": _known_bbox_px(cell),
            "nested_table_ids": nested_ids,
        })

    return {
        "schema_version": "1.0",
        "source_kind": "native",
        "table_id": table_id,
        "asset_id": asset_id,
        "image_sha256": image_sha256,
        "rows": rows,
        "cols": cols,
        "cells": candidate_cells,
        "status": "ready",
    }
