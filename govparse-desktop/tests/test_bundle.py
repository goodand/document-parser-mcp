import json

import pytest

from conftest import synthetic_ir
from gpdesk.bundle import OcrStatus, build_json_bundle, collect_table_pngs, parse_saved_count

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_parse_saved_count():
    assert parse_saved_count("표 이미지 31장 저장 (190 DPI)\n경로: X") == 31
    assert parse_saved_count("표가 없습니다. only_missed=False 로 다시 시도해 보십시오.") == 0
    assert parse_saved_count("PDF 가 없습니다: x") is None


def test_collect_all_31_plus(tmp_path):
    d = tmp_path / "img"
    d.mkdir()
    for i in range(35):
        (d / f"p{i + 1:04d}_t0.png").write_bytes(PNG)
    got = collect_table_pngs(d, expected=35)
    assert len(got) == 35 and got[0][0] == 1 and got[-1][0] == 35


def test_collect_detects_foreign_or_missing(tmp_path):
    d = tmp_path / "img"
    d.mkdir()
    (d / "p0001_t0.png").write_bytes(PNG)
    (d / "stray.png").write_bytes(PNG)
    with pytest.raises(ValueError):
        collect_table_pngs(d, expected=1)
    d2 = tmp_path / "img2"
    d2.mkdir()
    (d2 / "p0001_t0.png").write_bytes(PNG)
    with pytest.raises(ValueError):
        collect_table_pngs(d2, expected=2)


def test_bundle_assets_relative_and_provenance(tmp_path):
    out = tmp_path / "stage"
    out.mkdir()
    caps = tmp_path / "caps"
    caps.mkdir()
    for i in range(3):
        (caps / f"p{i + 1:04d}_t0.png").write_bytes(PNG)
    ocr = {"p0001_t0.png": OcrStatus("ok", ["가"]), "p0002_t0.png": OcrStatus("empty", []),
           "p0003_t0.png": OcrStatus("failed", [], "boom")}
    doc = build_json_bundle(out, synthetic_ir(), "합성.hwpx", "ab" * 32,
                            captures=collect_table_pngs(caps, 3), capture_state="ok",
                            embedded={1: ("image1.png", PNG)}, ocr=ocr)
    data = json.loads((out / "document.json").read_text("utf-8"))
    assert data["source_ir"]["body"][1]["cells"][2]["row_span"] == 2
    assets = data["govparse_desktop"]["assets"]
    assert {a["origin"] for a in assets} == {"embedded_picture", "rendered_table_capture"}
    for a in assets:
        assert not a["path"].startswith(("/", "..")) and ":" not in a["path"]
        assert (out / a["path"]).exists()
    caps_meta = [a for a in assets if a["origin"] == "rendered_table_capture"]
    assert {a["ocr"]["status"] for a in caps_meta} == {"ok", "empty", "failed"}
    assert all(a["table_ref"] is None and a["page"] for a in caps_meta)
    emb = [a for a in assets if a["origin"] == "embedded_picture"][0]
    assert emb["uri"] == "bin://1"
    assert doc["counts"]["rendered_table_capture"] == 3


def test_ocr_not_run_and_unverified_capture(tmp_path):
    out = tmp_path / "s"
    out.mkdir()
    doc = build_json_bundle(out, synthetic_ir(), "a.hwp", "0" * 64, captures=[],
                            capture_state="unverified", embedded={}, ocr={})
    assert doc["capture_state"] == "unverified"
    assert doc["counts"]["rendered_table_capture"] is None
    assert doc["counts"]["embedded_picture"] == 0
    assert doc["missing_pictures"] == ["bin://1"]
