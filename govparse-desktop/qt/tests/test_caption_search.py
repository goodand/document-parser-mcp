"""Search by caption (설명) once captions are filled — requested 2026-11-25.

Figure panel: a search box narrows the figure list to figures whose caption contains the words typed.
Result view: the same box narrows the asset list by its 위치 / 설명 text (which carries the caption).
Matching ignores spaces and letter case, so "사업 구성도" finds "사업구성도". Searching never calls the worker.
"""

import pytest
from PySide6.QtCore import Qt

from conftest import click, find, text
from test_figures import SHA, figs, link, panel, rows, select  # noqa: F401  (fixtures reused)


def shown(w):
    return sorted(int(r[0].split("#")[-1]) for _, r in rows(w))


def search(w, q, box="figSearch"):
    find(w, box).setText(q)


# ── figure panel ─────────────────────────────────────────────────────────────

def test_search_box_exists(panel):
    box = find(panel, "figSearch")
    assert box.placeholderText() == "설명으로 검색" and box.text() == ""


def test_search_by_caption(panel):
    search(panel, "구성도")
    assert shown(panel) == [1]                       # 〈그림 A〉 사업 구성도
    assert text(panel, "figStatus") == "‘구성도’ 검색 결과 1개"


def test_search_ignores_spaces_and_case(panel):
    search(panel, "사업구성도")
    assert shown(panel) == [1]
    search(panel, "  직접   쓴 ")
    assert shown(panel) == [4]


def test_figures_without_caption_never_match(panel):
    search(panel, "그림")
    assert 2 not in shown(panel) and 5 not in shown(panel) and 6 not in shown(panel)


def test_no_match_message(panel):
    search(panel, "없는 낱말")
    assert shown(panel) == []
    assert text(panel, "figStatus") == "‘없는 낱말’와 일치하는 설명이 없습니다."


def test_clearing_search_shows_all_again(panel, qtbot):
    search(panel, "구성도")
    find(panel, "figSearch").setFocus()
    qtbot.keyClick(find(panel, "figSearch"), Qt.Key_Escape)
    assert find(panel, "figSearch").text() == "" and shown(panel) == [1, 2, 3, 4, 5, 6]


def test_search_and_filter_combine(panel):
    find(panel, "figFilter").setCurrentText("설명 미확인만")
    search(panel, "구성도")
    assert shown(panel) == []                       # #1 has a caption, so it is not "미확인"
    find(panel, "figFilter").setCurrentText("모든 그림")
    assert shown(panel) == [1]


def test_search_keeps_selection_when_still_listed(panel):
    select(panel, 4)
    search(panel, "직접")
    assert find(panel, "figList").currentItem().text(0).endswith("#4")


def test_search_drops_hidden_selection(panel):
    select(panel, 4)
    search(panel, "구성도")
    assert find(panel, "figList").currentItem() is None or not find(panel, "figList").currentItem().text(0).endswith("#4")


def test_numbering_is_unchanged_by_search(panel):
    search(panel, "직접")
    assert [r[0].split("#")[-1] for _, r in rows(panel)] == ["4"]


def test_search_does_not_call_worker(panel, fake_worker):
    n = len(fake_worker.served), len(fake_worker.calls)
    search(panel, "구성도")
    search(panel, "")
    assert (len(fake_worker.served), len(fake_worker.calls)) == n


def test_newly_saved_caption_is_searchable(panel, qtbot, fake_worker, figs):
    """Caption filled in → immediately findable by that caption (the panel refreshes from the worker's reply)."""
    def reply(cmd):
        if cmd.get("cmd") == "decide" and cmd.get("action") == "text":
            for l in figs:
                if l["occurrence"]["occ_id"] in cmd["occ_ids"]:
                    l["caption_text"], l["status"] = cmd["text"], "user_linked"
        return {"ok": True, "data": {"source_sha256": SHA, "links": figs, "renderer_error": None, "note": None,
                                     "summary": {"total": 6, "auto_explicit": 1, "user_confirmed": 2, "location_unverified": 4}}}
    fake_worker.serve_reply = reply
    search(panel, "흐름도")
    assert shown(panel) == []
    click(qtbot, panel, "figEditToggle")
    search(panel, "")
    select(panel, 2)
    find(panel, "captionBox").setPlainText("사업 흐름도")
    click(qtbot, panel, "btnCaptionSave")
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("cmd") == "decide")
    search(panel, "흐름도")
    qtbot.waitUntil(lambda: shown(panel) == [2])


def test_search_text_survives_document_refresh(panel, qtbot, fake_worker):
    """A serve reply (e.g. after undo) refills the list; the search stays applied."""
    search(panel, "직접")
    click(qtbot, panel, "figEditToggle")
    click(qtbot, panel, "btnCaptionUndo")
    qtbot.waitUntil(lambda: fake_worker.served[-1].get("action") == "undo")
    qtbot.wait(100)
    assert find(panel, "figSearch").text() == "직접" and shown(panel) == [4]


def test_new_document_clears_search(panel, tmp_path):
    search(panel, "구성도")
    other = tmp_path / "새.hwpx"
    other.write_bytes(b"x")
    panel.open_document(str(other))
    assert find(panel, "figSearch").text() == ""


def test_bridge_respects_search(panel):
    """A figure hidden by the search is not selectable through MCP either (same as the filter)."""
    search(panel, "구성도")
    assert panel.bridge_request(f"show|{SHA}|s0-pic3")["status"] == "item_missing_or_filtered"


# ── result view ──────────────────────────────────────────────────────────────

def test_result_asset_search(window, qtbot, fake_worker, tmp_path):
    from test_result_view import ASSETS, _result

    d, idx = _result(tmp_path, [dict(a) for a in ASSETS])
    fake_worker.reply["index"] = idx
    window.load_result(str(d / "document.json"))
    lst = find(window, "assetList")
    qtbot.waitUntil(lambda: lst.topLevelItemCount() == 3)
    assert find(window, "assetSearch").placeholderText() == "설명으로 검색"
    search(window, "그림A", box="assetSearch")
    visible = [lst.topLevelItem(i).text(0) for i in range(lst.topLevelItemCount()) if not lst.topLevelItem(i).isHidden()]
    assert visible == ["bin1.png"]
    search(window, "", box="assetSearch")
    assert all(not lst.topLevelItem(i).isHidden() for i in range(lst.topLevelItemCount()))
