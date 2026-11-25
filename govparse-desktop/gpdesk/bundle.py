"""JSON + PNG bundle: original IR untouched under ``source_ir`` plus an asset list.

Asset origins are kept apart: ``embedded_picture`` (bytes from the source file),
``table_image`` (one table rendered on its own by the engine after matching the source XML;
linked by table_id) and ``rendered_table_capture`` (crop of a rendered page; fallback for an
engine without the per-table renderer). Captures are linked to a page only; no table/cell link is invented.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .embedded import collect_picture_uris

CAPTURE = re.compile(r"^p(\d{4})_t(\d+)\.png$")


@dataclass
class OcrStatus:
    status: str  # ok | empty | failed | not_run
    texts: list[str] = field(default_factory=list)
    error: str = ""


def parse_saved_count(reply: str) -> int | None:
    """extract_table_images returns prose; derive how many PNGs it says it wrote."""
    m = re.search(r"표 이미지 (\d+)장 저장", reply)
    if m:
        return int(m.group(1))
    if "표가 없습니다" in reply:
        return 0
    return None


def collect_table_pngs(img_dir: Path, expected: int) -> list[tuple[int, Path]]:
    files = sorted(p for p in Path(img_dir).iterdir() if p.is_file()) if Path(img_dir).exists() else []
    foreign = [p.name for p in files if not CAPTURE.match(p.name)]
    if foreign:
        raise ValueError(f"작업 출력 폴더에 다른 파일이 섞여 있습니다: {foreign[:5]}")
    if len(files) != expected:
        raise ValueError(f"보고된 표 이미지 {expected}장과 실제 파일 {len(files)}장이 다릅니다.")
    return [(int(CAPTURE.match(p.name).group(1)), p) for p in files]


def build_json_bundle(out: Path, ir: dict, source_name: str, source_sha256: str, *,
                      captures: list[tuple[int, Path]], capture_state: str,
                      embedded: dict[int, tuple[str, bytes]], ocr: dict[str, OcrStatus],
                      table_images: dict | None = None) -> dict:
    out = Path(out)
    assets = []
    uris = collect_picture_uris(ir)
    missing = []
    for uri in uris:
        bid = int(uri.split("://", 1)[1]) if uri.startswith("bin://") and uri[6:].isdigit() else None
        if bid is None or bid not in embedded:
            missing.append(uri)
            continue
        name, data = embedded[bid]
        rel = f"assets/embedded/bin{bid}{Path(name).suffix.lower() or '.bin'}"
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(data)
        assets.append({"origin": "embedded_picture", "uri": uri, "path": rel,
                       "source_stream": name})
    for page, src in captures:
        rel = f"assets/tables/{src.name}"
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(src.read_bytes())
        st = ocr.get(src.name, OcrStatus("not_run"))
        assets.append({"origin": "rendered_table_capture", "page": page, "path": rel,
                       "table_ref": None, "ocr": asdict(st)})
    from .table_ids import iter_table_images  # IR blocks already annotated by desktop_worker.table_images

    for table_id, paths in iter_table_images(ir):
        for part, rel in enumerate(paths, start=1):
            assets.append({"origin": "table_image", "table_id": table_id, "part": part, "parts": len(paths),
                           "path": rel})
    summary = {
        "source_name": source_name,
        "source_sha256": source_sha256,
        "capture_state": capture_state,  # ok | none_detected | failed | unverified
        "counts": {
            "embedded_picture": sum(a["origin"] == "embedded_picture" for a in assets),
            "rendered_table_capture": len(captures) if capture_state in ("ok", "none_detected") else None,
            "table_image": sum(a["origin"] == "table_image" for a in assets),
        },
        "table_images": table_images,
        "missing_pictures": missing,
        "notes": ("table_image is linked to its IR table by table_id; rendered_table_capture is linked to a "
                  "PDF page only; table/cell link not proven."),
    }
    payload = {"source_ir": ir, "govparse_desktop": {**summary, "assets": assets}}
    (out / "document.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    return summary
