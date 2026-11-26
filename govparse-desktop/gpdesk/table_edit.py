"""A table candidate from the engine and a person's corrections to it (qt/SPEC.md §8).

Shared by the Windows C# app (through the worker's table-save mode) and the PySide6 GUI (imported directly),
so both write corrections by the same rules.

This is the only module that knows the table JSON format: the engine's table JSON v1.0
(engine-handoff-table-edit-and-rhwp.md; govparse.table_edit.build_native_table_candidate).

Engine candidate  tables/<key>.json          never written by the GUI
Corrections       tables/<key>.edited.json   same shape + base_sha256, per-cell "edited", "edits", "review"
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from pathlib import Path

SCHEMA = "1.0"
EDITABLE = ("ready", "review")       # ready: read from the source document; review: read from an image


class TableFormatError(ValueError):
    """The file is not a table in a format this version reads."""


class EditError(ValueError):
    """The requested change is not allowed on this table (message is shown to the user)."""


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise TableFormatError(f"표 파일을 읽지 못했습니다 ({e})") from e
    if not isinstance(data, dict) or str(data.get("schema_version")) != SCHEMA or not isinstance(data.get("cells"), list):
        raise TableFormatError("이 버전이 읽을 수 없는 표 형식입니다")
    if type(data.get("rows")) is not int or data["rows"] < 1 or not data["cells"]:
        raise TableFormatError("표에 행이나 칸이 없습니다")
    return data


CELL_KEYS = ("row", "col", "row_span", "col_span", "text", "bbox_px", "nested_table_ids", "edited")
HEAD_SKIP = ("rows", "cols", "cells", "base_sha256", "edits", "review")


def _rows(data: dict) -> list[list[dict]]:
    """v1.0 cell list -> rows of cells (merged-away positions are simply absent, as in HTML).

    Fields this module does not use (text_check, OCR details ...) ride along in "extra" and are written back.
    """
    rows: list[list[dict]] = [[] for _ in range(data["rows"])]
    try:
        for c in sorted(data["cells"], key=lambda c: (c["row"], c["col"])):
            rows[c["row"]].append({"text": str(c.get("text") or ""), "rowspan": int(c.get("row_span") or 1),
                                   "colspan": int(c.get("col_span") or 1), "bbox": c.get("bbox_px"),
                                   "nested": list(c.get("nested_table_ids") or []), "edited": bool(c.get("edited")),
                                   "extra": {k: v for k, v in c.items() if k not in CELL_KEYS}})
    except (KeyError, TypeError, IndexError, ValueError) as e:
        raise TableFormatError(f"표의 칸 정보가 올바르지 않습니다 ({e})") from e
    return rows


def _layout(rows) -> tuple[dict, int]:
    """(grid row, grid col) of each cell's top-left corner -> (row index, cell index); and the column count."""
    taken, at, width = set(), {}, 0
    for r, row in enumerate(rows):
        c = 0
        for i, cell in enumerate(row):
            while (r, c) in taken:
                c += 1
            at[(r, c)] = (r, i)
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    taken.add((r + dr, c + dc))
            c += cell["colspan"]
            width = max(width, c)
    return at, width


def _content(rows) -> list:
    return [[(c["text"], c["rowspan"], c["colspan"]) for c in row] for row in rows]


def edited_path(engine_file: Path) -> Path:
    p = Path(engine_file)
    return p.with_name(p.stem + ".edited.json")


class TableDoc:
    def __init__(self, engine_file: Path, head: dict, engine_rows, rows, base_sha: str):
        self.engine_file = Path(engine_file)
        self.head = head                   # schema_version, source_kind, table_id, asset_id, image_sha256, status
        self._engine = engine_rows
        self.rows = rows
        self._base_sha = base_sha
        self.stale_edit = False            # an edited file exists but was made for an earlier candidate
        self.confirmed = False             # a person saved this table (matters for image-read tables)
        self.structure_changed = False     # rows or columns were added or removed
        self.history: list[dict] = []      # row/column operations, in order
        self._on_disk = _content(rows)     # content the files currently hold
        self._undo: list = []

    @classmethod
    def load(cls, engine_file) -> "TableDoc":
        engine_file = Path(engine_file)
        data = _read(engine_file)
        if data.get("status") not in EDITABLE:
            raise TableFormatError("편집할 수 있는 상태의 표가 아닙니다")
        engine_rows = _rows(data)
        for row in engine_rows:
            for c in row:
                c["edited"] = False
        head = {k: v for k, v in data.items() if k not in HEAD_SKIP}   # status, image, model, warnings, ocr ...
        doc = cls(engine_file, head, engine_rows, copy.deepcopy(engine_rows), hashlib.sha256(engine_file.read_bytes()).hexdigest())
        ep = edited_path(engine_file)
        if ep.is_file():
            try:
                ed = _read(ep)
                if ed.get("base_sha256") != doc._base_sha:
                    raise TableFormatError("stale")
                doc.rows = _rows(ed)
                edits = ed.get("edits") or {}
                doc.structure_changed = bool(edits.get("structure_changed"))
                doc.history = list(edits.get("history") or [])
                doc.confirmed = bool((ed.get("review") or {}).get("confirmed_by_user"))
                doc._on_disk = _content(doc.rows)
            except TableFormatError:
                doc.stale_edit = True      # kept on disk; shown as "이전 결과에서 수정함"
        return doc

    # ── reading ──────────────────────────────────────────────────────────
    @property
    def table_id(self):
        return self.head.get("table_id")

    @property
    def status(self) -> str:
        return self.head.get("status") or "ready"

    @property
    def needs_confirm(self) -> bool:
        """Read from an image and not yet saved by a person."""
        return self.status == "review" and not self.confirmed

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return _layout(self.rows)[1]

    @property
    def has_merge(self) -> bool:
        return any(c["rowspan"] > 1 or c["colspan"] > 1 for row in self.rows for c in row)

    @property
    def can_change_structure(self) -> bool:
        return not self.has_merge

    def _cell(self, r: int, c: int) -> dict:
        at = _layout(self.rows)[0]
        if (r, c) not in at:
            raise EditError("병합된 칸의 안쪽은 고칠 수 없습니다")
        ri, ci = at[(r, c)]
        return self.rows[ri][ci]

    def anchors(self) -> list[tuple[int, int]]:
        return sorted(_layout(self.rows)[0])

    def spans(self) -> list[tuple[int, int, int, int]]:
        at = _layout(self.rows)[0]
        return [(r, c, self.rows[ri][ci]["rowspan"], self.rows[ri][ci]["colspan"]) for (r, c), (ri, ci) in sorted(at.items())
                if self.rows[ri][ci]["rowspan"] > 1 or self.rows[ri][ci]["colspan"] > 1]

    def text(self, r: int, c: int) -> str:
        return self._cell(r, c)["text"]

    def bbox(self, r: int, c: int):
        return self._cell(r, c)["bbox"]

    def nested(self, r: int, c: int) -> list[str]:
        return list(self._cell(r, c)["nested"])

    def text_check(self, r: int, c: int) -> str | None:
        """not_checked | review (OCR text, unverified) | not_detected (no text found, not a confirmed blank)."""
        cell = self._cell(r, c)
        return None if cell["edited"] else cell["extra"].get("text_check")

    @property
    def image_sha256(self) -> str | None:
        """Hash of the image the cell boxes (bbox_px) were measured on."""
        img = self.head.get("image")
        return (img or {}).get("sha256") if isinstance(img, dict) else self.head.get("image_sha256")

    @property
    def warnings(self) -> list[str]:
        return [str(w) for w in self.head.get("warnings") or []]

    def edited_cells(self) -> int:
        return sum(1 for row in self.rows for c in row if c["edited"])

    @property
    def differs_from_engine(self) -> bool:
        return _content(self.rows) != _content(self._engine)

    @property
    def dirty(self) -> bool:
        return _content(self.rows) != self._on_disk

    @property
    def can_save(self) -> bool:
        return self.dirty or self.needs_confirm

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    # ── changing ─────────────────────────────────────────────────────────
    def _push(self) -> None:
        self._undo.append((copy.deepcopy(self.rows), self.structure_changed, list(self.history)))

    def set_text(self, r: int, c: int, value: str) -> bool:
        if self._cell(r, c)["text"] == value:
            return False
        self._push()
        cell = self._cell(r, c)
        cell["text"], cell["edited"] = value, True
        return True

    def _need_plain(self) -> None:
        if self.has_merge:
            raise EditError("병합된 칸이 있는 표는 행·열을 바꿀 수 없습니다")

    @staticmethod
    def _new() -> dict:
        return {"text": "", "rowspan": 1, "colspan": 1, "bbox": None, "nested": [], "edited": True, "extra": {}}

    def _structure(self, op: str, at: int) -> None:
        self.structure_changed = True
        self.history.append({"op": op, "at": at})

    def insert_row(self, at: int) -> None:
        self._need_plain()
        self._push()
        at = max(0, min(at, self.n_rows))
        self.rows.insert(at, [self._new() for _ in range(self.n_cols)])
        self._structure("insert_row", at)

    def delete_row(self, r: int) -> None:
        self._need_plain()
        if self.n_rows <= 1:
            raise EditError("마지막 행은 지울 수 없습니다")
        self._push()
        del self.rows[r]
        self._structure("delete_row", r)

    def insert_col(self, at: int) -> None:
        self._need_plain()
        self._push()
        at = max(0, min(at, self.n_cols))
        for row in self.rows:
            row.insert(at, self._new())
        self._structure("insert_col", at)

    def delete_col(self, c: int) -> None:
        self._need_plain()
        if self.n_cols <= 1:
            raise EditError("마지막 열은 지울 수 없습니다")
        self._push()
        for row in self.rows:
            del row[c]
        self._structure("delete_col", c)

    def undo(self) -> None:
        if self._undo:
            self.rows, self.structure_changed, self.history = self._undo.pop()

    def reset(self) -> None:
        """Back to what the engine read (undoable; the file changes on save)."""
        if not self.differs_from_engine and not self.edited_cells():
            return
        self._push()
        self.rows, self.structure_changed, self.history = copy.deepcopy(self._engine), False, []

    # ── saving ───────────────────────────────────────────────────────────
    def _payload(self) -> dict:
        at = _layout(self.rows)[0]
        cells = []
        for (r, c), (ri, ci) in sorted(at.items()):
            x = self.rows[ri][ci]
            cells.append({**x.get("extra", {}), "row": r, "col": c, "row_span": x["rowspan"], "col_span": x["colspan"],
                          "text": x["text"], "bbox_px": x["bbox"], "nested_table_ids": x["nested"], "edited": x["edited"]})
        return {**self.head, "rows": self.n_rows, "cols": self.n_cols, "cells": cells, "base_sha256": self._base_sha,
                "edits": {"cells": self.edited_cells(), "structure_changed": self.structure_changed, "history": self.history},
                "review": {"confirmed_by_user": True}}

    def save(self) -> Path | None:
        """Write the corrections next to the engine file; None when there is nothing to keep.

        A table read from the source document with no difference keeps no edited file. A table read from an
        image always keeps one: saving it is the person's confirmation.
        """
        ep = edited_path(self.engine_file)
        if not self.differs_from_engine and self.status != "review":
            if ep.exists() and not self.stale_edit:
                ep.unlink()                # a stale file belongs to an earlier candidate: keep both
            for row in self.rows:
                for c in row:
                    c["edited"] = False
            self._on_disk = _content(self.rows)
            self.structure_changed, self.history = False, []
            return None
        if self.stale_edit and ep.exists():    # corrections made for an earlier candidate: keep them beside the new ones
            os.replace(ep, ep.with_name(ep.name[:-len(".edited.json")] + ".previous-edited.json"))
        tmp = ep.with_name(f".{ep.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(self._payload(), ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, ep)
        finally:
            if tmp.exists():
                tmp.unlink()
        self._on_disk = _content(self.rows)
        self.stale_edit, self.confirmed = False, True
        return ep
