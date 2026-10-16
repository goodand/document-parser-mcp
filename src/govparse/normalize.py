"""2단계: 평탄화 + 리네이밍 + 인벤토리(manifest) 생성.

- 중첩 디렉토리를 한 겹으로 펴되, 원래 경로 정보는 파일명에 남긴다.
- 확장자를 믿지 않고 매직넘버로 실제 포맷을 판정한다.
- 같은 볼륨이면 하드링크로 연결해 디스크를 두 배로 쓰지 않는다.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
import zipfile
from pathlib import Path

from .config import EXTRACTED, NORMALIZED, REPORTS

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
DOC_EXTS = {".hwp", ".hwpx", ".pdf"}


def detect_format(path: Path) -> str:
    """매직넘버 기반 실제 포맷 판정. 확장자와 다를 수 있다."""
    with open(path, "rb") as fh:
        head = fh.read(8)

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(OLE_MAGIC):
        return "hwp"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            if "mimetype" in names or any(n.startswith("Contents/") for n in names):
                return "hwpx"
        except zipfile.BadZipFile:
            return "unknown"
        return "zip"
    return "unknown"


def sha256_of(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def flat_name(agency: str, rel: Path) -> str:
    """'기후부' + '예산/5권.pdf' -> '기후부__예산~5권.pdf' (경로 정보 보존)."""
    stem_parts = list(rel.parts)
    joined = "~".join(stem_parts)
    joined = re.sub(r"\s+", " ", joined).strip()
    name = f"{agency}__{joined}"
    return name[:180] + Path(joined).suffix if len(name) > 200 else name


def link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def run(src_dir: Path = EXTRACTED, out_dir: Path = NORMALIZED) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for agency_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
        agency = agency_dir.name
        for path in sorted(agency_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(agency_dir)
            ext = path.suffix.lower()
            fmt = detect_format(path)

            name = flat_name(agency, rel)
            dst = out_dir / name
            link_or_copy(path, dst)

            rows.append(
                {
                    "agency": agency,
                    "normalized_name": name,
                    "source_rel": str(rel),
                    "ext_declared": ext.lstrip("."),
                    "format_actual": fmt,
                    "ext_mismatch": int(ext.lstrip(".") != fmt),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_of(path),
                    "is_document": int(ext in DOC_EXTS or fmt in {"pdf", "hwp", "hwpx"}),
                }
            )

    # 내용이 완전히 같은 파일 묶기
    groups: dict[str, int] = {}
    for r in rows:
        groups.setdefault(r["sha256"], len(groups))
    for r in rows:
        r["dup_group"] = groups[r["sha256"]]

    # 문서가 0건이어도 헤더만 있는 manifest 를 남긴다.
    # rows[0] 로 필드를 뽑으면 빈 디렉토리에서 IndexError 로 죽는다.
    fields = list(rows[0].keys()) if rows else [
        "agency", "normalized_name", "source_rel", "ext_declared", "format_actual",
        "ext_mismatch", "size_bytes", "sha256", "is_document", "dup_group",
    ]
    manifest = REPORTS / "manifest.csv"
    with open(manifest, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    return rows
