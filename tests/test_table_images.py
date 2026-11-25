"""Regression coverage for source-bound, per-table HWPX image extraction.

The published desktop fixtures are intentionally used as input: CI must carry
them rather than quietly skipping these tests when a local sample is absent.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from copy import deepcopy
from pathlib import Path

import pymupdf
import pytest
from lxml import etree as ET

from govparse.table_images import extract_table_images_by_id


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = Path(os.environ.get("GOVPARSE_TEST_SAMPLES", REPO_ROOT / "govparse-desktop" / "samples"))
FIGURES = SAMPLES / "synthetic-figures.hwpx"
THIRTY_FIVE = SAMPLES / "synthetic-35tables.hwpx"
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
NS = {"hp": HP[1:-1]}


def _ir(source: Path) -> dict:
    import rhwp

    return json.loads(rhwp.parse(str(source)).to_ir_json())


def _png_paths(record: dict) -> list[Path]:
    raw = record.get("paths") or ([record["path"]] if record.get("path") else [])
    return [Path(p) for p in raw]


def _assert_integrity(record: dict, output_dir: Path) -> list[Path]:
    paths = [path if path.is_absolute() else output_dir / path for path in _png_paths(record)]
    assert paths, record
    hashes = record.get("sha256")
    if isinstance(hashes, str):
        hashes = [hashes]
    assert hashes and len(hashes) == len(paths)
    for path, expected in zip(paths, hashes):
        payload = path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected
        image = pymupdf.Pixmap(payload)
        assert image.width > 0 and image.height > 0
    return paths


def test_fixture_bundle_is_present_and_three_tables_match(tmp_path):
    assert FIGURES.is_file(), f"missing published fixture: {FIGURES}"
    ir = _ir(FIGURES)
    result = extract_table_images_by_id(FIGURES, ir, tmp_path)

    assert result["table_count"] == 3
    assert result["rendered_count"] == 3
    assert len(result["tables"]) == 3
    for record in result["tables"].values():
        assert record["status"] == "rendered", record
        _assert_integrity(record, tmp_path)


def test_nested_table_is_selected_individually_and_image_pixels_survive(tmp_path):
    ir = _ir(FIGURES)
    baseline = extract_table_images_by_id(FIGURES, ir, tmp_path / "all")
    nested_ids = [key for key in baseline["tables"] if "/c" in key]
    assert nested_ids, baseline["tables"].keys()
    chosen = nested_ids[0]

    selected = extract_table_images_by_id(
        FIGURES, ir, tmp_path / "nested", table_ids={chosen}
    )
    assert selected["table_count"] == 1
    assert list(selected["tables"]) == [chosen]
    record = selected["tables"][chosen]
    assert record["status"] == "rendered", record
    paths = _assert_integrity(record, tmp_path / "nested")

    # Fixture's teal/orange/blue assets must be rendered into visible pixels,
    # not replaced by empty image frames. Check every slice for evidence.
    color_evidence = 0
    for path in paths:
        image = pymupdf.Pixmap(path.read_bytes())
        if image.n < 3:
            image = pymupdf.Pixmap(pymupdf.csRGB, image)
        samples = memoryview(image.samples)
        for offset in range(0, len(samples), image.n):
            r, g, b = samples[offset:offset + 3]
            if (g > r * 1.25 and g > b * 1.1 and g > 45) or (r > b * 1.35 and g > b * 1.15):
                color_evidence += 1
    assert color_evidence > 100, f"no embedded-color pixels found; paths={paths}"


def test_tight_crop_contains_table_without_page_sized_whitespace(tmp_path):
    ir = _ir(FIGURES)
    manifest = extract_table_images_by_id(FIGURES, ir, tmp_path)
    for table_id, record in manifest["tables"].items():
        for path in _assert_integrity(record, tmp_path):
            image = pymupdf.Pixmap(path.read_bytes())
            # At 96 dpi a letter page is ~816x1056. These synthetic tables
            # are compact and should not be exported as full sheet renders.
            assert image.width < 816 or image.height < 1056, (table_id, image.width, image.height)
            assert image.width < 1100 and image.height < 1500, (table_id, image.width, image.height)


def test_ir_source_text_mismatch_fails_closed_without_png(tmp_path):
    ir = _ir(FIGURES)
    changed = deepcopy(ir)
    first = next(block for block in changed["body"] if block.get("kind") == "table")
    first["cells"][0]["blocks"][0]["text"] = "deliberately altered witness"
    manifest = extract_table_images_by_id(FIGURES, changed, tmp_path)

    failed = [record for record in manifest["tables"].values() if record["status"] != "rendered"]
    assert failed, manifest
    assert all(record.get("path") is None and not record.get("paths") for record in failed)
    assert all(record.get("sha256") is None for record in failed)
    assert all(not (tmp_path / path).exists() for record in failed for path in _png_paths(record))


def test_changed_hwpx_does_not_reuse_old_table_image(tmp_path):
    ir = _ir(FIGURES)
    first = extract_table_images_by_id(FIGURES, ir, tmp_path / "first")
    target = next(iter(first["tables"]))
    original_paths = _assert_integrity(first["tables"][target], tmp_path / "first")

    altered = tmp_path / "altered.hwpx"
    with zipfile.ZipFile(FIGURES) as source, zipfile.ZipFile(altered, "w", zipfile.ZIP_DEFLATED) as dest:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename == "Contents/section0.xml":
                # Change one visible table witness while preserving package XML.
                root = ET.fromstring(payload)
                text_node = root.find(".//hp:tbl//hp:t", NS)
                assert text_node is not None and text_node.text
                text_node.text = "source changed"
                payload = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
            dest.writestr(item, payload)

    second = extract_table_images_by_id(altered, ir, tmp_path / "second")
    assert second["source_sha256"] != first["source_sha256"]
    record = second["tables"].get(target)
    assert record is not None
    assert record["status"] != "rendered", record
    assert record.get("path") is None and not record.get("paths")
    assert all(path.exists() for path in original_paths)
    assert all(not ((tmp_path / "second") / path).exists() for path in _png_paths(record))


def test_all_thirty_five_tables_render_with_distinct_verified_images(tmp_path):
    assert THIRTY_FIVE.is_file(), f"missing published fixture: {THIRTY_FIVE}"
    manifest = extract_table_images_by_id(THIRTY_FIVE, _ir(THIRTY_FIVE), tmp_path)

    assert manifest["table_count"] == 35
    assert manifest["rendered_count"] == 35
    assert len(manifest["tables"]) == 35
    digests = set()
    for table_id, record in manifest["tables"].items():
        assert record["status"] == "rendered", (table_id, record)
        paths = _assert_integrity(record, tmp_path)
        assert len(paths) == 1, (table_id, paths)
        digests.add(hashlib.sha256(paths[0].read_bytes()).hexdigest())
    assert len(digests) == 35, "expected visually unique table output for all 35 fixtures"


def test_table_spanning_pages_emits_all_nonblank_slices(tmp_path):
    """Expand a fixture table until rhwp lays it out over multiple pages."""
    import rhwp

    source = tmp_path / "multipage.hwpx"
    with zipfile.ZipFile(FIGURES) as src, zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            payload = src.read(item.filename)
            if item.filename == "Contents/section0.xml":
                root = ET.fromstring(payload)
                table = root.find(".//hp:tbl", NS)
                rows = table.findall("./hp:tr", NS)
                original = [deepcopy(row) for row in rows]
                target_rows = 120
                for row_idx in range(len(rows), target_rows):
                    row = deepcopy(original[row_idx % len(original)])
                    for cell in row.findall("./hp:tc", NS):
                        addr = cell.find("./hp:cellAddr", NS)
                        addr.set("rowAddr", str(row_idx))
                    table.append(row)
                table.set("rowCnt", str(target_rows))
                payload = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
            dst.writestr(item, payload)

    parsed = rhwp.parse(str(source))
    ir = json.loads(parsed.to_ir_json())
    manifest = extract_table_images_by_id(source, ir, tmp_path / "rendered")
    first = next(iter(manifest["tables"].values()))
    assert first["status"] == "rendered", first
    paths = _assert_integrity(first, tmp_path / "rendered")
    assert len(paths) > 1, f"expected a multi-page table, got {len(paths)} image(s)"
