"""Native HWP sidecar stores source structure without inferring PDF page numbers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from govparse import hwp_source
from govparse import mcp_server as mcp


def test_v22_cache_is_invalidated_after_picture_matching_change(tmp_path):
    source = tmp_path / "sample.hwp"
    source.write_bytes(b"source")
    sidecar = tmp_path / "sidecar"
    sidecar.mkdir()
    document = sidecar / hwp_source.DOCUMENT
    document.write_text('{"body":[]}', encoding="utf8")
    source_hash = hwp_source.sha256_of(source)
    document_hash = hwp_source.sha256_of(document)
    (sidecar / hwp_source.MANIFEST).write_text(
        json.dumps({
            "version": 22,
            "source_path": str(source.resolve()),
            "source_sha256": source_hash,
            "document_path": hwp_source.DOCUMENT,
            "document_sha256": document_hash,
            "document_bytes": document.stat().st_size,
        }),
        encoding="utf8",
    )

    assert hwp_source._cached_manifest(source.resolve(), sidecar, source_hash) is None


def test_extract_source_keeps_cells_and_paragraph_boundaries(tmp_path, monkeypatch):
    src = tmp_path / "sample.hwp"
    src.write_bytes(b"hwp bytes")
    ir = {
        "schema_name": "document_ir",
        "body": [
            {"kind": "paragraph", "text": "one paragraph", "prov": {"para_idx": 7}},
            {"kind": "table", "rows": 2, "cols": 1, "cells": [
                {"row": 0, "col": 0, "row_span": 1, "col_span": 1,
                 "blocks": [{"kind": "paragraph", "text": "first"}]},
                {"row": 1, "col": 0, "row_span": 1, "col_span": 1,
                 "blocks": [{"kind": "paragraph", "text": "second"}]},
            ]},
        ],
    }
    calls = []

    def fake_parse(path):
        calls.append(path)
        return SimpleNamespace(to_ir_json=lambda **kw: json.dumps(ir, ensure_ascii=False))

    monkeypatch.setattr(hwp_source, "_rhwp_parse", fake_parse)
    out = tmp_path / "sidecar"
    first = hwp_source.extract_source(src, out)
    second = hwp_source.extract_source(src, out)

    assert len(calls) == 1  # source hash cache
    assert first == second
    assert first["counts"]["tables"] == 1
    assert first["counts"]["paragraphs"] == 1
    assert first["source_sha256"]
    saved = json.loads((out / "document.json").read_text(encoding="utf-8"))
    assert saved["body"][0]["text"] == "one paragraph"
    assert saved["body"][1]["cells"][1]["blocks"][0]["text"] == "second"
    assert "file_page" not in saved["body"][0]


def test_source_change_invalidates_cached_sidecar(tmp_path, monkeypatch):
    src = tmp_path / "sample.hwp"
    src.write_bytes(b"one")
    calls = []

    def fake_parse(path):
        calls.append(path)
        return SimpleNamespace(to_ir_json=lambda **kw: '{"body": []}')

    monkeypatch.setattr(hwp_source, "_rhwp_parse", fake_parse)
    out = tmp_path / "sidecar"
    a = hwp_source.extract_source(src, out)
    src.write_bytes(b"two")
    b = hwp_source.extract_source(src, out)

    assert a["source_sha256"] != b["source_sha256"]
    assert len(calls) == 2


def test_mcp_search_and_rag_export_keep_literal_cell_locator(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    src = tmp_path / "02_normalized" / "sample.hwp"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"hwp bytes")
    ir = {"body": [{"kind": "table", "rows": 1, "cols": 1,
                   "html": "<table>redundant</table>", "cells": [{
                       "row": 0, "col": 0, "blocks": [
                           {"kind": "paragraph", "text": "심사‧심판", "html": "redundant"}
                       ]
                   }]}]}
    monkeypatch.setattr(
        hwp_source, "_rhwp_parse",
        lambda path: SimpleNamespace(to_ir_json=lambda **kw: json.dumps(ir, ensure_ascii=False)),
    )

    parsed = json.loads(mcp.parse_hwp_source(src.name))
    found = json.loads(mcp.search_hwp_source(src.name, "심사‧심판"))
    element = json.loads(mcp.get_hwp_element(src.name, 0))
    selected = json.loads(mcp.get_hwp_element(src.name, 0, row=0, col=0))
    exported = json.loads(mcp.export_hwp_rag_chunks(src.name))

    assert parsed["counts"]["tables"] == 1
    assert found["total_matches"] == 1
    assert found["matches"][0]["cell"] == [0, 0]
    assert found["matches"][0]["body_index"] == 0
    assert "html" not in json.dumps(element["element"], ensure_ascii=False)
    assert selected["element"]["blocks"][0]["text"] == "심사‧심판"
    assert selected["selection"]["table_rows"] == 1
    assert exported["chunks"] == 1
    assert exported["source_sha256"] == parsed["source_sha256"]

def test_hwpx_nested_drawing_cache_recovers(tmp_path, monkeypatch):
    from xml.etree import ElementTree
    from zipfile import ZipFile

    src = tmp_path / "nested.hwpx"
    section = """<section xmlns="http://www.hancom.co.kr/hwpml/2011/paragraph">
    <tbl><tr><tc><cellAddr rowAddr="1" colAddr="2"/><subList><p><run>
    <tbl><tr><tc><cellAddr rowAddr="3" colAddr="0"/><subList><p><run>
    <container><polygon id="23"/></container><polygon id="24"/>
    </run></p></subList></tc></tr></tbl></run></p></subList></tc></tr></tbl></section>"""
    with ZipFile(src, "w") as package:
        package.writestr("Contents/section0.xml", section)
    monkeypatch.setattr(hwp_source, "_rhwp_parse", lambda path: SimpleNamespace(
        to_ir_json=lambda **kw: '{"body": []}'))
    out = tmp_path / "result"
    manifest = hwp_source.extract_source(src, out)
    sidecar = out / manifest["drawings_path"]
    objects = json.loads(sidecar.read_text(encoding="utf8"))
    assert [o["kind"] for o in objects] == ["container", "polygon"]
    for obj in objects:
        assert obj["cells"] == [[1, 2], [3, 0]]
        node = ElementTree.fromstring(section)
        for index in obj["child_indices"]:
            node = node[index]
        assert ElementTree.tostring(node, encoding="unicode") == obj["xml"]
    sidecar.write_text("[]", encoding="utf8")
    assert hwp_source.extract_source(src, out) == manifest
    assert json.loads(sidecar.read_text(encoding="utf8")) == objects
    sidecar.unlink()
    assert hwp_source.extract_source(src, out) == manifest
    assert json.loads(sidecar.read_text(encoding="utf8")) == objects

def test_shared_picture_preserves_bytes_and_each_cell_size(tmp_path):
    from zipfile import ZipFile
    from lxml import etree as ET
    from govparse.hwp_restore import restore_body

    binary = b"BM" + bytes(range(64))
    src = tmp_path / "pictures.hwpx"
    cells = []
    markup = []
    for col, width in enumerate((1000, 2000)):
        cells.append({"row": 0, "col": col, "row_span": 1, "col_span": 1,
                      "blocks": [{"kind": "picture", "image": {"uri": "bin://1"}}]})
        markup.append(f'<tc><cellAddr rowAddr="0" colAddr="{col}"/><subList><p><run>'
                      f'<pic><sz width="{width}"/><img binaryItemIDRef="image1"/></pic>'
                      '</run></p></subList></tc>')
    ir = {"body": [{"kind": "table", "rows": 1, "cols": 2,
                    "prov": {"section_idx": 0}, "cells": cells}]}
    with ZipFile(src, "w") as archive:
        archive.writestr("Contents/content.hpf", '<package><manifest><item id="image1" '
                         'href="BinData/image1.bmp" media-type="image/bmp"/></manifest></package>')
        archive.writestr("Contents/section0.xml",
                         '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/paragraph">'
                         '<p><run><tbl><tr>' + "".join(markup) + '</tr></tbl></run></p></sec>')
        archive.writestr("BinData/image1.bmp", binary)
    hwp_source._attach_pictures(ir, src)
    source_json = tmp_path / "document.json"
    source_json.write_text(json.dumps(ir), encoding="utf8")
    output = tmp_path / "restored.hwpx"
    restore_body(source_json, output)
    with ZipFile(output) as archive:
        assert archive.read("BinData/image1.bmp") == binary
        root = ET.fromstring(archive.read("Contents/section0.xml"))
        pics = root.findall(".//{*}tc/{*}subList/{*}p/{*}run/{*}pic")
        assert [pic.find("{*}sz").get("width") for pic in pics] == ["1000", "2000"]
        assert [pic.find("{*}img").get("binaryItemIDRef") for pic in pics] == ["image1"] * 2


def test_repeated_picture_keeps_body_and_nested_table_placements(tmp_path):
    from lxml import etree as ET
    from zipfile import ZipFile

    from govparse.hwp_restore import restore_body

    source = tmp_path / "shared-image.hwpx"
    binary = b"BM" + bytes(range(64))
    table = {
        "kind": "table",
        "rows": 1,
        "cols": 2,
        "prov": {"section_idx": 0},
        "cells": [
            {
                "row": 0,
                "col": col,
                "row_span": 1,
                "col_span": 1,
                "blocks": [{"kind": "picture", "image": {"uri": "bin://1"}}],
            }
            for col in range(2)
        ],
    }
    body_picture = {
        "kind": "picture",
        "image": {"uri": "bin://1"},
        "prov": {"section_idx": 0},
    }
    cell_xml = "".join(
        f'<tc><cellAddr rowAddr="0" colAddr="{col}"/><subList><p><run>'
        f'<pic><sz width="{1000 * (col + 1)}"/><img binaryItemIDRef="image1"/></pic>'
        "</run></p></subList></tc>"
        for col in range(2)
    )
    section_xml = (
        '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        f'<p><run><tbl><tr>{cell_xml}</tr></tbl></run></p>'
        '<p><run><pic><sz width="3000"/><img binaryItemIDRef="image1"/>'
        '<caption><subList><p><run><t>본문 캡션</t></run></p></subList></caption>'
        '</pic></run></p></sec>'
    )
    with ZipFile(source, "w") as package:
        package.writestr(
            "Contents/content.hpf",
            '<package><manifest><item id="image1" href="BinData/image1.bmp" '
            'media-type="image/bmp"/></manifest></package>',
        )
        package.writestr("Contents/section0.xml", section_xml)
        package.writestr("BinData/image1.bmp", binary)

    ir = {"body": [table, body_picture]}
    hwp_source._attach_pictures(ir, source)
    restored = tmp_path / "restored.hwpx"
    source_json = tmp_path / "document.json"
    source_json.write_text(json.dumps(ir), encoding="utf8")
    restore_body(source_json, restored)

    with ZipFile(restored) as package:
        section = ET.fromstring(package.read("Contents/section0.xml"))
        cell_pictures = section.findall(".//{*}tc/{*}subList/{*}p/{*}run/{*}pic")
        body_pictures = section.findall("./{*}p/{*}run/{*}pic")
        caption_text = body_pictures[0].find(".//{*}caption//{*}t")

    assert [pic.find("{*}sz").get("width") for pic in cell_pictures] == ["1000", "2000"]
    assert len(body_pictures) == 1
    assert body_pictures[0].find("{*}sz").get("width") == "3000"
    assert caption_text.text == "본문 캡션"


def test_ambiguous_repeated_picture_reference_is_left_unmatched(tmp_path):
    from zipfile import ZipFile

    source = tmp_path / "ambiguous-image.hwpx"
    with ZipFile(source, "w") as package:
        package.writestr(
            "Contents/content.hpf",
            '<package><manifest><item id="image1" href="BinData/image1.png" '
            'media-type="image/png"/></manifest></package>',
        )
        package.writestr(
            "Contents/section0.xml",
            '<sec xmlns="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<p><run><pic><sz width="1000"/><img binaryItemIDRef="image1"/></pic></run></p>'
            '<p><run><pic><sz width="2000"/><img binaryItemIDRef="image1"/></pic></run></p>'
            '</sec>',
        )
        package.writestr("BinData/image1.png", b"png")

    ir = {"body": [{
        "kind": "picture",
        "image": {"uri": "bin://1"},
        "prov": {"section_idx": 0},
    }]}
    hwp_source._attach_pictures(ir, source)

    assert "native_picture" not in ir["body"][0]


def _write_inline_picture_cell(path, separate_picture_paragraph):
    from copy import deepcopy
    from lxml import etree as ET
    from zipfile import ZipFile

    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    template = Path(hwp_source.__file__).with_name("blank_table.hwpx")
    with ZipFile(template) as source:
        section = ET.fromstring(source.read("Contents/section0.xml"))
        table = section.find(".//{*}tbl")
        cell = table.find("./{*}tr/{*}tc")
        sublist = cell.find("{*}subList")
        for child in list(sublist):
            sublist.remove(child)

        vector_paragraph = ET.SubElement(sublist, hp + "p", {
            "id": "9001", "paraPrIDRef": "0", "styleIDRef": "0",
        })
        vector_run = ET.SubElement(vector_paragraph, hp + "run", {"charPrIDRef": "0"})
        ET.SubElement(vector_run, hp + "rect", {
            "id": "9002", "x": "321", "y": "654", "width": "2345", "height": "678",
        })

        text_paragraph = ET.SubElement(sublist, hp + "p", {
            "id": "9003", "paraPrIDRef": "0", "styleIDRef": "0",
        })
        text_run = ET.SubElement(text_paragraph, hp + "run", {"charPrIDRef": "0"})
        text = ET.SubElement(text_run, hp + "t")
        text.text = "기상 관측"

        if separate_picture_paragraph:
            picture_paragraph = ET.SubElement(sublist, hp + "p", {
                "id": "9004", "paraPrIDRef": "0", "styleIDRef": "0",
            })
            picture_run = ET.SubElement(picture_paragraph, hp + "run", {"charPrIDRef": "0"})
        else:
            picture_run = text_run
        picture = ET.SubElement(picture_run, hp + "pic", {"id": "9005"})
        ET.SubElement(picture, hp + "sz", {"width": "4321", "height": "1234"})
        ET.SubElement(picture, hp + "img", {"binaryItemIDRef": "image1"})

        content = ET.fromstring(source.read("Contents/content.hpf"))
        manifest = content.find(".//{*}manifest")
        ET.SubElement(manifest, manifest[0].tag, {
            "id": "image1", "href": "BinData/image1.png", "media-type": "image/png",
        })
        with ZipFile(path, "w") as output:
            for item in source.infolist():
                if item.filename == "Contents/section0.xml":
                    payload = ET.tostring(section, encoding="UTF-8", xml_declaration=True)
                elif item.filename == "Contents/content.hpf":
                    payload = ET.tostring(content, encoding="UTF-8", xml_declaration=True)
                else:
                    payload = source.read(item.filename)
                output.writestr(item, payload)
            output.writestr("BinData/image1.png", b"synthetic-image-bytes")


def _inline_picture_ir(separate_picture_paragraph):
    blocks = [
        {"kind": "paragraph", "text": "", "inlines": [], "prov": {}},
        {"kind": "paragraph", "text": "기상 관측", "inlines": [], "prov": {}},
    ]
    if separate_picture_paragraph:
        blocks.append({"kind": "picture", "image": {"uri": "bin://1"}, "prov": {}})
    else:
        blocks[-1]["text"] = "기상 관측"
        blocks.append({"kind": "picture", "image": {"uri": "bin://1"}, "prov": {}})
    return {"body": [{
        "kind": "table",
        "rows": 1,
        "cols": 1,
        "prov": {"section_idx": 0},
        "cells": [{
            "row": 0,
            "col": 0,
            "row_span": 1,
            "col_span": 1,
            "blocks": blocks,
        }],
    }]}


def _picture_positions(path):
    from zipfile import ZipFile
    from lxml import etree as ET

    positions = []
    with ZipFile(path) as package:
        sections = sorted(
            name for name in package.namelist()
            if name.startswith("Contents/section") and name.endswith(".xml")
        )
        for section_name in sections:
            root = ET.fromstring(package.read(section_name))
            for paragraph in root.iter("{http://www.hancom.co.kr/hwpml/2011/paragraph}p"):
                for picture in paragraph.iter("{http://www.hancom.co.kr/hwpml/2011/paragraph}pic"):
                    cells = []
                    parent = picture.getparent()
                    while parent is not None:
                        if parent.tag == "{http://www.hancom.co.kr/hwpml/2011/paragraph}tc":
                            address = parent.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}cellAddr")
                            if address is not None:
                                cells.append((address.get("rowAddr"), address.get("colAddr")))
                        parent = parent.getparent()
                    cells.reverse()
                    size = picture.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}sz")
                    image = picture.find(".//{http://www.hancom.co.kr/hwpml/2011/paragraph}img")
                    caption = "".join(
                        node.text or "" for node in picture.findall(
                            ".//{http://www.hancom.co.kr/hwpml/2011/paragraph}caption//{http://www.hancom.co.kr/hwpml/2011/paragraph}t"
                        )
                    )
                    positions.append((
                        section_name,
                        tuple(cells),
                        paragraph.getparent().index(paragraph),
                        size.get("width") if size is not None else None,
                        size.get("height") if size is not None else None,
                        image.get("binaryItemIDRef") if image is not None else None,
                        caption,
                    ))
    return positions


def test_trailing_picture_inline_marker_respects_native_paragraphs(tmp_path, monkeypatch):
    import json
    from zipfile import ZipFile
    from lxml import etree as ET
    from govparse.hwp_restore import restore_body
    from govparse.table_roundtrip import compare, ir_records, read_hwpx

    for separate_picture_paragraph in (False, True):
        source = tmp_path / f"picture-{separate_picture_paragraph}.hwpx"
        _write_inline_picture_cell(source, separate_picture_paragraph)
        ir = _inline_picture_ir(separate_picture_paragraph)
        monkeypatch.setattr(
            hwp_source,
            "_rhwp_parse",
            lambda _path, parsed=ir: SimpleNamespace(
                to_ir_json=lambda **_kwargs: json.dumps(parsed, ensure_ascii=False)
            ),
        )
        sidecar = tmp_path / f"sidecar-{separate_picture_paragraph}"
        manifest = hwp_source.extract_source(source, sidecar)
        parsed = json.loads((sidecar / manifest["document_path"]).read_text("utf8"))
        picture = parsed["body"][0]["cells"][0]["blocks"][-1]

        assert bool(picture.get("inline_after_paragraph")) is (not separate_picture_paragraph)
        cell_records = dict(ir_records(parsed["body"]))
        assert cell_records == read_hwpx(source)

        restored = tmp_path / f"restored-{separate_picture_paragraph}.hwpx"
        restore_body(sidecar / manifest["document_path"], restored)
        assert compare(read_hwpx(source), read_hwpx(restored))["different_records"] == 0
        assert _picture_positions(source) == _picture_positions(restored)
        with ZipFile(restored) as package:
            section = ET.fromstring(package.read("Contents/section0.xml"))
        cell = section.find(".//{*}tbl/{*}tr/{*}tc")
        paragraphs = cell.findall("./{*}subList/{*}p")
        assert len(paragraphs) == (3 if separate_picture_paragraph else 2)
        assert len(cell.findall(".//{*}rect")) == 1
        assert len(cell.findall(".//{*}pic")) == 1


def test_legacy_hwp_without_pictures_uses_native_geometry_when_conversion_succeeds(tmp_path, monkeypatch):
    import json
    from zipfile import ZipFile

    source = tmp_path / "simple.hwp"
    source.write_bytes(b"synthetic hwp")
    template = Path(hwp_source.__file__).with_name("blank_table.hwpx")
    with ZipFile(template) as package:
        native_hwpx = package.read("mimetype")
        from io import BytesIO
        buffer = BytesIO()
        with ZipFile(buffer, "w") as copy:
            for item in package.infolist():
                copy.writestr(item, package.read(item.filename))
        native_hwpx = buffer.getvalue()
    ir = {"body": [{
        "kind": "table",
        "rows": 1,
        "cols": 1,
        "prov": {"section_idx": 0},
        "cells": [{
            "row": 0, "col": 0, "row_span": 1, "col_span": 1,
            "blocks": [{"kind": "paragraph", "text": "", "inlines": [], "prov": {}}],
        }],
    }]}
    calls = []
    monkeypatch.setattr(hwp_source, "_rhwp_parse", lambda _path: SimpleNamespace(
        to_ir_json=lambda **_kwargs: json.dumps(ir),
        to_hwpx_bytes=lambda: calls.append(True) or native_hwpx,
    ))

    sidecar = tmp_path / "converted"
    manifest = hwp_source.extract_source(source, sidecar)
    parsed = json.loads((sidecar / manifest["document_path"]).read_text("utf8"))

    assert calls == [True]
    assert parsed["body"][0]["native_geometry"]["sz"]
    assert parsed["body"][0]["cells"][0]["native_geometry"]["cellSz"]["width"]
    assert parsed["cell_border_styles"]


def test_legacy_hwp_conversion_failure_does_not_break_parsing(tmp_path, monkeypatch):
    import json

    source = tmp_path / "simple.hwp"
    source.write_bytes(b"synthetic hwp")
    ir = {"body": [{
        "kind": "table",
        "rows": 1,
        "cols": 1,
        "prov": {"section_idx": 0},
        "cells": [{
            "row": 0, "col": 0, "row_span": 1, "col_span": 1,
            "blocks": [{"kind": "paragraph", "text": "", "inlines": [], "prov": {}}],
        }],
    }]}

    class ParsedDocument:
        def to_ir_json(self, **_kwargs):
            return json.dumps(ir)

        def to_hwpx_bytes(self):
            raise RuntimeError("synthetic conversion failure")

    monkeypatch.setattr(hwp_source, "_rhwp_parse", lambda _path: ParsedDocument())
    sidecar = tmp_path / "conversion-failed"
    manifest = hwp_source.extract_source(source, sidecar)
    parsed = json.loads((sidecar / manifest["document_path"]).read_text("utf8"))

    assert manifest["counts"]["tables"] == 1
    assert "native_geometry" not in parsed["body"][0]
    assert "picture_assets" not in ir
