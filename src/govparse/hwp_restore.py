import json
import math
import re
from copy import deepcopy
from itertools import count
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E

clean = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
ns = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def el(name, **attrs):
    return E.Element(ns + name, {k: str(v) for k, v in attrs.items()})


def sub(node, name):
    return node.find(ns + name)


def restore_body(source: Path, destination: Path) -> None:
    """Write editable body paragraphs and nested tables from a govparse source IR."""
    body = json.loads(source.read_text("utf8"))["body"]
    ids = count(100)
    with ZipFile(Path(__file__).with_name("blank_table.hwpx")) as template:
        section = E.fromstring(template.read("Contents/section0.xml"))
        frame = section[-1]
        section.remove(frame)
        tbl = frame.find(".//" + ns + "tbl")
        row = sub(tbl, "tr")
        cellbase = sub(row, "tc")
        tbl.remove(row)
        sub(cellbase, "subList").remove(sub(sub(cellbase, "subList"), "p"))
        for margin in (sub(tbl, "inMargin"), sub(cellbase, "cellMargin")):
            margin.attrib.update(dict(left="90", right="90", top="0", bottom="0"))

        def paragraph(node, width):
            p = el(
                "p", id=next(ids), paraPrIDRef=0, styleIDRef=0, pageBreak=0, columnBreak=0, merged=0
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
                sub(table, "pos").set("treatAsChar", "0")
                sub(table, "sz").attrib.update(dict(width=str(width), height=str(rows * 950)))
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
                    lines[i].append(cell)
                    sub(cell, "cellAddr").attrib.update(dict(rowAddr=str(i), colAddr=str(j)))
                    sub(cell, "cellSpan").attrib.update(dict(rowSpan=str(h), colSpan=str(w)))
                    sub(cell, "cellSz").attrib.update(dict(width=str(cw), height=str(h * 950)))
                    blocks = c["blocks"] or [dict(kind="paragraph", text="")]
                    for b in blocks:
                        sub(cell, "subList").append(paragraph(b, max(1, cw - 180)))
            else:
                t = el("t")
                t.text = clean.sub("", node.get("text") or node.get("description") or "")
                run.append(t)
            return p

        for b in body:
            section.append(paragraph(b, 42520))
        with ZipFile(destination, "w") as out:
            for item in template.infolist():
                data = (
                    E.tostring(section, encoding="utf-8", xml_declaration=True)
                    if item.filename == "Contents/section0.xml"
                    else template.read(item)
                )
                out.writestr(item, data)
