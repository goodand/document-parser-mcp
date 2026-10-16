"""govparse CLI - HWP/HWPX/PDF 문서 파싱 파이프라인 진입점."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="HWP/HWPX/PDF 문서 파싱 파이프라인", no_args_is_help=True)


@app.callback()
def _main() -> None:
    """서브커맨드를 지정해 실행합니다."""
console = Console()


@app.command()
def doctor() -> None:
    """실행 환경(Java, 한컴오피스, 디스크) 점검."""
    import shutil
    import subprocess
    from pathlib import Path

    ok = True

    from .config import find_java

    java = find_java()
    if java:
        ver = subprocess.run(
            [java, "-version"], capture_output=True, text=True
        ).stderr.splitlines()[0]
        console.print(f"[green]OK[/]  java   : {ver}")
    else:
        console.print("[red]FAIL[/] java   : 미설치 (opendataloader-pdf 실행 불가)")
        ok = False

    hwp = Path(r"C:\Program Files (x86)\Hnc\Office 2020\HOffice110\Bin\Hword.exe")
    if hwp.exists():
        console.print(f"[green]OK[/]  한컴   : {hwp}")
    else:
        console.print("[red]FAIL[/] 한컴   : Hword.exe 미발견 (HWP 변환 불가)")
        ok = False

    try:
        import opendataloader_pdf  # noqa: F401

        console.print("[green]OK[/]  odl-pdf: import 성공")
    except ImportError as e:
        console.print(f"[red]FAIL[/] odl-pdf: {e}")
        ok = False

    free_gb = shutil.disk_usage("C:/").free / 1024**3
    tag = "green" if free_gb > 30 else "yellow"
    console.print(f"[{tag}]--[/]  디스크 : 여유 {free_gb:.0f} GB (권장 30GB+)")

    raise typer.Exit(0 if ok else 1)


@app.command()
def extract() -> None:
    """1단계: zip 해제 (CP949 파일명 복원, 중첩 zip 재귀)."""
    from . import extract as _extract
    from .config import EXTRACTED

    with console.status("압축 해제 중..."):
        stats = _extract.run()

    console.print(f"[green]완료[/] 아카이브 {stats.archives}개 / 파일 {stats.files}개")
    console.print(f"  파일명 CP949 복원: {stats.renamed}건")
    console.print(f"  중첩 zip: {stats.nested_zips}건")
    console.print(f"  출력: {EXTRACTED}")
    for e in stats.errors:
        console.print(f"  [red]오류[/] {e}")


@app.command()
def normalize() -> None:
    """2단계: 평탄화 + 리네이밍 + manifest.csv 생성."""
    from collections import Counter

    from rich.table import Table

    from . import normalize as _normalize
    from .config import NORMALIZED, REPORTS

    with console.status("인벤토리 작성 중 (해시 계산)..."):
        rows = _normalize.run()

    docs = [r for r in rows if r["is_document"]]
    by_fmt = Counter(r["format_actual"] for r in docs)
    by_agency = Counter(r["agency"] for r in docs)

    table = Table(title="부처별 문서")
    table.add_column("부처")
    table.add_column("문서수", justify="right")
    table.add_column("용량(MB)", justify="right")
    for agency, n in sorted(by_agency.items(), key=lambda x: -x[1]):
        mb = sum(r["size_bytes"] for r in docs if r["agency"] == agency) / 1024**2
        table.add_row(agency, str(n), f"{mb:,.0f}")
    console.print(table)

    console.print(f"[green]완료[/] 문서 {len(docs)}개 - {dict(by_fmt)}")

    mismatch = [r for r in docs if r["ext_mismatch"]]
    if mismatch:
        console.print(f"[yellow]확장자 불일치 {len(mismatch)}건[/]")
        for r in mismatch[:10]:
            console.print(
                f"  {r['normalized_name']} : .{r['ext_declared']} -> {r['format_actual']}"
            )

    dups = Counter(r["dup_group"] for r in docs)
    repeated = [g for g, c in dups.items() if c > 1]
    if repeated:
        console.print(f"[yellow]내용 동일 중복 {len(repeated)}쌍[/]")

    console.print(f"  출력: {NORMALIZED}")
    console.print(f"  인벤토리: {REPORTS / 'manifest.csv'}")


@app.command()
def parse(
    chunk_size: int = typer.Option(50, help="청크 페이지 수 (청킹 사용 시)"),
    heap: str = typer.Option("", help="JVM 힙. 비우면 시스템 RAM에서 자동 산정"),
    whole: bool = typer.Option(False, "--whole", help="통짜 파싱 우선. 실패한 파일만 청킹 폴백"),
    no_stitch: bool = typer.Option(False, "--no-stitch", help="경계 스티치 재파싱 생략"),
    limit: int = typer.Option(0, help="앞 N개 문서만 (시험용)"),
) -> None:
    """4단계: PDF 파싱 + 청크 경계 감사."""
    import psutil

    from . import chunking

    ram_gb = psutil.virtual_memory().total / 1024**3
    if not heap:
        # 시스템 RAM의 절반, 2~24GB 사이로 제한
        heap = f"{max(2, min(24, int(ram_gb // 2)))}g"
    auto_whole = whole or ram_gb >= 32

    console.print(f"RAM {ram_gb:.0f}GB / 힙 -Xmx{heap} / 통짜우선={auto_whole}")

    rep = chunking.run(
        chunk_size=chunk_size,
        heap=heap,
        allow_whole=auto_whole,
        stitch=not no_stitch,
        limit=limit or None,
    )
    _summarize(rep)


def _summarize(rep) -> None:
    from .config import REPORTS

    total = len(rep.chunks)
    failed = [c for c in rep.chunks if not c.ok]
    tables = sum(c.n_tables for c in rep.chunks)
    pages = sum(c.page_end - c.page_start for c in rep.chunks if c.ok)
    secs = sum(c.elapsed_s for c in rep.chunks)

    console.print(
        f"[green]완료[/] 단위 {total}개 / 성공 {total - len(failed)} / 실패 {len(failed)}"
    )
    console.print(f"  페이지 {pages:,} · 표 {tables:,} · 소요 {secs / 60:.0f}분")

    if failed:
        console.print(f"[red]파싱 실패 {len(failed)}건[/]")
        for c in failed[:10]:
            console.print(
                f"  {c.doc[:50]} #{c.chunk_idx} "
                f"p{c.page_start}-{c.page_end}: {c.error[:90]}"
            )

    susp = [b for b in rep.boundaries if b.suspected_table_split]
    console.print(f"  청크 경계 {len(rep.boundaries)}개 중 표 분할 의심 {len(susp)}건")
    recovered = [b for b in susp if b.stitched]
    if recovered:
        console.print(f"  스티치 복원 성공 {len(recovered)}건")
    console.print(f"  리포트: {REPORTS / 'parse_report.json'}")


@app.command()
def report() -> None:
    """5단계: manifest + parse_report 를 단일 dashboard.json 으로 합친다."""
    import csv
    import json
    from collections import Counter

    from .config import REPORTS

    man_path = REPORTS / "manifest.csv"
    if not man_path.exists():
        console.print("[red]manifest.csv 없음 — govparse normalize 먼저 실행[/]")
        raise typer.Exit(1)

    with open(man_path, encoding="utf-8-sig") as fh:
        docs = [r for r in csv.DictReader(fh) if r["is_document"] == "1"]

    parse_path = REPORTS / "parse_report.json"
    parsed = json.load(open(parse_path, encoding="utf-8")) if parse_path.exists() else None

    out = {
        "inventory": {
            "total_docs": len(docs),
            "by_format": dict(Counter(d["format_actual"] for d in docs)),
            "by_agency": [
                {
                    "agency": a,
                    "docs": n,
                    "mb": round(
                        sum(int(d["size_bytes"]) for d in docs if d["agency"] == a) / 1024**2
                    ),
                    "formats": dict(
                        Counter(d["format_actual"] for d in docs if d["agency"] == a)
                    ),
                }
                for a, n in Counter(d["agency"] for d in docs).most_common()
            ],
            "ext_mismatch": [
                {
                    "name": d["normalized_name"],
                    "declared": d["ext_declared"],
                    "actual": d["format_actual"],
                }
                for d in docs
                if d["ext_mismatch"] == "1"
            ],
        },
        "parsing": parsed,
    }

    dst = REPORTS / "dashboard.json"
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    console.print(f"[green]작성[/] {dst}")


if __name__ == "__main__":
    app()
