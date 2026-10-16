"""3단계: HWP/HWPX -> PDF 변환 (한컴오피스 COM).

파일마다 워커 프로세스를 띄우고 타임아웃을 건다. 한 건이 COM 안에서 멈춰도
배치 전체가 멈추지 않게 하기 위해서다.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import CONVERTED, NORMALIZED, REPORTS

HWP_PDF_DIR = CONVERTED / "hwp2pdf"

# 한컴 2020 의 실제 프로세스명은 Hwp.exe 다 (Hword.exe 가 아니다).
# Quit() 만으로는 인스턴스가 남는 일이 잦고, 쌓이면 다음 COM 연결이 좀비에 붙어
# 무한 대기한다. 실제로 12개까지 쌓여 배치가 멈춘 적이 있어 파일마다 강제 정리한다.
HANCOM_IMAGES = ("Hwp.exe", "Hword.exe", "Hcell.exe", "Hshow.exe")


def kill_hancom() -> None:
    for img in HANCOM_IMAGES:
        subprocess.run(["taskkill", "/F", "/IM", img], capture_output=True, check=False)


@dataclass
class ConvertResult:
    src: str
    agency: str
    src_format: str
    ok: bool = False
    pages: int = 0
    src_mb: float = 0.0
    pdf_mb: float = 0.0
    elapsed_s: float = 0.0
    error: str = ""


@dataclass
class ConvertReport:
    results: list[ConvertResult] = field(default_factory=list)


def _agency_of(name: str) -> str:
    return name.split("__", 1)[0]


def convert_one(src: Path, dst: Path, timeout: int) -> tuple[bool, int, str]:
    """워커 프로세스로 한 건 변환. (성공, 페이지수, 오류) 반환."""
    cmd = [sys.executable, "-m", "govparse.hwp_worker", str(src), str(dst)]
    try:
        # The worker must not inherit the MCP server's protocol input pipe.
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, 0, f"timeout>{timeout}s"

    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace").strip()

    pages = 0
    for line in out.splitlines():
        if line.startswith("pages="):
            pages = int(line.split("=", 1)[1] or 0)

    if proc.returncode != 0:
        return False, pages, (err.splitlines() or ["알 수 없는 오류"])[-1][:300]
    return True, pages, ""


def targets(limit: int | None = None, max_mb: float | None = None) -> list[Path]:
    """변환 대상 목록. 작은 파일부터 처리해 조기에 문제를 드러낸다."""
    items = [p for p in NORMALIZED.iterdir() if p.suffix.lower() in (".hwp", ".hwpx")]
    items.sort(key=lambda p: p.stat().st_size)
    if max_mb is not None:
        items = [p for p in items if p.stat().st_size / 1024**2 <= max_mb]
    return items[:limit] if limit else items


def run(
    limit: int | None = None,
    max_mb: float | None = None,
    timeout: int = 1800,
    skip_existing: bool = True,
) -> ConvertReport:
    HWP_PDF_DIR.mkdir(parents=True, exist_ok=True)
    report = ConvertReport()
    items = targets(limit, max_mb)

    for i, src in enumerate(items, 1):
        dst = HWP_PDF_DIR / (src.stem + ".pdf")
        src_mb = round(src.stat().st_size / 1024**2, 1)
        print(f"[{i}/{len(items)}] {src.name[:64]}  ({src_mb} MB)", flush=True)

        if skip_existing and dst.exists() and dst.stat().st_size > 0:
            print("    이미 변환됨 — 건너뜀", flush=True)
            report.results.append(
                ConvertResult(
                    src=src.name, agency=_agency_of(src.name),
                    src_format=src.suffix.lstrip(".").lower(), ok=True,
                    src_mb=src_mb, pdf_mb=round(dst.stat().st_size / 1024**2, 1),
                )
            )
            continue

        kill_hancom()
        t = time.time()
        ok, pages, err = convert_one(src, dst, timeout)
        elapsed = round(time.time() - t, 1)
        kill_hancom()

        res = ConvertResult(
            src=src.name, agency=_agency_of(src.name),
            src_format=src.suffix.lstrip(".").lower(),
            ok=ok, pages=pages, src_mb=src_mb, elapsed_s=elapsed, error=err,
        )
        if ok and dst.exists():
            res.pdf_mb = round(dst.stat().st_size / 1024**2, 1)
            print(f"    OK  {pages}p  {res.pdf_mb} MB  {elapsed}s", flush=True)
        else:
            print(f"    실패  {err[:120]}", flush=True)
        report.results.append(res)

    REPORTS.mkdir(parents=True, exist_ok=True)
    with open(REPORTS / "convert_report.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(report.results[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(r) for r in report.results)

    return report
