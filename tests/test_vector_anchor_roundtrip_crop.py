"""Pure-vector table roundtrip and crop coverage using the bundled blank fixture."""
from __future__ import annotations

import io
import json
import sys
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from lxml import etree as ET
from PIL import Image, ImageDraw

from govparse import hwp_restore, hwp_source, table_images


HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
NS = {"hp": HP[1:-1]}
TEMPLATE = Path(hwp_restore.__file__).with_name("blank_table.hwpx")


def _paragraph(text=""):
    return {"kind": "paragraph", "text": text, "inlines": [], "prov": {}}


def _vector_ir():
    cells = []
    for row in range(2):
        for col in range(2):
            cells.append({
                "row": row,
                "col": col,
                "row_span": 1,
                "col_span": 1,
                "blocks": [_paragraph()],
            })
    return {
        "schema_name": "HwpDocument",
        "body": [{
            "kind": "table",
            "rows": 2,
            "cols": 2,
            "prov": {"section_idx": 0},
            "cells": cells,
        }],
    }


def _vector_hwpx(destination: Path) -> dict:
    """Expand the package's blank 1×1 table into a 2×2 vector-only table."""
    with zipfile.ZipFile(TEMPLATE) as source:
        section = ET.fromstring(source.read("Contents/section0.xml"))
        table = section.find(".//hp:tbl", NS)
        assert table is not None
        original_row = table.find("hp:tr", NS)
        original_cell = original_row.find("hp:tc", NS)
        original_cell_xml = ET.tostring(original_cell)

        for child in list(original_row):
            original_row.remove(child)
        table.set("rowCnt", "2")
        table.set("colCnt", "2")
        placements = {}
        for row in range(2):
            row_node = deepcopy(original_row)
            for col in range(2):
                cell = ET.fromstring(original_cell_xml)
                cell.find("hp:cellAddr", NS).attrib.update({
                    "rowAddr": str(row),
                    "colAddr": str(col),
                })
                width = 14000 + col * 6000
                height = 8000 + row * 2500
                cell.find("hp:cellSz", NS).attrib.update({
                    "width": str(width),
                    "height": str(height),
                })
                paragraph = cell.find("hp:subList/hp:p", NS)
                run = paragraph.find("hp:run", NS)
                rect = ET.SubElement(run, HP + "rect", {
                    "id": str(110 + row * 2 + col),
                    "x": str(1000 + col * 200),
                    "y": str(2000 + row * 300),
                    "width": str(5000 + col * 100),
                    "height": str(3000 + row * 100),
                })
                rect.set("textWrap", "TOP_AND_BOTTOM")
                placements[row, col] = dict(rect.attrib)
                row_node.append(cell)
            table.append(row_node)

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as output:
            for item in source.infolist():
                payload = (
                    ET.tostring(section, encoding="UTF-8", xml_declaration=True)
                    if item.filename == "Contents/section0.xml"
                    else source.read(item.filename)
                )
                output.writestr(item, payload)
    return placements


def _rendered_vector_page() -> bytes:
    image = Image.new("RGB", (900, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((275, 190, 625, 510), outline="#111111", width=4)
    draw.line((275, 350, 625, 350), fill="#111111", width=4)
    draw.line((450, 190, 450, 510), fill="#111111", width=4)
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    return payload.getvalue()


def _extract_vector_source(source: Path, monkeypatch) -> dict:
    ir = _vector_ir()
    monkeypatch.setattr(
        hwp_source,
        "_rhwp_parse",
        lambda _path: SimpleNamespace(
            to_ir_json=lambda **_kwargs: json.dumps(ir, ensure_ascii=False)
        ),
    )
    sidecar = source.parent / "source-ir"
    hwp_source.extract_source(source, sidecar)
    return json.loads((sidecar / hwp_source.DOCUMENT).read_text("utf8"))


def test_vector_anchors_roundtrip_in_their_cells_with_native_geometry(tmp_path, monkeypatch):
    source = tmp_path / "pure-vector.hwpx"
    expected_placements = _vector_hwpx(source)
    ir = _extract_vector_source(source, monkeypatch)

    for cell in ir["body"][0]["cells"]:
        row, col = cell["row"], cell["col"]
        geometry = cell["native_geometry"]
        assert geometry["cellSz"]["width"] == str(14000 + col * 6000)
        assert geometry["cellSz"]["height"] == str(8000 + row * 2500)
        anchors = geometry["vector_anchors"]["0"]
        assert len(anchors) == 1
        shape = ET.fromstring(anchors[0].encode("utf8"))
        assert shape.tag == HP + "rect"
        assert dict(shape.attrib) == expected_placements[row, col]

    source_json = tmp_path / "vector-ir.json"
    source_json.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf8")
    restored = tmp_path / "restored-vector.hwpx"
    hwp_restore.restore_body(source_json, restored)

    with zipfile.ZipFile(restored) as package:
        section = ET.fromstring(package.read("Contents/section0.xml"))
    restored_cells = section.findall(".//hp:tbl/hp:tr/hp:tc", NS)
    assert len(restored_cells) == 4
    restored_ids = set()
    for cell in restored_cells:
        address = cell.find("hp:cellAddr", NS)
        key = int(address.get("rowAddr")), int(address.get("colAddr"))
        shape = cell.find("hp:subList/hp:p/hp:run/hp:rect", NS)
        assert shape is not None
        assert {
            attr: shape.get(attr)
            for attr in ("x", "y", "width", "height", "textWrap")
        } == {
            attr: expected_placements[key][attr]
            for attr in ("x", "y", "width", "height", "textWrap")
        }
        assert shape.get("id") not in restored_ids
        restored_ids.add(shape.get("id"))
        assert cell.find("hp:cellSz", NS).get("width") == str(14000 + key[1] * 6000)
        assert cell.find("hp:cellSz", NS).get("height") == str(8000 + key[0] * 2500)


def test_pure_vector_table_crop_keeps_all_four_cell_regions(tmp_path, monkeypatch):
    source = tmp_path / "pure-vector.hwpx"
    _vector_hwpx(source)
    ir = _extract_vector_source(source, monkeypatch)

    class FakeDocument:
        @classmethod
        def from_bytes(cls, _payload, source_uri=None):
            return cls()

        def render_all_png(self):
            return [_rendered_vector_page()]

    monkeypatch.setitem(sys.modules, "rhwp", SimpleNamespace(Document=FakeDocument))
    result = table_images.extract_table_images_by_id(source, ir, tmp_path / "crops")

    assert result["table_count"] == 1
    assert result["rendered_count"] == 1
    record = next(iter(result["tables"].values()))
    assert record["status"] == "rendered"
    assert record["crop_applied"] == [True]
    image = Image.open(tmp_path / "crops" / record["path"]).convert("RGB")
    assert image.size == (375, 345)

    pixels = image.load()
    for point in ((12, 12), (362, 12), (12, 332), (362, 332)):
        assert pixels[point] != (255, 255, 255)



def test_separate_picture_paragraph_survives_binary_hwp_roundtrip(tmp_path):
    import rhwp
    from govparse.hwp_source import extract_source
    from govparse.hwp_restore import restore_body
    from govparse.table_roundtrip import read_hwpx

    source = Path(__file__).resolve().parents[1] / "govparse-desktop/samples/synthetic-figures.hwp"
    reference = tmp_path / "native.hwpx"
    reference.write_bytes(rhwp.parse(str(source)).to_hwpx_bytes())
    manifest = extract_source(source, tmp_path)
    restored = tmp_path / "restored.hwpx"
    restore_body(tmp_path / manifest["document_path"], restored)
    assert read_hwpx(reference) == read_hwpx(restored)
