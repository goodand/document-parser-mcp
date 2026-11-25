"""Build samples/synthetic-figures.hwpx (public, synthetic) from the Hancom-made base.

Cases (ground truth lives in tests/test_figures.py):
  O-body   image1 in body after "그림 한 장" paragraph, no caption
  O-nest00 image1 reused in nested cell (0,0) with an explicit hp:caption
  O-nest10a/b image2 + image3 in nested cell (1,0), then one "<그림 B> …" paragraph (shared)
  O-nest11 image2 reused in nested cell (1,1) next to "그림입니다." and a file name line
  outer cell (1,2) keeps the unrelated sentence "이십오 개"
  O-frame  image4 alone in a 1x1 frame table after "<그림 C> 틀 표 속 그림"
"""

from __future__ import annotations

import copy
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_synthetic import png  # noqa: E402

HERE = Path(__file__).resolve().parent


def text_png(text: str) -> bytes:
    """A picture that contains text, for per-image OCR checks."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=70)
    page.draw_rect(page.rect, color=(0, 0.39, 0.43), fill=(0.93, 0.96, 0.96), width=2)
    page.insert_font(fontname="mg", fontfile=r"C:\Windows\Fonts\malgun.ttf")
    page.insert_text((16, 44), text, fontname="mg", fontsize=22)
    return page.get_pixmap(dpi=120).tobytes("png")
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
_ids = iter(range(1700000001, 1700009999))


def text_p(template_p, text: str):
    p = etree.Element(HP + "p", {k: v for k, v in template_p.attrib.items()})
    run = etree.SubElement(p, HP + "run", charPrIDRef=template_p.find(HP + "run").get("charPrIDRef"))
    t = etree.SubElement(run, HP + "t")
    t.text = text
    return p


def pic_p(pic_para, ref: str, caption: str | None = None):
    p = copy.deepcopy(pic_para)
    pic = p.find(f".//{HP}pic")
    pic.set("id", str(next(_ids)))
    pic.set("instid", str(next(_ids)))
    pic.find(f".//{HC}img").set("binaryItemIDRef", ref)
    for sc in pic.findall(HP + "shapeComment"):
        pic.remove(sc)
    if caption is not None:
        cap = etree.SubElement(pic, HP + "caption", side="BOTTOM", fullSz="0", width="8504",
                               gap="850", lastWidth="9000")
        sub = etree.SubElement(cap, HP + "subList", id="", textDirection="HORIZONTAL",
                               lineWrap="BREAK", vertAlign="TOP", linkListIDRef="0",
                               linkListNextIDRef="0", textWidth="0", textHeight="0",
                               hasTextRef="0", hasNumRef="0")
        cp = etree.SubElement(sub, HP + "p", id="2147483648", paraPrIDRef="0", styleIDRef="0",
                              pageBreak="0", columnBreak="0", merged="0")
        etree.SubElement(etree.SubElement(cp, HP + "run", charPrIDRef="0"), HP + "t").text = caption
    return p


def cell(tbl, r: int, c: int):
    for tc in tbl.iter(HP + "tc"):
        a = tc.find(HP + "cellAddr")
        if tc.getparent().getparent() is tbl and a.get("rowAddr") == str(r) and a.get("colAddr") == str(c):
            return tc.find(HP + "subList")
    raise KeyError((r, c))


def build(base: Path, out: Path) -> None:
    zin = zipfile.ZipFile(base)
    sec = etree.fromstring(zin.read("Contents/section0.xml"))
    pic_para = next(a for a in next(sec.iter(HP + "pic")).iterancestors() if a.tag == HP + "p")
    tmpl = next(p for p in sec.iter(HP + "p") if p.find(HP + "run") is not None
                and p.find(f".//{HP}t") is not None and p.find(f".//{HP}pic") is None)
    outer = next(sec.iter(HP + "tbl"))
    inner = next(t for t in outer.iter(HP + "tbl") if t is not outer)
    c00, c10, c11 = cell(inner, 0, 0), cell(inner, 1, 0), cell(inner, 1, 1)
    c00.append(pic_p(pic_para, "image1", "<그림 A> 사업 구성도"))
    c10.append(pic_p(pic_para, "image2"))
    c10.append(pic_p(pic_para, "image3"))
    c10.append(text_p(tmpl, "<그림 B> 두 그림의 공통 설명"))
    c11.append(text_p(tmpl, "그림입니다."))
    c11.append(pic_p(pic_para, "image2"))
    c11.append(text_p(tmpl, "photo_0042.png"))
    frame = copy.deepcopy(inner)
    frame.set("id", str(next(_ids)))
    frame.set("rowCnt", "1")
    frame.set("colCnt", "1")
    for tr in frame.findall(HP + "tr")[1:]:
        frame.remove(tr)
    tr0 = frame.find(HP + "tr")
    for tc in tr0.findall(HP + "tc")[1:]:
        tr0.remove(tc)
    fsub = tr0.find(HP + "tc").find(HP + "subList")
    for old in list(fsub):
        fsub.remove(old)
    fsub.append(pic_p(pic_para, "image4"))
    host = text_p(tmpl, "")
    host.find(HP + "run").append(frame)
    tail = list(sec.findall(HP + "p"))[-1]
    tail.addprevious(text_p(tmpl, "<그림 C> 틀 표 속 그림"))
    tail.addprevious(host)
    data = etree.tostring(sec, xml_declaration=True, encoding="UTF-8", standalone=True)
    hpf = zin.read("Contents/content.hpf").decode("utf-8")
    extra = "".join(f'<opf:item id="image{i}" href="BinData/image{i}.png" media-type="image/png" '
                    f'isEmbeded="1"/>' for i in (2, 3, 4))
    hpf = re.sub(r'(<opf:item id="image1"[^>]*/>)', r"\1" + extra, hpf)
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            body = zin.read(info)
            if info.filename == "Contents/section0.xml":
                body = data
            elif info.filename == "Contents/content.hpf":
                body = hpf.encode("utf-8")
            zout.writestr(info, body)
        zout.writestr("BinData/image2.png", png(80, 40, (200, 120, 0)))
        zout.writestr("BinData/image3.png", png(60, 60, (40, 40, 160)))
        zout.writestr("BinData/image4.png", text_png("사업 추진 체계 2026"))


if __name__ == "__main__":
    build(HERE / "synthetic-merged.hwpx", HERE / "synthetic-figures.hwpx")
    print("ok")
