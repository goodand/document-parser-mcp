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
