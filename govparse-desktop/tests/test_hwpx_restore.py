import json
import zipfile
from pathlib import Path

from lxml import etree

from gpdesk.figure_ir import attach_links
from gpdesk.figure_service import FigureService
from gpdesk.figures import extract
from gpdesk.hwpx_restore import restore_with_figures, verify_restored
from gpdesk.owpml_ir import build_ir

FIX = Path(__file__).resolve().parents[1] / "samples" / "synthetic-figures.hwpx"
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def make(tmp_path, decide=None):
    svc = FigureService(tmp_path / "dec")
    r = svc.handle({"cmd": "open", "src": str(FIX), "out": str(tmp_path / "f")})
    if decide:
        r = svc.handle(decide(json.loads(Path(r["file"]).read_text("utf-8"))))
    figs = json.loads(Path(r["file"]).read_text("utf-8"))
    ir = build_ir(FIX)
    assert attach_links(ir, figs)["matched"] == 6
    doc = extract(FIX)
    out = tmp_path / "r.hwpx"
    report = restore_with_figures(ir, doc.asset_bytes, out)
    return out, report


def test_pictures_and_explicit_caption_restored(tmp_path):
    out, report = make(tmp_path)
    with zipfile.ZipFile(out) as z:
        sec = etree.fromstring(z.read("Contents/section0.xml"))
        hpf = z.read("Contents/content.hpf").decode()
        names = z.namelist()
    pics = list(sec.iter(HP + "pic"))
    assert len(pics) == 6 and report["pictures"] == 6
    refs = [p.find(".//{http://www.hancom.co.kr/hwpml/2011/core}img").get("binaryItemIDRef") for p in pics]
    for r in set(refs):
        assert f'id="{r}"' in hpf and any(n.startswith(f"BinData/{r}.") for n in names)
    caps = [p for p in pics if p.find(HP + "caption") is not None]
    assert len(caps) == 1 and "<그림 A> 사업 구성도" in "".join(caps[0].itertext())
    # the picture sits inside the nested table inside the merged cell
    anc = [a for a in caps[0].iterancestors() if a.tag == HP + "tc"]
    spans = [(int(a.find(HP + "cellSpan").get("rowSpan")), int(a.find(HP + "cellSpan").get("colSpan"))) for a in anc]
    assert spans == [(1, 1), (2, 2)]


def test_user_links_not_written_as_captions(tmp_path):
    def share(d):
        ids = [d["links"][1]["occurrence"]["occ_id"], d["links"][2]["occurrence"]["occ_id"]]
        ref = [p for p in d["links"][1]["pick_list"] if p["text"].startswith("<그림 B>")][0]["ref"]
        return {"cmd": "decide", "action": "share", "occ_ids": ids, "para_refs": [ref]}

    out, report = make(tmp_path, share)
    with zipfile.ZipFile(out) as z:
        sec = z.read("Contents/section0.xml").decode()
    assert sec.count("<그림 B> 두 그림의 공통 설명".replace("<", "&lt;").replace(">", "&gt;")) == 1
    links = json.loads(out.with_suffix(".links.json").read_text("utf-8"))
    assert [l["status"] for l in links["figures"]].count("user_shared") == 2
    assert links["note"].startswith("사용자가 정한 연결")


def test_verify_restored_reports_structure(tmp_path):
    out, _ = make(tmp_path)
    v = verify_restored(out, expected_pictures=6)
    assert v["pictures"] == 6 and v["missing_bindata"] == [] and v["nested_tables"] >= 1
    assert v["ok"] is True
