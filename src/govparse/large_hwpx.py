"""Read HWPX table structure directly when rhwp's section-size guard rejects it.

This fallback only constructs the table and paragraph IR needed by table image
matching. It does not replace the general-purpose HWP/HWPX parser.
"""
from __future__ import annotations

import re
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET

from .table_roundtrip import paragraph_blocks

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _xml_text(element) -> str:
    parts = [element.text or ""]
    for child in element:
        local = ET.QName(child).localname
        if local == "fwSpace":
            parts.append("\u2007")
        elif local == "tab":
            parts.append("\t")
        elif local == "lineBreak":
            parts.append("\n")
        else:
            parts.append(_xml_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _table(element) -> dict:
    cells = []
    for row in element.findall(HP + "tr"):
        for native_cell in row.findall(HP + "tc"):
            address = native_cell.find(HP + "cellAddr")
            span = native_cell.find(HP + "cellSpan")
            sublist = native_cell.find(HP + "subList")
            blocks = []
            if sublist is not None:
                for paragraph in sublist.findall(HP + "p"):
                    nested_tables = iter(paragraph.findall(HP + "run/" + HP + "tbl"))
                    for kind, value in paragraph_blocks(paragraph):
                        if kind == "paragraph":
                            blocks.append({"kind": kind, "text": value, "inlines": [], "prov": {}})
                        else:
                            nested = next(nested_tables, None)
                            if nested is not None:
                                blocks.append(_table(nested))
            cells.append({
                "row": int(address.get("rowAddr")),
                "col": int(address.get("colAddr")),
                "row_span": int(span.get("rowSpan")) if span is not None else 1,
                "col_span": int(span.get("colSpan")) if span is not None else 1,
                "blocks": blocks,
            })
    text = " ".join(
        block.get("text", "")
        for cell in cells
        for block in cell["blocks"]
        if block.get("kind") == "paragraph"
    )
    return {
        "kind": "table",
        "rows": int(element.get("rowCnt") or 0),
        "cols": int(element.get("colCnt") or 0),
        "cells": cells,
        "text": text,
        "caption": None,
        "prov": {"table_id": element.get("id")},
    }


def _append_anchored(node, body: list[dict], section_idx: int) -> None:
    for child in node:
        if child.tag == HP + "tbl":
            table = _table(child)
            table["prov"]["section_idx"] = section_idx
            body.append(table)
        elif child.tag == HP + "pic":
            body.append({"kind": "picture", "prov": {"section_idx": section_idx}})
        else:
            _append_anchored(child, body, section_idx)


def build_table_ir(source: Path | str) -> dict:
    """Build a rhwp-shaped body with stable table positions from a large HWPX."""
    source = Path(source)
    body: list[dict] = []
    parser = ET.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
    with ZipFile(source) as package:
        sections = sorted(
            (name for name in package.namelist()
             if re.fullmatch(r"Contents/section\d+\.xml", name)),
            key=lambda name: int(re.search(r"(\d+)", name).group(1)),
        )
        for name in sections:
            section_idx = int(re.search(r"(\d+)", name).group(1))
            with package.open(name) as stream:
                root = ET.parse(stream, parser).getroot()
            for paragraph in root.findall(HP + "p"):
                body.append({
                    "kind": "paragraph",
                    "text": "".join(
                        _xml_text(text)
                        for text in paragraph.findall(".//" + HP + "t")
                    ).strip(),
                    "inlines": [],
                    "prov": {"section_idx": section_idx},
                })
                _append_anchored(paragraph, body, section_idx)
            del root
    return {"schema_name": "HwpDocument", "schema_version": "1.1-owpml", "body": body}
