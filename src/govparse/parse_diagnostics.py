"""Conservative, source-scoped comparison of HWP and PDF table structures.

Text resemblance is a candidate for a table link, not proof of one. A confirmed
PDF structure defect requires an independently verified table link and two
unique source-cell witnesses from different source rows in one PDF cell.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _source_tables(ir: dict[str, Any]) -> dict[int, dict[str, Any]]:
    body = ir.get("body") if isinstance(ir, dict) else None
    if not isinstance(body, list):
        return {}
    return {
        index: block
        for index, block in enumerate(body)
        if isinstance(block, dict) and block.get("kind") == "table"
    }


def _cell_text(cell: dict[str, Any]) -> str:
    """Read direct text blocks only; a nested table is a different structure."""
    blocks = cell.get("blocks")
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        block["text"]
        for block in blocks
        if isinstance(block, dict)
        and block.get("kind") in {"paragraph", "list_item"}
        and isinstance(block.get("text"), str)
    )


def _witnesses(
    tables: dict[int, dict[str, Any]],
) -> tuple[dict[int, list[dict[str, Any]]], Counter[str]]:
    by_table: dict[int, list[dict[str, Any]]] = {}
    occurrences: Counter[str] = Counter()
    for body_index, table in tables.items():
        found = []
        for cell in table.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            row, col = cell.get("row"), cell.get("col")
            if type(row) is not int or type(col) is not int or row < 0 or col < 0:
                continue
            value = _cell_text(cell)
            if not value or not value.strip():
                continue
            # A source cell spanning rows does not witness a row boundary.
            if cell.get("row_span", 1) != 1:
                continue
            found.append({"text": value, "row": row, "col": col})
            occurrences[value] += 1
        by_table[body_index] = found
    return by_table, occurrences


def _exact_occurs_once(haystack: str, needle: str) -> bool:
    """Require the literal codepoints with word boundaries; never normalize."""
    start = haystack.find(needle)
    if start < 0 or haystack.find(needle, start + 1) >= 0:
        return False
    end = start + len(needle)
    return (start == 0 or not haystack[start - 1].isalnum()) and (
        end == len(haystack) or not haystack[end].isalnum()
    )


def _pdf_cells(table: dict[str, Any]):
    for row_index, row in enumerate(table.get("rows") or []):
        if not isinstance(row, list):
            continue
        for col_index, value in enumerate(row):
            if isinstance(value, str) and value:
                yield row_index, col_index, value


def _matched_witnesses(
    value: str, candidates: list[dict[str, Any]], occurrences: Counter[str]
) -> list[dict[str, Any]]:
    return [
        witness
        for witness in candidates
        if occurrences[witness["text"]] == 1
        and _exact_occurs_once(value, witness["text"])
    ]


def _has_two_rows(matched: list[dict[str, Any]]) -> bool:
    return len({item["row"] for item in matched}) >= 2


def _evidence_present(link: dict[str, Any]) -> bool:
    evidence = link.get("evidence")
    if isinstance(evidence, str):
        return bool(evidence.strip())
    return bool(evidence) if isinstance(evidence, (dict, list)) else False


def _exact_matrix(table: dict[str, Any], pdf_table: dict[str, Any]) -> bool:
    """Certify only a literal, unmerged cell matrix; other layouts stay held."""
    rows, cols = table.get("rows"), table.get("cols")
    pdf_rows = pdf_table.get("rows")
    if (
        type(rows) is not int or type(cols) is not int
        or rows < 1 or cols < 1
        or not isinstance(pdf_rows, list) or len(pdf_rows) != rows
        or any(not isinstance(row, list) or len(row) != cols for row in pdf_rows)
    ):
        return False
    source_cells = table.get("cells")
    if not isinstance(source_cells, list) or len(source_cells) != rows * cols:
        return False
    seen: set[tuple[int, int]] = set()
    for cell in source_cells:
        if not isinstance(cell, dict):
            return False
        row, col = cell.get("row"), cell.get("col")
        if (
            type(row) is not int or type(col) is not int
            or not 0 <= row < rows or not 0 <= col < cols
            or (row, col) in seen
            or cell.get("row_span", 1) != 1 or cell.get("col_span", 1) != 1
            or pdf_rows[row][col] != _cell_text(cell)
        ):
            return False
        seen.add((row, col))
    return True


def diagnose_tables(
    ir: dict[str, Any],
    pdf_tables: list[dict[str, Any]],
    verified_links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Diagnose only table-boundary claims supported by local witnesses.

    ``verified_links`` must come from a separate source-to-PDF region check.
    This function does not infer a link from text, a page number, or a hash.
    The output never certifies units, footnotes, or a business similarity call.
    """
    source_tables = _source_tables(ir)
    if not isinstance(pdf_tables, list):
        raise TypeError("pdf_tables must be a list")
    by_source, occurrences = _witnesses(source_tables)
    diagnostics: list[dict[str, Any]] = []

    candidates: dict[int, set[int]] = {}
    for pdf_index, pdf_table in enumerate(pdf_tables):
        if not isinstance(pdf_table, dict):
            continue
        candidates[pdf_index] = set()
        for _, _, value in _pdf_cells(pdf_table):
            for source_index, witnesses in by_source.items():
                if any(_exact_occurs_once(value, w["text"]) for w in witnesses):
                    candidates[pdf_index].add(source_index)

    links: dict[int, dict[str, Any]] = {}
    link_conflicts: set[int] = set()
    for link in verified_links or []:
        if not isinstance(link, dict):
            continue
        source_index = link.get("source_body_index")
        pdf_index = link.get("pdf_table_index")
        if (
            type(source_index) is not int
            or type(pdf_index) is not int
            or source_index not in source_tables
            or pdf_index < 0
            or pdf_index >= len(pdf_tables)
            or not isinstance(pdf_tables[pdf_index], dict)
            or not _evidence_present(link)
        ):
            continue
        if pdf_index in links and links[pdf_index]["source_body_index"] != source_index:
            link_conflicts.add(pdf_index)
        else:
            links[pdf_index] = link

    defects_found = False
    unresolved = False
    for pdf_index, pdf_table in enumerate(pdf_tables):
        if not isinstance(pdf_table, dict):
            continue
        page = pdf_table.get("page")
        linked = links.get(pdf_index) if pdf_index not in link_conflicts else None
        if linked is None:
            # Do not mark every unlinked, otherwise normal unrelated tables
            # become needless review work. Report a concrete ambiguous match.
            potential_collapse = False
            for _, _, value in _pdf_cells(pdf_table):
                for witnesses in by_source.values():
                    if _has_two_rows(_matched_witnesses(value, witnesses, occurrences)):
                        potential_collapse = True
                        break
                if potential_collapse:
                    break
            # Shared values are common in budget tables. Candidate count alone
            # is not an error or a useful alert on an otherwise normal table.
            if potential_collapse or pdf_index in link_conflicts:
                diagnostics.append({
                    "code": "table_match_ambiguous",
                    "status": "판단불가",
                    "source_locator": None,
                    "pdf_locator": {"table_index": pdf_index, "page": page},
                    "reason": "원본 표와 PDF 표의 독립적인 위치 연결이 확정되지 않았습니다.",
                    "weak_text_candidate_count": len(candidates.get(pdf_index, ())),
                })
                unresolved = True
            continue

        source_index = linked["source_body_index"]
        witnesses = by_source[source_index]
        affected: list[dict[str, Any]] = []
        for row, col, value in _pdf_cells(pdf_table):
            matched = _matched_witnesses(value, witnesses, occurrences)
            if not _has_two_rows(matched):
                continue
            affected.append({"cell": [row, col], "witnesses": matched})
        if affected:
            diagnostics.append({
                "code": "row_boundary_collapse",
                "status": "문제있음",
                "source_locator": {"body_index": source_index,
                                   "cells": [list(position) for position in sorted({
                                       (w["row"], w["col"])
                                       for event in affected
                                       for w in event["witnesses"]})]},
                "pdf_locator": {"table_index": pdf_index, "page": page,
                                "cell": affected[0]["cell"],
                                "affected_cells": [event["cell"] for event in affected]},
                "witnesses": affected,
                "link_evidence": linked["evidence"],
                "reason": "PDF 셀에 원본의 서로 다른 행에 속한 고유 텍스트가 결합됐습니다.",
            })
            defects_found = True

    pdf_structure_usable = (
        bool(pdf_tables) and len(links) == len(pdf_tables)
        and not link_conflicts and not defects_found and not unresolved
        and all(_exact_matrix(source_tables[link["source_body_index"]], pdf_tables[index])
                for index, link in links.items())
    )
    held_claims: list[dict[str, str]] = [{
        "claim": "automatic_similarity_or_difference",
        "reason": "표 밖 단위, 각주, 사업 문맥 및 판단 조건을 이 진단에서 검증하지 않았습니다.",
    }]
    if not pdf_structure_usable:
        held_claims.append({
            "claim": "pdf_table_structure",
            "reason": "표 대응 또는 PDF의 원본 행·셀 경계를 확정할 수 없습니다.",
        })
    return {
        "diagnostics": diagnostics,
        "use_decision": {
            "source_hwp_retrieval": "usable" if source_tables else "held",
            "pdf_table_structure": "usable" if pdf_structure_usable else "held",
            "automatic_judgment": "held",
            "held_claims": held_claims,
        },
    }
