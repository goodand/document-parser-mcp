"""Compare table dimensions, merged cells and cell text at each nesting level."""
import json
import re
from zipfile import ZipFile

from lxml import etree as ET

HP = {"p": "http://www.hancom.co.kr/hwpml/2011/paragraph"}


def ir_records(blocks, parent=""):
    tables = (block for block in blocks if block.get("kind") == "table")
    for index, table in enumerate(tables):
        path = f"{parent}/table[{index}]"
        yield path, (table["rows"], table["cols"])
        for cell in table["cells"]:
            key = f"{path}/cell[{cell['row']},{cell['col']}]"
            contents = cell["blocks"]
            text = "".join(block.get("text", "") for block in contents if block["kind"] != "table")
            yield key, (cell["row_span"], cell["col_span"], text)
            yield from ir_records(contents, key)


def xml_records(tables, parent=""):
    for index, table in enumerate(tables):
        path = f"{parent}/table[{index}]"
        yield path, (int(table.get("rowCnt")), int(table.get("colCnt")))
        for cell in table.findall("p:tr/p:tc", HP):
            addr = cell.find("p:cellAddr", HP)
            span = cell.find("p:cellSpan", HP)
            key = f"{path}/cell[{addr.get('rowAddr')},{addr.get('colAddr')}]"
            runs = cell.findall("p:subList/p:p/p:run", HP)
            text = "".join("".join(t.itertext()) for run in runs for t in run.findall("p:t", HP))
            yield key, (int(span.get("rowSpan")), int(span.get("colSpan")), text)
            yield from xml_records((t for run in runs for t in run.findall("p:tbl", HP)), key)


def read_hwpx(path):
    with ZipFile(path) as archive:
        sections = sorted((n for n in archive.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)),
                          key=lambda n: int(re.search(r"\d+", n).group()))
        def tables():
            for name in sections:
                root = ET.fromstring(archive.read(name))
                yield from root.findall("p:p/p:run/p:tbl", HP)
        return dict(xml_records(tables()))


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
    return {
        "scope": "body tables by ordinal, cell coordinates, spans and text",
        "source_to_json": compare(read_hwpx(source), records) if source.suffix.lower() == ".hwpx" else None,
        "json_to_hwpx": compare(records, read_hwpx(restored)),
    }
