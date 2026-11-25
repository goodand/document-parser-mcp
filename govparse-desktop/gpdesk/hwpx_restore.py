"""Editable HWPX restore with pictures and their source captions.

Body text and nested tables come from the engine's verified ``restore_body``.
Each picture node is first written as a private-use marker paragraph, then the
marker is replaced by a real hp:pic (template made by Hancom from a synthetic
file) pointing at a BinData copy of the original bytes.

Captions: only a caption the source declared (hp:caption) is restored as a
caption. Person-made links are NOT turned into document text; they go to a
``<name>.links.json`` sidecar so the document never gains captions it did not have.
"""

from __future__ import annotations

import copy
import io
import itertools
import json
import mimetypes
import re
import zipfile
from pathlib import Path

from lxml import etree

# Pinned copy of the engine's restore_body (commit ea53268, see vendor/PROVENANCE.txt).
# The engine working tree is edited in parallel; never import it from there.
from .vendor.hwp_restore import restore_body

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
MARK = re.compile("GPPIC(\\d+)")
TEMPLATE = Path(__file__).with_name("pic_template.xml")
_ids = itertools.count(1800000001)


def _pictures(nodes, out):
    for n in nodes:
        if n.get("kind") == "picture":
            out.append(n)
        for c in n.get("cells", []) or []:
            _pictures(c.get("blocks", []), out)
    return out


def _ext(data: bytes) -> str:
    sig = {b"\x89PNG": "png", b"\xff\xd8": "jpg", b"GIF8": "gif", b"BM": "bmp"}
    for k, v in sig.items():
        if data.startswith(k):
            return v
    return "bin"


def _pic_element(ref: str, size, data: bytes, caption: str | None):
    pic = etree.fromstring(TEMPLATE.read_bytes())
    pic.set("id", str(next(_ids)))
    pic.set("instid", str(next(_ids)))
    pic.find(f"{HC}img").set("binaryItemIDRef", ref)
    if size:
        w, h = int(size[0]), int(size[1])
    else:  # fall back to pixel size at 96 dpi (75 HWPUNIT per px)
        w, h = 9000, 4500
        try:
            import pymupdf

            px = pymupdf.Pixmap(data)
            w, h = px.width * 75, px.height * 75
        except Exception:  # noqa: BLE001
            pass
    for tag in ("orgSz", "sz"):
        pic.find(HP + tag).attrib.update({"width": str(w), "height": str(h)})
    pts = pic.find(HP + "imgRect")
    for el, (x, y) in zip(pts, [(0, 0), (w, 0), (w, h), (0, h)]):
        el.attrib.update({"x": str(x), "y": str(y)})
    pic.find(HP + "rotationInfo").attrib.update({"centerX": str(w // 2), "centerY": str(h // 2)})
    if caption:
        cap = etree.SubElement(pic, HP + "caption", side="BOTTOM", fullSz="0", width="8504",
                               gap="850", lastWidth=str(w))
        sub = etree.SubElement(cap, HP + "subList", id="", textDirection="HORIZONTAL",
                               lineWrap="BREAK", vertAlign="TOP", linkListIDRef="0",
                               linkListNextIDRef="0", textWidth="0", textHeight="0",
                               hasTextRef="0", hasNumRef="0")
        p = etree.SubElement(sub, HP + "p", id="2147483648", paraPrIDRef="0", styleIDRef="0",
                             pageBreak="0", columnBreak="0", merged="0")
        etree.SubElement(etree.SubElement(p, HP + "run", charPrIDRef="0"), HP + "t").text = caption
    return pic


def restore_with_figures(ir: dict, asset_bytes: dict[str, bytes], out: Path) -> dict:
    ir = copy.deepcopy(ir)
    pics = _pictures(ir.get("body", []), [])
    plan = []
    for i, node in enumerate(pics):
        link = node.get("figure_link") or {}
        ref = link.get("asset_ref") or node.get("asset_ref") or ""
        if not ref:
            m = re.search(r"(\d+)$", (node.get("image") or {}).get("uri", ""))
            ref = f"image{m.group(1)}" if m else ""
        source_caption = node.get("caption_text") or (
            link.get("caption_text") if link.get("status") == "auto_explicit" else None)
        plan.append({"ref": ref, "size": link.get("size"), "caption": source_caption, "link": link})
        node["kind"] = "paragraph"
        node["text"] = f"GPPIC{i}"
    draft_json = out.with_suffix(".ir.tmp.json")
    draft = out.with_suffix(".draft.hwpx")
    draft_json.write_text(json.dumps(ir, ensure_ascii=False), "utf-8")
    try:
        restore_body(draft_json, draft)
    finally:
        draft_json.unlink(missing_ok=True)
    missing = []
    with zipfile.ZipFile(draft) as zin:
        sec = etree.fromstring(zin.read("Contents/section0.xml"))
        placed = 0
        for t in list(sec.iter(HP + "t")):
            m = MARK.fullmatch(t.text or "")
            if not m:
                continue
            item = plan[int(m.group(1))]
            data = asset_bytes.get(item["ref"])
            run = t.getparent()
            if not data:
                t.text = "[그림 없음: 원본 그림 바이트를 찾지 못함]"
                missing.append(item["ref"])
                continue
            run.replace(t, _pic_element(item["ref"], item["size"], data, item["caption"]))
            placed += 1
        used = {p["ref"] for p in plan if asset_bytes.get(p["ref"])}
        hpf = zin.read("Contents/content.hpf").decode("utf-8")
        items = "".join(
            f'<opf:item id="{r}" href="BinData/{r}.{_ext(asset_bytes[r])}" '
            f'media-type="{mimetypes.types_map.get("." + _ext(asset_bytes[r]), "application/octet-stream")}" isEmbeded="1"/>'
            for r in sorted(used))
        hpf = hpf.replace("<opf:manifest>", "<opf:manifest>" + items, 1)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zout:
            for info in zin.infolist():
                body = zin.read(info)
                if info.filename == "Contents/section0.xml":
                    body = etree.tostring(sec, xml_declaration=True, encoding="UTF-8", standalone=True)
                elif info.filename == "Contents/content.hpf":
                    body = hpf.encode("utf-8")
                zout.writestr(info, body)
            for r in sorted(used):
                zout.writestr(f"BinData/{r}.{_ext(asset_bytes[r])}", asset_bytes[r])
    draft.unlink(missing_ok=True)
    out.write_bytes(buf.getvalue())
    figures = [{"index": i + 1, "asset_ref": p["ref"], "occ_id": p["link"].get("occ_id"),
                "status": p["link"].get("status", "not_linked"),
                "caption_text": p["link"].get("caption_text", ""),
                "evidence": p["link"].get("evidence", ""),
                "restored_as_caption": bool(p["caption"])} for i, p in enumerate(plan)]
    out.with_suffix(".links.json").write_text(json.dumps({
        "note": "사용자가 정한 연결은 문서 본문에 캡션으로 넣지 않고 이 파일에만 기록합니다. "
                "restored_as_caption=true 는 원문이 명시한 캡션입니다.",
        "figures": figures}, ensure_ascii=False, indent=1), "utf-8")
    return {"pictures": placed, "missing_pictures": missing,
            "source_captions": sum(1 for p in plan if p["caption"]),
            "user_links_sidecar": sum(1 for f in figures if f["status"].startswith("user_"))}


def verify_restored(path: Path, expected_pictures: int) -> dict:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        hpf = z.read("Contents/content.hpf").decode("utf-8")
        manifest = dict(re.findall(r'<opf:item id="([^"]+)" href="([^"]+)"', hpf))
        pics, refs, nested = 0, [], 0
        for n in sorted(x for x in names if re.match(r"Contents/section\d+\.xml$", x)):
            sec = etree.fromstring(z.read(n), etree.XMLParser(huge_tree=True))
            for p in sec.iter(HP + "pic"):
                pics += 1
                refs.append(p.find(f".//{HC}img").get("binaryItemIDRef"))
            nested += sum(1 for t in sec.iter(HP + "tbl") if any(a.tag == HP + "tc" for a in t.iterancestors()))
    missing = sorted({r for r in refs if manifest.get(r) not in names})
    return {"pictures": pics, "expected": expected_pictures, "missing_bindata": missing,
            "nested_tables": nested, "ok": pics == expected_pictures and not missing}
