"""Locate native body tables in an original rhwp render, without using PDF detector crops."""

import base64
import hashlib
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET
from PIL import Image

from ..hwp_source import _rhwp_parse
from ..normalize import sha256_of
from ..table_images import iter_table_nodes

SVG = "{http://www.w3.org/2000/svg}"


def _text(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def _fingerprint(blob):
    with Image.open(BytesIO(blob)) as im:
        rgba = im.convert("RGBA")
        alpha = rgba.getchannel("A").getextrema()
        pixels = rgba.convert("RGB") if alpha == (255, 255) else rgba
        return (pixels.size, pixels.mode, hashlib.sha256(pixels.tobytes()).hexdigest())


def _rect(element):
    x, y, w, h = (float(element.get(k, 0)) for k in ("x", "y", "width", "height"))
    return x, y, x + w, y + h


def _inside(a, b, tolerance=0.5):
    return all(
        x <= y + tolerance
        for x, y in zip((b[0], b[1], a[2], a[3]), (a[0], a[1], b[2], b[3]), strict=True)
    )


@dataclass(frozen=True)
class PaintedCell:
    signature: tuple
    page: int
    box: tuple
    visible: bool
    fully_visible: bool


def _painted_cells(document, pdf_page_sizes):
    result = []
    image_cache = {}
    for page in range(1, document.page_count + 1):
        svg = ET.fromstring(document.render_svg(page - 1).encode())
        view = [float(v) for v in svg.get("viewBox").split()]
        page_box = (view[0], view[1], view[0] + view[2], view[1] + view[3])
        width, height = pdf_page_sizes[page - 1] if pdf_page_sizes else view[2:]
        sx, sy = width / view[2], height / view[3]
        if abs(sx - sy) > 0.001 * max(sx, sy):
            raise ValueError(f"PDF and rhwp page {page} have different aspect ratios")
        clips = {
            p.get("id"): _rect(rect)
            for p in svg.iter(SVG + "clipPath")
            if (rect := p.find(SVG + "rect")) is not None
        }
        for group in svg.iter(SVG + "g"):
            key = group.get("clip-path", "")[5:-1]
            if not key.startswith("cell-clip-") or key not in clips:
                continue
            text, images = [], []
            for element in group.iterdescendants():
                if element.tag not in (SVG + "text", SVG + "image"):
                    continue
                nearest = next(
                    (
                        p
                        for p in element.iterancestors()
                        if p.get("clip-path", "").startswith("url(#cell-clip-")
                    ),
                    None,
                )
                if nearest is not group:
                    continue
                if element.tag == SVG + "text":
                    text.append("".join(element.itertext()))
                else:
                    href = element.get("href") or element.get(
                        "{http://www.w3.org/1999/xlink}href", ""
                    )
                    digest = hashlib.sha256(href.encode()).digest()
                    if digest not in image_cache:
                        try:
                            if not href.startswith("data:image/") or ";base64," not in href:
                                raise ValueError("Not an embedded image")
                            image_cache[digest] = _fingerprint(
                                base64.b64decode(href.split(",", 1)[1])
                            )
                        except (ValueError, OSError, Image.DecompressionBombError):
                            image_cache[digest] = ("unreadable-render", digest.hex())
                    images.append(image_cache[digest])
            box = clips[key]
            ancestors = list(group.iterancestors())
            active = [
                clips[p.get("clip-path", "")[5:-1]]
                for p in ancestors
                if p.get("clip-path", "")[5:-1] in clips
            ]
            # A transformed clipping coordinate space needs separate geometric evidence.
            visible = not any(
                p.get("transform") or p.get("display") == "none" or p.get("visibility") == "hidden"
                for p in [group, *ancestors]
            )
            areas = [page_box, *active]
            intersection = [box, *areas]
            visible = visible and all(
                max(r[i] for r in intersection) < min(r[i + 2] for r in intersection)
                for i in (0, 1)
            )
            fully_visible = visible and all(_inside(box, area) for area in areas)
            coordinates = (
                (box[0] - view[0]) * sx,
                (box[1] - view[1]) * sy,
                (box[2] - view[0]) * sx,
                (box[3] - view[1]) * sy,
            )
            result.append(
                PaintedCell(
                    (_text("".join(text)), tuple(images)), page, coordinates, visible, fully_visible
                )
            )
    return result


def _native_sequences(ir, document):
    assets = ir.get("picture_assets", {})
    image_cache = {}
    package = None
    references = {}
    signatures = []
    tables = {}
    stable_ids = {id(table): key for key, table in iter_table_nodes(ir)}

    def picture(block):
        nonlocal package, references
        uri = (block.get("image") or {}).get("uri", "")
        if uri not in image_cache:
            ref = "image" + uri.removeprefix("bin://")
            try:
                if not uri.startswith("bin://"):
                    raise ValueError("Unresolved native picture")
                if ref in assets:
                    blob = base64.b64decode(assets[ref]["bytes"])
                else:
                    if package is None:
                        package = ZipFile(BytesIO(document.to_hwpx_bytes()))
                        header = ET.fromstring(package.read("Contents/content.hpf"))
                        references = {
                            n.get("id"): n.get("href")
                            for n in header.findall(".//{*}manifest/{*}item")
                        }
                    blob = package.read(references[ref])
                image_cache[uri] = _fingerprint(blob)
            except (KeyError, ValueError, OSError, Image.DecompressionBombError):
                image_cache[uri] = ("unreadable-native", uri)
        return image_cache[uri]

    def visit(table):
        start = len(signatures)
        direct = []
        for cell in table["cells"]:
            blocks = cell["blocks"]
            text = "".join(
                b.get("text", "") for b in blocks if b["kind"] in ("paragraph", "list_item")
            )
            images = tuple(picture(b) for b in blocks if b["kind"] == "picture")
            direct.append(len(signatures))
            signatures.append((_text(text), images))
            for nested in (b for b in blocks if b["kind"] == "table"):
                visit(nested)
        tables[stable_ids[id(table)]] = (start, len(signatures), direct)

    try:
        for block in ir["body"]:
            if block["kind"] == "table":
                visit(block)
    finally:
        if package is not None:
            package.close()
    return signatures, tables


def _positions(sequence):
    found = defaultdict(list)
    for i, sig in enumerate(sequence):
        found[sig].append(i)
    return found


def _intervals(pattern, sequence, positions, anchor):
    size = len(pattern)
    return [
        j - anchor
        for j in positions.get(pattern[anchor], ())
        if 0 <= j - anchor <= len(sequence) - size
        and sequence[j - anchor : j - anchor + size] == pattern
    ]


def _resolve(native, tables, painted, coordinate_space):
    rendered = [c.signature for c in painted]
    ni, ri = _positions(native), _positions(rendered)
    results = {}
    for key, (start, stop, direct) in tables.items():
        pattern = native[start:stop]
        # Full table interval includes every descendant, with each cell's own content emitted once.
        anchors = [i for i, (text, images) in enumerate(pattern) if len(text) >= 2 or images]
        record = {
            "pages": [],
            "bounds_by_page": {},
            "coordinate_space": coordinate_space,
            "status": "held",
            "reason": "no_distinguishing_cell",
            "evidence": {},
        }
        results[key] = record
        if not anchors:
            continue
        anchor = min(anchors, key=lambda i: len(ri.get(pattern[i], ())))
        candidates = _intervals(pattern, rendered, ri, anchor)
        native_candidates = _intervals(pattern, native, ni, anchor)
        record["evidence"] = {
            "native_cells_including_nested": len(pattern),
            "native_direct_cells": len(direct),
            "rendered_occurrences": len(candidates),
            "native_occurrences": len(native_candidates),
        }
        if len(candidates) != 1 or native_candidates != [start]:
            record["reason"] = (
                "ambiguous_cell_sequence"
                if len(candidates) > 1 or len(native_candidates) > 1
                else "incomplete_cell_sequence"
            )
            continue
        offset = candidates[0]
        cells = painted[offset : offset + len(pattern)]
        if not all(c.visible for c in cells):
            record["reason"] = "cell_geometry_unconfirmed"
            continue
        pages = sorted({c.page for c in cells})
        bounds = {
            str(p): [
                round(f(c.box[j] for c in cells if c.page == p), 3)
                for j, f in enumerate((min, min, max, max))
            ]
            for p in pages
        }
        record.update(
            pages=pages,
            bounds_by_page=bounds,
            status="verified",
            reason="unique_native_and_rendered_cell_sequence",
        )
        record["evidence"].update(
            rendered_cell_start=offset,
            all_cells_fully_visible=all(c.fully_visible for c in cells),
            matched_pictures=sum(len(s[1]) for s in pattern),
        )
    return results


def build_page_index(source, ir, *, expected_sha256=None, pdf=None):
    """Precompute one exact cell-sequence index per source. Returns one record per stable table ID.

    Pages are one-based physical rhwp pages. Missing cell clips, unreadable pictures and repeated
    sequences yield an empty verified-page list. A verified page locates the source table; it
    does not assess the fidelity or completeness of a separate PNG or PDF conversion. Pass a
    path to a source-derived SVG-to-PDF render for bounds in that PDF's top-left points;
    otherwise bounds use the original rhwp SVG top-left viewBox units. Page numbers used as
    bounds_by_page keys are strings. This conversion assumes full, unrotated, uncropped pages.
    """
    source = Path(source)
    if expected_sha256 is not None and sha256_of(source) != expected_sha256.lower():
        raise ValueError("Source SHA-256 does not match the requested document")
    document = _rhwp_parse(source)
    native, tables = _native_sequences(ir, document)
    if not tables:
        return {}
    sizes = []
    if pdf is not None:
        import pymupdf

        with pymupdf.open(pdf) as rendered_pdf:
            if len(rendered_pdf) != document.page_count:
                raise ValueError("Source-derived PDF and rhwp page counts differ")
            for p in rendered_pdf:
                if p.rotation or p.cropbox != p.mediabox:
                    raise ValueError(
                        "Rotated/cropped PDF pages lack a verified coordinate transform"
                    )
                sizes.append((p.rect.width, p.rect.height))
    coordinate_space = (
        "pdf_top_left_points" if pdf is not None else "rhwp_svg_top_left_viewbox_units"
    )
    return _resolve(native, tables, _painted_cells(document, sizes), coordinate_space)
