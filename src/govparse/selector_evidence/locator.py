"""Locate native table content on 1-based PDF pages using corroborated cell text."""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in normalized if char.isalnum())


class PageIndex:
    """Reuse the normalized text and phrase lookup cache across tables in one PDF."""

    def __init__(self, pdf: str | Path | pymupdf.Document):
        if isinstance(pdf, pymupdf.Document):
            self.pages = self._read_pages(pdf)
        else:
            with pymupdf.open(pdf) as document:
                self.pages = self._read_pages(document)
        self._matches: dict[str, tuple[int, ...]] = {}

    @staticmethod
    def _read_pages(document):
        # Original PDF text order often keeps a cell together. Position-sorted
        # text helps when separate glyphs or text runs were painted out of order.
        return [
            tuple(dict.fromkeys(_normalize(page.get_text(sort=sort)) for sort in (False, True)))
            for page in document
        ]

    def matches(self, phrase: str) -> tuple[int, ...]:
        if phrase not in self._matches:
            self._matches[phrase] = tuple(
                index
                for index, streams in enumerate(self.pages, 1)
                if any(phrase in stream for stream in streams)
            )
        return self._matches[phrase]


def _source_cells(table, include_nested=False):
    for cell in table.get("cells", ()):
        blocks = cell.get("blocks", cell.get("paragraphs", ()))
        parts = []
        for block in blocks:
            if block.get("kind") == "table":
                if include_nested:
                    yield from _source_cells(block, include_nested=True)
            elif block.get("kind", "paragraph") == "paragraph":
                part = _normalize(block.get("text") or "")
                if part:
                    parts.append(part)
        if parts:
            yield parts


def _phrases(parts):
    # Disjoint evidence is measured later in this cell's source coordinates.
    # Short wrapped labels can span multiple native paragraphs.
    if len(parts) > 1 and sum(map(len, parts)) <= 18:
        parts = ["".join(parts)]
    offset = 0
    for part in parts:
        size = len(part)
        if 4 <= size <= 18:
            if any(char.isalpha() for char in part):
                yield offset, part
        elif size > 18:
            width = 12
            starts = [*range(0, size - width + 1, width // 2), size - width]
            for start in dict.fromkeys(starts):
                phrase = part[start : start + width]
                if any(char.isalpha() for char in phrase):
                    yield offset + start, phrase
        offset += size + 1


def _cell_evidence(parts, index):
    candidates = defaultdict(list)
    for offset, phrase in _phrases(parts):
        pages = index.matches(phrase)
        if len(pages) == 1:
            candidates[pages[0]].append((offset, phrase))
    result = {}
    for page, hits in candidates.items():
        phrases = []
        end = -1
        for offset, phrase in hits:
            if offset >= end and phrase not in phrases:
                phrases.append(phrase)
                end = offset + len(phrase)
        result[page] = phrases
    return result


def _row_evidence(table, index):
    """Match complete ordered rows, including numeric continuations of long tables."""
    rows = defaultdict(list)
    for cell in sorted(table.get("cells", ()), key=lambda c: (c.get("row", 0), c.get("col", 0))):
        blocks = cell.get("blocks", cell.get("paragraphs", ()))
        value = _normalize(
            "".join(
                block.get("text") or ""
                for block in blocks
                if block.get("kind", "paragraph") == "paragraph"
            )
        )
        if value:
            rows[cell.get("row", 0)].append(value)
    matches = defaultdict(dict)
    for values in rows.values():
        phrase = "".join(values)
        distinct = len(set(values))
        if distinct < 2 or not 8 <= len(phrase) <= 160:
            continue
        pages = index.matches(phrase)
        if len(pages) == 1:
            matches[pages[0]][phrase] = distinct
    return matches


def _score(table, index, include_nested=False):
    scores = defaultdict(list)
    for cell_number, parts in enumerate(_source_cells(table, include_nested)):
        evidence = _cell_evidence(parts, index)
        cover = {p: sum(map(len, phrases)) for p, phrases in evidence.items()}
        strongest = max(cover.values(), default=0)
        for page, phrases in evidence.items():
            # A stray short mention in another section must not locate this cell.
            if cover[page] * 3 < strongest:
                continue
            scores[page].append((cell_number, phrases))

    qualified = {}
    rows = _row_evidence(table, index)
    for page in scores.keys() | rows.keys():
        hits = scores[page]
        # Repeated labels in different cells are one witness. At least two
        # distinct cells or substantial independent text within a cell is needed.
        frequencies = Counter(phrase for _, phrases in hits for phrase in phrases)
        specific_cells = [
            number
            for number, phrases in hits
            if any(frequencies[phrase] == 1 for phrase in phrases)
        ]
        unique = list(frequencies)
        page_rows = rows[page]
        row_support = len(page_rows) >= 2 or any(
            count >= 3 and len(phrase) >= 15 for phrase, count in page_rows.items()
        )
        if (
            row_support
            or (len(specific_cells) >= 2 and sum(map(len, unique)) >= 12)
            or any(len(phrases) >= 2 and sum(map(len, phrases)) >= 22 for _, phrases in hits)
        ):
            qualified[page] = {
                "page": page,
                "supporting_cells": len(hits),
                "supporting_rows": len(page_rows),
                "matched_characters": max(sum(map(len, unique)), sum(map(len, page_rows))),
                "example_anchors": sorted(unique or list(page_rows), key=len, reverse=True)[:6],
            }
    return qualified


def locate_with_evidence(table: dict, pdf) -> dict:
    """Return verified text matches, their scope, and the reason for abstention.

    Accepts a November sidecar table or October C3 native table. Image-only
    objects and PDFs lacking usable text can yield no match. A matched page
    establishes presence of cell text; image coverage requires separate review.
    """
    index = pdf if isinstance(pdf, PageIndex) else PageIndex(pdf)
    scope = "direct_cells"
    scores = _score(table, index)
    if not scores and any(
        b.get("kind") == "table" for c in table.get("cells", ()) for b in c.get("blocks", ())
    ):
        scope = "including_nested_cells"
        scores = _score(table, index, include_nested=True)
    if not scores:
        return {"pages": [], "scope": scope, "reason": "insufficient_unique_text", "evidence": []}

    # Competing matches in distant parts of a document may be summary/reprint
    # mentions. Require the leading contiguous span to dominate in that case.
    groups = []
    for page in sorted(scores):
        if not groups or page != groups[-1][-1] + 1:
            groups.append([])
        groups[-1].append(page)

    def strength(group):
        return sum(scores[page]["matched_characters"] for page in group)

    groups.sort(key=strength, reverse=True)
    if len(groups) > 1 and strength(groups[0]) < 2 * strength(groups[1]):
        return {
            "pages": [],
            "scope": scope,
            "reason": "ambiguous_nonadjacent_spans",
            "evidence": [scores[p] for p in sorted(scores)],
        }
    pages = groups[0]
    return {
        "pages": pages,
        "scope": scope,
        "reason": "matched",
        "evidence": [scores[p] for p in pages],
    }


def locate(table: dict, pdf) -> list[int]:
    """Return ascending 1-based matched pages, or [] when evidence is insufficient."""
    return locate_with_evidence(table, pdf)["pages"]
