"""Build a conservative source-page crop for a confirmed table-geometry loss.

Only verified, single-page cell geometry is accepted. The crop starts with the
native table bounds and may grow to include nearby PDF words whose text belongs
to one of the table's cells, plus adjacent arrow glyphs when the table itself
contains an arrow. Every extension is bounded to 48 PDF points per edge.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from pathlib import Path

import pymupdf

from ..normalize import sha256_of
from ..visual_evidence import _write_bytes_atomic

MAX_EXTENSION_PT = 48.0
EDGE_PAD_PT = 2.0
ARROWS = frozenset("→⇒➔➜➝➞➟➠➡⟶⟹▶►➤➨\uf0e8")


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in value if char.isalnum())


def _cell_texts(table: dict) -> list[str]:
    texts: list[str] = []

    def visit(node: dict) -> None:
        for cell in node.get("cells", ()):
            blocks = cell.get("blocks", cell.get("paragraphs", ()))
            value = "".join(
                str(block.get("text") or "")
                for block in blocks
                if block.get("kind", "paragraph") in ("paragraph", "list_item")
            )
            if _norm(value):
                texts.append(value)
            for block in blocks:
                if block.get("kind") == "table":
                    visit(block)

    if not isinstance(table, dict):
        return texts
    visit(table)
    return texts


def _owned(
    word: tuple, texts: list[str], table_has_arrow: bool, bounds: pymupdf.Rect
) -> tuple[bool, str]:
    x0, y0, x1, y1, raw = word[:5]
    box = pymupdf.Rect(x0, y0, x1, y1)
    normalized = _norm(str(raw))
    if len(normalized) >= 2 and any(normalized in _norm(text) for text in texts):
        return True, "native_cell_text_match"
    if table_has_arrow and any(char in ARROWS for char in str(raw)):
        # Preserve a symbol only when it is within the bounded extension and
        # vertically alongside the verified table. This avoids pulling in an
        # unrelated arrow from elsewhere on the page.
        expanded = pymupdf.Rect(
            bounds.x0 - MAX_EXTENSION_PT,
            bounds.y0 - max(4.0, box.height),
            bounds.x1 + MAX_EXTENSION_PT,
            bounds.y1 + max(4.0, box.height),
        )
        if expanded.intersects(box):
            return True, "adjacent_arrow_glyph"
    return False, ""


def make_source_crop(pdf, table, page_entry, output_path) -> tuple[bool, dict]:
    """Render a verified native table footprint plus nearby owned overflow.

    `page_entry` must be the verified record from ``build_page_index`` and use
    ``pdf_top_left_points`` coordinates. Multi-page/ambiguous entries abstain.
    Returns ``(success, evidence)``; failures never write an output image.
    """
    evidence: dict = {"status": "held", "reason": "insufficient_source_geometry"}
    if not isinstance(page_entry, dict) or page_entry.get("status") != "verified":
        return False, evidence
    pages = page_entry.get("pages")
    bounds_by_page = page_entry.get("bounds_by_page")
    if (
        page_entry.get("coordinate_space") != "pdf_top_left_points"
        or not isinstance(pages, list)
        or len(pages) != 1
        or type(pages[0]) is not int
        or not isinstance(bounds_by_page, dict)
        or set(bounds_by_page) != {str(pages[0])}
    ):
        return False, {**evidence, "reason": "multi_page_or_unverified_bounds"}
    values = bounds_by_page[str(pages[0])]
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return False, {**evidence, "reason": "invalid_native_bounds"}
    try:
        values = [float(value) for value in values]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non-finite bound")
        native_bounds = pymupdf.Rect(*values)
    except (TypeError, ValueError):
        return False, {**evidence, "reason": "invalid_native_bounds"}
    if native_bounds.is_empty or native_bounds.is_infinite:
        return False, {**evidence, "reason": "invalid_native_bounds"}
    cell_texts = _cell_texts(table)
    if not cell_texts:
        return False, {**evidence, "reason": "no_native_cell_text_witness"}

    owns_document = not isinstance(pdf, pymupdf.Document)
    document = None
    try:
        document = pymupdf.open(pdf) if owns_document else pdf
        page_number = pages[0]
        if page_number < 1 or page_number > len(document):
            return False, {**evidence, "reason": "page_out_of_range"}
        page = document[page_number - 1]
        if page.rotation or page.cropbox != page.mediabox:
            return False, {**evidence, "reason": "rotated_or_cropped_page"}
        if not page.rect.contains(native_bounds):
            return False, {**evidence, "reason": "native_bounds_outside_page"}

        has_arrows = any(any(char in ARROWS for char in text) for text in cell_texts)
        crop = pymupdf.Rect(native_bounds)
        extensions = []
        included_glyphs = []
        for word in page.get_text("words", sort=True):
            word_box = pymupdf.Rect(word[:4])
            if native_bounds.contains(word_box):
                if has_arrows and any(char in ARROWS for char in str(word[4])):
                    included_glyphs.append(
                        {
                            "text": str(word[4]),
                            "bbox_top_left_points": [round(v, 2) for v in word_box],
                            "relation": "table_arrow_glyph_in_verified_bounds",
                        }
                    )
                continue
            # Avoid scanning unrelated page content: an eligible overflow must
            # be within the fixed extension limit before text ownership is tested.
            gap_x = max(native_bounds.x0 - word_box.x1, word_box.x0 - native_bounds.x1, 0)
            gap_y = max(native_bounds.y0 - word_box.y1, word_box.y0 - native_bounds.y1, 0)
            # Adjacent continuation is accepted only along a row of the table;
            # text above/below a table is ambiguous even when it repeats a title.
            if gap_x > MAX_EXTENSION_PT or gap_y > 2.0:
                continue
            owned, relation = _owned(word, cell_texts, has_arrows, native_bounds)
            if not owned:
                continue
            padded = pymupdf.Rect(word_box)
            padded.x0 -= EDGE_PAD_PT
            padded.y0 -= EDGE_PAD_PT
            padded.x1 += EDGE_PAD_PT
            padded.y1 += EDGE_PAD_PT
            extension = padded & page.rect
            # Fail closed if ownership would require a crop outside the agreed
            # bounded context. Never silently clip the owned word itself.
            if not page.rect.contains(extension) or not extension.contains(word_box):
                return False, {**evidence, "reason": "owned_content_outside_page"}
            crop |= extension
            extensions.append(
                {
                    "text": str(word[4]),
                    "bbox_top_left_points": [round(v, 2) for v in word_box],
                    "relation": relation,
                }
            )

        # The table itself must intersect the verified source page and must not
        # expand beyond 48 points on any side to acquire ancillary content.
        if not page.rect.contains(crop):
            return False, {**evidence, "reason": "crop_outside_page"}
        deltas = [
            native_bounds.x0 - crop.x0,
            native_bounds.y0 - crop.y0,
            crop.x1 - native_bounds.x1,
            crop.y1 - native_bounds.y1,
        ]
        if any(delta > MAX_EXTENSION_PT + EDGE_PAD_PT for delta in deltas):
            return False, {**evidence, "reason": "adjacent_context_limit_exceeded"}
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        pixmap = page.get_pixmap(clip=crop, dpi=190, alpha=False)
        payload = pixmap.tobytes("png")
        png_hash = hashlib.sha256(payload).hexdigest()
        source_hash = (
            sha256_of(Path(document.name))
            if document.name and Path(document.name).is_file()
            else None
        )
        if not destination.is_file() or sha256_of(destination) != png_hash:
            _write_bytes_atomic(destination, payload)
        return True, {
            "status": "source_crop",
            "reason": "verified_native_bounds_with_owned_overflow",
            "page": page_number,
            "native_bounds_top_left_points": [round(v, 2) for v in native_bounds],
            "crop_bounds_top_left_points": [round(v, 2) for v in crop],
            "extension_limit_points": MAX_EXTENSION_PT,
            "extensions": extensions,
            "included_glyphs": included_glyphs,
            "dpi": 190,
            "png_path": str(destination),
            "png_sha256": png_hash,
            "png_size_bytes": len(payload),
            "source_pdf_sha256": source_hash,
        }
    except (OSError, ValueError, TypeError, pymupdf.FileDataError) as exc:
        return False, {**evidence, "reason": f"source_crop_error:{type(exc).__name__}"}
    finally:
        if owns_document and document is not None:
            document.close()
