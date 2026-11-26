"""Normal JSON + PNG exports preserve the engine's table IDs, selected images and source-page fallbacks."""

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
        assert img["choice"] in {"november", "october", "source_crop", "source_page", "review"}
        if img["status"] in {"rendered", "source_page"}:
            assert img["paths"]
            assert all((out / p).is_file() and p.startswith("assets/tables/") for p in img["paths"])
        else:
            assert img["paths"] == [] and img["reason"]
    assert len({id_ for id_, _ in tables}) == 3  # parent and nested table are distinct


def test_bundle_lists_table_images_not_page_captures(exported):
    out, rep, _ = exported
    meta = json.loads((out / "document.json").read_text("utf-8"))["govparse_desktop"]
    origins = {a["origin"] for a in meta["assets"]}
    assert "rendered_table_capture" not in origins and "table_source_page" in origins
    ti = meta["table_images"]
    assert ti["tables"] == 3 and ti["rendered"] + ti["not_rendered"] == 3
    assert ti["png"] == sum(a["origin"] == "table_image" for a in meta["assets"])
    for a in meta["assets"]:
        if a["origin"] in {"table_image", "table_source_page"}:
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
    tables = [a for a in idx["assets"] if a["origin"] in {"table_image", "table_source_page"}]
    assert len({a["table_id"] for a in tables}) == 3
    assert all(Path(a["file"]).is_file() for a in tables)
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


def test_selected_crops_context_pages_and_review_survive_zip(tmp_path, monkeypatch):
    import zipfile

    from govparse.table_images import iter_table_nodes

    def select(source, ir, out_dir):
        ids = [key for key, _ in iter_table_nodes(ir)]
        assert len(ids) == 3
        decisions = {
            ids[0]: {"choice": "source_crop", "paths": ["crop.png"], "source_pages": [3]},
            ids[1]: {"choice": "source_page", "paths": ["p3.png", "p4.png"], "source_pages": [3, 4]},
            ids[2]: {"choice": "review", "paths": [], "source_pages": [], "reason": "위치 미확인"},
        }
        out_dir.mkdir(parents=True)
        for name in ("crop.png", "p3.png", "p4.png"):
            shutil.copyfile(SAMPLES / "teal.png", out_dir / name)
        manifest = {"tables": decisions}
        (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return manifest

    monkeypatch.setattr(dw, "_table_renderer", lambda: (select, iter_table_nodes))
    out = tmp_path / "export"
    out.mkdir()
    dw.run("json", SAMPLES / "synthetic-figures.hwpx", out)
    archive = tmp_path / "export.zip"
    dw.zip_result(out, archive)
    shutil.rmtree(out)
    with zipfile.ZipFile(archive) as packed:
        packed.extractall(tmp_path / "moved")
    result = tmp_path / "moved" / "export" / "document.json"
    index = dw.index(result)
    tables = index["table_images"]
    assert (tables["rendered"], tables["source_page_tables"], len(tables["missing"])) == (1, 1, 1)
    assets = [a for a in index["assets"] if a.get("table_id")]
    assert [(a["origin"], a["choice"], a["page"]) for a in assets] == [
        ("table_image", "source_crop", None), ("table_source_page", "source_page", 3),
        ("table_source_page", "source_page", 4)]
    assert "원본 3쪽" in assets[0]["text"] and "원본 4쪽 전체" in assets[-1]["text"]
    assert tables["missing"][0]["reason"] == "위치 미확인"
    assert not index["missing"] and not index["integrity"]["changed"]
