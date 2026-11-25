"""Engine drift check: does the engine *working tree* restore_body still fit our picture restore?

Skipped unless GPDESK_ENGINE_SRC points at document-parser-mcp/src. A failure means:
resync gpdesk/vendor deliberately (see vendor/PROVENANCE.txt), do not patch the engine from here.
"""
import importlib.util
import os
from pathlib import Path

import pytest

from gpdesk import hwpx_restore
from gpdesk.figures import extract
from gpdesk.owpml_ir import build_ir

FIX = Path(__file__).resolve().parents[1] / "samples" / "synthetic-figures.hwpx"


@pytest.mark.skipif(not os.environ.get("GPDESK_ENGINE_SRC"), reason="engine working tree not given")
def test_working_tree_restore_body_keeps_picture_count(tmp_path, monkeypatch):
    path = Path(os.environ["GPDESK_ENGINE_SRC"]) / "govparse" / "hwp_restore.py"
    spec = importlib.util.spec_from_file_location("engine_hwp_restore", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(hwpx_restore, "restore_body", mod.restore_body)
    out = tmp_path / "r.hwpx"
    hwpx_restore.restore_with_figures(build_ir(FIX), extract(FIX).asset_bytes, out)
    v = hwpx_restore.verify_restored(out, expected_pictures=6)
    assert v["ok"], v


def _placements(path):
    """(asset ref, table depth, has caption) for every picture placement, in document order."""
    import zipfile

    from lxml import etree

    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    hc = "{http://www.hancom.co.kr/hwpml/2011/core}"
    rows = []
    with zipfile.ZipFile(path) as z:
        for n in sorted(x for x in z.namelist() if x.startswith("Contents/section")):
            for p in etree.fromstring(z.read(n)).iter(hp + "pic"):
                rows.append((p.find(f".//{hc}img").get("binaryItemIDRef"),
                             sum(1 for a in p.iterancestors() if a.tag == hp + "tbl"),
                             p.find(hp + "caption") is not None))
    return rows


@pytest.mark.skipif(not os.environ.get("GPDESK_ENGINE_SRC"), reason="engine working tree not given")
def test_engine_alone_keeps_every_picture_placement(tmp_path):
    """Handoff condition agreed with the engine session (2026-11-25): without the app's post-processing,
    every placement survives at its table depth with its source caption. Counts placements, not BinData.
    Verified against the published engine release that maps all six picture placements,
    including a shared image placed in both nested cells and the document body.
    Keep the placement-level assertion so future engine updates cannot silently regress it."""
    import sys

    src_root = os.environ["GPDESK_ENGINE_SRC"]
    sys.path.insert(0, src_root)
    try:
        from govparse.hwp_restore import restore_body
        from govparse.hwp_source import extract_source
    finally:
        sys.path.remove(src_root)
    sidecar = tmp_path / "sidecar"
    extract_source(FIX, sidecar)
    out = tmp_path / "engine-only.hwpx"
    restore_body(sidecar / "document.json", out)
    assert _placements(out) == _placements(FIX)
    assert hwpx_restore.verify_restored(out, expected_pictures=6)["missing_bindata"] == []
