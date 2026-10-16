"""Source preserving C3 core, adapted from mss_c3_v1/build.py.

Original author: chatggpt_gpt6_extrahigh. Product adaptation: chatgpt_gpt6.
Run via c3_pipeline; every invocation requires a new output directory.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter
import hashlib
from . import c3_native as native_probe
import json
from pathlib import Path
import subprocess
import sys

from lxml import etree
import pymupdf

AUTHOR = "chatgpt_gpt6"
NATIVE_PROBE = Path(native_probe.__file__)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def walk_tables(value: object, path: str = ""):
    if isinstance(value, dict):
        if value.get("kind") == "table":
            yield path, value
        for key, child in value.items():
            yield from walk_tables(child, path + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_tables(child, path + "/" + str(index))


def segment(node, section: int, paragraph_ids: dict[str, str], table_ids: dict[str, str], counts: Counter, formulas: list):
    if node.tag == "Text":
        return {"kind": "text", "text": node.text or "", "attributes": dict(node.attrib)}
    if node.tag == "Paragraph":
        return {"kind": "paragraph_ref", "paragraph_id": paragraph_ids.get(node.get("paragraph-id"),
                f"S{section}:unknown-paragraph-{node.get('paragraph-id')}")}
    if node.tag == "TableControl":
        return {"kind": "table_ref", "table_id": table_ids.get(node.get("table-id"),
                f"S{section}:unknown-table-{node.get('table-id')}")}
    counts[node.tag + ":" + node.get("name", node.get("chid", ""))] += 1
    result = {"kind": "control", "type": node.tag, "attributes": dict(node.attrib),
              "children": [segment(c, section, paragraph_ids, table_ids, counts, formulas) for c in node]}
    if node.tag == "FieldFormula":
        formulas.append({"section": section, "command": node.get("command"),
                         "displayed_text": "".join(t.text or "" for t in node.findall(".//Text")),
                         "policy": "preserve displayed value and command separately; do not recalculate"})
    return result


def projection(segments: list) -> str:
    out = []
    for item in segments:
        if item["kind"] == "text":
            out.append(item["text"])
        elif item["kind"] == "control":
            if item["type"] == "ControlChar":
                out.append({"9": "\t", "10": "\n", "13": "\n"}.get(item["attributes"].get("code"), ""))
            out.append(projection(item["children"]))
    return "".join(out)


def cell_paragraphs(native: dict, cell: dict) -> list:
    start = cell["list_header_rec"]
    level = native["records"][start]["level"]
    records = native["records"]
    end = len(records)
    for record in records[start + 1:]:
        if record["level"] < level or (record["tag"] == 72 and record["level"] == level):
            end = record["rec"]
            break
    headers = [p["header_rec"] for p in native["paragraphs"]]
    return [p for p in native["paragraphs"][bisect.bisect_right(headers, start):bisect.bisect_left(headers, end)]
            if p["level"] == level]


def eligible_rhwp_tables(native_tables: list, rhwp_tables: list) -> list:
    """A missing table invalidates ordinal matching for the entire section."""
    return rhwp_tables if len(native_tables) == len(rhwp_tables) else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--pyhwp", required=True)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    source, pdf_path = args.source.resolve(), args.pdf.resolve()
    HERE = args.output.resolve()
    HERE.mkdir(parents=True, exist_ok=False)
    PYHWP = args.pyhwp
    raw = HERE / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    xml_file, rhwp_file = raw / "pyhwp.xml", raw / "rhwp_ir.json"
    raw_manifest = raw / "manifest.json"
    source_hash = sha(source)
    if not args.refresh and xml_file.exists() and rhwp_file.exists() and not raw_manifest.exists():
        raise ValueError("raw files lack source-bound manifest; rerun with --refresh")
    if not args.refresh and xml_file.exists() and rhwp_file.exists() and raw_manifest.exists():
        prior = json.loads(raw_manifest.read_text(encoding="utf-8"))
        if prior["source_sha256"] != source_hash or prior["pyhwp_xml_sha256"] != sha(xml_file) or prior["rhwp_ir_sha256"] != sha(rhwp_file):
            raise ValueError("raw cache hash/source mismatch; rerun with --refresh")
    commands = []
    if args.refresh or not xml_file.exists():
        command = [str(PYHWP), "xml", "--output", str(xml_file), str(source)]
        with (HERE / "pyhwp.stderr.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        commands.append({"argv": command, "exit_code": completed.returncode})
        if completed.returncode:
            raise RuntimeError("pyhwp XML extraction failed; see pyhwp.stderr.log")
    if args.refresh or not rhwp_file.exists():
        import rhwp
        command = [sys.executable, "-c", "rhwp.parse(source).to_ir_json()", str(source)]
        rhwp_file.write_text(rhwp.parse(str(source)).to_ir_json(), encoding="utf-8")
        commands.append({"argv": command, "exit_code": 0})
    if not raw_manifest.exists() or args.refresh or commands:
        save(raw_manifest, {"author": AUTHOR, "source_sha256": source_hash,
                            "pyhwp_xml_sha256": sha(xml_file), "rhwp_ir_sha256": sha(rhwp_file),
                            "initial_extraction": "source-bound raw extraction; commands listed in REPORT.md when cache was first adopted"})
    native_sections = native_probe.native(source)
    tree = etree.parse(str(xml_file), etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))
    xml_sections = {int(x.get("section-id")): x for x in tree.findall(".//BodyText/SectionDef")}
    xml_paragraph_global_ids = [int(p.get("paragraph-id")) for s in xml_sections.values()
                                for p in s.findall(".//Paragraph")]
    xml_table_global_ids = [int(t.get("table-id")) for s in xml_sections.values()
                            for t in s.findall(".//TableControl")]
    xml_id_sequence = {"paragraph_ids_unique_contiguous_from_zero": xml_paragraph_global_ids == list(range(len(xml_paragraph_global_ids))),
                       "table_ids_unique_contiguous_from_zero": xml_table_global_ids == list(range(len(xml_table_global_ids))),
                       "paragraph_ids": len(xml_paragraph_global_ids), "table_ids": len(xml_table_global_ids)}
    if not all((xml_id_sequence["paragraph_ids_unique_contiguous_from_zero"],
                xml_id_sequence["table_ids_unique_contiguous_from_zero"])):
        raise ValueError("pyhwp global ID sequence changed; ordinal mapping requires review")
    ir = json.loads(rhwp_file.read_text(encoding="utf-8"))
    rhwp_tables = {}
    for pointer, table in walk_tables(ir.get("body", []), "/body"):
        section = table.get("prov", {}).get("section_idx")
        rhwp_tables.setdefault(section, []).append((pointer, table))
    paragraphs, tables, mismatches, holds, formulas = {}, {}, [], [], []
    counts = Counter()
    for name, native in native_sections.items():
        section = int(name.removeprefix("Section"))
        xsection = xml_sections.get(section)
        if xsection is None:
            raise ValueError(f"Missing XML section {section}; ordinal mapping held")
        xp = xsection.findall(".//Paragraph")
        xt = xsection.findall(".//TableControl")
        if len(xp) != len(native["paragraphs"]) or len(xt) != len(native["tables"]):
            raise ValueError("Native/XML counts differ; ordinal mapping held")
        xparas = {i: p for i, p in enumerate(xp)}
        paragraph_ids = {p.get("paragraph-id"): f"S{section}:P{i}" for i, p in enumerate(xp)}
        table_ids = {t.get("table-id"): f"S{section}:T{n['table_rec']}" for t, n in zip(xt, native["tables"])}
        if len(xp) != len(native["paragraphs"]):
            mismatches.append({"section": section, "kind": "paragraph_count", "native": len(native["paragraphs"]), "xml": len(xp)})
        for p in native["paragraphs"]:
            idx = p["para_index"]
            key = f"S{section}:P{idx}"
            x = xparas.get(idx)
            if x is None:
                mismatches.append({"id": key, "kind": "missing_xml_paragraph"})
                continue
            segments = [segment(n, section, paragraph_ids, table_ids, counts, formulas) for line in x.findall("./LineSeg") for n in line]
            text = projection(segments)
            equal = text.encode("utf-16-le", "surrogatepass") == p["text"].encode("utf-16-le", "surrogatepass")
            if not equal:
                mismatches.append({"id": key, "kind": "native_xml_text", "native": p["text"], "xml": text})
            paragraphs[key] = {"id": key, "anchor": {"stream": f"BodyText/{name}", "para_index": idx,
                "paragraph_header_rec": p["header_rec"], "para_text_recs": p["text_records"]},
                "segments": segments, "plain_text_projection": text[:-1] if text.endswith("\n") else text,
                "native_controls": p["controls"], "xml_xpath": tree.getpath(x),
                "text_verification": "native_xml_agree" if equal else "needs_review",
                "alignment_status": "ordinal_candidate; duplicate/empty paragraph identity not independently verified",
                "control_semantics": "held_for_source_review"}
        rt = rhwp_tables.get(section, [])
        if len(xt) != len(native["tables"]) or len(rt) != len(native["tables"]):
            mismatches.append({"section": section, "kind": "table_count", "native": len(native["tables"]), "xml": len(xt), "rhwp": len(rt)})
        rt = eligible_rhwp_tables(native["tables"], rt)
        for ordinal, t in enumerate(native["tables"]):
            tid = f"S{section}:T{t['table_rec']}"
            x = xt[ordinal] if ordinal < len(xt) else None
            entry = rt[ordinal] if ordinal < len(rt) else None
            xb = x.find("./TableBody") if x is not None else None
            xc = {(int(c.get("row")), int(c.get("col"))): c for c in xb.findall("./TableRow/TableCell")} if xb is not None else {}
            pointer, r = entry if entry else (None, None)
            rc = {(c["row"], c["col"]): c for c in r["cells"]} if r else {}
            shape = [t["rows"], t["cols"], len(t["cells"])]
            xml_shape = [int(xb.get("rows")), int(xb.get("cols")), len(xc)] if xb is not None else None
            rhwp_shape = [r["rows"], r["cols"], len(rc)] if r else None
            ok = shape == xml_shape == rhwp_shape
            if not ok:
                mismatches.append({"id": tid, "kind": "table_shape", "native": shape, "xml": xml_shape, "rhwp": rhwp_shape})
            cells = []
            for c in t["cells"]:
                pos = (c["row"], c["col"])
                ps = cell_paragraphs(native, c)
                pids = [f"S{section}:P{p['para_index']}" for p in ps]
                xmlc, rhwpc = xc.get(pos), rc.get(pos)
                xml_ids = [paragraph_ids.get(p.get("paragraph-id"), "unknown") for p in xmlc.xpath("./Paragraph | ./ColumnSet/Paragraph")] if xmlc is not None else None
                spans = [c["rowspan"], c["colspan"]]
                xml_spans = [int(xmlc.get("rowspan")), int(xmlc.get("colspan"))] if xmlc is not None else None
                rhwp_spans = [rhwpc["row_span"], rhwpc["col_span"]] if rhwpc else None
                structure_ok = pids == xml_ids and spans == xml_spans == rhwp_spans
                if not structure_ok:
                    mismatches.append({"id": tid, "cell": list(pos), "kind": "cell_structure", "native_paragraph_ids": pids,
                                       "xml_paragraph_ids": xml_ids, "native_spans": spans, "xml_spans": xml_spans, "rhwp_spans": rhwp_spans})
                expected = [paragraphs[pid]["plain_text_projection"] for pid in pids if pid in paragraphs]
                actual = [b["text"] for b in rhwpc["blocks"] if b["kind"] in ("paragraph", "list_item")] if rhwpc else None
                status = "projection_equal" if expected == actual else "representation_or_content_review"
                counts["rhwp_cell_projection_equal"] += status == "projection_equal"
                if status != "projection_equal":
                    holds.append({"table_id": tid, "cell": list(pos), "paragraph_ids": pids,
                                  "native_xml_projection": expected, "rhwp_text": actual,
                                  "reason": "Do not overwrite control-aware output using plain text alone."})
                cells.append({"row": c["row"], "col": c["col"], "rowspan": c["rowspan"], "colspan": c["colspan"],
                              "list_header_rec": c["list_header_rec"], "paragraph_ids": pids,
                              "nested_table_ids": [f"S{section}:T{n}" for n in c.get("nested_tables", [])],
                              "structure_verified": structure_ok, "rhwp_comparison": status})
            tables[tid] = {"id": tid, "anchor": {"stream": f"BodyText/{name}", "table_rec": t["table_rec"]},
                           "rows": t["rows"], "cols": t["cols"], "cells": cells,
                           "xml_xpath": tree.getpath(x) if x is not None else None, "rhwp_pointer": pointer if ok else None,
                           "alignment_status": "ordinal_candidate; structural agreement does not establish identity",
                           "structure_verified": ok and all(c["structure_verified"] for c in cells),
                           "page_mapping_status": "not_assigned", "context_status": "held_for_source_review"}
    docinfo = tree.find("./DocInfo")
    if docinfo is not None:
        (HERE / "source_docinfo.xml").write_bytes(etree.tostring(docinfo, encoding="utf-8", xml_declaration=True))
    with pymupdf.open(pdf_path) as pdf:
        page_count = len(pdf)
    artifacts = {key: {"path": str(path), "sha256": sha(path)} for key, path in
                 [("source_hwp", source), ("source_pdf", pdf_path), ("pyhwp_xml", xml_file),
                  ("rhwp_ir", rhwp_file), ("native_probe", NATIVE_PROBE)]}
    document = {"meta": {"author": AUTHOR, "kind": "generic_c3_core", "source": artifacts,
                "counts": {"sections": len(native_sections), "paragraphs": len(paragraphs), "tables": len(tables),
                           "cells": sum(len(t["cells"]) for t in tables.values()), "pdf_pages": page_count},
                "phase_status": {"C1_structure": "measured_with_mismatches_retained",
                                 "C2_pdf_local_context": "page_evidence_index_only; links_unassigned",
                                 "C3_control_annotations": "held_for_source_review"},
                "product_parser_modified": True, "scorer_used": False},
                "body_paragraph_ids": [f"S{int(name.removeprefix('Section'))}:P{p['para_index']}"
                                       for name, section in native_sections.items() for p in section["paragraphs"] if p["level"] == 0],
                "paragraphs": paragraphs, "tables": tables,
                "pdf_evidence": {"path": str(pdf_path), "sha256": sha(pdf_path), "file_page_count": page_count,
                                 "page_reference_rule": "one-based PDF file page; no inferred HWP paragraph-to-page mapping"},
                "formula_policy": "Keep displayed field text and formula command separately; never repair totals by arithmetic.",
                "formulas": formulas, "rhwp_comparison_holds": holds,
                "source_style_definitions": {"path": "source_docinfo.xml", "status": "preserved; semantics held"}}
    save(HERE / "document.json", document)
    verification = {"author": AUTHOR, "source_sha256": artifacts["source_hwp"]["sha256"],
                    "artifact_hashes": artifacts, "commands_this_run": commands,
                    "raw_extraction_record": json.loads(raw_manifest.read_text(encoding="utf-8")),
                    "pyhwp_global_id_sequence": xml_id_sequence,
                    "native_xml_mismatch_count": sum(x["kind"] in ("native_xml_text", "paragraph_count", "missing_xml_paragraph") for x in mismatches),
                    "table_structure_verified": sum(t["structure_verified"] for t in tables.values()),
                    "rhwp_cell_projection_equal": counts["rhwp_cell_projection_equal"],
                    "rhwp_cell_comparison_holds": len(holds), "formula_fields": len(formulas),
                    "control_counts": dict(counts), "all_visual_semantics_verified": False,
                    "document_sha256": sha(HERE / "document.json")}
    save(HERE / "verification.json", verification)
    save(HERE / "mismatches.json", mismatches)
    files = [HERE / "build.py", HERE / "REPORT.md", HERE / "document.json", HERE / "verification.json", HERE / "mismatches.json",
             HERE / "source_docinfo.xml", xml_file, rhwp_file, raw_manifest]
    (HERE / "OUTPUTS.sha256").write_text("# Author: " + AUTHOR + "\n" +
        "\n".join(f"{sha(p)}  {p.relative_to(HERE).as_posix()}" for p in files if p.exists()) + "\n", encoding="utf-8")
    print(json.dumps({**document["meta"]["counts"], "mismatches": len(mismatches),
                      "verified_tables": verification["table_structure_verified"],
                      "rhwp_holds": len(holds)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
