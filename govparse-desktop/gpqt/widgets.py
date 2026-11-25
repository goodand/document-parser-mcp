"""Shared widgets: tone colors, page view, OCR box, copyable list, picture clipboard."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QTreeWidget,
                               QVBoxLayout, QWidget)

TONES = {"ok": "#198100", "warn": "#B25400", "muted": "#63707E", "text": "#1F2A37"}

PRIMARY = "#00646E"


def set_tone(w, tone: str) -> None:
    w.setProperty("tone", tone)
    w.setStyleSheet(f"color: {TONES.get(tone, TONES['text'])};")


class Poster(QObject):
    """Runs callables on the UI thread (results of background work)."""
    post = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.post.connect(self._dispatch, Qt.QueuedConnection)

    @Slot(object)
    def _dispatch(self, callback) -> None:
        callback()


def run_bg(poster: Poster, work, done=None, fail=None) -> threading.Thread:
    def go():
        try:
            r = work()
        except Exception as e:  # noqa: BLE001 - reported on screen
            if fail:
                poster.post.emit(lambda e=e: fail(e))
            return
        if done:
            poster.post.emit(lambda r=r: done(r))
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def copy_picture(path) -> tuple[bool, str, QSize | None]:
    """Picture to the clipboard as an image (transparent → white) and as a file."""
    if not path or not os.path.isfile(path):
        return False, "", None
    img = QImage(str(path))
    if img.isNull():
        return False, "이 그림 형식은 복사하지 못했습니다 · 읽을 수 없는 그림", None
    flat = QImage(img.size(), QImage.Format_RGB32)
    flat.fill(QColor("white"))
    p = QPainter(flat)
    p.drawImage(0, 0, img)
    p.end()
    from PySide6.QtCore import QMimeData

    mime = QMimeData()
    mime.setImageData(flat)
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    QGuiApplication.clipboard().setMimeData(mime)
    return True, f"그림을 복사했습니다 ({img.width()}×{img.height()}). 문서나 메일에 붙여 넣을 수 있습니다.", img.size()


class CopyTree(QTreeWidget):
    """Tree whose Ctrl+C / ⌘C copies the selected picture instead of the row text."""

    def __init__(self, on_copy, parent=None):
        super().__init__(parent)
        self._on_copy = on_copy

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Copy):
            self._on_copy()
            e.accept()
            return
        super().keyPressEvent(e)


class PageView(QWidget):
    """Scrollable page picture with a centre message/button and an overlay painter."""

    def __init__(self, names: tuple[str, str, str] | None = None, empty_text: str = "", action_text: str | None = None):
        super().__init__()
        self.scroll = QScrollArea()
        self.scroll.setObjectName("pageScroll")
        self.scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.scroll.setWidget(self.picture)
        self.scroll.setWidgetResizable(True)
        self.message = QLabel(empty_text)
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.action = QPushButton(action_text or "")
        self.action.setVisible(bool(action_text))
        lay = QVBoxLayout(self)
        lay.addWidget(self.scroll, 1)
        center = QVBoxLayout()
        center.addStretch(1)
        center.addWidget(self.message)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.action)
        row.addStretch(1)
        center.addLayout(row)
        center.addStretch(1)
        lay.addLayout(center)
        if names:
            self.setObjectName(names[0])
            self.message.setObjectName(names[1])
            self.action.setObjectName(names[2])
        set_tone(self.message, "muted")
        self.scroll.setVisible(False)
        self.doc = None
        self.page = 0
        self.pages = 0
        self.renderer = ""
        self.error = None
        self.busy = False
        self.gen = 0
        self.cache: dict[int, QPixmap] = {}
        self.zoom = "폭 맞춤"
        self.overlay = None          # callable(page) -> list of draw ops, or None

    def show_message(self, text: str, tone: str = "muted", action: str | None = None):
        self.message.setText(text)
        set_tone(self.message, tone)
        self.message.setVisible(True)
        self.action.setText(action or "")
        self.action.setVisible(bool(action))
        self.scroll.setVisible(False)

    def show_pixmap(self, pm: QPixmap):
        self.message.setVisible(False)
        self.action.setVisible(False)
        self.scroll.setVisible(True)
        self._raw = pm
        self.repaint_page()

    def repaint_page(self):
        pm = getattr(self, "_raw", None)
        if pm is None:
            return
        target = self._scaled_size(pm)
        out = pm.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if self.overlay:
            ops = self.overlay(self.page)
            if ops:
                out = QPixmap(out)
                p = QPainter(out)
                sx = out.width()
                for op in ops:
                    if op[0] == "notice":
                        flags = Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap
                        box = p.boundingRect(QRectF(16, 12, sx - 32, 400), flags, op[1])
                        p.fillRect(QRectF(8, 8, sx - 16, box.height() + 8), QColor("#FFF7EB"))
                        p.setPen(QColor(TONES["warn"]))
                        p.drawText(QRectF(16, 12, sx - 32, box.height()), flags, op[1])
                    else:
                        _, (x, y, w, h), (pw, ph), label, color, dashed = op
                        k = out.width() / pw if pw else 1
                        pen = QPen(QColor(color), 3 if not dashed else 2)
                        if dashed:
                            pen.setStyle(Qt.DashLine)
                        p.setPen(pen)
                        r = QRectF(x * k, y * k, w * k, h * k)
                        p.drawRect(r)
                        p.fillRect(QRectF(r.x(), r.y() - 18, 36, 18), QColor(color))
                        p.setPen(QColor("white"))
                        p.drawText(QRectF(r.x(), r.y() - 18, 36, 18), Qt.AlignCenter, label)
                p.end()
        self.picture.setPixmap(out)

    def _scaled_size(self, pm: QPixmap) -> QSize:
        vw = max(240, self.scroll.viewport().width() - 32)
        vh = max(240, self.scroll.viewport().height() - 16)
        if self.zoom == "폭 맞춤":
            w = min(1100, vw)
            return QSize(w, int(pm.height() * w / max(1, pm.width())))
        if self.zoom == "쪽 맞춤":
            return pm.size().scaled(QSize(vw, vh), Qt.KeepAspectRatio)
        pct = int(self.zoom.rstrip("%")) / 100
        return QSize(int(pm.width() / 1.1 * pct), int(pm.height() / 1.1 * pct))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.repaint_page()


class OcrBox(QWidget):
    """이 그림 OCR / 텍스트 복사 / state line / text, for the picture returned by file_of()."""

    def __init__(self, name: str, poster: Poster, worker, file_of, extra: QPushButton | None = None):
        super().__init__()
        self.setObjectName(name)
        self.poster, self.worker, self.file_of = poster, worker, file_of
        self.run_btn = QPushButton("이 그림 OCR")
        self.run_btn.setObjectName("ocrRun")
        self.copy_btn = QPushButton("텍스트 복사")
        self.copy_btn.setObjectName("ocrCopy")
        self.state = QLabel("OCR 미실행")
        self.state.setObjectName("ocrState")
        self.text = QPlainTextEdit()
        self.text.setObjectName("ocrText")
        self.text.setReadOnly(True)
        self.text.setAccessibleName("OCR 결과 텍스트")
        self.text.setMaximumHeight(150)
        bar = QHBoxLayout()
        bar.addWidget(self.run_btn)
        bar.addWidget(self.copy_btn)
        if extra is not None:
            bar.addWidget(extra)
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.state)
        lay.addWidget(self.text)
        set_tone(self.state, "muted")
        self.cache: dict[str, list[str]] = {}
        self.ticket = 0
        self.run_btn.clicked.connect(self.run)
        self.copy_btn.clicked.connect(self.copy)
        self.refresh()

    def lines(self) -> str:
        return self.text.toPlainText()

    def refresh(self, known: list[str] | None = None):
        self.ticket += 1
        path = self.file_of()
        exists = bool(path) and os.path.isfile(path)
        self.run_btn.setEnabled(exists)
        if known is None and path in self.cache:
            known = self.cache[path]
        if known is not None:
            self._show(known)
            return
        self.text.setPlainText("")
        self.copy_btn.setEnabled(False)
        self.state.setText("OCR 미실행" if exists or not path else "선택한 그림의 파일이 없습니다")
        if path and not exists:
            self.state.setText("선택한 그림의 파일이 없습니다")
        set_tone(self.state, "muted")

    def _show(self, lines: list[str], suffix: str = ""):
        self.text.setPlainText("\n".join(lines))
        self.copy_btn.setEnabled(bool(lines))
        self.state.setText((f"OCR 결과 {len(lines)}줄 (그림 안 글자 · 설명 아님)" if lines else "OCR 결과 없음 (빈 결과)") + suffix)
        set_tone(self.state, "muted")

    def run(self):
        path = self.file_of()
        if not path:
            return
        self.ticket += 1
        ticket = self.ticket
        self.run_btn.setEnabled(False)
        self.state.setText("OCR 실행 중…")
        set_tone(self.state, "muted")

        def done(r):
            status, texts = r.get("status"), [t for t in r.get("texts") or [] if t and t.strip()]
            if status in ("ok", "empty"):
                self.cache[path] = texts
            if ticket != self.ticket:
                return
            self.run_btn.setEnabled(True)
            if status in ("ok", "empty"):
                self._show(texts, f" · {r['engine_label']}" if r.get("engine_label") else "")
            elif status == "not_available":
                self.state.setText(f"OCR 사용 불가 · {r.get('error', '')}")
                set_tone(self.state, "warn")
            else:
                self.state.setText(f"OCR 실패 · {r.get('error', '')}")
                set_tone(self.state, "warn")

        def fail(e):
            if ticket == self.ticket:
                self.run_btn.setEnabled(True)
                self.state.setText(f"OCR 실패 · {e}")
                set_tone(self.state, "warn")

        run_bg(self.poster, lambda: self.worker.run("ocr", path), done, fail)

    def copy(self):
        t = self.text.toPlainText()
        if not t:
            return
        QGuiApplication.clipboard().setText(t)
        self.state.setText(f"클립보드에 복사했습니다 ({len(t)}자)")
        set_tone(self.state, "ok")
