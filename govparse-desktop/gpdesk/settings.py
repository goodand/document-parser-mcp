"""User settings, stored per user in the OS's usual application-data folder.

Windows: %LOCALAPPDATA%\\GovParse Desktop\\settings.json
macOS:   ~/Library/Application Support/GovParse Desktop/settings.json
other:   ~/.config/GovParse Desktop/settings.json
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

APP = "GovParse Desktop"
DEFAULTS = {"ocr_engine": "rapidocr", "preview": "rhwp"}
PREVIEW_IDS = ("rhwp", "hancom")   # how the 원본 tab draws pages


def settings_path() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP / "settings.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP / "settings.json"
    return Path.home() / ".config" / APP / "settings.json"


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        stored = json.loads(settings_path().read_text("utf-8"))
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass  # missing or broken file: defaults
    return data


def update(**changes) -> dict:
    from .ocr_engines import ENGINE_IDS

    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"알 수 없는 설정: {sorted(unknown)}")
    if "ocr_engine" in changes and changes["ocr_engine"] not in ENGINE_IDS:
        raise ValueError(f"알 수 없는 OCR 엔진: {changes['ocr_engine']}")
    if "preview" in changes and changes["preview"] not in PREVIEW_IDS:
        raise ValueError(f"알 수 없는 미리보기 방식: {changes['preview']}")
    data = load()
    data.update(changes)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)
    return data
