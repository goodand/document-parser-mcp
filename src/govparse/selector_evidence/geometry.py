"""Verify visible text at crop boundaries and isolated symbol glyphs against a PDF."""

from __future__ import annotations

import math
import unicodedata
from contextlib import nullcontext

import numpy as np
import pymupdf
from PIL import Image


def _norm(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def _symbol(value):
    chars = [c for c in value if not c.isspace()]
    if len(chars) != 1:
        return False
    kind = unicodedata.category(chars[0])
    return kind.startswith("S") or kind == "Co"


def _texts(table):
    for cell in table.get("cells", ()):
        paragraphs = []
        for block in cell.get("blocks", cell.get("paragraphs", ())):
            if block.get("kind", "paragraph") == "table":
                yield from _texts(block)
            elif block.get("kind", "paragraph") in ("paragraph", "list_item"):
                paragraphs.append(block.get("text") or "")
        text = "".join(paragraphs).strip()
        if text:
            yield text


def _pdf_image(page):
    pix = page.get_pixmap(colorspace=pymupdf.csGRAY, alpha=False)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _ink(image, width, height):
    reduced = image.resize((width, height), Image.Resampling.BOX)
    return (255 - np.asarray(reduced)).astype(np.float32)


def _window_sum(integral, h, w):
    return integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]


def _correlation(search, template):
    """Normalized cross-correlation over all translations of a downsampled crop."""
    h, w = template.shape
    H, W = search.shape
    if h > H or w > W or min(w, h) < 3:
        return None
    centered = template - template.mean()
    energy = float(np.square(centered).sum())
    if energy < 1:
        return None
    shape = (H + h - 1, W + w - 1)
    corr = np.fft.irfft2(
        np.fft.rfft2(search, shape, axes=(0, 1))
        * np.fft.rfft2(centered[::-1, ::-1], shape, axes=(0, 1)),
        shape,
        axes=(0, 1),
    )
    corr = corr[h - 1 : H, w - 1 : W]

    def integral(a):
        return np.pad(a.cumsum(axis=0, dtype=np.float64).cumsum(axis=1), ((1, 0), (1, 0)))

    sums = _window_sum(integral(search), h, w)
    squares = _window_sum(integral(search * search), h, w)
    score = corr / np.sqrt(energy * np.maximum(squares - sums * sums / (w * h), 1))
    y, x = np.unravel_index(score.argmax(), score.shape)
    return float(score[y, x]), int(x), int(y)


def _register(page_im, candidate, dpi):
    # The padding allows isolated PNGs to keep a white margin outside a page.
    shrink = 0.35
    pad = 24
    canvas = Image.new("L", (page_im.width + 2 * pad, page_im.height + 2 * pad), 255)
    canvas.paste(page_im, (pad, pad))
    search = _ink(canvas, round(canvas.width * shrink), round(canvas.height * shrink))
    # Some PNG canvases have a solid, full-height black strip at an outer
    # edge. Suppress this only while finding the geometric transform. The
    # original pixels remain the evidence for every subsequent comparison.
    pixels = np.array(candidate)
    for view in (pixels, pixels[:, ::-1], pixels.T, pixels.T[:, ::-1]):
        width = 0
        for ratio in (view < 8).mean(axis=0):
            if ratio < 0.98:
                break
            width += 1
        if 2 <= width <= view.shape[1] // 5:
            view[:, :width] = 255
    candidate = Image.fromarray(pixels)
    scales = (
        [72 / dpi] if dpi and 50 <= dpi <= 1200 else [1.0, 0.75, 0.5, 72 / 190, 0.375, 0.333, 1.333]
    )
    best = None
    for scale in scales:
        w, h = (
            round(candidate.width * scale * shrink),
            round(candidate.height * scale * shrink),
        )
        if min(w, h) < 3 or w > search.shape[1] or h > search.shape[0]:
            continue
        found = _correlation(search, _ink(candidate, w, h))
        if found and (best is None or found[0] > best[0]):
            best = (*found, scale)
    if best is None or best[0] < 0.50:
        return None
    coarse, x, y, scale = best
    left, top = round(x / shrink - pad), round(y / shrink - pad)
    width = max(1, round(candidate.width * scale))
    height = max(1, round(candidate.height * scale))
    # Refine the translation in full-resolution pixels in a small neighborhood.
    margin = 6
    near = canvas.crop(
        (
            left + pad - margin,
            top + pad - margin,
            left + pad + width + margin,
            top + pad + height + margin,
        )
    )
    target = _ink(candidate, width, height)
    # Search only 13×13 offsets; FFT on the whole original page is unnecessary.
    dim = np.asarray(near, dtype=np.float32)
    ref = 255 - dim
    best_local = (float("-inf"), margin, margin)
    if min(width, height) > 1:
        down = 2 if width * height > 250_000 else 1
        centered = target[::down, ::down] - target[::down, ::down].mean()
        target_energy = float(np.sum(centered * centered))
        for yy in range(2 * margin + 1):
            for xx in range(2 * margin + 1):
                crop = ref[yy : yy + height : down, xx : xx + width : down]
                if crop.shape != centered.shape:
                    continue
                variance = max(1.0, float(np.sum(crop * crop)) - float(crop.sum()) ** 2 / crop.size)
                value = float(np.sum(crop * centered)) / math.sqrt(
                    max(1.0, target_energy) * variance
                )
                if value > best_local[0]:
                    best_local = (value, xx, yy)
    fine, xx, yy = best_local
    if fine < 0.45:
        return None
    return (
        left - margin + xx,
        top - margin + yy,
        scale,
        round(coarse, 3),
        round(fine, 3),
    )


def _ink_count(image, box):
    b = pymupdf.Rect(box) & pymupdf.Rect(0, 0, image.width, image.height)
    if b.is_empty:
        return 0
    part = np.asarray(
        image.crop((math.floor(b.x0), math.floor(b.y0), math.ceil(b.x1), math.ceil(b.y1)))
    )
    return int((part < 155).sum())


def _candidate_box(source_box, reg, padding=0):
    x, y, scale, _, _ = reg
    a, b, c, d = source_box
    return (
        (a - x - padding) / scale,
        (b - y - padding) / scale,
        (c - x + padding) / scale,
        (d - y + padding) / scale,
    )


def _internal_text_loss(page, src, candidate, reg, words, cells):
    """Find visible word endings beyond source column dividers that became blank."""
    dividers = []
    for drawing in page.get_drawings():
        if "s" not in drawing["type"]:
            continue
        for item in drawing["items"]:
            if item[0] == "l":
                _, a, b = item
                if abs(a.x - b.x) < 0.25 and abs(a.y - b.y) > 8:
                    dividers.append((a.x, min(a.y, b.y), max(a.y, b.y)))
            elif item[0] == "re":
                box = item[1]
                dividers.extend(((box.x0, box.y0, box.y1), (box.x1, box.y0, box.y1)))
    for word in words:
        token = _norm(word[4])
        if len(token) < 4 or not any(token in cell for cell in cells):
            continue
        box = pymupdf.Rect(word[:4])
        for x, top, bottom in dividers:
            if not (
                box.x0 + box.width * 0.5 < x < box.x1 - 5
                and top < box.y0 + 3
                and bottom > box.y1 - 3
            ):
                continue
            check = pymupdf.Rect(x + 2, box.y0 + 1, box.x1 - 1, box.y1 - 1)
            ink = _ink_count(src, check)
            # Allow modest font and baseline changes without counting the solid
            # divider itself as retained text.
            visible = _ink_count(candidate, _candidate_box(check, reg, padding=1)) * reg[2] ** 2
            if ink >= 15 and visible <= max(2, ink * 0.12):
                yield {
                    "kind": "clipped_cell_text",
                    "text": word[4],
                    "divider_x": round(x, 1),
                    "pdf_bbox": list(word[:4]),
                    "source_ink": ink,
                    "candidate_ink_equivalent": round(visible, 1),
                }
                break


def _check_page(page, src, candidate, reg, cells, verified_bounds):
    x, y, scale, coarse, fine = reg
    rect = pymupdf.Rect(x, y, x + candidate.width * scale, y + candidate.height * scale)
    words = page.get_text("words")
    normalized = [_norm(t) for t in cells if _norm(t)]
    markers = [t for t in cells if _symbol(t)]
    evidence = {
        "registered_bbox": [round(v, 1) for v in rect],
        "scale": round(scale, 4),
        "coarse_correlation": coarse,
        "fine_correlation": fine,
        "defects": [],
    }
    if verified_bounds is not None:
        overlap = (verified_bounds & rect).get_area()
        if overlap < 0.50 * min(rect.get_area(), verified_bounds.get_area()):
            evidence["reason"] = "registered_at_different_source_region"
            return None, evidence
    nearby = [
        w
        for w in words
        if rect.x0 - 60 <= w[0] <= rect.x1 + 60 and rect.y0 <= 0.5 * (w[1] + w[3]) <= rect.y1
    ]
    matches = []
    for w in nearby:
        token = _norm(w[4])
        if (
            len(token) >= 2
            and rect.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
            and any(token in a for a in normalized)
        ):
            matches.append(token)
    if not matches and not normalized and not markers and fine >= 0.85:
        evidence["reason"] = "registered_no_native_text_targets"
        return True, evidence
    if not matches:
        evidence["reason"] = "no_owned_text_anchor"
        return None, evidence
    evidence["matched_text_anchors"] = len(set(matches))

    if (
        verified_bounds is not None
        and (verified_bounds & rect).get_area() < 0.60 * verified_bounds.get_area()
    ):
        missing = set()
        for w in words:
            middle = pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            token = _norm(w[4])
            if not verified_bounds.contains(middle) or (pymupdf.Rect(w[:4]) & rect).get_area() > 0:
                continue
            if (
                len(token) >= 3
                and any(token in c for c in normalized)
                and _ink_count(src, w[:4]) >= 8
            ):
                missing.add(token)
        if len(missing) >= 2 and sum(map(len, missing)) >= 12:
            evidence["defects"].append(
                {
                    "kind": "omitted_table_region",
                    "missing_word_count": len(missing),
                    "examples": sorted(missing, key=lambda s: (-len(s), s))[:5],
                }
            )

    if markers:
        for w in nearby:
            if not _symbol(w[4]) or not rect.contains(
                pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            ):
                continue
            if w[4] not in markers and not any(unicodedata.category(s) == "Co" for s in markers):
                continue
            box = pymupdf.Rect(w[:4])
            box.x0 += 1
            box.x1 -= 1
            n = _ink_count(src, box)
            local = _ink_count(candidate, _candidate_box(box, reg, padding=1)) * scale * scale
            if n >= 9 and local <= max(2, 0.12 * n):
                evidence["defects"].append(
                    {
                        "kind": "missing_symbol",
                        "text": w[4],
                        "pdf_bbox": list(w[:4]),
                        "source_ink": n,
                        "candidate_ink_equivalent": round(local, 1),
                    }
                )

    if fine >= 0.58:
        # Restrict the detector to the registered table, excluding nearby
        # paragraphs outside its horizontal extent.
        inside = [w for w in nearby if rect.x0 <= w[0] <= rect.x1 and rect.y0 <= w[1] <= rect.y1]
        evidence["defects"].extend(
            _internal_text_loss(page, src, candidate, reg, inside, normalized)
        )

    # A word straddling/past a crop boundary is part of the table only when
    # its adjacent text on the same baseline also matches the same native cell.
    # Ignore unrelated running text, including neighboring tables and headings.
    # Cell text can run beyond a table grid while still fitting inside the
    # original physical page or even a PNG's nominal (white-padded) canvas.
    # Consider only a PDF grid occupying most of this registered crop.
    grids = [verified_bounds] if verified_bounds is not None else []
    if not grids:
        for found in page.find_tables(snap_tolerance=1).tables:
            grid = pymupdf.Rect(found.bbox)
            overlap = (grid & rect).get_area()
            if overlap > 0.60 * rect.get_area() and overlap > 0.85 * grid.get_area():
                grids.append(grid)
    grid = max(grids, key=lambda b: b.get_area()) if grids else rect
    evidence["source_grid_bbox"] = [round(v, 1) for v in grid] if grids else None
    for w in nearby:
        side = (
            "right"
            if w[2] > grid.x1 + 2 or w[2] > rect.x1 + 2
            else ("left" if w[0] < grid.x0 - 2 or w[0] < rect.x0 - 2 else None)
        )
        token = _norm(w[4])
        if not side or not token:
            continue
        center = (w[1] + w[3]) / 2
        line = sorted(
            [a for a in nearby if abs((a[1] + a[3]) / 2 - center) < 0.32 * (w[3] - w[1])],
            key=lambda a: a[0],
        )
        n = line.index(w)
        near_words = line[max(0, n - 3) : n] if side == "right" else line[n + 1 : n + 4]
        adjacent = "".join(_norm(a[4]) for a in near_words)
        owned = any(
            any(
                (adjacent[-k:] + token if side == "right" else token + adjacent[:k]) in cell
                for k in range(3, min(12, len(adjacent)) + 1)
            )
            for cell in normalized
        )
        if not owned:
            continue
        box = pymupdf.Rect(w[:4])
        if side == "right":
            box.x0 = max(box.x0, min(grid.x1, rect.x1) + 1)
        else:
            box.x1 = min(box.x1, max(grid.x0, rect.x0) - 1)
        ink = _ink_count(src, box)
        captured = _ink_count(candidate, _candidate_box(box, reg, padding=1)) * scale * scale
        if ink >= 8 and captured <= max(2, 0.14 * ink):
            evidence["defects"].append(
                {
                    "kind": "clipped_owned_word",
                    "side": side,
                    "text": w[4],
                    "pdf_bbox": list(w[:4]),
                    "source_ink_outside": ink,
                    "candidate_ink_equivalent": round(captured, 1),
                }
            )
    evidence["reason"] = (
        "visible_content_lost" if evidence["defects"] else "registered_no_targeted_loss"
    )
    return not bool(evidence["defects"]), evidence


def geometry_check(pdf, page_list, table, paths, *, page_entry=None):
    """Return (True/False/None, evidence). None denotes insufficient evidence.

    Pages are 1-based and in reading order. One PNG is expected per source
    page; unrelated page counts or duplicates abstain. Passing covers only
    these layout defects and does not establish photo or whole-table fidelity.
    """
    bounds = {}
    if (
        page_entry
        and page_entry.get("status") == "verified"
        and page_entry.get("coordinate_space") == "pdf_top_left_points"
    ):
        bounds = page_entry.get("bounds_by_page", {})
    pages = list(page_list or ())
    names = list(paths or ())
    evidence = {"pages": [], "reason": "insufficient_page_or_candidate_correspondence"}
    if len(names) != len(pages) or not pages or len(set(pages)) != len(pages):
        return None, evidence
    states = []
    try:
        with (
            nullcontext(pdf) if isinstance(pdf, pymupdf.Document) else pymupdf.open(pdf)
        ) as document:
            for number, path in zip(pages, names, strict=True):
                if type(number) is not int or not 1 <= number <= len(document):
                    return None, evidence
                page = document[number - 1]
                src = _pdf_image(page)
                with Image.open(path) as im:
                    image = im.convert("L")
                    density = im.info.get("dpi")
                dpi = float(density[0]) if density else None
                reg = _register(src, image, dpi)
                if reg:
                    box = bounds.get(str(number)) or bounds.get(number)
                    verified_bounds = pymupdf.Rect(box) if box else None
                    answer, result = _check_page(
                        page, src, image, reg, list(_texts(table)), verified_bounds
                    )
                else:
                    answer, result = (
                        None,
                        {"reason": "candidate_not_reliably_registered"},
                    )
                evidence["pages"].append({"page": number, **result})
                states.append(answer)
    except (OSError, ValueError, TypeError, pymupdf.FileDataError) as exc:
        return None, {
            **evidence,
            "reason": f"geometry_input_error:{type(exc).__name__}",
        }
    verdict = False if False in states else True if all(s is True for s in states) else None
    evidence["reason"] = {
        False: "visible_content_lost",
        True: "registered_no_targeted_loss",
        None: "insufficient_geometry",
    }[verdict]
    return verdict, evidence
