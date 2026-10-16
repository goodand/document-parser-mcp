"""Select one verified parse generation; retain all previous artifacts for audit."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from pypdf import PdfReader

from .normalize import sha256_of

MANIFEST = "_active_parse.json"
RANGE = re.compile(r"p(\d+)-(\d+)")
CHILD_KEYS = ("kids", "rows", "cells", "list items")


def page_offset(path: Path) -> int:
    match = RANGE.fullmatch(path.stem)
    return int(match[1]) if match else 0


def _nodes(node):
    if not isinstance(node, dict):
        return
    yield node
    for key in CHILD_KEYS:
        for child in node.get(key) or []:
            yield from _nodes(child)


def _range(path: Path, *, strict: bool = False) -> tuple[int, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid parse JSON: {path.name}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("kids"), list):
        raise ValueError(f"Invalid parse tree: {path.name}")
    match = RANGE.fullmatch(path.stem)
    declared = data.get("number of pages")
    if declared is not None and (type(declared) is not int or declared < 1):
        raise ValueError(f"Invalid page count: {path.name}")
    if strict and declared is None:
        raise ValueError(f"Missing declared page count: {path.name}")
    if match:
        start, end = map(int, match.groups())
        if end <= start or (declared is not None and end - start != declared):
            raise ValueError(f"Page range/count mismatch: {path.name}")
    else:
        start = 0
        page_numbers = [n.get("page number") for n in _nodes(data)]
        inferred = max((p for p in page_numbers if type(p) is int and p > 0), default=1)
        end = declared or inferred
    for node in _nodes(data):
        if "page number" not in node:
            continue
        page = node["page number"]
        # Containers can have no page assignment; assigned pages must be local.
        if page is None:
            continue
        if type(page) is not int or not 1 <= page <= end - start:
            raise ValueError(f"Node page outside declared range: {path.name}: {page!r}")
    return start, end


def _ordered(files: list[Path], *, strict=False, total: int | None = None):
    items = sorted(((*_range(p, strict=strict), p) for p in files), key=lambda x: x[:2])
    previous = 0
    for i, (start, end, path) in enumerate(items):
        if i and start < previous:
            raise ValueError("Overlapping parse outputs: explicit legacy selection is required")
        if total is not None and start != previous:
            raise ValueError(f"Incomplete page coverage before {path.name}")
        previous = end
    if total is not None and (not items or previous != total):
        raise ValueError("Parse outputs do not cover the complete source PDF")
    return items


def _inside(folder: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(folder.resolve()) or not resolved.is_file():
        raise ValueError(f"Selected output is missing or outside output folder: {path}")
    return resolved


def selected_json_files(out_dir: Path) -> list[Path]:
    """Read active generation, or accept only unambiguous nonoverlapping legacy JSON."""
    out_dir = Path(out_dir)
    active = out_dir / MANIFEST
    if not active.exists():
        files = [p for p in out_dir.glob("*.json") if not p.name.startswith("_")]
        return [p for _, _, p in _ordered(files)]
    try:
        manifest = json.loads(active.read_text(encoding="utf-8"))
        if manifest["version"] != 1:
            raise ValueError("Unsupported active parse manifest version")
        source = manifest["source"]
        source_path = Path(source["path"])
        if not source_path.is_file() or sha256_of(source_path) != source["sha256"]:
            raise ValueError("Source PDF changed or is missing; parse again before reading outputs")
        if type(source["pages"]) is not int or len(PdfReader(source_path).pages) != source["pages"]:
            raise ValueError("Source PDF page count differs from active manifest")
        files = []
        entries = manifest["files"]
        for entry in entries:
            path = _inside(out_dir, out_dir / entry["path"])
            if sha256_of(path) != entry["sha256"]:
                raise ValueError(f"Parse output content changed: {path.name}")
            if _range(path, strict=True) != (entry["page_start"], entry["page_end"]):
                raise ValueError(f"Parse output range changed: {path.name}")
            files.append(path)
        items = _ordered(files, strict=True, total=source["pages"])
        return [p for _, _, p in items]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid active parse manifest: {exc}") from exc


def adopt_legacy(
    out_dir: Path, files: list[Path], source_pdf: Path, *, origin: str = "legacy_adoption"
) -> Path:
    """Atomically publish an explicitly selected, full-coverage parse generation.

    Used both for legacy adoption and freshly produced isolated runs. No source
    artifact is removed. Call only once all parse units have succeeded.
    """
    out_dir, source_pdf = Path(out_dir).resolve(), Path(source_pdf).resolve()
    source_hash = sha256_of(source_pdf)
    total = len(PdfReader(source_pdf).pages)
    resolved = [_inside(out_dir, Path(p)) for p in files]
    items = _ordered(resolved, strict=True, total=total)
    payload = {
        "version": 1,
        "origin": origin,
        "provenance_note": (
            "Legacy selection verified for coverage/content; original parser execution "
            "and source identity at that execution cannot be certified retroactively."
            if origin == "legacy_adoption" else "Published after complete isolated parser run."
        ),
        "source": {"path": str(source_pdf), "sha256": source_hash, "pages": total},
        "files": [
            {"path": p.relative_to(out_dir).as_posix(), "page_start": start,
             "page_end": end, "sha256": sha256_of(p)}
            for start, end, p in items
        ],
    }
    if sha256_of(source_pdf) != source_hash:
        raise ValueError("Source PDF changed while verifying parse outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    temp = out_dir / f"_active_parse.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, out_dir / MANIFEST)
    finally:
        temp.unlink(missing_ok=True)
    return out_dir / MANIFEST
