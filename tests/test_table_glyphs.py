from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops

from govparse.table_glyphs import _load_symbol_font, repair_missing_symbol_glyphs


def _blank_png(size=(100, 60)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, "white").save(stream, format="PNG")
    return stream.getvalue()


def _svg(*, ancestor_transform: str = "", text: str = "➜") -> str:
    transform = f' transform="{ancestor_transform}"' if ancestor_transform else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60" viewBox="0 0 100 60">
      <defs><clipPath id="cell-clip-0"><rect x="10" y="10" width="60" height="40"/></clipPath></defs>
      <g{transform} clip-path="url(#cell-clip-0)"><text x="20" y="35" font-size="20" font-family="Segoe UI Symbol">{text}</text></g>
    </svg>'''


def _ink_bbox(png: bytes):
    image = Image.open(io.BytesIO(png)).convert("RGB")
    return ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()


@pytest.mark.parametrize("symbol", ["➜", "→"])
def test_blank_symbol_cell_gets_real_glyph_when_font_is_available(symbol):
    if _load_symbol_font("Segoe UI Symbol", symbol, 20) is None:
        pytest.skip(f"No installed font with a verified {symbol!r} glyph")
    original = _blank_png()
    repaired = repair_missing_symbol_glyphs(original, _svg(text=symbol))
    assert repaired != original

    bbox = _ink_bbox(repaired)
    assert bbox is not None
    assert 10 <= bbox[0] < bbox[2] <= 70
    assert 10 <= bbox[1] < bbox[3] <= 50
    # Existing ink in the symbol cell must never be painted over or duplicated.
    assert repair_missing_symbol_glyphs(repaired, _svg(text=symbol)) == repaired


def test_non_symbol_text_is_unchanged():
    original = _blank_png()
    assert repair_missing_symbol_glyphs(original, _svg(text="A")) == original


def test_unsupported_ancestor_transform_fails_closed():
    original = _blank_png()
    assert repair_missing_symbol_glyphs(
        original, _svg(ancestor_transform="matrix(1 0 0 1 2 0)")
    ) == original


def test_nonzero_viewbox_origin_fails_closed():
    original = _blank_png()
    svg = _svg().replace('viewBox="0 0 100 60"', 'viewBox="1 0 100 60"')
    assert repair_missing_symbol_glyphs(original, svg) == original
