"""Lossless parser-block RAG export; does not claim to repair source paragraphs."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from pathlib import Path

from .normalize import sha256_of
from .parsed_store import CHILD_KEYS, page_offset, selected_json_files
from .parsed_store import _nodes as walk_nodes


def _nodes(el: dict, pointer: str = ""):
    yield el, pointer
    # Tables own their entire subtree. Cell paragraphs must not become body chunks.
    if el.get("type") == "table":
        return
    for key in CHILD_KEYS:
        for index, child in enumerate(el.get(key) or []):
            if isinstance(child, dict):
                yield from _nodes(child, f"{pointer}/{key}/{index}")


def _table_text(table: dict) -> str:
    # Text is for retrieval. Cell boundaries and spans live in the raw structure.
    return "\n".join(
        node["content"] for node in walk_nodes(table) if isinstance(node.get("content"), str)
    )


def export_document(
    out_dir: Path,
    doc_name: str,
    pdf: Path,
    image_dir: Path,
    dst: Path,
    max_chars: int = 1800,
) -> dict:
    """Export atomic text/table blocks. max_chars is a soft limit, never a cutter.

    Original strings and table structures remain unchanged. Parser-level mistakes
    (merged paragraphs or page-broken sentences) remain visible for human review.
    Image links only establish an existing same-page file, not table identity or
    content validity. No embeddings or retrieval database are created.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    selected = selected_json_files(out_dir)
    if not selected:
        raise ValueError("No selected parsed JSON files")
    records = []
    headings = []
    coverage: Counter = Counter()
    source_files = []
    pdf_hash = sha256_of(pdf) if pdf.is_file() else None
    for path in selected:
        raw = path.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        offset = page_offset(path)
        declared = data.get("number of pages")
        if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
            coverage.update(range(offset + 1, offset + declared + 1))
        source_files.append({"file": path.name, "sha256": fingerprint})
        for element, pointer in _nodes(data):
            kind = element.get("type", "unknown")
            content = element.get("content")
            if kind != "table" and not isinstance(content, str):
                continue
            local_page = element.get("page number")
            valid_page = (
                isinstance(local_page, int)
                and not isinstance(local_page, bool)
                and local_page > 0
                and (not isinstance(declared, int) or local_page <= declared)
            )
            page = offset + local_page if valid_page else None
            text = _table_text(element) if kind == "table" else content
            flags = []
            if page is None:
                flags.append("missing_page" if local_page is None else "invalid_page")
            if len(text) > max_chars:
                flags.append("oversized_block")
            identity = json.dumps(
                [doc_name, path.name, fingerprint, pointer], ensure_ascii=False
            ).encode("utf-8")
            record_id = hashlib.sha256(identity).hexdigest()[:24]
            if kind == "heading":
                level = element.get("heading level")
                if not isinstance(level, int) or isinstance(level, bool) or level < 1:
                    level = 1
                    flags.append("unknown_heading_level")
                headings = [h for h in headings if h["level"] < level]
                headings.append(
                    {"text": text, "level": level, "page": page, "record_id": record_id}
                )
            record = {
                "id": record_id,
                "kind": kind,
                "text": text,
                "source": {
                    "document": doc_name,
                    "pdf_path": str(pdf.resolve()),
                    "pdf_sha256": pdf_hash,
                    "json_file": path.name,
                    "json_path": str(path.resolve()),
                    "json_sha256": fingerprint,
                    "element_id": element.get("id"),
                    "element_path": pointer,
                    "local_page": local_page,
                    "page": page,
                    "bbox": element.get("bounding box"),
                    "bbox_coordinates": "PDF bottom-left points",
                },
                "heading_context": list(headings),
                "flags": flags,
                "review_required": kind == "table" or page is None,
                "image_candidates": [],
                "previous_id": None,
                "next_id": None,
            }
            if kind == "table":
                record["structure"] = element
                record["flags"].append("table_structure_requires_review")
                if page is not None:
                    record["image_candidates"] = [
                        {
                            "path": str(image.resolve()),
                            "match": "same_page_only",
                            "content_verified": False,
                            "source_fingerprint_verified": False,
                        }
                        for image in sorted(image_dir.glob(f"p{page:04d}_t*.png"))
                        if image.is_file()
                    ]
            records.append(record)

    for index, record in enumerate(records):
        if index:
            record["previous_id"] = records[index - 1]["id"]
        if index + 1 == len(records):
            continue
        following = records[index + 1]
        record["next_id"] = following["id"]
        a, b = record["source"]["page"], following["source"]["page"]
        if (
            a is not None
            and b == a + 1
            and record["kind"] in {"paragraph", "list item", "text block"}
            and following["kind"] in {"paragraph", "list item", "text block"}
        ):
            record["flags"].append("possible_continuation_next_page")
            following["flags"].append("possible_continuation_previous_page")

    # A legacy partial parse cannot establish that the complete PDF was covered.
    page_count = None
    if pdf.is_file():
        from pypdf import PdfReader

        page_count = len(PdfReader(str(pdf)).pages)
    complete = page_count is not None and coverage == Counter(range(1, page_count + 1))
    manifest = {
        "schema_version": 1,
        "document": doc_name,
        "pdf_sha256": pdf_hash,
        "pdf_pages": page_count,
        "selected_json_files": [p.name for p in selected],
        "sources": source_files,
        "record_count": len(records),
        "kind_counts": dict(Counter(r["kind"] for r in records)),
        "max_chars_soft_limit": max_chars,
        "oversized_blocks": sum("oversized_block" in r["flags"] for r in records),
        "coverage_status": "complete" if complete else "partial_or_unverified",
        "covered_pages": sorted(coverage),
        "policy": "Atomic parser blocks; original paragraph boundaries are not repaired",
        "semantic_paragraph_reconstruction": False,
        "image_policy": "Existing same-page candidates; exact table association unverified",
    }
    dst.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    staging = dst / f".staging_{run_id}"
    published = dst / f"run_{run_id}"
    staging.mkdir()
    (staging / "chunks.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Only publish a directory after both artifacts exist; retain failed staging
    # for diagnosis, and never overwrite evidence from a previous/concurrent run.
    staging.rename(published)
    jsonl_path = published / "chunks.jsonl"
    manifest_path = published / "manifest.json"
    return {
        "jsonl_path": str(jsonl_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "record_count": len(records),
        "kind_counts": manifest["kind_counts"],
        "coverage_status": manifest["coverage_status"],
        "oversized_blocks": manifest["oversized_blocks"],
    }
