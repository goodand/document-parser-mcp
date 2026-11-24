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
