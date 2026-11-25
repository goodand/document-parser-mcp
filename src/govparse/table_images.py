"""Render individually verified HWP/HWPX tables as linked PNG assets."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import os
import tempfile
import unicodedata
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _ir_tables(table, roundtrip, stable):
    yield roundtrip, stable, table
    for cell in table.get("cells", []):
        children = [block for block in cell.get("blocks", []) if block.get("kind") == "table"]
        cell_path = f"{roundtrip}/cell[{cell['row']},{cell['col']}]"
        stable_cell = f"{stable}/c{cell['row']}_{cell['col']}"
        for index, child in enumerate(children):
            yield from _ir_tables(child, f"{cell_path}/table[{index}]",
                                  f"{stable_cell}/t{index}")


def _all_ir_tables(ir):
    output = []
    top_index = 0
    for body_index, block in enumerate(ir.get("body", [])):
        if block.get("kind") != "table":
            continue
        section = (block.get("prov") or {}).get("section_idx", 0)
        table = block
        stable = f"s{section}/b{body_index}/t{top_index}"
        output.extend(_ir_tables(table, f"table[{top_index}]", stable))
        top_index += 1
    return output


def iter_table_nodes(ir):
    """Yield ``(stable_table_id, IR table block)`` in document traversal order."""
    for _roundtrip_path, stable_id, table in _all_ir_tables(ir):
        yield stable_id, table


def table_index(ir):
    """Return the canonical stable ID to table block mapping for an IR."""
    return dict(iter_table_nodes(ir))


def _xml_tables(package, cached_parts=None):
    output = []
    section_shells = {}
    section_names = sorted(
        (name for name in package.namelist()
         if re.fullmatch(r"Contents/section\d+\.xml", name)),
        key=lambda name: int(re.search(r"section(\d+)", name).group(1)),
    )

    def visit_table(table, path, section_name):
        output.append((path, table, section_name))
        for cell in table.findall("p:tr/p:tc", {"p": HP[1:-1]}):
            nested = [node for para in cell.findall("p:subList/p:p", {"p": HP[1:-1]})
                      for run in para.findall("p:run", {"p": HP[1:-1]})
                      for node in run.findall("p:tbl", {"p": HP[1:-1]})]
            cell_addr = cell.find("p:cellAddr", {"p": HP[1:-1]})
            cell_key = f"{path}/cell[{cell_addr.get('rowAddr')},{cell_addr.get('colAddr')}]"
            for index, child in enumerate(nested):
                visit_table(child, f"{cell_key}/table[{index}]", section_name)

    for section_name in section_names:
        section_bytes = (cached_parts or {}).get(section_name)
        if isinstance(section_bytes, tuple):
            section_bytes = section_bytes[1]
        root = ET.fromstring(section_bytes if section_bytes is not None else package.read(section_name),
                             parser=ET.XMLParser(huge_tree=True, resolve_entities=False, no_network=True))
        shell = ET.Element(root.tag, attrib=dict(root.attrib), nsmap=root.nsmap)
        for child in root:
            if child.tag == HP + "p" and child.find(".//" + HP + "secPr") is not None:
                secpr = child.find(".//" + HP + "secPr")
                page_setup = ET.Element(HP + "p", id="2147482999",
                                        paraPrIDRef="0", styleIDRef="0")
                run = ET.SubElement(page_setup, HP + "run", charPrIDRef="0")
                run.append(deepcopy(secpr))
                shell.append(page_setup)
        section_shells[section_name] = ET.tostring(
            shell, encoding="UTF-8", xml_declaration=True
        )
        roots = [table for para in root.findall("p:p", {"p": HP[1:-1]})
                 for run in para.findall("p:run", {"p": HP[1:-1]})
                 for table in run.findall("p:tbl", {"p": HP[1:-1]})]
        top_index = sum(1 for path, _, _ in output if path.count("/table[") == 0)
        for index, table in enumerate(roots):
            visit_table(table, f"table[{top_index + index}]", section_name)
    return section_names, output, section_shells


def _native_records(xml_tables):
    from govparse.table_roundtrip import cell_blocks

    records = {}
    for path, table, _section_name in xml_tables:
        records[path] = (int(table.get("rowCnt")), int(table.get("colCnt")))
        for cell in table.findall("p:tr/p:tc", {"p": HP[1:-1]}):
            addr = cell.find("p:cellAddr", {"p": HP[1:-1]})
            span = cell.find("p:cellSpan", {"p": HP[1:-1]})
            key = f"{path}/cell[{addr.get('rowAddr')},{addr.get('colAddr')}]"
            records[key] = (int(span.get("rowSpan")), int(span.get("colSpan")), tuple(cell_blocks(cell)))
    return records


def _binary_id_refs(root):
    return {
        value for node in root.iter() for name, value in node.attrib.items()
        if name.rsplit("}", 1)[-1] == "binaryItemIDRef" and value
    }


def _cached_base_archive(parts, section_names, shared_binary_refs):
    """Compress shared package members once; table renders append only deltas."""
    manifest = ET.fromstring(parts["Contents/content.hpf"][1]).find("{*}manifest")
    binary_items = {
        item.get("href"): item.get("id") for item in manifest.findall("{*}item")
        if item.get("href", "").startswith("BinData/")
    }
    base = io.BytesIO()
    with ZipFile(base, "w", compression=8, compresslevel=1) as archive:
        for filename, (info, data) in parts.items():
            if filename == "Contents/content.hpf" or filename in section_names:
                continue
            if filename.startswith("BinData/") and binary_items.get(filename) not in shared_binary_refs:
                continue
            archive.writestr(info, data)
    return base.getvalue(), binary_items


def _isolate_table(base_archive, parts, section_names, section_shells, content_template,
                   binary_items, target_section, target_table, shared_binary_refs):
    out = io.BytesIO()
    hp = {"hp": HP[1:-1]}
    target_index = int(re.search(r"section(\d+)", target_section).group(1))
    binary_refs = set(shared_binary_refs) | _binary_id_refs(target_table)
    missing_refs = binary_refs - set(binary_items.values())
    if missing_refs:
        raise ValueError(f"unresolved_binary_item_refs:{','.join(sorted(missing_refs))}")
    out.write(base_archive)
    with ZipFile(out, "a", compression=8, compresslevel=1) as dest:
        package = deepcopy(content_template)
        manifest = package.find("{*}manifest")
        for item in list(manifest):
            href = item.get("href", "")
            match = re.fullmatch(r"Contents/section(\d+)\.xml", href)
            if match and int(match.group(1)) != target_index:
                manifest.remove(item)
            elif match:
                item.set("id", "section0")
                item.set("href", "Contents/section0.xml")
            elif href.startswith("BinData/") and item.get("id") not in binary_refs:
                manifest.remove(item)
        spine = package.find("{*}spine")
        for itemref in list(spine):
            ref = itemref.get("idref", "")
            if re.fullmatch(r"section\d+", ref) and ref != f"section{target_index}":
                spine.remove(itemref)
            elif ref == f"section{target_index}":
                itemref.set("idref", "section0")
        content_info = parts["Contents/content.hpf"][0]
        dest.writestr(content_info, ET.tostring(package, encoding="UTF-8", xml_declaration=True))
        section_info = parts[target_section][0]
        root = ET.fromstring(section_shells[target_section])
        paragraph = ET.Element(HP + "p", id="2147483000", paraPrIDRef="0", styleIDRef="0")
        run = ET.SubElement(paragraph, HP + "run", charPrIDRef="0")
        run.append(deepcopy(target_table))
        root.append(paragraph)
        dest.writestr("Contents/section0.xml", ET.tostring(root, encoding="UTF-8", xml_declaration=True))
        for filename, (info, data) in parts.items():
            if filename.startswith("BinData/") and binary_items.get(filename) in binary_refs - shared_binary_refs:
                dest.writestr(info, data)
    return out.getvalue()


def _shared_binary_refs(package, section_names, cached_parts=None):
    refs = set()
    for name in package.namelist():
        if name in section_names or not name.lower().endswith((".xml", ".hpf")):
            continue
        try:
            data = (cached_parts or {}).get(name)
            if isinstance(data, tuple):
                data = data[1]
            root = ET.fromstring(
                data if data is not None else package.read(name),
                parser=ET.XMLParser(huge_tree=True, resolve_entities=False, no_network=True),
            )
        except (ET.XMLSyntaxError, KeyError):
            continue
        refs.update(_binary_id_refs(root))
    return refs


def _record_subtrees(records):
    """Index each cell/table signature under every containing table once."""
    output = {}
    table_token = re.compile(r"table\[\d+\]")
    cell_token = re.compile(r"/cell\[\d+,\d+\]$")
    for key, value in records.items():
        normalized = key.strip("/")
        if cell_token.search(normalized):
            row_span, col_span, blocks = value
            signature = (row_span, col_span, tuple(
                (kind, text if kind == "paragraph" else "")
                for kind, text in blocks
                if kind == "table" or (kind == "paragraph" and text.strip())
            ))
        elif table_token.search(normalized):
            signature = value
        else:
            continue
        for match in table_token.finditer(normalized):
            prefix = normalized[:match.end()]
            output.setdefault(prefix, {})[normalized] = signature
    return output


def _crop_png(png: bytes, margin: int = 12, page_svg=None, repair_symbols: bool = False) -> tuple[bytes, bool]:
    """Crop to non-white content with a safety margin; fail if that is uncertain."""
    from PIL import Image, ImageChops

    svg_text = None
    if page_svg is not None and repair_symbols:
        try:
            svg_text = page_svg()
        except Exception:
            # SVG is optional; preserve the established raster crop on failure.
            svg_text = None
    if svg_text is not None and repair_symbols:
        # Some portable renderers omit symbol glyphs when the requested font is
        # unavailable. Repair only verified blank symbol-only cells; the helper
        # returns the original bytes for already-rendered or unsupported cases.
        from govparse.table_glyphs import repair_missing_symbol_glyphs

        png = repair_missing_symbol_glyphs(png, svg_text)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    bbox = ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
    if not bbox:
        raise ValueError("rendered_page_has_no_crop_content")
    needs_body_clip = bbox[3] > image.height * 0.85
    if page_svg is not None and svg_text is None and needs_body_clip:
        try:
            svg_text = page_svg()
        except Exception:
            svg_text = None
    # A folio outside the body can make a small table look a full page tall.
    # Restrict the raster only when the renderer supplies its body clipping rectangle.
    if svg_text is not None and needs_body_clip:
        try:
            svg = ET.fromstring(svg_text.encode("utf8"))
            for group in svg.iterfind(".//{*}g[@clip-path]"):
                ref = re.fullmatch(r"url\(#(body-clip-[0-9]+)\)", group.get("clip-path", ""))
                if ref:
                    rect = svg.find(f".//{{*}}clipPath[@id='{ref[1]}']/{{*}}rect")
                    if rect is not None:
                        sx = image.width / float(svg.get("width"))
                        sy = image.height / float(svg.get("height"))
                        x, y, w, h = (float(rect.get(key)) for key in ("x", "y", "width", "height"))
                        region = image.crop((int(x*sx), int(y*sy), math.ceil((x+w)*sx), math.ceil((y+h)*sy)))
                        body_bbox = ImageChops.difference(region, Image.new("RGB", region.size, "white")).getbbox()
                        if body_bbox:
                            image, bbox = region, body_bbox
                    break
        except (ET.XMLSyntaxError, OSError, ValueError, TypeError):
            pass
    left, top, right, bottom = bbox
    left, top = max(0, left - margin), max(0, top - margin)
    right, bottom = min(image.width, right + margin), min(image.height, bottom + margin)
    cropped = image.crop((left, top, right, bottom))
    output = io.BytesIO()
    cropped.save(output, format="PNG", optimize=False)
    return output.getvalue(), True


def _table_has_single_symbol_text(table) -> bool:
    """Cheap native-XML gate so ordinary table renders never request SVG."""
    for cell in table.iterfind(".//{*}tc"):
        text_nodes = [node for node in cell.iter() if ET.QName(node).localname == "t" and (node.text or "").strip()]
        if len(text_nodes) == 1:
            text = (text_nodes[0].text or "").strip()
            if len(text) == 1 and text.isprintable() and unicodedata.category(text).startswith("S"):
                return True
    return False


def extract_table_images_by_id(source, ir, out_dir, table_ids=None):
    """Render one verified table per isolated HWPX, retaining all page slices."""
    import pymupdf
    import rhwp

    source = Path(source)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    requested = None if table_ids is None else set(table_ids)
    if source.suffix.lower() == ".hwpx":
        pass
    elif source.suffix.lower() == ".hwp":
        try:
            source_document = rhwp.Document.from_bytes(source_bytes, source_uri=str(source))
            source_bytes = source_document.to_hwpx_bytes()
        except Exception as exc:
            results = {
                stable_id: {
                    "status": "unmatched", "method": "failed", "path": None,
                    "paths": [], "sha256": None, "page_hint": None,
                    "reason": f"source_conversion_failed:{type(exc).__name__}:{exc}",
                }
                for _roundtrip, stable_id, _table in _all_ir_tables(ir)
                if requested is None or stable_id in requested
            }
            return _write_manifest(source, digest, out_dir, results)
    else:
        raise ValueError("Expected HWP or HWPX")

    results = {}
    from govparse.table_roundtrip import ir_records

    with ZipFile(io.BytesIO(source_bytes)) as package:
        # Decompress every package member once. The large header and image assets
        # are reused by every isolated table package below.
        parts = {
            info.filename: (info, package.read(info.filename))
            for info in package.infolist()
        }
        section_names, xml_tables, section_shells = _xml_tables(package, parts)
        shared_binary_refs = _shared_binary_refs(package, section_names, parts)
        header_info, header_data = parts["Contents/header.xml"]
        normalized_header = ET.fromstring(header_data)
        normalized_header.set("secCnt", "1")
        parts["Contents/header.xml"] = (
            header_info,
            ET.tostring(normalized_header, encoding="UTF-8", xml_declaration=True),
        )
        content_template = ET.fromstring(parts["Contents/content.hpf"][1])
        base_archive, binary_items = _cached_base_archive(parts, section_names, shared_binary_refs)
        ir_tables = _all_ir_tables(ir)
        xml_by_path = {path: table for path, table, _section in xml_tables}
        section_by_path = {path: section for path, _table, section in xml_tables}
        parsed_records = _record_subtrees(dict(ir_records(ir.get("body", []))))
        native_records = _record_subtrees(_native_records(xml_tables))
        for roundtrip_path, stable_id, table in ir_tables:
            if requested is not None and stable_id not in requested:
                continue
            record = {"status": "unmatched", "method": "failed", "path": None,
                      "paths": [], "sha256": None, "page_hint": None, "reason": None}
            results[stable_id] = record
            xml_table = xml_by_path.get(roundtrip_path)
            if xml_table is None:
                record["reason"] = "native_table_path_not_found"
                continue
            prefix = roundtrip_path
            parsed_subtree = parsed_records.get(prefix)
            native_subtree = native_records.get(prefix)
            if not parsed_subtree or parsed_subtree != native_subtree:
                record["reason"] = "table_structure_or_text_not_exact"
                continue
            element = xml_table
            owning_section = section_by_path[roundtrip_path]
            try:
                isolated = _isolate_table(
                    base_archive, parts, section_names, section_shells, content_template,
                    binary_items, owning_section, element, shared_binary_refs
                )
            except Exception as exc:
                record["reason"] = f"isolation_failed:{type(exc).__name__}:{exc}"
                continue
            table_tag = hashlib.sha256(f"{digest}:{stable_id}".encode()).hexdigest()[:12]
            try:
                document = rhwp.Document.from_bytes(isolated, source_uri=f"mem://{digest}/{stable_id}")
                pages = document.render_all_png()
            except Exception as exc:  # renderer failures are evidence, not fatal to other tables
                record["reason"] = f"rhwp_render_failed:{type(exc).__name__}:{exc}"
                continue
            prepared_pages = []
            has_symbol_cell = _table_has_single_symbol_text(element)
            render_svg = getattr(document, "render_svg", None)
            for index, png in enumerate(pages, start=1):
                if pymupdf.Pixmap(png).is_unicolor:
                    continue
                try:
                    png, cropped = _crop_png(
                        png,
                        page_svg=(lambda: render_svg(index - 1)) if callable(render_svg) else None,
                        repair_symbols=has_symbol_cell,
                    )
                except Exception as exc:
                    record["reason"] = f"crop_failed:{type(exc).__name__}:{exc}"
                    prepared_pages = []
                    break
                name = f"table-{table_tag}-p{index:03}.png"
                prepared_pages.append((name, png, hashlib.sha256(png).hexdigest(), cropped))
            if record["reason"]:
                continue
            for name, png, _sha, _cropped in prepared_pages:
                path = out_dir / name
                fd, temp_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=out_dir)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(png)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_name, path)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
                record["paths"].append(name)
            if not record["paths"]:
                record["reason"] = "rendered_pages_are_blank"
                continue
            if len(record["paths"]) == 1:
                record["path"] = record["paths"][0]
            record.update(status="rendered", method="isolated_native_table", sha256=[
                row[2] for row in prepared_pages
            ], page_count=len(record["paths"]), crop_applied=[row[3] for row in prepared_pages], reason=None)
    if requested is not None:
        for missing_id in sorted(requested - results.keys()):
            results[missing_id] = {
                "status": "unmatched", "method": "failed", "path": None,
                "paths": [], "sha256": None, "page_hint": None,
                "reason": "table_id_not_present_in_ir",
            }
    return _write_manifest(source, digest, out_dir, results)


def _write_manifest(source, digest, out_dir, results):
    manifest = {
        "source_path": str(source),
        "source_sha256": digest,
        "method_contract": "isolated_native_table",
        "table_count": len(results),
        "rendered_count": sum(row["status"] == "rendered" for row in results.values()),
        "tables": results,
    }
    fd, tmp_name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, out_dir / "manifest.json")
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return manifest

