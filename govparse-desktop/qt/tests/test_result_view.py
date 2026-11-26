"""Result view, asset list, OCR box on results, clipboard (inventory A10, A13)."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence

from conftest import click, find, png, text, tone, wait_status


NO_MANIFEST = object()


def _result(tmp_path, assets, table_images=None, integrity=None, source="합성 문서.hwpx", zip_=False):
    d = tmp_path / "결과-json"
    (d / "assets").mkdir(parents=True)
    (d / "document.json").write_text("{}", "utf-8")
    (d / "report.json").write_text(f'{{"source": "C:/docs/{source}"}}', "utf-8")
    rows = []
    for a in assets:
        f = png(d / a["path"]) if a.pop("exists", True) else d / a["path"]   # missing: listed but not on disk
        rows.append({"ocr_status": "not_run", "ocr_texts": [], "text": "", "page": None, **a, "file": str(f)})
    if zip_:
        (tmp_path / "결과-json.zip").write_bytes(b"PK")
    idx = {"assets": rows, "missing": [], "source_name": source, "table_images": table_images,
           "capture_state": "not_used", "convertible": True,
           "integrity": None if integrity is NO_MANIFEST else integrity if integrity is not None
           else {"checked": 6, "changed": [], "missing": []}}
    return d, idx


@pytest.fixture
def loaded(window, qtbot, fake_worker, tmp_path):
    def go(**kw):
        d, idx = _result(tmp_path, **kw)
        fake_worker.reply["index"] = idx
        window.load_result(str(d / "document.json"))
        qtbot.waitUntil(lambda: "미리보기 성공" in text(window, "resState")
                        or "자산 목록을 읽지 못했습니다" in text(window, "resState"))
        return d
    return go


ASSETS = [
    {"origin": "embedded_picture", "path": "assets/embedded/bin1.png", "text": "표 2행 1열 칸 → 안쪽 표 1행 1열 · 〈그림 A〉"},
    {"origin": "table_image", "path": "assets/tables/table-aaa-p001.png", "table_id": "s0/b3/t0", "part": 1, "parts": 1,
     "label": "본문 1번째 표", "text": "본문 1번째 표"},
    {"origin": "table_image", "path": "assets/tables/table-bbb-p001.png", "table_id": "s0/b3/t0/c1_0/t0", "part": 1, "parts": 2,
     "label": "본문 1번째 표 › 2행 1열 칸 안 1번째 표", "text": "본문 1번째 표 › 2행 1열 칸 안 1번째 표 · 1/2쪽 조각"},
]


def _rows(window):
    lst = find(window, "assetList")
    return [[lst.topLevelItem(i).text(c) for c in range(4)] for i in range(lst.topLevelItemCount())]


def test_load_result_dialog_and_tab(window, qtbot, dialogs, fake_worker, tmp_path):
    d, idx = _result(tmp_path, [dict(a) for a in ASSETS])
    fake_worker.reply["index"] = idx
    dialogs.load_result = str(d / "document.json")
    click(qtbot, window, "btnLoadResult")
    wait_status(qtbot, window, f"기존 결과를 불러왔습니다 · {d / 'document.json'}")
    assert find(window, "tabResult").isEnabled() and find(window, "tabResult").property("active") is True


def test_load_result_failure(window, qtbot, dialogs, fake_worker, tmp_path):
    d, _ = _result(tmp_path, [])
    fake_worker.reply["index"] = ValueError("GovParse 결과 JSON이 아닙니다")
    dialogs.load_result = str(d / "document.json")
    click(qtbot, window, "btnLoadResult")
    wait_status(qtbot, window, "결과를 불러오지 못했습니다 · GovParse 결과 JSON이 아닙니다")


def test_title_has_source_name(window, loaded):
    loaded(assets=[dict(a) for a in ASSETS])
    assert text(window, "resTitle") == "합성 문서.hwpx → document.json"


def test_asset_columns_and_rows(window, loaded):
    loaded(assets=[dict(a) for a in ASSETS])
    lst = find(window, "assetList")
    assert [lst.headerItem().text(i) for i in range(4)] == ["자산", "출처", "위치 / 설명", "원본 파일"]
    rows = _rows(window)
    assert rows[0][:2] == ["bin1.png", "내장 그림"] and rows[0][3] == "합성 문서.hwpx"
    assert rows[1][1:3] == ["표 이미지", "본문 1번째 표"]
    assert rows[2][2].endswith("1/2쪽 조각")


def test_page_capture_rows_say_table_unknown(window, loaded):
    loaded(assets=[{"origin": "rendered_table_capture", "path": "assets/tables/p0012_t0.png", "page": 12}])
    assert _rows(window)[0][1:3] == ["표 캡처", "미리보기 12쪽의 표 영역 (어느 표인지는 확인되지 않음)"]


def test_embedded_without_text(window, loaded):
    loaded(assets=[{"origin": "embedded_picture", "path": "assets/embedded/bin9.png", "text": ""}])
    assert _rows(window)[0][2] == "문서에 들어 있던 그림"


def test_source_page_selection_is_labeled_as_a_whole_page(window, loaded):
    summary = {"tables": 1, "rendered": 0, "png": 0, "source_page_tables": 1, "missing": []}
    loaded(assets=[{"origin": "table_source_page", "path": "assets/tables/page-2.png",
                    "page": 2, "text": "본문 1번째 표 · 원본 2쪽 전체"}], table_images=summary)
    assert _rows(window)[0][1:3] == ["원본 쪽", "본문 1번째 표 · 원본 2쪽 전체"]
    assert "표 1개는 원본 쪽으로 표시" in text(window, "resNotes")


def test_tables_without_image_are_listed_with_reason(window, loaded):
    ti = {"tables": 3, "rendered": 2, "png": 2, "not_rendered": 1,
          "missing": [{"table_id": "s0/b5/t2", "label": "본문 3번째 표", "reason_text": "원본 표와 구조·글자가 정확히 일치하지 않음"}]}
    loaded(assets=[dict(a) for a in ASSETS[:2]], table_images=ti)
    last = _rows(window)[-1]
    assert last[:3] == ["(이미지 없음)", "표 이미지 미생성", "본문 3번째 표 · 원본 표와 구조·글자가 정확히 일치하지 않음"]
    assert "· 표 3개 중 2개 이미지 생성 · PNG 2장 · 미생성 1개는 목록 끝에 사유 표시" in text(window, "resNotes")


def test_three_kinds_of_missing_are_kept_apart(window, loaded):
    """not_rendered = tables without an image (count), table_images.missing = their reasons (rows),
    index.missing = asset files listed in the result but gone from disk (engine review 3)."""
    ti = {"tables": 2, "rendered": 1, "png": 1, "not_rendered": 1,
          "missing": [{"table_id": "s0/b5/t1", "label": "본문 2번째 표", "reason_text": "그려진 쪽이 비어 있음"}]}
    loaded(assets=[dict(ASSETS[1]), {**ASSETS[0], "exists": False}], table_images=ti)
    notes, state = text(window, "resNotes"), text(window, "resState")
    assert "표 2개 중 1개 이미지 생성 · PNG 1장 · 미생성 1개" in notes
    assert "파일 없는 자산 1개" in state and tone(window, "resState") == "warn"
    assert [r[1] for r in _rows(window)].count("표 이미지 미생성") == 1


def test_old_result_without_table_images_or_manifest(window, loaded):
    """Results written before 2026-11-25: table_images and integrity are None."""
    loaded(assets=[{"origin": "rendered_table_capture", "path": "assets/tables/p0001_t0.png", "page": 1}],
           table_images=None, integrity=NO_MANIFEST)
    assert "표" not in text(window, "resNotes").split("저장 후")[0].replace("표 캡처", "")
    assert "확인할 수 없음 (검증 목록이 없는 결과)" in text(window, "resNotes")


def test_selecting_missing_table_row_clears_picture(window, qtbot, loaded):
    ti = {"tables": 1, "rendered": 0, "png": 0, "not_rendered": 1,
          "missing": [{"table_id": "s0/b5/t2", "label": "본문 1번째 표", "reason_text": "빈 그림"}]}
    loaded(assets=[], table_images=ti)
    lst = find(window, "assetList")
    lst.setCurrentItem(lst.topLevelItem(0))
    assert find(window, "assetPreview").pixmap() is None or find(window, "assetPreview").pixmap().isNull()
    assert not find(find(window, "resultOcr"), "ocrRun").isEnabled()


@pytest.mark.parametrize("integ,expected", [
    (NO_MANIFEST, "· 저장 후 변경 여부: 확인할 수 없음 (검증 목록이 없는 결과)"),
    ({"checked": 6, "changed": [], "missing": []}, "· 저장 후 변경 없음 (파일 6개 해시 확인)"),
    ({"checked": 6, "changed": ["a"], "missing": ["b", "c"]}, "· 주의: 저장 후 바뀐 파일 1개, 없어진 파일 2개"),
])
def test_integrity_note(window, loaded, integ, expected):
    loaded(assets=[dict(ASSETS[0])], integrity=integ)
    assert expected in text(window, "resNotes")


def test_state_line_and_first_row_selected(window, loaded):
    loaded(assets=[dict(a) for a in ASSETS])
    assert text(window, "resState") == "파일 저장: 성공\n미리보기 성공 · 자산 3개" and tone(window, "resState") == "ok"
    assert find(window, "assetList").currentItem() is find(window, "assetList").topLevelItem(0)


def test_no_assets_state(window, loaded):
    loaded(assets=[])
    assert text(window, "resState").endswith("(추출된 PNG 0개)")


def test_small_picture_shown_at_natural_size(window, qtbot, loaded):
    loaded(assets=[dict(ASSETS[0])])
    assert window.asset_preview_mode() == "natural"   # 120x80 fits the panel


def test_buttons_by_kind(window, loaded, tmp_path):
    loaded(assets=[dict(ASSETS[0])], zip_=True)
    assert text(window, "btnOpenExternal") == "JSON 열기"
    assert find(window, "btnOpenZip").isVisible()
    assert find(window, "btnToHtml").isVisible() and find(window, "btnToHwpx").isVisible()


def test_zip_button_hidden_without_zip(window, loaded):
    loaded(assets=[dict(ASSETS[0])])
    assert not find(window, "btnOpenZip").isVisible()


def test_open_folder_and_zip_reveal_files(window, qtbot, loaded, platform_, tmp_path):
    d = loaded(assets=[dict(ASSETS[0])], zip_=True)
    click(qtbot, window, "btnOpenFolder")
    click(qtbot, window, "btnOpenZip")
    assert platform_.revealed == [str(d / "document.json"), str(d) + ".zip"]


def test_open_external_uses_default_app(window, qtbot, loaded, platform_):
    d = loaded(assets=[dict(ASSETS[0])])
    click(qtbot, window, "btnOpenExternal")
    assert platform_.opened[-1] == str(d / "document.json")


def test_html_result(window, qtbot, tmp_path):
    h = tmp_path / "r-html"
    h.mkdir()
    (h / "document.html").write_text("<p>x</p>", "utf-8")
    window.load_result(str(h))
    assert text(window, "btnOpenExternal") == "브라우저에서 열기"
    qtbot.waitUntil(lambda: text(window, "resState").endswith("미리보기 성공 · 저장된 로컬 HTML을 표시"))
    assert not find(window, "assetList").isVisible()


def test_hwpx_result_pages(window, qtbot, tmp_path, fake_worker):
    f = tmp_path / "복원.hwpx"
    f.write_bytes(b"PK")
    window.load_result(str(f))
    assert text(window, "btnOpenExternal") == "한글에서 열기"
    qtbot.waitUntil(lambda: "미리보기 성공 · 저장된 결과 파일을 다시 그려 표시 (3쪽)" in text(window, "resState"))
    assert text(window, "lblPage") == "1 / 3"  # page tools follow the result pages


# ── clipboard ────────────────────────────────────────────────────────────────

def test_copy_selected_asset_with_ctrl_c(window, qtbot, loaded):
    loaded(assets=[dict(ASSETS[0])])
    lst = find(window, "assetList")
    lst.setFocus()
    qtbot.keySequence(lst, QKeySequence.Copy)
    img = QGuiApplication.clipboard().image()
    assert (img.width(), img.height()) == (120, 80)
    wait_status(qtbot, window, "그림을 복사했습니다 (120×80). 문서나 메일에 붙여 넣을 수 있습니다.")
    assert QGuiApplication.clipboard().mimeData().hasUrls()   # also as a file for Finder/Explorer/mail


def test_right_click_selects_row_then_copy(window, qtbot, loaded):
    loaded(assets=[dict(a) for a in ASSETS])
    lst = find(window, "assetList")
    menu = window.asset_context_menu(lst.topLevelItem(1))
    assert lst.currentItem() is lst.topLevelItem(1)
    assert [a.text() for a in menu.actions()][0].startswith("그림 복사")


def test_copy_missing_file(window, qtbot, loaded):
    loaded(assets=[{**ASSETS[0], "exists": False}])
    window.copy_selected_asset()
    wait_status(qtbot, window, "이 그림은 원본 파일이 없어 복사할 수 없습니다.")


def test_transparent_picture_copied_on_white(window, qtbot, loaded, tmp_path):
    from PySide6.QtGui import QImage, QColor

    d = loaded(assets=[dict(ASSETS[0])])
    img = QImage(10, 10, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    img.save(str(d / "assets/embedded/bin1.png"))
    window.copy_selected_asset()
    assert QGuiApplication.clipboard().image().pixelColor(0, 0).name() == "#ffffff"


# ── OCR box on results (A10) ──────────────────────────────────────────────────

def _ocr(window, name):
    return find(find(window, "resultOcr"), name)


def test_ocr_initial_state(window, loaded):
    loaded(assets=[dict(ASSETS[0])])
    assert _ocr(window, "ocrState").text() == "OCR 미실행"
    assert _ocr(window, "ocrRun").isEnabled() and not _ocr(window, "ocrCopy").isEnabled()


def test_ocr_run_and_copy(window, qtbot, loaded, fake_worker):
    fake_worker.reply["ocr"] = {"status": "ok", "texts": ["중소벤처기업부", "2026"], "error": "", "engine_label": "RapidOCR · PP-OCRv5 한국어 (mobile)"}
    loaded(assets=[dict(ASSETS[0])])
    qtbot.mouseClick(_ocr(window, "ocrRun"), Qt.LeftButton)
    qtbot.waitUntil(lambda: _ocr(window, "ocrState").text().startswith("OCR 결과 2줄"))
    assert _ocr(window, "ocrState").text() == "OCR 결과 2줄 (그림 안 글자 · 설명 아님) · RapidOCR · PP-OCRv5 한국어 (mobile)"
    assert _ocr(window, "ocrText").toPlainText().splitlines() == ["중소벤처기업부", "2026"]
    qtbot.mouseClick(_ocr(window, "ocrCopy"), Qt.LeftButton)
    assert QGuiApplication.clipboard().text().splitlines() == ["중소벤처기업부", "2026"]
    assert _ocr(window, "ocrState").text().startswith("클립보드에 복사했습니다 (")


@pytest.mark.parametrize("reply,expected", [
    ({"status": "empty", "texts": []}, "OCR 결과 없음 (빈 결과)"),
    ({"status": "not_available", "texts": [], "error": "모델 없음"}, "OCR 사용 불가 · 모델 없음"),
    ({"status": "failed", "texts": [], "error": "디코딩 실패"}, "OCR 실패 · 디코딩 실패"),
])
def test_ocr_outcomes(window, qtbot, loaded, fake_worker, reply, expected):
    fake_worker.reply["ocr"] = reply
    loaded(assets=[dict(ASSETS[0])])
    qtbot.mouseClick(_ocr(window, "ocrRun"), Qt.LeftButton)
    qtbot.waitUntil(lambda: _ocr(window, "ocrState").text().startswith(expected))


def test_ocr_running_disables_button(window, qtbot, loaded, fake_worker):
    fake_worker.reply["ocr"] = {"status": "ok", "texts": ["a"]}
    fake_worker.hold["ocr"] = True
    loaded(assets=[dict(ASSETS[0])])
    qtbot.mouseClick(_ocr(window, "ocrRun"), Qt.LeftButton)
    qtbot.waitUntil(lambda: _ocr(window, "ocrState").text() == "OCR 실행 중…")
    assert not _ocr(window, "ocrRun").isEnabled()
    fake_worker.release("ocr")


def test_late_ocr_does_not_overwrite_other_selection(window, qtbot, loaded, fake_worker):
    fake_worker.reply["ocr"] = {"status": "ok", "texts": ["첫 그림 글자"]}
    fake_worker.hold["ocr"] = True
    loaded(assets=[dict(a) for a in ASSETS])
    qtbot.mouseClick(_ocr(window, "ocrRun"), Qt.LeftButton)
    lst = find(window, "assetList")
    lst.setCurrentItem(lst.topLevelItem(1))
    fake_worker.release("ocr")
    qtbot.wait(300)
    assert _ocr(window, "ocrText").toPlainText() == ""
    lst.setCurrentItem(lst.topLevelItem(0))            # cached result comes back for the first picture
    assert _ocr(window, "ocrText").toPlainText() == "첫 그림 글자"
