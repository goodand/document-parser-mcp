"""Native HWP sidecar stores source structure without inferring PDF page numbers."""

from __future__ import annotations

import json
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
    assert "picture_assets" not in ir
