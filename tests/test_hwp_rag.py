"""HWP RAG projection must retain literal text and native paragraph boundaries."""

from __future__ import annotations

import json

from govparse.hwp_rag import export_hwp_chunks


def test_export_retains_paragraphs_cell_positions_and_unicode(tmp_path):
    ir = {"body": [
        {"kind": "paragraph", "text": "심사‧심판", "inlines": []},
        {"kind": "paragraph", "text": "", "inlines": []},
        {"kind": "table", "rows": 2, "cols": 1, "cells": [
            {"row": 0, "col": 0, "row_span": 1, "col_span": 1, "blocks": [
                {"kind": "paragraph", "text": "first\nline"},
                {"kind": "paragraph", "text": "second"},
            ]},
            {"row": 1, "col": 0, "row_span": 1, "col_span": 1, "blocks": [
                {"kind": "paragraph", "text": "third"},
            ]},
        ]},
    ]}
    out = tmp_path / "chunks.jsonl"

    result = export_hwp_chunks(ir, "a" * 64, out)
    chunks = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    assert result["chunks"] == 5
    assert [item["text"] for item in chunks] == [
        "심사‧심판", "", "first\nline", "second", "third"
    ]
    assert chunks[0]["source_locator"]["body_index"] == 0
    assert chunks[0]["file_page"] is None
    assert chunks[1]["retrieval_usable"] is False
    assert chunks[2]["source_locator"]["cell"] == [0, 0]
    assert chunks[3]["source_locator"]["paragraph_in_cell"] == 1
    assert chunks[4]["source_locator"]["cell"] == [1, 0]
    assert chunks[0]["text"].encode("utf-8") == "심사‧심판".encode()


def test_nested_table_is_not_flattened_into_parent_cell(tmp_path):
    ir = {"body": [{"kind": "table", "rows": 1, "cols": 1, "cells": [
        {"row": 0, "col": 0, "blocks": [
            {"kind": "paragraph", "text": "outer"},
            {"kind": "table", "rows": 1, "cols": 1, "cells": [
                {"row": 0, "col": 0, "blocks": [
                    {"kind": "paragraph", "text": "inner"}
                ]}
            ]},
        ]}
    ]}]}
    out = tmp_path / "chunks.jsonl"

    export_hwp_chunks(ir, "b" * 64, out)
    chunks = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    assert [item["text"] for item in chunks] == ["outer", "inner"]
    assert chunks[0]["source_locator"]["table_path"] != chunks[1]["source_locator"]["table_path"]
