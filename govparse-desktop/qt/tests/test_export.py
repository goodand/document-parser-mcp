"""Export, zip, cancel, convert (inventory A11, A12)."""

from pathlib import Path

import pytest

from conftest import click, find, open_doc, text, tone, wait_status


def _report(out: Path, mode="json", tables=None):
    """Make the worker leave a real result in its output dir, like worker.py does."""
    out.mkdir(parents=True, exist_ok=True)
    if mode in ("json", "html"):
        (out / f"document.{mode}").write_text("{}", "utf-8")
        (out / "report.json").write_text('{"source": "x.hwpx"}', "utf-8")
    else:
        (out / f"document.{mode}").write_bytes(b"PK")
    return {"saved": True, "notes": [], "ir_builder": "rhwp",
            "bundle": {"counts": {"embedded_picture": 4, "rendered_table_capture": None, "table_image": 3}}}


@pytest.fixture
def exporting(fake_worker):
    for m in ("json", "html", "hwpx", "hwp"):
        fake_worker.reply[m] = (lambda mode: lambda c: _report(Path(c.args[1]), mode))(m)
    fake_worker.reply["zip"] = lambda c: (Path(c.args[1]).write_bytes(b"PK"), {"zip": c.args[1], "files": 3, "bytes": 2_200_000})[1]
    fake_worker.reply["index"] = {"assets": [], "missing": [], "source_name": "합성 문서.hwpx", "table_images": None, "integrity": None}
    return fake_worker


def test_format_options(window):
    labels = window.format_options()
    assert [l for l, _, _ in labels] == ["JSON + PNG", "HTML", "한글 문서 · HWPX (.hwpx)", "한글 문서 · HWP (.hwp)"]
    assert text(window, "btnFormat") == "JSON + PNG ▾"


def test_hwp_disabled_without_hancom(window):
    hwp = dict((l, (desc, enabled)) for l, desc, enabled in window.format_options())["한글 문서 · HWP (.hwp)"]
    assert hwp == ("이 PC에서 한컴오피스를 찾지 못해 사용할 수 없음", False)


def test_export_without_document(window, qtbot):
    click(qtbot, window, "btnExport")
    wait_status(qtbot, window, "먼저 문서를 여세요.")
    assert tone(window, "statusText") == "warn"


def test_save_dialog_defaults(window, qtbot, hwpx, dialogs):
    open_doc(qtbot, window, hwpx)
    click(qtbot, window, "btnExport")
    assert dialogs.asked[-1] == ("save_target", "json", "합성 문서-json")
    window.set_format("hwpx")
    click(qtbot, window, "btnExport")
    assert dialogs.asked[-1] == ("save_target", "hwpx", "합성 문서-복원.hwpx")


def test_save_dialog_cancel(window, qtbot, hwpx, dialogs, exporting):
    open_doc(qtbot, window, hwpx)
    dialogs.save_target = None
    click(qtbot, window, "btnExport")
    wait_status(qtbot, window, "저장을 취소했습니다. 문서와 형식 선택은 그대로입니다.")
    assert "json" not in exporting.modes()


def test_refuse_saving_over_source(window, qtbot, hwpx, dialogs, exporting):
    open_doc(qtbot, window, hwpx)
    window.set_format("hwpx")
    dialogs.save_target = str(hwpx)
    click(qtbot, window, "btnExport")
    wait_status(qtbot, window, "원본과 같은 경로에는 저장할 수 없습니다. 다른 이름을 선택하세요.")
    assert "hwpx" not in exporting.modes()
    qtbot.wait(2300)   # two 1-second refresh ticks: the answer must stay (formal/gui-spec C2)
    assert text(window, "statusText").startswith("원본과 같은 경로에는 저장할 수 없습니다.")
    click(qtbot, window, "btnNext")   # the next user action may replace it with the page line
    qtbot.waitUntil(lambda: text(window, "statusText").startswith("원본 미리보기 · 2 / 3쪽"))


def test_json_export_publishes_zips_and_reports(window, qtbot, hwpx, dialogs, exporting, tmp_path):
    open_doc(qtbot, window, hwpx)
    target = tmp_path / "out" / "결과-json"
    dialogs.save_target = str(target)
    click(qtbot, window, "btnExport")
    wait_status(qtbot, window, "저장 완료", timeout=5000)
    assert target.is_dir() and (target / "document.json").is_file()
    assert not list(target.parent.glob(".govparse-*")), "staging folder left behind"
    z = next(c for c in exporting.calls if c.mode == "zip")
    assert z.args == [str(target), str(target) + ".zip"]
    s = text(window, "statusText")
    assert "압축 파일 결과-json.zip (2.1MB)" in s and s.endswith("— ‘결과 보기’에서 확인")
    assert find(window, "tabResult").isEnabled()
    assert find(window, "tabOriginal").property("active") is True  # export does not switch tabs


def test_existing_target_gets_new_name(window, qtbot, hwpx, dialogs, exporting, tmp_path):
    open_doc(qtbot, window, hwpx)
    target = tmp_path / "결과-json"
    target.mkdir()
    (target / "keep.txt").write_text("user file")
    dialogs.save_target = str(target)
    click(qtbot, window, "btnExport")
    wait_status(qtbot, window, "(같은 이름이 있어 새 이름으로 저장)", timeout=5000)
    assert (target / "keep.txt").read_text() == "user file"
    assert (tmp_path / "결과-json (2)" / "document.json").is_file()


def test_hwpx_export_moves_single_file(window, qtbot, hwpx, dialogs, exporting, tmp_path):
    open_doc(qtbot, window, hwpx)
    window.set_format("hwpx")
    dialogs.save_target = str(tmp_path / "복원.hwpx")
    click(qtbot, window, "btnExport")
    wait_status(qtbot, window, "저장 완료", timeout=5000)
    assert (tmp_path / "복원.hwpx").is_file() and "zip" not in exporting.modes()


def test_running_export_state_and_progress(window, qtbot, hwpx, dialogs, exporting, tmp_path):
    open_doc(qtbot, window, hwpx)
    exporting.hold["json"] = True
    exporting.progress["json"] = [{"stage": "표 이미지", "done": 10, "total": 40}]
    dialogs.save_target = str(tmp_path / "r")
    click(qtbot, window, "btnExport")
    qtbot.waitUntil(lambda: find(window, "btnCancel").isVisible())
    assert not find(window, "btnExport").isEnabled()
    assert text(window, "statusText").startswith("내보내는 중 · 합성 문서.hwpx → JSON + PNG") and "(Esc로 취소)" in text(window, "statusText")
    qtbot.waitUntil(lambda: "표 이미지 10/40쪽 (25%)" in text(window, "stExport"))
    assert find(window, "busyBar").isVisible()
    exporting.release("json")
    wait_status(qtbot, window, "저장 완료", timeout=5000)
    assert not find(window, "btnCancel").isVisible() and find(window, "btnExport").isEnabled()


def test_second_export_ignored_while_running(window, qtbot, hwpx, dialogs, exporting, tmp_path):
    open_doc(qtbot, window, hwpx)
    exporting.hold["json"] = True
    dialogs.save_target = str(tmp_path / "r")
    click(qtbot, window, "btnExport")
    qtbot.waitUntil(lambda: find(window, "btnCancel").isVisible())
    window.export()                      # programmatic second request
    assert exporting.modes().count("json") == 1
    exporting.release("json")


@pytest.mark.parametrize("how", ["button", "esc"])
def test_cancel_export(window, qtbot, hwpx, dialogs, exporting, tmp_path, how):
    from PySide6.QtCore import Qt

    open_doc(qtbot, window, hwpx)
    exporting.hold["json"] = True
    dialogs.save_target = str(tmp_path / "r")
    click(qtbot, window, "btnExport")
    qtbot.waitUntil(lambda: find(window, "btnCancel").isVisible())
    if how == "button":
        click(qtbot, window, "btnCancel")
    else:
        qtbot.keyClick(window, Qt.Key_Escape)
    wait_status(qtbot, window, "내보내기를 취소했습니다. 원본과 기존 결과는 그대로입니다.", timeout=5000)
    assert not (tmp_path / "r").exists() and not list(tmp_path.glob(".govparse-*"))
    assert next(c for c in exporting.calls if c.mode == "json").cancelled


def test_export_failure_message(window, qtbot, hwpx, dialogs, exporting, tmp_path):
    open_doc(qtbot, window, hwpx)
    exporting.reply["json"] = RuntimeError("표를 그리지 못했습니다")
    dialogs.save_target = str(tmp_path / "r")
    click(qtbot, window, "btnExport")
    wait_status(qtbot, window, "내보내기 실패 · 합성 문서.hwpx · 표를 그리지 못했습니다", timeout=5000)
    assert tone(window, "statusText") == "warn" and not list(tmp_path.glob(".govparse-*"))


def test_zip_failure_keeps_export(window, qtbot, hwpx, dialogs, exporting, tmp_path):
    open_doc(qtbot, window, hwpx)
    exporting.reply["zip"] = RuntimeError("디스크 부족")
    dialogs.save_target = str(tmp_path / "r")
    click(qtbot, window, "btnExport")
    wait_status(qtbot, window, " · 압축 실패: 디스크 부족 (결과 폴더는 저장됨)", timeout=5000)
    assert (tmp_path / "r" / "document.json").is_file() and tone(window, "statusText") == "warn"


def test_export_of_previous_document_does_not_replace_view(window, qtbot, hwpx, dialogs, exporting, tmp_path):
    open_doc(qtbot, window, hwpx)
    exporting.hold["json"] = True
    dialogs.save_target = str(tmp_path / "r")
    click(qtbot, window, "btnExport")
    qtbot.waitUntil(lambda: find(window, "btnCancel").isVisible())
    other = tmp_path / "다른.hwpx"
    other.write_bytes(b"x")
    window.open_document(str(other))
    exporting.release("json")
    wait_status(qtbot, window, "이전 문서 합성 문서.hwpx의 내보내기 완료", timeout=5000)
    assert "(현재 문서 화면은 바꾸지 않았습니다)" in text(window, "statusText")
    assert text(window, "lblFileName") == "다른.hwpx"


# ── convert (A12) ─────────────────────────────────────────────────────────────

def _json_result(tmp_path):
    d = tmp_path / "기존-json"
    d.mkdir()
    (d / "document.json").write_text("{}", "utf-8")
    return d


@pytest.mark.parametrize("fmt,button", [("html", "btnToHtml"), ("hwpx", "btnToHwpx")])
def test_convert_from_json_result(window, qtbot, dialogs, exporting, tmp_path, fmt, button):
    exporting.reply["convert"] = lambda c: _report(Path(c.args[1]), c.args[2])
    window.load_result(str(_json_result(tmp_path) / "document.json"))
    qtbot.waitUntil(lambda: find(window, button).isVisible())
    dialogs.save_target = str(tmp_path / f"변환.{fmt}" if fmt == "hwpx" else tmp_path / "변환-html")
    click(qtbot, window, button)
    wait_status(qtbot, window, "변환 완료 ·", timeout=5000)
    c = next(c for c in exporting.calls if c.mode == "convert")
    assert c.args[0].endswith("document.json") and c.args[2] == fmt
    assert dialogs.asked[-1][2] == ("기존-json-변환.hwpx" if fmt == "hwpx" else "기존-json-html")


def test_convert_buttons_only_for_json(window, qtbot, tmp_path, exporting):
    h = tmp_path / "r.html"
    h.write_text("<p>x</p>")
    window.load_result(str(h))
    assert not find(window, "btnToHtml").isVisible() and not find(window, "btnToHwpx").isVisible()


def test_late_convert_does_not_replace_newer_result(window, qtbot, dialogs, exporting, tmp_path):
    """Intentional change (SPEC.md §7): the Windows app has no job guard for convert."""
    exporting.reply["convert"] = lambda c: _report(Path(c.args[1]), "html")
    exporting.hold["convert"] = True
    window.load_result(str(_json_result(tmp_path) / "document.json"))
    dialogs.save_target = str(tmp_path / "변환-html")
    click(qtbot, window, "btnToHtml")
    other = tmp_path / "다른.html"
    other.write_text("<p>y</p>")
    window.load_result(str(other))
    exporting.release("convert")
    qtbot.wait(300)
    assert text(window, "resTitle").endswith("다른.html")
