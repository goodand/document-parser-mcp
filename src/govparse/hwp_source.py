"""Author: chatggpt_gpt6_extrahigh. Preserve rhwp's source structure.

The IR is a source artifact. In particular, ``prov.para_idx`` is rhwp's own
index and must not be joined numerically to a low-level HWP paragraph index.
The IR does not establish a PDF page or bounding box.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .normalize import sha256_of

DOCUMENT = "document.json"
MANIFEST = "_source_manifest.json"


def _rhwp_parse(path: Path) -> Any:
    """Lazy import keeps PDF-only installations usable."""
    try:
        import rhwp
    except ImportError as exc:
        raise RuntimeError(
            "HWP 원본 구조 추출에 rhwp-python이 필요합니다. "
            "프로젝트 환경에 rhwp-python을 설치하세요."
        ) from exc
    return rhwp.parse(str(path))


def _write_atomic(path: Path, payload: bytes) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _counts(ir: dict[str, Any]) -> dict[str, int]:
    body = ir.get("body")
    if not isinstance(body, list):
        raise ValueError("rhwp IR에 body 배열이 없습니다")

    counts = {"paragraphs": 0, "all_paragraphs": 0, "tables": 0, "cells": 0,
              "list_items": 0}

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind == "paragraph":
            counts["all_paragraphs"] += 1
        elif kind == "table":
            counts["tables"] += 1
            cells = node.get("cells")
            if isinstance(cells, list):
                counts["cells"] += len(cells)
        elif kind == "list_item":
            counts["list_items"] += 1
        for key in ("blocks", "cells", "body"):
            if key in node:
                visit(node[key])

    counts["paragraphs"] = sum(
        isinstance(node, dict) and node.get("kind") == "paragraph" for node in body
    )
    visit(body)
    return counts


def _cached_manifest(src: Path, out_dir: Path, source_hash: str) -> dict[str, Any] | None:
    manifest_path = out_dir / MANIFEST
    document_path = out_dir / DOCUMENT
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("version") != 1
            or manifest.get("source_path") != str(src)
            or manifest.get("source_sha256") != source_hash
            or manifest.get("document_path") != DOCUMENT
            or not document_path.is_file()
            or sha256_of(document_path) != manifest.get("document_sha256")
            or document_path.stat().st_size != manifest.get("document_bytes")
        ):
            return None
        return manifest
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def extract_source(src: Path, out_dir: Path) -> dict[str, Any]:
    """Extract a HWP/HWPX IR unchanged, with a source-bound cache manifest.

    The result describes original structure only. It intentionally contains no
    inferred PDF page or location. The PDF parser's active manifest remains
    independent of this sidecar.
    """
    src = Path(src).resolve()
    out_dir = Path(out_dir).resolve()
    if not src.is_file():
        raise FileNotFoundError(src)
    if src.suffix.lower() not in {".hwp", ".hwpx"}:
        raise ValueError(f"HWP/HWPX 파일이 아닙니다: {src}")
    source_hash = sha256_of(src)
    cached = _cached_manifest(src, out_dir, source_hash)
    if cached is not None:
        return cached

    ir_text = _rhwp_parse(src).to_ir_json(indent=2)
    if not isinstance(ir_text, str):
        raise TypeError("rhwp to_ir_json 결과가 문자열이 아닙니다")
    ir = json.loads(ir_text)
    if not isinstance(ir, dict):
        raise ValueError("rhwp IR 루트가 객체가 아닙니다")
    counts = _counts(ir)
    if sha256_of(src) != source_hash:
        raise ValueError("HWP 원본이 추출 중 변경되었습니다")

    payload = ir_text.encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(out_dir / DOCUMENT, payload)
    if sha256_of(src) != source_hash:
        raise ValueError("HWP 원본이 저장 중 변경되었습니다")
    manifest = {
        "version": 1,
        "author": "chatggpt_gpt6_extrahigh",
        "source_path": str(src),
        "source_sha256": source_hash,
        "document_path": DOCUMENT,
        "document_sha256": hashlib.sha256(payload).hexdigest(),
        "document_bytes": len(payload),
        "schema_name": ir.get("schema_name"),
        "schema_version": ir.get("schema_version"),
        "counts": counts,
        "provenance_contract": {
            "para_idx": "rhwp index; not native HWP paragraph index",
            "pdf_page": "not inferred",
        },
    }
    _write_atomic(
        out_dir / MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return manifest
