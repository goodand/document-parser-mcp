"""Local OCR worker, run in the engine interpreter (rapidocr installed there).

usage: python ocr_tool.py <model_dir> <out.json> <png>...
Writes {png_name: {"status": ok|empty|failed, "texts": [...], "error": ""}}.
Uses govparse.pdf_table_ocr._run_ocr so models and parameters match the engine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    model_dir, out = Path(argv[0]), Path(argv[1])
    from govparse.pdf_table_ocr import _run_ocr

    result = {}
    for png in argv[2:]:
        p = Path(png)
        try:
            r = _run_ocr(p.read_bytes(), model_dir)
            texts = [t for t in r.get("texts", []) if t and t.strip()]
            result[p.name] = {"status": "ok" if texts else "empty", "texts": texts, "error": ""}
        except Exception as e:  # noqa: BLE001
            result[p.name] = {"status": "failed", "texts": [], "error": f"{type(e).__name__}: {e}"}
        out.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    out.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
