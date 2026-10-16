"""1단계: zip 해제 — CP949 파일명 복원 + 중첩 zip 재귀 처리."""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import EXTRACTED, SOURCE_DIR

AGENCY_RE = re.compile(r"^\(([^)]+)\)")


def agency_of(name: str) -> str:
    """'(고용부)_2026년도...' -> '고용부'. 괄호가 없으면 확장자 뺀 앞부분."""
    m = AGENCY_RE.match(name)
    return m.group(1) if m else Path(name).stem[:20]


def decode_zip_name(info: zipfile.ZipInfo) -> str:
    """zip 내부 한글 파일명 복원.

    UTF-8 플래그(0x800)가 없으면 zipfile이 cp437로 디코딩해 둔 상태이므로,
    되돌려 cp949로 다시 읽는다. 실패하면 원래 이름을 그대로 쓴다.
    """
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def safe_relpath(name: str) -> Path:
    """zip-slip 방지: 절대경로/상위참조 제거."""
    parts = [p for p in Path(name.replace("\\", "/")).parts if p not in ("", ".", "..")]
    parts = [re.sub(r'[<>:"|?*]', "_", p) for p in parts]
    return Path(*parts) if parts else Path("_unnamed")


@dataclass
class ExtractStats:
    archives: int = 0
    files: int = 0
    nested_zips: int = 0
    renamed: int = 0
    errors: list[str] = field(default_factory=list)


def extract_zip(zip_path: Path, dest: Path, stats: ExtractStats, depth: int = 0) -> None:
    """zip 하나를 dest 아래로 푼다. 내부에 zip이 또 있으면 재귀."""
    if depth > 3:
        stats.errors.append(f"{zip_path.name}: 중첩 깊이 초과")
        return

    stats.archives += 1
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            decoded = decode_zip_name(info)
            if decoded != info.filename:
                stats.renamed += 1

            target = dest / safe_relpath(decoded)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
            except Exception as e:  # noqa: BLE001
                stats.errors.append(f"{zip_path.name}::{decoded}: {e}")
                continue
            stats.files += 1

            if target.suffix.lower() == ".zip":
                stats.nested_zips += 1
                inner_dest = target.with_suffix("")
                extract_zip(target, inner_dest, stats, depth + 1)
                target.unlink()


def run(source_dir: Path = SOURCE_DIR, out_dir: Path = EXTRACTED) -> ExtractStats:
    stats = ExtractStats()
    out_dir.mkdir(parents=True, exist_ok=True)

    for item in sorted(source_dir.iterdir()):
        if not item.is_file():
            continue
        agency = agency_of(item.name)
        dest = out_dir / agency

        if item.suffix.lower() == ".zip":
            extract_zip(item, dest, stats)
        else:
            # 최상위 단일 문서: 그대로 복사해 이후 단계에서 동일하게 취급
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / item.name
            if not target.exists() or target.stat().st_size != item.stat().st_size:
                shutil.copy2(item, target)
            stats.files += 1

    return stats
