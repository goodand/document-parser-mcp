"""그림 목록: figures of the open document, captions, caption search and editing (SPEC.md §3, §4-1)."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QSplitter, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .widgets import TONES, CopyTree, OcrBox, copy_picture, run_bg, set_tone

STATUS = {"auto_explicit": "설명 있음", "user_linked": "설명 지정함", "user_shared": "설명 지정함(공통)",
          "user_no_caption": "설명 없는 그림", "user_undecided": "결정 불가"}
FILTERS = ["모든 그림", "설명 미확인만", "설명 있는 그림만"]
PICK_KIND = {"figure_marker": "  (그림 표식 문구)", "file_name": "  (파일 이름)", "table_title": "  (표 제목)"}


def unknown(link) -> bool:
    return link.get("status") in ("needs_review", "user_undecided")


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").casefold()


def occ(link) -> str:
    return link["occurrence"]["occ_id"]


def location_text(link) -> str:
    loc = link.get("location") or {}
    if loc.get("verified"):
        head = f"{loc['page_index'] + 1}쪽 · "
    elif loc.get("candidate_pages"):
        c = loc["candidate_pages"]
        head = "후보 " + "·".join(str(p + 1) for p in c[:4]) + ("…" if len(c) > 4 else "") + "쪽 · "
    else:
        head = "쪽 미확인 · "
    return f"{head}{link.get('path_text', '')} · 그 칸의 {link.get('order_in_cell', 0) + 1}번째 그림"


class SearchBox(QLineEdit):
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.text():
            self.clear()
            e.accept()
            return
        super().keyPressEvent(e)


class FigurePanel(QWidget):
    def __init__(self, poster, worker, on_select):
        super().__init__()
        self.setObjectName("figurePanel")
        self.poster, self.worker, self.on_select = poster, worker, on_select
        self.hash = None
        self.source = None
        self.links: list[dict] = []
        self.current: dict | None = None
        self.edit = False
        self.pending = "typed"
        self.generation = 0

        self.summary = QLabel("")
        self.summary.setObjectName("figSummary")
        self.filter = QComboBox()
        self.filter.setObjectName("figFilter")
        self.filter.addItems(FILTERS)
        self.search = SearchBox()
        self.search.setObjectName("figSearch")
        self.search.setPlaceholderText("설명으로 검색")
        self.edit_btn = QPushButton("그림 설명 편집")
        self.edit_btn.setObjectName("figEditToggle")
        self.edit_btn.setProperty("active", False)
        self.list = CopyTree(self.copy_selected)
        self.list.setObjectName("figList")
        self.list.setColumnCount(5)
        self.list.setHeaderLabels(["그림", "설명 여부", "설명", "문서 안 위치", "원본 유형"])
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setIconSize(QPixmap(64, 48).size())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.detail = QLabel("")
        self.detail.setObjectName("figDetail")
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.status = QLabel("")
        self.status.setObjectName("figStatus")
        self.status.setWordWrap(True)
        set_tone(self.status, "muted")

        # edit groups
        self.caption = QPlainTextEdit()
        self.caption.setObjectName("captionBox")
        self.save_btn = QPushButton("설명 저장 (Ctrl+S)")
        self.save_btn.setObjectName("btnCaptionSave")
        self.del_btn = QPushButton("설명 삭제")
        self.del_btn.setObjectName("btnCaptionDelete")
        self.undo_btn = QPushButton("설명 되돌리기")
        self.undo_btn.setObjectName("btnCaptionUndo")
        g1 = QGroupBox("① 이 그림의 설명")
        g1.setObjectName("groupCaption")
        l1 = QVBoxLayout(g1)
        l1.addWidget(self.caption)
        b1 = QHBoxLayout()
        for b in (self.save_btn, self.del_btn, self.undo_btn):
            b1.addWidget(b)
        b1.addStretch(1)
        l1.addLayout(b1)
        self.to_caption = QPushButton("설명 칸에 넣기")
        self.to_caption.setObjectName("ocrToCaption")
        self.ocr = OcrBox("figureOcr", poster, worker, lambda: (self.current or {}).get("asset_file"), extra=self.to_caption)
        g2 = QGroupBox("② 그림 속 글자 (OCR)")
        g2.setObjectName("groupOcr")
        self.g2_layout = QVBoxLayout(g2)
        self.picks = QListWidget()
        self.picks.setObjectName("pickList")
        self.picks.setSelectionMode(QAbstractItemView.ExtendedSelection)
        g3 = QGroupBox("③ 주변 원문 문장 (그림에서 가까운 순) — 더블클릭하면 ① 설명 칸에 들어갑니다")
        g3.setObjectName("groupPicks")
        QVBoxLayout(g3).addWidget(self.picks)
        self.groups = (g1, g2, g3)

        bar = QHBoxLayout()
        bar.addWidget(self.filter)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.edit_btn)
        self.lower = QWidget()
        self.lower_layout = QVBoxLayout(self.lower)
        self.lower_layout.setContentsMargins(0, 0, 0, 0)
        self.lower_layout.addWidget(self.detail)
        self.lower_layout.addWidget(self.ocr)
        for g in self.groups:
            self.lower_layout.addWidget(g)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.list)
        split.addWidget(self.lower)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addLayout(bar)
        lay.addWidget(split, 1)
        lay.addWidget(self.status)
        self._apply_mode()

        self.filter.currentIndexChanged.connect(lambda _: self.fill())
        self.search.textChanged.connect(lambda _: self._search_changed())
        self.edit_btn.clicked.connect(self.toggle_edit)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.save_btn.clicked.connect(self.save_caption)
        self.del_btn.clicked.connect(lambda: self._decide_unlink())
        self.undo_btn.clicked.connect(lambda: self.send({"cmd": "decide", "action": "undo"}))
        self.picks.itemDoubleClicked.connect(lambda _: self.activate_picks())
        self.to_caption.clicked.connect(self.ocr_to_caption)

    # ── mode ────────────────────────────────────────────────────────────────
    def _apply_mode(self):
        self.detail.setVisible(not self.edit)
        for g in self.groups:
            g.setVisible(self.edit)
        self.to_caption.setVisible(self.edit)
        self.edit_btn.setProperty("active", self.edit)
        # the OCR box lives in group ② while editing
        self.ocr.setParent(None)
        if self.edit:
            self.g2_layout.addWidget(self.ocr)
        else:
            self.lower_layout.insertWidget(1, self.ocr)
        self.ocr.setVisible(True)

    def toggle_edit(self):
        self.edit = not self.edit
        self._apply_mode()
        if self.current:
            self._show_current()

    def keyPressEvent(self, e):
        if self.edit and e.matches(QKeySequence.Save):
            self.save_caption()
            e.accept()
            return
        super().keyPressEvent(e)

    # ── data ────────────────────────────────────────────────────────────────
    def reset(self):
        self.hash = None
        self.source = None
        self.generation += 1
        self.current = None
        self.links = []
        self.list.clear()
        self.detail.setText("")
        self.picks.clear()
        self.summary.setText("")
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.clear_editor()

    def clear_editor(self):
        self.caption.setPlainText("")
        self.pending = "typed"
        self.ocr.refresh()

    def open(self, path: str):
        self.reset()
        self.source = path
        self.summary.setText("그림 위치와 설명 후보를 읽는 중…" if path.lower().endswith(".hwpx")
                             else "HWP는 한컴으로 HWPX 변환본을 만든 뒤 구조를 읽습니다. 잠시 걸립니다…")
        out = os.path.join(tempfile.gettempdir(), "GovParseFigures", uuid.uuid4().hex[:8])
        self.send({"cmd": "open", "src": path, "out": out})

    def send(self, cmd: dict):
        gen = self.generation
        self.status.setText("처리 중…")
        set_tone(self.status, "muted")

        def done(r):
            if gen != self.generation:
                return
            if not r.get("ok"):
                self.status.setText(f"실패 · {r.get('error', '')}")
                set_tone(self.status, "warn")
                return
            self._load(r.get("data") or {})

        def fail(e):
            if gen == self.generation:
                self.status.setText(f"실패 · {e}")
                set_tone(self.status, "warn")

        run_bg(self.poster, lambda: self.worker.serve(cmd), done, fail)

    def _load(self, data: dict):
        if not data:
            return
        keep = occ(self.current) if self.current else None
        self.hash = data.get("source_sha256")
        self.links = list(data.get("links") or [])
        s = data.get("summary") or {}
        total = s.get("total", len(self.links))
        known = s.get("auto_explicit", 0) + s.get("user_confirmed", 0)
        located = total - s.get("location_unverified", 0)
        text = f"그림 {total}개\n설명이 확인된 그림 {known}개 · 미확인 {total - known}개\n원본에서 위치를 표시할 수 있는 그림 {located}개"
        if data.get("renderer_error"):
            text += " (미리보기가 이 문서를 열지 못함)"
        self.summary.setText(text)
        self.fill(keep)
        note = data.get("note")
        self.status.setText(note or ("저장한 설명은 같은 내용의 문서를 다시 열면 복원되고, 문서 본문에는 쓰지 않습니다." if self.edit
                                     else "그림을 고르면 원본에서 위치와 설명을 보여 줍니다."))
        set_tone(self.status, "muted")
        if self.search.text().strip():
            self._search_status()

    # ── list ────────────────────────────────────────────────────────────────
    def visible(self, link) -> bool:
        f = self.filter.currentIndex()
        if f == 1 and not unknown(link):
            return False
        if f == 2 and unknown(link):
            return False
        q = norm(self.search.text())
        return not q or (bool(link.get("caption_text")) and q in norm(link["caption_text"]))

    def fill(self, keep: str | None = None):
        if keep is None and self.current is not None:
            keep = occ(self.current)
        self.list.blockSignals(True)
        self.list.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        target = None
        for n, link in enumerate(self.links, start=1):
            if not self.visible(link):
                continue
            key = link.get("group_key", "")
            if key not in groups:
                g = QTreeWidgetItem([link.get("path_text", "")])
                g.setFlags(Qt.ItemIsEnabled)
                self.list.addTopLevelItem(g)
                g.setExpanded(True)
                groups[key] = g
            caption = (link.get("caption_text") or "").replace("\n", " ")
            it = QTreeWidgetItem([f"#{n}", STATUS.get(link.get("status"), "설명 미확인"), caption,
                                  location_text(link), link.get("source_kind") or ""])
            it.setData(0, Qt.UserRole, link)
            if unknown(link):
                it.setData(0, Qt.UserRole + 1, "warn")
                for c in range(5):
                    it.setForeground(c, QBrush(QColor(TONES["warn"])))
            if link.get("asset_file") and os.path.isfile(link["asset_file"]):
                it.setIcon(0, QIcon(QPixmap(link["asset_file"]).scaled(64, 48, Qt.KeepAspectRatio)))
            groups[key].addChild(it)
            if keep and occ(link) == keep:
                target = it
        self.list.blockSignals(False)
        if target is not None:
            self.list.setCurrentItem(target)
            self.list.scrollToItem(target)
        else:
            self.list.setCurrentItem(None)
            self.list.clearSelection()
            if keep is not None:
                self.current = None
                self.on_select(None)

    def _search_changed(self):
        self.fill()
        if self.search.text().strip():
            self._search_status()
        else:
            self.status.setText("")

    def _search_status(self):
        q = self.search.text().strip()
        n = sum(1 for l in self.links if self.visible(l))
        self.status.setText(f"‘{q}’ 검색 결과 {n}개" if n else f"‘{q}’와 일치하는 설명이 없습니다.")
        set_tone(self.status, "muted")

    def selected_links(self) -> list[dict]:
        return [i.data(0, Qt.UserRole) for i in self.list.selectedItems() if i.data(0, Qt.UserRole)]

    def item_of(self, occ_id: str):
        for g in range(self.list.topLevelItemCount()):
            grp = self.list.topLevelItem(g)
            for i in range(grp.childCount()):
                if occ(grp.child(i).data(0, Qt.UserRole)) == occ_id:
                    return grp.child(i)
        return None

    def _selection_changed(self):
        cur = self.list.currentItem()
        link = cur.data(0, Qt.UserRole) if cur is not None else None
        if link is None:
            sel = self.selected_links()
            link = sel[0] if sel else None
        if link is None:
            return
        self.current = link
        self._show_current()

    def _show_current(self):
        link = self.current
        lines = []
        if link.get("parent_item"):
            lines.append(f"속한 항목: {link['parent_item']}")
        cap = link.get("caption_text") or ""
        if link.get("status") == "user_no_caption":
            lines.append("설명: 없음 (설명 없는 그림으로 표시함)")
        elif cap:
            lines.append(f"설명: {cap}  ({'문서에 적힌 캡션' if link.get('status') == 'auto_explicit' else '직접 지정함'})")
        else:
            lines.append("설명: 문서에 이 그림의 캡션이 따로 적혀 있지 않습니다.")
        if link.get("before"):
            lines.append("그림 앞 글: " + " / ".join(link["before"]))
        if link.get("after"):
            lines.append("그림 뒤 글: " + " / ".join(link["after"]))
        if not (link.get("location") or {}).get("verified"):
            lines.append("원본 화면에서 위치를 표시하지 못했습니다. 위 ‘문서 안 위치’를 참고하세요.")
        if self.edit:
            lines.append(f"— 고급 —  판정 근거: {link.get('evidence', '')} · 그림 파일: {link.get('asset_ref', '')}")
        self.detail.setText("\n".join(lines))
        self.picks.clear()
        for p in link.get("pick_list") or []:
            it = QListWidgetItem("  " + p["text"] + PICK_KIND.get(p.get("kind"), ""))
            it.setData(Qt.UserRole, p)
            if p.get("kind") in PICK_KIND:
                it.setForeground(QBrush(QColor(TONES["muted"])))
            self.picks.addItem(it)
        self.caption.setPlainText(cap)
        self.pending = "typed"
        self.ocr.refresh()
        self.on_select(link)

    # ── copy ────────────────────────────────────────────────────────────────
    def copy_selected(self):
        if self.current is None or not self.selected_links():
            self.status.setText("먼저 목록에서 그림을 선택하세요.")
            set_tone(self.status, "warn")
            return
        ok, msg, _ = copy_picture(self.current.get("asset_file"))
        self.status.setText(msg if ok else (msg or "이 그림은 원본 파일을 꺼내지 못해 복사할 수 없습니다."))
        set_tone(self.status, "ok" if ok else "warn")

    def _context_menu(self, pos):
        it = self.list.itemAt(pos)
        if it is not None and not it.isSelected():
            self.list.setCurrentItem(it)
        m = QMenu(self)
        m.addAction("그림 복사\tCtrl+C", self.copy_selected)
        m.popup(self.list.viewport().mapToGlobal(pos))

    # ── captions ────────────────────────────────────────────────────────────
    def _ids(self) -> list[str]:
        sel = self.selected_links()
        return [occ(l) for l in sel] or ([occ(self.current)] if self.current else [])

    def _picked(self) -> list[dict]:
        return [i.data(Qt.UserRole) for i in self.picks.selectedItems()]

    def save_caption(self):
        if self.current is None:
            self.status.setText("먼저 그림을 선택하세요.")
            return
        text = self.caption.toPlainText().strip()
        if not text:
            self.status.setText("설명 칸이 비어 있습니다. 설명을 없애려면 ‘설명 삭제’를 누르세요.")
            set_tone(self.status, "warn")
            return
        picked = self._picked()
        if self.pending == "source" and picked and text == " / ".join(p["text"] for p in picked):
            action = "share" if len(self.selected_links()) > 1 else "link"
            self.send({"cmd": "decide", "action": action, "occ_ids": self._ids(), "para_refs": [p["ref"] for p in picked]})
            return
        ocr_text = " ".join(self.ocr.lines().splitlines()).strip()
        origin = "ocr" if self.pending == "ocr" and text == ocr_text else "typed"
        self.send({"cmd": "decide", "action": "text", "occ_ids": self._ids(), "text": text, "origin": origin})

    def _decide_unlink(self):
        if self.current is None:
            self.status.setText("먼저 그림을 선택하세요.")
            return
        self.send({"cmd": "decide", "action": "unlink", "occ_ids": self._ids(), "para_refs": [p["ref"] for p in self._picked()]})

    def activate_picks(self):
        picked = self._picked()
        if not picked:
            return
        self.caption.setPlainText(" / ".join(p["text"] for p in picked))
        self.pending = "source"
        self.status.setText("문장을 설명 칸에 넣었습니다. Ctrl+S 또는 ‘설명 저장’으로 저장합니다.")
        set_tone(self.status, "muted")

    def ocr_to_caption(self):
        t = self.ocr.lines()
        if not t.strip():
            self.status.setText("먼저 ‘이 그림 OCR’을 눌러 글자를 읽으세요.")
            set_tone(self.status, "warn")
            return
        self.caption.setPlainText(" ".join(t.splitlines()).strip())
        self.pending = "ocr"
        self.caption.setFocus()
        self.status.setText("OCR 글을 설명 칸에 넣었습니다. 고친 뒤 ‘설명 저장’을 누르세요.")
        set_tone(self.status, "muted")
