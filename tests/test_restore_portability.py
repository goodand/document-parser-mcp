from pathlib import Path

from govparse import hwp_restore, mcp_server


def test_restore_hwpx_through_tool(tmp_path, monkeypatch):
    import json
    from shutil import copyfile
    from zipfile import ZipFile

    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    normalized = tmp_path / "02_normalized"
    normalized.mkdir()
    template = Path(hwp_restore.__file__).with_name("blank_table.hwpx")
    copyfile(template, normalized / "sample.hwpx")

    result = json.loads(mcp_server.restore_hwp_body("sample.hwpx"))
    assert "error" not in result, result
    with ZipFile(result["hwpx_path"]) as restored:
        assert restored.testzip() is None
        assert b"<hp:tbl " in restored.read("Contents/section0.xml")
    assert result["table_diff"]["source_to_json"]["different_records"] == 0
    assert result["table_diff"]["json_to_hwpx"]["different_records"] == 0

def test_missing_nested_cell_is_reported():
    from govparse.table_roundtrip import compare, ir_records

    inner = {"kind": "table", "rows": 1, "cols": 1, "cells": [
        {"row": 0, "col": 0, "row_span": 1, "col_span": 1,
         "blocks": [{"kind": "paragraph", "text": "금액"}]}
    ]}
    outer = {"kind": "table", "rows": 1, "cols": 2, "cells": [
        {"row": 0, "col": 0, "row_span": 1, "col_span": 2, "blocks": [inner]}
    ]}
    expected = dict(ir_records([outer]))
    inner["cells"].clear()
    diff = compare(expected, dict(ir_records([outer])))
    assert diff == {"different_records": 1,
                    "example_paths": ["/table[0]/cell[0,0]/table[0]/cell[0,0]"]}


def test_restore_preserves_section_order_margins_and_body(tmp_path):
    import json
    from xml.etree import ElementTree
    from zipfile import ZipFile

    source = tmp_path / "document.json"
    output = tmp_path / "result.hwpx"
    source.write_text(json.dumps({
        "sections": [{"section_idx": 0}, {"section_idx": 1}],
        "section_page_geometry": {
            str(i): {"page": {"width": "59528"}, "margin": {"left": margin}}
            for i, margin in enumerate(["8504", "5669"])
        },
        "body": [
            {"kind": "paragraph", "text": label, "prov": {"section_idx": i}}
            for i, label in enumerate(["first section", "second section"])
        ],
    }), encoding="utf8")
    hwp_restore.restore_body(source, output)

    with ZipFile(output) as package:
        manifest = ElementTree.fromstring(package.read("Contents/content.hpf"))
        section_ids = [item.get("idref") for item in manifest.findall(".//{*}spine/{*}itemref")]
        assert section_ids == ["header", "section0", "section1"]
        header = ElementTree.fromstring(package.read("Contents/header.xml"))
        assert header.get("secCnt") == "2"
        for i, (label, margin) in enumerate([("first section", "8504"), ("second section", "5669")]):
            part = ElementTree.fromstring(package.read(f"Contents/section{i}.xml"))
            assert part.find(".//{*}pagePr/{*}margin").get("left") == margin
            assert [t.text for t in part.findall(".//{*}t") if t.text] == [label]


def test_restore_tool_preserves_solid_cell_fill(tmp_path, monkeypatch):
    import json
    from zipfile import ZipFile
    from lxml import etree

    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    corpus = tmp_path / "02_normalized"
    corpus.mkdir()
    template = Path(hwp_restore.__file__).with_name("blank_table.hwpx")
    core = "{http://www.hancom.co.kr/hwpml/2011/core}"
    with ZipFile(template) as source, ZipFile(corpus / "colored.hwpx", "w") as fixture:
        section = etree.fromstring(source.read("Contents/section0.xml"))
        ref = section.find(".//{*}tc").get("borderFillIDRef")
        header = etree.fromstring(source.read("Contents/header.xml"))
        border = header.find(f".//{{*}}borderFill[@id='{ref}']")
        fill = border.find(core + "fillBrush")
        if fill is not None:
            border.remove(fill)
        fill = etree.SubElement(border, core + "fillBrush")
        expected = {"faceColor": "#E5FFF0", "hatchColor": "#000000", "alpha": "0"}
        etree.SubElement(fill, core + "winBrush", expected)
        for item in source.infolist():
            data = etree.tostring(header) if item.filename == "Contents/header.xml" else source.read(item)
            fixture.writestr(item, data)

    result = json.loads(mcp_server.restore_hwp_body("colored.hwpx"))
    with ZipFile(result["hwpx_path"]) as restored:
        section = etree.fromstring(restored.read("Contents/section0.xml"))
        header = etree.fromstring(restored.read("Contents/header.xml"))
        ref = section.find(".//{*}tc").get("borderFillIDRef")
        brush = header.find(f".//{{*}}borderFill[@id='{ref}']/{{*}}fillBrush/{{*}}winBrush")
        assert brush is not None
        assert dict(brush.attrib) == expected
