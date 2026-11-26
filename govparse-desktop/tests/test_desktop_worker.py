import json
import shutil
import zipfile
from pathlib import Path

import pytest

from gpdesk import desktop_worker as dw

FIX = Path(__file__).resolve().parents[1] / "samples" / "synthetic-figures.hwpx"


@pytest.fixture
def src(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    p = tmp_path / "in" / "doc.hwpx"
    p.parent.mkdir()
    shutil.copy2(FIX, p)
    return p


@pytest.mark.parametrize("force_xml", [False, True])
def test_html_export_both_ir_routes(src, tmp_path, monkeypatch, force_xml):
    if force_xml:
        monkeypatch.setenv("GPDESK_FORCE_OWPML", "1")
    out = tmp_path / "o"
    out.mkdir()
    rep = dw.run("html", src, out, ocr=False)
    html = (out / "document.html").read_text("utf-8")
    assert html.count("<img") == 6 and "&lt;그림 A&gt; 사업 구성도" in html
    assert rep["ir_builder"] == ("gpdesk.owpml_ir" if force_xml else "rhwp")
    assert rep["figures"]["matched"] == 6


def test_hwpx_export_has_pictures_and_report(src, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    rep = dw.run("hwpx", src, out)
    hwpx = out / "document.hwpx"
    with zipfile.ZipFile(hwpx) as z:
        assert sum(1 for n in z.namelist() if n.startswith("BinData/")) == 4
    assert rep["verify"]["ok"] and (out / "document.links.json").exists()
    assert any("쪽 배치" in n for n in rep["notes"])
    assert json.loads((out / "report.json").read_text("utf-8"))["saved"] is True


def test_convert_from_existing_json(src, tmp_path):
    first = tmp_path / "j"
    first.mkdir()
    dw.run("json", src, first, ocr=False)
    for fmt in ("html", "hwpx"):
        out = tmp_path / f"c-{fmt}"
        out.mkdir()
        rep = dw.convert(first / "document.json", out, fmt)
        assert rep["saved"]
    html = (tmp_path / "c-html" / "document.html").read_text("utf-8")
    assert html.count("<img") == 6
    assert all((tmp_path / "c-html" / s).is_file() for s in dw.img_srcs(html))
    with zipfile.ZipFile(tmp_path / "c-hwpx" / "document.hwpx") as z:
        assert sum(1 for n in z.namelist() if n.startswith("BinData/")) == 4


def test_convert_rejects_unknown_json(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text('{"hello": 1}', "utf-8")
    with pytest.raises(ValueError):
        dw.convert(bad, tmp_path, "html")


def test_preview_page_count_and_renderer(src, tmp_path):
    png = tmp_path / "p.png"
    count, renderer = dw.preview(src, png, 0)
    assert count == 1 and renderer == "rhwp" and png.read_bytes()[:4] == b"\x89PNG"


@pytest.mark.live
def test_preview_falls_back_to_hancom(src, tmp_path, monkeypatch):
    monkeypatch.setenv("GPDESK_FORCE_OWPML", "1")
    png = tmp_path / "p.png"
    count, renderer = dw.preview(src, png, 0)
    assert count == 1 and renderer == "hancom-pdf" and png.read_bytes()[:4] == b"\x89PNG"


@pytest.mark.live
def test_hwp_export_real_ole(src, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    rep = dw.run("hwp", src, out)
    assert (out / "document.hwp").read_bytes()[:4] == b"\xd0\xcf\x11\xe0" and rep["reopened"] is True


@pytest.mark.live
def test_ocr_single_image(tmp_path):
    from samples_text import text_png

    png = tmp_path / "t.png"
    png.write_bytes(text_png("사업 구성도 2026"))
    res = dw.ocr(png)
    assert res["status"] in ("ok", "empty") and isinstance(res["texts"], list)
    assert res["status"] == "ok" and any("2026" in t for t in res["texts"])


def test_index_lists_assets_with_origin_and_caption(src, tmp_path):
    out = tmp_path / "j"
    out.mkdir()
    dw.run("json", src, out, ocr=False)
    idx = dw.index(out / "document.json")
    emb = [a for a in idx["assets"] if a["origin"] == "embedded_picture"]
    tbl = [a for a in idx["assets"] if a["origin"] in {"table_image", "table_source_page"}]
    assert len(emb) == 4 and len({a["table_id"] for a in tbl}) == 3
    assert all(Path(a["file"]).is_file() for a in idx["assets"])
    assert any("사업 구성도" in a["text"] for a in emb)
    assert all(a["table_id"] and a["label"] for a in tbl) and idx["convertible"] is True
    assert all(a["table_type"] in {"D", "M", "V", "?"} for a in tbl)
    assert all(a["table_type_label"].startswith("표 유형(추정) ") for a in tbl)


def test_index_keeps_unknown_type_when_optional_classifier_is_missing(tmp_path, monkeypatch):
    import sys

    result = tmp_path / "document.json"
    image = tmp_path / "assets" / "table.png"
    image.parent.mkdir()
    image.write_bytes(b"png")
    ir = {"body": [{
        "kind": "table", "rows": 2, "cols": 2, "cells": [
            {"row": 0, "col": 0, "row_span": 1, "col_span": 1,
             "blocks": [{"kind": "paragraph", "text": "분류"}]},
            {"row": 0, "col": 1, "row_span": 1, "col_span": 1,
             "blocks": [{"kind": "paragraph", "text": "결과"}]},
        ],
    }]}
    payload = {
        "source_ir": ir,
        "govparse_desktop": {
            "assets": [{"origin": "table_image", "path": "assets/table.png",
                        "table_id": "s0/b0/t0", "part": 1, "parts": 1}],
        },
    }
    result.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    monkeypatch.setitem(sys.modules, "govparse.table_use", None)

    row = dw.index(result)["assets"][0]

    assert row["table_type"] == "?"
    assert row["table_type_label"] == "표 유형(추정) 검토 필요"


def test_pad_for_ocr_adds_white_margin(tmp_path):
    import pymupdf
    import zipfile

    raw = zipfile.ZipFile(FIX).read("BinData/image4.png")
    src = tmp_path / "image4.png"
    src.write_bytes(raw)
    out = dw.pad_for_ocr(src, tmp_path / "pad")
    a, b = pymupdf.Pixmap(str(src)), pymupdf.Pixmap(str(out))
    assert out.name == src.name and out != src
    assert b.width == a.width + 2 * dw.OCR_PAD and b.height == a.height + 2 * dw.OCR_PAD
    assert tuple(b.pixel(2, 2)[:3]) == (255, 255, 255)
    assert src.read_bytes() == raw  # original untouched


@pytest.mark.live
def test_ocr_reads_text_touching_the_border(tmp_path):
    """Measured 2026-11-25: without padding the engine returned 사업/체/2026/거 for this picture."""
    import zipfile

    png = tmp_path / "image4.png"
    png.write_bytes(zipfile.ZipFile(FIX).read("BinData/image4.png"))
    joined = "".join(dw.ocr(png)["texts"]).replace(" ", "")
    assert "사업" in joined and "추진" in joined and "체계" in joined and "2026" in joined


def test_result_manifest_detects_changed_files(src, tmp_path):
    """Pattern from govparse parsed_store.py: published files carry sha256 and are re-verified on load."""
    out = tmp_path / "j"
    out.mkdir()
    rep = dw.run("json", src, out, ocr=False)
    assert rep["files"] and all(len(f["sha256"]) == 64 for f in rep["files"])
    assert dw.index(out / "document.json")["integrity"] == {"checked": len(rep["files"]), "changed": [], "missing": []}
    victim = next(out.glob("assets/embedded/*"))
    victim.write_bytes(b"tampered")
    integ = dw.index(out / "document.json")["integrity"]
    assert integ["changed"] == [victim.relative_to(out).as_posix()]
    victim.unlink()
    assert dw.index(out / "document.json")["missing"] or dw.index(out / "document.json")["integrity"]["missing"]


def test_index_without_manifest_says_unverified(tmp_path):
    (tmp_path / "document.json").write_text('{"body": []}', "utf-8")
    assert dw.index(tmp_path / "document.json")["integrity"] is None


def test_summary_counts_structure(src):
    s = dw.summary(src)
    assert s["builder"] == "rhwp" and s["pictures"] == 6 and s["tables"] == 3 and s["nested_tables"] == 1  # inner table in the merged cell; the frame table is in the body
    assert s["paragraphs"] > 0 and s["seconds"] >= 0 and s["source_sha256"]


def test_summary_large_route(src, monkeypatch):
    monkeypatch.setenv("GPDESK_FORCE_OWPML", "1")
    s = dw.summary(src)
    assert s["builder"] == "gpdesk.owpml_ir" and s["pictures"] == 6 and s["nested_tables"] == 1
    assert "미리보기" in s["preview_note"]


def test_json_export_reports_page_progress(src, tmp_path):
    events = []
    out = tmp_path / "o"
    out.mkdir()
    dw.run("json", src, out, ocr=False, progress=events.append)
    stages = [e["stage"] for e in events]
    assert stages[0] == "구조 읽기" and "표 이미지" in stages and stages[-1] == "저장"
    tables = [e for e in events if e["stage"] == "표 이미지"]
    assert tables[-1]["done"] == tables[-1]["total"] >= 1
    assert all(0 <= e.get("done", 0) <= e.get("total", 0) for e in events if "total" in e)


def test_progress_line_format(capsys):
    dw.emit_progress({"stage": "표 찾기", "done": 3, "total": 10})
    err = capsys.readouterr().err.strip()
    assert err.startswith("@@progress ") and json.loads(err[len("@@progress "):])["done"] == 3


def test_native_stdout_noise_never_reaches_the_result(tmp_path):
    """2026-11-25 TIPS export failed after 10 min: a native library wrote "svg ..." straight to fd 1,
    so the GUI could not parse the result JSON. Anything written to fd 1 during work must go to stderr."""
    import subprocess
    import sys
    code = "\n".join([
        "import os, sys",
        "from gpdesk import desktop_worker as dw",
        "def noisy(argv):",
        "    os.write(1, b'svg: native warning written to fd 1' + bytes([10]))",
        "    print('python print during work')",
        "    return {'ok': True}",
        "sys.exit(dw.protected_main(noisy, sys.argv[1:]))",
    ])
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c", code, "x"], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"ok": True}
    assert "native warning" in r.stderr and "python print" in r.stderr


def test_pad_for_ocr_shrinks_large_pictures(tmp_path):
    """Measured 2026-11-25: a 2154x477 logo read as '소 / ㅇ'; scaled to <=1200 px wide it read correctly."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=1600, height=360)
    big = tmp_path / "big.png"
    page.get_pixmap(dpi=144).save(str(big))  # 3200 x 720
    out = dw.pad_for_ocr(big, tmp_path / "pad")
    p = pymupdf.Pixmap(str(out))
    assert max(p.width, p.height) <= dw.OCR_MAX_SIDE + 2 * dw.OCR_PAD
    small = tmp_path / "small.png"
    pymupdf.open().new_page(width=200, height=60).get_pixmap().save(str(small))
    q = pymupdf.Pixmap(str(dw.pad_for_ocr(small, tmp_path / "pad2")))
    assert (q.width, q.height) == (200 + 2 * dw.OCR_PAD, 60 + 2 * dw.OCR_PAD)  # small ones are not resized


@pytest.mark.live
def test_ocr_reads_large_logo_text(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=1080, height=240)
    page.insert_font(fontname="mg", fontfile=r"C:\Windows\Fonts\malgun.ttf")
    page.insert_text((60, 150), "중소벤처기업부", fontname="mg", fontsize=96)
    png = tmp_path / "logo.png"
    page.get_pixmap(dpi=144).save(str(png))  # 2160 x 480, text close to the size of the real logo
    assert "중소벤처기업부" in "".join(dw.ocr(png)["texts"]).replace(" ", "")


def _fake_engine(monkeypatch, engine, results):
    """dw.ocr with the chosen engine and a recorded runner; results are returned call by call."""
    from gpdesk import ocr_engines, settings

    calls = []
    monkeypatch.setattr(settings, "load", lambda: {"ocr_engine": engine})
    monkeypatch.setattr(ocr_engines, "is_available", lambda e: (True, ""))

    def runner(png, **kw):
        calls.append((Path(png), kw))
        return dict(results[min(len(calls), len(results)) - 1])

    monkeypatch.setitem(ocr_engines.RUNNERS, engine, runner)
    return calls


def _white_png(path, w=300, h=120):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=w, height=h).get_pixmap(alpha=False).save(str(path))
    return path


def test_rapidocr_gets_the_original_picture(tmp_path, monkeypatch):
    """The engine shrinks and pads once (recognize_image); the GUI must not preprocess first."""
    png = _white_png(tmp_path / "a.png")
    calls = _fake_engine(monkeypatch, "rapidocr", [{"status": "ok", "texts": ["가"], "error": "", "preprocess": "engine"}])
    res = dw.ocr(png)
    assert [c[0] for c in calls] == [png] and res["status"] == "ok" and res["preprocess"] == "engine"


def test_rapidocr_old_engine_falls_back_to_gui_preprocessing(tmp_path, monkeypatch):
    """An engine without the shared OCR function answers needs_preprocess; then the GUI pads as before."""
    png = _white_png(tmp_path / "a.png")
    calls = _fake_engine(monkeypatch, "rapidocr", [{"status": "needs_preprocess", "texts": [], "error": ""},
                                                   {"status": "ok", "texts": ["가"], "error": "", "preprocess": "gui"}])
    res = dw.ocr(png)
    assert len(calls) == 2 and calls[1][0] != png and calls[1][1] == {"legacy": True}
    assert res["status"] == "ok" and res["preprocess"] == "gui"


def test_windows_ocr_keeps_gui_preprocessing(tmp_path, monkeypatch):
    import pymupdf

    png = _white_png(tmp_path / "a.png")
    calls = _fake_engine(monkeypatch, "windows", [{"status": "empty", "texts": [], "error": ""}])
    dw.ocr(png)
    assert len(calls) == 1 and calls[0][0] != png
    assert pymupdf.Pixmap(str(calls[0][0])).width == 300 + 2 * dw.OCR_PAD


def _result_folder(root):
    folder = root / "doc-json"
    (folder / "assets").mkdir(parents=True)
    (folder / "document.json").write_text('{"a": 1}', "utf-8")
    (folder / "report.json").write_text('{"files": []}', "utf-8")
    (folder / "assets" / "bin1.png").write_bytes(b"\x89PNG fake")
    return folder


def test_zip_result_keeps_the_folder_layout(tmp_path):
    """The zip opens to one folder with the same relative paths, so JSON image links still resolve."""
    import zipfile

    folder = _result_folder(tmp_path)
    events = []
    rep = dw.zip_result(folder, tmp_path / "doc-json.zip", progress=events.append)
    z = Path(rep["zip"])
    assert z == tmp_path / "doc-json.zip" and rep["files"] == 3 and rep["bytes"] == z.stat().st_size
    with zipfile.ZipFile(z) as f:
        assert sorted(f.namelist()) == ["doc-json/assets/bin1.png", "doc-json/document.json", "doc-json/report.json"]
        assert f.read("doc-json/assets/bin1.png") == b"\x89PNG fake" and f.testzip() is None
    assert events and events[-1]["stage"] == "압축" and events[-1]["done"] == 3
    assert not list(tmp_path.glob(".govparse-zip-*"))  # no temporary file left


def test_zip_result_never_overwrites(tmp_path):
    folder = _result_folder(tmp_path)
    (tmp_path / "doc-json.zip").write_bytes(b"someone else's file")
    rep = dw.zip_result(folder, tmp_path / "doc-json.zip")
    assert (tmp_path / "doc-json.zip").read_bytes() == b"someone else's file"
    assert Path(rep["zip"]) != tmp_path / "doc-json.zip" and Path(rep["zip"]).is_file()


def test_zip_mode_through_dispatch(tmp_path):
    folder = _result_folder(tmp_path)
    rep = dw._dispatch(["zip", str(folder), str(tmp_path / "out.zip")])
    assert Path(rep["zip"]).is_file()


def test_preview_overlap_risk_finds_picture_then_table_in_one_cell():
    """rhwp draws a picture and a later table in the same cell over each other; the app warns about such places."""
    from gpdesk.desktop_worker import preview_overlap_risk

    def para(t=""):
        return {"kind": "paragraph", "text": t}

    pic = {"kind": "paragraph", "text": "", "inlines": [{"kind": "picture", "image": {"uri": "bin://1"}}]}
    inner = {"kind": "table", "rows": 1, "cols": 1, "cells": [{"row": 0, "col": 0, "blocks": [para("안쪽")]}]}
    risky = {"kind": "table", "rows": 1, "cols": 1, "cells": [{"row": 0, "col": 0, "blocks": [pic, para(), inner]}]}
    table_first = {"kind": "table", "rows": 1, "cols": 1, "cells": [{"row": 0, "col": 0, "blocks": [inner, pic]}]}
    plain = {"kind": "table", "rows": 1, "cols": 1, "cells": [{"row": 0, "col": 0, "blocks": [para("글자")]}]}
    assert preview_overlap_risk({"body": [para("제목"), plain, table_first]}) == {"count": 0, "labels": []}
    assert preview_overlap_risk({"body": [plain, risky]}) == {"count": 1, "labels": ["본문 2번째 표"]}
    assert preview_overlap_risk({"body": [para("7) 사업  절차"), risky]}) == {"count": 1, "labels": ["‘7) 사업 절차’ 아래 표"]}


def test_summary_reports_preview_overlap(tmp_path):
    from gpdesk import desktop_worker as dw

    s = dw.summary(Path(__file__).resolve().parents[1] / "samples" / "synthetic-figures.hwpx")
    assert set(s["preview_overlap"]) == {"count", "labels"} and s["preview_overlap"]["count"] >= 0
