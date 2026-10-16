"""Propose source-table candidates from literal multi-cell row witnesses.

This is a narrowing aid, not a HWP-to-PDF identity certificate. Neither PDF
page numbers nor document-specific labels, dimensions, hashes, or silver IDs
participate in matching. The caller must independently verify a candidate's
source-to-PDF region before using it as a ``verified_link``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any


def _walk_tables(node: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        if node.get("kind") == "table":
            yield path, node
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                yield from _walk_tables(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, (dict, list)):
                yield from _walk_tables(value, f"{path}/{index}")


def _cell_text(cell: dict[str, Any]) -> str:
    blocks = cell.get("blocks")
    if not isinstance(blocks, list):
        return ""
    # The separator is used solely for candidate matching; no source text is
    # rewritten or exported from this module.
    return " ".join(
        block["text"] for block in blocks
        if isinstance(block, dict)
        and block.get("kind") in {"paragraph", "list_item"}
        and isinstance(block.get("text"), str)
    ).strip()


def _source_tables(ir: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(ir, dict) or not isinstance(ir.get("body"), list):
        return []
    result = []
    for pointer, table in _walk_tables(ir["body"], "/body"):
        rows: dict[int, dict[int, str]] = {}
        for cell in table.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            row, col = cell.get("row"), cell.get("col")
            if type(row) is int and type(col) is int and row >= 0 and col >= 0:
                rows.setdefault(row, {})[col] = _cell_text(cell)
        components = pointer.split("/")
        body_index = (
            int(components[2]) if len(components) == 3
            and components[1] == "body" and components[2].isdigit() else None
        )
        result.append({"pointer": pointer, "body_index": body_index, "rows": rows})
    return result


def _prefix_cell(pdf_text: str, source_text: str) -> bool:
    return pdf_text == source_text or pdf_text.startswith(source_text + " ")


def _has_number(text: str) -> bool:
    return any(character.isdigit() for character in text)


def _row_witnesses(pdf_row: list[str], source_cells: dict[int, str]) -> list[dict[str, Any]]:
    label = source_cells.get(0, "")
    if len(label) < 6 or not pdf_row or not _prefix_cell(pdf_row[0], label):
        return []
    matches = []
    for col in range(1, len(pdf_row)):
        value = source_cells.get(col, "")
        if value and _has_number(value) and pdf_row[col]:
            if not _prefix_cell(pdf_row[col], value):
                return []
            matches.append({"column": col, "source_value": value})
    return matches


def _data_rows(pdf_table: dict[str, Any]) -> list[tuple[int, list[str]]]:
    found = []
    rows = pdf_table.get("rows")
    if not isinstance(rows, list):
        return found
    for index, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        cells = [str(value).strip() if value is not None else "" for value in row]
        if (cells and len(cells[0]) >= 6
                and sum(_has_number(cell) for cell in cells[1:]) >= 3):
            found.append((index, cells))
    return found


def _probe_one(pdf_table: dict[str, Any], pdf_index: int,
               sources: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _data_rows(pdf_table)
    candidates = []
    for source in sources:
        matched_rows = []
        for pdf_row_index, pdf_cells in rows:
            row_matches = []
            for source_row_index, source_cells in source["rows"].items():
                numbers = _row_witnesses(pdf_cells, source_cells)
                if len(numbers) >= 3:
                    row_matches.append({
                        "pdf_row": pdf_row_index,
                        "source_row": source_row_index,
                        "source_label": source_cells[0],
                        "numeric_witnesses": numbers,
                    })
            if len(row_matches) == 1:
                matched_rows.append(row_matches[0])
            elif len(row_matches) > 1:
                matched_rows.append({"pdf_row": pdf_row_index, "ambiguous_rows": True})
        if matched_rows:
            candidates.append({"source": source, "row_witnesses": matched_rows})

    complete = [
        candidate for candidate in candidates
        if len(candidate["row_witnesses"]) == len(rows)
        and all("source_row" in item for item in candidate["row_witnesses"])
        and len({item["source_row"] for item in candidate["row_witnesses"]}) == len(rows)
    ] if rows else []
    unique = complete[0] if len(complete) == 1 else None
    return {
        "pdf_table_index": pdf_index,
        "pdf_page_observed_only": pdf_table.get("page"),
        "status": "unique_identity_witness" if unique else "undecidable",
        "identity_confirmed": False,
        "source_body_index": unique["source"]["body_index"] if unique else None,
        "source_pointer": unique["source"]["pointer"] if unique else None,
        "row_witnesses": unique["row_witnesses"] if unique else [],
        "candidate_count": len(candidates),
        "complete_candidate_count": len(complete),
        "data_rows_requiring_witness": [index for index, _ in rows],
        "not_verified": [
            "PDF 표 영역과 원본 표의 독립적인 위치 연결",
            "표 밖 단위·각주와 연속 페이지",
            "전체 행·셀 구조의 충실도",
        ],
    }


def propose_links(ir: dict[str, Any], pdf_tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one bounded candidate report per PDF table, without raw ID lists."""
    if not isinstance(pdf_tables, list):
        raise TypeError("pdf_tables must be a list")
    sources = _source_tables(ir)
    return [
        _probe_one(table, index, sources)
        if isinstance(table, dict) else {
            "pdf_table_index": index, "status": "undecidable",
            "identity_confirmed": False, "source_body_index": None,
            "source_pointer": None, "row_witnesses": [],
            "candidate_count": 0, "complete_candidate_count": 0,
            "data_rows_requiring_witness": [], "not_verified": ["PDF 표 형식"],
        }
        for index, table in enumerate(pdf_tables)
    ]


def _literal_occurs_once(haystack: str, needle: str) -> bool:
    """Locate one literal label with token edges, preserving every codepoint."""
    start = haystack.find(needle)
    if start < 0 or haystack.find(needle, start + 1) >= 0:
        return False
    end = start + len(needle)
    return (start == 0 or not haystack[start - 1].isalnum()) and (
        end == len(haystack) or not haystack[end].isalnum()
    )


def suspect_row_collapses(
    ir: dict[str, Any],
    pdf_tables: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Surface local row-boundary suspicions without certifying a table link.

    Only a unique multi-cell identity *candidate* is eligible. Two distinct,
    locally unique source row labels must occur literally in the same PDF cell.
    Even then, this is ``판단불가`` until PDF region identity is independently
    checked; the source or PDF data are not rewritten here.
    """
    if not isinstance(pdf_tables, list) or not isinstance(proposals, list):
        raise TypeError("pdf_tables and proposals must be lists")
    sources = {source["pointer"]: source for source in _source_tables(ir)}
    risks: list[dict[str, Any]] = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        if (proposal.get("status") != "unique_identity_witness"
                or proposal.get("identity_confirmed") is not False
                or proposal.get("complete_candidate_count") != 1):
            continue
        pdf_index = proposal.get("pdf_table_index")
        pointer = proposal.get("source_pointer")
        if (type(pdf_index) is not int or not 0 <= pdf_index < len(pdf_tables)
                or not isinstance(pdf_tables[pdf_index], dict)
                or pointer not in sources):
            continue
        source = sources[pointer]
        labels = {
            row: cells.get(0, "")
            for row, cells in source["rows"].items()
            if len(cells.get(0, "")) >= 6
        }
        frequency = Counter(labels.values())
        pdf_table = pdf_tables[pdf_index]
        pdf_rows = pdf_table.get("rows")
        if not isinstance(pdf_rows, list):
            continue
        for pdf_row, cells in enumerate(pdf_rows):
            if not isinstance(cells, list):
                continue
            for pdf_col, value in enumerate(cells):
                if not isinstance(value, str):
                    continue
                witnesses = [
                    {"source_row": row, "source_col": 0, "text": label}
                    for row, label in labels.items()
                    if frequency[label] == 1 and _literal_occurs_once(value, label)
                ]
                if len(witnesses) < 2:
                    continue
                risks.append({
                    "code": "row_boundary_collapse_suspected",
                    "status": "판단불가",
                    "identity_confirmed": False,
                    "source_locator": {
                        "body_index": source["body_index"],
                        "pointer": pointer,
                        "rows": [item["source_row"] for item in witnesses],
                    },
                    "pdf_locator": {
                        "table_index": pdf_index,
                        "page": pdf_table.get("page"),
                        "cell": [pdf_row, pdf_col],
                    },
                    "witnesses": witnesses,
                    "reason": (
                        "서로 다른 원본 행 이름이 PDF 한 셀에 함께 나타납니다. "
                        "표 영역의 동일성은 미확정입니다."
                    ),
                    "not_verified": ["원본 표와 PDF 표 영역의 독립적 동일성", "전체 표 행·셀 구조"],
                })
    return risks
