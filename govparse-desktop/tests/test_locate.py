from pathlib import Path

import pytest

from gpdesk.figures import DecisionStore, extract, link_figures
from gpdesk.locate import find_text_box, locate, parse_svg_page

FIX = Path(__file__).resolve().parents[1] / "samples" / "synthetic-figures.hwpx"

SVG = ('<svg width="100" height="200">'
       '<image x="10" y="20" width="30" height="40" href="data:image/png;base64,QUJD"/>'
       '<text x="10" y="70" font-size="10">&lt;</text><text x="20" y="70" font-size="10">그</text>'
       '<text x="30" y="70" font-size="10">림</text><text x="40" y="70" font-size="10">&gt;</text>'
       '</svg>')


def test_parse_svg_page():
    page = parse_svg_page(SVG)
    assert page.width == 100 and len(page.images) == 1
    img = page.images[0]
    assert img.rect == (10.0, 20.0, 40.0, 60.0)
    assert img.sha256 == "b5d4045c3f466fa91fe2cc6abe79232a1a57cdf104f7a26e716e0a1e2789df78"  # sha256("ABC")


def test_find_text_box_unique_only():
    page = parse_svg_page(SVG)
    box = find_text_box(page, "< 그림 >")
    assert box is not None and box[0] == 10.0
    assert find_text_box(page, "없는 글자") is None


class FakeRenderer:
    """Stands in for rhwp: one page with the given svg."""

    def __init__(self, svg):
        self.svg = svg
        self.page_count = 1

    def render_svg(self, i):
        return self.svg


def test_locate_unique_vs_reused():
    rhwp = pytest.importorskip("rhwp")
    doc = extract(FIX)
    links = link_figures(doc, DecisionStore.memory(doc.source_sha256))
    locate(links, rhwp.parse(str(FIX)))
    loc = [l.location for l in links]
    # image1/2 are reused twice: never verified by order guessing
    assert [l["verified"] for l in loc] == [False, False, True, False, False, True]
    assert loc[0]["candidates"] == 2 and "재사용" in loc[0]["note"]
    assert loc[5]["page_index"] == 0 and loc[5]["figure_box"]


def test_locate_renderer_failure_marks_all_unverified():
    doc = extract(FIX)
    links = link_figures(doc, DecisionStore.memory(doc.source_sha256))
    locate(links, None, renderer_error="parse failed: entry exceeds limit")
    assert all(not l.location["verified"] for l in links)
    assert "rhwp" in links[0].location["note"]


def _png_and_pnm(color):
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 4), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png"), pix.tobytes("pnm")


class _Occ:
    def __init__(self, sha, occ_id):
        self.asset_sha256, self.occ_id = sha, occ_id


class _Link:
    def __init__(self, sha, occ_id):
        self.occ, self.caption_text, self.candidates, self.location = _Occ(sha, occ_id), "", [], {}


def test_locate_matches_by_pixels_when_bytes_differ():
    """HWP source: picture list reads a Hancom-made HWPX (BMP kept), rhwp's SVG embeds a PNG of the same pixels."""
    import base64
    import hashlib

    png, pnm = _png_and_pnm((10, 120, 200))
    svg = ('<svg width="100" height="200"><image x="5" y="6" width="8" height="4" '
           f'href="data:image/png;base64,{base64.b64encode(png).decode()}"/></svg>')
    sha = hashlib.sha256(pnm).hexdigest()
    link = _Link(sha, "o1")
    locate([link], FakeRenderer(svg), asset_bytes={sha: pnm})
    assert link.location["verified"] is True and link.location["page_index"] == 0
    assert "픽셀" in link.location["match"]


def test_reused_picture_lists_candidate_pages():
    import base64
    import hashlib

    png, _ = _png_and_pnm((200, 10, 10))
    img = f'<image x="1" y="1" width="8" height="4" href="data:image/png;base64,{base64.b64encode(png).decode()}"/>'

    class TwoPages:
        page_count = 3

        def render_svg(self, i):
            return f'<svg width="100" height="100">{img if i in (0, 2) else ""}</svg>'

    sha = hashlib.sha256(png).hexdigest()
    links = [_Link(sha, "a"), _Link(sha, "b")]
    locate(links, TwoPages(), asset_bytes={sha: png})
    assert all(not l.location["verified"] for l in links)
    assert links[0].location["candidate_pages"] == [0, 2]
