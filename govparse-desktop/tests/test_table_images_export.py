"""JSON + PNG export links every table block to its own PNG(s) via the engine's per-table renderer.

Contract (table-images-gui-handoff.md, engine 66bde41): table_id from the IR, status rendered | unmatched | failed,
ordered images relative to the result folder, reason when no image. Page captures are not presented as a table's image.
"""

import json
import shutil
from pathlib import Path

import pytest

from gpdesk import desktop_worker as dw

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    out = tmp_path_factory.mktemp("tbl") / "res"
    out.mkdir()
    events = []
    rep = dw.run("json", SAMPLES / "synthetic-figures.hwpx", out, progress=events.append)
    return out, rep, events


def _tables(ir):
    from govparse.table_images import iter_table_nodes

    return list(iter_table_nodes(ir))


def test_every_table_block_carries_its_id_and_image_status(exported):
    out, rep, _ = exported
    ir = json.loads((out / "document.json").read_text("utf-8"))["source_ir"]
    tables = _tables(ir)
    assert len(tables) == 3  # outer, nested inside a merged cell, body 1x1 frame
    for table_id, node in tables:
        assert node["table_id"] == table_id
        img = node["table_image"]
        assert img["status"] in ("rendered", "unmatched", "failed")
        if img["status"] == "rendered":
            assert img["paths"] and img["reason"] is None
            assert all((out / p).is_file() and p.startswith("assets/tables/") for p in img["paths"])
        else:
            assert img["paths"] == [] and img["reason"]
    assert len({id_ for id_, _ in tables}) == 3  # parent and nested table are distinct


def test_bundle_lists_table_images_not_page_captures(exported):
    out, rep, _ = exported
    meta = json.loads((out / "document.json").read_text("utf-8"))["govparse_desktop"]
    origins = {a["origin"] for a in meta["assets"]}
    assert "rendered_table_capture" not in origins and "table_image" in origins
    ti = meta["table_images"]
    assert ti["tables"] == 3 and ti["rendered"] + ti["not_rendered"] == 3
    assert ti["png"] == sum(a["origin"] == "table_image" for a in meta["assets"])
    for a in meta["assets"]:
        if a["origin"] == "table_image":
            assert a["table_id"] and 1 <= a["part"] <= a["parts"]
    assert (out / "assets" / "tables" / "manifest.json").is_file()
    assert rep["bundle"]["counts"]["table_image"] == ti["png"]


def test_progress_reports_tables(exported):
    _, _, events = exported
    stages = [e for e in events if e.get("stage") == "표 이미지"]
    assert stages and stages[-1]["done"] == stages[-1]["total"] == 3


def test_moved_result_still_shows_table_images(exported, tmp_path):
    out, _, _ = exported
    moved = tmp_path / "옮긴 결과 폴더"
    shutil.copytree(out, moved)
    idx = dw.index(moved / "document.json")
    tables = [a for a in idx["assets"] if a["origin"] == "table_image"]
    assert tables and all(Path(a["file"]).is_file() for a in tables)
    assert all(a["table_id"] and a["label"] for a in tables)
    assert idx["table_images"]["tables"] == 3


def test_falls_back_to_page_captures_when_engine_has_no_table_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr(dw, "_table_renderer", lambda: None)
    out = tmp_path / "res"
    out.mkdir()
    rep = dw.run("json", SAMPLES / "synthetic-figures.hwpx", out)
    meta = json.loads((out / "document.json").read_text("utf-8"))["govparse_desktop"]
    assert "table_image" not in {a["origin"] for a in meta["assets"]}
    assert meta["table_images"] is None
    assert any("표별 이미지" in n for n in rep["notes"])


def test_table_label_is_readable():
    assert dw.table_label("s0/b3/t0") == "본문 1번째 표"
    assert dw.table_label("s0/b3/t1/c1_0/t0") == "본문 2번째 표 › 2행 1열 칸 안 1번째 표"
