"""Settings dialog (A14), MCP bridge (A15), per-OS behavior (SPEC.md §6)."""

import json
import sys

import pytest
from PySide6.QtCore import Qt

from conftest import click, find, open_doc, text, wait_status
from test_figures import SHA, figs, panel, select  # noqa: F401  (fixtures reused)


# ── settings ─────────────────────────────────────────────────────────────────

@pytest.fixture
def settings_dialog(window, qtbot):
    d = window.make_settings_dialog()
    qtbot.addWidget(d)
    d.show()
    qtbot.waitUntil(lambda: find(d, "settingsSave").isEnabled())
    return d


def test_settings_lists_engines(settings_dialog):
    r = find(settings_dialog, "engine_rapidocr")
    w = find(settings_dialog, "engine_windows")
    assert r.isChecked() and r.isEnabled() and not w.isEnabled()
    assert "사용할 수 없음 · Windows에서만 쓸 수 있음" in settings_dialog.engine_description("windows")
    assert text(settings_dialog, "settingsPath").startswith("설정 파일: ")


def test_settings_save(window, settings_dialog, qtbot, fake_worker):
    fake_worker.reply["settings-set"] = fake_worker.reply["settings"]
    click(qtbot, settings_dialog, "settingsSave")
    qtbot.waitUntil(lambda: any(c.mode == "settings-set" for c in fake_worker.calls))
    c = next(c for c in fake_worker.calls if c.mode == "settings-set")
    assert c.args == ["ocr_engine", "rapidocr"]


def test_settings_save_failure(settings_dialog, qtbot, fake_worker):
    fake_worker.reply["settings-set"] = OSError("쓰기 금지")
    click(qtbot, settings_dialog, "settingsSave")
    qtbot.waitUntil(lambda: text(settings_dialog, "settingsPath") == "저장하지 못했습니다 · 쓰기 금지")
    assert find(settings_dialog, "settingsSave").isEnabled()


def test_settings_read_failure(window, qtbot, fake_worker):
    fake_worker.reply["settings"] = RuntimeError("워커 없음")
    d = window.make_settings_dialog()
    qtbot.addWidget(d)
    d.show()
    qtbot.waitUntil(lambda: "설정을 읽지 못했습니다 · 워커 없음" in d.engines_text())
    assert not find(d, "settingsSave").isEnabled()


def test_settings_escape_cancels(settings_dialog, qtbot, fake_worker):
    qtbot.keyClick(settings_dialog, Qt.Key_Escape)
    assert not settings_dialog.isVisible() and not any(c.mode == "settings-set" for c in fake_worker.calls)


def test_settings_saved_message(window, qtbot, fake_worker):
    fake_worker.reply["settings-set"] = fake_worker.reply["settings"]
    window.after_settings_saved("RapidOCR · PP-OCRv5 한국어 (mobile)")
    wait_status(qtbot, window, "OCR 엔진을 바꿨습니다 · RapidOCR · PP-OCRv5 한국어 (mobile)")


# ── MCP bridge: same status codes as the Windows app, measured 2026-11-25 ─────

def test_state_without_document(window):
    r = window.bridge_request("state")
    assert r == {"status": "open", "title": "GovParse Desktop", "view": "original", "source_sha256": None, "item_id": None, "page": None}


def test_state_reports_user_selection(panel):
    select(panel, 3)
    r = panel.bridge_request("state")
    assert (r["source_sha256"], r["item_id"], r["page"], r["view"]) == (SHA, "s0-pic3", 1, "original")


def test_show_reselects_and_jumps(panel, qtbot):
    select(panel, 1)
    r = panel.bridge_request(f"show|{SHA}|s0-pic4")
    assert r["status"] == "selected" and r["item_id"] == "s0-pic4"
    qtbot.waitUntil(lambda: text(panel, "lblPage") == "2 / 3")
    assert panel.overlay_state()["label"] == "그림"


@pytest.mark.parametrize("line,status", [
    (f"show|{'0' * 64}|s0-pic3", "document_mismatch"),
    (f"show|{SHA}|s0-nope", "item_missing_or_filtered"),
    (f"show|{SHA}|s0-pic2", "location_unverified"),
    ("bogus", "invalid_request"),
])
def test_rejections_keep_user_selection(panel, qtbot, line, status):
    """A rejected request changes nothing the user sees: selection, tab, page, document (engine review 1)."""
    select(panel, 1)
    before = panel.bridge_request("state")
    r = panel.bridge_request(line)
    assert r["status"] == status
    assert {k: r[k] for k in ("item_id", "view", "page", "source_sha256")} == \
           {k: before[k] for k in ("item_id", "view", "page", "source_sha256")}


def test_show_with_panel_closed(panel, qtbot):
    click(qtbot, panel, "btnFigures")
    assert panel.bridge_request(f"show|{SHA}|s0-pic3")["status"] == "figure_panel_unavailable"


def test_show_filtered_out_item(panel):
    find(panel, "figFilter").setCurrentText("설명 있는 그림만")
    assert panel.bridge_request(f"show|{SHA}|s0-pic3")["status"] == "item_missing_or_filtered"


def test_switch_views(window, qtbot, tmp_path):
    assert window.bridge_request("result")["status"] == "view_unavailable"
    h = tmp_path / "r.html"
    h.write_text("<p>x</p>")
    window.load_result(str(h))
    r = window.bridge_request("original")
    assert (r["status"], r["view"]) == ("shown", "original")
    r = window.bridge_request("result")
    assert (r["status"], r["view"]) == ("shown", "result")


def test_show_from_result_tab_with_figure_list_ready(panel, tmp_path):
    """Windows code path, kept (formal/gui-spec C1). Not yet observed on the real Windows window:
    the 2026-11-25 experiment covered this request only with the list closed (next test)."""
    h = tmp_path / "r.html"
    h.write_text("<p>x</p>")
    panel.load_result(str(h))
    r = panel.bridge_request(f"show|{SHA}|s0-pic3")
    assert (r["status"], r["view"], r["item_id"]) == ("selected", "original", "s0-pic3")


def test_show_from_result_tab_with_figure_list_closed(panel, qtbot, tmp_path):
    """Measured on Windows 2026-11-25 (experiment row 11)."""
    click(qtbot, panel, "btnFigures")
    h = tmp_path / "r.html"
    h.write_text("<p>x</p>")
    panel.load_result(str(h))
    r = panel.bridge_request(f"show|{SHA}|s0-pic3")
    assert (r["status"], r["view"], r["item_id"]) == ("figure_panel_unavailable", "result", None)


def test_modal_dialog_blocks_bridge(window, qtbot):
    """Found 2026-11-25 in the Windows app: requests must not act behind an open dialog."""
    d = window.make_settings_dialog()
    d.setModal(True)
    d.open()
    qtbot.waitUntil(d.isVisible)
    assert window.bridge_request("original")["status"] == "dialog_open"
    assert window.bridge_request("result")["status"] == "dialog_open"
    assert window.bridge_request(f"show|{SHA}|s0-pic3")["status"] == "dialog_open"
    d.reject()


def test_bridge_transport_is_private(window, tmp_path):
    """Windows: named pipe for the current user. macOS/Linux: socket 0600 in a directory owned by the user, 0700
    (engine review 2): <XDG_RUNTIME_DIR or per-user temp>/govparse-desktop/app-<pid>.sock."""
    import os

    addr = window.bridge_address()
    if sys.platform == "win32":
        assert addr.startswith(r"\\.\pipe\GovParseDesktop-")
    else:
        import stat

        parent = os.path.dirname(addr)
        assert os.path.basename(addr) == f"app-{os.getpid()}.sock" and os.path.basename(parent) == "govparse-desktop"
        assert stat.S_IMODE(os.stat(addr).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(parent).st_mode) == 0o700 and os.stat(parent).st_uid == os.getuid()
        assert len(addr.encode()) < 104          # sun_path limit (macOS 104, Linux 108)


def test_bridge_round_trip_over_transport(window, qtbot):
    """Another process finds the app on its own (no address given), connects and gets an answer,
    while the Qt event loop keeps running (engine review 2)."""
    import subprocess

    from conftest import APP

    code = ("import json,sys; sys.path.insert(0, sys.argv[1]); from gpqt.bridge import discover, request; "
            "found = discover(); print(json.dumps({'found': found.status, 'reply': request(found.address, 'state', timeout=3)}))")
    p = subprocess.Popen([sys.executable, "-c", code, str(APP)], stdout=subprocess.PIPE, text=True)
    qtbot.waitUntil(lambda: p.poll() is not None, timeout=8000)   # waiting here keeps the GUI event loop alive
    out = json.loads(p.stdout.read())
    assert out["found"] == "one" and out["reply"]["status"] == "open"


@pytest.mark.parametrize("apps,stale,expected", [(0, 0, "app_closed"), (1, 0, "one"), (2, 0, "multiple_windows"), (0, 1, "app_closed")])
def test_bridge_discovery_states(tmp_path, monkeypatch, apps, stale, expected):
    """0 / 1 / several live apps, and a socket left behind by a crashed app is not counted as live."""
    from gpqt.bridge import discover

    assert discover(_fake_runtime(tmp_path, monkeypatch, apps, stale)).status == expected


def _fake_runtime(tmp_path, monkeypatch, apps, stale):
    """Bridge directory with `apps` listening sockets and `stale` dead socket files (non-Windows)."""
    import os
    import socket

    if sys.platform == "win32":
        pytest.skip("Unix sockets: macOS/Linux")
    d = tmp_path / "govparse-desktop"
    d.mkdir(mode=0o700)
    keep = []
    for i in range(apps):
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(d / f"app-{1000 + i}.sock"))
        s.listen(1)
        keep.append(s)
    for i in range(stale):
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(d / f"app-{2000 + i}.sock"))
        s.close()
    monkeypatch.setattr(sys.modules[__name__], "_keep", keep, raising=False)
    return d


# ── per-OS behavior (SPEC.md §6) ─────────────────────────────────────────────

def test_settings_folder_per_os(user_dirs):
    from gpqt.platform import settings_dir

    d = str(settings_dir())
    if sys.platform == "win32":
        assert d.endswith("GovParse Desktop")
    elif sys.platform == "darwin":
        assert d.endswith("Library/Application Support/GovParse Desktop")
    else:
        assert d.endswith("govparse-desktop")


@pytest.mark.parametrize("hancom", [True, False])
def test_hancom_option_follows_platform(qtbot, fake_worker, dialogs, user_dirs, hancom):
    """Feature toggle by FakePlatform (moved from test_integration, engine review 6)."""
    from conftest import FakePlatform
    from gpqt.app import MainWindow

    w = MainWindow(worker=fake_worker, dialogs=dialogs, platform=FakePlatform(hancom=hancom))
    qtbot.addWidget(w)
    hwp = dict((l, e) for l, _, e in w.format_options())["한글 문서 · HWP (.hwp)"]
    assert hwp is hancom


@pytest.mark.skipif(sys.platform == "win32", reason="Linux/macOS path rules")
def test_same_path_guard_respects_case_sensitivity(tmp_path):
    from gpqt.paths import same_path

    a = tmp_path / "Doc.hwpx"
    a.write_bytes(b"x")
    b = tmp_path / "doc.hwpx"
    case_insensitive_fs = b.exists()
    assert same_path(str(a), str(b)) is case_insensitive_fs


def test_real_platform_reveal_command(monkeypatch, tmp_path):
    """The real platform uses Explorer / Finder / the desktop file manager, without blocking."""
    import subprocess

    from gpqt.platform import CurrentPlatform

    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: seen.append(args))
    f = tmp_path / "a.json"
    f.write_text("{}")
    CurrentPlatform().reveal(f)
    cmd = " ".join(map(str, seen[0]))
    if sys.platform == "win32":
        assert cmd.startswith("explorer") and "/select," in cmd
    elif sys.platform == "darwin":
        assert cmd.startswith("open -R")
    else:
        assert cmd.startswith("xdg-open") and str(tmp_path) in cmd
