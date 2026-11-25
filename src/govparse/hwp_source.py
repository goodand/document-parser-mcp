"""Author: chatggpt_gpt6_extrahigh. Preserve rhwp's source structure.

The IR is a source artifact. In particular, ``prov.para_idx`` is rhwp's own
index and must not be joined numerically to a low-level HWP paragraph index.
The IR does not establish a PDF page or bounding box.
"""

from __future__ import annotations

from io import BytesIO

import base64
import re
from zipfile import ZipFile
from lxml import etree as ET

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .hwpx_drawings import cell_drawings
from .normalize import sha256_of

DOCUMENT = "document.json"
MANIFEST = "_source_manifest.json"
SOURCE_MANIFEST_VERSION = 25


def _rhwp_parse(path: Path) -> Any:
    """Lazy import keeps PDF-only installations usable."""
    try:
        import rhwp
    except ImportError as exc:
        raise RuntimeError(
            "HWP 원본 구조 추출에 rhwp-python이 필요합니다. "
            "프로젝트 환경에 rhwp-python을 설치하세요."
        ) from exc
    return rhwp.parse(str(path))


def _write_atomic(path: Path, payload: bytes) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _counts(ir: dict[str, Any]) -> dict[str, int]:
    body = ir.get("body")
    if not isinstance(body, list):
        raise ValueError("rhwp IR에 body 배열이 없습니다")

    counts = {"paragraphs": 0, "all_paragraphs": 0, "tables": 0, "cells": 0,
              "list_items": 0, "pictures": 0}

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind == "paragraph":
            counts["all_paragraphs"] += 1
        elif kind == "table":
            counts["tables"] += 1
            cells = node.get("cells")
            if isinstance(cells, list):
                counts["cells"] += len(cells)
        elif kind == "picture":
            counts["pictures"] += 1
        elif kind == "list_item":
            counts["list_items"] += 1
        for key in ("blocks", "cells", "body"):
            if key in node:
                visit(node[key])

    counts["paragraphs"] = sum(
        isinstance(node, dict) and node.get("kind") == "paragraph" for node in body
    )
    visit(body)
    return counts


def _cached_manifest(src: Path, out_dir: Path, source_hash: str) -> dict[str, Any] | None:
    manifest_path = out_dir / MANIFEST
    document_path = out_dir / DOCUMENT
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("version") != SOURCE_MANIFEST_VERSION
            or (src.suffix.lower() == ".hwpx" and (
                manifest.get("drawings_path") != "cell_drawings.json"
                or sha256_of(out_dir / "cell_drawings.json") != manifest.get("drawings_sha256")
            ))
            or manifest.get("source_path") != str(src)
            or manifest.get("source_sha256") != source_hash
            or manifest.get("document_path") != DOCUMENT
            or not document_path.is_file()
            or sha256_of(document_path) != manifest.get("document_sha256")
            or document_path.stat().st_size != manifest.get("document_bytes")
        ):
            return None
        return manifest
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _attach_pictures(ir, native):
    """Match raster picture blocks using the rhwp binary ID and native image reference."""
    def pictures(blocks):
        for block in blocks:
            if block.get("kind") == "picture":
                yield block
            for cell in block.get("cells", []):
                yield from pictures(cell["blocks"])

    def match_cell_pictures(blocks, tables):
        ir_tables = [block for block in blocks if block["kind"] == "table"]
        if len(ir_tables) != len(tables):
            return
        for table, element in zip(ir_tables, tables):
            has_cell_picture = element.find("./{*}tr/{*}tc/{*}subList/{*}p/{*}run/{*}pic") is not None
            cells = {(c["row"], c["col"]): c for c in table["cells"]}
            for tc in element.findall("./{*}tr/{*}tc"):
                address = tc.find("{*}cellAddr")
                cell = cells.get((int(address.get("rowAddr")), int(address.get("colAddr"))))
                if cell is None:
                    continue
                children = cell["blocks"]
                nodes = [b for b in children if b["kind"] == "picture"]
                pics = tc.findall("./{*}subList/{*}p/{*}run/{*}pic")
                if len(nodes) == len(pics):
                    for node, pic in zip(nodes, pics):
                        image = pic.find(".//{*}img")
                        uri = node.get("image", {}).get("uri", "")
                        if image is not None and uri.startswith("bin://") and image.get("binaryItemIDRef") == "image" + uri[6:]:
                            matched[id(node)] = pic
                # Recover a trailing inline image only when the complete prefix aligns
                # with the native cell and the image placement is independently matched.
                paragraphs = tc.findall("./{*}subList/{*}p")
                if nodes and paragraphs and len(children) >= 2 and children[-1]["kind"] == "picture":
                    last = paragraphs[-1].findall("./{*}run/*")
                    content = [el for el in last if not (el.tag.endswith("}t") and not len(el) and not el.text)]
                    text_nodes = [el for el in content[:-1] if el.tag.endswith("}t")]
                    if (content and content[-1] is matched.get(id(children[-1]))
                            and all(el.tag.endswith("}t") for el in content[:-1])
                            and children[-2]["kind"] == "paragraph"):
                        from .table_roundtrip import cell_blocks, text_content
                        prefix = [("table", "") if b["kind"] == "table" else (b["kind"], b.get("text", ""))
                                  for b in children[:-1]]
                        if (prefix == list(cell_blocks(tc)) and children[-2].get("text")
                                == "".join(text_content(el) for el in text_nodes)):
                            children[-1]["inline_after_paragraph"] = True
                if has_cell_picture and len(nodes) == len(pics) and all(id(node) in matched for node in nodes):
                    source_blocks = []
                    for para in tc.findall("./{*}subList/{*}p"):
                        elements = para.findall("./{*}run/*")
                        if len(elements) == 1 and elements[0].tag.endswith("}pic"):
                            source_blocks.append(("picture", ""))
                        elif all(el.tag.endswith("}t") for el in elements):
                            from .table_roundtrip import text_content
                            source_blocks.append(("paragraph", "".join(text_content(el) for el in elements)))
                        else:
                            break
                    else:
                        retained = []
                        for child in children:
                            signature = child["kind"], child.get("text", "")
                            if len(retained) < len(source_blocks) and signature == source_blocks[len(retained)]:
                                retained.append(child)
                            elif not pics or signature != ("paragraph", ""):
                                break
                        else:
                            if len(retained) == len(source_blocks):
                                children[:] = retained
                                for index, block in enumerate(children):
                                    # This path has just proved that every picture occupies
                                    # its own native paragraph; never retain an inline marker.
                                    if block["kind"] == "picture":
                                        block.pop("inline_after_paragraph", None)
                                for block, para in zip(retained, tc.findall("./{*}subList/{*}p")):
                                    lines = para.findall("./{*}linesegarray/{*}lineseg")
                                    if lines:
                                        block["picture_cell_lines"] = [dict(line.attrib) for line in lines]
                match_cell_pictures(children, tc.findall("./{*}subList/{*}p/{*}run/{*}tbl"))

    matched = {}
    assets = {}
    with ZipFile(native) as package:
        manifest = ET.fromstring(package.read("Contents/content.hpf"))
        items = {item.get("id"): item for item in manifest.findall(".//{*}manifest/{*}item")}
        by_ref = {}
        for name in package.namelist():
            if re.fullmatch(r"Contents/section\d+\.xml", name):
                root = ET.fromstring(package.read(name))
                section_index = int(re.search(r"section(\d+)", name)[1])
                section_blocks = [b for b in ir["body"] if (b.get("prov") or {}).get("section_idx") == section_index]
                match_cell_pictures(section_blocks, root.findall("./{*}p/{*}run/{*}tbl"))
                for pic in root.iterfind(".//{*}pic"):
                    img = pic.find(".//{*}img")
                    if img is not None:
                        by_ref.setdefault(img.get("binaryItemIDRef"), []).append(pic)
        picture_blocks = list(pictures(ir["body"]))
        blocks_by_ref = {}
        for block in picture_blocks:
            match = re.fullmatch(r"bin://(\d+)", block.get("image", {}).get("uri", ""))
            if not match:
                continue
            ref = "image" + match[1]
            blocks_by_ref.setdefault(ref, []).append(block)

        assigned = {}
        for ref, blocks in blocks_by_ref.items():
            candidates = by_ref.get(ref, [])
            reserved = {
                id(picture)
                for block in blocks
                if (picture := matched.get(id(block))) is not None
            }
            unmatched = [block for block in blocks if id(block) not in matched]
            available = [picture for picture in candidates if id(picture) not in reserved]
            if len(unmatched) == len(available):
                assigned.update(
                    (id(block), picture) for block, picture in zip(unmatched, available)
                )

        for block in picture_blocks:
            match = re.fullmatch(r"bin://(\d+)", block.get("image", {}).get("uri", ""))
            if not match:
                continue
            ref = "image" + match[1]
            item = items.get(ref)
            picture = matched.get(id(block))
            if picture is None:
                picture = assigned.get(id(block))
            if picture is None or item is None:
                continue
            href = item.get("href", "")
            if not re.fullmatch(r"BinData/[\w.-]+\.(?:bmp|png|jpeg|jpg|gif|wmf)", href, re.I):
                continue
            if ref not in assets:
                encoded = base64.b64encode(package.read(href)).decode("ascii")
                assets[ref] = {"item": dict(item.attrib), "bytes": encoded}
            block["native_picture"] = ET.tostring(picture, encoding="unicode", with_tail=False)
    if assets:
        ir["picture_assets"] = assets


def extract_source(src: Path, out_dir: Path) -> dict[str, Any]:
    """Extract a HWP/HWPX IR with verified nested-table paragraph boundaries, with a source-bound cache manifest.

    The result describes original structure only. It intentionally contains no
    inferred PDF page or location. The PDF parser's active manifest remains
    independent of this sidecar.
    """
    src = Path(src).resolve()
    out_dir = Path(out_dir).resolve()
    if not src.is_file():
        raise FileNotFoundError(src)
    if src.suffix.lower() not in {".hwp", ".hwpx"}:
        raise ValueError(f"HWP/HWPX 파일이 아닙니다: {src}")
    source_hash = sha256_of(src)
    cached = _cached_manifest(src, out_dir, source_hash)
    if cached is not None:
        return cached

    document = _rhwp_parse(src)
    ir_text = document.to_ir_json(indent=2)
    if not isinstance(ir_text, str):
        raise TypeError("rhwp to_ir_json 결과가 문자열이 아닙니다")
    ir = json.loads(ir_text)
    if not isinstance(ir, dict):
        raise ValueError("rhwp IR 루트가 객체가 아닙니다")
    counts = _counts(ir)
    removed = 0
    native = src if src.suffix.lower() == ".hwpx" else None
    native_conversion_attempted = False
    if native is None and (counts["tables"] or counts["pictures"]):
        native_conversion_attempted = True
        try:
            native = BytesIO(document.to_hwpx_bytes())
        except Exception:
            native = None
    if (native is not None
            and counts["tables"] > sum(node.get("kind") == "table" for node in ir["body"])):
        from .table_roundtrip import read_hwpx, remove_table_placeholders

        if native_conversion_attempted:
            try:
                removed = remove_table_placeholders(ir["body"], read_hwpx(native))
            except Exception:
                removed = 0
        else:
            removed = remove_table_placeholders(ir["body"], read_hwpx(native))
        if removed:
            ir_text = json.dumps(ir, ensure_ascii=False, indent=2)
            counts = _counts(ir)
    if counts["pictures"] and native is not None:
        if native_conversion_attempted:
            try:
                _attach_pictures(ir, native)
            except Exception:
                pass
        else:
            _attach_pictures(ir, native)
        counts = _counts(ir)
        if ir.get("picture_assets"):
            ir_text = json.dumps(ir, ensure_ascii=False, indent=2)
    if native is not None and counts["tables"]:
        from .table_roundtrip import attach_cell_borders

        if native_conversion_attempted:
            try:
                attach_cell_borders(ir, native)
            except Exception:
                pass
        else:
            attach_cell_borders(ir, native)
        if ir.get("character_heights") or ir.get("section_page_geometry") or ir.get("page_geometry") or ir.get("cell_border_styles"):
            ir_text = json.dumps(ir, ensure_ascii=False, indent=2)
    if sha256_of(src) != source_hash:
        raise ValueError("HWP 원본이 추출 중 변경되었습니다")

    payload = ir_text.encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(out_dir / DOCUMENT, payload)
    if sha256_of(src) != source_hash:
        raise ValueError("HWP 원본이 저장 중 변경되었습니다")
    manifest = {
        "version": SOURCE_MANIFEST_VERSION,
        "author": "chatggpt_gpt6_extrahigh",
        "source_path": str(src),
        "source_sha256": source_hash,
        "document_path": DOCUMENT,
        "document_sha256": hashlib.sha256(payload).hexdigest(),
        "document_bytes": len(payload),
        "schema_name": ir.get("schema_name"),
        "schema_version": ir.get("schema_version"),
        "counts": counts,
        "removed_table_placeholders": removed,
        "provenance_contract": {
            "para_idx": "rhwp index; not native HWP paragraph index",
            "pdf_page": "not inferred",
        },
    }
    if src.suffix.lower() == ".hwpx":
        drawings = cell_drawings(src)
        if sha256_of(src) != source_hash:
            raise ValueError("HWPX changed during drawing extraction")
        drawing_payload = json.dumps(drawings, ensure_ascii=False).encode("utf-8")
        _write_atomic(out_dir / "cell_drawings.json", drawing_payload)
        manifest.update(drawings_path="cell_drawings.json", drawings_count=len(drawings),
                        drawings_sha256=hashlib.sha256(drawing_payload).hexdigest())
    _write_atomic(
        out_dir / MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return manifest
