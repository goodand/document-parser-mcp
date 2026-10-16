"""Text-only source projection with conservative, lexical section annotations.

Source paragraphs are never joined or rewritten. Section context is an annotation,
not a claim that visually separated text has been semantically reconstructed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from .hwp_rag import _paragraphs

_PREFIX = r"\s*(?:(?:\d+[.)]|[①-⑳]|[□○◦ㅇ▪•])\s*)?"
_TARGET_LABEL = r"사업\s*목적(?:\s*[·ㆍ]\s*내용)?|지원\s*대상|지원\s*방식"
_TARGET = re.compile(
    rf"^{_PREFIX}(?P<label>{_TARGET_LABEL})"
    r"(?:(?P<colon>\s*[:：])(?P<content>.*)|\s*)$",
    re.DOTALL,
)
_PAREN_TARGET = re.compile(
    rf"^{_PREFIX}\((?P<label>{_TARGET_LABEL})\)\s*(?P<content>.*)$", re.DOTALL,
)
_NUMBERED = re.compile(r"^\s*(?:\d+[.)]|[①-⑳]|[가-하][.)])\s*\S")
_LABEL = re.compile(rf"^{_PREFIX}[^\n:：]{{1,40}}\s*[:：]")
_PAREN_LABEL = re.compile(rf"^{_PREFIX}\([^\n()]{{1,40}}\)")
_CATEGORIES = {"사업목적": "사업목적", "지원대상": "지원대상", "지원방식": "지원 방식"}
_BUSINESS_HEADER = re.compile(r"^\s*사\s*업\s*명\s*\n\s*\((\d+)\)\s*([^\n]+)")


def _business_headers(ir: dict[str, Any], source_hash: str) -> list[dict[str, Any]]:
    """Recognize explicit numbered business headers in top-level tables only.

    These identify business-description candidates, not a count of grant programs.
    Nested tables and mentions within body prose cannot open a business scope.
    """
    body = ir.get("body")
    if not isinstance(body, list):
        raise ValueError("rhwp IR에 body 배열이 없습니다")
    candidates = []
    for body_index, block in enumerate(body):
        if not isinstance(block, dict) or block.get("kind") != "table":
            continue
        text = block.get("text")
        match = _BUSINESS_HEADER.match(text) if isinstance(text, str) else None
        if not match:
            continue
        candidates.append({
            "header_body_index": body_index,
            "ordinal": int(match[1]),
            "raw_name": match[2],
            "source_locator": {
                "source_sha256": source_hash,
                "body_index": body_index,
                "json_pointer": f"/body/{body_index}",
            },
            "header_text": match[0],
            "evidence": "explicit_numbered_business_name_in_top_level_table",
        })
    return candidates


def export_structure_aware_text_chunks(
    ir: dict[str, Any], source_hash: str, out_path: Path,
) -> dict[str, Any]:
    """Keep every source paragraph and annotate the three priority sections.

    Table-cell text is retained but held from independent retrieval because cell
    traversal alone cannot establish sentence order or diagram relationships.
    Context lasts until another explicit numbered heading or inline label in the
    same structural scope. Cell headings never change surrounding body context.
    """
    if not isinstance(ir, dict):
        raise ValueError("rhwp IR 루트가 객체가 아닙니다")
    if (not isinstance(source_hash, str) or len(source_hash) != 64
            or any(char not in "0123456789abcdef" for char in source_hash)):
        raise ValueError("source_hash는 소문자 SHA-256이어야 합니다")
    business_candidates = _business_headers(ir, source_hash)
    candidate_index = 0
    business_scope = None
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp = out_path.with_name(f".{out_path.name}.{uuid.uuid4().hex}.tmp")
    contexts: dict[str, dict[str, Any] | None] = {"body": None}
    counts: Counter[str] = Counter()
    section_counts: Counter[str] = Counter()
    digest = hashlib.sha256()
    seen_ids: set[str] = set()
    try:
        with temp.open("xb") as handle:
            for record in _paragraphs(ir, source_hash):
                if record["id"] in seen_ids:
                    raise ValueError(f"중복 문단 위치: {record['id']}")
                seen_ids.add(record["id"])
                body_index = record["source_locator"]["body_index"]
                while (candidate_index < len(business_candidates)
                       and business_candidates[candidate_index]["header_body_index"] <= body_index):
                    business_scope = business_candidates[candidate_index]
                    candidate_index += 1
                    # A header can contain no emitted paragraphs; reset at the
                    # first paragraph after it as well as within a header table.
                    contexts = {"body": None}
                cells = record["source_locator"]["cell_ancestry"]
                scope = cells[-1]["json_pointer"] if cells else "body"
                parent = cells[-2]["json_pointer"] if len(cells) > 1 else "body"
                if scope not in contexts:
                    contexts[scope] = contexts.get(parent)
                text = record["text"]
                target = _TARGET.fullmatch(text)
                parenthetical = _PAREN_TARGET.fullmatch(text) if not target else None
                target = target or parenthetical
                role = "paragraph"
                if target:
                    label = re.sub(r"\s", "", target["label"])
                    category = "사업목적" if label.startswith("사업목적") else _CATEGORIES[label]
                    role = ("inline_label" if parenthetical or target["colon"]
                            else "section_heading")
                    contexts[scope] = {
                        "category": category,
                        "heading_id": record["id"],
                        "heading_text": text,
                        "heading_source_locator": record["source_locator"],
                        "scope": scope,
                        "evidence": "explicit_lexical_label",
                    }
                    section_counts[category] += 1
                elif _NUMBERED.match(text) or _LABEL.match(text) or _PAREN_LABEL.match(text):
                    contexts[scope] = None
                    role = "section_boundary"
                context = contexts[scope]
                reasons = []
                if not text.strip():
                    reasons.append("empty_source_paragraph")
                if cells:
                    reasons.append("table_cell_relationships_unverified")
                heading_only = bool(target and not (target["content"] or "").strip())
                if heading_only:
                    reasons.append("heading_without_body")
                record.update({
                    "mode": "text_only_structure_aware",
                    "schema_version": 1,
                    "chunk_unit": "source_paragraph",
                    "role": role,
                    "section_context": context,
                    "priority_section": context["category"] if context else None,
                    "business_scope": business_scope,
                    "retrieval_usable": bool(text.strip()) and not cells and not heading_only,
                    "review_required": bool(cells),
                    "review_reasons": reasons,
                    "semantic_reconstruction": "not_performed",
                })
                encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                           + "\n").encode("utf-8")
                handle.write(encoded)
                digest.update(encoded)
                counts["chunks"] += 1
                counts["retrieval_usable"] += int(record["retrieval_usable"])
                counts["review_required"] += int(record["review_required"])
                counts["business_scoped_paragraphs"] += int(business_scope is not None)
                if context:
                    counts["priority_section_paragraphs"] += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, out_path)
    finally:
        temp.unlink(missing_ok=True)
    return {
        "mode": "text_only_structure_aware",
        "schema_version": 1,
        "path": str(out_path),
        "source_sha256": source_hash,
        "jsonl_sha256": digest.hexdigest(),
        "chunks": counts["chunks"],
        "retrieval_usable": counts["retrieval_usable"],
        "not_retrieval_usable": counts["chunks"] - counts["retrieval_usable"],
        "review_required": counts["review_required"],
        "priority_section_paragraphs": counts["priority_section_paragraphs"],
        "explicit_section_counts": {name: section_counts[name] for name in _CATEGORIES.values()},
        "file_page_policy": "unassigned",
        "semantic_reconstruction": "not_performed",
        "scope_policy": "body_and_individual_cells",
        "business_structure": {
            "classification": ("multiple_business_sections" if len(business_candidates) >= 2
                               else "single_candidate" if business_candidates else "unknown"),
            "business_section_candidate_count": len(business_candidates),
            "candidates": business_candidates,
            "business_scoped_paragraphs": counts["business_scoped_paragraphs"],
            "unassigned_paragraphs": counts["chunks"] - counts["business_scoped_paragraphs"],
            "scope_policy": "header_body_index_until_next_header",
            "count_semantics": "business_description_candidates_not_grant_programs",
            "detection_policy": "explicit_numbered_business_name_in_top_level_table",
        },
    }
