"""3-4단계: PDF 청크 분할 + 파싱 + 청크 경계 감사.

RAM 이 넉넉하면 통짜로 파싱하고(--whole), 부족하면 페이지 단위로 잘라
개별 JVM 프로세스로 처리한다.

청킹에는 두 가지 위험이 있고 각각 대응한다.
  1) 청크가 여전히 무거워 OOM -> 실패한 청크만 이분할해 재시도(최소 1p까지).
  2) 페이지를 가로지르는 표가 잘림 -> 인접 청크 쌍을 감사해 후보를 찾고,
     2페이지 스티치 윈도우로 재파싱해 복원한다.
"""

from __future__ import annotations

import json
import shutil  # noqa: F401 - test patch point for the shared Java resolver
import subprocess
import sys
import time
import uuid
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .config import CONVERTED, JVM_HEAP, NORMALIZED, PARSED, PDF_CHUNK_PAGES, REPORTS, find_java
from .normalize import sha256_of
from .parsed_store import adopt_legacy

warnings.filterwarnings("ignore")

JAR = (
    Path(__file__).resolve().parents[2]
    / ".venv/Lib/site-packages/opendataloader_pdf/jar/opendataloader-pdf-cli.jar"
)

# 본문 흐름이 아니라 경계 판정에서 제외할 요소
NON_BODY = {"header", "footer"}


@dataclass
class ChunkResult:
    doc: str
    agency: str
    chunk_idx: int
    page_start: int  # 0-based, inclusive
    page_end: int  # 0-based, exclusive
    ok: bool = False
    elapsed_s: float = 0.0
    error: str = ""
    depth: int = 0  # 이분할 재시도 깊이 (0 = 최초 분할)
    n_elements: int = 0
    n_tables: int = 0
    n_table_rows: int = 0
    n_chars: int = 0
    first_type: str = ""
    last_type: str = ""
    first_table_has_header: bool | None = None


@dataclass
class BoundaryAudit:
    doc: str
    agency: str
    boundary_idx: int
    page_at_cut: int  # 경계 직전 페이지(0-based)
    prev_last_type: str
    next_first_type: str
    suspected_table_split: bool
    stitched: bool = False
    stitch_recovered_tables: int = 0
    stitch_error: str = ""


@dataclass
class Report:
    chunks: list[ChunkResult] = field(default_factory=list)
    boundaries: list[BoundaryAudit] = field(default_factory=list)


# ---------------------------------------------------------------- 분할


def write_pages(reader: PdfReader, start: int, end: int, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        writer = PdfWriter()
        for page in reader.pages[start:end]:
            writer.add_page(page)
        with open(dst, "wb") as fh:
            writer.write(fh)
    return dst


# ---------------------------------------------------------------- 파싱


DEFAULT_FMT = "json,html"  # markdown 은 colspan 을 잃어 예산표 헤더가 무너진다


HYBRID_URL = "http://127.0.0.1:5002"


def run_jar(
    pdf: Path,
    out_dir: Path,
    heap: str,
    timeout: int = 1800,
    fmt: str = DEFAULT_FMT,
    hybrid: str = "",
    hybrid_url: str = HYBRID_URL,
) -> tuple[bool, str]:
    """opendataloader CLI 를 호출한다.

    hybrid 를 켜면 triage 를 건너뛰는 full 모드로 보낸다. auto 는 '복잡한
    페이지만' 백엔드로 보내는데, 어느 페이지가 넘어갔는지 확인할 수 없어
    결과 해석이 불가능해진다. 느려도 전 페이지를 보내는 편이 판정 가능하다.

    별도 서버가 떠 있어야 한다: opendataloader-pdf-hybrid --port 5002
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    java = find_java()
    cmd = [
        java or "java",
        f"-Xmx{heap}",
        "-Djava.awt.headless=true",
        "-jar",
        str(JAR),
        str(pdf),
        "--output-dir",
        str(out_dir),
        "--format",
        fmt,
        "--use-struct-tree",
    ]
    if hybrid:
        cmd += [
            "--hybrid", hybrid,
            "--hybrid-mode", "full",
            "--hybrid-url", hybrid_url,
            "--hybrid-fallback",  # 백엔드 실패 시 로컬 파서로 되돌아간다
        ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout>{timeout}s"

    if proc.returncode == 0:
        return True, ""

    err = proc.stderr.decode("utf-8", "replace")
    if "OutOfMemoryError" in err:
        return False, "OutOfMemoryError"
    lines = [
        ln.strip()
        for ln in err.splitlines()
        if ln.strip()
        and "bfrange" not in ln
        and "CMapParser" not in ln
        and not ln.lstrip().startswith("at ")
    ]
    return False, " | ".join(lines[-2:])[:300] or f"exit {proc.returncode}"


# ---------------------------------------------------------------- JSON 분석


def _walk(el: dict):
    """Follow body, table and nested list nodes without dropping list items."""
    yield el
    for key in ("kids", "rows", "cells", "list items"):
        for kid in el.get(key) or []:
            if isinstance(kid, dict):
                yield from _walk(kid)


def _text_of(el: dict) -> str:
    return " ".join(
        e["content"] for e in _walk(el) if isinstance(e.get("content"), str)
    ).strip()


def _has_header_row(table: dict) -> bool | None:
    """표의 첫 행이 머리행인지 추정. 숫자 위주면 데이터행 -> 잘린 표로 본다."""
    rows = table.get("rows") or []
    if not rows:
        return None
    texts = [t for t in (_text_of(c) for c in rows[0].get("cells") or []) if t]
    if not texts:
        return None
    numericish = sum(
        1 for t in texts if t.replace(",", "").replace("-", "").replace(".", "").isdigit()
    )
    return numericish < max(1, len(texts) // 2)


def analyse_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    top = [k for k in (data.get("kids") or []) if isinstance(k, dict)]
    flat = [e for t in top for e in _walk(t)]
    body = [e for e in top if e.get("type") not in NON_BODY]

    first_hdr = None
    if body and body[0].get("type") == "table":
        first_hdr = _has_header_row(body[0])

    return {
        "n_elements": len(flat),
        "n_tables": sum(1 for e in flat if e.get("type") == "table"),
        "n_table_rows": sum(1 for e in flat if e.get("type") == "table row"),
        "n_chars": sum(len(e["content"]) for e in flat if isinstance(e.get("content"), str)),
        "first_type": body[0].get("type", "") if body else "",
        "last_type": body[-1].get("type", "") if body else "",
        "first_table_has_header": first_hdr,
    }


# ---------------------------------------------------------------- 오케스트레이션


def _agency_of(name: str) -> str:
    return name.split("__", 1)[0]


def parse_range(
    pdf: Path,
    reader: PdfReader,
    start: int,
    end: int,
    report: Report,
    heap: str,
    chunk_dir: Path,
    out_dir: Path,
    depth: int = 0,
    fmt: str = DEFAULT_FMT,
    hybrid: str = "",
) -> None:
    """[start, end) 페이지를 파싱. 실패하면 절반으로 쪼개 재시도(최소 1p)."""
    name = f"p{start:05d}-{end:05d}"
    cpath = write_pages(reader, start, end, chunk_dir / f"{name}.pdf")

    t = time.time()
    ok, err = run_jar(cpath, out_dir, heap=heap, fmt=fmt, hybrid=hybrid)
    elapsed = round(time.time() - t, 1)

    if not ok and end - start > 1:
        print(f"    {name} 실패({err[:40]}) -> {end - start}p 이분할 재시도", file=sys.stderr)
        mid = start + (end - start) // 2
        args = (report, heap, chunk_dir, out_dir, depth + 1, fmt, hybrid)
        parse_range(pdf, reader, start, mid, *args)
        parse_range(pdf, reader, mid, end, *args)
        return

    res = ChunkResult(
        doc=pdf.name,
        agency=_agency_of(pdf.name),
        chunk_idx=start,
        page_start=start,
        page_end=end,
        ok=ok,
        elapsed_s=elapsed,
        error=err,
        depth=depth,
    )
    if ok:
        jpath = out_dir / f"{cpath.stem}.json"
        if jpath.exists():
            try:
                for key, val in analyse_json(jpath).items():
                    setattr(res, key, val)
            except (OSError, ValueError, TypeError) as exc:
                res.ok, res.error = False, f"Invalid JSON: {exc}"
        else:
            res.ok, res.error = False, "JSON 미생성"
    report.chunks.append(res)


def parse_document(
    pdf: Path,
    report: Report,
    heap: str,
    chunk_size: int,
    allow_whole: bool,
    stitch: bool,
    fmt: str = DEFAULT_FMT,
    hybrid: str = "",
    data_root: Path | None = None,
) -> None:
    parsed_dir = data_root / "04_parsed" if data_root is not None else PARSED
    converted_dir = data_root / "03_converted" if data_root is not None else CONVERTED
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    if "json" not in {part.strip() for part in fmt.split(",")}:
        raise ValueError("JSON output is required to verify parse coverage")
    document_dir = parsed_dir / pdf.stem
    run_id = uuid.uuid4().hex
    out_dir = document_dir / "_runs" / run_id
    chunk_dir = converted_dir / pdf.stem / "_runs" / run_id
    source_hash = sha256_of(pdf)
    report_start = len(report.chunks)
    reader = PdfReader(str(pdf))
    n_pages = len(reader.pages)
    # A page-only copy does not carry the document-level structure tree reliably.
    # Try the original tagged PDF before the page-splitting fallback.
    if "/StructTreeRoot" in reader.trailer["/Root"]:
        allow_whole = True

    if allow_whole:
        t = time.time()
        ok, err = run_jar(pdf, out_dir, heap=heap, fmt=fmt, hybrid=hybrid)
        if ok:
            try:
                stats = analyse_json(out_dir / f"{pdf.stem}.json")
                if sha256_of(pdf) != source_hash:
                    raise ValueError("Source PDF changed during parsing")
                adopt_legacy(
                    document_dir, [out_dir / f"{pdf.stem}.json"], pdf, origin="parser_run"
                )
            except (OSError, ValueError, TypeError) as exc:
                ok, err = False, str(exc)
        if ok:
            report.chunks.append(
                ChunkResult(
                    doc=pdf.name,
                    agency=_agency_of(pdf.name),
                    chunk_idx=0,
                    page_start=0,
                    page_end=n_pages,
                    ok=True,
                    elapsed_s=round(time.time() - t, 1),
                    **stats,
                )
            )
            return
        print(f"    통짜 실패({err[:60]}) -> 청킹 폴백", file=sys.stderr)
        if "/StructTreeRoot" in reader.trailer["/Root"]:
            print("    주의: 분할 PDF의 구조 태그 보존은 확인되지 않았습니다", file=sys.stderr)

    for start in range(0, n_pages, chunk_size):
        parse_range(
            pdf,
            reader,
            start,
            min(start + chunk_size, n_pages),
            report,
            heap,
            chunk_dir,
            out_dir,
            fmt=fmt,
            hybrid=hybrid,
        )

    current = report.chunks[report_start:]
    if current and all(c.ok for c in current):
        if sha256_of(pdf) != source_hash:
            raise ValueError("Source PDF changed during parsing")
        files = [out_dir / f"p{c.page_start:05d}-{c.page_end:05d}.json" for c in current]
        adopt_legacy(document_dir, files, pdf, origin="parser_run")
    audit_boundaries(pdf, report, stitch, heap, data_root=data_root, run_id=run_id)


def audit_boundaries(
    pdf: Path, report: Report, stitch: bool, heap: str, data_root: Path | None = None,
    run_id: str | None = None,
) -> None:
    """인접 청크 쌍을 페이지 순서대로 검사해 경계에서 잘린 표 후보를 찾는다."""
    mine = sorted((c for c in report.chunks if c.doc == pdf.name), key=lambda c: c.page_start)
    if len(mine) < 2:
        return

    reader = PdfReader(str(pdf))
    for a, b in zip(mine, mine[1:], strict=False):
        if not (a.ok and b.ok):
            continue
        suspect = (
            a.last_type == "table"
            and b.first_type == "table"
            and b.first_table_has_header is False
        )
        aud = BoundaryAudit(
            doc=pdf.name,
            agency=_agency_of(pdf.name),
            boundary_idx=a.page_end,
            page_at_cut=a.page_end - 1,
            prev_last_type=a.last_type,
            next_first_type=b.first_type,
            suspected_table_split=suspect,
        )

        if suspect and stitch:
            try:
                converted = (
                    data_root / "03_converted" if data_root is not None else CONVERTED
                ) / pdf.stem
                parsed = (data_root / "04_parsed" if data_root is not None else PARSED) / pdf.stem
                # Standalone audits also receive a fresh generation for stitch artifacts.
                stitch_run = run_id or uuid.uuid4().hex
                win = write_pages(
                    reader,
                    a.page_end - 1,
                    min(a.page_end + 1, len(reader.pages)),
                    converted / "_runs" / stitch_run / f"stitch_{a.page_end:05d}.pdf",
                )
                sout = parsed / "_runs" / stitch_run / "_stitch"
                ok, err = run_jar(win, sout, heap=heap)
                if ok:
                    aud.stitched = True
                    aud.stitch_recovered_tables = analyse_json(sout / f"{win.stem}.json")[
                        "n_tables"
                    ]
                else:
                    aud.stitch_error = err
            except Exception as e:  # noqa: BLE001
                aud.stitch_error = str(e)[:200]

        report.boundaries.append(aud)


def run(
    chunk_size: int = PDF_CHUNK_PAGES,
    heap: str = JVM_HEAP,
    allow_whole: bool = False,
    stitch: bool = True,
    limit: int | None = None,
) -> Report:
    report = Report()
    pdfs = sorted(NORMALIZED.glob("*.pdf"))
    if limit:
        pdfs = pdfs[:limit]

    for i, pdf in enumerate(pdfs, 1):
        print(f"[{i}/{len(pdfs)}] {pdf.name[:70]}", file=sys.stderr)
        try:
            parse_document(pdf, report, heap, chunk_size, allow_whole, stitch)
        except Exception as e:  # noqa: BLE001
            report.chunks.append(
                ChunkResult(
                    doc=pdf.name,
                    agency=_agency_of(pdf.name),
                    chunk_idx=-1,
                    page_start=0,
                    page_end=0,
                    ok=False,
                    error=str(e)[:300],
                )
            )

    REPORTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": {
            "chunk_size": chunk_size,
            "heap": heap,
            "allow_whole": allow_whole,
            "stitch": stitch,
        },
        "chunks": [asdict(c) for c in report.chunks],
        "boundaries": [asdict(b) for b in report.boundaries],
    }
    with open(REPORTS / "parse_report.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    return report
