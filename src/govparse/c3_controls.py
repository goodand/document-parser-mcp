"""Source-bound, bounded C3 consumer projection for a selected document.

Author: chatggpt_gpt6_extrahigh. This reads the generic core and original
source; it does not change either. Product adaptation: chatgpt_gpt6.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import argparse
from . import c3_native as probe
import json
from pathlib import Path

from lxml import etree

AUTHOR = "chatgpt_gpt6"
TARGETS = {"FieldFormula", "TCPSControl"}
SPACE_CODES = {"30", "31"}
BREAKS = {"9": "\t", "10": "\n", "13": "\n"}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pointer(*parts: object) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def relevant(segment: dict) -> bool:
    if segment["kind"] == "control":
        if segment["type"] in TARGETS:
            return True
        if segment["type"] == "ControlChar" and segment["attributes"].get("code") in SPACE_CODES:
            return True
        return any(relevant(child) for child in segment["children"])
    return False


def source_projection(node) -> str:
    if node.tag == "Text":
        return node.text or ""
    if node.tag in ("Paragraph", "TableControl"):
        return ""
    if node.tag == "ControlChar":
        return BREAKS.get(node.get("code"), "")
    return "".join(source_projection(child) for child in node)


def emitted_text_nodes(node):
    if node.tag in ("Paragraph", "TableControl"):
        return
    if node.tag == "Text":
        yield node
        return
    for child in node:
        yield from emitted_text_nodes(child)


def core_projection(segment: dict) -> str:
    if segment["kind"] == "text":
        return segment["text"]
    if segment["kind"] != "control":
        return ""
    prefix = BREAKS.get(segment["attributes"].get("code"), "") if segment["type"] == "ControlChar" else ""
    return prefix + "".join(core_projection(child) for child in segment["children"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    HERE = ap.parse_args().output.resolve()
    document_path = HERE / "document.json"
    xml_path = HERE / "raw/pyhwp.xml"
    before = sha(document_path)
    document = json.loads(document_path.read_text(encoding="utf-8"))
    artifacts = document["meta"]["source"]
    source_path = Path(artifacts["source_hwp"]["path"])
    probe_path = Path(artifacts["native_probe"]["path"])
    for name, path in (("source_hwp", source_path), ("pyhwp_xml", xml_path), ("native_probe", probe_path)):
        if sha(path) != artifacts[name]["sha256"]:
            raise ValueError(f"Source hash changed: {name}")
    selected = {key: p for key, p in document["paragraphs"].items()
                if any(relevant(s) for s in p["segments"])}
    native = probe.native(source_path)
    tree = etree.parse(str(xml_path), etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))
    xml_sections = {int(s.get("section-id")): s for s in tree.findall(".//BodyText/SectionDef")}
    xml_paragraphs = {}
    for section in {int(key.split(":", 1)[0][1:]) for key in selected}:
        xml_paragraphs[section] = xml_sections[section].findall(".//Paragraph")

    entries = []
    totals = Counter()
    for key, paragraph in selected.items():
        section = int(key.split(":", 1)[0][1:])
        index = int(key.split(":P", 1)[1])
        node = xml_paragraphs[section][index]
        native_p = native[f"Section{section}"]["paragraphs"][index]
        anchor = paragraph["anchor"]
        if anchor["paragraph_header_rec"] != native_p["header_rec"] or anchor["para_text_recs"] != native_p["text_records"]:
            raise ValueError(f"Native anchor changed: {key}")
        if paragraph["xml_xpath"] != tree.getpath(node):
            raise ValueError(f"XML anchor changed: {key}")
        children = [child for line in node.findall("./LineSeg") for child in line]
        xml_text_nodes = [n.text or "" for child in children for n in emitted_text_nodes(child)]
        core_text_nodes = []
        def collect_core(item):
            if item["kind"] == "text":
                core_text_nodes.append(item["text"])
            elif item["kind"] == "control":
                for child in item["children"]:
                    collect_core(child)
        for item in paragraph["segments"]:
            collect_core(item)
        if xml_text_nodes != core_text_nodes:
            raise ValueError(f"XML/core text nodes changed: {key}")
        xml_projection = "".join(source_projection(child) for child in children)
        core_full = "".join(core_projection(item) for item in paragraph["segments"])
        if xml_projection != core_full or core_full.removesuffix("\n") != paragraph["plain_text_projection"]:
            raise ValueError(f"XML/core projection changed: {key}")
        native_equal = xml_projection.encode("utf-16-le", "surrogatepass") == native_p["text"].encode("utf-16-le", "surrogatepass")
        if native_equal != (paragraph["text_verification"] == "native_xml_agree"):
            raise ValueError(f"Native verification changed: {key}")

        runs = []
        annotations = []
        consumed_text_paths = []
        def consume(n, path_parts):
            xpath = tree.getpath(n)
            segment_pointer = pointer("paragraphs", key, "segments", *path_parts)
            if n.tag == "Text":
                runs.append({"kind": "text", "text": n.text or "", "xml_xpath": xpath,
                             "source_segment_pointer": segment_pointer})
                consumed_text_paths.append(xpath)
                return
            if n.tag in ("Paragraph", "TableControl"):
                runs.append({"kind": "reference", "object_type": n.tag, "xml_xpath": xpath,
                             "source_segment_pointer": segment_pointer})
                return
            if n.tag == "ControlChar" and n.get("code") in BREAKS:
                runs.append({"kind": "layout_break", "code": int(n.get("code")), "text": BREAKS[n.get("code")],
                             "name": n.get("name"), "xml_xpath": xpath,
                             "source_segment_pointer": segment_pointer})
                return
            run_index = len(runs)
            runs.append({"kind": "object", "object_type": n.tag, "source_attributes": dict(n.attrib),
                         "xml_xpath": xpath, "source_segment_pointer": segment_pointer,
                         "semantic_status": "held", "unicode_replacement": None})
            if n.tag in TARGETS or (n.tag == "ControlChar" and n.get("code") in SPACE_CODES):
                annotation = {"paragraph_id": key, "control_type": n.tag, "run_index": run_index,
                              "status": "pass" if native_equal else "held",
                              "semantic_status": "held", "source_sha256": artifacts["source_hwp"]["sha256"],
                              "source_segment_pointer": segment_pointer, "xml_xpath": xpath,
                              "source_attributes": dict(n.attrib)}
                if n.tag == "FieldFormula":
                    annotation["displayed_text"] = "".join(t.text or "" for t in n.findall(".//Text"))
                    annotation["command"] = n.get("command")
                    annotation["policy"] = "Display text emitted once; command retained as metadata; calculation held."
                elif n.tag == "TCPSControl":
                    annotation["policy"] = "Raw attributes and descendant text preserved; visual meaning held."
                else:
                    annotation["policy"] = "Source control retained; Unicode character mapping held."
                annotations.append(annotation)
                totals[(n.tag, n.get("code"))] += 1
            for child_index, child in enumerate(n):
                consume(child, [*path_parts, "children", child_index])
        for seg_index, child in enumerate(children):
            consume(child, [seg_index])
        expected_text_paths = [tree.getpath(n) for child in children for n in emitted_text_nodes(child)]
        if consumed_text_paths != expected_text_paths or len(consumed_text_paths) != len(set(consumed_text_paths)):
            raise ValueError(f"Text node duplication or omission: {key}")
        consumer_projection = "".join(r.get("text", "") for r in runs)
        if consumer_projection != xml_projection:
            raise ValueError(f"Consumer projection changed: {key}")
        # A JSON serialization round trip is also checked without rewriting raw segments.
        if json.loads(json.dumps(runs, ensure_ascii=False)) != runs:
            raise ValueError(f"Consumer serialization failed: {key}")
        entries.append({"target_id": key, "target_pointer": pointer("paragraphs", key),
                        "status": "pass" if native_equal else "held",
                        "source_sha256": artifacts["source_hwp"]["sha256"],
                        "xml_sha256": artifacts["pyhwp_xml"]["sha256"],
                        "anchor": anchor, "xml_xpath": paragraph["xml_xpath"],
                        "runs": runs, "annotations": annotations,
                        "checks": {"native_xml_utf16_equal": native_equal, "core_xml_projection_equal": True,
                                   "text_nodes_emitted_once": True, "json_roundtrip_equal": True},
                        "policy": "Consume runs once; object tokens are unresolved objects, never source text. Raw segments unchanged."})
    if sha(document_path) != before:
        raise ValueError("Core document changed while building controls")
    annotations = [annotation for entry in entries for annotation in entry["annotations"]]
    result = {"author": AUTHOR, "kind": "bounded_c3_consumer_representation",
              "scope": "All FieldFormula, TCPSControl, FIXWIDTH_SPACE and NONBREAK_SPACE controls in the selected document",
              "source": {"hwp_path": str(source_path), "hwp_sha256": artifacts["source_hwp"]["sha256"],
                         "xml_path": str(xml_path), "xml_sha256": artifacts["pyhwp_xml"]["sha256"],
                         "document_path": str(document_path), "document_sha256": before,
                         "native_probe_sha256": artifacts["native_probe"]["sha256"]},
              "counts": {"paragraphs": len(entries), "formulas": totals[("FieldFormula", None)],
                         "tcps": totals[("TCPSControl", None)], "fixwidth_spaces": totals[("ControlChar", "31")],
                         "nonbreak_spaces": totals[("ControlChar", "30")],
                         "annotations": len(annotations),
                         "pass_paragraphs": sum(e["status"] == "pass" for e in entries),
                         "held_paragraphs": sum(e["status"] == "held" for e in entries)},
              "limits": ["The native decoder is reused from the core build, not independent.",
                         "Control 30/31 Unicode meanings and TCPS visual meanings remain held.",
                         "Formula commands are metadata and have not been recalculated.",
                         "No search-token policy is selected for unresolved objects."],
              "annotations": annotations,
              "entries": entries}
    output = HERE / "controls.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(output.read_text(encoding="utf-8")) != result:
        raise ValueError("Output JSON round trip failed")
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
