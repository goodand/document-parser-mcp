"""Regression contracts for full table JSON, not display strings."""
import json

from govparse import mcp_server as M


def test_full_table_json_keeps_long_cells_and_page_context(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    folder = tmp_path / "04_parsed" / "sample"
    folder.mkdir(parents=True)
    long_text = "정확한 항목명과 예산 수치를 끝까지 보존합니다" * 3
    data = {"number of pages": 1, "kids": [
        {"type": "paragraph", "page number": 1, "content": "(단위: 백만원, %)"},
        {"type": "table", "id": 2, "page number": 1,
         "bounding box": [10, 10, 100, 100], "rows": [
             {"cells": [{"column span": 2, "row span": 1,
                          "kids": [{"type": "paragraph", "content": long_text}]}]}
             for _ in range(15)
         ]},
    ]}
    (folder / "p00000-00001.json").write_text(json.dumps(data), encoding="utf-8")
    result = json.loads(M.get_tables("sample.pdf", recover_missing=False, output_format="json"))
    assert len(result["tables"]) == 1
    table = result["tables"][0]
    assert len(table["rows"]) == 15
    assert table["rows"][14][0] == long_text
    assert "(단위: 백만원, %)" in table["page_context"]
    assert table["page"] == 1
    assert table["review_required"] is True
    assert table["raw_table"]["rows"][0]["cells"][0]["column span"] == 2
    assert table["source_json"].endswith("p00000-00001.json")
    assert "unit" not in table


def test_json_has_more_than_forty_tables_and_errors_are_json(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    folder = tmp_path / "04_parsed" / "sample"
    folder.mkdir(parents=True)
    payload = {"number of pages": 1, "kids": [
        {"type": "table", "id": i, "page number": 1, "rows": [
            {"cells": [{"content": f"001-{i}"}]}]} for i in range(41)
    ]}
    (folder / "p00000-00001.json").write_text(json.dumps(payload), encoding="utf-8")
    result = json.loads(M.get_tables("sample.pdf", recover_missing=False, output_format="json"))
    assert len(result["tables"]) == 41
    assert result["tables"][-1]["rows"] == [["001-40"]]
    assert result["truncated"] is False
    assert "error" in json.loads(M.get_tables("../bad.pdf", output_format="json"))


def test_ambiguous_legacy_results_are_not_silently_combined(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    folder = tmp_path / "04_parsed" / "sample"
    folder.mkdir(parents=True)
    for end in (1, 2):
        (folder / f"p00000-{end:05}.json").write_text(
            json.dumps({"number of pages": end, "kids": []}), encoding="utf-8"
        )
    result = M.get_tables("sample.pdf", recover_missing=False)
    assert "선택" in result or "중복" in result
    assert "표가 없습니다" not in result


def test_inactive_json_does_not_change_tables_or_image_filter(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pymupdf

    from govparse.parsed_store import adopt_legacy

    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    source = tmp_path / "02_normalized" / "sample.pdf"
    source.parent.mkdir()
    pdf = pymupdf.open()
    pdf.new_page(width=600, height=800)
    pdf.save(source)
    pdf.close()
    folder = tmp_path / "04_parsed" / "sample"
    folder.mkdir(parents=True)
    active = folder / "p00000-00001.json"
    stale = folder / "sample.json"
    active.write_text(json.dumps({"number of pages": 1, "kids": [
        {"type": "table", "page number": 1, "bounding box": [50, 700, 200, 750],
         "rows": [{"cells": [{"content": "active"}]}]},
    ]}), encoding="utf-8")
    adopt_legacy(folder, [active], source)
    candidates = [
        SimpleNamespace(bbox=(50, 50, 200, 100)),
        SimpleNamespace(bbox=(50, 200, 200, 250)),
    ]
    monkeypatch.setattr(pymupdf.Page, "find_tables", lambda *a, **kw:
                        SimpleNamespace(tables=candidates))
    before = M.get_tables("sample.pdf", recover_missing=False, output_format="json")
    stale.write_text(json.dumps({"number of pages": 1, "kids": [
        {"type": "table", "page number": 1, "bounding box": [50, 550, 200, 600],
         "rows": [{"cells": [{"content": "inactive"}]}]},
    ]}), encoding="utf-8")
    assert M.get_tables("sample.pdf", recover_missing=False, output_format="json") == before
    result = M.extract_table_images("sample.pdf", only_missed=True)
    assert "p0001_t1.png" in result
    assert "p0001_t0.png" not in result


def test_missing_table_page_is_unknown_not_chunk_offset(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    folder = tmp_path / "04_parsed" / "sample"
    folder.mkdir(parents=True)
    (folder / "p00020-00022.json").write_text(json.dumps({
        "number of pages": 2, "kids": [{"type": "table", "rows": []}]
    }), encoding="utf-8")
    result = json.loads(M.get_tables("sample.pdf", recover_missing=False, output_format="json"))
    assert result["tables"][0]["page"] is None
    assert "missing_page" in result["tables"][0]["flags"]
    result = json.loads(M.get_tables("sample.pdf", page=20,
                                    recover_missing=False, output_format="json"))
    assert result["tables"] == []
