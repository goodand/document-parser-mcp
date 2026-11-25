"""OCR engines the user can choose in Settings.

Each engine says whether it can run here and why not. Adding an engine = one entry in ENGINES,
one runner in RUNNERS, one availability rule in is_available(). Model comparisons are done on the
engine side; this module only offers what is installed and lets the user pick.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import settings

HERE = Path(__file__).resolve().parent

ENGINES = [
    {"id": "rapidocr", "label": "RapidOCR · PP-OCRv5 한국어 (mobile)",
     "description": "govparse 엔진에 설치된 로컬 모델. 실문서 그림 5장에서 정답 문구 23/27 (2026-11-25 측정)."},
    {"id": "windows", "label": "Windows 내장 OCR · 한국어",
     "description": "추가 설치 없이 빠르지만 같은 표본에서 11/27로 정확도가 낮음. Windows 전용."},
]
ENGINE_IDS = {e["id"] for e in ENGINES}


def _rapidocr_available() -> tuple[bool, str]:
    try:
        from .config import load as load_config

        cfg = load_config()
    except Exception as e:  # noqa: BLE001
        return False, f"govparse 엔진을 찾지 못함 ({str(e)[:80]})"
    if not cfg.ocr_models:
        return False, "OCR 모델 폴더가 없음"
    return True, ""


def _windows_available() -> tuple[bool, str]:
    if not sys.platform.startswith("win"):
        return False, "Windows에서만 쓸 수 있음"
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime];"
                        "$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime];"
                        "if([Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko'))){'ok'}"],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return ("ok" in r.stdout, "" if "ok" in r.stdout else "Windows 한국어 OCR 인식기가 설치되어 있지 않음")


def is_available(engine_id: str) -> tuple[bool, str]:
    return {"rapidocr": _rapidocr_available, "windows": _windows_available}[engine_id]()


def list_engines() -> list[dict]:
    out = []
    for e in ENGINES:
        ok, why = is_available(e["id"])
        out.append({**e, "available": ok, "reason": why})
    return out


def _run_rapidocr(png: Path) -> dict:
    from . import runner
    from .config import load as load_config

    cfg = load_config()
    out = Path(tempfile.mkdtemp(prefix="gpocr-")) / "r.json"
    r = runner.run([str(cfg.engine_python), str(HERE / "ocr_tool.py"), str(cfg.ocr_models), str(out), str(png)],
                   timeout=180, env=dict(os.environ, PYTHONUTF8="1"))
    if not out.exists():
        return {"status": "failed", "texts": [], "error": (r.stderr or "결과 없음")[-300:]}
    return json.loads(out.read_text("utf-8")).get(Path(png).name, {"status": "failed", "texts": []})


def _run_windows(png: Path) -> dict:
    out = Path(tempfile.mkdtemp(prefix="gpwinocr-")) / "r.json"
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "winocr.ps1"),
                    "-OutJson", str(out), str(Path(png).resolve())],
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not out.exists():
        return {"status": "failed", "texts": [], "error": "Windows OCR 결과 없음"}
    res = json.loads(out.read_text("utf-8-sig")).get(Path(png).name, {})
    texts = [t for t in res.get("texts", []) if t and t.strip()]
    if res.get("status") == "failed":
        return {"status": "failed", "texts": [], "error": res.get("error", "")}
    return {"status": "ok" if texts else "empty", "texts": texts, "error": ""}


RUNNERS = {"rapidocr": _run_rapidocr, "windows": _run_windows}


def run(png: Path) -> dict:
    """OCR one picture with the engine chosen in Settings; the result names the engine."""
    engine = settings.load()["ocr_engine"]
    label = next(e["label"] for e in ENGINES if e["id"] == engine)
    ok, why = is_available(engine)
    if not ok:
        return {"status": "not_available", "texts": [], "error": f"{label}: {why}", "engine": engine,
                "engine_label": label}
    return {**RUNNERS[engine](png), "engine": engine, "engine_label": label}
