"""Read the table_id / table_image annotations that desktop_worker.table_images writes into IR table blocks."""

from __future__ import annotations


def _walk(blocks):
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        if b.get("kind") == "table":
            yield b
            for cell in b.get("cells", []):
                yield from _walk(cell.get("blocks"))


def iter_table_images(ir: dict):
    """(table_id, [result-relative PNG paths]) for every rendered table, in document order."""
    for t in _walk(ir.get("body")):
        img = t.get("table_image") or {}
        if t.get("table_id") and img.get("status") == "rendered" and img.get("paths"):
            yield t["table_id"], list(img["paths"])
