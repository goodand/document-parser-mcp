"""Conservative safeguards for D decisions from the original table-use rules."""

from __future__ import annotations
import re, unicodedata

ARROWS = set("→←↓↑➜⇒☞↔↕↗↘↙↖➩➨➔➟⬇⬆⟶⟵")
ARROW_ONLY = set("→←↓↑➜⇒☞↔↕↗↘↙↖➩➨➔➟⬇⬆⟶⟵.,;:|")
SIGNATURE = re.compile(r"\(\s*인\s*\)|서명|날인")
PHOTO_CAPTION = re.compile(r"(?:사진|전경|행사\s*사진|사진\s*예시)")
FLOW_TERMS = re.compile(r"모집|선정|운영|추진|접수|심사|결과|수행")


def _cell_text(cell: dict) -> str:
    return " ".join(
        block.get("text") or ""
        for block in cell.get("blocks", [])
        if block.get("kind") == "paragraph"
    ).strip()


def _original(table: dict):
    cells = table.get("cells") or []
    strings = [_cell_text(cell) for cell in cells]
    nr, nc = int(table.get("rows") or 0), int(table.get("cols") or 0)
    n = max(1, len(cells))
    numeric = sum(bool(re.search(r"\d", s)) for s in strings) / n
    arrows = sum(bool(re.search(r"[→←↓↑➜⇒☞]", s)) for s in strings)
    if arrows:
        return ("M" if numeric >= 0.25 else "V"), "original_arrow_rule"
    if n == 1:
        return "V", "original_single_cell_rule"
    blank = sum(not s for s in strings) / n
    merges = sum(int(c.get("row_span", 1)) > 1 or int(c.get("col_span", 1)) > 1 for c in cells) / n
    nested = sum(b.get("kind") == "table" for c in cells for b in c.get("blocks", []))
    wide_note = sum(
        len(s) >= 75 and int(c.get("col_span", 1)) >= nc and nc >= 2 for c, s in zip(cells, strings)
    )
    if nested or wide_note:
        return ("M" if nr >= 3 else "?"), "original_nested_or_wide_note_rule"
    if nr >= 2 and nc >= 2 and blank <= 0.55 and merges <= 0.5:
        return "D", "original_regular_grid_rule"
    return "?", "original_fallback_review"


def _private_use_symbol_only(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    has_private = any(unicodedata.category(ch) == "Co" for ch in text)
    has_word = any(ch.isalnum() or ch.isalpha() or "가" <= ch <= "힣" for ch in text)
    return has_private and not has_word


def classify_with_reason(table: dict) -> tuple[str, str]:
    """Return a label and safeguard reason; only original D decisions are refined."""
    baseline, base_reason = _original(table)
    if baseline != "D":
        return baseline, f"preserved_{base_reason}"
    cells = table.get("cells") or []
    strings = [_cell_text(c) for c in cells]
    nr, nc = int(table.get("rows") or 0), int(table.get("cols") or 0)
    standalone_arrows = sum(
        bool(s)
        and all(ch.isspace() or ch in ARROW_ONLY for ch in s)
        and any(ch in ARROWS for ch in s)
        for s in strings
    )
    if standalone_arrows:
        return "V", "standalone_flow_arrow_cells"
    if sum(bool(PHOTO_CAPTION.search(s)) for s in strings) >= 2:
        return "V", "repeated_photo_captions"
    if sum(_private_use_symbol_only(s) for s in strings) >= 2:
        return "M", "repeated_decorative_private_use_markers"
    if any(("⬇" in s or "↓" in s) and FLOW_TERMS.search(s) for s in strings):
        return "M", "downward_process_sequence"
    long_cells = sum(len(s) >= 50 for s in strings)
    if nc <= 2 and long_cells >= 2 and max(map(len, strings), default=0) >= 150:
        return "M", "multiple_long_prose_cells"
    blank = sum(not s for s in strings) / max(1, len(cells))
    if nc <= 2 and nr <= 7 and blank >= 0.35 and SIGNATURE.search(" ".join(strings)):
        return "M", "sparse_signature_form"
    return "D", "original_regular_grid_no_safety_trigger"


def classify(table: dict) -> str:
    """Return D (data), M (mixed), V (visual/layout), or ? (review)."""
    return classify_with_reason(table)[0]
