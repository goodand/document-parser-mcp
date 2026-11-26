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


def table_assets(ir: dict):
    """Export each selected image with its table ID, provenance and known source pages."""
    for table in _walk(ir.get("body")):
        image = table.get("table_image") or {}
        status = image.get("status")
        if not table.get("table_id") or status not in {"rendered", "source_page"}:
            continue
        paths = image["paths"]
        pages = image.get("source_pages") or []
        for part, path in enumerate(paths, start=1):
            asset = {"origin": "table_source_page" if status == "source_page" else "table_image",
                     "table_id": table["table_id"], "part": part, "parts": len(paths), "path": path,
                     "choice": image.get("choice"), "source_pages": pages}
            if status == "source_page":
                asset["page"] = pages[part - 1]
            if image.get("selection_warning"):
                asset["selection_warning"] = image["selection_warning"]
            yield asset
