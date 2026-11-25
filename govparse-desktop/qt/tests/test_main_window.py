"""Main window: start-up, top bar, opening documents, page tools, status strip (inventory A1–A6)."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QMimeData, QUrl, QPoint
from PySide6.QtGui import QKeySequence

from conftest import click, find, open_doc, text, tone, wait_status


# ── start-up (A1) ─────────────────────────────────────────────────────────────

def test_title_and_minimum_size(window):
    assert window.windowTitle() == "GovParse Desktop"
    assert window.minimumWidth() >= 560 and window.minimumHeight() >= 420


def test_initial_state(window):
    assert not find(window, "tabResult").isEnabled()
    assert find(window, "tabOriginal").property("active") is True
    assert not find(window, "btnCancel").isVisible()
    assert not find(window, "figurePanel").isVisible()
    assert text(window, "origMessage") == "HWP 또는 HWPX 파일을 여기에 놓으세요"
    assert text(window, "origAction") == "파일 열기"
    assert text(window, "lblPage") == "– / –"
    for b in ("btnPrev", "btnNext", "btnZoom"):
        assert not find(window, b).isEnabled(), b
    assert text(window, "stStructure") == "구조: 문서를 열면 자동으로 읽습니다"
    assert text(window, "stPreview") == "" and text(window, "stExport") == ""
    assert not find(window, "busyBar").isVisible()


def test_start_status_message(window, qtbot):
    wait_status(qtbot, window, "HWP/HWPX 문서를 여세요.")
    assert tone(window, "statusText") == "muted"


def test_top_bar_order(window):
    names = ["tabOriginal", "tabResult", "btnOpen", "btnLoadResult", "btnSettings", "btnManual", "lblFileName"]
    xs = [find(window, n).mapTo(window, QPoint(0, 0)).x() for n in names]
    assert xs == sorted(xs), "top bar order differs from the Windows app"
    labels = [text(window, n) for n in names[:-1]]
    assert labels == ["원본 보기", "결과 보기", "파일 열기", "결과 불러오기", "설정", "설명서"]


def test_initial_result_view_message(window, qtbot):
    window.switch_tab(result=True)  # programmatic: the tab button is disabled at start
    assert "아직 결과가 없습니다" in text(window, "resultView") or "아직 결과가 없습니다" in window.result_placeholder_text()


# ── opening (A2, A6) ──────────────────────────────────────────────────────────

def test_open_button_uses_dialog(window, qtbot, dialogs, hwpx, fake_worker):
    dialogs.open_file = str(hwpx)
    click(qtbot, window, "btnOpen")
    qtbot.waitUntil(lambda: "preview" in fake_worker.modes() and "summary" in fake_worker.modes())
    assert text(window, "lblFileName") == hwpx.name
    assert window.windowTitle() == f"GovParse Desktop · {hwpx.name}"


def test_open_dialog_cancel_changes_nothing(window, qtbot, dialogs, fake_worker):
    before = text(window, "statusText")
    dialogs.open_file = None
    click(qtbot, window, "btnOpen")
    assert fake_worker.calls == [] and text(window, "statusText") == before


def test_ctrl_o_opens_dialog(window, qtbot, dialogs):
    qtbot.keySequence(window, QKeySequence.Open)  # Ctrl+O on Windows/Linux, Cmd+O on macOS
    assert ("open_file",) in dialogs.asked


def test_center_button_opens_dialog(window, qtbot, dialogs):
    click(qtbot, window, "origAction")
    assert ("open_file",) in dialogs.asked


@pytest.mark.parametrize("name", ["a.HWPX", "b.hwp", "c.Hwp"])
def test_extension_check_is_case_insensitive(window, qtbot, tmp_path, fake_worker, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    window.open_document(str(p))
    qtbot.waitUntil(lambda: "preview" in fake_worker.modes())


@pytest.mark.parametrize("name", ["a.pdf", "b.docx", "noext"])
def test_other_files_are_refused(window, qtbot, tmp_path, fake_worker, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    window.open_document(str(p))
    wait_status(qtbot, window, f"HWP 또는 HWPX 파일만 열 수 있습니다: {name}")
    assert tone(window, "statusText") == "warn" and fake_worker.calls == []


def test_open_runs_preview_page0_and_summary(window, qtbot, hwpx, fake_worker):
    open_doc(qtbot, window, hwpx)
    prev = next(c for c in fake_worker.calls if c.mode == "preview")
    assert prev.args[0] == str(hwpx) and int(prev.args[2]) == 0
    assert any(c.mode == "summary" and c.args[0] == str(hwpx) for c in fake_worker.calls)


def test_open_shows_structure_counts(window, qtbot, hwpx):
    open_doc(qtbot, window, hwpx)
    s = text(window, "stStructure")
    assert s.startswith("구조 읽기 완료") and "문단 16" in s and "표 3 (중첩 1)" in s and "그림 6" in s
    assert tone(window, "stStructure") == "ok"


def test_structure_from_xml_route_is_named(window, qtbot, hwpx, fake_worker):
    fake_worker.reply["summary"] = {**fake_worker.reply["summary"], "builder": "owpml"}
    open_doc(qtbot, window, hwpx)
    assert text(window, "stStructure").endswith("원본 XML에서 직접 읽음")


def test_structure_failure(window, qtbot, hwpx, fake_worker):
    fake_worker.reply["summary"] = RuntimeError("문서를 읽지 못함")
    window.open_document(str(hwpx))
    qtbot.waitUntil(lambda: text(window, "stStructure") == "구조: 읽기 실패 · 문서를 읽지 못함")
    assert tone(window, "stStructure") == "warn"


def test_open_switches_to_original_tab(window, qtbot, hwpx):
    window.switch_tab(result=True)
    open_doc(qtbot, window, hwpx)
    assert find(window, "tabOriginal").property("active") is True


def test_drop_one_file_opens_it(window, qtbot, hwpx, fake_worker):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(hwpx))])
    window.handle_drop(mime)
    qtbot.waitUntil(lambda: "preview" in fake_worker.modes())


def test_drop_two_files_is_refused(window, qtbot, hwpx, tmp_path):
    other = tmp_path / "b.hwpx"
    other.write_bytes(b"x")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(hwpx)), QUrl.fromLocalFile(str(other))])
    window.handle_drop(mime)
    wait_status(qtbot, window, "파일을 하나만 놓아 주세요. (2개를 놓았습니다)")


def test_korean_and_space_paths(window, qtbot, hwpx, fake_worker):
    assert " " in str(hwpx) and "합성" in str(hwpx)
    open_doc(qtbot, window, hwpx)
    assert fake_worker.calls[0].args[0] == str(hwpx)


# ── page tools (A3, A4) ───────────────────────────────────────────────────────

def test_page_label_and_buttons(window, qtbot, hwpx):
    open_doc(qtbot, window, hwpx)
    assert text(window, "lblPage") == "1 / 3"
    assert not find(window, "btnPrev").isEnabled() and find(window, "btnNext").isEnabled()


def test_next_and_prev(window, qtbot, hwpx, fake_worker):
    open_doc(qtbot, window, hwpx)
    click(qtbot, window, "btnNext")
    qtbot.waitUntil(lambda: text(window, "lblPage") == "2 / 3")
    assert any(c.mode == "preview" and int(c.args[2]) == 1 for c in fake_worker.calls)
    click(qtbot, window, "btnNext")
    qtbot.waitUntil(lambda: text(window, "lblPage") == "3 / 3")
    assert not find(window, "btnNext").isEnabled()
    click(qtbot, window, "btnPrev")
    qtbot.waitUntil(lambda: text(window, "lblPage") == "2 / 3")


def test_visited_pages_are_cached(window, qtbot, hwpx, fake_worker):
    open_doc(qtbot, window, hwpx)
    click(qtbot, window, "btnNext")
    qtbot.waitUntil(lambda: text(window, "lblPage") == "2 / 3")
    click(qtbot, window, "btnPrev")
    qtbot.waitUntil(lambda: text(window, "lblPage") == "1 / 3")
    assert sum(1 for c in fake_worker.calls if c.mode == "preview" and int(c.args[2]) == 0) == 1


def test_preview_temp_png_is_deleted(window, qtbot, hwpx, fake_worker):
    open_doc(qtbot, window, hwpx)
    out = Path(next(c for c in fake_worker.calls if c.mode == "preview").args[1])
    qtbot.waitUntil(lambda: not out.exists())


def test_preview_failure_offers_retry(window, qtbot, hwpx, fake_worker):
    fake_worker.reply["preview"] = RuntimeError("렌더 실패")
    window.open_document(str(hwpx))
    qtbot.waitUntil(lambda: "미리보기 실패 · 렌더 실패" in text(window, "origMessage"))
    assert "내보내기는 계속할 수 있습니다." in text(window, "origMessage")
    assert text(window, "origAction") == "다시 시도" and tone(window, "origMessage") == "warn"
    assert text(window, "stPreview") == "미리보기: 실패"


def test_retry_after_failure(window, qtbot, hwpx, fake_worker, tmp_path):
    from conftest import default_replies

    fake_worker.reply["preview"] = RuntimeError("x")
    window.open_document(str(hwpx))
    qtbot.waitUntil(lambda: text(window, "origAction") == "다시 시도")
    fake_worker.reply["preview"] = default_replies(tmp_path)["preview"]
    click(qtbot, window, "origAction")
    qtbot.waitUntil(lambda: text(window, "lblPage") == "1 / 3")


def test_stale_preview_does_not_overwrite_new_document(window, qtbot, hwpx, tmp_path, fake_worker):
    fake_worker.hold["preview"] = True
    window.open_document(str(hwpx))
    second = tmp_path / "두번째.hwpx"
    second.write_bytes(b"x")
    window.open_document(str(second))
    fake_worker.hold["preview"] = False
    fake_worker.release("preview")
    qtbot.waitUntil(lambda: text(window, "lblFileName") == "두번째.hwpx")
    assert text(window, "lblFileName") == "두번째.hwpx"


@pytest.mark.parametrize("label", ["폭 맞춤", "쪽 맞춤", "100%", "150%", "200%"])
def test_zoom_options(window, qtbot, hwpx, label):
    open_doc(qtbot, window, hwpx)
    window.set_zoom(label)
    assert text(window, "btnZoom") == f"{label} ▾"


def test_zoom_is_per_view(window, qtbot, hwpx):
    open_doc(qtbot, window, hwpx)
    window.set_zoom("200%")
    window.switch_tab(result=True)
    assert text(window, "btnZoom") != "200% ▾"


def test_figures_button_only_on_original_tab(window, qtbot, hwpx):
    open_doc(qtbot, window, hwpx)
    assert find(window, "btnFigures").isVisible()
    window.switch_tab(result=True)
    assert not find(window, "btnFigures").isVisible()


# ── status strip (A5) ─────────────────────────────────────────────────────────

def test_busy_bar_while_structure_runs(window, qtbot, hwpx, fake_worker):
    fake_worker.hold["summary"] = True
    window.open_document(str(hwpx))
    qtbot.waitUntil(lambda: text(window, "stStructure").startswith("구조 읽는 중 ·"))
    assert find(window, "busyBar").isVisible()
    fake_worker.release("summary")
    qtbot.waitUntil(lambda: not find(window, "busyBar").isVisible() or "완료" in text(window, "stStructure"))


def test_preview_done_line(window, qtbot, hwpx):
    open_doc(qtbot, window, hwpx)
    qtbot.waitUntil(lambda: text(window, "stPreview") == "미리보기: 완료 · 3쪽")


def test_hancom_pdf_route_is_named(window, qtbot, hwpx, fake_worker):
    base = fake_worker.reply["preview"]
    fake_worker.reply["preview"] = lambda c: {**base(c), "renderer": "hancom-pdf"}
    open_doc(qtbot, window, hwpx)
    qtbot.waitUntil(lambda: text(window, "stPreview") == "미리보기: 완료 · 3쪽 (한컴 PDF)")
    assert text(window, "statusText").endswith(" · 한컴 PDF로 표시")


def test_status_shows_page_after_open(window, qtbot, hwpx):
    open_doc(qtbot, window, hwpx)
    wait_status(qtbot, window, "원본 미리보기 · 1 / 3쪽")


@pytest.mark.parametrize("seconds,expected", [(5, "5초"), (59, "59초"), (60, "1분 00초"), (125, "2분 05초")])
def test_elapsed_format(seconds, expected):
    from gpqt.status import ago

    assert ago(seconds) == expected


@pytest.mark.parametrize("event,expected", [
    ({"stage": "표 이미지", "done": 10, "total": 40}, "표 이미지 10/40쪽 (25%)"),
    ({"stage": "압축", "done": 1200, "total": 2457}, "압축 1,200/2,457쪽 (49%)"),
    ({"stage": "쪽 그리기", "detail": "한컴 PDF 재사용"}, "쪽 그리기 (한컴 PDF 재사용)"),
])
def test_progress_text(event, expected):
    from gpqt.status import progress_text

    assert progress_text(event, elapsed=0) == expected


def test_remaining_time_appears_after_five_steps():
    from gpqt.status import progress_text

    assert "남은 시간 약" in progress_text({"stage": "표 이미지", "done": 10, "total": 100}, elapsed=60)
    assert "남은 시간" not in progress_text({"stage": "표 이미지", "done": 4, "total": 100}, elapsed=60)
    assert progress_text({"stage": "표 이미지", "done": 99, "total": 100}, elapsed=30).endswith("곧 끝남")


# ── other top-bar buttons ─────────────────────────────────────────────────────

def test_manual_opens_with_default_app(window, qtbot, platform_):
    click(qtbot, window, "btnManual")
    assert platform_.opened and platform_.opened[-1].endswith("manual.html")


def test_close_stops_worker_processes(window, fake_worker):
    window.close()
    assert window.worker_shutdown_called is True
