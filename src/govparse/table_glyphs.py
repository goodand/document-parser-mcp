"""Fail-closed recovery for font-dependent symbol glyphs in table PNGs."""
from __future__ import annotations

import io
import math
import os
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

from lxml import etree as ET
from PIL import Image, ImageChops, ImageDraw, ImageFont


_SVG_NS = "http://www.w3.org/2000/svg"
_FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}


@lru_cache(maxsize=1)
def _installed_font_files() -> tuple[Path, ...]:
    roots: list[Path] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        roots.append(Path(windir) / "Fonts")
    elif sys.platform == "darwin":
        roots.extend((Path("/System/Library/Fonts"), Path("/Library/Fonts")))
    else:
        roots.extend((Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local/share/fonts"))
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(path for path in root.rglob("*") if path.suffix.lower() in _FONT_SUFFIXES)
    return tuple(files)


def _font_aliases(family: str) -> list[str]:
    aliases = [item.strip().strip("\"'") for item in family.split(",") if item.strip()]
    aliases.extend((
        "seguisym", "Segoe UI Symbol", "Apple Symbols", "Noto Sans Symbols 2",
        "Noto Sans Symbols", "DejaVu Sans", "Arial Unicode MS",
    ))
    return aliases


@lru_cache(maxsize=128)
def _load_symbol_font(family: str, text: str, pixel_size: int) -> ImageFont.FreeTypeFont | None:
    fonts = _installed_font_files()
    by_stem: dict[str, list[Path]] = {}
    for path in fonts:
        key = re.sub(r"[^a-z0-9]", "", path.stem.lower())
        by_stem.setdefault(key, []).append(path)

    preferred: list[Path] = []
    for alias in _font_aliases(family):
        key = re.sub(r"[^a-z0-9]", "", alias.lower())
        preferred.extend(by_stem.get(key, ()))
        # Family names and file names sometimes differ (for example, Malgun Gothic).
        for stem, paths in by_stem.items():
            if key and (stem.startswith(key) or key.startswith(stem)):
                preferred.extend(paths)

    seen: set[Path] = set()
    for path in preferred:
        if path in seen:
            continue
        seen.add(path)
        try:
            font = ImageFont.truetype(str(path), pixel_size)
            if _font_has_glyph(path, text) and font.getmask(text).getbbox():
                return font
        except (OSError, ValueError):
            continue
    return None


def _font_has_glyph(path: Path, text: str) -> bool:
    """Check the actual font cmap; FreeType's tofu fallback is not sufficient."""
    try:
        import pymupdf

        font = pymupdf.Font(fontfile=str(path))
        return all(font.has_glyph(ord(character)) != 0 for character in text)
    except Exception:
        return False


def _number(value: str | None) -> float:
    if value is None:
        return 0.0
    match = re.match(r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))", value)
    if not match:
        raise ValueError(f"unsupported SVG length: {value!r}")
    return float(match.group(1))


def _axis_transform(node: ET._Element) -> tuple[float, float, float, float] | None:
    """Return (sx, sy, tx, ty) for the verified translate-then-scale form only."""
    transform = node.get("transform", "").strip()
    if not transform:
        return 1.0, 1.0, _number(node.get("x")), _number(node.get("y"))
    match = re.fullmatch(
        r"translate\(\s*([-+\d.eE]+)(?:[ ,]+([-+\d.eE]+))?\s*\)"
        r"(?:\s+scale\(\s*([-+\d.eE]+)(?:[ ,]+([-+\d.eE]+))?\s*\))?",
        transform,
    )
    if not match:
        return None
    tx = float(match.group(1))
    ty = float(match.group(2) or 0.0)
    sx = float(match.group(3) or 1.0)
    sy = float(match.group(4) or match.group(3) or 1.0)
    return sx, sy, tx + sx * _number(node.get("x")), ty + sy * _number(node.get("y"))


def _safe_inherited_state(node: ET._Element) -> bool:
    current: ET._Element | None = node
    is_text_node = True
    while current is not None and ET.QName(current).localname != "svg":
        if not is_text_node and current.get("transform"):
            return False
        if current.get("visibility", "visible") == "hidden" or current.get("display", "inline") == "none":
            return False
        if current.get("opacity", "1") not in ("1", "1.0"):
            return False
        style = current.get("style", "").lower()
        if any(token in style for token in ("opacity:", "visibility:hidden", "display:none")):
            return False
        current = current.getparent()
        is_text_node = False
    return True


def _cell_clip(root: ET._Element, node: ET._Element) -> tuple[float, float, float, float] | None:
    parent = node.getparent()
    while parent is not None:
        ref = re.fullmatch(r"url\(#([^)]*)\)", parent.get("clip-path", ""))
        if ref and ref.group(1).startswith("cell-clip-"):
            clip = root.find(f".//{{{_SVG_NS}}}clipPath[@id='{ref.group(1)}']")
            rect = clip.find(f"{{{_SVG_NS}}}rect") if clip is not None else None
            if rect is not None:
                return tuple(_number(rect.get(key)) for key in ("x", "y", "width", "height"))
        parent = parent.getparent()
    return None


def _cell_clip_group(node: ET._Element) -> ET._Element | None:
    parent = node.getparent()
    while parent is not None:
        ref = re.fullmatch(r"url\(#([^)]*)\)", parent.get("clip-path", ""))
        if ref and ref.group(1).startswith("cell-clip-"):
            return parent
        parent = parent.getparent()
    return None


def _parse_color(value: str | None) -> tuple[int, int, int, int]:
    if not value or value == "black":
        return (0, 0, 0, 255)
    if value == "none":
        return (0, 0, 0, 0)
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", value.strip())
    if match:
        rgb = bytes.fromhex(match.group(1))
        return (rgb[0], rgb[1], rgb[2], 255)
    match = re.fullmatch(r"#([0-9a-fA-F]{3})", value.strip())
    if match:
        return tuple(int(char * 2, 16) for char in match.group(1)) + (255,)
    raise ValueError(f"unsupported SVG text fill: {value!r}")


def _symbol_candidates(root: ET._Element):
    for node in root.iter():
        if ET.QName(node).localname != "text":
            continue
        if not _safe_inherited_state(node):
            continue
        text = "".join(node.itertext()).strip()
        if len(text) != 1 or not text.isprintable() or not unicodedata.category(text).startswith("S"):
            continue
        # The clipped group must represent one text-only cell. This avoids
        # painting over mixed content or choosing a symbol from a busy cell.
        clipped_group = _cell_clip_group(node)
        if clipped_group is None:
            continue
        texts = [child for child in clipped_group.iter() if ET.QName(child).localname == "text"]
        if texts != [node]:
            continue
        clip = _cell_clip(root, node)
        transform = _axis_transform(node)
        if clip is None or transform is None:
            continue
        yield node, text, clip, transform


def repair_missing_symbol_glyphs(png: bytes, svg: str) -> bytes:
    """Restore blank symbol-only cell glyphs using an installed Unicode font.

    The source SVG's text transform and cell clip define the exact pixel region.
    If the raster already contains ink in that region, the original PNG bytes are
    returned unchanged. Unsupported SVG geometry, fonts, or image crops also fail
    closed and return the original bytes.
    """
    try:
        root = ET.fromstring(svg.encode("utf-8") if isinstance(svg, str) else svg)
        image = Image.open(io.BytesIO(png)).convert("RGBA")
        svg_width = _number(root.get("width"))
        svg_height = _number(root.get("height"))
        if svg_width <= 0 or svg_height <= 0:
            return png
        scale_x, scale_y = image.width / svg_width, image.height / svg_height
        view_box = [_number(value) for value in root.get("viewBox", f"0 0 {svg_width} {svg_height}").split()]
        if (len(view_box) != 4 or abs(view_box[0]) > 1e-3 or abs(view_box[1]) > 1e-3
                or abs(view_box[2] - svg_width) > 1e-3 or abs(view_box[3] - svg_height) > 1e-3):
            return png
        if abs(scale_x - scale_y) > max(scale_x, scale_y) * 0.01:
            return png

        changed = False
        for node, text, clip, (sx, sy, base_x, base_y) in _symbol_candidates(root):
            if sx <= 0 or sy <= 0:
                continue
            font_size = _number(node.get("font-size"))
            if font_size <= 0:
                continue
            pixel_font_size = max(1, round(font_size * scale_y * sy))
            family = node.get("font-family", "")
            font = _load_symbol_font(family, text, pixel_font_size)
            if font is None:
                continue
            glyph_box = font.getbbox(text, anchor="ls")
            if not glyph_box:
                continue

            left = (base_x + sx * glyph_box[0] / (scale_y * sy)) * scale_x
            top = (base_y + sy * glyph_box[1] / (scale_y * sy)) * scale_y
            right = (base_x + sx * glyph_box[2] / (scale_y * sy)) * scale_x
            bottom = (base_y + sy * glyph_box[3] / (scale_y * sy)) * scale_y
            cx, cy, cw, ch = clip
            clip_box = (cx * scale_x, cy * scale_y, (cx + cw) * scale_x, (cy + ch) * scale_y)
            expected = (
                max(0, math.floor(max(left, clip_box[0]) + 1)),
                max(0, math.floor(max(top, clip_box[1]) + 1)),
                min(image.width, math.ceil(min(right, clip_box[2]) - 1)),
                min(image.height, math.ceil(min(bottom, clip_box[3]) - 1)),
            )
            if expected[2] <= expected[0] or expected[3] <= expected[1]:
                continue
            # The SVG cell is symbol-only. Checking its full inset interior also
            # catches a correctly rendered glyph whose raster baseline differs
            # slightly from another backend's SVG metrics.
            cell_interior = (
                max(0, math.floor(clip_box[0] + 2)),
                max(0, math.floor(clip_box[1] + 2)),
                min(image.width, math.ceil(clip_box[2] - 2)),
                min(image.height, math.ceil(clip_box[3] - 2)),
            )
            region = image.crop(cell_interior).convert("RGB")
            difference = ImageChops.difference(region, Image.new("RGB", region.size, "white"))
            if difference.getbbox():
                continue

            # Draw the real Unicode glyph on a transparent layer at its SVG
            # baseline, then clip it to the same table-cell rectangle.
            local = Image.new("RGBA", (pixel_font_size * 5, pixel_font_size * 4), (0, 0, 0, 0))
            baseline_x = pixel_font_size
            baseline_y = pixel_font_size * 2
            fill = _parse_color(node.get("fill"))
            ImageDraw.Draw(local).text((baseline_x, baseline_y), text, font=font, fill=fill, anchor="ls")
            bounds = local.getbbox()
            if not bounds:
                continue
            glyph = local.crop(bounds)
            x_ratio = (scale_x * sx) / (scale_y * sy)
            if abs(x_ratio - 1.0) > 1e-3:
                glyph = glyph.resize((max(1, round(glyph.width * x_ratio)), glyph.height), Image.Resampling.LANCZOS)
            baseline_x = round((base_x * scale_x) - (baseline_x - bounds[0]) * x_ratio)
            baseline_y = round((base_y * scale_y) - (baseline_y - bounds[1]))
            target = (baseline_x, baseline_y)
            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            overlay.alpha_composite(glyph, dest=target)
            clip_mask = Image.new("L", image.size, 0)
            ImageDraw.Draw(clip_mask).rectangle(
                (math.floor(clip_box[0]), math.floor(clip_box[1]),
                 math.ceil(clip_box[2]), math.ceil(clip_box[3])), fill=255
            )
            overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), clip_mask))
            image = Image.alpha_composite(image, overlay)
            changed = True
        if not changed:
            return png
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False)
        return output.getvalue()
    except (ET.XMLSyntaxError, OSError, ValueError, TypeError, OverflowError):
        return png
