"""Harness for the PySide6 GUI tests (Windows, macOS, Linux).

The tests describe the shared GUI behavior, with a scripted worker for deterministic UI tests.

How the GUI is reached:
- Widgets are found by objectName (see qt/SPEC.md), never by pixel position, so the same test runs on
  every OS and in any window size.
- Qt runs with QT_QPA_PLATFORM=offscreen: no screen needed (CI on Linux/macOS/Windows).
- The document worker is replaced by FakeWorker for GUI tests: every call is recorded and answered from a
  script, so tests are fast and exact. Tests marked `integration` use the real worker and samples/.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP = Path(__file__).resolve().parents[2]           # govparse-desktop/
sys.path.insert(0, str(APP))                          # gpdesk (worker) and gpqt (GUI)
sys.path.insert(0, str(APP.parent / "src"))
SAMPLES = APP / "samples"


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: uses the real document worker and samples/")
    config.addinivalue_line("markers", "windows_only: behavior that exists only on Windows (Hancom, Windows OCR)")


@dataclass
class Call:
    mode: str
    args: list
    cancelled: bool = False


@dataclass
class FakeWorker:
    """Stands in for gpqt's worker client. Answers are scripted per mode.

    reply[mode] is a dict (returned as the result), an Exception (raised), or a callable(call) -> dict.
    progress[mode] is a list of progress events sent before the result.
    hold[mode] = True keeps the call running until release(mode) or cancel(), to test running/cancel states.
    """
    reply: dict = field(default_factory=dict)
    progress: dict = field(default_factory=dict)
    hold: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    _gates: dict = field(default_factory=dict)
    # Figure review session (worker.py serve): one dict in, one dict out.
    serve_reply: object = None
    served: list = field(default_factory=list)

    def run(self, mode: str, *args, on_progress=None, cancel=None) -> dict:
        call = Call(mode, list(args))
        self.calls.append(call)
        for event in self.progress.get(mode, []):
            if on_progress:
                on_progress(dict(event))
        if self.hold.get(mode):
            gate = self._gates.setdefault(mode, threading.Event())
            while not gate.wait(0.02):
                if cancel is not None and cancel.is_set():
                    call.cancelled = True
                    raise CancelledError()
        r = self.reply.get(mode, {})
        if isinstance(r, Exception):
            raise r
        return r(call) if callable(r) else dict(r)

    def release(self, mode: str) -> None:
        self._gates.setdefault(mode, threading.Event()).set()

    def modes(self) -> list[str]:
        return [c.mode for c in self.calls]

    def serve(self, cmd: dict) -> dict:
        self.served.append(dict(cmd))
        if self.hold.get("serve"):
            gate = self._gates.setdefault("serve", threading.Event())
            gate.wait(10)
        r = self.serve_reply
        if isinstance(r, Exception):
            raise r
        return r(cmd) if callable(r) else dict(r or {"ok": True})


class CancelledError(Exception):
    """Raised by the worker client when a running call was cancelled (Esc / 취소)."""


def png(path: Path, w: int = 120, h: int = 80, color=(0, 100, 110)) -> Path:
    """A real PNG file (the GUI shows and copies it)."""
    from PySide6.QtGui import QImage, QColor

    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(*color))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path))
    return path


def default_replies(tmp: Path) -> dict:
    """Worker answers for a 3-page HWPX with 6 pictures, shaped like the real worker's (SPEC.md §2)."""
    def preview(call):
        out = Path(call.args[1])
        png(out, 600, 800)
        return {"pages": 3, "renderer": "rhwp", "png": str(out)}
    return {
        "preview": preview,
        "summary": {"seconds": 0.3, "builder": "rhwp", "paragraphs": 16, "tables": 3, "nested_tables": 1, "pictures": 6,
                    "source_sha256": "b9986eb86f46b63a63b8559fa595e283454294ea16f97cdf68f8ba63752dfce2", "preview_note": None},
        "settings": {"settings": {"ocr_engine": "rapidocr"}, "path": str(tmp / "settings.json"), "engines": [
            {"id": "rapidocr", "label": "RapidOCR · PP-OCRv5 한국어 (mobile)", "description": "앱에 들어 있는 로컬 모델.", "available": True, "reason": ""},
            {"id": "windows", "label": "Windows 내장 OCR · 한국어", "description": "Windows 전용.", "available": False, "reason": "Windows에서만 쓸 수 있음"}]},
    }


@dataclass
class FakeDialogs:
    """Answers for file dialogs: None = the user pressed Cancel."""
    open_file: object = None
    load_result: object = None
    save_target: object = None
    asked: list = field(default_factory=list)

    def ask_open_file(self, parent):
        self.asked.append(("open_file",))
        return self.open_file

    def ask_load_result(self, parent):
        self.asked.append(("load_result",))
        return self.load_result

    def ask_save_target(self, parent, mode, default_name, start_dir):
        self.asked.append(("save_target", mode, default_name))
        return self.save_target


@dataclass
class FakePlatform:
    hancom: bool = False
    revealed: list = field(default_factory=list)
    opened: list = field(default_factory=list)

    def has_hancom(self) -> bool:
        return self.hancom

    def reveal(self, path) -> None:          # Explorer /select, Finder open -R, xdg-open folder
        self.revealed.append(str(path))

    def open_default(self, path) -> None:    # default app for a file
        self.opened.append(str(path))


@pytest.fixture
def fake_worker(tmp_path):
    w = FakeWorker()
    w.reply.update(default_replies(tmp_path))
    return w


@pytest.fixture
def dialogs():
    return FakeDialogs()


@pytest.fixture
def platform_():
    return FakePlatform()


@pytest.fixture
def hwpx(tmp_path):
    """A document path the GUI accepts (contents are irrelevant to the fake worker)."""
    p = tmp_path / "문서 폴더" / "합성 문서.hwpx"
    p.parent.mkdir()
    p.write_bytes(b"PK\x03\x04 fake")
    return p


@pytest.fixture
def user_dirs(tmp_path, monkeypatch):
    """Per-test settings / decisions / temp folders, so tests never touch the real user profile."""
    home = tmp_path / "home"
    home.mkdir()
    for var in ("LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    return home


@pytest.fixture
def window(qtbot, fake_worker, dialogs, platform_, user_dirs):
    """The main window with fakes injected (SPEC.md §1). Fails (ImportError) until gpqt exists."""
    from gpqt.app import MainWindow

    w = MainWindow(worker=fake_worker, dialogs=dialogs, platform=platform_)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def find(w, name, cls=None):
    """Child widget by objectName (SPEC.md §3). Fails the test with the missing name."""
    from PySide6.QtCore import QObject

    child = w.findChild(cls or QObject, name)
    assert child is not None, f"widget '{name}' not found (SPEC.md)"
    return child


def text(w, name) -> str:
    return find(w, name).text()


def tone(w, name) -> str:
    return find(w, name).property("tone")


def click(qtbot, w, name):
    from PySide6.QtCore import Qt

    b = find(w, name)
    assert b.isVisible() and b.isEnabled(), f"'{name}' is not clickable"
    qtbot.mouseClick(b, Qt.LeftButton)


def wait_status(qtbot, w, fragment, timeout=3000):
    qtbot.waitUntil(lambda: fragment in text(w, "statusText"), timeout=timeout)


def open_doc(qtbot, w, path):
    """Open a document the way the user does (Ctrl+O path is tested separately) and wait for preview + structure."""
    w.open_document(str(path))
    qtbot.waitUntil(lambda: "완료" in text(w, "stStructure") and text(w, "lblPage") != "– / –", timeout=3000)
