"""Bind a converted PDF to the exact HWP bytes used to create it.

A matching document name or a valid PDF hash alone cannot prove conversion
lineage. The sidecar records paths, sizes, and hashes for both files. A missing
sidecar stays unverified; it is never treated as a successful conversion.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1


def _fingerprint(path: Path) -> dict:
    """Hash and count the same byte stream, avoiding separate stat/hash races."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
            size += len(block)
    return {"path": str(path.resolve()), "sha256": digest.hexdigest(), "size": size}


def record_conversion(
    hwp: Path, pdf: Path, manifest_path: Path, expected_source_sha256: str | None = None
) -> dict:
    """Atomically record the two files that form one HWP-to-PDF conversion."""
    hwp = Path(hwp)
    pdf = Path(pdf)
    manifest_path = Path(manifest_path)
    if hwp.resolve() == pdf.resolve():
        raise ValueError("HWP and PDF paths must differ")
    if not hwp.is_file() or not pdf.is_file():
        raise FileNotFoundError("Both source HWP and converted PDF must exist")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_hwp": _fingerprint(hwp),
        "converted_pdf": _fingerprint(pdf),
    }
    if (
        expected_source_sha256 is not None
        and manifest["source_hwp"]["sha256"] != expected_source_sha256
    ):
        raise ValueError("HWP 원본이 변환 중 변경되었습니다")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, manifest_path)
    finally:
        tmp.unlink(missing_ok=True)
    return manifest


def verify_conversion(hwp: Path, pdf: Path, manifest_path: Path) -> dict:
    """Return verified, mismatch, or unverified for the current file bytes."""
    hwp = Path(hwp)
    pdf = Path(pdf)
    manifest_path = Path(manifest_path)
    result = {"manifest_path": str(manifest_path.resolve())}
    if not manifest_path.is_file():
        return result | {"status": "unverified", "reason": "conversion_manifest_missing"}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return result | {"status": "mismatch", "reason": f"manifest_unreadable: {exc}"}
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        return result | {"status": "mismatch", "reason": "manifest_schema_mismatch"}
    if not hwp.is_file() or not pdf.is_file():
        return result | {"status": "mismatch", "reason": "source_or_pdf_missing"}

    try:
        current = {"source_hwp": _fingerprint(hwp), "converted_pdf": _fingerprint(pdf)}
    except OSError as exc:
        return result | {"status": "mismatch", "reason": f"source_or_pdf_unreadable: {exc}"}

    differences = [
        f"{kind}.{field}"
        for kind, fields in current.items()
        for field in ("path", "sha256", "size")
        if not isinstance(manifest.get(kind), dict)
        or manifest[kind].get(field) != fields[field]
    ]
    if differences:
        return result | {
            "status": "mismatch",
            "reason": "conversion_lineage_mismatch",
            "differences": differences,
            "current": current,
        }
    return result | {
        "status": "verified",
        "reason": "source_and_pdf_paths_hashes_sizes_match",
        "source_hwp_sha256": current["source_hwp"]["sha256"],
        "converted_pdf_sha256": current["converted_pdf"]["sha256"],
    }
