"""Local OCR worker, run in the engine interpreter (rapidocr installed there).

usage: python ocr_tool.py <model_dir> <out.json> [--legacy] <picture>...
Writes {name: {"status": ok|empty|failed|needs_preprocess, "texts": [...], "error": "", "preprocess": engine|gui}}.

Default: the engine's shared govparse.image_ocr.recognize_image reads the ORIGINAL picture; it shrinks,
pads and maps boxes back to source pixels itself, so GUI, batch OCR and MCP read alike.
An engine that has no such function (or one that cannot shrink) gets status needs_preprocess; the GUI
then shrinks and pads the picture and calls again with --legacy, which uses pdf_table_ocr._run_ocr as before.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path


def _shared_reader():
    """recognize_image if this engine has it with downscaling, else None."""
    try:
        from govparse.image_ocr import recognize_image
    except Exception:  # noqa: BLE001 - older engine
        return None
    return recognize_image if "max_side_px" in inspect.signature(recognize_image).parameters else None


def main(argv: list[str]) -> int:
    model_dir, out = Path(argv[0]), Path(argv[1])
    files = argv[2:]
    legacy = bool(files) and files[0] == "--legacy"
    files = files[1:] if legacy else files
    shared = None if legacy else _shared_reader()
    if legacy:
        from govparse.pdf_table_ocr import _run_ocr

    result = {}
    for png in files:
        p = Path(png)
        try:
            if legacy:
                r = _run_ocr(p.read_bytes(), model_dir)
            elif shared is None:
                result[p.name] = {"status": "needs_preprocess", "texts": [], "error": ""}
                continue
            else:
                r = shared(p, model_dir=model_dir)
            texts = [t for t in r.get("texts", []) if t and t.strip()]
            result[p.name] = {"status": "ok" if texts else "empty", "texts": texts, "error": "",
                              "preprocess": "gui" if legacy else "engine"}
        except Exception as e:  # noqa: BLE001
            result[p.name] = {"status": "failed", "texts": [], "error": f"{type(e).__name__}: {e}"}
        out.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    out.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
