"""Compare table dimensions, merged cells and cell text at each nesting level."""
import json
import re
from zipfile import ZipFile

from lxml import etree as ET

HP = {"p": "http://www.hancom.co.kr/hwpml/2011/paragraph"}
VECTOR_TAGS = {
    f"{{{HP['p']}}}{kind}"
    for kind in ("polygon", "container", "line", "rect", "ellipse", "arc", "curve", "connectLine")
}


def is_inline_picture(blocks, index):
    """True only when a native image can be merged into an existing paragraph."""
    if not 0 <= index < len(blocks):
        return False
    block = blocks[index]
    return (
        block.get("kind") == "picture"
        and block.get("inline_after_paragraph")
        and bool(block.get("native_picture"))
        and index > 0
        and blocks[index - 1].get("kind") == "paragraph"
    )


def ir_records(blocks, parent=""):
    tables = (block for block in blocks if block.get("kind") == "table")
    for index, table in enumerate(tables):
        path = f"{parent}/table[{index}]"
        yield path, (table["rows"], table["cols"])
        for cell in table["cells"]:
            key = f"{path}/cell[{cell['row']},{cell['col']}]"
            contents = cell["blocks"]
            blocks_in_cell = tuple(
                ("table", "") if block["kind"] == "table" else ("paragraph", block.get("text", ""))
                for index, block in enumerate(contents)
                if not is_inline_picture(contents, index)
            )
            yield key, (cell["row_span"], cell["col_span"], blocks_in_cell)
            yield from ir_records(contents, key)


def text_content(element):
    """Decode fixed-width space controls in HWPX mixed text."""
    parts = [element.text or ""]
    for child in element:
        if child.tag == "{%s}fwSpace" % HP["p"]:
            parts.append("\u2007")
        elif child.tag == "{%s}tab" % HP["p"]:
            parts.append("\t")
        elif child.tag == "{%s}lineBreak" % HP["p"]:
            parts.append("\n")
        else:
            parts.append(text_content(child))
        parts.append(child.tail or "")
    return "".join(parts)


def cell_blocks(cell):
    for paragraph in cell.findall("p:subList/p:p", HP):
        yield from paragraph_blocks(paragraph)


def paragraph_blocks(paragraph):
    parts = []
    contains_table = False
    for child in paragraph.findall("p:run/*", HP):
        if child.tag == "{%s}t" % HP["p"]:
            parts.append(text_content(child))
        elif child.tag == "{%s}tbl" % HP["p"]:
            if any(parts):
                yield ("paragraph", "".join(parts))
            parts.clear()
            yield ("table", "")
            contains_table = True
    if any(parts) or not contains_table:
        yield ("paragraph", "".join(parts))


def xml_records(tables, parent="", border_styles=None, geometry=False):
    for index, table in enumerate(tables):
        path = f"{parent}/table[{index}]"
        if geometry:
            layout = {
                name: dict(node.attrib) for name in ("sz", "pos", "inMargin", "outMargin")
                if (node := table.find("p:" + name, HP)) is not None
            }
            layout["table_attributes"] = {
                key: table.get(key) for key in ("pageBreak", "repeatHeader", "cellSpacing")
                if key in table.attrib
            }
            yield path, layout
        if border_styles is None and not geometry:
            yield path, (int(table.get("rowCnt")), int(table.get("colCnt")))
        for cell in table.findall("p:tr/p:tc", HP):
            addr = cell.find("p:cellAddr", HP)
            span = cell.find("p:cellSpan", HP)
            key = f"{path}/cell[{addr.get('rowAddr')},{addr.get('colAddr')}]"
            runs = cell.findall("p:subList/p:p/p:run", HP)
            if geometry:
                layout = {name: dict(node.attrib) for name in ("cellSz", "cellMargin")
                            if (node := cell.find("p:" + name, HP)) is not None}
                layout["hasMargin"] = cell.get("hasMargin", "0")
                if "header" in cell.attrib:
                    layout["header"] = cell.get("header")
                paragraphs = cell.findall("p:subList/p:p", HP)
                layout["paragraph_refs"] = [
                    p.get("paraPrIDRef", "0") for p in paragraphs for _ in paragraph_blocks(p)
                ]
                vector_anchors = {}
                anchor_lines = {}
                offset = 0
                for paragraph in paragraphs:
                    blocks = list(paragraph_blocks(paragraph))
                    children = paragraph.findall("p:run/*", HP)
                    vectors = [child for child in children if child.tag in VECTOR_TAGS]
                    if (vectors and blocks == [("paragraph", "")] and
                            all(child.tag in VECTOR_TAGS or child.tag == f"{{{HP['p']}}}t"
                                for child in children) and
                            not any(n.get("binaryItemIDRef") or n.tag.endswith(("}drawText", "}subList", "}ole"))
                                    for shape in vectors for n in shape.iter())):
                        # Attached endpoints refer to other shape instances in the source document.
                        # Keep independent siblings when a connector or group needs those targets.
                        anchors = [
                            ET.tostring(shape, encoding="unicode", with_tail=False)
                            for shape in vectors
                            if all(node.get("subjectIDRef", "0") == "0" for node in shape.iter())
                        ]
                        if anchors:
                            vector_anchors[str(offset)] = anchors
                    simple_text = len(blocks) == 1 and all(child.tag == f"{{{HP['p']}}}t" for child in children)
                    if blocks == [("table", "")] or str(offset) in vector_anchors or simple_text:
                        lines = paragraph.findall("p:linesegarray/p:lineseg", HP)
                        if lines:
                            anchor_lines[str(offset)] = [dict(line.attrib) for line in lines]
                    offset += len(blocks)
                if vector_anchors:
                    layout["vector_anchors"] = vector_anchors
                if anchor_lines:
                    layout["table_anchor_lines"] = anchor_lines
                yield key, layout
            elif border_styles is not None:
                yield key, border_styles[cell.get("borderFillIDRef")]
            else:
                yield key, (int(span.get("rowSpan")), int(span.get("colSpan")), tuple(cell_blocks(cell)))
            yield from xml_records((t for run in runs for t in run.findall("p:tbl", HP)), key, border_styles, geometry)


def read_hwpx(path, *, borders=False, geometry=False):
    with ZipFile(path) as archive:
        sections = sorted((n for n in archive.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)),
                          key=lambda n: int(re.search(r"\d+", n).group()))
        def tables():
            for name in sections:
                root = ET.fromstring(archive.read(name))
                yield from root.findall("p:p/p:run/p:tbl", HP)
        styles = None
        if borders:
            header = ET.fromstring(archive.read("Contents/header.xml"))
            sides = ("leftBorder", "rightBorder", "topBorder", "bottomBorder")
            styles = {
                item.get("id"): tuple(
                    (side, tuple(sorted(edge.attrib.items())))
                    for side in (*sides, "winBrush")
                    for edge in item.findall("{*}fillBrush/{*}winBrush" if side == "winBrush" else "{*}" + side)
                )
                for item in header.findall(".//{*}borderFill")
            }
        return dict(xml_records(tables(), border_styles=styles, geometry=geometry))


def compare(expected, actual):
    count = 0
    examples = []
    for path in sorted(expected.keys() | actual.keys()):
        if expected.get(path) == actual.get(path):
            continue
        count += 1
        if len(examples) < 10:
            examples.append(path)
    return {"different_records": count, "example_paths": examples}


def audit_tables(source, parsed, restored):
    records = dict(ir_records(json.loads(parsed.read_text("utf8"))["body"]))
    converted_diff = None
    if source.suffix.lower() == ".hwp":
        from io import BytesIO
        from .hwp_source import _rhwp_parse

        converted = BytesIO(_rhwp_parse(source).to_hwpx_bytes())
        converted_diff = compare(read_hwpx(converted), records)
    border_diff = None
    if source.suffix.lower() == ".hwpx":
        try:
            border_diff = compare(read_hwpx(source, borders=True), read_hwpx(restored, borders=True))
        except KeyError as exc:
            border_diff = {"error": f"Missing HWPX border definition or package entry: {exc}"}
    return {
        "rhwp_export_to_json": converted_diff,
        "source_to_restored_cell_borders": border_diff,
        "scope": "body tables by ordinal, cell coordinates, spans, paragraph text and nested block order",
        "source_to_json": compare(read_hwpx(source), records) if source.suffix.lower() == ".hwpx" else None,
        "json_to_hwpx": compare(records, read_hwpx(restored)),
    }
def remove_table_placeholders(body, native):
    """Remove empty blocks only when the native cell confirms its full block order."""
    removed = 0

    def visit(blocks, parent):
        nonlocal removed
        for index, table in enumerate(b for b in blocks if b.get("kind") == "table"):
            path = f"{parent}/table[{index}]"
            if native.get(path) != (table["rows"], table["cols"]):
                continue
            for cell in table["cells"]:
                key = f"{path}/cell[{cell['row']},{cell['col']}]"
                ref = native.get(key)
                if ref is None or ref[:2] != (cell["row_span"], cell["col_span"]):
                    continue
                items = cell["blocks"]
                kinds = [(b["kind"], b.get("text", "") if b["kind"] != "table" else "") for b in items]
                if tuple(kinds) != ref[2]:
                    keep = []
                    offset = 0
                    for index, kind in enumerate(kinds):
                        if offset < len(ref[2]) and kind == ref[2][offset]:
                            keep.append(items[index])
                            offset += 1
                        elif kind != ("paragraph", "") or kinds[index + 1:index + 2] != [("table", "")]:
                            break
                    else:
                        if offset == len(ref[2]):
                            removed += len(items) - len(keep)
                            items[:] = keep
                if ("table", "") in ref[2] and tuple(kinds) != ref[2]:
                    # Keep each styled paragraph and nested table intact. Only interleave
                    # them when both sequences independently match the native cell.
                    tables = [b for b in items if b["kind"] == "table"]
                    text = [b for b in items if b["kind"] == "paragraph" and b.get("text")]
                    blanks = [b for b in items if b["kind"] == "paragraph" and not b.get("text")]
                    expected_text = [v for kind, v in ref[2] if kind == "paragraph" and v]
                    blank_count = ref[2].count(("paragraph", ""))
                    if (len(tables) + len(text) + len(blanks) == len(items)
                            and len(tables) == ref[2].count(("table", ""))
                            and all(native.get(f"{key}/table[{i}]") == (t["rows"], t["cols"])
                                    for i, t in enumerate(tables))
                            and [b["text"] for b in text] == expected_text
                            and len(blanks) >= blank_count):
                        table_iter, text_iter, blank_iter = map(iter, (tables, text, blanks))
                        ordered = [next(table_iter) if kind == "table" else
                                   next(text_iter) if value else next(blank_iter)
                                   for kind, value in ref[2]]
                        removed += len(items) - len(ordered)
                        items[:] = ordered
                visit(items, key)
    visit(body, "")
    return removed

def attach_cell_borders(ir, source):
    """Carry verified cell edge styles in the JSON used for reconstruction."""
    with ZipFile(source) as package:
        header = ET.fromstring(package.read("Contents/header.xml"))
        sections = [n for n in package.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)]
        section_blocks = {}
        for body_index, block in enumerate(ir["body"]):
            provenance = block.get("prov") or {}
            section_blocks.setdefault(provenance.get("section_idx"), []).append((body_index, block))
        geometries = {}
        for name in sections:
            section_index = int(re.search(r"section(\d+)\.xml$", name)[1])
            section = ET.fromstring(package.read(name))
            pages = section.findall(".//p:secPr/p:pagePr", HP)
            if len(pages) == 1 and (margin := pages[0].find("p:margin", HP)) is not None:
                geometries[str(section_index)] = {"page": dict(pages[0].attrib), "margin": dict(margin.attrib)}
            paragraphs = section.findall("p:p", HP)
            for body_index, block in section_blocks.get(section_index, []):
                prov = block.get("prov") or {}
                index = prov.get("para_idx")
                if (block.get("kind") != "paragraph"
                        or not isinstance(index, int) or not 0 <= index < len(paragraphs)):
                    continue
                original = paragraphs[index]
                children = original.findall("p:run/*", HP)
                if (index == 0 and not block.get("text") and children
                        and all(child.tag == f"{{{HP['p']}}}secPr" for child in children)):
                    block["section_setup_only"] = True
                following = ir["body"][body_index + 1:body_index + 2]
                if (not block.get("text") and len(children) == 1 and children[0].tag == f"{{{HP['p']}}}tbl"
                        and following and following[0]["kind"] == "table"
                        and (following[0].get("prov") or {}).get("para_idx") == index
                        and (following[0].get("prov") or {}).get("section_idx") == section_index
                        and original.get("pageBreak", "0") == "0"):
                    block["table_anchor_only"] = True
                if original.get("pageBreak") == "1":
                    text = "".join(text_content(t) for t in original.findall("p:run/p:t", HP))
                    if text == block.get("text"):
                        block["page_break_before"] = True
        if len(sections) > 1 and geometries:
            ir["section_page_geometry"] = geometries
        elif "0" in geometries:
            ir["page_geometry"] = geometries["0"]
    ir["character_heights"] = {
        prop.get("id"): int(prop.get("height"))
        for prop in header.findall(".//{*}charProperties/{*}charPr")
    }
    ir["character_metrics"] = {
        prop.get("id"): {name: dict(child.attrib) for name in ("ratio", "spacing", "relSz", "offset")
                         if (child := prop.find("{*}" + name)) is not None}
        for prop in header.findall(".//{*}charProperties/{*}charPr")
    }
    ir["paragraph_layouts"] = {
        prop.get("id"): {"align": dict(align.attrib), "spacing": dict(spacing.attrib)}
        for prop in header.findall(".//{*}paraProperties/{*}paraPr")
        if (align := prop.find("{*}align")) is not None
        and (spacing := prop.find(".//{*}lineSpacing")) is not None
    }
    expected = read_hwpx(source)
    source_borders = read_hwpx(source, borders=True)
    dimensions = read_hwpx(source, geometry=True)
    actual = dict(ir_records(ir["body"]))
    styles = []
    indices = {}

    def visit(blocks, parent):
        for i, table in enumerate(b for b in blocks if b.get("kind") == "table"):
            path = f"{parent}/table[{i}]"
            if actual[path] != expected.get(path):
                continue
            table["native_geometry"] = dimensions[path]
            for cell in table["cells"]:
                key = f"{path}/cell[{cell['row']},{cell['col']}]"
                if actual[key] == expected.get(key):
                    cell["native_geometry"] = dimensions[key]
                    border = source_borders[key]
                    if border not in indices:
                        indices[border] = len(styles)
                        styles.append({side: dict(attrs) for side, attrs in border})
                    cell["border_style_index"] = indices[border]
                visit(cell["blocks"], key)

    visit(ir["body"], "")
    if styles:
        ir["cell_border_styles"] = styles
    return len(styles)
