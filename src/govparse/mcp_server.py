"""govparse MCP — HWP/HWPX/PDF 문서 파서.

PDF 구조는 opendataloader-pdf와 PyMuPDF에서, HWP 원본 경계는 rhwp에서
읽는다. 서로 다른 출처를 같은 표로 확정하기 전에는 판단불가로 남긴다.

기존 PDF 파이프라인은 다음 네 가지를 담당한다.

  1. 압축 해제 · CP949 파일명 복원 · 평탄화 · 인벤토리
  2. HWP/HWPX -> PDF 변환 (한컴 COM, Windows 전용)
  3. RAM 이 모자랄 때의 청킹 + OOM 이분할 폴백
  4. 총괄표 복구 — opendataloader 가 놓친 표를 PyMuPDF 로 되살림

4번은 실측 근거가 있다. 사업명·요구액·확정액이 든 총괄표를 opendataloader 는
184건 중 27건(15%)만 표로 인식하고 나머지는 문단으로 흩뜨린다. PyMuPDF
find_tables 폴백을 붙이면 177/184(96%) 가 된다.

find_tables 의 snap_tolerance 는 반드시 1 이어야 한다. 기본값은 가까운 괘선을
병합해 열 경계를 밀어내고, 그 결과 값의 끝자리가 옆 칸으로 넘어간다
(`35,695|35,695` -> `35,69|535,695`). 실버셋 156건 전수 재추출로 확인했다 —
snap_tolerance=1 로 바꾸면 오류 12건이 사라지고 깨지는 것은 없다.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import find_java

mcp = FastMCP("govparse")

# --- 실측으로 고정된 값 (근거는 모듈 docstring) ---
SNAP_TOLERANCE = 1
DEFAULT_CHUNK_PAGES = 50
DEFAULT_FORMAT = "json,html"  # markdown 은 colspan 을 잃는다


@contextmanager
def _scoped_env(**pairs: str):
    """환경변수를 이 블록 안에서만 바꾼다.

    MCP 서버는 한 프로세스에서 여러 요청을 처리한다. 도구가 환경변수를
    남기면 다음 요청이 엉뚱한 경로를 쓴다. 전역 가변 상태를 두지 않는
    이유도 같다 — 동시 호출에서 서로 덮어쓴다.
    """
    saved = {k: os.environ.get(k) for k in pairs}
    os.environ.update(pairs)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _safe_doc_name(name: str) -> str | None:
    """doc_name 은 파일명이다. 경로 구분자가 들어오면 코퍼스 밖을 가리킬 수 있다."""
    if not name or name != Path(name).name or name in (".", ".."):
        return None
    if any(c in name for c in "\\/\r\n\0"):
        return None
    return name


def _same_table(
    candidate: tuple[float, float, float, float], boxes: list[list[float]], height: float
) -> bool:
    """Match a PyMuPDF table to an opendataloader table by PDF page area."""
    import pymupdf

    found = pymupdf.Rect(*candidate)
    if found.is_empty:
        return False
    for box in boxes:
        try:
            left, bottom, right, top = (float(value) for value in box)
        except (TypeError, ValueError):
            continue
        known = pymupdf.Rect(left, height - top, right, height - bottom)
        if known.is_empty:
            continue
        overlap = known & found
        if overlap.is_empty:
            continue
        smaller_area = min(known.get_area(), found.get_area())
        if overlap.get_area() / smaller_area >= 0.7:
            return True
    return False


def _data_root() -> Path:
    root = os.environ.get("GOVPARSE_DATA")
    if root:
        return Path(root)
    from .config import DATA

    return DATA


def _corpus_dir() -> Path:
    d = _data_root()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return _corpus_dir() / "05_reports" / "manifest.csv"


def _load_manifest() -> list[dict]:
    import csv

    p = _manifest_path()
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as fh:
        return [r for r in csv.DictReader(fh) if r.get("is_document") == "1"]


# ---------------------------------------------------------------- 환경


@mcp.tool()
def doctor() -> str:
    """실행 환경을 점검한다. 무엇이 되고 무엇이 안 되는지 먼저 확인할 것.

    Java 는 PDF 파싱에, 한컴오피스는 HWP→PDF 변환에 필요하다.
    rhwp는 HWP/HWPX 원본 문단·표 추출에 사용한다.
    """
    lines: list[str] = [f"플랫폼: {platform.system()} {platform.release()}"]

    java = find_java()
    if java:
        ver = subprocess.run([java, "-version"], capture_output=True, text=True).stderr
        lines.append(f"OK   java: {ver.splitlines()[0] if ver else '확인'}")
    else:
        lines.append("FAIL java: 없음 — PDF 파싱 불가. JDK 11+ 설치 필요")

    try:
        import opendataloader_pdf  # noqa: F401

        lines.append("OK   opendataloader-pdf")
    except ImportError as e:
        lines.append(f"FAIL opendataloader-pdf: {e}")

    try:
        import pymupdf  # noqa: F401

        lines.append("OK   pymupdf (총괄표 복구용)")
    except ImportError as e:
        lines.append(f"FAIL pymupdf: {e}")

    try:
        import rhwp  # noqa: F401

        lines.append("OK   rhwp (HWP/HWPX 원본 구조용)")
    except ImportError as e:
        lines.append(f"FAIL rhwp: {e}")

    if platform.system() == "Windows":
        hwp = Path(r"C:\Program Files (x86)\Hnc")
        if hwp.exists():
            lines.append("OK   한컴오피스 — HWP/HWPX 변환 가능")
        else:
            lines.append("주의 한컴오피스 없음 — HWP/HWPX→PDF 변환 불가")
    else:
        lines.append("주의 Windows 아님 — HWP/HWPX→PDF 변환 불가 (한컴 COM 필요)")

    free_gb = shutil.disk_usage(str(_data_root().anchor or "/")).free / 1024**3
    lines.append(f"--   디스크 여유 {free_gb:.0f} GB")
    lines.append(f"--   데이터 루트 {_data_root()}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 코퍼스


@mcp.tool()
def prepare_corpus(source_dir: str) -> str:
    """문서 묶음을 파싱 가능한 상태로 만든다.

    zip 을 풀고(중첩 포함), 깨진 한글 파일명을 복원하고, 중첩 디렉토리를
    평탄화한 뒤 인벤토리를 만든다.

    한국 정부 문서 묶음에는 두 가지 함정이 있어 이 단계가 필요하다.
      - 윈도우 압축 도구가 파일명을 CP949 로 저장하면서 UTF-8 플래그를 켜지
        않아, 일반 해제 도구로 풀면 이름이 깨져 출처 추적이 불가능해진다.
      - 확장자가 실제 포맷과 다른 파일이 섞인다(.hwpx 인데 내용은 OLE HWP).
        확장자를 믿으면 파서가 엉뚱하게 붙는다. 매직넘버로 판정한다.

    Args:
        source_dir: 원본 문서가 든 디렉토리 경로
    """
    from . import extract, normalize

    src = Path(source_dir)
    if not src.is_dir():
        return f"디렉토리가 없습니다: {source_dir}"

    corpus = _corpus_dir()
    with _scoped_env(GOVPARSE_SOURCE=str(src), GOVPARSE_DATA=str(corpus)):
        st = extract.run(source_dir=src, out_dir=corpus / "01_extracted")
        rows = normalize.run(
            src_dir=corpus / "01_extracted", out_dir=corpus / "02_normalized"
        )

    docs = [r for r in rows if r["is_document"]]
    from collections import Counter

    fmt = Counter(r["format_actual"] for r in docs)
    mismatch = [r for r in docs if r["ext_mismatch"]]

    out = [
        f"압축 해제: 아카이브 {st.archives}개 / 파일 {st.files}개",
        f"  CP949 파일명 복원 {st.renamed}건 · 중첩 zip {st.nested_zips}건",
        f"문서 {len(docs)}건 — {dict(fmt)}",
    ]
    if mismatch:
        out.append(f"확장자 위장 {len(mismatch)}건 (실제 포맷 기준으로 처리됨):")
        out += [
            f"  .{r['ext_declared']} -> {r['format_actual']}  {r['normalized_name'][:60]}"
            for r in mismatch[:5]
        ]
    if st.errors:
        out.append(f"오류 {len(st.errors)}건: {st.errors[:3]}")
    out.append(f"인벤토리: {_manifest_path()}")
    return "\n".join(out)


@mcp.tool()
def list_documents(agency: str = "", doc_format: str = "") -> str:
    """준비된 코퍼스의 문서 목록을 본다.

    prepare_corpus 를 먼저 실행해야 한다.

    Args:
        agency: 부처로 거르기 (부분 일치). 비우면 전체
        doc_format: 포맷으로 거르기 — hwp / hwpx / pdf. 비우면 전체
    """
    rows = _load_manifest()
    if not rows:
        return "인벤토리가 없습니다. prepare_corpus 를 먼저 실행하세요."

    if agency:
        rows = [r for r in rows if agency in r["agency"]]
    if doc_format:
        rows = [r for r in rows if r["format_actual"] == doc_format.lower()]
    if not rows:
        return "조건에 맞는 문서가 없습니다."

    lines = [f"{'부처':<14} {'포맷':<5} {'MB':>6}  문서"]
    lines.append("-" * 78)
    for r in sorted(rows, key=lambda x: (x["agency"], x["normalized_name"])):
        mb = int(r["size_bytes"]) / 1024**2
        lines.append(
            f"{r['agency'][:12]:<14} {r['format_actual']:<5} {mb:>6.1f}  "
            f"{r['normalized_name'][:46]}"
        )
    lines.append(f"\n총 {len(rows)}건")
    return "\n".join(lines)


# ---------------------------------------------------------------- 변환·파싱


@mcp.tool()
def convert_hwp(doc_name: str, timeout_sec: int = 900) -> str:
    """HWP/HWPX 를 PDF 로 변환한다. Windows + 한컴오피스가 필요하다.

    파일마다 별도 워커 프로세스를 띄우고 끝나면 한컴 프로세스를 강제 정리한다.
    한 건이 COM 안에서 멈춰도 배치 전체가 멈추지 않게 하기 위해서다
    (정리를 빠뜨리면 좀비가 쌓여 다음 연결이 그 좀비에 붙어 무한 대기한다).

    Args:
        doc_name: 정규화된 파일명 (list_documents 로 확인)
        timeout_sec: 파일 하나당 제한 시간
    """
    if platform.system() != "Windows":
        return "Windows 가 아닙니다. HWP 변환은 한컴오피스 COM 이 필요합니다."

    from . import convert

    if _safe_doc_name(doc_name) is None:
        return f"문서 이름에 경로가 들어갈 수 없습니다: {doc_name!r}"
    src = _corpus_dir() / "02_normalized" / doc_name
    if not src.exists():
        return f"문서가 없습니다: {doc_name}"

    dst = _corpus_dir() / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
    temp_dst = dst.with_name(f".{dst.stem}.{uuid.uuid4().hex}.pdf")
    from .normalize import sha256_of

    source_before = sha256_of(src)
    convert.kill_hancom()
    ok, pages, err = convert.convert_one(src, temp_dst, timeout_sec)
    convert.kill_hancom()

    if not ok:
        temp_dst.unlink(missing_ok=True)
        return f"변환 실패: {err}"
    if not temp_dst.is_file() or temp_dst.stat().st_size == 0:
        return "변환 실패: 새 PDF가 생성되지 않았습니다."
    if sha256_of(src) != source_before:
        temp_dst.unlink(missing_ok=True)
        return "변환 중 HWP 원본이 변경되어 PDF 계보를 확정할 수 없습니다. 다시 변환하세요."
    from .conversion_lineage import record_conversion

    try:
        os.replace(temp_dst, dst)
        record_conversion(
            src, dst, dst.with_suffix(".source.json"), expected_source_sha256=source_before
        )
    except (OSError, ValueError) as exc:
        temp_dst.unlink(missing_ok=True)
        return f"PDF 변환은 완료됐지만 계보 기록에 실패했습니다: {exc}"
    mb = dst.stat().st_size / 1024**2
    return f"변환 완료: {pages}p · {mb:.1f} MB\n{dst}\n계보: {dst.with_suffix('.source.json')}"


@mcp.tool()
def parse_document(
    doc_name: str,
    output_format: str = DEFAULT_FORMAT,
    chunk_pages: int = DEFAULT_CHUNK_PAGES,
    whole_file: bool = False,
    hybrid: str = "",
) -> str:
    """문서를 구조화 데이터로 파싱한다.

    큰 문서는 통째로 넘기면 JVM 이 OutOfMemory 로 죽는다(1,119페이지 문서가
    기본 힙에서 실패). 그래서 페이지 단위로 잘라 처리하고, 잘린 조각이 또
    실패하면 절반으로 쪼개 재시도한다. `--pages` 옵션으로는 이 문제를 피할 수
    없다 — 페이지 선택이 문서 전체 전처리 이후에 적용되기 때문이다.

    출력은 JSON + HTML 이 기본이다. Markdown 은 병합 셀(colspan)을 표현하지
    못해 예산표의 헤더 계층이 무너진다.

    Args:
        doc_name: 정규화된 파일명. HWP/HWPX 면 변환본을 찾아 쓴다
        output_format: json,html,markdown,text 중 쉼표로 구분
        chunk_pages: 청크 크기. 메모리가 넉넉하면 whole_file 을 쓸 것
        whole_file: 통째로 시도하고 실패한 것만 청킹 (메모리 여유가 있을 때)
        hybrid: AI 백엔드. "docling-fast" 를 주면 쓴다. 별도 서버가 필요하고
            (opendataloader-pdf-hybrid --port 5002) 50배 이상 느리다.
            이 문서군에서는 오히려 표 인식이 나빠졌으므로 기본은 끔
    """
    from . import chunking

    if _safe_doc_name(doc_name) is None:
        return f"문서 이름에 경로가 들어갈 수 없습니다: {doc_name!r}"
    norm_dir = _corpus_dir() / "02_normalized"
    src = norm_dir / doc_name
    if not src.exists():
        return f"문서가 없습니다: {doc_name}"

    if src.suffix.lower() in (".hwp", ".hwpx"):
        pdf = _corpus_dir() / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
        if not pdf.exists():
            return (
                f"{doc_name} 은 HWP 계열입니다. convert_hwp 를 먼저 실행하세요."
            )
        src = pdf

    report = chunking.Report()
    chunking.parse_document(
        src, report, heap=os.environ.get("GOVPARSE_HEAP", "3g"),
        chunk_size=chunk_pages, allow_whole=whole_file, stitch=False,
        fmt=output_format, hybrid=hybrid, data_root=_corpus_dir(),
    )

    ok = [c for c in report.chunks if c.ok]
    bad = [c for c in report.chunks if not c.ok]
    pages = sum(c.page_end - c.page_start for c in ok)
    lines = [
        f"파싱 완료: {pages}페이지 · 단위 {len(report.chunks)}개 "
        f"(성공 {len(ok)} / 실패 {len(bad)})",
        f"  표 {sum(c.n_tables for c in ok)} · 행 {sum(c.n_table_rows for c in ok)}",
        f"  소요 {sum(c.elapsed_s for c in report.chunks) / 60:.1f}분",
    ]
    resplit = [c for c in report.chunks if c.depth > 0]
    if resplit:
        lines.append(f"  메모리 부족으로 재분할된 단위 {len(resplit)}개")
    if bad:
        lines.append(f"실패 {len(bad)}건:")
        lines += [f"  p{c.page_start}-{c.page_end}: {c.error[:70]}" for c in bad[:5]]
    lines.append(f"출력: {_corpus_dir() / "04_parsed" / src.stem}")
    return "\n".join(lines)


@mcp.tool()
def get_tables(
    doc_name: str, page: int = 0, recover_missing: bool = True, output_format: str = "text"
) -> str:
    """파싱된 문서에서 표를 꺼낸다.

    예산 문서의 핵심인 사업별 총괄표(사업명·요구액·확정액)는 opendataloader 가
    표로 인식하지 못하고 문단으로 흩뜨리는 일이 잦다 — 실측 184건 중 157건이
    그랬다. recover_missing 을 켜면 그런 영역을 PyMuPDF 로 다시 훑어 되살린다.

    Args:
        doc_name: 정규화된 파일명
        page: 특정 페이지만 (1부터). 0 이면 전체
        recover_missing: 놓친 표를 복구할지. 끄면 opendataloader 결과만 본다
        output_format: text는 미리보기, json은 전체 셀·행과 원본 구조 및 페이지 문맥
    """
    from . import chunking
    from .parsed_store import page_offset, selected_json_files

    def error(message: str) -> str:
        if output_format == "json":
            return json.dumps({"error": message, "tables": []}, ensure_ascii=False)
        return message

    if output_format not in ("text", "json"):
        return "output_format은 text 또는 json이어야 합니다."
    if page < 0:
        return error("page는 0(전체) 또는 양수여야 합니다.")

    if _safe_doc_name(doc_name) is None:
        return error(f"문서 이름에 경로가 들어갈 수 없습니다: {doc_name!r}")
    src = _corpus_dir() / "02_normalized" / doc_name
    if src.suffix.lower() in (".hwp", ".hwpx"):
        src = _corpus_dir() / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
    out_dir = _corpus_dir() / "04_parsed" / src.stem
    if not out_dir.exists():
        return error(f"파싱 결과가 없습니다. parse_document 를 먼저 실행하세요: {doc_name}")

    tables: list[dict] = []
    known_boxes: dict[int, list[list[float]]] = {}
    try:
        selected = selected_json_files(out_dir)
    except (ValueError, OSError) as exc:
        return error(f"파싱 결과 선택 오류: {exc}")
    page_context: dict[int, list[str]] = {}
    for jpath in selected:
        offset = page_offset(jpath)
        data = json.loads(jpath.read_text(encoding="utf-8"))
        def collect_context(node: dict, page_base: int = offset) -> None:
            if node.get("type") == "table":
                return
            if isinstance(node.get("content"), str) and node.get("page number"):
                pg = int(node["page number"]) + page_base
                page_context.setdefault(pg, []).append(node["content"])
            for key in ("kids", "list items"):
                for child in node.get(key) or []:
                    if isinstance(child, dict):
                        collect_context(child, page_base)

        collect_context(data)
        for top in data.get("kids") or []:
            if not isinstance(top, dict):
                continue
            for el in chunking._walk(top):
                if el.get("type") != "table":
                    continue
                local_page = el.get("page number")
                pg = local_page + offset if type(local_page) is int and local_page > 0 else None
                if page and pg != page:
                    continue
                bbox = el.get("bounding box")
                if pg is not None and isinstance(bbox, list) and len(bbox) == 4:
                    known_boxes.setdefault(pg, []).append(bbox)
                rows = [
                    [chunking._text_of(c) for c in (r.get("cells") or [])]
                    for r in (el.get("rows") or [])
                ]
                tables.append({
                    "page": pg, "source": "opendataloader", "rows": rows,
                    "raw_table": el, "source_json": str(jpath.resolve()),
                    "source_element_id": el.get("id"), "review_required": True,
                    "flags": ["missing_page"] if pg is None else [],
                })

    recovered = 0
    if recover_missing and src.exists():
        import pymupdf

        doc = pymupdf.open(str(src))
        try:
            pages = [page] if page else range(1, doc.page_count + 1)
            for pg in pages:
                if pg < 1 or pg > doc.page_count:
                    continue
                for t in doc[pg - 1].find_tables(snap_tolerance=SNAP_TOLERANCE).tables:
                    if _same_table(t.bbox, known_boxes.get(pg, []), doc[pg - 1].rect.height):
                        continue
                    rows = [
                        ["" if c is None else " ".join(c.split()) for c in row]
                        for row in t.extract()
                    ]
                    if any(any(c for c in r) for r in rows):
                        tables.append({
                            "page": pg, "source": "recovered", "rows": rows,
                            "bbox_top_left": list(t.bbox), "source_pdf": str(src.resolve()),
                            "review_required": True,
                        })
                        recovered += 1
        finally:
            doc.close()

    for table in tables:
        table["page_context"] = page_context.get(table["page"], [])
        table["page_context_scope"] = "page_candidates_not_verified_table_units"
    tables.sort(key=lambda t: t["page"] if t["page"] is not None else float("inf"))
    if output_format == "json":
        return json.dumps({
            "document": doc_name, "tables": tables,
            "table_structure_verified": False, "truncated": False,
        }, ensure_ascii=False)
    if not tables:
        return "표가 없습니다."

    tables.sort(key=lambda t: t["page"] if t["page"] is not None else float("inf"))
    lines = [
        f"표 {len(tables)}개 (opendataloader {len(tables) - recovered} · 복구 {recovered})"
    ]
    lines.append("미리보기: 최대 40표·표당 12행·셀당 18자. 전체 내용은 output_format=json.")
    for t in tables[:40]:
        lines.append(f"\n--- p{t['page']} [{t['source']}] {len(t['rows'])}행")
        for row in t["rows"][:12]:
            lines.append("  | " + " | ".join(c[:18] for c in row))
    if len(tables) > 40:
        lines.append(f"\n... 외 {len(tables) - 40}개")
    return "\n".join(lines)


@mcp.tool()
def extract_table_images(
    doc_name: str, page: int = 0, only_missed: bool = True, dpi: int = 190
) -> str:
    """표를 이미지로 잘라낸다. 파서가 못 읽은 표를 눈으로 읽기 위한 경로다.

    이 문서군에는 파서가 구조를 복원하지 못하는 표 서식이 있다 — 사업명·
    요구액·확정액이 든 총괄표가 대표적이고, 옵션을 바꿔도(구조 트리, 안전
    필터, table-method, AI 백엔드) 인식되지 않는다. 같은 문서의 다른 표
    수십 개는 정확히 뽑히므로 도구 전반의 한계가 아니라 이 서식의 사각지대다.

    값을 텍스트로 못 뽑는다면 그림으로 넘기면 된다. 잘라낸 PNG 를 멀티모달
    모델이 직접 읽으면 값·병합구조까지 복원된다 — 실제로 이 방식으로 표
    184건을 판독해 138건이 전 셀 일치했다.

    반환된 경로를 Read 도구로 열어 표를 읽으십시오.

    Args:
        doc_name: 정규화된 파일명
        page: 특정 페이지만 (1부터). 0 이면 전체
        only_missed: 파서가 놓친 표만. 끄면 인식된 표도 함께 자른다
        dpi: 해상도. 작은 글씨가 뭉개지면 300 까지 올린다
    """
    if _safe_doc_name(doc_name) is None:
        return f"문서 이름에 경로가 들어갈 수 없습니다: {doc_name!r}"

    import pymupdf

    from . import chunking

    src = _corpus_dir() / "02_normalized" / doc_name
    if src.suffix.lower() in (".hwp", ".hwpx"):
        src = _corpus_dir() / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
    if not src.exists():
        return f"PDF 가 없습니다: {doc_name} (convert_hwp 를 먼저 실행했습니까?)"

    # Keep areas so other tables on the same page can still be recovered.
    from .parsed_store import page_offset, selected_json_files

    known_boxes: dict[int, list[list[float]]] = {}
    out_dir = _corpus_dir() / "04_parsed" / src.stem
    if out_dir.exists():
        try:
            selected = selected_json_files(out_dir)
        except (ValueError, OSError) as exc:
            return f"파싱 결과 선택 오류: {exc}"
        for jpath in selected:
            offset = page_offset(jpath)
            data = json.loads(jpath.read_text(encoding="utf-8"))
            for top in data.get("kids") or []:
                if isinstance(top, dict):
                    for el in chunking._walk(top):
                        if el.get("type") == "table":
                            bbox = el.get("bounding box")
                            local_page = el.get("page number")
                            if (type(local_page) is int and local_page > 0
                                    and isinstance(bbox, list) and len(bbox) == 4):
                                pg = local_page + offset
                                known_boxes.setdefault(pg, []).append(bbox)

    img_dir = _corpus_dir() / "06_table_images" / src.stem
    img_dir.mkdir(parents=True, exist_ok=True)

    saved: list[tuple[int, str]] = []
    doc = pymupdf.open(str(src))
    try:
        pages = [page] if page else range(1, doc.page_count + 1)
        for pg in pages:
            if pg < 1 or pg > doc.page_count:
                continue
            p = doc[pg - 1]
            found = p.find_tables(snap_tolerance=SNAP_TOLERANCE).tables
            for i, t in enumerate(found):
                if only_missed and _same_table(t.bbox, known_boxes.get(pg, []), p.rect.height):
                    continue
                rect = pymupdf.Rect(*t.bbox) + (-6, -6, 6, 6)
                rect &= p.rect
                if rect.width < 30 or rect.height < 20:
                    continue
                dst = img_dir / f"p{pg:04d}_t{i}.png"
                p.get_pixmap(clip=rect, dpi=dpi).save(str(dst))
                saved.append((pg, str(dst)))
    finally:
        doc.close()

    if not saved:
        scope = "파서가 놓친 표" if only_missed else "표"
        return f"{scope}가 없습니다. only_missed=False 로 다시 시도해 보십시오."

    lines = [
        f"표 이미지 {len(saved)}장 저장 ({dpi} DPI)",
        f"경로: {img_dir}",
        "",
        "Read 도구로 아래 파일을 열어 표를 직접 읽으십시오:",
    ]
    lines += [f"  p{pg:>4}  {path}" for pg, path in saved[:30]]
    if len(saved) > 30:
        lines.append(f"  ... 외 {len(saved) - 30}장")
    return "\n".join(lines)


@mcp.tool()
def export_rag_chunks(doc_name: str, max_chars: int = 1800) -> str:
    """활성 파싱 결과를 원문 보존 RAG JSONL로 내보내고 파일 경로를 반환한다.

    문단·목록 텍스트, 제목 문맥, 앞뒤 연결, 원문 페이지·좌표·JSON 출처를 저장한다.
    파서가 잘못 나눈 문장을 의미적으로 복원했다고 가정하지 않는다. max_chars는
    문자 기준 검토 임계값이며 긴 단일 문단은 자르지 않고 크기 초과를 표시한다.
    표 구조는 검토 대상으로 보존하고 이미지 후보를 연결한다. 실제 검색은 별도다.
    """
    from .rag import export_document

    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    root = _corpus_dir()
    pdf = root / "02_normalized" / doc_name
    if pdf.suffix.lower() in (".hwp", ".hwpx"):
        pdf = root / "03_converted" / "hwp2pdf" / f"{pdf.stem}.pdf"
    try:
        result = export_document(
            root / "04_parsed" / pdf.stem, doc_name, pdf,
            root / "06_table_images" / pdf.stem,
            root / "07_rag" / pdf.stem, max_chars=max_chars,
        )
        return json.dumps(result, ensure_ascii=False)
    except (ValueError, OSError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _hwp_source_paths(doc_name: str) -> tuple[Path, Path]:
    root = _corpus_dir()
    src = root / "02_normalized" / doc_name
    sidecar = root / "08_hwp_source" / src.name
    return src, sidecar


@mcp.tool()
def parse_hwp_source(doc_name: str) -> str:
    """HWP/HWPX 원본 문단·표·셀 경계를 별도 JSON sidecar에 보존한다.

    PDF 페이지 귀속은 추정하지 않는다. 기존 ODL 결과와 평가 기준은 변경하지 않는다.
    결과의 document.json은 rhwp IR 원문이고 SHA256 manifest로 캐시를 검증한다.
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    from .hwp_source import extract_source

    src, sidecar = _hwp_source_paths(doc_name)
    try:
        manifest = extract_source(src, sidecar)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(
        {**manifest, "document_json": str(sidecar / "document.json")}, ensure_ascii=False
    )


@mcp.tool()
def get_hwp_element(doc_name: str, body_index: int, row: int = -1, col: int = -1) -> str:
    """원본 HWP/HWPX의 한 body 요소를 JSON으로 반환한다.

    body_index는 rhwp IR의 0 기반 body 순서다. 원본 HWP 레코드 번호 또는
    PDF 쪽 번호로 해석하지 않는다. 표는 모든 셀과 중첩 블록을 반환하며,
    row·col을 함께 지정하면 해당 표의 셀 하나만 반환한다. 지정하지 않으면
    body 요소 전체를 반환한다. 중복 HTML 문자열은 응답에서 생략한다.
    전체 원문 IR은 sidecar에 있다.
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    if type(body_index) is not int or body_index < 0:
        return json.dumps({"error": "body_index는 0 이상의 정수여야 합니다."}, ensure_ascii=False)
    if (
        type(row) is not int or type(col) is not int
        or (row == -1) != (col == -1)
        or row < -1 or col < -1
    ):
        return json.dumps(
            {"error": "row·col을 함께 0 이상의 정수로 지정하세요."}, ensure_ascii=False
        )
    from .hwp_source import extract_source

    src, sidecar = _hwp_source_paths(doc_name)
    try:
        manifest = extract_source(src, sidecar)
        ir = json.loads((sidecar / "document.json").read_text(encoding="utf-8"))
        body = ir["body"]
        if body_index >= len(body):
            raise ValueError(f"body_index 범위 초과: {body_index} >= {len(body)}")
        def without_redundant_html(value):
            if isinstance(value, dict):
                return {key: without_redundant_html(child)
                        for key, child in value.items() if key != "html"}
            if isinstance(value, list):
                return [without_redundant_html(child) for child in value]
            return value

        element = without_redundant_html(body[body_index])
        selection = None
        if row >= 0:
            if element.get("kind") != "table":
                raise ValueError("row·col 조회 대상이 표가 아닙니다")
            cell = next(
                (item for item in element.get("cells") or []
                 if item.get("row") == row and item.get("col") == col), None
            )
            if cell is None:
                raise ValueError(f"표 셀 앵커가 없습니다: row={row}, col={col}")
            selection = {"row": row, "col": col, "table_rows": element.get("rows"),
                         "table_cols": element.get("cols")}
            element = cell
    except (OSError, KeyError, IndexError, TypeError, ValueError, RuntimeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps({
        "source_sha256": manifest["source_sha256"],
        "body_index": body_index,
        "pdf_page": None,
        "pdf_page_status": "판단불가",
        "selection": selection,
        "element": element,
    }, ensure_ascii=False)


@mcp.tool()
def search_hwp_source(doc_name: str, query: str, limit: int = 20) -> str:
    """원본 HWP 문단·표 셀에서 문자 그대로 검색해 body/cell 위치를 찾는다.

    위치 후보 조회일 뿐 PDF 페이지 귀속 또는 사업 유사도를 판정하지 않는다.
    문자 정규화·공백 접기 없이 정확한 부분 문자열을 찾는다.
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    if not isinstance(query, str) or len(query) < 2:
        return json.dumps({"error": "query는 2자 이상이어야 합니다."}, ensure_ascii=False)
    if type(limit) is not int or not 1 <= limit <= 100:
        return json.dumps({"error": "limit은 1~100이어야 합니다."}, ensure_ascii=False)
    from .hwp_source import extract_source

    src, sidecar = _hwp_source_paths(doc_name)
    try:
        manifest = extract_source(src, sidecar)
        ir = json.loads((sidecar / "document.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, RuntimeError, KeyError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    matches: list[dict] = []
    total = 0

    def walk(node, body_index: int, pointer: str, cell: list[int] | None = None):
        nonlocal total
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        value = node.get("text")
        if kind in ("paragraph", "list_item") and isinstance(value, str):
            offset = value.find(query)
            if offset >= 0:
                total += 1
                if len(matches) < limit:
                    matches.append({
                        "body_index": body_index, "json_pointer": pointer,
                        "cell": cell, "kind": kind,
                        "snippet": value[max(0, offset - 60):offset + len(query) + 60],
                    })
            return
        if kind == "table":
            for ci, c in enumerate(node.get("cells") or []):
                position = [c.get("row"), c.get("col")]
                for bi, block in enumerate(c.get("blocks") or []):
                    walk(block, body_index, f"{pointer}/cells/{ci}/blocks/{bi}", position)
        else:
            for key in ("blocks", "items", "list_items"):
                for index, child in enumerate(node.get(key) or []):
                    walk(child, body_index, f"{pointer}/{key}/{index}", cell)

    for index, block in enumerate(ir.get("body") or []):
        walk(block, index, f"/body/{index}")
    return json.dumps({
        "source_sha256": manifest["source_sha256"], "query": query,
        "total_matches": total, "returned": len(matches), "matches": matches,
        "pdf_page_status": "판단불가",
    }, ensure_ascii=False)


@mcp.tool()
def export_hwp_rag_chunks(doc_name: str) -> str:
    """원본 HWP 문단·표 셀 경계를 보존한 검색용 JSONL을 내보낸다.

    빈 문단도 경계 증거로 남기지만 retrieval_usable=false다. PDF 쪽·바깥 단위·
    각주를 추정하지 않으므로 이 JSONL만으로 유사/상이 자동 확정을 하지 않는다.
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    from .hwp_rag import export_hwp_chunks
    from .hwp_source import extract_source

    src, sidecar = _hwp_source_paths(doc_name)
    try:
        manifest = extract_source(src, sidecar)
        ir = json.loads((sidecar / "document.json").read_text(encoding="utf-8"))
        result = export_hwp_chunks(
            ir, manifest["source_sha256"], sidecar / "chunks.jsonl"
        )
    except (OSError, TypeError, ValueError, RuntimeError, KeyError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def export_structure_aware_text_chunks(doc_name: str) -> str:
    """HWP 원문을 text-only 구조 인식 JSONL로 별도 내보낸다.

    사업목적·지원대상·지원 방식의 명시적 제목/콜론 라벨을 문맥으로 연결한다.
    원문 문단·표 셀 위치와 텍스트를 그대로 보존하며 표 셀 문자열은 독립 검색에
    사용하지 않고 검토 대상으로 남긴다. 의미 문단 병합·도식 해석·이미지 출력은
    수행하지 않는다. 기존 chunks.jsonl과 별도 structure_aware_text_chunks.jsonl이다.
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    from .hwp_source import extract_source
    from .structure_aware_text import export_structure_aware_text_chunks as export

    src, sidecar = _hwp_source_paths(doc_name)
    try:
        manifest = extract_source(src, sidecar)
        ir = json.loads((sidecar / "document.json").read_text(encoding="utf-8"))
        result = export(
            ir, manifest["source_sha256"], sidecar / "structure_aware_text_chunks.jsonl"
        )
    except (OSError, TypeError, ValueError, RuntimeError, KeyError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def inspect_c3_odl_figures(
    doc_name: str, c3_document_path: str, page: int, run_id: str = "",
    c3_element_id: str = "",
) -> str:
    """C3 원본 구조와 ODL 그림 후보를 같은 HWP/PDF에 한정해 조회한다.

    C3 결과는 별도로 생성된 `document.json` 경로를 지정한다. HWP/PDF 해시와
    변환 계보를 확인하고 PDF 파일 쪽의 ODL PNG를 반환한다. 후보가 실제 도식인지,
    온전한 크롭인지, 어느 HWP 문단에 속하는지는 확인 전까지 판단불가다.
    활성 ODL 결과가 없다면 `_runs` 아래의 명시적인 run_id를 전달한다.

    Args:
        doc_name: 02_normalized의 HWP 파일명
        c3_document_path: 해당 HWP로 생성하고 검증한 C3 document.json 절대 경로
        page: PDF 파일 기준 1부터 시작하는 쪽 번호
        run_id: 활성 파싱본 대신 사용할 32자리 ODL 실행 ID
        c3_element_id: 검토할 C3 문단/표 ID(예: S14:P876). 쪽 연결을 확정하지 않음
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    src = _corpus_dir() / "02_normalized" / doc_name
    if src.suffix.lower() != ".hwp":
        return json.dumps({"error": "C3 MVP는 원본 HWP만 지원합니다."}, ensure_ascii=False)
    pdf = _corpus_dir() / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
    out_dir = _corpus_dir() / "04_parsed" / pdf.stem
    from .c3_odl_figures import inspect_c3_odl_figures as inspect

    try:
        result = inspect(src, pdf, Path(c3_document_path), out_dir, page, run_id,
                         c3_element_id=c3_element_id)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def get_best_available(
    doc_name: str, output_kind: str, page: int = 0,
    c3_document_path: str = "", odl_run_id: str = "",
    rag_mode: str = "default",
) -> str:
    """실제 파일 형식과 원하는 출력에 맞는 검증된 경로를 실행한다.

    output_kind: structure | tables | visual | rag. HWP의 C3 결과를 이미
    만들어 뒀다면 c3_document_path를 지정한다. 없으면 원본 rhwp 구조를 쓴다.
    PDF 표·본문은 활성 ODL 파싱 결과를 사용한다. visual은 PDF 화면 렌더와
    ODL 표·이미지 후보를 함께 제공하되 대상 일치·크롭 완전성은 판단불가다.
    준비되지 않은 PDF 변환/파싱은 필요한 기존 MCP 도구를 반환한다.
    page는 tables/visual 조회에 필요하며 PDF 파일 기준 1부터 센다.
    rag_mode='text_only_structure_aware'는 HWP/HWPX rag의 원문 문단과 명시적
    항목 문맥을 보존한다. PDF에는 이 모드를 지원하지 않는다.
    """
    from .normalize import detect_format, sha256_of

    def response(**kwargs):
        return json.dumps({"document": doc_name, "output_kind": output_kind, **kwargs},
                          ensure_ascii=False)

    if output_kind not in {"structure", "tables", "visual", "rag"}:
        return response(status="error", error="output_kind는 structure/tables/visual/rag 중 하나")
    if rag_mode not in {"default", "text_only_structure_aware"}:
        return response(status="error", error="지원하지 않는 rag_mode입니다")
    if rag_mode != "default" and output_kind != "rag":
        return response(status="error", error="rag_mode는 output_kind='rag'에만 지정할 수 있습니다")
    if _safe_doc_name(doc_name) is None:
        return response(status="error", error="문서 이름에 경로가 들어갈 수 없습니다")
    if type(page) is not int or page < 0:
        return response(status="error", error="page는 0 이상의 정수여야 합니다")
    if output_kind == "visual" and page < 1:
        return response(status="error", error="visual에는 PDF 파일 쪽 page>=1이 필요합니다")

    root = _corpus_dir()
    src = root / "02_normalized" / doc_name
    if not src.is_file():
        return response(status="error", error=f"문서가 없습니다: {src}")
    actual = detect_format(src)
    if actual not in {"hwp", "hwpx", "pdf"}:
        return response(status="unsupported", detected_format=actual)
    if src.suffix.lower() != f".{actual}":
        return response(
            status="held", detected_format=actual,
            reason="실제 포맷과 확장자가 다릅니다. normalize 결과를 확인하세요.",
        )
    source_hash = sha256_of(src)
    base = {"detected_format": actual, "source_sha256": source_hash}
    if rag_mode != "default":
        base["rag_mode"] = rag_mode
        if actual == "pdf":
            return response(status="unsupported", **base,
                            reason="PDF의 text_only_structure_aware 모드는 지원하지 않습니다")
    if actual == "pdf" and output_kind == "tables" and page < 1:
        return response(status="error", **base,
                        error="PDF tables에는 파일 쪽 page>=1이 필요합니다")

    if actual in {"hwp", "hwpx"} and output_kind in {"structure", "tables", "rag"}:
        if c3_document_path and actual == "hwp" and output_kind != "rag":
            try:
                from .conversion_lineage import verify_conversion

                c3_path = Path(c3_document_path).resolve()
                c3 = json.loads(c3_path.read_text(encoding="utf-8"))
                meta = c3.get("meta") or {}
                refs = meta.get("source") or {}
                if (meta.get("kind") != "generic_c3_core"
                    or (refs.get("source_hwp") or {}).get("sha256") != source_hash):
                    raise ValueError("C3 원본 HWP 해시가 다릅니다")
                pdf = root / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
                if (not pdf.is_file()
                    or (refs.get("source_pdf") or {}).get("sha256") != sha256_of(pdf)
                    or (c3.get("pdf_evidence") or {}).get("sha256") != sha256_of(pdf)):
                    raise ValueError("C3 PDF 근거 해시가 다릅니다")
                lineage = verify_conversion(src, pdf, pdf.with_suffix(".source.json"))
                if lineage["status"] != "verified":
                    return response(status="held", route="c3", **base,
                                    reason="C3 HWP/PDF 변환 계보 미확정", lineage=lineage)
                return response(status="ready", route="c3", **base,
                                c3_document=str(c3_path), c3_sha256=sha256_of(c3_path),
                                counts=meta.get("counts"),
                                page_mapping="unassigned_for_most_source_elements")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                return response(status="held", route="c3", **base, reason=str(exc))
        if output_kind == "rag":
            exporter = (export_structure_aware_text_chunks
                        if rag_mode == "text_only_structure_aware" else export_hwp_rag_chunks)
            parsed = json.loads(exporter(doc_name))
        else:
            parsed = json.loads(parse_hwp_source(doc_name))
        if "error" in parsed:
            return response(status="error", **base, error=parsed["error"])
        return response(status="ready", route="rhwp_source", **base,
                        validation_scope=("HWPX 성능 비교 미완료" if actual == "hwpx"
                                          else "원본 IR 경계 보존"), result=parsed)

    pdf = src
    if actual in {"hwp", "hwpx"}:
        siblings = [root / "02_normalized" / f"{src.stem}{ext}"
                    for ext in (".hwp", ".hwpx") if ext != src.suffix.lower()]
        if any(p.is_file() for p in siblings):
            return response(status="held", **base,
                            reason="동일 stem의 HWP/HWPX가 있어 변환 PDF 경로가 충돌합니다")
        pdf = root / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
        if not pdf.is_file():
            return response(status="needs_preparation", **base,
                            next_tool="convert_hwp", reason="변환 PDF가 없습니다")
        from .conversion_lineage import verify_conversion

        lineage = verify_conversion(src, pdf, pdf.with_suffix(".source.json"))
        if lineage["status"] != "verified":
            return response(status="held", **base, lineage=lineage,
                            reason="HWP/HWPX→PDF 계보를 검증할 수 없습니다")
        base["conversion_lineage"] = lineage

    if output_kind == "visual":
        visual = json.loads(get_visual_evidence(doc_name, page, odl_run_id=odl_run_id))
        if "error" in visual:
            return response(status="error", route="odl_pdf_visual", **base,
                            error=visual["error"])
        c3 = None
        if c3_document_path and actual == "hwp":
            c3 = json.loads(inspect_c3_odl_figures(
                doc_name, c3_document_path, page, run_id=odl_run_id
            ))
            if "error" in c3:
                return response(status="held", route="c3_plus_odl_pdf_visual", **base,
                                reason=c3["error"], result=visual)
        return response(status="판단불가", route=("c3_plus_odl_pdf_visual" if c3
                        else "odl_pdf_visual"), **base, result=visual,
                        c3_source=c3)

    from .parsed_store import selected_json_files

    try:
        selected = selected_json_files(root / "04_parsed" / pdf.stem)
    except (OSError, ValueError) as exc:
        return response(status="held", route="odl_pdf", **base, reason=str(exc))
    if not selected:
        return response(status="needs_preparation", route="odl_pdf", **base,
                        next_tool="parse_document", reason="선택된 ODL JSON이 없습니다")
    if output_kind == "tables":
        result = json.loads(get_tables(doc_name, page=page, output_format="json"))
        if "error" in result:
            return response(status="error", route="odl_plus_pymupdf", **base,
                            error=result["error"])
        return response(status="판단불가", route="odl_plus_pymupdf", **base,
                        result=result)
    if output_kind == "rag":
        result = json.loads(export_rag_chunks(doc_name))
        if "error" in result:
            return response(status="error", route="odl_rag", **base, error=result["error"])
        return response(status="ready", route="odl_rag", **base, result=result)
    return response(status="ready", route="odl_pdf", **base,
                    parsed_json=[str(path) for path in selected],
                    pdf_sha256=sha256_of(pdf))


@mcp.tool()
def get_visual_evidence(
    doc_name: str, page: int, dpi: int = 190, bbox_top_left: list[float] | None = None,
    odl_run_id: str = "",
) -> str:
    """표 검출 실패에도 PDF 전체 쪽 이미지를 남기고 크롭 후보를 구분한다.

    ODL 표·이미지 좌표를 PDF 화면으로 합성 렌더한다. 원본 이미지 객체에 없는
    덧씌운 글자·도형도 화면에 보이는 대로 포함한다. 작은 조각은 별도 기록한다.
    검출 영역은 목표 표/도식과 같은지 검증되지 않았다. 제목·단위·각주·다음 쪽
    연결도 자동 확정하지 않는다. 반환 JSON의 PDF/이미지 해시로 근거를 고정한다.
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    from .visual_evidence import render_visual_evidence

    root = _corpus_dir()
    original = root / "02_normalized" / doc_name
    pdf = original
    is_hwp = original.suffix.lower() in (".hwp", ".hwpx")
    if is_hwp:
        pdf = root / "03_converted" / "hwp2pdf" / f"{original.stem}.pdf"
    if not pdf.is_file():
        return json.dumps({"error": f"PDF가 없습니다: {pdf}"}, ensure_ascii=False)
    try:
        result = render_visual_evidence(
            pdf, root / "06_visual_evidence",
            page=page, dpi=dpi, bbox_top_left=bbox_top_left,
            odl_out_dir=root / "04_parsed" / pdf.stem,
            odl_run_id=odl_run_id,
        )
    except (OSError, TypeError, ValueError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    if is_hwp:
        from .conversion_lineage import verify_conversion

        result["hwp_pdf_lineage"] = verify_conversion(
            original, pdf, pdf.with_suffix(".source.json")
        )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def recover_pdf_table_text(
    doc_name: str, page: int, table_index: int = 0, dpi: int = 190
) -> str:
    """PDF에서 검출한 표 한 개를 로컬 OCR로 읽어 검토 후보를 만든다.

    page는 PDF 파일 기준 1부터 세고, table_index는 해당 쪽에서
    PyMuPDF find_tables(snap_tolerance=1)가 반환한 0 기반 번호다.
    OCR 원문, 신뢰도, 표 셀 좌표를 저장하지만 자동으로 원 파싱 JSON을
    수정하거나 RAG 사용을 승인하지 않는다. 결과는 반드시 원문 이미지와
    대조해야 하며, 표 제목·단위·각주·이어짐은 판단불가로 남는다.

    RapidOCR와 로컬 한국어 모델이 필요하다. 모델 경로는
    GOVPARSE_OCR_MODEL_DIR 또는 GOVPARSE_DATA/models/rapidocr에서 찾는다.
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    if type(page) is not int or page < 1:
        return json.dumps({"error": "page는 1 이상의 정수여야 합니다."}, ensure_ascii=False)
    if type(table_index) is not int or table_index < 0:
        return json.dumps({"error": "table_index는 0 이상의 정수여야 합니다."}, ensure_ascii=False)
    if type(dpi) is not int or not 72 <= dpi <= 600:
        return json.dumps({"error": "dpi는 72~600 정수여야 합니다."}, ensure_ascii=False)

    root = _corpus_dir()
    src = root / "02_normalized" / doc_name
    if not src.is_file():
        return json.dumps({"error": f"문서가 없습니다: {src}"}, ensure_ascii=False)
    from .normalize import detect_format, sha256_of

    actual = detect_format(src)
    if actual not in {"pdf", "hwp", "hwpx"}:
        return json.dumps({"status": "unsupported", "detected_format": actual}, ensure_ascii=False)
    if src.suffix.lower() != f".{actual}":
        return json.dumps({
            "status": "held", "detected_format": actual,
            "reason": "실제 포맷과 확장자가 다릅니다. normalize 결과를 확인하세요.",
        }, ensure_ascii=False)

    pdf = src
    lineage = None
    if actual in {"hwp", "hwpx"}:
        pdf = root / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
        if not pdf.is_file():
            return json.dumps({"status": "needs_preparation", "next_tool": "convert_hwp",
                               "reason": "변환 PDF가 없습니다."}, ensure_ascii=False)
        from .conversion_lineage import verify_conversion

        lineage = verify_conversion(src, pdf, pdf.with_suffix(".source.json"))
        if lineage["status"] != "verified":
            return json.dumps({
                "status": "held", "source_sha256": sha256_of(src),
                "pdf_sha256": sha256_of(pdf), "hwp_pdf_lineage": lineage,
                "reason": "HWP/HWPX와 PDF의 변환 계보가 확인되지 않았습니다.",
            }, ensure_ascii=False)

    from .pdf_table_ocr import recover_pdf_table_text as recover

    try:
        result = recover(
            pdf_path=pdf,
            page=page,
            table_index=table_index,
            dpi=dpi,
            output_dir=root / "06_visual_evidence" / "table_ocr",
        )
    except ImportError as exc:
        return json.dumps({
            "status": "unavailable",
            "error": "로컬 OCR 선택 의존성이 없습니다. govparse[ocr-table] 설치를 확인하세요.",
            "detail": str(exc),
        }, ensure_ascii=False)
    except (OSError, TypeError, ValueError, RuntimeError, KeyError) as exc:
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)
    result["document"] = doc_name
    result["document_sha256"] = sha256_of(src)
    if lineage is not None:
        result["hwp_pdf_lineage"] = lineage
    # Persist the final provenance-enriched result, not just the module's base record.
    artifact_json = Path(result["artifact_json"])
    temp_json = artifact_json.with_name(f".{artifact_json.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
        os.replace(temp_json, artifact_json)
    finally:
        temp_json.unlink(missing_ok=True)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def diagnose_hwp_pdf_tables(doc_name: str, page: int) -> str:
    """원본 HWP 표와 PDF 파싱 표의 국소 구조 위험을 보수적으로 진단한다.

    문자열 증인은 표 연결의 후보일 뿐이다. 독립적으로 확인된 연결이 없으면
    행 붕괴를 확정하지 않고 판단불가와 보류 범위를 반환한다.
    """
    if _safe_doc_name(doc_name) is None:
        return json.dumps({"error": "문서 이름에 경로가 들어갈 수 없습니다."}, ensure_ascii=False)
    if type(page) is not int or page < 1:
        return json.dumps({"error": "page는 1 이상의 정수여야 합니다."}, ensure_ascii=False)
    from .conversion_lineage import verify_conversion
    from .hwp_source import extract_source
    from .parse_diagnostics import diagnose_tables
    from .table_identity import propose_links, suspect_row_collapses

    src, sidecar = _hwp_source_paths(doc_name)
    pdf = _corpus_dir() / "03_converted" / "hwp2pdf" / f"{src.stem}.pdf"
    lineage = verify_conversion(src, pdf, pdf.with_suffix(".source.json"))
    if lineage["status"] == "mismatch":
        return json.dumps({
            "document": doc_name, "page": page, "hwp_pdf_lineage": lineage,
            "comparison_performed": False, "table_identity_candidates": [],
            "diagnostics": [{
                "code": "hwp_pdf_lineage_mismatch", "status": "문제있음",
                "source_locator": {"hwp_path": str(src)},
                "pdf_locator": {"pdf_path": str(pdf), "page": page},
                "reason": lineage["reason"],
            }],
            "use_decision": {
                "source_hwp_retrieval": "held",
                "pdf_table_structure": "held", "automatic_judgment": "held",
                "held_claims": [{"claim": "hwp_pdf_same_source", "reason": lineage["reason"]}],
            },
        }, ensure_ascii=False)
    try:
        manifest = extract_source(src, sidecar)
        ir = json.loads((sidecar / "document.json").read_text(encoding="utf-8"))
        pdf_result = json.loads(get_tables(doc_name, page=page, output_format="json"))
        if "error" in pdf_result:
            return json.dumps(pdf_result, ensure_ascii=False)
        result = diagnose_tables(ir, pdf_result["tables"])
        proposals = propose_links(ir, pdf_result["tables"])
        suspected = suspect_row_collapses(ir, pdf_result["tables"], proposals)
    except (OSError, TypeError, ValueError, RuntimeError, KeyError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    result["diagnostics"] = [
        diag for diag in result["diagnostics"]
        if not (
            diag["code"] == "table_match_ambiguous"
            and proposals[diag["pdf_locator"]["table_index"]]["status"]
            == "unique_identity_witness"
        )
    ] + suspected
    if lineage["status"] != "verified":
        result["diagnostics"].append({
            "code": "hwp_pdf_lineage_unverified",
            "status": "판단불가",
            "source_locator": {"source_sha256": manifest["source_sha256"]},
            "pdf_locator": {"page": page, "pdf_path": str(pdf)},
            "reason": lineage["reason"],
        })
        result["use_decision"]["pdf_table_structure"] = "held"
        result["use_decision"]["held_claims"].append({
            "claim": "hwp_pdf_same_source",
            "reason": lineage["reason"],
        })
    return json.dumps({
        "document": doc_name, "source_hwp_sha256": manifest["source_sha256"],
        "page": page, "hwp_pdf_lineage": lineage,
        "comparison_performed": True,
        "table_identity_candidates": proposals, **result,
    }, ensure_ascii=False)


def main() -> None:
    # On Windows stdio MCP, first importing NumPy from a tool call can block the
    # event loop inside its native multiarray initialization. Initialize it before
    # the loop when the optional local OCR stack is installed. Keep the base MCP
    # usable without OCR dependencies.
    try:
        import numpy  # noqa: F401
    except ImportError:
        pass
    mcp.run()


if __name__ == "__main__":
    main()
