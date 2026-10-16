"""파이프라인 공통 설정.

경로는 환경변수로 덮어쓸 수 있다 (다른 PC에서 실행할 때 사용):
  GOVPARSE_SOURCE : 원본 자료 디렉토리
  GOVPARSE_DATA   : 산출물 루트
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def find_java() -> str | None:
    """Find the Java executable used by diagnostics and PDF parsing."""
    if executable := shutil.which("java"):
        return executable
    if home := os.environ.get("JAVA_HOME"):
        candidate = Path(home) / "bin" / ("java.exe" if platform.system() == "Windows" else "java")
        if candidate.is_file():
            return str(candidate)
    executable = "java.exe" if platform.system() == "Windows" else "java"
    bundled = (PROJECT_ROOT / ".runtime").glob(f"jdk-*/bin/{executable}")
    for candidate in sorted(bundled, reverse=True):
        if candidate.is_file():
            return str(candidate)
    return None

DATA = Path(os.environ.get("GOVPARSE_DATA") or Path.home() / ".govparse")

_DEFAULT_SOURCE = DATA / "input"
SOURCE_DIR = Path(os.environ.get("GOVPARSE_SOURCE") or _DEFAULT_SOURCE)

RAW = DATA / "00_raw"
EXTRACTED = DATA / "01_extracted"  # zip 해제 (CP949 파일명 복원)
NORMALIZED = DATA / "02_normalized"  # 평탄화 + 리네이밍
CONVERTED = DATA / "03_converted"  # HWP->HWPX, PDF 청크 분할
PARSED = DATA / "04_parsed"  # Markdown / JSON
REPORTS = DATA / "05_reports"

# --- 저사양 기본값 (RAM 6GB 실측) ---
# 50p 청크 + -Xmx3g 에서 청크당 약 12초. 고사양 PC는 govparse parse --whole 로 통짜 처리.
JVM_HEAP = os.environ.get("GOVPARSE_HEAP", "3g")
PDF_CHUNK_PAGES = int(os.environ.get("GOVPARSE_CHUNK_PAGES", "50"))

HANCOM_EXE = Path(r"C:\Program Files (x86)\Hnc\Office 2020\HOffice110\Bin\Hword.exe")

# zip 내부 한글 파일명이 CP949로 저장된 경우의 복원 경로
ZIP_NAME_FALLBACK = ("cp437", "cp949")
