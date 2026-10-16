"""표·이미지를 전부 PNG 로 잘라내고, 판독용 메타데이터를 함께 남긴다.

한컴 문서에서 표는 데이터 구조가 아니라 그리기 도구로도 쓰인다 — 셀을 칠해 만든
간트차트, 빈 칸을 채우는 서식, 칸을 나눠 그린 조직도. 이런 표를 markdown 행으로
펴면 의미가 사라진다.

처음에는 '데이터표 / 시각자료'를 규칙으로 갈라 시각자료만 잘라내려 했는데,
실측에서 25건 전부 오분류했다 — 예산표는 셀이 숫자라 짧고 연도·회계구분 때문에
열이 넓어서 '열 많고 셀 짧으면 간트' 규칙에 그대로 걸렸다. 규칙으로 가르는 것을
포기하고 방침을 바꿨다:

  **표와 이미지를 전수 잘라내고, 판독은 멀티모달 모델에 맡긴다.**

텍스트/마크다운 추출은 그대로 두므로 잃는 것이 없고, 표가 사실은 그림이었던
경우도 이미지가 함께 있으니 복구된다. 아래 특징값(numeric_ratio 등)은 추출 여부를
가르는 기준이 아니라, 나중에 판독 우선순위를 매기거나 표본을 고를 때 쓰는
메타데이터다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

MIN_CROP_PT = 24.0  # 이보다 작은 영역은 잘라도 의미 없음
CROP_PAD_PT = 4.0  # 잘라낼 때 여백
RENDER_DPI = 200

# 숫자 셀: 1,234 / -12.5 / - / (1,200) / 45% 등 예산표 표기
NUMERIC_RE = re.compile(r"^[\d,.\-()%\s–—~]+$")
# 간트 막대·체크 표시에 쓰이는 기호
SYMBOLS = set("■□▪▫●○◆◇▲△▼▽★☆※✓✔√◈▶◀↑↓←→↔↕┃━│─┼")

EXTRACT_TYPES = ("table", "image")

# 판독 힌트 임계값 (참고용 — 추출 여부를 가르지 않는다)
NUMERIC_RATIO_DATA = 0.35
SYMBOL_RATIO_VISUAL = 0.25
EMPTY_RATIO_FORM = 0.75
EMPTY_RATIO_VISUAL = 0.55
MEAN_CHARS_VISUAL = 3.0


@dataclass
class Figure:
    doc: str
    page: int  # 1-based, 원본 PDF 기준
    element_id: int
    kind: str  # "table" | "image"
    image_path: str = ""
    bbox: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    # --- 표일 때의 메타데이터 ---
    n_rows: int = 0
    n_cols: int = 0
    n_cells: int = 0
    empty_ratio: float = 0.0
    mean_cell_chars: float = 0.0
    span_ratio: float = 0.0
    numeric_ratio: float = 0.0
    symbol_ratio: float = 0.0
    looks_visual: bool = False  # 참고값
    note: str = ""


# ---------------------------------------------------------------- 특징 추출


def _walk(el: dict):
    """Follow body, table and nested list children."""
    yield el
    for key in ("kids", "rows", "cells", "list items"):
        for kid in el.get(key) or []:
            if isinstance(kid, dict):
                yield from _walk(kid)


def _cell_text(cell: dict) -> str:
    return " ".join(
        e["content"] for e in _walk(cell) if isinstance(e.get("content"), str)
    ).strip()


def _is_numeric(t: str) -> bool:
    return bool(t) and bool(NUMERIC_RE.match(t)) and any(c.isdigit() for c in t)


def _is_symbol(t: str) -> bool:
    return bool(t) and all(c in SYMBOLS or c.isspace() for c in t)


def table_features(table: dict) -> dict:
    rows = table.get("rows") or []
    cells = [c for r in rows for c in (r.get("cells") or [])]
    n_cells = len(cells)
    base = {
        "n_rows": int(table.get("number of rows") or len(rows)),
        "n_cols": int(table.get("number of columns") or 0),
        "n_cells": n_cells,
    }
    if not n_cells:
        return base | {
            "empty_ratio": 1.0,
            "mean_cell_chars": 0.0,
            "span_ratio": 0.0,
            "numeric_ratio": 0.0,
            "symbol_ratio": 0.0,
        }

    texts = [_cell_text(c) for c in cells]
    filled = [t for t in texts if t]
    n_filled = len(filled)
    spans = sum(
        1 for c in cells if (c.get("row span") or 1) > 1 or (c.get("column span") or 1) > 1
    )
    return base | {
        "empty_ratio": round((n_cells - n_filled) / n_cells, 3),
        "mean_cell_chars": round(sum(len(t) for t in texts) / n_cells, 2),
        "span_ratio": round(spans / n_cells, 3),
        "numeric_ratio": round(sum(_is_numeric(t) for t in filled) / n_filled, 3)
        if n_filled
        else 0.0,
        "symbol_ratio": round(sum(_is_symbol(t) for t in filled) / n_filled, 3)
        if n_filled
        else 0.0,
    }


def visual_hint(f: dict) -> tuple[bool, str]:
    """그림에 가까운 표인지 참고 표시만 한다. 추출 여부는 여기서 갈리지 않는다.

    숫자 비율을 가장 먼저 본다 — 이 순서를 안 지켜서 연도별 예산표를
    간트차트로 오판한 적이 있다.
    """
    if f["n_cells"] == 0:
        return True, "셀 없음 — 그리기용 테두리"
    if f["numeric_ratio"] >= NUMERIC_RATIO_DATA:
        return False, ""
    if f["symbol_ratio"] >= SYMBOL_RATIO_VISUAL:
        return True, f"기호 셀 {f['symbol_ratio']:.0%} — 기호로 그린 간트/체크표"
    if f["empty_ratio"] >= EMPTY_RATIO_FORM:
        return True, f"빈 셀 {f['empty_ratio']:.0%} — 채움 양식"
    if f["mean_cell_chars"] < MEAN_CHARS_VISUAL and f["empty_ratio"] >= EMPTY_RATIO_VISUAL:
        return True, f"빈 셀 {f['empty_ratio']:.0%} · 셀 평균 {f['mean_cell_chars']}자"
    return False, ""


# ---------------------------------------------------------------- 잘라내기


def crop(pdf: pymupdf.Document, page_no: int, bbox: list[float], dst: Path) -> bool:
    """PDF 좌표(좌하단 원점)를 PyMuPDF 좌표(좌상단 원점)로 바꿔 렌더링."""
    if page_no < 1 or page_no > pdf.page_count:
        return False
    page = pdf[page_no - 1]
    ph = page.rect.height

    left, bottom, right, top = bbox
    rect = pymupdf.Rect(
        max(0, left - CROP_PAD_PT),
        max(0, ph - top - CROP_PAD_PT),
        min(page.rect.width, right + CROP_PAD_PT),
        min(ph, ph - bottom + CROP_PAD_PT),
    )
    if rect.width < MIN_CROP_PT or rect.height < MIN_CROP_PT:
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(clip=rect, dpi=RENDER_DPI).save(str(dst))
    return True


# ---------------------------------------------------------------- 실행


def run_on_document(
    parsed_json: Path,
    source_pdf: Path,
    out_dir: Path,
    page_offset: int = 0,
) -> list[Figure]:
    """파싱 JSON 하나를 훑어 표·이미지를 전부 잘라낸다.

    page_offset: 청크 파싱이면 청크의 첫 페이지 번호(0-based)를 준다.
    """
    with open(parsed_json, encoding="utf-8") as fh:
        data = json.load(fh)

    elements = [
        e
        for top in (data.get("kids") or [])
        if isinstance(top, dict)
        for e in _walk(top)
        if e.get("type") in EXTRACT_TYPES
    ]
    if not elements:
        return []

    out: list[Figure] = []
    pdf = pymupdf.open(str(source_pdf))
    try:
        for el in elements:
            kind = el["type"]
            page = int(el.get("page number") or 0) + page_offset
            bbox = [float(x) for x in (el.get("bounding box") or [0, 0, 0, 0])]
            fig = Figure(
                doc=source_pdf.name,
                page=page,
                element_id=int(el.get("id") or 0),
                kind=kind,
                bbox=bbox,
            )

            if kind == "table":
                feats = table_features(el)
                for k, v in feats.items():
                    setattr(fig, k, v)
                fig.looks_visual, fig.note = visual_hint(feats)

            name = f"{source_pdf.stem[:60]}__p{page:04d}_{kind}{fig.element_id}.png"
            dst = out_dir / name
            if crop(pdf, page, bbox, dst):
                fig.image_path = dst.name
            out.append(fig)
    finally:
        pdf.close()

    return out


def write_report(figures: list[Figure], dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump([asdict(f) for f in figures], fh, ensure_ascii=False, indent=2)
