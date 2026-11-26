"""표 고치기 모드 (SPEC.md §8): table list on top, an editable grid below."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
                               QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .table_edit import EDITABLE, EditError, TableDoc, TableFormatError
from .widgets import set_tone

ACTIONS = [("row_above", "위에 행 추가"), ("row_below", "아래에 행 추가"), ("row_delete", "이 행 삭제"),
           ("col_left", "왼쪽에 열 추가"), ("col_right", "오른쪽에 열 추가"), ("col_delete", "이 열 삭제"),
           ("nested_open", "안쪽 표 열기")]
NOT_EDITABLE = {"not_checked": "검사 전", "no_grid": "표 구조 확인 못함", "failed": "검사 실패"}
TEXT_CHECK = {"review": "그림에서 읽은 글자 · 원본과 확인 필요",
              "not_detected": "글자를 찾지 못함 · 빈 칸으로 확인된 것은 아님"}
CHECK_BG = QColor("#FFF7EB")         # theme WARN_BG: cells whose text a person has not looked at yet


def image_candidate_path(png) -> Path:
    """tables/<png stem>.image.json in the same result folder (assets/tables/<png> -> tables/)."""
    p = Path(png)
    return p.parents[2] / "tables" / f"{p.stem}.image.json"


class TableEditPanel(QWidget):
    def __init__(self, say, show_image, read_image=None):
        super().__init__()
        self.setObjectName("tableEditPanel")
        self.say, self.show_image = say, show_image
        self.read_image = read_image          # (png, out, with_ocr, table_id, done, fail) -> None, runs in background
        self.reading = None                   # entry being read from its image
        self._sha: dict[str, str] = {}
        self.can_read_images = True
        self.entries: list[dict] = []      # {asset, label, doc | None, reason}
        self._filling = False
        self.list = QTreeWidget()
        self.list.setObjectName("tblList")
        self.list.setColumnCount(3)
        self.list.setHeaderLabels(["표", "상태", "크기"])
        self.list.setRootIsDecorated(False)
        self.list.setMaximumHeight(104)
        for c, w in enumerate((170, 200, 70)):
            self.list.setColumnWidth(c, w)
        self.state = QLabel("")
        self.state.setObjectName("tblState")
        self.message = QLabel("")
        self.message.setObjectName("tblMessage")
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignCenter)
        set_tone(self.message, "warn")
        self.grid = QTableWidget()
        self.grid.setObjectName("tblGrid")
        self.grid.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.grid.setWordWrap(True)
        self.grid.setMinimumHeight(220)
        self.grid.horizontalHeader().setStretchLastSection(True)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        hint = QLabel("칸을 더블클릭해 고치고, 행·열은 오른쪽 클릭으로 추가·삭제합니다.")
        set_tone(hint, "muted")
        hint.setWordWrap(True)
        self.read_btn = QPushButton("이미지에서 표 읽기")
        self.read_btn.setObjectName("tblRead")
        self.read_ocr = QCheckBox("글자도 읽기 (OCR)")
        self.read_ocr.setObjectName("tblReadOcr")
        self.read_ocr.setChecked(True)
        read_row = QHBoxLayout()
        read_row.addWidget(self.read_btn)
        read_row.addWidget(self.read_ocr)
        read_row.addStretch(1)
        self.undo_btn = QPushButton("되돌리기")
        self.undo_btn.setObjectName("tblUndo")
        self.reset_btn = QPushButton("처음 읽은 표로")
        self.reset_btn.setObjectName("tblReset")
        self.save_btn = QPushButton("저장")
        self.save_btn.setObjectName("tblSave")
        theme.kind(self.save_btn, "primary")
        buttons = QHBoxLayout()
        buttons.addWidget(self.undo_btn)
        buttons.addWidget(self.reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (self.list, self.state):
            lay.addWidget(w)
        lay.addLayout(read_row)
        lay.addWidget(hint)
        lay.addWidget(self.grid, 1)
        lay.addWidget(self.message, 1)
        lay.addLayout(buttons)
        self.message.setVisible(False)
        self.list.currentItemChanged.connect(lambda *_: self._show())
        self.grid.itemChanged.connect(self._cell_changed)
        self.undo_btn.clicked.connect(self._undo)
        self.reset_btn.clicked.connect(self._reset)
        self.save_btn.clicked.connect(self._save)
        self.read_btn.clicked.connect(self.read_current)
        self.grid.currentCellChanged.connect(lambda *_: self._highlight())

    # ── data ─────────────────────────────────────────────────────────────
    def load(self, assets, can_read_images: bool = True) -> int:
        """Table-image assets of a result; returns how many can be edited."""
        self.entries = []
        seen = set()
        self.reading = None
        self.can_read_images = can_read_images
        for a in assets:
            if a.get("origin") not in ("table_image", "table_source_page") or not a.get("table_id") or a.get("table_id") in seen:
                continue
            seen.add(a.get("table_id"))     # one entry per table (a long table has several image pieces)
            tj = a.get("table_json") if isinstance(a.get("table_json"), dict) else {}
            entry = {"asset": a, "label": a.get("label") or a.get("table_id") or "", "doc": None,
                     "status": tj.get("status") or "not_checked", "reason": tj.get("reason") or ""}
            if entry["status"] in EDITABLE and tj.get("file"):
                self._open(entry, tj["file"])
            elif entry["status"] not in NOT_EDITABLE:
                entry["status"] = "failed"
            if entry["doc"] is None and a.get("file"):     # no table from the source cells: one read from the image?
                img = image_candidate_path(a["file"])
                if img.is_file():
                    self._open(entry, img)
            self.entries.append(entry)
        self._fill_list()
        return self.editable()

    @staticmethod
    def _open(entry, file) -> None:
        try:
            entry["doc"] = TableDoc.load(file)
            entry["status"], entry["reason"] = entry["doc"].status, ""
        except TableFormatError as e:
            entry["doc"] = None
            data_status = None
            try:
                import json

                data_status = json.loads(Path(file).read_text("utf-8")).get("status")
            except (OSError, ValueError):
                pass
            entry["status"] = data_status if data_status in ("no_grid",) else "failed"
            entry["reason"] = "이미지에서 표 격자를 찾지 못했습니다" if data_status == "no_grid" else str(e)

    def clear(self) -> None:
        self.load([])

    def editable(self) -> int:
        return sum(1 for e in self.entries if e["doc"] is not None)

    def unsaved(self) -> int:
        return sum(1 for e in self.entries if e["doc"] is not None and e["doc"].dirty)

    @staticmethod
    def _state_of(e) -> str:
        d = e["doc"]
        if d is None:
            return NOT_EDITABLE.get(e["status"], "검사 실패") + (f" · {e['reason']}" if e["reason"] else "")
        n = d.edited_cells()
        tail = " · 행·열 바뀜" if d.structure_changed else ""
        if d.dirty:
            return f"저장 안 됨 · {n}칸{tail}" if n or d.structure_changed else "저장 안 됨 · 처음 읽은 표로 되돌림"
        if d.differs_from_engine:
            return f"사람이 고침 · {n}칸{tail}"
        if d.status == "review":           # read from an image: a person's check is a separate fact
            return "사람이 확인함" if d.confirmed else "이미지에서 읽음 · 확인 필요"
        return "원본 표에서 읽음" + (" · 이전 결과에서 수정함" if d.stale_edit else "")

    def _fill_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for e in self.entries:
            it = QTreeWidgetItem([e["label"], "", ""])
            it.setData(0, Qt.UserRole, e["asset"])
            self.list.addTopLevelItem(it)
        self.list.blockSignals(False)
        self._refresh_states()
        if self.entries:
            self.list.setCurrentItem(self.list.topLevelItem(0))
        self._show()

    def _refresh_states(self) -> None:
        for i, e in enumerate(self.entries):
            it = self.list.topLevelItem(i)
            it.setText(1, self._state_of(e))
            it.setText(2, f"{e['doc'].n_rows}행 {e['doc'].n_cols}열" if e["doc"] is not None else "")
        e = self._entry()
        d = e["doc"] if e else None
        self.state.setText(self._state_of(e) if e else "")
        set_tone(self.state, "warn" if d is None or d.dirty else "ok" if d.differs_from_engine else "muted")
        self.undo_btn.setEnabled(bool(d and d.can_undo))
        self.reset_btn.setEnabled(bool(d and (d.differs_from_engine or d.edited_cells())))
        self.save_btn.setEnabled(bool(d and d.can_save))
        self.save_btn.setText("확인 후 저장" if d is not None and d.status == "review" else "저장")
        can, why = self.can_read(e)
        self.read_btn.setVisible(self.can_read_images and e is not None and not (d is not None and d.status == "ready"))
        self.read_ocr.setVisible(self.read_btn.isVisible())
        self.read_btn.setEnabled(can)
        self.read_btn.setToolTip(why)
        self.read_btn.setText("이미지에서 다시 읽기" if d is not None and d.status == "review" else "이미지에서 표 읽기")
        if self.reading is not None:
            self.read_btn.setText("표 읽는 중…")

    def can_read(self, e) -> tuple[bool, str]:
        """Reading from the image is offered for tables without source cells; never over a source-cell table."""
        if e is None or self.read_image is None:
            return False, ""
        if e["doc"] is not None and e["doc"].status == "ready":
            return False, "원본 문서에서 칸을 읽은 표는 그 결과를 씁니다"
        if e["asset"].get("origin") == "table_source_page":
            return False, "이 그림은 원본 쪽 전체라서 표 하나만 읽을 수 없습니다"
        if self.reading is not None:
            return False, "다른 표를 읽는 중입니다"
        if (e["asset"].get("parts") or 1) > 1:
            return False, "여러 쪽에 걸친 표는 조각마다 따로 읽어야 해서 아직 지원하지 않습니다"
        if e["doc"] is not None and e["doc"].dirty:
            return False, "저장하지 않은 수정이 있습니다 · 저장하거나 되돌린 뒤 다시 읽으세요"
        return True, "표 그림에서 행·열과 병합을 추정합니다. 결과는 원본과 대조해 확인해야 합니다"

    def read_current(self) -> None:
        e = self._entry()
        ok, why = self.can_read(e)
        if not ok:
            if why:
                self.say(why, "warn")
            return
        png = e["asset"]["file"]
        out = image_candidate_path(png)
        self.reading = e
        self._refresh_states()
        self.say(f"표 그림을 읽는 중 · {e['label']}", "muted")

        def done(r):
            self.reading = None
            if e not in self.entries:                 # another result was opened meanwhile
                return
            self._open(e, r.get("file") or out)
            if e["doc"] is not None:
                n = r.get("ocr_cells")
                self.say(f"표 그림을 읽었습니다 · {e['label']} · {e['doc'].n_rows}행 {e['doc'].n_cols}열"
                         + (f" · 글자 읽은 칸 {n}개" if n else " · 글자는 읽지 않음") + " · 원본과 대조해 확인하세요", "ok")
            else:
                self.say(f"표 그림에서 격자를 찾지 못했습니다 · {e['label']} · 표 이미지는 그대로 볼 수 있습니다", "warn")
            if self._entry() is e:
                self._show()
            else:
                self._refresh_states()

        def fail(err):
            self.reading = None
            if e in self.entries:
                self.say(f"표 그림을 읽지 못했습니다 · {e['label']} · {err}", "warn")
                self._refresh_states()

        self.read_image(png, str(out), self.read_ocr.isChecked(), e["asset"].get("table_id"), done, fail)

    def _image_sha(self, file) -> str | None:
        if not file:
            return None
        if file not in self._sha:
            try:
                self._sha[file] = hashlib.sha256(Path(file).read_bytes()).hexdigest()
            except OSError:
                return None
        return self._sha[file]

    def selected_box(self):
        """Pixel box of the selected cell on the shown PNG, only when the boxes were measured on that very image."""
        e = self._entry()
        d = e["doc"] if e else None
        if d is None or self.grid.currentRow() < 0 or not d.image_sha256:
            return None
        if d.image_sha256 != self._image_sha(e["asset"].get("file")):
            return None
        try:
            box = d.bbox(*self._at())
        except EditError:
            return None
        return list(box) if box else None

    def _highlight(self) -> None:
        e = self._entry()
        if e is not None and not self._filling:
            self.show_image(e["asset"].get("file"), self.selected_box())

    def _entry(self):
        i = self.list.indexOfTopLevelItem(self.list.currentItem()) if self.list.currentItem() is not None else -1
        return self.entries[i] if 0 <= i < len(self.entries) else None

    # ── grid ─────────────────────────────────────────────────────────────
    def _show(self, keep=None) -> None:
        e = self._entry()
        d = e["doc"] if e else None
        self.grid.setVisible(d is not None)
        self.message.setVisible(d is None)
        if e:
            self.show_image(e["asset"].get("file"))
        if d is None:
            self.message.setText(self._state_of(e) + "\n표 이미지는 그대로 볼 수 있습니다." if e else "고칠 표가 없습니다")
            self.grid.setRowCount(0)
            self._refresh_states()
            return
        self._filling = True
        self.grid.clearSpans()
        self.grid.clear()
        self.grid.setRowCount(d.n_rows)
        self.grid.setColumnCount(d.n_cols)
        for r, c in d.anchors():
            item = QTableWidgetItem(d.text(r, c))
            tips = []
            if d.nested(r, c):
                tips.append("이 칸 안에 표가 있습니다 · 오른쪽 클릭 › 안쪽 표 열기")
            check = d.text_check(r, c)
            if check in TEXT_CHECK and not d.confirmed:
                tips.append(TEXT_CHECK[check])
                item.setBackground(QBrush(CHECK_BG))
            item.setData(Qt.UserRole, check)
            if tips:
                item.setToolTip("\n".join(tips))
            self.grid.setItem(r, c, item)
        for r, c, rs, cs in d.spans():
            self.grid.setSpan(r, c, rs, cs)
        self.grid.resizeColumnsToContents()
        self.grid.resizeRowsToContents()
        self._filling = False
        if keep is not None:
            self.grid.setCurrentCell(min(keep[0], d.n_rows - 1), min(keep[1], d.n_cols - 1))
        self._refresh_states()
        self._highlight()

    def _cell_changed(self, item) -> None:
        e = self._entry()
        if self._filling or e is None or e["doc"] is None:
            return
        e["doc"].set_text(item.row(), item.column(), item.text())
        self._refresh_states()

    def _at(self) -> tuple[int, int]:
        return max(self.grid.currentRow(), 0), max(self.grid.currentColumn(), 0)

    def context_actions(self) -> list[tuple[str, str, bool]]:
        e = self._entry()
        d = e["doc"] if e else None
        ok = bool(d and d.can_change_structure)
        out = [(a, label, ok and not (a == "row_delete" and d.n_rows <= 1) and not (a == "col_delete" and d.n_cols <= 1))
               for a, label in ACTIONS[:6]]
        return out + [("nested_open", "안쪽 표 열기", self._nested_row() is not None)]

    def _nested_row(self):
        """List row of the table inside the current cell, if it is in this result."""
        e = self._entry()
        d = e["doc"] if e else None
        if d is None or self.grid.currentRow() < 0:
            return None
        try:
            ids = d.nested(*self._at())
        except EditError:
            return None
        for i, other in enumerate(self.entries):
            if other["asset"].get("table_id") in ids:
                return i
        return None

    def do(self, action: str) -> None:
        e = self._entry()
        d = e["doc"] if e else None
        if d is None:
            return
        if action == "nested_open":
            i = self._nested_row()
            if i is not None:
                self.list.setCurrentItem(self.list.topLevelItem(i))
            return
        r, c = self._at()
        try:
            {"row_above": lambda: d.insert_row(r), "row_below": lambda: d.insert_row(r + 1), "row_delete": lambda: d.delete_row(r),
             "col_left": lambda: d.insert_col(c), "col_right": lambda: d.insert_col(c + 1), "col_delete": lambda: d.delete_col(c)}[action]()
        except EditError as err:
            self.say(str(err), "warn")
            return
        self._show(keep=(r, c))

    def _menu(self, pos) -> None:
        idx = self.grid.indexAt(pos)
        if idx.isValid():
            self.grid.setCurrentCell(idx.row(), idx.column())
        m = QMenu(self)
        for i, (a, label, on) in enumerate(self.context_actions()):
            if i in (3, 6):
                m.addSeparator()
            act = m.addAction(label)
            act.setEnabled(on)
            act.triggered.connect(lambda _=False, a=a: self.do(a))
        m.popup(self.grid.viewport().mapToGlobal(pos))

    # ── buttons ──────────────────────────────────────────────────────────
    def _undo(self) -> None:
        e = self._entry()
        if e and e["doc"]:
            e["doc"].undo()
            self._show(keep=self._at())

    def _reset(self) -> None:
        e = self._entry()
        if e and e["doc"]:
            e["doc"].reset()
            self._show(keep=self._at())

    def _save(self) -> None:
        e = self._entry()
        d = e["doc"] if e else None
        if d is None:
            return
        try:
            out = d.save()
        except OSError as err:
            self.say(f"표를 저장하지 못했습니다 · {err}", "warn")
            return
        if out is None:
            self.say(f"원본에서 읽은 표로 되돌렸습니다 · {e['label']}", "ok")
        else:
            self.say(f"표를 저장했습니다 · {e['label']} · 고친 칸 {d.edited_cells()}개 · {out.name}", "ok")
        self._show(keep=self._at())
