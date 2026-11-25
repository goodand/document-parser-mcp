"""Live tests: real govparse engine (MCP stdio) and real Hancom COM on this PC.

Run: .venv/Scripts/python -m pytest tests/test_pipeline_live.py -m live
"""

import hashlib
import json
import shutil
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

from gpdesk import config
from gpdesk.jobs import JobRegistry
from gpdesk.pipeline import Workspace, verify_with_hancom
from gpdesk.publish import PublishError

pytestmark = pytest.mark.live
SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    c = config.load()
    c.data_root = tmp_path_factory.mktemp("gpdata")
    return c


@pytest.fixture(scope="module")
def merged(cfg, tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    src = d / "synthetic-merged.hwpx"
    shutil.copy2(SAMPLES / "synthetic-merged.hwpx", src)
    (d / "neighbour.hwp").write_bytes(b"must not be collected")
    job = JobRegistry().open(src)
    ws = Workspace(cfg, job)
    ws.prepare()
    return ws


def test_prepare_copies_only_dropped_file(merged):
    assert [p.name for p in merged.input_dir.iterdir()] == ["synthetic-merged.hwpx"]
    assert merged.doc_name.endswith("synthetic-merged.hwpx")
    assert "parse_hwp_source" in merged.tools and "restore_hwp_body" in merged.tools


def test_preview_pdf(merged):
    pdf, pages = merged.preview_pdf()
    assert pdf.exists() and pages == 1


def test_json_png_export(merged, tmp_path):
    before = sha(merged.job.source)
    rep = merged.export("json", tmp_path / "out")
    data = json.loads((rep.final_path / "document.json").read_text("utf-8"))
    meta = data["govparse_desktop"]
    assert meta["counts"]["embedded_picture"] == 1
    assert meta["counts"]["rendered_table_capture"] >= 1
    outer = [n for n in data["source_ir"]["body"] if n.get("kind") == "table"][0]
    merged_cell = [c for c in outer["cells"] if c["row_span"] == 2][0]
    assert merged_cell["col_span"] == 2 and any(b.get("kind") == "table" for b in merged_cell["blocks"])
    for a in meta["assets"]:
        assert (rep.final_path / a["path"]).is_file()
    assert sha(merged.job.source) == before


class Img(HTMLParser):
    def __init__(self):
        super().__init__()
        self.srcs = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.srcs.append(dict(attrs)["src"])


def test_html_export_relocatable(merged, tmp_path):
    rep = merged.export("html", tmp_path / "h")
    moved = tmp_path / "moved" / "elsewhere"
    shutil.move(str(rep.final_path), moved)
    html = (moved / "index.html").read_text("utf-8")
    p = Img()
    p.feed(html)
    assert p.srcs and all((moved / s).is_file() for s in p.srcs)
    assert "http://" not in html and "https://" not in html
    assert 'rowspan="2"' in html and "일억 이천만 원" in html


def test_hwpx_export_and_reopen(merged, tmp_path):
    rep = merged.export("hwpx", tmp_path / "r.hwpx")
    assert rep.final_path.suffix == ".hwpx"
    assert zipfile.is_zipfile(rep.final_path)
    assert verify_with_hancom(merged.cfg, rep.final_path) >= 1
    assert "그림" in " ".join(rep.notes)


def test_hwp_export_real_binary(merged, tmp_path):
    rep = merged.export("hwp", tmp_path / "r.hwp")
    assert rep.final_path.read_bytes()[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    assert verify_with_hancom(merged.cfg, rep.final_path) >= 1


def test_block_export_onto_source(merged):
    with pytest.raises(PublishError):
        merged.export("hwpx", merged.job.source)


def test_existing_result_not_overwritten(merged, tmp_path):
    first = merged.export("html", tmp_path / "x")
    stamp = sha(first.final_path / "index.html")
    second = merged.export("html", tmp_path / "x")
    assert second.final_path != first.final_path
    assert sha(first.final_path / "index.html") == stamp


def test_31_plus_captures(cfg, tmp_path):
    src = tmp_path / "s" / "synthetic-35tables.hwpx"
    src.parent.mkdir()
    shutil.copy2(SAMPLES / "synthetic-35tables.hwpx", src)
    ws = Workspace(cfg, JobRegistry().open(src))
    ws.prepare()
    ws.preview_pdf()
    rep = ws.export("json", tmp_path / "o", ocr=False)
    meta = json.loads((rep.final_path / "document.json").read_text("utf-8"))["govparse_desktop"]
    assert meta["counts"]["rendered_table_capture"] >= 35
    assert all(a["ocr"]["status"] == "not_run" for a in meta["assets"]
               if a["origin"] == "rendered_table_capture")


def test_json_when_preview_failed(cfg, tmp_path):
    src = tmp_path / "s" / "synthetic-merged.hwpx"
    src.parent.mkdir()
    shutil.copy2(SAMPLES / "synthetic-merged.hwpx", src)
    ws = Workspace(cfg, JobRegistry().open(src))
    ws.prepare()
    ws.preview_error = "한컴 변환 실패(시험)"  # no PDF available
    rep = ws.export("json", tmp_path / "o")
    meta = json.loads((rep.final_path / "document.json").read_text("utf-8"))["govparse_desktop"]
    assert meta["capture_state"] == "unverified"
    assert meta["counts"]["rendered_table_capture"] is None
    # retry keeps the earlier JSON
    ws.preview_error = None
    ws.preview_pdf()
    rep2 = ws.export("json", tmp_path / "o")
    assert rep2.final_path != rep.final_path and (rep.final_path / "document.json").exists()
