"""설정: which OCR engine reads a picture (SPEC.md §3)."""

from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from .widgets import run_bg, set_tone


class SettingsDialog(QDialog):
    def __init__(self, parent, poster, worker):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("설정")
        self.poster, self.worker = poster, worker
        self.chosen = None
        self.saved_label = None
        self.descriptions: dict[str, str] = {}
        title = QLabel("OCR 엔진")
        title.setStyleSheet("font-weight: 700; font-size: 12pt;")
        sub = QLabel("‘이 그림 OCR’을 누를 때 그림 속 글자를 읽는 방법입니다. 쓸 수 없는 엔진은 이유와 함께 흐리게 표시됩니다.")
        sub.setWordWrap(True)
        set_tone(sub, "muted")
        self.engines = QWidget()
        self.engines_layout = QVBoxLayout(self.engines)
        self.placeholder = QLabel("엔진 목록을 확인하는 중…")
        self.engines_layout.addWidget(self.placeholder)
        self.path = QLabel("")
        self.path.setObjectName("settingsPath")
        set_tone(self.path, "muted")
        self.save = QPushButton("저장")
        self.save.setObjectName("settingsSave")
        self.save.setEnabled(False)
        self.save.setProperty("kind", "primary")
        self.cancel = QPushButton("취소")
        self.cancel.setObjectName("settingsCancel")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.save)
        buttons.addWidget(self.cancel)
        lay = QVBoxLayout(self)
        for w in (title, sub, self.engines, self.path):
            lay.addWidget(w)
        lay.addLayout(buttons)
        self.cancel.clicked.connect(self.reject)
        self.save.clicked.connect(self._save)
        run_bg(poster, lambda: worker.run("settings"), self._loaded, self._load_failed)

    def _loaded(self, d):
        self.placeholder.setParent(None)
        self.chosen = d["settings"]["ocr_engine"]
        self.path.setText(f"설정 파일: {d['path']}")
        for e in d["engines"]:
            rb = QRadioButton(e["label"])
            rb.setObjectName(f"engine_{e['id']}")
            rb.setEnabled(bool(e["available"]))
            rb.setChecked(e["id"] == self.chosen)
            rb.toggled.connect(lambda on, i=e["id"]: on and setattr(self, "chosen", i))
            desc = e["description"] + ("" if e["available"] else f"\n사용할 수 없음 · {e['reason']}")
            self.descriptions[e["id"]] = desc
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            set_tone(lbl, "muted")
            self.engines_layout.addWidget(rb)
            self.engines_layout.addWidget(lbl)
        self.save.setEnabled(True)
        self.save.setFocus()

    def _load_failed(self, e):
        self.placeholder.setText(f"설정을 읽지 못했습니다 · {e}")
        set_tone(self.placeholder, "warn")

    def engine_description(self, engine_id: str) -> str:
        return self.descriptions.get(engine_id, "")

    def engines_text(self) -> str:
        return self.placeholder.text() if self.placeholder.parent() is not None else ""

    def _save(self):
        if self.chosen is None:
            return
        self.save.setEnabled(False)

        def done(d):
            self.saved_label = next(e["label"] for e in d["engines"] if e["id"] == self.chosen)
            self.accept()

        def fail(e):
            self.save.setEnabled(True)
            self.path.setText(f"저장하지 못했습니다 · {e}")
            set_tone(self.path, "warn")

        run_bg(self.poster, lambda: self.worker.run("settings-set", "ocr_engine", self.chosen), done, fail)
