"""표 고치기 모드 (SPEC.md §8): table model + save rules, then the GUI mode.

Files are the engine's table JSON v1.0 (engine-handoff-table-edit-and-rhwp.md); only gpqt/table_edit.py knows the format.
"""

import hashlib
import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from conftest import click, find, png, text, wait_status


def cell(t, rowspan=1, colspan=1, bbox=None):
    return {"text": t, "rowspan": rowspan, "colspan": colspan, "bbox": bbox}


PLAIN = [[cell("구분", bbox=[0, 0, 50, 20]), cell("담당 업무", bbox=[50, 0, 200, 20])],
         [cell("주관 기관"), cell("계획 수립")],
         [cell("수행 기관"), cell("과제 수행")]]
MERGED = [[cell("연도", rowspan=2), cell("예산", colspan=2)],
          [cell("본예산"), cell("추경")],
          [cell("2026"), cell("10"), cell("2")]]


def v1(rows, table_id="s0/b3/t0", status="ready", version="1.0", nested=None) -> dict:
    """Rows of cells (HTML-like, merged-away positions absent) -> engine table JSON v1.0."""
    taken, cells, width = set(), [], 0
    for r, row in enumerate(rows):
        c = 0
        for x in row:
            while (r, c) in taken:
                c += 1
            cells.append({"row": r, "col": c, "row_span": x["rowspan"], "col_span": x["colspan"], "text": x["text"],
                          "bbox_px": x["bbox"], "nested_table_ids": (nested or {}).get((r, c), [])})
            taken |= {(r + i, c + j) for i in range(x["rowspan"]) for j in range(x["colspan"])}
            c += x["colspan"]
            width = max(width, c)
    return {"schema_version": version, "source_kind": "native" if status == "ready" else "image", "table_id": table_id,
            "asset_id": None, "image_sha256": None, "status": status, "rows": len(rows), "cols": width, "cells": cells}


def write_table(path: Path, rows, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(v1(rows, **kw), ensure_ascii=False), "utf-8")
    return path


def saved_cell(saved: dict, r: int, c: int) -> dict:
    return next(x for x in saved["cells"] if (x["row"], x["col"]) == (r, c))


def edited_of(path: Path) -> Path:
    return path.with_name(path.stem + ".edited.json")


# ───────────────────────────── model ─────────────────────────────

@pytest.fixture
def plain(tmp_path):
    return write_table(tmp_path / "tables" / "table-aaa.json", PLAIN)


def load(path):
    from gpqt.table_edit import TableDoc

    return TableDoc.load(path)


def test_model_loads_engine_table(plain):
    d = load(plain)
    assert (d.n_rows, d.n_cols) == (3, 2)
    assert d.text(1, 0) == "주관 기관" and d.bbox(0, 1) == [50, 0, 200, 20]
    assert not d.dirty and not d.differs_from_engine and d.edited_cells() == 0 and not d.has_merge


def test_model_rejects_unknown_format(tmp_path):
    from gpqt.table_edit import TableFormatError

    f = write_table(tmp_path / "t.json", PLAIN, version="9.0")
    with pytest.raises(TableFormatError):
        load(f)


def test_model_edit_then_save_writes_only_the_edited_file(plain):
    before = plain.read_bytes()
    d = load(plain)
    d.set_text(1, 1, "계획 수립, 요청서 작성")
    assert d.dirty and d.edited_cells() == 1
    out = d.save()
    assert out == edited_of(plain) and out.is_file()
    assert plain.read_bytes() == before                      # the engine result is never written
    saved = json.loads(out.read_text("utf-8"))
    assert saved["base_sha256"] == hashlib.sha256(before).hexdigest()
    assert saved_cell(saved, 1, 1)["text"] == "계획 수립, 요청서 작성" and saved_cell(saved, 1, 1)["edited"] is True
    assert saved_cell(saved, 0, 1)["bbox_px"] == [50, 0, 200, 20] and saved_cell(saved, 0, 1)["edited"] is False
    assert saved["edits"] == {"cells": 1, "structure_changed": False, "history": []}
    assert (saved["schema_version"], saved["table_id"], saved["rows"], saved["cols"]) == ("1.0", "s0/b3/t0", 3, 2)
    assert not d.dirty
    assert [p.name for p in plain.parent.iterdir()] == ["table-aaa.edited.json", "table-aaa.json"] or \
        sorted(p.name for p in plain.parent.iterdir()) == ["table-aaa.edited.json", "table-aaa.json"]   # no temp leftovers


def test_model_reload_uses_saved_edit(plain):
    d = load(plain)
    d.set_text(2, 0, "수행 업체")
    d.save()
    again = load(plain)
    assert again.text(2, 0) == "수행 업체" and again.edited_cells() == 1
    assert again.differs_from_engine and not again.dirty and not again.stale_edit


def test_model_setting_same_text_is_not_an_edit(plain):
    d = load(plain)
    d.set_text(0, 0, "구분")
    assert not d.dirty and d.edited_cells() == 0


def test_model_save_without_difference_leaves_no_edited_file(plain):
    d = load(plain)
    d.set_text(0, 0, "항목")
    d.save()
    assert edited_of(plain).is_file()
    d.reset()
    assert d.dirty                                             # file still holds the old edit
    assert d.save() is None and not edited_of(plain).exists()
    assert not d.dirty and not d.differs_from_engine


def test_model_stale_edit_is_not_applied(plain):
    d = load(plain)
    d.set_text(0, 0, "항목")
    d.save()
    write_table(plain, [[cell("새로 읽은 표")]])              # the engine read the table again
    again = load(plain)
    assert again.stale_edit and again.text(0, 0) == "새로 읽은 표" and not again.differs_from_engine


def test_model_rows_and_columns(plain):
    d = load(plain)
    d.insert_row(1)                                            # above the second row
    assert d.n_rows == 4 and d.text(1, 0) == "" and d.text(2, 0) == "주관 기관" and d.bbox(1, 0) is None
    d.insert_col(2)                                            # after the last column
    assert d.n_cols == 3 and d.text(0, 2) == ""
    d.delete_row(0)
    assert d.n_rows == 3 and d.text(1, 0) == "주관 기관"
    d.delete_col(0)
    assert d.n_cols == 2 and d.text(1, 0) == "계획 수립"
    assert d.structure_changed and d.dirty
    saved = json.loads(d.save().read_text("utf-8"))
    assert saved["edits"]["structure_changed"] is True
    assert saved["edits"]["history"] == [{"op": "insert_row", "at": 1}, {"op": "insert_col", "at": 2},
                                         {"op": "delete_row", "at": 0}, {"op": "delete_col", "at": 0}]
    assert (saved["rows"], saved["cols"], len(saved["cells"])) == (3, 2, 6)
    assert saved_cell(saved, 0, 1)["bbox_px"] is None           # a new cell has no place in the image


def test_model_last_row_and_column_cannot_be_deleted(tmp_path):
    from gpqt.table_edit import EditError

    d = load(write_table(tmp_path / "one.json", [[cell("하나")]]))
    with pytest.raises(EditError):
        d.delete_row(0)
    with pytest.raises(EditError):
        d.delete_col(0)
    assert (d.n_rows, d.n_cols) == (1, 1) and not d.dirty


def test_model_merged_table_allows_text_but_not_structure(tmp_path):
    from gpqt.table_edit import EditError

    d = load(write_table(tmp_path / "m.json", MERGED))
    assert d.has_merge and (d.n_rows, d.n_cols) == (3, 3)
    assert {(r, c): (rs, cs) for r, c, rs, cs in d.spans()} == {(0, 0): (2, 1), (0, 1): (1, 2)}
    assert d.text(1, 1) == "본예산" and d.text(2, 2) == "2"
    d.set_text(0, 1, "예산(백만원)")
    assert d.text(0, 1) == "예산(백만원)" and d.edited_cells() == 1
    for op in (lambda: d.insert_row(0), lambda: d.delete_row(2), lambda: d.insert_col(1), lambda: d.delete_col(2)):
        with pytest.raises(EditError):
            op()
    assert d.can_change_structure is False


def test_model_undo_and_reset(plain):
    d = load(plain)
    assert not d.can_undo
    d.set_text(0, 0, "항목")
    d.insert_row(3)
    d.undo()
    assert d.n_rows == 3 and d.text(0, 0) == "항목"
    d.undo()
    assert d.text(0, 0) == "구분" and not d.dirty and not d.can_undo
    d.set_text(0, 0, "항목")
    d.reset()
    assert d.text(0, 0) == "구분" and not d.differs_from_engine
    d.undo()                                                   # reset can be undone
    assert d.text(0, 0) == "항목"


def test_model_multiline_text_and_nested_ids_survive_a_save(tmp_path):
    f = write_table(tmp_path / "n.json", [[cell("지급액\n(백만원)"), cell("안쪽 표가 있는 칸")]], nested={(0, 1): ["s0/b3/t0/c0_1/t0"]})
    d = load(f)
    assert d.text(0, 0) == "지급액\n(백만원)" and d.nested(0, 1) == ["s0/b3/t0/c0_1/t0"] and d.nested(0, 0) == []
    d.set_text(0, 0, "지급액\n(천원)")
    saved = json.loads(d.save().read_text("utf-8"))
    assert saved_cell(saved, 0, 0)["text"] == "지급액\n(천원)" and saved_cell(saved, 0, 1)["nested_table_ids"] == ["s0/b3/t0/c0_1/t0"]


def test_model_image_table_needs_a_person_to_confirm(tmp_path):
    f = write_table(tmp_path / "img.json", PLAIN, status="review")
    d = load(f)
    assert d.needs_confirm and d.can_save and not d.dirty      # nothing changed, but nobody has checked it
    out = d.save()
    assert out is not None and json.loads(out.read_text("utf-8"))["review"] == {"confirmed_by_user": True}
    assert not d.needs_confirm and not d.can_save
    assert not load(f).needs_confirm                           # the confirmation is remembered


def test_model_corrections_follow_the_folder(tmp_path):
    """ZIP unpacked somewhere else: the edited file sits next to the candidate, no absolute paths inside."""
    import shutil

    f = write_table(tmp_path / "a" / "tables" / "k.json", MERGED)
    d = load(f)
    d.set_text(2, 1, "11")
    d.save()
    shutil.copytree(tmp_path / "a", tmp_path / "다른 PC" / "풀린 폴더")
    again = load(tmp_path / "다른 PC" / "풀린 폴더" / "tables" / "k.json")
    assert again.text(2, 1) == "11" and again.edited_cells() == 1 and again.spans() == d.spans()
    assert str(tmp_path) not in edited_of(f).read_text("utf-8")


def test_model_stale_corrections_are_kept_when_new_ones_are_saved(plain):
    d = load(plain)
    d.set_text(0, 0, "옛 수정")
    d.save()
    write_table(plain, [[cell("새로 읽은 표")]])
    again = load(plain)
    again.set_text(0, 0, "새 수정")
    again.save()
    names = sorted(p.name for p in plain.parent.iterdir())
    assert names == ["table-aaa.edited.json", "table-aaa.json", "table-aaa.previous-edited.json"]
    assert "옛 수정" in (plain.parent / "table-aaa.previous-edited.json").read_text("utf-8")


def test_model_non_editable_status_is_refused(tmp_path):
    from gpqt.table_edit import TableFormatError

    with pytest.raises(TableFormatError):
        load(write_table(tmp_path / "x.json", PLAIN, status="no_grid"))


# ───────────────────────────── GUI mode ─────────────────────────────

def _result(tmp_path, tables):
    """tables: list of (name, label, rows | None, status, reason[, nested])."""
    d = tmp_path / "결과-json"
    (d / "assets" / "tables").mkdir(parents=True)
    (d / "document.json").write_text("{}", "utf-8")
    (d / "report.json").write_text('{"source": "C:/docs/합성 문서.hwpx"}', "utf-8")
    rows = [{"origin": "embedded_picture", "path": "assets/embedded/bin1.png", "text": "〈그림 A〉",
             "file": str(png(d / "assets" / "embedded" / "bin1.png")), "ocr_status": "not_run", "ocr_texts": [], "page": None}]
    for i, (name, label, trows, status, reason, *more) in enumerate(tables):
        img = png(d / "assets" / "tables" / f"{name}-p001.png", 300 + i, 100)
        tj = {"status": status, "file": None, "reason": reason}
        if trows is not None:
            tj["file"] = str(write_table(d / "tables" / f"{name}.json", trows, table_id=f"s0/b{i}/t0", status=status,
                                         nested=more[0] if more else None))
        rows.append({"origin": "table_image", "path": f"assets/tables/{name}-p001.png", "file": str(img), "table_id": f"s0/b{i}/t0",
                     "part": 1, "parts": 1, "label": label, "text": label, "ocr_status": "not_run", "ocr_texts": [], "page": None,
                     "table_json": tj})
    idx = {"assets": rows, "missing": [], "source_name": "합성 문서.hwpx", "table_images": None, "capture_state": "not_used",
           "convertible": True, "integrity": {"checked": 3, "changed": [], "missing": []}}
    return d, idx


THREE = [("table-aaa", "본문 1번째 표", PLAIN, "ready", ""),
         ("table-bbb", "본문 2번째 표", MERGED, "ready", ""),
         ("table-ccc", "본문 3번째 표", None, "no_grid", "표 선을 찾지 못했습니다")]


@pytest.fixture
def result(window, qtbot, fake_worker, tmp_path):
    def go(tables=THREE, prepare=None):
        d, idx = _result(tmp_path, tables)
        if prepare:
            prepare(d)                       # files as they are on disk before the result is opened
        fake_worker.reply["index"] = idx
        window.load_result(str(d / "document.json"))
        qtbot.waitUntil(lambda: "미리보기 성공" in text(window, "resState"))
        return d
    return go


@pytest.fixture
def mode(window, qtbot, result):
    d = result()
    click(qtbot, window, "btnTableEdit")
    return d


def _list(window):
    lst = find(window, "tblList")
    return [[lst.topLevelItem(i).text(c) for c in range(3)] for i in range(lst.topLevelItemCount())]


def _pick(window, row):
    lst = find(window, "tblList")
    lst.setCurrentItem(lst.topLevelItem(row))


def _grid(window):
    g = find(window, "tblGrid")
    return [[(g.item(r, c).text() if g.item(r, c) else None) for c in range(g.columnCount())] for r in range(g.rowCount())]


def _edit(window, r, c, value):
    find(window, "tblGrid").item(r, c).setText(value)        # same signal path as typing into the cell


def test_entry_button_only_for_json_results(window, qtbot, result, fake_worker, tmp_path):
    assert not find(window, "btnTableEdit").isVisible()       # nothing loaded
    result()
    b = find(window, "btnTableEdit")
    assert b.isVisible() and b.isEnabled() and b.text() == "표 고치기"
    assert not find(window, "tableEditPanel").isVisible()
    html = tmp_path / "out.html"
    html.write_text("<p>x</p>", "utf-8")
    window.load_result(str(html))
    assert not find(window, "btnTableEdit").isVisible()


def test_entry_button_disabled_without_table_images(window, result):
    result([])
    b = find(window, "btnTableEdit")
    assert b.isVisible() and not b.isEnabled()
    assert "표 이미지가 없습니다" in b.toolTip()


def test_entry_button_enabled_when_tables_can_only_be_read_from_images(window, result):
    result([("table-ccc", "본문 1번째 표", None, "failed", "선이 희미함")])
    assert find(window, "btnTableEdit").isEnabled()


def test_entering_and_leaving_the_mode(window, qtbot, mode):
    assert find(window, "tableEditPanel").isVisible()
    assert text(window, "btnTableEdit") == "표 고치기 닫기"
    for name in ("assetList", "assetSearch", "resultOcr"):
        assert not find(window, name).isVisible()
    click(qtbot, window, "btnTableEdit")
    assert not find(window, "tableEditPanel").isVisible() and find(window, "assetList").isVisible()
    assert text(window, "btnTableEdit") == "표 고치기"


def test_list_shows_only_tables_with_state_and_size(window, mode):
    assert _list(window) == [["본문 1번째 표", "원본 표에서 읽음", "3행 2열"],
                             ["본문 2번째 표", "원본 표에서 읽음", "3행 3열"],
                             ["본문 3번째 표", "표 구조 확인 못함 · 표 선을 찾지 못했습니다", ""]]
    assert [find(window, "tblList").headerItem().text(c) for c in range(3)] == ["표", "상태", "크기"]


def test_first_table_is_shown_with_its_image(window, mode):
    assert _grid(window) == [["구분", "담당 업무"], ["주관 기관", "계획 수립"], ["수행 기관", "과제 수행"]]
    assert window.asset_preview_source_size().width() == 300   # table-aaa image
    _pick(window, 1)
    assert window.asset_preview_source_size().width() == 301


def test_merged_cells_are_spanned(window, mode):
    _pick(window, 1)
    g = find(window, "tblGrid")
    assert (g.rowSpan(0, 0), g.columnSpan(0, 0)) == (2, 1) and (g.rowSpan(0, 1), g.columnSpan(0, 1)) == (1, 2)
    assert g.item(1, 1).text() == "본예산" and g.item(2, 2).text() == "2"


def test_unreadable_table_shows_reason_instead_of_grid(window, mode):
    _pick(window, 2)
    assert not find(window, "tblGrid").isVisible()
    assert find(window, "tblMessage").isVisible() and "표 선을 찾지 못했습니다" in text(window, "tblMessage")
    assert not find(window, "tblSave").isEnabled()


def test_edit_then_save(window, qtbot, mode):
    engine = mode / "tables" / "table-aaa.json"
    before = engine.read_bytes()
    assert not find(window, "tblSave").isEnabled() and not find(window, "tblUndo").isEnabled()
    _edit(window, 1, 1, "계획 수립, 요청서 작성")
    assert _list(window)[0][1] == "저장 안 됨 · 1칸"
    assert text(window, "tblState") == "저장 안 됨 · 1칸"
    click(qtbot, window, "tblSave")
    assert _list(window)[0][1] == "사람이 고침 · 1칸"
    assert engine.read_bytes() == before and edited_of(engine).is_file()
    wait_status(qtbot, window, "표를 저장했습니다 · 본문 1번째 표 · 고친 칸 1개")
    assert not find(window, "tblSave").isEnabled()


def test_saved_edit_is_shown_after_reloading_the_result(window, qtbot, mode, fake_worker):
    _edit(window, 0, 0, "항목")
    click(qtbot, window, "tblSave")
    window.load_result(str(mode / "document.json"))
    qtbot.waitUntil(lambda: "미리보기 성공" in text(window, "resState"))
    assert not find(window, "tableEditPanel").isVisible()      # loading a result leaves the mode
    click(qtbot, window, "btnTableEdit")
    assert _list(window)[0][1] == "사람이 고침 · 1칸" and _grid(window)[0][0] == "항목"


def test_context_menu_items(window, mode):
    panel = find(window, "tableEditPanel")
    find(window, "tblGrid").setCurrentCell(1, 0)
    assert [(label, on) for _, label, on in panel.context_actions()] == [
        ("위에 행 추가", True), ("아래에 행 추가", True), ("이 행 삭제", True),
        ("왼쪽에 열 추가", True), ("오른쪽에 열 추가", True), ("이 열 삭제", True), ("안쪽 표 열기", False)]
    _pick(window, 1)                                           # merged table
    find(window, "tblGrid").setCurrentCell(2, 0)
    assert all(not on for _, _, on in panel.context_actions())


def test_rows_and_columns_from_the_menu(window, qtbot, mode):
    panel = find(window, "tableEditPanel")
    g = find(window, "tblGrid")
    g.setCurrentCell(1, 0)
    panel.do("row_below")
    assert g.rowCount() == 4 and _grid(window)[2] == ["", ""] and _grid(window)[3][0] == "수행 기관"
    g.setCurrentCell(0, 1)
    panel.do("col_right")
    assert g.columnCount() == 3
    g.setCurrentCell(0, 0)
    panel.do("row_delete")
    assert g.rowCount() == 3 and _grid(window)[0][0] == "주관 기관"
    assert _list(window)[0][1].endswith("· 행·열 바뀜") and _list(window)[0][2] == "3행 3열"
    click(qtbot, window, "tblSave")
    saved = json.loads(edited_of(mode / "tables" / "table-aaa.json").read_text("utf-8"))
    assert saved["edits"]["structure_changed"] is True and saved["rows"] == 3


def test_last_row_cannot_be_deleted_from_the_menu(window, result, qtbot):
    result([("table-one", "본문 1번째 표", [[cell("하나")]], "ready", "")])
    click(qtbot, window, "btnTableEdit")
    find(window, "tblGrid").setCurrentCell(0, 0)
    acts = {a: on for a, _, on in find(window, "tableEditPanel").context_actions()}
    assert acts["row_delete"] is False and acts["col_delete"] is False and acts["row_above"] is True


def test_undo_and_reset(window, qtbot, mode):
    _edit(window, 0, 0, "항목")
    find(window, "tableEditPanel").do("row_below")
    click(qtbot, window, "tblUndo")
    assert find(window, "tblGrid").rowCount() == 3 and _grid(window)[0][0] == "항목"
    click(qtbot, window, "tblUndo")
    assert _grid(window)[0][0] == "구분" and _list(window)[0][1] == "원본 표에서 읽음"
    assert not find(window, "tblUndo").isEnabled() and not find(window, "tblSave").isEnabled()
    _edit(window, 0, 0, "항목")
    click(qtbot, window, "tblSave")
    assert find(window, "tblReset").isEnabled()
    click(qtbot, window, "tblReset")
    assert _grid(window)[0][0] == "구분" and find(window, "tblSave").isEnabled()   # file still has the edit
    click(qtbot, window, "tblSave")
    assert not edited_of(mode / "tables" / "table-aaa.json").exists() and _list(window)[0][1] == "원본 표에서 읽음"
    wait_status(qtbot, window, "원본에서 읽은 표로 되돌렸습니다 · 본문 1번째 표")


def test_unsaved_edits_survive_switching_tables_and_leaving(window, qtbot, mode):
    _edit(window, 2, 0, "수행 업체")
    _pick(window, 1)
    _edit(window, 2, 1, "11")
    _pick(window, 0)
    assert _grid(window)[2][0] == "수행 업체" and _list(window)[1][1] == "저장 안 됨 · 1칸"
    click(qtbot, window, "btnTableEdit")                       # leave
    wait_status(qtbot, window, "저장하지 않은 표 2개가 있습니다")
    click(qtbot, window, "btnTableEdit")                       # back
    assert _grid(window)[2][0] == "수행 업체"
    assert not (mode / "tables" / "table-aaa.edited.json").exists()


def test_opening_another_result_discards_unsaved_edits(window, qtbot, mode, fake_worker):
    _edit(window, 0, 0, "항목")
    window.load_result(str(mode / "document.json"))
    qtbot.waitUntil(lambda: "미리보기 성공" in text(window, "resState"))
    click(qtbot, window, "btnTableEdit")
    assert _grid(window)[0][0] == "구분" and _list(window)[0][1] == "원본 표에서 읽음"


def test_stale_edit_is_reported(window, qtbot, result):
    def old_edit(d):
        edited_of(d / "tables" / "table-aaa.json").write_text(json.dumps(
            {**v1([[cell("옛 수정")]], table_id="s0/b0/t0"), "base_sha256": "0" * 64,
             "edits": {"cells": 1, "structure_changed": True, "history": []}}, ensure_ascii=False), "utf-8")
    result(prepare=old_edit)
    click(qtbot, window, "btnTableEdit")
    assert _list(window)[0][1] == "원본 표에서 읽음 · 이전 결과에서 수정함"
    assert _grid(window)[0][0] == "구분"


def test_broken_table_file_is_listed_not_fatal(window, qtbot, result):
    result(prepare=lambda d: (d / "tables" / "table-aaa.json").write_text("{not json", "utf-8"))
    click(qtbot, window, "btnTableEdit")
    assert _list(window)[0][1].startswith("검사 실패 · ")
    assert _list(window)[1][1] == "원본 표에서 읽음"


def test_image_table_is_marked_until_a_person_confirms(window, qtbot, result):
    d = result([("table-img", "본문 그림 1", PLAIN, "review", ""), ("table-new", "본문 그림 2", None, "not_checked", "")])
    click(qtbot, window, "btnTableEdit")
    assert [r[1] for r in _list(window)] == ["이미지에서 읽음 · 확인 필요", "검사 전"]
    save = find(window, "tblSave")
    assert save.text() == "확인 후 저장" and save.isEnabled()   # confirming without a change is allowed
    click(qtbot, window, "tblSave")
    assert _list(window)[0][1] == "사람이 확인함" and not save.isEnabled()
    assert json.loads(edited_of(d / "tables" / "table-img.json").read_text("utf-8"))["review"]["confirmed_by_user"] is True
    _edit(window, 0, 0, "항목")
    click(qtbot, window, "tblSave")
    assert _list(window)[0][1] == "사람이 고침 · 1칸"


def test_save_button_says_save_for_source_tables(window, mode):
    assert text(window, "tblSave") == "저장"


def test_nested_table_opens_from_its_cell(window, qtbot, result):
    result([("table-out", "본문 1번째 표", PLAIN, "ready", "", {(1, 1): ["s0/b1/t0"]}),
            ("table-in", "본문 1번째 표 › 2행 2열 칸 안 1번째 표", [[cell("안쪽")]], "ready", "")])
    click(qtbot, window, "btnTableEdit")
    panel, g = find(window, "tableEditPanel"), find(window, "tblGrid")
    g.setCurrentCell(0, 0)
    assert dict((a, on) for a, _, on in panel.context_actions())["nested_open"] is False
    g.setCurrentCell(1, 1)
    assert "안에 표가 있습니다" in g.item(1, 1).toolTip()
    assert dict((a, on) for a, _, on in panel.context_actions())["nested_open"] is True
    panel.do("nested_open")
    assert find(window, "tblList").currentItem().text(0).endswith("칸 안 1번째 표") and _grid(window) == [["안쪽"]]


# ───────────────────────────── 이미지에서 표 읽기 (engine govparse.image_table) ─────────────────────────────

def image_candidate(png_file: Path, rows, ocr=True):
    """Shaped like govparse.image_table.extract_table_from_image (+ worker: file name only)."""
    doc = v1(rows, status="review")
    for c in doc["cells"]:
        c["text_check"] = ("review" if c["text"] else "not_detected") if ocr else "not_checked"
        c.pop("nested_table_ids")
        c["bbox_px"] = c["bbox_px"] or [10 + 40 * c["col"], 5 + 20 * c["row"], 45 + 40 * c["col"], 22 + 20 * c["row"]]
    doc.update(source_kind="image", structure_check="review", table_id=None,
               image={"path": png_file.name, "width_px": 300, "height_px": 100,
                      "sha256": hashlib.sha256(png_file.read_bytes()).hexdigest()},
               model={"name": "RapidTable SLANETPLUS", "version": "3.0.2", "sha256": "d" * 64},
               warnings=["Review predicted rows, columns, and merged cells against the image."])
    if not rows:
        doc.update(rows=0, cols=0, cells=[], status="no_grid")
    return doc


def test_model_keeps_image_fields_and_text_check(tmp_path):
    png_file = png(tmp_path / "assets" / "tables" / "t-p001.png", 300, 100)
    f = tmp_path / "tables" / "t-p001.image.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(image_candidate(png_file, [[cell("구분"), cell("")]]), ensure_ascii=False), "utf-8")
    d = load(f)
    assert d.status == "review" and d.needs_confirm
    assert d.text_check(0, 0) == "review" and d.text_check(0, 1) == "not_detected"
    assert d.image_sha256 == hashlib.sha256(png_file.read_bytes()).hexdigest() and d.bbox(0, 1) == [50, 5, 85, 22]
    d.set_text(0, 1, "담당 업무")
    assert d.text_check(0, 1) is None                       # a person typed it: no longer an unchecked OCR value
    saved = json.loads(d.save().read_text("utf-8"))
    assert saved["model"]["name"] == "RapidTable SLANETPLUS" and saved["structure_check"] == "review"
    assert saved_cell(saved, 0, 0)["text_check"] == "review" and saved_cell(saved, 0, 1)["edited"] is True


def _image_reply(rows, ocr_cells=None):
    """FakeWorker answer for image-table: writes the candidate where the GUI asked, as the worker does."""
    def reply(call):
        png_file, out, mode = Path(call.args[0]), Path(call.args[1]), call.args[2]
        cand = image_candidate(png_file, rows, ocr=(mode == "ocr"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cand, ensure_ascii=False), "utf-8")
        return {"file": str(out), "status": cand["status"], "rows": cand["rows"], "cols": cand["cols"],
                "cells": len(cand["cells"]), "ocr_cells": ocr_cells if ocr_cells is not None else
                sum(c["text_check"] == "review" for c in cand["cells"]), "warnings": cand["warnings"]}
    return reply


PICTURE_ONLY = [("table-pic", "본문 1번째 표", None, "no_grid", "원본에 칸 정보 없음"),
                ("table-aaa", "본문 2번째 표", PLAIN, "ready", "")]


def _read(window, qtbot):
    click(qtbot, window, "btnTableEdit")
    click(qtbot, window, "tblRead")


def test_read_button_only_for_tables_without_source_cells(window, qtbot, result):
    result(PICTURE_ONLY)
    click(qtbot, window, "btnTableEdit")
    read = find(window, "tblRead")
    assert read.isVisible() and read.isEnabled() and read.text() == "이미지에서 표 읽기"
    assert find(window, "tblReadOcr").isChecked()
    _pick(window, 1)                                           # read from the source cells: never overwritten
    assert not read.isVisible()


def test_read_from_image_fills_the_grid_for_review(window, qtbot, result, fake_worker):
    d = result(PICTURE_ONLY)
    fake_worker.reply["image-table"] = _image_reply([[cell("구분"), cell("담당 업무")], [cell("주관 기관"), cell("")]])
    _read(window, qtbot)
    qtbot.waitUntil(lambda: _list(window)[0][1] == "이미지에서 읽음 · 확인 필요")
    call = [c for c in fake_worker.calls if c.mode == "image-table"][-1]
    assert Path(call.args[0]).name == "table-pic-p001.png" and call.args[2] == "ocr" and call.args[3] == "s0/b0/t0"
    assert Path(call.args[1]) == d / "tables" / "table-pic-p001.image.json"
    assert _grid(window) == [["구분", "담당 업무"], ["주관 기관", ""]]
    wait_status(qtbot, window, "표 그림을 읽었습니다 · 본문 1번째 표 · 2행 2열 · 글자 읽은 칸 3개")
    g = find(window, "tblGrid")
    assert "원본과 확인 필요" in g.item(0, 0).toolTip() and "빈 칸으로 확인된 것은 아님" in g.item(1, 1).toolTip()
    assert text(window, "tblSave") == "확인 후 저장" and find(window, "tblSave").isEnabled()
    assert text(window, "tblRead") == "이미지에서 다시 읽기"
    assert not (d / "tables" / "table-pic-p001.image.edited.json").exists()


def test_read_without_ocr(window, qtbot, result, fake_worker):
    result(PICTURE_ONLY)
    fake_worker.reply["image-table"] = _image_reply([[cell(""), cell("")]], ocr_cells=0)
    click(qtbot, window, "btnTableEdit")
    find(window, "tblReadOcr").setChecked(False)
    click(qtbot, window, "tblRead")
    wait_status(qtbot, window, "글자는 읽지 않음")
    assert [c for c in fake_worker.calls if c.mode == "image-table"][-1].args[2] == "no-ocr"


def test_selected_cell_is_outlined_on_the_picture(window, qtbot, result, fake_worker):
    result(PICTURE_ONLY)
    fake_worker.reply["image-table"] = _image_reply([[cell("구분"), cell("담당 업무")]])
    _read(window, qtbot)
    qtbot.waitUntil(lambda: _list(window)[0][1].startswith("이미지에서 읽음"))
    find(window, "tblGrid").setCurrentCell(0, 1)
    assert window.preview_box == [50, 5, 85, 22]
    _pick(window, 1)                                           # source-cell table: its boxes are not image pixels
    find(window, "tblGrid").setCurrentCell(0, 1)
    assert window.preview_box is None


def test_box_is_not_drawn_on_a_different_picture(window, qtbot, result, fake_worker):
    d = result(PICTURE_ONLY)
    fake_worker.reply["image-table"] = _image_reply([[cell("구분")]])
    _read(window, qtbot)
    qtbot.waitUntil(lambda: _list(window)[0][1].startswith("이미지에서 읽음"))
    png(d / "assets" / "tables" / "table-pic-p001.png", 300, 100, color=(200, 0, 0))   # picture replaced afterwards
    window.table_panel._sha.clear()
    find(window, "tblGrid").setCurrentCell(0, 0)
    window.table_panel._highlight()
    assert window.preview_box is None


def test_no_grid_from_the_image_keeps_the_picture(window, qtbot, result, fake_worker):
    result(PICTURE_ONLY)
    fake_worker.reply["image-table"] = _image_reply([])
    _read(window, qtbot)
    wait_status(qtbot, window, "표 그림에서 격자를 찾지 못했습니다")
    assert _list(window)[0][1] == "표 구조 확인 못함 · 이미지에서 표 격자를 찾지 못했습니다"
    assert find(window, "tblMessage").isVisible() and window.asset_preview_source_size().width() == 300
    assert find(window, "tblRead").isEnabled()                 # can try again


def test_read_failure_is_reported(window, qtbot, result, fake_worker):
    result(PICTURE_ONLY)
    fake_worker.reply["image-table"] = RuntimeError("이 엔진에는 이미지에서 표를 읽는 기능이 없습니다")
    _read(window, qtbot)
    wait_status(qtbot, window, "표 그림을 읽지 못했습니다 · 본문 1번째 표 · 이 엔진에는 이미지에서 표를 읽는 기능이 없습니다")
    assert find(window, "tblRead").isEnabled()


def test_image_read_table_is_found_again_after_reopening(window, qtbot, result, fake_worker):
    d = result(PICTURE_ONLY)
    fake_worker.reply["image-table"] = _image_reply([[cell("구분"), cell("담당")]])
    _read(window, qtbot)
    qtbot.waitUntil(lambda: _list(window)[0][1].startswith("이미지에서 읽음"))
    _edit(window, 0, 1, "담당 업무")
    click(qtbot, window, "tblSave")
    window.load_result(str(d / "document.json"))
    qtbot.waitUntil(lambda: "미리보기 성공" in text(window, "resState"))
    click(qtbot, window, "btnTableEdit")
    assert _list(window)[0][1] == "사람이 고침 · 1칸" and _grid(window) == [["구분", "담당 업무"]]


def test_unsaved_edits_block_reading_again(window, qtbot, result, fake_worker):
    result(PICTURE_ONLY)
    fake_worker.reply["image-table"] = _image_reply([[cell("구분")]])
    _read(window, qtbot)
    qtbot.waitUntil(lambda: _list(window)[0][1].startswith("이미지에서 읽음"))
    _edit(window, 0, 0, "항목")
    read = find(window, "tblRead")
    assert not read.isEnabled() and "저장하지 않은 수정" in read.toolTip()


def test_multi_page_table_is_not_read_from_one_piece(window, qtbot, result, fake_worker):
    d = result(PICTURE_ONLY)
    for a in fake_worker.reply["index"]["assets"]:
        if a.get("table_id") == "s0/b0/t0":
            a["parts"] = 2
    window.load_result(str(d / "document.json"))
    qtbot.waitUntil(lambda: "미리보기 성공" in text(window, "resState"))
    click(qtbot, window, "btnTableEdit")
    read = find(window, "tblRead")
    assert not read.isEnabled() and "여러 쪽에 걸친 표" in read.toolTip()


def test_source_page_tables_are_listed_but_not_read_from_the_page(window, qtbot, result, fake_worker, tmp_path):
    d = result(PICTURE_ONLY)
    for a in fake_worker.reply["index"]["assets"]:
        if a.get("table_id"):
            a["origin"] = "table_source_page"                  # the picture is the whole source page
    window.load_result(str(d / "document.json"))
    qtbot.waitUntil(lambda: "미리보기 성공" in text(window, "resState"))
    click(qtbot, window, "btnTableEdit")
    assert [r[0] for r in _list(window)] == ["본문 1번째 표", "본문 2번째 표"]
    read = find(window, "tblRead")
    assert not read.isEnabled() and "원본 쪽 전체" in read.toolTip()
    _pick(window, 1)                                           # source cells still open for editing
    assert _grid(window)[0] == ["구분", "담당 업무"]
