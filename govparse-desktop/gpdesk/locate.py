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
        page.images.append(SvgImage((x, y, x + w, y + h), hashlib.sha256(data).hexdigest()))
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


def locate(links, renderer, renderer_error: str | None = None, max_pages: int | None = None):
    """Fill link.location in place. `renderer` is an rhwp Document (or None)."""
    if renderer is None:
        note = f"원본 화면 위치 미확인: 미리보기 렌더러가 문서를 열지 못함 ({(renderer_error or '')[:80]})"
        for l in links:
            l.location = {"verified": False, "note": note, "candidates": 0}
        return
    pages = []
    n = renderer.page_count if max_pages is None else min(renderer.page_count, max_pages)
    for i in range(n):
        try:
            pages.append(parse_svg_page(renderer.render_svg(i)))
        except Exception:  # noqa: BLE001 - a page that fails to render proves nothing
            pages.append(None)
    seen: dict[str, list[tuple[int, SvgImage]]] = {}
    for i, p in enumerate(pages):
        for img in (p.images if p else []):
            seen.setdefault(img.sha256, []).append((i, img))
    per_asset: dict[str | None, int] = {}
    for l in links:
        per_asset[l.occ.asset_sha256] = per_asset.get(l.occ.asset_sha256, 0) + 1
    for l in links:
        sha = l.occ.asset_sha256
        hits = seen.get(sha, []) if sha else []
        if not sha:
            l.location = {"verified": False, "note": "원본 화면 위치 미확인: 그림 바이트 없음", "candidates": 0}
        elif len(hits) == 1 and per_asset[sha] == 1:
            i, img = hits[0]
            cap_box = None
            for text in ([l.caption_text] if l.caption_text else []) + \
                        [c.text for c in l.candidates if c.proposed][:1]:
                cap_box = find_text_box(pages[i], text)
                if cap_box:
                    break
            l.location = {"verified": True, "page_index": i, "figure_box": img.rect,
                          "caption_box": cap_box, "page_size": (pages[i].width, pages[i].height),
                          "note": "렌더러 쪽 인덱스(인쇄 쪽번호 아님)" + ("" if cap_box else " · 설명 위치 미확인"),
                          "candidates": 1}
        elif per_asset[sha] > 1:
            l.location = {"verified": False, "candidates": len(hits),
                          "note": f"원본 화면 위치 미확인: 같은 그림이 문서에서 재사용됨 (렌더 후보 {len(hits)}곳)"}
        else:
            l.location = {"verified": False, "candidates": len(hits),
                          "note": "원본 화면 위치 미확인: 렌더 결과에서 그림을 찾지 못함" if not hits
                          else f"원본 화면 위치 미확인: 렌더 후보 {len(hits)}곳"}
