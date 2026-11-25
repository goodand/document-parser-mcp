"""OWPML (HWPX XML) -> rhwp-shaped IR, used when rhwp cannot open a large HWPX."""

import json
from pathlib import Path

import pytest
import rhwp

from gpdesk.owpml_ir import build_ir

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def shape(nodes):
    """Comparable skeleton: kinds, table geometry, cell spans, picture asset numbers, text."""
    out = []
    for n in nodes:
        k = n.get("kind")
        if k == "table":
            out.append(("table", n["rows"], n["cols"], [
                (c["row"], c["col"], c["row_span"], c["col_span"], shape(c["blocks"])) for c in
                sorted(n["cells"], key=lambda c: (c["row"], c["col"]))]))
        elif k == "picture":
            out.append(("picture", n["image"]["uri"]))
        elif k == "paragraph" and (n.get("text") or "").strip():
            out.append(("p", n["text"].strip()))
    return out


@pytest.mark.parametrize("name", ["synthetic-figures.hwpx", "synthetic-merged.hwpx", "synthetic-35tables.hwpx"])
def test_matches_rhwp_skeleton(name):
    mine = build_ir(SAMPLES / name)
    ref = json.loads(rhwp.parse(str(SAMPLES / name)).to_ir_json())
    assert shape(mine["body"]) == shape(ref["body"])


def test_explicit_caption_kept_on_picture():
    ir = build_ir(SAMPLES / "synthetic-figures.hwpx")
    pics = []

    def walk(ns):
        for n in ns:
            if n.get("kind") == "picture":
                pics.append(n)
            for c in n.get("cells", []):
                walk(c["blocks"])

    walk(ir["body"])
    assert pics[0]["caption_text"] == "<그림 A> 사업 구성도"
    assert ir["source"]["builder"] == "gpdesk.owpml_ir"
