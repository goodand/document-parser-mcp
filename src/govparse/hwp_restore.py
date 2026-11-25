import base64
import json
import math
import re
from copy import deepcopy
from itertools import count
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree as E

clean = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
ns = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def el(name, **attrs):
    return E.Element(ns + name, {k: str(v) for k, v in attrs.items()})


def sub(node, name):
    return node.find(ns + name)


def restore_body(source: Path, destination: Path) -> None:
    """Write editable body paragraphs and nested tables from a govparse source IR."""
    ir = json.loads(source.read_text("utf8"))
    body = ir["body"]
    ids = count(100)
    with ZipFile(Path(__file__).with_name("blank_table.hwpx")) as template:
        section = E.fromstring(template.read("Contents/section0.xml"))
        if geometry := ir.get("page_geometry"):
            page = section.find(".//" + ns + "pagePr")
            for tag, target in (("page", page), ("margin", sub(page, "margin"))):
                for key, value in geometry[tag].items():
                    if key in target.attrib:
                        target.set(key, value)
        frame = section[-1]
        section.remove(frame)
        tbl = frame.find(".//" + ns + "tbl")
        row = sub(tbl, "tr")
        cellbase = sub(row, "tc")
        tbl.remove(row)
        sub(cellbase, "subList").remove(sub(sub(cellbase, "subList"), "p"))
        header = E.fromstring(template.read("Contents/header.xml"))
        pool = header.find(".//{*}borderFills")
        border_ids = []
        default_border = next(item for item in pool if item.get("id") == cellbase.get("borderFillIDRef"))
        next_id = count(1 + max(int(item.get("id")) for item in pool))
        for style in ir.get("cell_border_styles", []):
            border = deepcopy(default_border)
            border.set("id", str(next(next_id)))
            for side in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"):
                edge = border.find("{*}" + side)
                if side in style:
                    edge.attrib.clear()
                    edge.attrib.update(style[side])
            if "winBrush" in style:
                core = "{http://www.hancom.co.kr/hwpml/2011/core}"
                fill = border.find(core + "fillBrush")
                if fill is not None:
                    border.remove(fill)
                fill = E.SubElement(border, core + "fillBrush")
                E.SubElement(fill, core + "winBrush", style["winBrush"])
            pool.append(border)
            border_ids.append(border.get("id"))
        pool.set("itemCnt", str(len(pool)))
        for margin in (sub(tbl, "inMargin"), sub(cellbase, "cellMargin")):
            margin.attrib.update(dict(left="90", right="90", top="0", bottom="0"))

        char_props = header.find(".//{*}charProperties")
        base_char = next(prop for prop in char_props if prop.get("id") == "0")
        char_ids = {int(base_char.get("height")): "0"}
        next_char = count(1 + max(int(prop.get("id")) for prop in char_props))
        heights = ir.get("character_heights", {})

        def char_id(inline):
            height = heights.get(str(inline.get("raw_style_id")))
            if height is None:
                return "0"
            metrics = ir.get("character_metrics", {}).get(str(inline.get("raw_style_id")), {})
            key = (height, tuple((name, tuple(sorted(attrs.items()))) for name, attrs in sorted(metrics.items()))) if metrics else height
            if key not in char_ids:
                prop = deepcopy(base_char)
                prop.set("height", str(height))
                prop.set("id", str(next(next_char)))
                char_props.append(prop)
                for name, attributes in metrics.items():
                    child = prop.find("{*}" + name)
                    if child is not None:
                        child.attrib.update(attributes)
                char_ids[key] = prop.get("id")
                char_props.set("itemCnt", str(len(char_props)))
            return char_ids[key]

        para_props = header.find(".//{*}paraProperties")
        base_para = para_props.find("{*}paraPr[@id='0']")
        next_para = count(1 + max(int(prop.get("id")) for prop in para_props))
        para_ids = {}
        for ref, layout in ir.get("paragraph_layouts", {}).items():
            prop = deepcopy(base_para)
            prop.set("id", str(next(next_para)))
            prop.find("{*}align").attrib.update(layout["align"])
            for spacing in prop.findall(".//{*}lineSpacing"):
                spacing.attrib.update(layout["spacing"])
            para_props.append(prop)
            para_ids[ref] = prop.get("id")
        para_props.set("itemCnt", str(len(para_props)))

        def paragraph(node, width, para_id="0"):
            p = el(
                "p", id=next(ids), paraPrIDRef=para_id, styleIDRef=0, pageBreak=int(bool(node.get("page_break_before"))), columnBreak=0, merged=0
            )
            run = el("run", charPrIDRef=0)
            p.append(run)
            if node["kind"] == "table" and node["cells"]:
                table = deepcopy(tbl)
                run.append(table)
                cells = node["cells"]
                cols = node["cols"]
                rows = node["rows"]
                table.attrib.update(dict(id=str(next(ids)), colCnt=str(cols), rowCnt=str(rows)))
                native_attrs = node.get("native_geometry", {}).get("table_attributes", {})
                for key in ("pageBreak", "repeatHeader", "cellSpacing"):
                    if key in native_attrs:
                        table.set(key, native_attrs[key])
                sub(table, "pos").set("treatAsChar", "0")
                sub(table, "sz").attrib.update(dict(width=str(width), height=str(rows * 950)))
                for tag in ("sz", "pos", "inMargin", "outMargin"):
                    saved = node.get("native_geometry", {}).get(tag, {})
                    target = sub(table, tag)
                    for attr in set(target.attrib) & saved.keys():
                        target.set(attr, saved[attr])
                width = int(sub(table, "sz").get("width"))
                weights = [1.0] * cols
                for c in cells:
                    if c["col_span"] == 1:
                        length = sum(len(b.get("text", "")) for b in c["blocks"])
                        weights[c["col"]] = max(weights[c["col"]], min(8, 1 + math.sqrt(length)))
                edges = [0]
                for weight in weights:
                    edges.append(edges[-1] + weight)
                edges = [round(width * e / edges[-1]) for e in edges]
                lines = [el("tr") for _ in range(rows)]
                table.extend(lines)
                for c in cells:
                    i, j = c["row"], c["col"]
                    h, w = c["row_span"], c["col_span"]
                    cw = max(1, edges[j + w] - edges[j])
                    cell = deepcopy(cellbase)
                    if "border_style_index" in c:
                        cell.set("borderFillIDRef", border_ids[c["border_style_index"]])
                    lines[i].append(cell)
                    sub(cell, "cellAddr").attrib.update(dict(rowAddr=str(i), colAddr=str(j)))
                    sub(cell, "cellSpan").attrib.update(dict(rowSpan=str(h), colSpan=str(w)))
                    sub(cell, "cellSz").attrib.update(dict(width=str(cw), height=str(h * 950)))
                    geometry = c.get("native_geometry", {})
                    if "header" in geometry:
                        cell.set("header", geometry["header"])
                    if "hasMargin" in geometry:
                        cell.set("hasMargin", geometry["hasMargin"])
                    for tag, fields in (("cellSz", ("width", "height")),
                                        ("cellMargin", ("left", "right", "top", "bottom"))):
                        saved = geometry.get(tag, {})
                        for field in fields:
                            if field in saved:
                                sub(cell, tag).set(field, str(int(saved[field])))
                    cw = int(sub(cell, "cellSz").get("width"))
                    blocks = c["blocks"] or [dict(kind="paragraph", text="")]
                    refs = geometry.get("paragraph_refs", [])
                    for block_index, b in enumerate(blocks):
                        para_id = para_ids.get(refs[block_index], "0") if len(refs) == len(blocks) else "0"
                        block_paragraph = paragraph(b, max(1, cw - 180), para_id)
                        if b.get("inline_after_paragraph") and b.get("native_picture") and block_index:
                            previous = sub(cell, "subList")[-1]
                            previous.insert(len(previous.findall(ns + "run")), sub(block_paragraph, "run"))
                            continue
                        anchor_lines = geometry.get("table_anchor_lines", {}).get(str(block_index))
                        vectors = geometry.get("vector_anchors", {}).get(str(block_index), [])
                        if vectors and b["kind"] == "paragraph" and not b.get("text"):
                            vector_run = sub(block_paragraph, "run")
                            for index, xml in enumerate(vectors):
                                shape = E.fromstring(xml.encode("utf8"))
                                for component in shape.iter():
                                    if component.get("id") is not None:
                                        component.set("id", str(next(ids)))
                                vector_run.insert(index, shape)
                        if anchor_lines and not b.get("picture_cell_lines"):
                            array = el("linesegarray")
                            for line in anchor_lines:
                                array.append(el("lineseg", **line))
                            block_paragraph.append(array)
                        sub(cell, "subList").append(block_paragraph)
            elif node["kind"] == "picture" and node.get("native_picture"):
                picture = E.fromstring(node["native_picture"].encode("utf8"))
                picture.set("id", str(next(ids)))
                run.append(picture)
            else:
                value = node.get("text") or node.get("description") or ""
                inlines = node.get("inlines") or []
                if heights and inlines and "".join(i["text"] for i in inlines) == value:
                    p.remove(run)
                    for inline in inlines:
                        styled = el("run", charPrIDRef=char_id(inline))
                        t = el("t")
                        t.text = clean.sub("", inline["text"])
                        styled.append(t)
                        p.append(styled)
                else:
                    t = el("t")
                    t.text = clean.sub("", value)
                    run.append(t)
            if node.get("picture_cell_lines"):
                array = el("linesegarray")
                for line in node["picture_cell_lines"]:
                    array.append(el("lineseg", **line))
                p.append(array)
            return p

        geometries = ir.get("section_page_geometry", {})
        sections = {0: section}
        if geometries:
            for info in ir["sections"]:
                idx = info["section_idx"]
                if idx:
                    sections[idx] = deepcopy(section)
            for idx, part in sections.items():
                geometry = geometries.get(str(idx))
                if geometry:
                    page = part.find(".//" + ns + "pagePr")
                    page.attrib.update(geometry["page"])
                    sub(page, "margin").attrib.update(geometry["margin"])
        for b in body:
            if (b.get("section_setup_only") or b.get("table_anchor_only")) and b.get("kind") == "paragraph" and not b.get("text"):
                continue
            idx = (b.get("prov") or {}).get("section_idx", 0)
            sections.get(idx, section).append(paragraph(b, 42520))

        package = E.fromstring(template.read("Contents/content.hpf"))
        manifest = package.find("{*}manifest")
        spine = package.find("{*}spine")
        for idx in sections:
            if idx:
                item = deepcopy(manifest.find("{*}item[@id='section0']"))
                item.set("id", f"section{idx}")
                item.set("href", f"Contents/section{idx}.xml")
                manifest.append(item)
                ref = deepcopy(spine.find("{*}itemref[@idref='section0']"))
                ref.set("idref", f"section{idx}")
                spine.append(ref)
        binaries = {}
        for ref, asset in ir.get("picture_assets", {}).items():
            attrs = asset["item"]
            href = attrs["href"]
            if attrs["id"] != ref or not re.fullmatch(r"BinData/[\w.-]+\.(?:bmp|png|jpeg|jpg|gif|wmf)", href, re.I):
                raise ValueError("Invalid picture asset reference")
            E.SubElement(manifest, manifest[0].tag, attrs)
            binaries[href] = base64.b64decode(asset["bytes"], validate=True)
        header.set("secCnt", str(len(sections)))
        with ZipFile(destination, "w") as out:
            for item in template.infolist():
                data = template.read(item)
                if item.filename == "Contents/section0.xml":
                    data = E.tostring(section, encoding="UTF-8", xml_declaration=True)
                elif item.filename == "Contents/header.xml":
                    data = E.tostring(header, encoding="UTF-8", xml_declaration=True)
                elif item.filename == "Contents/content.hpf" and (len(sections) > 1 or binaries):
                    data = E.tostring(package, encoding="UTF-8", xml_declaration=True)
                out.writestr(item, data)
            for href, binary in binaries.items():
                out.writestr(href, binary, compress_type=ZIP_DEFLATED)
            for idx, part in sections.items():
                if idx:
                    out.writestr(f"Contents/section{idx}.xml",
                                 E.tostring(part, encoding="UTF-8", xml_declaration=True),
                                 compress_type=ZIP_DEFLATED)
