import json
import shutil

import pytest
from pathlib import Path

from gpdesk.figure_service import FigureService

FIX = Path(__file__).resolve().parents[1] / "samples" / "synthetic-figures.hwpx"


def load(reply):
    assert reply["ok"], reply
    return json.loads(Path(reply["file"]).read_text("utf-8"))


def test_open_decide_rerun_restore(tmp_path):
    src = tmp_path / "doc.hwpx"
    shutil.copy2(FIX, src)
    svc = FigureService(tmp_path / "decisions")
    data = load(svc.handle({"cmd": "open", "src": str(src), "out": str(tmp_path / "o1")}))
    assert data["summary"]["total"] == 6 and len(data["links"]) == 6
    first = data["links"][1]
    assert Path(first["thumb"]).is_file() and first["path_text"].count("→") == 1
    assert Path(first["asset_file"]).read_bytes()[:4] == b"\x89PNG"
    assert any(p["text"] == "<그림 B> 두 그림의 공통 설명" for p in first["pick_list"])
    ref = [p for p in first["pick_list"] if p["text"].startswith("<그림 B>")][0]["ref"]
    ids = [data["links"][1]["occurrence"]["occ_id"], data["links"][2]["occurrence"]["occ_id"]]
    data = load(svc.handle({"cmd": "decide", "action": "share", "occ_ids": ids, "para_refs": [ref]}))
    assert data["links"][1]["status"] == "user_shared"
    data = load(svc.handle({"cmd": "decide", "action": "none", "occ_ids": [ids[0]]}))
    data = load(svc.handle({"cmd": "decide", "action": "undo"}))
    assert data["links"][1]["status"] == "user_shared"
    # a new service (app restart) + re-extraction restores the same decision
    svc2 = FigureService(tmp_path / "decisions")
    again = load(svc2.handle({"cmd": "open", "src": str(src), "out": str(tmp_path / "o2")}))
    assert again["links"][1]["status"] == again["links"][2]["status"] == "user_shared"
    assert again["summary"]["user_confirmed"] == 2


@pytest.mark.live
def test_hwp_source_reviewed_via_hancom_copy(tmp_path, monkeypatch):
    """Real HWP made by Hancom from the fixture: same occurrences, decisions keyed by the HWP's hash."""
    from gpdesk.desktop_worker import hancom

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    hwp = tmp_path / "doc.hwp"
    hancom("save", FIX, hwp, "HWP")
    svc = FigureService(tmp_path / "dec")
    data = load(svc.handle({"cmd": "open", "src": str(hwp), "out": str(tmp_path / "o")}))
    assert data["summary"]["total"] == 6 and data["summary"]["auto_explicit"] == 1
    assert "HWP 원본" in data["note"]
    import hashlib

    assert data["source_sha256"] == hashlib.sha256(hwp.read_bytes()).hexdigest()


@pytest.mark.live
def test_hwp_input_reports_cleanly(tmp_path):
    bad = tmp_path / "x.hwp"
    bad.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 64)
    r = FigureService(tmp_path / "d").handle({"cmd": "open", "src": str(bad), "out": str(tmp_path / "o")})
    # Hancom force-opens unknown bytes as text: either a clean error or an empty review, never a crash
    assert ("HWPX" in r["error"]) if not r["ok"] else load(r)["summary"]["total"] == 0


def test_serve_replies_on_stdout_without_eof(tmp_path):
    """GUI keeps stdin open; each reply must arrive on stdout, one line per command."""
    import os
    import subprocess
    import sys
    import threading

    root = Path(__file__).resolve().parents[1]
    p = subprocess.Popen([sys.executable, "-X", "utf8", "-c",
                          "import sys; from gpdesk.figure_service import serve; sys.exit(serve(sys.argv[1]))",
                          str(tmp_path / "dec")], cwd=root, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        cmd = {"cmd": "open", "src": str(FIX), "out": str(tmp_path / "o")}
        p.stdin.write((json.dumps(cmd) + "\n").encode())
        p.stdin.flush()
        got = {}
        t = threading.Thread(target=lambda: got.setdefault("line", p.stdout.readline()))
        t.start()
        t.join(60)
        assert json.loads(got["line"])["ok"] is True
    finally:
        p.kill()
        _ = os


def test_service_sets_text_caption(tmp_path):
    src = tmp_path / "doc.hwpx"
    shutil.copy2(FIX, src)
    svc = FigureService(tmp_path / "dec")
    data = load(svc.handle({"cmd": "open", "src": str(src), "out": str(tmp_path / "o")}))
    oid = data["links"][5]["occurrence"]["occ_id"]
    data = load(svc.handle({"cmd": "decide", "action": "text", "occ_ids": [oid], "text": "직접 쓴 설명", "origin": "typed"}))
    assert data["links"][5]["caption_text"] == "직접 쓴 설명" and data["links"][5]["status"] == "user_linked"
    assert not svc.handle({"cmd": "decide", "action": "text", "occ_ids": [oid], "text": " ", "origin": "typed"})["ok"]


def test_locations_computed_once_per_document(tmp_path, monkeypatch):
    """Saving a caption must not re-render every page (TIPS: ~20 s per save before this fix)."""
    from gpdesk import figure_service

    calls = []
    real = figure_service.locate
    monkeypatch.setattr(figure_service, "locate", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    src = tmp_path / "doc.hwpx"
    shutil.copy2(FIX, src)
    svc = FigureService(tmp_path / "dec")
    data = load(svc.handle({"cmd": "open", "src": str(src), "out": str(tmp_path / "o")}))
    oid = data["links"][5]["occurrence"]["occ_id"]
    for text in ("하나", "둘", "셋"):
        data = load(svc.handle({"cmd": "decide", "action": "text", "occ_ids": [oid], "text": text, "origin": "typed"}))
    assert len(calls) == 1
    assert data["links"][5]["location"]["verified"] is True  # cached location still attached
    load(svc.handle({"cmd": "rerun"}))
    assert len(calls) == 2  # re-extraction recomputes


def test_source_kind_from_document_structure(tmp_path):
    """'원본 유형' = picture file format + where it sits; only facts read from the document."""
    src = tmp_path / "doc.hwpx"
    shutil.copy2(FIX, src)
    data = load(FigureService(tmp_path / "dec").handle({"cmd": "open", "src": str(src), "out": str(tmp_path / "o")}))
    kinds = [l["source_kind"] for l in data["links"]]
    assert kinds[0] == "그림(PNG) · 표 안 (중첩 표)"
    assert kinds[4] == "그림(PNG) · 본문"
    assert kinds[5] == "그림(PNG) · 표 안"
