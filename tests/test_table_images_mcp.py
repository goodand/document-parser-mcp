"""Small contract tests for the table-image MCP wrapper."""
from __future__ import annotations

import json
from pathlib import Path

from govparse import mcp_server as M


def _fixture_ir():
    return {"body": [
        {"kind": "paragraph", "text": "intro"},
        {"kind": "table", "cells": [
            {"row": 0, "col": 0, "blocks": [{"kind": "paragraph", "text": "A"}]},
            {"row": 0, "col": 1, "blocks": [{"kind": "table", "cells": []}]},
        ]},
    ]}


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    src, sidecar = M._hwp_source_paths("fixture.hwp")
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fixture")
    sidecar.mkdir(parents=True, exist_ok=True)
    ir = _fixture_ir()
    (sidecar / "document.json").write_text(json.dumps(ir), encoding="utf-8")

    import govparse.hwp_source as source
    monkeypatch.setattr(source, "extract_source", lambda path, target: {
        "source_sha256": M.sha256_of(path) if hasattr(M, "sha256_of") else __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    })
    return ir


def test_table_image_list_manifest_has_ids_and_no_failures(tmp_path, monkeypatch):
    original_ir = _setup(tmp_path, monkeypatch)
    result = json.loads(M.extract_hwp_table_images("fixture.hwp", list_only=True))
    assert result["status"] == "listed"
    assert result["failed_count"] == 0
    linked = json.loads(Path(result["table_images_ir_json"]).read_text(encoding="utf-8"))
    assert linked["body"][1]["table_id"] == "s0/b1/t0"
    assert linked["body"][1]["cells"][1]["blocks"][0]["table_id"].startswith("s0/b1/t0/c0_1/t")
    assert (Path(result["manifest_path"])).is_file()
    assert original_ir["body"][1].get("table_id") is None


def test_table_image_selection_and_unknown_id_reported(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    import govparse.table_selection as images

    def fake_render(source, ir, out_dir, table_ids=None):
        ids = sorted(table_ids or [])
        return {"tables": {key: {"status": "rendered" if key.endswith("/t0") else "unmatched",
                                  "path": "x.png" if key.endswith("/t0") else None,
                                  "paths": ["x.png"] if key.endswith("/t0") else [],
                                  "sha256": ["abc"] if key.endswith("/t0") else [],
                                  "reason": "table_id_not_present_in_ir" if key.startswith("missing") else None}
                            for key in ids},
                "rendered_count": sum(key.endswith("/t0") for key in ids)}

    monkeypatch.setattr(images, "extract_selected_table_images", fake_render)
    result = json.loads(M.extract_hwp_table_images("fixture.hwp", ["s0/b1/t0", "missing/id"]))
    assert result["status"] == "partial"
    assert result["rendered_count"] == 1
    assert result["unknown_table_ids"] == ["missing/id"]
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["tables"]["s0/b1/t0"]["ir_pointer"] == "/body/1"
    assert manifest["asset_paths_relative_to"] == result["image_dir"]
