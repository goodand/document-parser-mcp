"""Figure panel: list, filter, detail, overlay, edit mode, captions, copy (inventory A7–A9)."""

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence

from conftest import click, find, open_doc, png, text, tone

SHA = "b9986eb86f46b63a63b8559fa595e283454294ea16f97cdf68f8ba63752dfce2"


def link(n, status="needs_review", verified=False, page=0, cands=(), cap="", group="g1", path_text="표 2행 1열 칸",
         kind="png · 표 안", picks=(), asset=None, caption_box=None):
    return {
        "occurrence": {"occ_id": f"s0-pic{n}"}, "status": status, "caption_text": cap, "group_key": group,
        "path_text": path_text, "order_in_cell": 0, "source_kind": kind, "parent_item": "",
        "location": {"verified": verified, "page_index": page, "candidate_pages": list(cands),
                     "figure_box": [100, 200, 300, 150], "page_size": [600, 800], "caption_box": caption_box},
        "before": ["내부 나"], "after": ["〈그림 B〉 두 그림의 공통 설명"], "pick_list": list(picks),
        "asset_file": asset, "evidence": "", "asset_ref": f"bin{n}",
    }


@pytest.fixture
def figs(tmp_path):
    a = str(png(tmp_path / "f" / "1.png", 64, 48))
    return [
        link(1, "auto_explicit", cap="〈그림 A〉 사업 구성도", cands=(0,), asset=a, group="g1", path_text="표 2행 1열 칸 → 안쪽 표 1행 1열 칸"),
        link(2, cands=(0, 1, 2, 3, 4), group="g2", path_text="표 2행 1열 칸 → 안쪽 표 2행 1열 칸"),
        link(3, verified=True, page=0, group="g2", path_text="표 2행 1열 칸 → 안쪽 표 2행 1열 칸", asset=a,
             picks=[{"ref": "p1", "text": "〈그림 B〉 두 그림의 공통 설명", "kind": "sentence"},
                    {"ref": "p2", "text": "photo_0042.png", "kind": "file_name"}]),
        link(4, "user_linked", verified=True, page=1, cap="직접 쓴 설명", caption_box=[100, 360, 300, 20], group="g3", path_text="본문"),
        link(5, "user_undecided", group="g3", path_text="본문"),
        link(6, "user_no_caption", group="g3", path_text="본문"),
    ]


@pytest.fixture
def panel(window, qtbot, fake_worker, hwpx, figs):
    fake_worker.serve_reply = lambda cmd: {"ok": True, "data": {
        "source_sha256": SHA, "links": figs, "renderer_error": None, "note": None,
        "summary": {"total": 6, "auto_explicit": 1, "user_confirmed": 1, "location_unverified": 4}}}
    open_doc(qtbot, window, hwpx)
    click(qtbot, window, "btnFigures")
    qtbot.waitUntil(lambda: find(window, "figList").topLevelItemCount() > 0)
    return window


def rows(w):
    lst = find(w, "figList")
    out = []
    for g in range(lst.topLevelItemCount()):
        grp = lst.topLevelItem(g)
        for i in range(grp.childCount()):
            out.append((grp.text(0), [grp.child(i).text(c) for c in range(5)]))
    return out


def select(w, n):
    lst = find(w, "figList")
    for g in range(lst.topLevelItemCount()):
        grp = lst.topLevelItem(g)
        for i in range(grp.childCount()):
            if grp.child(i).text(0).endswith(f"#{n}"):
                lst.setCurrentItem(grp.child(i))
                return grp.child(i)
    raise AssertionError(f"#{n} not listed")


# ── list ─────────────────────────────────────────────────────────────────────

def test_open_panel_sends_open(panel, fake_worker, hwpx):
    cmd = fake_worker.served[0]
    assert cmd["cmd"] == "open" and cmd["src"] == str(hwpx) and cmd["out"]


def test_summary_text(panel):
    assert text(panel, "figSummary") == "그림 6개\n설명이 확인된 그림 2개 · 미확인 4개\n원본에서 위치를 표시할 수 있는 그림 2개"


def test_columns_and_groups(panel):
    lst = find(panel, "figList")
    assert [lst.headerItem().text(i) for i in range(5)] == ["그림", "설명 여부", "설명", "문서 안 위치", "원본 유형"]
    groups = [lst.topLevelItem(i).text(0) for i in range(lst.topLevelItemCount())]
    assert groups == ["표 2행 1열 칸 → 안쪽 표 1행 1열 칸", "표 2행 1열 칸 → 안쪽 표 2행 1열 칸", "본문"]


@pytest.mark.parametrize("n,status", [(1, "설명 있음"), (2, "설명 미확인"), (4, "설명 지정함"), (5, "결정 불가"), (6, "설명 없는 그림")])
def test_status_column(panel, n, status):
    row = dict((r[0].split("#")[-1], r) for _, r in rows(panel))[str(n)]
    assert row[1] == status


@pytest.mark.parametrize("n,loc", [
    (1, "후보 1쪽 · 표 2행 1열 칸 → 안쪽 표 1행 1열 칸 · 그 칸의 1번째 그림"),
    (2, "후보 1·2·3·4…쪽 · "),
    (3, "1쪽 · "),
    (5, "쪽 미확인 · 본문"),
])
def test_location_column(panel, n, loc):
    row = dict((r[0].split("#")[-1], r) for _, r in rows(panel))[str(n)]
    assert row[3].startswith(loc)


def test_unknown_rows_are_warn_colored(panel):
    assert select(panel, 2).data(0, Qt.UserRole + 1) == "warn"
    assert select(panel, 1).data(0, Qt.UserRole + 1) != "warn"


@pytest.mark.parametrize("option,shown", [
    ("모든 그림", {1, 2, 3, 4, 5, 6}), ("설명 미확인만", {2, 3, 5}), ("설명 있는 그림만", {1, 4, 6})])
def test_filter(panel, option, shown):
    find(panel, "figFilter").setCurrentText(option)
    assert {int(r[0].split("#")[-1]) for _, r in rows(panel)} == shown


def test_numbering_counts_all_figures(panel):
    find(panel, "figFilter").setCurrentText("설명 있는 그림만")
    assert [r[0].split("#")[-1] for _, r in rows(panel)] == ["1", "4", "6"]


def test_filter_keeps_selection_when_still_listed(panel):
    """Intentional change (SPEC.md §7)."""
    select(panel, 4)
    find(panel, "figFilter").setCurrentText("설명 있는 그림만")
    assert find(panel, "figList").currentItem().text(0).endswith("#4")


# ── detail and original overlay ──────────────────────────────────────────────

def test_detail_for_auto_caption(panel):
    select(panel, 1)
    d = text(panel, "figDetail")
    assert "설명: 〈그림 A〉 사업 구성도  (문서에 적힌 캡션)" in d and "그림 앞 글: 내부 나" in d
    assert "원본 화면에서 위치를 표시하지 못했습니다." in d


def test_detail_for_no_caption(panel):
    select(panel, 6)
    assert "설명: 없음 (설명 없는 그림으로 표시함)" in text(panel, "figDetail")


def test_verified_figure_jumps_to_its_page_and_boxes_it(panel, qtbot):
    select(panel, 4)
    qtbot.waitUntil(lambda: text(panel, "lblPage") == "2 / 3")
    ov = panel.overlay_state()
    assert ov["figure_box"] == [100, 200, 300, 150] and ov["label"] == "그림" and ov["caption_label"] == "설명"


def test_unverified_figure_shows_notice_on_every_page(panel, qtbot):
    select(panel, 2)
    assert panel.overlay_state()["notice"] == "이 그림은 원본 화면에서 위치를 표시하지 못했습니다 — 오른쪽 ‘문서 안 위치’를 보세요"
    click(qtbot, panel, "btnNext")
    assert panel.overlay_state()["notice"]


def test_verified_figure_other_page_draws_nothing(panel, qtbot):
    select(panel, 3)
    click(qtbot, panel, "btnNext")
    qtbot.waitUntil(lambda: text(panel, "lblPage") == "2 / 3")
    assert panel.overlay_state() == {}


# ── copy ─────────────────────────────────────────────────────────────────────

def test_copy_figure_ctrl_c(panel, qtbot):
    select(panel, 1)
    lst = find(panel, "figList")
    lst.setFocus()
    qtbot.keySequence(lst, QKeySequence.Copy)
    assert QGuiApplication.clipboard().image().width() == 64
    assert text(panel, "figStatus") == "그림을 복사했습니다 (64×48). 문서나 메일에 붙여 넣을 수 있습니다."


def test_copy_without_selection(panel):
    panel.copy_selected_figure()
    assert text(panel, "figStatus") == "먼저 목록에서 그림을 선택하세요." and tone(panel, "figStatus") == "warn"


def test_copy_figure_without_file(panel):
    select(panel, 2)
    panel.copy_selected_figure()
    assert text(panel, "figStatus") == "이 그림은 원본 파일을 꺼내지 못해 복사할 수 없습니다."


# ── edit mode ────────────────────────────────────────────────────────────────

def test_edit_mode_groups(panel, qtbot):
    assert not find(panel, "groupCaption").isVisible()
    click(qtbot, panel, "figEditToggle")
    assert find(panel, "figEditToggle").property("active") is True
    assert find(panel, "groupCaption").title() == "① 이 그림의 설명"
    assert find(panel, "groupOcr").title() == "② 그림 속 글자 (OCR)"
    assert find(panel, "groupPicks").title() == "③ 주변 원문 문장 (그림에서 가까운 순) — 더블클릭하면 ① 설명 칸에 들어갑니다"
    assert [text(panel, b) for b in ("btnCaptionSave", "btnCaptionDelete", "btnCaptionUndo")] == ["설명 저장 (Ctrl+S)", "설명 삭제", "설명 되돌리기"]
    assert find(find(panel, "figureOcr"), "ocrToCaption").isVisible()


def test_ocr_to_caption_hidden_in_view_mode(panel):
    assert not find(find(panel, "figureOcr"), "ocrToCaption").isVisible()


def test_save_without_selection(panel, qtbot):
    click(qtbot, panel, "figEditToggle")
    click(qtbot, panel, "btnCaptionSave")
    assert text(panel, "figStatus") == "먼저 그림을 선택하세요."


def test_save_empty_caption(panel, qtbot):
    click(qtbot, panel, "figEditToggle")
    select(panel, 2)
    find(panel, "captionBox").setPlainText("   ")
    click(qtbot, panel, "btnCaptionSave")
    assert text(panel, "figStatus") == "설명 칸이 비어 있습니다. 설명을 없애려면 ‘설명 삭제’를 누르세요."


def test_save_typed_caption_with_ctrl_s(panel, qtbot, fake_worker):
    click(qtbot, panel, "figEditToggle")
    select(panel, 2)
    box = find(panel, "captionBox")
    box.setPlainText("사업 흐름도")
    box.setFocus()
    qtbot.keySequence(box, QKeySequence.Save)
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("cmd") == "decide")
    assert fake_worker.served[-1] == {"cmd": "decide", "action": "text", "occ_ids": ["s0-pic2"], "text": "사업 흐름도", "origin": "typed"}


def test_ctrl_s_only_in_edit_mode(panel, qtbot, fake_worker):
    select(panel, 2)
    n = len(fake_worker.served)
    qtbot.keySequence(find(panel, "figList"), QKeySequence.Save)
    assert len(fake_worker.served) == n


def test_double_click_sentence_fills_caption(panel, qtbot):
    click(qtbot, panel, "figEditToggle")
    select(panel, 3)
    picks = find(panel, "pickList")
    picks.item(0).setSelected(True)
    panel.activate_picks()                       # = double-click
    assert find(panel, "captionBox").toPlainText() == "〈그림 B〉 두 그림의 공통 설명"
    assert text(panel, "figStatus") == "문장을 설명 칸에 넣었습니다. Ctrl+S 또는 ‘설명 저장’으로 저장합니다."


def test_saving_picked_sentence_is_a_link(panel, qtbot, fake_worker):
    click(qtbot, panel, "figEditToggle")
    select(panel, 3)
    find(panel, "pickList").item(0).setSelected(True)
    panel.activate_picks()
    click(qtbot, panel, "btnCaptionSave")
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("action") == "link")
    assert fake_worker.served[-1]["para_refs"] == ["p1"] and fake_worker.served[-1]["occ_ids"] == ["s0-pic3"]


def test_edited_picked_sentence_is_typed(panel, qtbot, fake_worker):
    click(qtbot, panel, "figEditToggle")
    select(panel, 3)
    find(panel, "pickList").item(0).setSelected(True)
    panel.activate_picks()
    find(panel, "captionBox").setPlainText("〈그림 B〉 고친 설명")
    click(qtbot, panel, "btnCaptionSave")
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("action") == "text")
    assert fake_worker.served[-1]["origin"] == "typed"


def test_pick_kinds_are_marked(panel, qtbot):
    click(qtbot, panel, "figEditToggle")
    select(panel, 3)
    assert find(panel, "pickList").item(1).text().endswith("  (파일 이름)")


def test_ocr_text_into_caption(panel, qtbot, fake_worker):
    fake_worker.reply["ocr"] = {"status": "ok", "texts": ["중소", "벤처"]}
    click(qtbot, panel, "figEditToggle")
    select(panel, 3)
    ocr = find(panel, "figureOcr")
    qtbot.mouseClick(find(ocr, "ocrRun"), Qt.LeftButton)
    qtbot.waitUntil(lambda: find(ocr, "ocrText").toPlainText() != "")
    qtbot.mouseClick(find(ocr, "ocrToCaption"), Qt.LeftButton)
    assert find(panel, "captionBox").toPlainText() == "중소 벤처"
    assert text(panel, "figStatus") == "OCR 글을 설명 칸에 넣었습니다. 고친 뒤 ‘설명 저장’을 누르세요."
    click(qtbot, panel, "btnCaptionSave")
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("action") == "text")
    assert fake_worker.served[-1]["origin"] == "ocr"


def test_ocr_to_caption_before_ocr(panel, qtbot):
    click(qtbot, panel, "figEditToggle")
    select(panel, 3)
    qtbot.mouseClick(find(find(panel, "figureOcr"), "ocrToCaption"), Qt.LeftButton)
    assert text(panel, "figStatus") == "먼저 ‘이 그림 OCR’을 눌러 글자를 읽으세요."


def test_delete_and_undo(panel, qtbot, fake_worker):
    click(qtbot, panel, "figEditToggle")
    select(panel, 4)
    click(qtbot, panel, "btnCaptionDelete")
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("action") == "unlink")
    assert fake_worker.served[-1]["occ_ids"] == ["s0-pic4"]
    click(qtbot, panel, "btnCaptionUndo")
    qtbot.waitUntil(lambda: fake_worker.served[-1] == {"cmd": "decide", "action": "undo"})


def test_multi_select_save_applies_to_all(panel, qtbot, fake_worker):
    click(qtbot, panel, "figEditToggle")
    lst = find(panel, "figList")
    items = [select(panel, 2), panel.figures.item_of("s0-pic5")]
    for item in items:
        lst.selectionModel().select(lst.indexFromItem(item), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    find(panel, "captionBox").setPlainText("공통 설명")
    click(qtbot, panel, "btnCaptionSave")
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("cmd") == "decide")
    assert sorted(fake_worker.served[-1]["occ_ids"]) == ["s0-pic2", "s0-pic5"]


def test_selection_kept_after_save(panel, qtbot, fake_worker):
    click(qtbot, panel, "figEditToggle")
    select(panel, 4)
    find(panel, "captionBox").setPlainText("새 설명")
    click(qtbot, panel, "btnCaptionSave")
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("cmd") == "decide")
    qtbot.wait(200)
    assert find(panel, "figList").currentItem().text(0).endswith("#4")


def test_new_document_clears_editor(panel, qtbot, tmp_path):
    click(qtbot, panel, "figEditToggle")
    select(panel, 2)
    find(panel, "captionBox").setPlainText("이전 문서의 설명")
    other = tmp_path / "새.hwpx"
    other.write_bytes(b"x")
    panel.open_document(str(other))
    assert find(panel, "captionBox").toPlainText() == ""


def test_serve_failure_message(window, qtbot, fake_worker, hwpx):
    fake_worker.serve_reply = {"ok": False, "error": "그림을 꺼내지 못함"}
    open_doc(qtbot, window, hwpx)
    click(qtbot, window, "btnFigures")
    qtbot.waitUntil(lambda: text(window, "figStatus") == "실패 · 그림을 꺼내지 못함")
    assert tone(window, "figStatus") == "warn"


def test_hwp_panel_explains_conversion(window, qtbot, fake_worker, tmp_path):
    fake_worker.hold["serve"] = True
    p = tmp_path / "a.hwp"
    p.write_bytes(b"x")
    window.open_document(str(p))
    click(qtbot, window, "btnFigures")
    assert text(window, "figSummary").startswith("HWP는 한컴으로 HWPX 변환본을 만든 뒤 구조를 읽습니다.")
