"""JSON + PNG export writes one editable-table candidate per table (engine table JSON v1.0) and links it.

Contract: engine-handoff-table-edit-and-rhwp.md. Candidates come from the source cells via the engine's
govparse.table_edit.build_native_table_candidate(ir, table_id); file names never contain the table_id's slashes;
the result can be moved or unzipped elsewhere and still find its candidates.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from gpdesk import desktop_worker as dw

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _fake_builder(ir, table_id, *, asset_id=None, image_sha256=None):
    if table_id.endswith("/t0") and "/c" in table_id:
        raise ValueError("nested table rejected by this fake")      # one table fails: the export goes on
    return {"schema_version": "1.0", "source_kind": "native", "table_id": table_id, "asset_id": asset_id,
            "image_sha256": image_sha256, "status": "ready", "rows": 1, "cols": 1,
            "cells": [{"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": table_id, "bbox_px": None,
                       "nested_table_ids": []}]}


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(dw, "_candidate_builder", lambda: _fake_builder)
    out = tmp_path_factory.mktemp("cand") / "res"
    out.mkdir()
    events = []
    try:
        rep = dw.run("json", SAMPLES / "synthetic-figures.hwpx", out, progress=events.append)
    finally:
        mp.undo()
    return out, rep, events


def _meta(out):
    return json.loads((out / "document.json").read_text("utf-8"))


def test_candidates_are_written_per_table_with_safe_names(exported):
    out, rep, _ = exported
    files = sorted((out / "tables").glob("*.json"))
    assert files and all(f.name.startswith("table-") and "/" not in f.name for f in files)
    for f in files:
        c = json.loads(f.read_text("utf-8"))
        assert c["schema_version"] == "1.0" and c["asset_id"] == f.stem
    cands = rep["bundle"]["table_images"]["candidates"]
    assert cands["tables"] == 3 and cands["ready"] + cands["failed"] == 3 and cands["failed"] == 1
    assert any("표 편집 후보 실패 1개" in n for n in rep["notes"])


def test_candidate_paths_are_safe_names(exported):
    out, _, _ = exported
    for a in _meta(out)["govparse_desktop"]["assets"]:
        if a.get("table_json", {}).get("path"):                      # 12 hex digits, never the table_id's slashes
            assert re.fullmatch(r"tables/table-[0-9a-f]{12}\.json", a["table_json"]["path"])


def test_ir_tables_carry_candidate_status(exported):
    out, _, _ = exported
    from gpdesk.table_ids import iter_table_nodes

    nodes = list(iter_table_nodes(_meta(out)["source_ir"]))
    assert len(nodes) == 3
    states = sorted(n["table_json"]["status"] for n in nodes)
    assert states == ["failed", "ready", "ready"]
    failed = next(n for n in nodes if n["table_json"]["status"] == "failed")
    assert failed["table_json"]["path"] is None and "rejected" in failed["table_json"]["reason"]


def test_index_resolves_candidates_after_moving_the_result(exported, tmp_path):
    out, _, _ = exported
    moved = tmp_path / "다른 PC" / "풀린 결과"
    shutil.copytree(out, moved)
    idx = dw.index(moved / "document.json")
    tables = [a for a in idx["assets"] if a["origin"] in ("table_image", "table_source_page") and "table_json" in a]
    assert tables
    for a in tables:
        tj = a["table_json"]
        if tj["status"] == "ready":
            assert Path(tj["file"]).is_file() and str(moved) in tj["file"]
        else:
            assert tj["file"] is None and tj["reason"]


def test_candidates_are_covered_by_the_integrity_list(exported):
    out, _, _ = exported
    listed = {f["path"] for f in json.loads((out / "report.json").read_text("utf-8")).get("files") or []}
    if not listed:
        pytest.skip("this build writes no integrity list")
    assert {f"tables/{f.name}" for f in (out / "tables").glob("*.json")} <= listed


def test_engine_without_builder_exports_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(dw, "_candidate_builder", lambda: None)
    out = tmp_path / "res"
    out.mkdir()
    rep = dw.run("json", SAMPLES / "synthetic-figures.hwpx", out)
    assert rep["saved"] and not (out / "tables").exists()
    assert any("표 편집 후보를 만드는 기능이 없음" in n for n in rep["notes"])
    assert all("table_json" not in a for a in _meta(out)["govparse_desktop"]["assets"])


def test_real_engine_candidates_match_the_source_cells(tmp_path):
    """Runs only with an engine that has govparse.table_edit (engine draft or a release that includes it)."""
    if dw._candidate_builder() is None:
        pytest.skip("engine has no govparse.table_edit yet")
    out = tmp_path / "res"
    out.mkdir()
    rep = dw.run("json", SAMPLES / "synthetic-figures.hwpx", out)
    assert rep["bundle"]["table_images"]["candidates"] == {"tables": 3, "ready": 3, "failed": 0}
    cands = [json.loads(f.read_text("utf-8")) for f in sorted((out / "tables").glob("*.json"))]
    by_id = {c["table_id"]: c for c in cands}
    nested = [c for c in cands if any(cell["nested_table_ids"] for cell in c["cells"])]
    assert nested, "the outer table links its nested table"
    for c in nested:
        for cell in c["cells"]:
            assert all(child in by_id for child in cell["nested_table_ids"])
    assert any(cell["row_span"] > 1 or cell["col_span"] > 1 for c in cands for cell in c["cells"]), "merged cell kept"


# ───────────── image-table mode (engine govparse.image_table.extract_table_from_image) ─────────────

def _png(path: Path) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (120, 60), "white").save(path)
    return path


def _fake_reader(cells):
    seen = {}

    def read(image_path, *, model_path=None, include_ocr=False, ocr_model_dir=None):
        seen.update(image=Path(image_path), include_ocr=include_ocr, ocr_model_dir=ocr_model_dir, model_path=model_path)
        return {"schema_version": "1.0", "source_kind": "image", "status": "review", "structure_check": "review",
                "image": {"path": str(Path(image_path).resolve()), "width_px": 120, "height_px": 60, "sha256": "a" * 64},
                "rows": 1 if cells else 0, "cols": len(cells), "cells": cells, "warnings": ["Review ..."]}
    return read, seen


def test_image_table_writes_a_review_candidate_without_absolute_paths(tmp_path, monkeypatch):
    cells = [{"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "구분", "text_check": "review", "bbox_px": [0, 0, 60, 30]},
             {"row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "", "text_check": "not_detected", "bbox_px": [60, 0, 120, 30]}]
    read, seen = _fake_reader(cells)
    monkeypatch.setattr(dw, "_image_table_reader", lambda: read)
    png = _png(tmp_path / "res" / "assets" / "tables" / "table-abc-p001.png")
    out = tmp_path / "res" / "tables" / "table-abc-p001.image.json"
    r = dw._dispatch(["image-table", str(png), str(out), "ocr", "s0/b2/t0"])
    assert r == {"file": str(out), "status": "review", "rows": 1, "cols": 2, "cells": 2, "ocr_cells": 1, "warnings": ["Review ..."]}
    saved = json.loads(out.read_text("utf-8"))
    assert saved["image"]["path"] == "table-abc-p001.png" and str(tmp_path) not in out.read_text("utf-8")
    assert saved["table_id"] == "s0/b2/t0" and saved["asset_id"] == "table-abc-p001"
    assert seen["include_ocr"] is True and seen["image"] == png
    assert not list(out.parent.glob(".*.tmp"))


def test_image_table_without_ocr_and_without_grid(tmp_path, monkeypatch):
    read, seen = _fake_reader([])
    monkeypatch.setattr(dw, "_image_table_reader", lambda: read)
    png = _png(tmp_path / "assets" / "tables" / "t-p001.png")
    r = dw._dispatch(["image-table", str(png), str(tmp_path / "tables" / "t-p001.image.json"), "no-ocr"])
    assert r["status"] == "no_grid" and r["cells"] == 0 and seen["include_ocr"] is False
    assert json.loads((tmp_path / "tables" / "t-p001.image.json").read_text("utf-8"))["table_id"] is None


def test_image_table_needs_the_engine_function(tmp_path, monkeypatch):
    monkeypatch.setattr(dw, "_image_table_reader", lambda: None)
    png = _png(tmp_path / "t.png")
    with pytest.raises(RuntimeError, match="이미지에서 표를 읽는 기능이 없습니다"):
        dw.image_table(png, tmp_path / "t.image.json")
    assert not (tmp_path / "t.image.json").exists()


# ───────────── table-save mode (Windows C# 표 고치기 saves through the shared rules) ─────────────

def _candidate(path: Path, cells, rows, cols, status="ready") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "1.0", "source_kind": "native", "table_id": "s0/b1/t0", "asset_id": "table-k",
                                "image_sha256": None, "status": status, "rows": rows, "cols": cols, "cells": cells},
                               ensure_ascii=False), "utf-8")
    return path


def _cell(r, c, text, **kw):
    return {"row": r, "col": c, "row_span": 1, "col_span": 1, "text": text, "bbox_px": None, "nested_table_ids": [], **kw}


def _state(tmp_path, cells, rows, cols, structure=False, history=()):
    f = tmp_path / "state.json"
    f.write_text(json.dumps({"rows": rows, "cols": cols, "cells": cells, "structure_changed": structure,
                             "history": list(history)}, ensure_ascii=False), "utf-8")
    return f


def test_table_save_writes_corrections_and_keeps_the_candidate(tmp_path):
    cand = _candidate(tmp_path / "tables" / "table-k.json", [_cell(0, 0, "구분"), _cell(0, 1, "금액")], 1, 2)
    before = cand.read_bytes()
    state = _state(tmp_path, [_cell(0, 0, "구분"), _cell(0, 1, "금액(백만원)", edited=True)], 1, 2)
    r = dw._dispatch(["table-save", str(cand), str(state)])
    assert r["saved"].endswith("table-k.edited.json") and r["edited_cells"] == 1 and r["differs"] is True
    assert cand.read_bytes() == before
    saved = json.loads(Path(r["saved"]).read_text("utf-8"))
    assert saved["edits"]["cells"] == 1 and saved["review"]["confirmed_by_user"] is True


def test_table_save_back_to_the_candidate_removes_the_corrections(tmp_path):
    cand = _candidate(tmp_path / "tables" / "table-k.json", [_cell(0, 0, "구분")], 1, 1)
    dw.table_save(cand, _state(tmp_path, [_cell(0, 0, "항목", edited=True)], 1, 1))
    r = dw.table_save(cand, _state(tmp_path, [_cell(0, 0, "구분")], 1, 1))
    assert r["saved"] is None and not (tmp_path / "tables" / "table-k.edited.json").exists()


def test_table_save_keeps_engine_extras_and_structure_history(tmp_path):
    cand = _candidate(tmp_path / "tables" / "t.image.json",
                      [_cell(0, 0, "가", text_check="review", bbox_px=[0, 0, 10, 10])], 1, 1, status="review")
    cells = [_cell(0, 0, "가", text_check="review", bbox_px=[0, 0, 10, 10]), _cell(1, 0, "", edited=True)]
    r = dw.table_save(cand, _state(tmp_path, cells, 2, 1, True, [{"op": "insert_row", "at": 1}]))
    saved = json.loads(Path(r["saved"]).read_text("utf-8"))
    first = next(c for c in saved["cells"] if (c["row"], c["col"]) == (0, 0))
    assert first["text_check"] == "review" and first["bbox_px"] == [0, 0, 10, 10]
    assert saved["edits"]["history"] == [{"op": "insert_row", "at": 1}] and saved["edits"]["structure_changed"] is True
    assert r["confirmed"] is True


def test_gpqt_uses_the_same_rules():
    import gpdesk.table_edit as shared
    import gpqt.table_edit as gui

    assert gui.TableDoc is shared.TableDoc
