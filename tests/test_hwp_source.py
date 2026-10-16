"""Native HWP sidecar stores source structure without inferring PDF page numbers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from govparse import hwp_source
from govparse import mcp_server as mcp


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
