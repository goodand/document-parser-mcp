"""Main window (SPEC.md). Behavior follows the Windows app (Desktop.cs) except SPEC.md §7."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPixmap, QImage
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMenu, QProgressBar,
                               QPushButton, QSplitter, QStackedWidget, QTextBrowser, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from . import bridge as bridge_mod
from .figures import FigurePanel, SearchBox, norm
from .paths import base_name, same_path, unique
from .platform import CurrentPlatform
from .settings_dialog import SettingsDialog
from .status import ago, progress_text
from .widgets import TONES, CopyTree, OcrBox, PageView, Poster, copy_picture, run_bg, set_tone
from .worker import temp_png

APP = Path(__file__).resolve().parents[1]
ZOOMS = ["폭 맞춤", "쪽 맞춤", "100%", "150%", "200%"]
FORMATS = [("json", "JSON + PNG", "데이터와 표 이미지"), ("html", "HTML", "브라우저에서 열기"),
           ("hwpx", "한글 문서 · HWPX (.hwpx)", "편집 가능한 HWPX — 본문·표·그림 복원"),
           ("hwp", "한글 문서 · HWP (.hwp)", "한컴에서 HWP로 저장하고 다시 열어 확인")]
NO_RESULT = "아직 결과가 없습니다. 내보내기를 하거나 ‘결과 불러오기’로 기존 결과를 여세요."


class QtDialogs:
    def ask_open_file(self, parent):
        f, _ = QFileDialog.getOpenFileName(parent, "한글 문서 열기", "", "한글 문서 (*.hwp *.hwpx)")
        return f or None

    def ask_load_result(self, parent):
        f, _ = QFileDialog.getOpenFileName(parent, "기존 결과 불러오기", "",
                                           "GovParse 결과 (document.json *.html *.hwpx *.hwp) (*.json *.html *.htm *.hwpx *.hwp)")
        return f or None

    def ask_save_target(self, parent, mode, default_name, start_dir):
        title = "한글 문서로 저장" if mode in ("hwpx", "hwp") else "결과 폴더 이름과 위치 (이 이름의 새 폴더가 만들어집니다)"
        f, _ = QFileDialog.getSaveFileName(parent, title, os.path.join(start_dir or "", default_name))
        return f or None


class ResultView(QWidget):
    """Result tab's left side; text() is the placeholder while there is no result."""

    def __init__(self):
        super().__init__()
        self.setObjectName("resultView")
        self.placeholder = QLabel(NO_RESULT)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        set_tone(self.placeholder, "muted")
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.placeholder)
        lay.addWidget(self.stack, 1)
        self.stack.setVisible(False)

    def text(self) -> str:
        return self.placeholder.text() if self.placeholder.isVisibleTo(self) or not self.stack.isVisibleTo(self) else ""


class MainWindow(QMainWindow):
    def __init__(self, worker=None, dialogs=None, platform=None):
        super().__init__()
        if worker is None:
            from .worker import WorkerClient

            worker = WorkerClient()
        self.worker = worker
        self.dialogs = dialogs or QtDialogs()
        self.platform = platform or CurrentPlatform()
        # Keep the queued callback receiver in the window's QObject lifetime so
        # late worker completions are discarded when the window closes.
        self.poster = Poster(self)
        self.worker_shutdown_called = False
        self.setWindowTitle("GovParse Desktop")
        self.setMinimumSize(560, 420)
        self.resize(1180, 820)
        self.setAcceptDrops(True)

        self.source: str | None = None
        self.job = 0
        self.showing_result = False
        self.figures_open = False
        self.mode = "json"
        self.last_dir = None
        self.status_kind = "auto"
        self.struct_start = None
        self.struct_job = None
        self.preview_start = None
        self.export_start = None
        self.export_step = ""
        self.export_event: dict | None = None
        self.stage_start = None
        self.cancel_event: threading.Event | None = None
        self.result_path: str | None = None
        self.result_kind: str | None = None
        self.result_json: str | None = None
        self.result_gen = 0
        self.preview_mode = "natural"
        self._preview_src_size = QSize()

        self._build()
        self.say("Ctrl+O 또는 끌어다 놓기로 HWP/HWPX 문서를 여세요.", "muted", "auto")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self._start_bridge()
        self.refresh()

    # ── layout ─────────────────────────────────────────────────────────────
    def _btn(self, text, name):
        b = QPushButton(text)
        b.setObjectName(name)
        return b

    def _build(self):
        root = QWidget()
        lay = QVBoxLayout(root)
        top = QHBoxLayout()
        brand = QLabel("GovParse Desktop")
        brand.setStyleSheet(f"font-weight: bold; color: {TONES['text']};")
        top.addWidget(brand)
        self.tab_orig = self._btn("원본 보기", "tabOriginal")
        self.tab_result = self._btn("결과 보기", "tabResult")
        self.tab_result.setEnabled(False)
        self.btn_open = self._btn("파일 열기", "btnOpen")
        self.btn_load = self._btn("결과 불러오기", "btnLoadResult")
        self.btn_settings = self._btn("설정", "btnSettings")
        self.btn_manual = self._btn("설명서", "btnManual")
        self.lbl_file = QLabel("")
        self.lbl_file.setObjectName("lblFileName")
        set_tone(self.lbl_file, "muted")
        for w in (self.tab_orig, self.tab_result, self.btn_open, self.btn_load, self.btn_settings, self.btn_manual, self.lbl_file):
            top.addWidget(w)
        top.addStretch(1)
        lay.addLayout(top)

        tools = QHBoxLayout()
        self.btn_prev = self._btn("이전", "btnPrev")
        self.lbl_page = QLabel("– / –")
        self.lbl_page.setObjectName("lblPage")
        self.btn_next = self._btn("다음", "btnNext")
        self.btn_zoom = self._btn("폭 맞춤 ▾", "btnZoom")
        self.btn_figures = self._btn("그림 목록", "btnFigures")
        for w in (self.btn_prev, self.lbl_page, self.btn_next, self.btn_zoom, self.btn_figures):
            tools.addWidget(w)
        tools.addStretch(1)
        lay.addLayout(tools)

        strip = QHBoxLayout()
        self.st_struct = QLabel("구조: 문서를 열면 자동으로 읽습니다")
        self.st_struct.setObjectName("stStructure")
        self.st_preview = QLabel("")
        self.st_preview.setObjectName("stPreview")
        self.st_export = QLabel("")
        self.st_export.setObjectName("stExport")
        self.busy = QProgressBar()
        self.busy.setObjectName("busyBar")
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.setVisible(False)
        for w in (self.st_struct, self.st_preview, self.st_export):
            set_tone(w, "muted")
            strip.addWidget(w)
        strip.addStretch(1)
        strip.addWidget(self.busy)
        lay.addLayout(strip)

        # original tab
        self.orig = PageView(("origView", "origMessage", "origAction"), "HWP 또는 HWPX 파일을 여기에 놓으세요", "파일 열기")
        self.orig.overlay = self._overlay_ops
        self.figures = FigurePanel(self.poster, self.worker, self._figure_selected)
        self.orig_split = QSplitter(Qt.Horizontal)
        self.orig_split.addWidget(self.orig)
        self.orig_split.addWidget(self.figures)
        self.figures.setVisible(False)

        # result tab
        self.result_view = ResultView()
        self.asset_preview = QLabel()
        self.asset_preview.setObjectName("assetPreview")
        self.asset_preview.setAlignment(Qt.AlignCenter)
        self.result_web = QTextBrowser()
        self.result_web.setObjectName("resultWeb")
        self.result_pages = PageView(None, "", None)
        self.result_pages.setObjectName("resultPages")
        for w in (self.asset_preview, self.result_web, self.result_pages):
            self.result_view.stack.addWidget(w)
        side = QWidget()
        sl = QVBoxLayout(side)
        self.res_title = QLabel("")
        self.res_title.setObjectName("resTitle")
        self.res_title.setStyleSheet("font-weight: bold;")
        self.res_state = QLabel("")
        self.res_state.setObjectName("resState")
        self.res_notes = QLabel("")
        self.res_notes.setObjectName("resNotes")
        self.res_notes.setWordWrap(True)
        self.asset_search = SearchBox()
        self.asset_search.setObjectName("assetSearch")
        self.asset_search.setPlaceholderText("설명으로 검색")
        self.assets = CopyTree(self.copy_selected_asset)
        self.assets.setObjectName("assetList")
        self.assets.setColumnCount(4)
        self.assets.setHeaderLabels(["자산", "출처", "위치 / 설명", "원본 파일"])
        self.assets.setRootIsDecorated(False)
        self.assets.setContextMenuPolicy(Qt.CustomContextMenu)
        self.assets.customContextMenuRequested.connect(
            lambda pos: self.asset_context_menu(self.assets.itemAt(pos)).popup(self.assets.viewport().mapToGlobal(pos)))
        self.result_ocr = OcrBox("resultOcr", self.poster, self.worker, self._asset_file)
        self.btn_external = self._btn("JSON 열기", "btnOpenExternal")
        self.btn_folder = self._btn("폴더 열기", "btnOpenFolder")
        self.btn_zip = self._btn("압축 파일 보기", "btnOpenZip")
        self.btn_to_html = self._btn("HTML로 변환", "btnToHtml")
        self.btn_to_hwpx = self._btn("HWPX로 변환", "btnToHwpx")
        actions = QHBoxLayout()
        for b in (self.btn_external, self.btn_folder, self.btn_zip, self.btn_to_html, self.btn_to_hwpx):
            actions.addWidget(b)
        for w in (self.res_title, self.res_state, self.res_notes, self.asset_search, self.assets, self.result_ocr):
            sl.addWidget(w)
        sl.addLayout(actions)
        self.result_split = QSplitter(Qt.Horizontal)
        self.result_split.addWidget(self.result_view)
        self.result_split.addWidget(side)
        for w in (self.asset_search, self.assets, self.result_ocr, self.btn_zip, self.btn_to_html, self.btn_to_hwpx):
            w.setVisible(False)

        self.main = QStackedWidget()
        self.main.addWidget(self.orig_split)
        self.main.addWidget(self.result_split)
        lay.addWidget(self.main, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("내보낼 형식"))
        self.btn_format = self._btn("JSON + PNG ▾", "btnFormat")
        self.btn_export = self._btn("내보내기", "btnExport")
        self.btn_cancel = self._btn("취소 (Esc)", "btnCancel")
        self.btn_cancel.setVisible(False)
        for w in (self.btn_format, self.btn_export, self.btn_cancel):
            bottom.addWidget(w)
        bottom.addStretch(1)
        lay.addLayout(bottom)
        self.status = QLabel("")
        self.status.setObjectName("statusText")
        lay.addWidget(self.status)
        self.setCentralWidget(root)

        self.tab_orig.clicked.connect(lambda: self.switch_tab(False))
        self.tab_result.clicked.connect(lambda: self.switch_tab(True))
        self.btn_open.clicked.connect(self.pick)
        self.orig.action.clicked.connect(lambda: self.pick() if self.source is None else self._show_page(self.orig, self.orig.page))
        self.result_pages.action.clicked.connect(lambda: self._show_page(self.result_pages, self.result_pages.page))
        self.btn_load.clicked.connect(self._load_result_dialog)
        self.btn_settings.clicked.connect(self._open_settings)
        self.btn_manual.clicked.connect(lambda: self.platform.open_default(APP / "manual.html"))
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.btn_zoom.clicked.connect(self._zoom_menu)
        self.btn_figures.clicked.connect(self.toggle_figures)
        self.btn_format.clicked.connect(self._format_menu)
        self.btn_export.clicked.connect(self.export)
        self.btn_cancel.clicked.connect(self.cancel_export)
        self.assets.currentItemChanged.connect(lambda *_: self._select_asset())
        self.asset_search.textChanged.connect(self._filter_assets)
        self.btn_external.clicked.connect(self._open_external)
        self.btn_folder.clicked.connect(lambda: self.result_path and self.platform.reveal(self._result_file()))
        self.btn_zip.clicked.connect(lambda: self._zip_of() and self.platform.reveal(self._zip_of()))
        self.btn_to_html.clicked.connect(lambda: self.convert("html"))
        self.btn_to_hwpx.clicked.connect(lambda: self.convert("hwpx"))
        act = QAction(self)
        act.setShortcut(QKeySequence.Open)
        act.triggered.connect(self.pick)
        self.addAction(act)
        self._set_active_tab()

    # ── status ─────────────────────────────────────────────────────────────
    def say(self, text: str, tone: str = "muted", kind: str = "event"):
        self.status.setText(text)
        set_tone(self.status, tone)
        self.status_kind = kind

    def _page_line(self):
        v = self.orig
        if self.showing_result or self.source is None or v.pages <= 0 or v.error or self.export_start is not None:
            return None
        return f"원본 미리보기 · {v.page + 1} / {v.pages}쪽" + (" · 한컴 PDF로 표시" if v.renderer == "hancom-pdf" else "")

    def tick(self):
        self.refresh()

    def refresh(self):
        line = self._page_line()
        if line and self.status_kind == "auto":
            self.say(line, "muted", "auto")
        # status strip
        if self.struct_start is not None:
            self.st_struct.setText(f"구조 읽는 중 · {ago(time.time() - self.struct_start)} 경과")
            set_tone(self.st_struct, "text")
        if self.orig.busy and self.preview_start is not None:
            self.st_preview.setText(f"미리보기: 그리는 중 · {ago(time.time() - self.preview_start)} 경과")
            set_tone(self.st_preview, "text")
        elif self.orig.error:
            self.st_preview.setText("미리보기: 실패")
            set_tone(self.st_preview, "warn")
        elif self.source and self.orig.pages > 0:
            self.st_preview.setText(f"미리보기: 완료 · {self.orig.pages}쪽" + (" (한컴 PDF)" if self.orig.renderer == "hancom-pdf" else ""))
            set_tone(self.st_preview, "muted")
        if self.export_start is not None:
            ev = self.export_event
            prog = progress_text(ev, time.time() - (self.stage_start or time.time())) if ev else ""
            self.st_export.setText(f"내보내는 중: {self.export_step}" + (f" · {prog}" if prog else "") + f" · {ago(time.time() - self.export_start)} 경과")
        else:
            self.st_export.setText("")
        self.busy.setVisible(self.struct_start is not None or self.orig.busy or self.export_start is not None)
        # page tools
        v = self._paged_view()
        if v is not None and v.pages > 0:
            self.lbl_page.setText(f"{v.page + 1} / {v.pages}")
        else:
            self.lbl_page.setText("– / –")
        self.btn_prev.setEnabled(v is not None and v.page > 0 and not v.busy)
        self.btn_next.setEnabled(v is not None and v.page + 1 < v.pages and not v.busy)
        self.btn_zoom.setEnabled(v is not None and v.doc is not None)
        zv = self.result_pages if self.showing_result else self.orig
        self.btn_zoom.setText(f"{zv.zoom} ▾")
        self.btn_figures.setVisible(not self.showing_result)

    def _paged_view(self):
        if not self.showing_result:
            return self.orig
        return self.result_pages if self.result_kind == "pages" else None

    def _set_active_tab(self):
        for b, on in ((self.tab_orig, not self.showing_result), (self.tab_result, self.showing_result)):
            b.setProperty("active", on)
            b.setStyleSheet(f"font-weight: bold; color: {TONES['text']};" if on else "")

    def switch_tab(self, result: bool):
        self.showing_result = bool(result)
        self.main.setCurrentIndex(1 if self.showing_result else 0)
        self._set_active_tab()
        self.refresh()

    def result_placeholder_text(self) -> str:
        return NO_RESULT if self.result_kind is None else ""

    # ── open ───────────────────────────────────────────────────────────────
    def pick(self):
        f = self.dialogs.ask_open_file(self)
        if f:
            self.open_document(f)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.cancel_event is not None:
            self.cancel_export()
            return
        if e.matches(QKeySequence.Open):
            self.pick()
            return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.handle_drop(e.mimeData())

    def handle_drop(self, mime):
        files = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if len(files) != 1:
            self.say(f"파일을 하나만 놓아 주세요. ({len(files)}개를 놓았습니다)", "warn")
            return
        self.open_document(files[0])

    def open_document(self, path: str):
        name = base_name(path)
        if not path.lower().endswith((".hwp", ".hwpx")):
            self.say(f"HWP 또는 HWPX 파일만 열 수 있습니다: {name}", "warn")
            return
        self.source = path
        self.job += 1
        job = self.job
        self.setWindowTitle(f"GovParse Desktop · {name}")
        self.lbl_file.setText(name)
        self.say(f"여는 중 · {name}", "muted", "auto")
        self.switch_tab(False)
        self.preview_start = time.time()
        self.st_preview.setText("")
        self._load_pages(self.orig, path)
        self.struct_start = time.time()
        self.struct_job = job
        t0 = time.time()

        def done(r):
            if job != self.job:
                return
            self.struct_start = None
            text = (f"구조 읽기 완료 ({time.time() - t0:.1f}초 걸림) · 문단 {r.get('paragraphs', 0):,} · "
                    f"표 {r.get('tables', 0):,} (중첩 {r.get('nested_tables', 0):,}) · 그림 {r.get('pictures', 0):,}")
            if r.get("builder") and r.get("builder") != "rhwp":
                text += " · 원본 XML에서 직접 읽음"
            self.st_struct.setText(text)
            set_tone(self.st_struct, "ok")
            self.refresh()

        def fail(e):
            if job != self.job:
                return
            self.struct_start = None
            self.st_struct.setText(f"구조: 읽기 실패 · {e}")
            set_tone(self.st_struct, "warn")
            self.refresh()

        run_bg(self.poster, lambda: self.worker.run("summary", path), done, fail)
        if self.figures_open:
            self.figures.open(path)
        else:
            self.figures.reset()
        self.refresh()

    # ── pages ──────────────────────────────────────────────────────────────
    def _load_pages(self, view: PageView, path: str):
        view.gen += 1
        view.cache.clear()
        view.doc = path
        view.page = 0
        view.pages = 0
        view.error = None
        view._raw = None
        view.picture.clear()
        view.show_message(f"미리보기 준비 중 · {base_name(path)}")
        self._show_page(view, 0)

    def _show_page(self, view: PageView, page: int):
        if view.doc is None:
            return
        if page in view.cache:
            view.page = page
            view.show_pixmap(view.cache[page])
            self._after_page(view, user=False)
            return
        gen = view.gen
        view.busy = True
        out = temp_png()
        doc = view.doc
        self.refresh()

        def done(r):
            if gen != view.gen:
                return
            view.busy = False
            pm = QPixmap(r.get("png") or out)
            try:
                os.remove(r.get("png") or out)
            except OSError:
                pass
            view.pages = int(r["pages"])
            view.renderer = r.get("renderer", "")
            view.page = page
            view.error = None
            view.cache[page] = pm
            for k in list(view.cache):
                if abs(k - page) > 1:
                    del view.cache[k]
            view.show_pixmap(pm)
            self._after_page(view, user=False)

        def fail(e):
            if gen != view.gen:
                return
            view.busy = False
            view.error = str(e)
            try:
                os.remove(out)
            except OSError:
                pass
            view.show_message(f"미리보기 실패 · {e}\n내보내기는 계속할 수 있습니다.", "warn", "다시 시도")
            if view is self.result_pages:
                self._set_res_state(True, f"미리보기 실패 · {e}", False)
            self.refresh()

        run_bg(self.poster, lambda: self.worker.run("preview", doc, out, page), done, fail)

    def _after_page(self, view, user):
        if view is self.result_pages and self.result_kind == "pages":
            self._set_res_state(True, f"미리보기 성공 · 저장된 결과 파일을 다시 그려 표시 ({view.pages}쪽)", True)
        if user and view is self.orig and self.cancel_event is None:
            self.status_kind = "auto"     # the next user action replaces an earlier answer (SPEC.md §7, C2)
        self.refresh()

    def _step(self, d):
        v = self._paged_view()
        if v is None:
            return
        target = v.page + d
        if 0 <= target < v.pages:
            if v is self.orig and self.cancel_event is None:
                self.status_kind = "auto"
            self._show_page(v, target)

    def set_zoom(self, label: str):
        v = self.result_pages if self.showing_result else self.orig
        v.zoom = label
        v.repaint_page()
        self.refresh()

    def _zoom_menu(self):
        m = QMenu(self)
        for z in ZOOMS:
            m.addAction(z, lambda z=z: self.set_zoom(z))
        m.popup(self.btn_zoom.mapToGlobal(self.btn_zoom.rect().bottomLeft()))

    # ── figures ────────────────────────────────────────────────────────────
    def toggle_figures(self):
        self.figures_open = not self.figures_open
        self.figures.setVisible(self.figures_open)
        if self.figures_open and self.source and self.figures.source != self.source:
            self.figures.open(self.source)

    def _figure_selected(self, link):
        if link is not None:
            loc = link.get("location") or {}
            if loc.get("verified") and loc.get("page_index") is not None and loc["page_index"] != self.orig.page:
                self._show_page(self.orig, int(loc["page_index"]))
        self.orig.repaint_page()

    def overlay_state(self) -> dict:
        link = self.figures.current if self.figures_open else None
        if link is None:
            return {}
        loc = link.get("location") or {}
        if not loc.get("verified"):
            return {"notice": "이 그림은 원본 화면에서 위치를 표시하지 못했습니다 — 오른쪽 ‘문서 안 위치’를 보세요"}
        if loc.get("page_index") != self.orig.page:
            return {}
        st = {"figure_box": list(loc.get("figure_box") or []), "label": "그림"}
        if loc.get("caption_box"):
            st.update(caption_box=list(loc["caption_box"]), caption_label="설명")
        return st

    def _overlay_ops(self, page):
        st = self.overlay_state()
        if not st:
            return []
        if "notice" in st:
            return [("notice", st["notice"])]
        size = (self.figures.current.get("location") or {}).get("page_size") or [0, 0]
        ops = [("box", st["figure_box"], size, "그림", "#00646E", False)]
        if "caption_box" in st:
            ops.append(("box", st["caption_box"], size, "설명", TONES["warn"], True))
        return ops

    def copy_selected_figure(self):
        self.figures.copy_selected()

    def activate_picks(self):
        self.figures.activate_picks()

    # ── export ─────────────────────────────────────────────────────────────
    def format_options(self):
        hancom = self.platform.has_hancom()
        out = []
        for key, label, desc in FORMATS:
            if key == "hwp" and not hancom:
                out.append((label, "이 PC에서 한컴오피스를 찾지 못해 사용할 수 없음", False))
            else:
                out.append((label, desc, True))
        return out

    def set_format(self, mode: str):
        self.mode = mode
        self.btn_format.setText(dict((k, l) for k, l, _ in FORMATS)[mode] + " ▾")

    def _format_menu(self):
        m = QMenu(self)
        for (key, label, _), (_, desc, enabled) in zip(FORMATS, self.format_options()):
            a = m.addAction(f"{label}\n{desc}", lambda k=key: self.set_format(k))
            a.setEnabled(enabled)
            a.setCheckable(True)
            a.setChecked(key == self.mode)
        m.popup(self.btn_format.mapToGlobal(self.btn_format.rect().topLeft()))

    def _set_exporting(self, on: bool):
        self.btn_export.setEnabled(not on)
        self.btn_cancel.setVisible(on)
        for b in (self.btn_to_html, self.btn_to_hwpx):
            b.setEnabled(not on and self.result_kind == "json")

    def _default_name(self, mode, stem, suffix):
        return f"{stem}{suffix}.{mode}" if mode in ("hwpx", "hwp") else f"{stem}-{mode}"

    def export(self):
        if self.source is None:
            self.say("먼저 문서를 여세요.", "warn")
            return
        if self.cancel_event is not None:
            return
        mode, src, name, job = self.mode, self.source, base_name(self.source), self.job
        target = self.dialogs.ask_save_target(self, mode, self._default_name(mode, Path(src).stem, "-복원"),
                                              self.last_dir or str(Path(src).parent))
        if not target:
            self.say("저장을 취소했습니다. 문서와 형식 선택은 그대로입니다.", "muted")
            return
        if same_path(target, src):
            self.say("원본과 같은 경로에는 저장할 수 없습니다. 다른 이름을 선택하세요.", "warn")
            return
        label = dict((k, l) for k, l, _ in FORMATS)[mode]

        def finished(final, rep):
            count = sum(1 for p in Path(final).rglob("*") if p.is_file()) if os.path.isdir(final) else 1
            renamed = "" if same_path(final, target) else " (같은 이름이 있어 새 이름으로 저장)"
            if mode == "json":
                self._make_zip(final, name, lambda note: report(final, rep, count, renamed, note))
            else:
                report(final, rep, count, renamed, "")

        def report(final, rep, count, renamed, zip_note):
            if job == self.job:
                self.show_result(final, rep, name)
                self.tab_result.setEnabled(True)
                self.say(f"저장 완료 · {name} · 파일 {count}개 · {final}{renamed}{zip_note} — ‘결과 보기’에서 확인",
                         "warn" if zip_note.startswith(" · 압축 실패") else "ok")
            else:
                self.say(f"이전 문서 {name}의 내보내기 완료 · 파일 {count}개 · {final}{renamed} (현재 문서 화면은 바꾸지 않았습니다)", "ok")

        self._run_staged(mode, [src], target, f"{name} → {label}", finished,
                         lambda e: self.say(f"내보내기 실패 · {name} · {e}", "warn"),
                         lambda: self.say("내보내기를 취소했습니다. 원본과 기존 결과는 그대로입니다.", "muted"))
        self.say(f"내보내는 중 · {name} → {label}  (Esc로 취소)", "muted")

    def _run_staged(self, worker_mode, args, target, step, finished, failed, cancelled, kind=None, extra=()):
        kind = kind or worker_mode
        parent = Path(target).parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / f".govparse-{uuid.uuid4().hex[:10]}"
        staging.mkdir()
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.export_start = time.time()
        self.export_step = step
        self.export_event = None
        self._set_exporting(True)
        self.refresh()

        def progress(ev):
            def apply():
                if self.export_event is None or ev.get("stage") != self.export_event.get("stage"):
                    self.stage_start = time.time()
                self.export_event = ev
                self.refresh()
            self.poster.post.emit(apply)

        def work():
            rep = self.worker.run(worker_mode, *args, str(staging), *extra, on_progress=progress, cancel=cancel)
            if cancel.is_set():
                raise _Cancelled()
            if kind in ("hwpx", "hwp"):
                final = unique(target)
                os.replace(staging / f"document.{kind}", final)
                links = staging / "document.links.json"
                if links.exists():
                    os.replace(links, unique(str(Path(final).with_suffix(".links.json"))))
                shutil.rmtree(staging, ignore_errors=True)
            else:
                final = unique(target)
                os.rename(staging, final)
            return final, rep

        def end():
            shutil.rmtree(staging, ignore_errors=True)
            self.cancel_event = None
            self.export_start = None
            self.export_event = None
            self._set_exporting(False)
            self.refresh()

        def done(r):
            end()
            self.last_dir = str(parent)
            finished(*r)

        def fail(e):
            was_cancelled = cancel.is_set()
            end()
            cancelled() if was_cancelled else failed(e)

        run_bg(self.poster, work, done, fail)

    def cancel_export(self):
        if self.cancel_event is not None:
            self.say("취소하는 중…", "muted")
            self.cancel_event.set()

    def _make_zip(self, folder, name, then):
        target = folder.rstrip("\\/") + ".zip"
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.export_start = time.time()
        self.export_step = f"{name} → 압축 파일"
        self._set_exporting(True)
        self.say(f"압축 파일 만드는 중 · {base_name(target)}  (Esc로 취소해도 결과 폴더는 그대로)", "muted")

        def end():
            self.cancel_event = None
            self.export_start = None
            self._set_exporting(False)
            self.refresh()

        def done(z):
            end()
            then(f" · 압축 파일 {base_name(z['zip'])} ({z['bytes'] / 1048576:.1f}MB)")

        def fail(e):
            was = cancel.is_set()
            end()
            then(" · 압축은 취소함 (결과 폴더는 저장됨)" if was else f" · 압축 실패: {e} (결과 폴더는 저장됨)")

        run_bg(self.poster, lambda: self.worker.run("zip", folder, target, cancel=cancel), done, fail)

    def convert(self, fmt: str):
        if self.result_json is None or self.cancel_event is not None:
            return
        stem = Path(self.result_json).parent.name
        target = self.dialogs.ask_save_target(self, fmt, self._default_name(fmt, stem, "-변환"), self.last_dir or str(Path(self.result_json).parent.parent))
        if not target:
            self.say("변환을 취소했습니다.", "muted")
            return
        gen = self.result_gen

        def finished(final, rep):
            if gen == self.result_gen:           # SPEC.md §7: a late convert does not replace a newer result
                self.show_result(final, rep, None)
                self.tab_result.setEnabled(True)
            self.say(f"변환 완료 · {final}", "ok")

        self._run_staged("convert", [self.result_json], target, f"기존 결과 → {fmt.upper()}", finished,
                         lambda e: self.say(f"변환 실패 · {e}", "warn"), lambda: self.say("변환을 취소했습니다.", "muted"),
                         kind=fmt, extra=(fmt,))

    # ── result view ────────────────────────────────────────────────────────
    def _load_result_dialog(self):
        f = self.dialogs.ask_load_result(self)
        if f:
            self.load_result(f, announce=True)

    def load_result(self, path: str, announce: bool = True):
        self.tab_result.setEnabled(True)
        self.switch_tab(True)
        self.show_result(path, None, None, announce=announce)

    def _result_file(self) -> str:
        p = self.result_path
        if p and os.path.isdir(p):
            h = os.path.join(p, "document.html")
            return h if os.path.exists(h) else os.path.join(p, "document.json")
        return p

    def _zip_of(self):
        f = self._result_file()
        if not f:
            return None
        folder = os.path.dirname(f) if os.path.basename(f) in ("document.json", "document.html") else None
        if not folder:
            return None
        z = folder.rstrip("\\/") + ".zip"
        return z if os.path.isfile(z) else None

    def _source_name_of(self, file: str):
        try:
            rep = json.loads((Path(file).parent / "report.json").read_text("utf-8"))
            return base_name(rep["source"]) if rep.get("source") else None
        except (OSError, ValueError, KeyError):
            return None

    def _set_res_state(self, saved, preview, ok):
        self.res_state.setText(("파일 저장: 성공" if saved else "파일 저장: 확인 필요") + "\n" + preview)
        set_tone(self.res_state, "ok" if saved and ok else "warn")

    def show_result(self, path, rep, source_name, announce=False):
        self.result_gen += 1
        gen = self.result_gen
        path = str(path)
        self.result_path = path
        file = self._result_file()
        ext = Path(file).suffix.lower()
        kind = "json" if ext == ".json" else "html" if ext in (".html", ".htm") else "pages"
        self.result_kind = kind
        self.result_json = file if kind == "json" else None
        notes = []
        if rep is not None:
            notes += [f"· {n}" for n in rep.get("notes") or []]
            if rep.get("restore"):
                r = rep["restore"]
                notes.append(f"· 그림 {r.get('pictures')}개 복원 · 원문 캡션 {r.get('source_captions')}개 · 사용자 연결 {r.get('user_links_sidecar')}건은 .links.json")
            if rep.get("bundle"):
                c = rep["bundle"]["counts"]
                if c.get("table_image"):
                    notes.append(f"· 내장 그림 {c.get('embedded_picture')}개 · 표 이미지 {c['table_image']}장 (표마다 따로)")
                else:
                    t = c.get("rendered_table_capture")
                    notes.append(f"· 내장 그림 {c.get('embedded_picture')}개 · 표 캡처 {'미확인' if t is None else f'{t}장'}")
            if rep.get("ir_builder"):
                notes.append(f"· 구조 읽기: {rep['ir_builder']}")
        else:
            notes.append("· 기존 결과를 불러왔습니다 (이번 실행에서 만든 결과가 아님)")
        source_name = source_name or self._source_name_of(file)
        self.res_title.setText((f"{source_name} → " if source_name else "") + base_name(path))
        self.res_notes.setText("\n".join(notes))
        saved = os.path.exists(file)
        self.result_view.placeholder.setVisible(False)
        self.result_view.stack.setVisible(True)
        self.btn_external.setText({"html": "브라우저에서 열기", "pages": "한글에서 열기"}.get(kind, "JSON 열기"))
        is_json = kind == "json"
        for w in (self.asset_search, self.assets, self.result_ocr, self.btn_to_html, self.btn_to_hwpx):
            w.setVisible(is_json)
        self._set_exporting(self.cancel_event is not None)
        self.btn_zip.setVisible(self._zip_of() is not None)
        self.assets.clear()
        self.asset_search.blockSignals(True)
        self.asset_search.clear()
        self.asset_search.blockSignals(False)
        self.asset_preview.clear()
        if kind == "html":
            self.result_view.stack.setCurrentWidget(self.result_web)
            self.result_web.setSearchPaths([str(Path(file).parent)])
            self.result_web.setSource(Path(file).as_uri())
            self._set_res_state(saved, "미리보기 성공 · 저장된 로컬 HTML을 표시", True)
            if announce and rep is None:
                self.say(f"기존 결과를 불러왔습니다 · {path}", "ok")
        elif kind == "pages":
            self.result_view.stack.setCurrentWidget(self.result_pages)
            self._set_res_state(saved, "미리보기 준비 중 · 저장된 결과 파일을 다시 그리는 중", False)
            self._load_pages(self.result_pages, file)
            if announce and rep is None:
                self.say(f"기존 결과를 불러왔습니다 · {path}", "ok")
        else:
            self.result_view.stack.setCurrentWidget(self.asset_preview)
            self._set_res_state(saved, "자산 목록 읽는 중…", False)

            def done(idx):
                if gen != self.result_gen:
                    return
                self._fill_assets(idx, saved)
                if announce and rep is None:
                    self.say(f"기존 결과를 불러왔습니다 · {path}", "ok")

            def fail(e):
                if gen != self.result_gen:
                    return
                self._set_res_state(saved, f"자산 목록을 읽지 못했습니다 · {e}", False)
                self.say(f"결과를 불러오지 못했습니다 · {e}", "warn")

            run_bg(self.poster, lambda: self.worker.run("index", file), done, fail)
        self.refresh()

    def _fill_assets(self, idx, saved):
        src = idx.get("source_name") or ""
        gone = 0
        for a in idx.get("assets") or []:
            origin, txt = a.get("origin"), a.get("text") or ""
            if origin == "embedded_picture":
                kind_text, info = "내장 그림", txt or "문서에 들어 있던 그림"
            elif origin == "table_image":
                kind_text, info = "표 이미지", txt
            else:
                kind_text, info = "표 캡처", f"미리보기 {a.get('page')}쪽의 표 영역 (어느 표인지는 확인되지 않음)"
            it = QTreeWidgetItem([base_name(a["path"]), kind_text, info, src])
            it.setData(0, Qt.UserRole, a)
            self.assets.addTopLevelItem(it)
            if not os.path.isfile(a.get("file") or ""):
                gone += 1
        notes = self.res_notes.text().split("\n") if self.res_notes.text() else []
        ti = idx.get("table_images")
        if ti:
            line = f"· 표 {ti['tables']}개 중 {ti['rendered']}개 이미지 생성 · PNG {ti['png']}장"
            if ti.get("not_rendered"):
                line += f" · 미생성 {ti['not_rendered']}개는 목록 끝에 사유 표시"
            notes.append(line)
            for m in ti.get("missing") or []:
                it = QTreeWidgetItem(["(이미지 없음)", "표 이미지 미생성", f"{m['label']} · {m['reason_text']}", src])
                it.setData(0, Qt.UserRole, dict(m))
                for c in range(4):
                    it.setForeground(c, QBrush(QColor(TONES["warn"])))
                self.assets.addTopLevelItem(it)
        integ = idx.get("integrity")
        missing = len(idx.get("missing") or []) + gone
        if not integ:
            notes.append("· 저장 후 변경 여부: 확인할 수 없음 (검증 목록이 없는 결과)")
        else:
            ch, ms = len(integ.get("changed") or []), len(integ.get("missing") or [])
            notes.append(f"· 저장 후 변경 없음 (파일 {integ['checked']}개 해시 확인)" if ch + ms == 0
                         else f"· 주의: 저장 후 바뀐 파일 {ch}개, 없어진 파일 {ms}개")
            missing += ch
        self.res_notes.setText("\n".join(notes))
        rows = self.assets.topLevelItemCount()
        line = f"미리보기 성공 · 자산 {rows}개" + (f" · 파일 없는 자산 {missing}개" if missing else " (추출된 PNG 0개)" if rows == 0 else "")
        self._set_res_state(saved, line, missing == 0)
        if rows:
            self.assets.setCurrentItem(self.assets.topLevelItem(0))
        self.result_ocr.refresh()

    def _asset_file(self):
        it = self.assets.currentItem()
        d = it.data(0, Qt.UserRole) if it is not None else None
        return d.get("file") if isinstance(d, dict) else None

    def _select_asset(self):
        f = self._asset_file()
        it = self.assets.currentItem()
        d = it.data(0, Qt.UserRole) if it is not None else None
        self.asset_preview.clear()
        self._preview_src_size = QSize()
        if f and os.path.isfile(f):
            pm = QPixmap(f)
            self._preview_src_size = pm.size()
            area = self.asset_preview.size()
            if pm.width() <= max(area.width(), 1) and pm.height() <= max(area.height(), 1) or area.width() < 10:
                self.preview_mode = "natural"
                self.asset_preview.setPixmap(pm)
            else:
                self.preview_mode = "fit"
                self.asset_preview.setPixmap(pm.scaled(area, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        st = (d or {}).get("ocr_status")
        self.result_ocr.refresh(list((d or {}).get("ocr_texts") or []) if st in ("ok", "empty") and d.get("ocr_texts") is not None and st == "ok" else None)

    def asset_preview_mode(self):
        return self.preview_mode

    def asset_preview_source_size(self):
        return self._preview_src_size

    def _filter_assets(self, q):
        n = norm(q)
        for i in range(self.assets.topLevelItemCount()):
            it = self.assets.topLevelItem(i)
            it.setHidden(bool(n) and n not in norm(it.text(2)))

    def copy_selected_asset(self):
        f = self._asset_file()
        ok, msg, _ = copy_picture(f)
        self.say(msg if ok else (msg or "이 그림은 원본 파일이 없어 복사할 수 없습니다."), "ok" if ok else "warn")

    def asset_context_menu(self, item):
        if item is not None:
            self.assets.setCurrentItem(item)
        m = QMenu(self)
        m.addAction("그림 복사\tCtrl+C", self.copy_selected_asset)
        return m

    def _open_external(self):
        f = self._result_file()
        if not f:
            return
        try:
            self.platform.open_default(f)
        except OSError as e:
            self.say(f"열 수 없습니다 · {e}", "warn")

    # ── settings ───────────────────────────────────────────────────────────
    def make_settings_dialog(self):
        return SettingsDialog(self, self.poster, self.worker)

    def _open_settings(self):
        d = self.make_settings_dialog()
        if d.exec() and d.saved_label:
            self.after_settings_saved(d.saved_label)

    def after_settings_saved(self, label):
        self.say(f"OCR 엔진을 바꿨습니다 · {label}", "ok")

    # ── bridge (SPEC.md §5) ────────────────────────────────────────────────
    def _start_bridge(self):
        from PySide6.QtNetwork import QLocalServer

        self._server = QLocalServer(self)
        # Qt's UserAccessOption makes QLocalServer.listen() fail on the tested
        # Windows Qt runtime. Windows named pipes use the process user's default
        # security descriptor; Unix sockets get an explicit private mode below.
        if sys.platform != "win32":
            self._server.setSocketOptions(QLocalServer.UserAccessOption)
        name = bridge_mod.server_name()
        if sys.platform != "win32" and os.path.exists(name):
            os.remove(name)
        if self._server.listen(name):
            if sys.platform != "win32":
                os.chmod(name, 0o600)
            self._server.newConnection.connect(self._bridge_connection)

    def bridge_address(self) -> str:
        return self._server.fullServerName()

    def _bridge_connection(self):
        while self._server.hasPendingConnections():
            c = self._server.nextPendingConnection()
            buf = bytearray()

            def ready(c=c, buf=buf):
                buf.extend(bytes(c.readAll()))
                if b"\n" in buf:
                    line = buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
                    c.write((json.dumps(self.bridge_request(line), ensure_ascii=False) + "\n").encode("utf-8"))
                    c.flush()
                    c.disconnectFromServer()

            c.readyRead.connect(ready)

    def bridge_request(self, line: str) -> dict:
        modal = QApplication.activeModalWidget() is not None
        status = "open"
        parts = line.split("|")
        if len(parts) == 3 and parts[0] == "show":
            if modal:
                status = "dialog_open"
            elif self.figures.hash is None or not self.figures_open:
                status = "figure_panel_unavailable"
            elif self.figures.hash != parts[1]:
                status = "document_mismatch"
            else:
                it = self.figures.item_of(parts[2])
                if it is None:
                    status = "item_missing_or_filtered"
                elif not (it.data(0, Qt.UserRole).get("location") or {}).get("verified"):
                    status = "location_unverified"
                else:
                    self.figures.list.clearSelection()
                    self.figures.list.setCurrentItem(it)
                    self.figures.list.scrollToItem(it)
                    self.switch_tab(False)
                    status = "selected"
        elif line in ("original", "result"):
            if modal:
                status = "dialog_open"
            elif line == "result" and not self.tab_result.isEnabled():
                status = "view_unavailable"
            else:
                self.switch_tab(line == "result")
                status = "shown"
        elif line != "state":
            status = "invalid_request"
        on_orig = not self.showing_result
        sel = self.figures.selected_links()
        return {"status": status, "title": self.windowTitle(), "view": "result" if self.showing_result else "original",
                "source_sha256": self.figures.hash,
                "item_id": sel[0]["occurrence"]["occ_id"] if on_orig and self.figures_open and len(sel) == 1 else None,
                "page": self.orig.page + 1 if on_orig and self.orig.pages > 0 else None}

    # ── close ──────────────────────────────────────────────────────────────
    def closeEvent(self, e):
        self.worker_shutdown_called = True
        if hasattr(self.worker, "shutdown"):
            try:
                self.worker.shutdown()
            except Exception:  # noqa: BLE001
                pass
        if getattr(self, "_server", None) is not None:
            self._server.close()
        super().closeEvent(e)


class _Cancelled(Exception):
    pass


def main() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
