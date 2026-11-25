"""Where a picture occurrence is on the rendered page — only when it can be proven.

The existing preview renderer (rhwp) also renders SVG; its <image> elements carry
the original picture bytes. A location is *verified* only when the occurrence's
asset hash appears exactly once in the whole rendered document AND the asset has
exactly one occurrence. Reused assets are never matched by order ("위치 미확인,
후보 N곳"). Page indices are renderer indices (0-based), not printed page numbers.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from html import unescape

_IMG = re.compile(r'<image\b([^>]*)/?>')
_TXT = re.compile(r'<text\b([^>]*)>([^<]*)</text>')
_ATTR = re.compile(r'([\w:-]+)="([^"]*)"')


@dataclass
class SvgImage:
    rect: tuple[float, float, float, float]
    sha256: str
    data: bytes = field(default=b"", repr=False)
    _pixels: str | None = field(default=None, repr=False)

    @property
    def pixels(self) -> str | None:
        """Hash of the decoded picture (size + RGB samples), independent of the file format."""
        if self._pixels is None:
            self._pixels = pixel_hash(self.data) or ""
        return self._pixels or None


def pixel_hash(data: bytes) -> str | None:
    try:
        import pymupdf

        pix = pymupdf.Pixmap(data)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        if pix.colorspace is None or pix.colorspace.n != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        return hashlib.sha256(f"{pix.width}x{pix.height}:".encode() + pix.samples).hexdigest()
    except Exception:  # noqa: BLE001 - formats PyMuPDF cannot decode (wmf/emf) simply do not match
        return None


@dataclass
class SvgPage:
    width: float
    height: float
    images: list[SvgImage] = field(default_factory=list)
    glyphs: list[tuple[float, float, float, str]] = field(default_factory=list)  # x, y, size, ch


def parse_svg_page(svg: str) -> SvgPage:
    head = dict(_ATTR.findall(svg[: svg.find(">") + 1]))
    page = SvgPage(float(head.get("width", 0)), float(head.get("height", 0)))
    for m in _IMG.finditer(svg):
        a = dict(_ATTR.findall(m.group(1)))
        href = a.get("href") or a.get("xlink:href") or ""
        if not href.startswith("data:") or ";base64," not in href:
            continue
        data = base64.b64decode(href.split(";base64,", 1)[1])
        x, y, w, h = (float(a.get(k, 0)) for k in ("x", "y", "width", "height"))
        page.images.append(SvgImage((x, y, x + w, y + h), hashlib.sha256(data).hexdigest(), data))
    for m in _TXT.finditer(svg):
        a = dict(_ATTR.findall(m.group(1)))
        ch = unescape(m.group(2))
        if ch.strip():
            page.glyphs.append((float(a.get("x", 0)), float(a.get("y", 0)),
                                float(a.get("font-size", 10)), ch.strip()))
    return page


def find_text_box(page: SvgPage, text: str, max_chars: int = 14):
    """Bounding box of `text` (spaces ignored) if it occurs exactly once on the page."""
    want = re.sub(r"\s+", "", text or "")[:max_chars]
    if len(want) < 2:
        return None
    chars = "".join(g[3] for g in page.glyphs)
    starts = [m.start() for m in re.finditer(re.escape(want), chars)]
    if len(starts) != 1:
        return None
    # map char offsets back to glyphs (each glyph may hold >1 char)
    spans, pos = [], 0
    for g in page.glyphs:
        spans.append((pos, pos + len(g[3]), g))
        pos += len(g[3])
    hit = [g for a, b, g in spans if a < starts[0] + len(want) and b > starts[0]]
    x0 = min(g[0] for g in hit)
    x1 = max(g[0] + g[2] * len(g[3]) for g in hit)
    y0 = min(g[1] - g[2] for g in hit)
    y1 = max(g[1] + g[2] * 0.25 for g in hit)
    return (x0, y0, x1, y1)


def locate(links, renderer, renderer_error: str | None = None, max_pages: int | None = None,
           asset_bytes: dict[str, bytes] | None = None):
    """Fill link.location in place. `renderer` is an rhwp Document (or None).

    asset_bytes (asset sha256 -> original bytes) enables a second, format-independent match on decoded
    pixels: for an HWP source the picture list reads a Hancom-made HWPX copy while rhwp's SVG embeds
    its own PNG, so the bytes differ even when the picture is the same.
    """
    if renderer is None:
        note = f"원본 화면 위치 미확인: 미리보기 프로그램(rhwp)이 문서를 열지 못함 ({(renderer_error or '')[:80]})"
        for l in links:
            l.location = {"verified": False, "note": note, "candidates": 0, "candidate_pages": []}
        return
    pages = []
    n = renderer.page_count if max_pages is None else min(renderer.page_count, max_pages)
    for i in range(n):
        try:
            pages.append(parse_svg_page(renderer.render_svg(i)))
        except Exception:  # noqa: BLE001 - a page that fails to render proves nothing
            pages.append(None)
    by_bytes: dict[str, list[tuple[int, SvgImage]]] = {}
    for i, p in enumerate(pages):
        for img in (p.images if p else []):
            by_bytes.setdefault(img.sha256, []).append((i, img))
    by_pixels: dict[str, list[tuple[int, SvgImage]]] | None = None
    per_asset: dict[str | None, int] = {}
    for l in links:
        per_asset[l.occ.asset_sha256] = per_asset.get(l.occ.asset_sha256, 0) + 1
    for l in links:
        sha = l.occ.asset_sha256
        hits, match = (by_bytes.get(sha, []) if sha else []), "파일 바이트 일치"
        if sha and not hits and asset_bytes and sha in asset_bytes:
            if by_pixels is None:  # decode lazily, once, only when some picture needs it
                by_pixels = {}
                for i, p in enumerate(pages):
                    for img in (p.images if p else []):
                        if img.pixels:
                            by_pixels.setdefault(img.pixels, []).append((i, img))
            ph = pixel_hash(asset_bytes[sha])
            hits, match = (by_pixels.get(ph, []) if ph else []), "픽셀 일치(파일 형식은 다름)"
        candidate_pages = sorted({i for i, _ in hits})
        if not sha:
            l.location = {"verified": False, "note": "원본 화면 위치 미확인: 그림 바이트 없음",
                          "candidates": 0, "candidate_pages": []}
        elif len(hits) == 1 and per_asset[sha] == 1:
            i, img = hits[0]
            cap_box = None
            for text in ([l.caption_text] if l.caption_text else []):   # only a real caption is boxed
                cap_box = find_text_box(pages[i], text)
                if cap_box:
                    break
            l.location = {"verified": True, "page_index": i, "figure_box": img.rect, "match": match,
                          "caption_box": cap_box, "page_size": (pages[i].width, pages[i].height),
                          "note": "미리보기 기준 쪽 (인쇄된 쪽 번호와 다를 수 있음)" + ("" if cap_box else " · 설명 위치 미확인"),
                          "candidates": 1, "candidate_pages": [i]}
        elif per_asset[sha] > 1:
            l.location = {"verified": False, "candidates": len(hits), "candidate_pages": candidate_pages,
                          "note": f"원본 화면 위치 미확인: 같은 그림이 문서에서 재사용됨 (미리보기 후보 {len(hits)}곳)"}
        else:
            l.location = {"verified": False, "candidates": len(hits), "candidate_pages": candidate_pages,
                          "note": "원본 화면 위치 미확인: 미리보기 화면에서 그림을 찾지 못함" if not hits
                          else f"원본 화면 위치 미확인: 미리보기 후보 {len(hits)}곳"}
