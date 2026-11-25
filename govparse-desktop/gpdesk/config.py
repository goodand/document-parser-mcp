"""Locate the engine. Existing LM Studio / Claude MCP settings are only *read*.

Override any value in ``govparse-desktop/config.json``:
  {"engine_python": "...python.exe", "engine_src": "...src", "ocr_models": "...rapidocr",
   "data_root": "..."}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


@dataclass
class EngineConfig:
    engine_python: Path
    engine_src: Path
    ocr_models: Path | None
    data_root: Path

    def env(self, data_root: Path | None = None) -> dict:
        env = dict(os.environ)
        env.update(PYTHONPATH=str(self.engine_src), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                   GOVPARSE_DATA=str(data_root or self.data_root))
        return env


def _from_mcp_settings() -> tuple[Path | None, dict]:
    for p in (Path.home() / ".lmstudio" / "mcp.json", Path.home() / ".claude.json"):
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                gp = node.get("govparse")
                if isinstance(gp, dict) and gp.get("command"):
                    exe = Path(gp["command"])
                    py = exe.with_name("python.exe")
                    if py.exists():
                        return py, gp.get('env') or {}
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return None, {}


def load() -> EngineConfig:
    over: dict = {}
    cfg = APP_DIR / "config.json"
    if cfg.exists():
        over = json.loads(cfg.read_text("utf-8"))
    registered_py, env = _from_mcp_settings()
    py = Path(over["engine_python"]) if over.get("engine_python") else registered_py
    if py != registered_py:
        env = {}
    if py is None:
        raise RuntimeError("govparse 엔진 Python을 찾지 못했습니다. config.json에 engine_python을 지정하세요.")
    registered_src = next((Path(s) for s in env.get("PYTHONPATH", "").split(os.pathsep)
                           if s and (Path(s) / "govparse").is_dir()), None)
    src = Path(over.get("engine_src") or registered_src or APP_DIR.parent / "src")
    models = over.get("ocr_models") or env.get("GOVPARSE_OCR_MODEL_DIR")
    if not models and env.get("GOVPARSE_DATA"):
        candidate = Path(env["GOVPARSE_DATA"]) / "models" / "rapidocr"
        if candidate.is_dir():
            models = candidate
    if not models:
        guess = py.parents[2] / "data" / "models" / "rapidocr"
        models = guess if guess.exists() else None
    root = Path(over.get("data_root") or Path(os.environ.get("LOCALAPPDATA", Path.home()))
                / "GovParseDesktop")
    return EngineConfig(py, src, Path(models) if models else None, root)
