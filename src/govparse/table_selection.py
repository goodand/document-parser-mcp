"""Select per-table PNGs by comparing candidate images against the rendered source."""
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import os
import shutil

import pymupdf

from .selector_evidence.assess import assess
from .selector_evidence.geometry import geometry_check
from .selector_evidence.legacy import generate_legacy_candidates
from .selector_evidence.locator import PageIndex, locate
from .selector_evidence.pages import build_page_index
from .selector_evidence.projected import verify
from .selector_evidence.recover import recover
from .selector_evidence.reference import render_reference_pdf
from .selector_evidence.source_crop import make_source_crop
from .table_images import extract_table_images_by_id, table_index


def _select_one(pdf, ir, key, table, record, native_dir, candidates, entry, pages, temp):
    native = [native_dir / name for name in record["paths"]]
    rejected_geometry = False
    if assess(ir, key, native, record["status"] == "rendered")["eligible"]:
        verdict, _ = geometry_check(pdf, pages, table, native, page_entry=entry)
        if verdict is not False:
            return "november", native
        rejected_geometry = True
    accepted = []
    for item in candidates:
        if pages and item["page"] not in pages:
            continue
        path = Path(item["png_path"])
        same = (table["rows"], table["cols"]) == (item["detected_rows"], item["detected_cols"])
        result = assess(ir, key, [path], same)
        eligible = result["eligible"]
        placements = result.get("pictures", {}).get("placements", [])
        if not eligible and same and placements:
            eligible, _ = verify(pdf, item["page"], item["bbox"], path, ir, placements)
        if not eligible and len(pages) == 1 and entry.get("status") == "verified":
            eligible, _ = recover(pdf, table, entry, item, path, ir)
        if eligible:
            verdict, _ = geometry_check(pdf, pages, table, [path], page_entry=entry)
            if verdict is False:
                rejected_geometry = True
            else:
                accepted.append(path)
    if len(accepted) == 1:
        return "october", accepted
    if pages and rejected_geometry:
        cropped = temp / "source-crop.png"
        ok, _ = make_source_crop(pdf, table, entry, cropped)
        if ok:
            return "source_crop", [cropped]
    page_files = []
    for pg in pages:
        filename = temp / f"source-page-{pg}.png"
        if not filename.exists():
            pdf[pg - 1].get_pixmap(dpi=190, alpha=False).save(filename)
        page_files.append(filename)
    return ("source_page", page_files) if pages else ("review", [])


def _publish(files, destination, temp):
    names, hashes = [], []
    for path in files:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        name = f"table-{digest}.png"
        if not (destination / name).exists():
            staged = temp / "publish.tmp"
            shutil.copyfile(path, staged)
            os.replace(staged, destination / name)
        names.append(name)
        hashes.append(digest)
    return names, hashes


def extract_selected_table_images(source, ir, out_dir, table_ids=None):
    """Write selected PNGs and publish the corresponding manifest last."""
    # The PDF library writes its optional layout recommendation to stdout.
    # MCP reserves that stream for JSON-RPC frames.
    pymupdf.no_recommend_layout()
    source, out_dir = Path(source), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out_dir) as scratch:
        temp = Path(scratch)
        native_dir = temp / "isolated"
        manifest = extract_table_images_by_id(source, ir, native_dir, table_ids=table_ids)
        rows = manifest["tables"]
        if rows:
            try:
                pdf_path = render_reference_pdf(source, temp / "reference.pdf")
            except (ValueError, RuntimeError, OSError) as exc:
                # Large HWPX sections can exceed rhwp's source-render limit.
                for row in rows.values():
                    files = [native_dir / p for p in row["paths"]]
                    names, hashes = _publish(files, out_dir, temp)
                    row.update(choice="november" if names else "review", paths=names,
                               path=next(iter(names), None), sha256=hashes, source_pages=[],
                               selection_warning=f"source_render_unavailable:{type(exc).__name__}")
            else:
                _select_document(source, ir, pdf_path, manifest, native_dir, out_dir, temp)
        manifest.update(method_contract="source_verified_table_selection_v2",
                        rendered_count=sum(row["status"] == "rendered" for row in rows.values()),
                        source_page_count=sum(row["choice"] == "source_page" for row in rows.values()))
        tmp_manifest = temp / "selected.json"
        tmp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
        os.replace(tmp_manifest, out_dir / "manifest.json")
        return manifest


def _select_document(source, ir, pdf_path, manifest, native_dir, out_dir, temp):
    old = defaultdict(list)
    for item in generate_legacy_candidates(pdf_path, ir, temp / "legacy")["candidates"]:
        if item.get("native_table_id"):
            old[item["native_table_id"]].append(item)
    index = build_page_index(source, ir, expected_sha256=manifest["source_sha256"], pdf=pdf_path)
    located = {}
    with pymupdf.open(pdf_path) as pdf:
        text_index = PageIndex(pdf)
        for key, table in table_index(ir).items():
            entry = index.get(key, {})
            pages = entry.get("pages") or locate(table, text_index) or sorted({c["page"] for c in old[key]})
            parent = key.rpartition("/c")[0]
            if not pages and len(located.get(parent, [])) == 1:
                pages = located[parent]
            located[key] = pages
            row = manifest["tables"].get(key)
            if row is None:
                continue
            choice, files = _select_one(pdf, ir, key, table, row, native_dir, old[key], entry, pages, temp)
            names, hashes = _publish(files, out_dir, temp)
            status = {"source_page": "source_page", "review": "unmatched"}.get(choice, "rendered")
            row.update(status=status, choice=choice, source_pages=pages,
                       page_hint=pages[0] if pages else None, method={"november":"isolated_native_table",
                       "october":"legacy_pdf_crop","source_crop":"verified_source_crop",
                       "source_page":"source_page","review":"failed"}[choice],
                       paths=names, path=next(iter(names), None), sha256=hashes,
                       reason={"review":"no_verified_candidate_or_page","source_page":"no_complete_candidate"}.get(choice))
