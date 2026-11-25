import json
import shutil
from pathlib import Path

import rhwp

from gpdesk.figure_ir import attach_links, caption_html
from gpdesk.figure_service import FigureService

FIX = Path(__file__).resolve().parents[1] / "samples" / "synthetic-figures.hwpx"


def figures(tmp_path, decide=None):
    src = tmp_path / "d.hwpx"
    shutil.copy2(FIX, src)
    svc = FigureService(tmp_path / "dec")
    r = svc.handle({"cmd": "open", "src": str(src), "out": str(tmp_path / "o")})
    if decide:
        r = svc.handle(decide(json.loads(Path(r["file"]).read_text("utf-8"))))
    return json.loads(Path(r["file"]).read_text("utf-8")), src


def pictures(ir):
    out = []

    def walk(n):
        if isinstance(n, list):
            for c in n:
                walk(c)
        elif isinstance(n, dict):
            if n.get("kind") == "picture":
                out.append(n)
            for k in ("cells", "blocks"):
                if k in n:
                    walk(n[k])

    walk(ir["body"])
    return out


def share(data):
    ids = [data["links"][1]["occurrence"]["occ_id"], data["links"][2]["occurrence"]["occ_id"]]
    ref = [p for p in data["links"][1]["pick_list"] if p["text"].startswith("<그림 B>")][0]["ref"]
    return {"cmd": "decide", "action": "share", "occ_ids": ids, "para_refs": [ref]}


def test_attach_one_to_one_and_shared_once(tmp_path):
    data, src = figures(tmp_path, share)
    ir = json.loads(rhwp.parse(str(src)).to_ir_json())
    report = attach_links(ir, data)
    assert report["matched"] == 6 and report["mismatch"] == []
    pics = pictures(ir)
    assert pics[0]["figure_link"]["status"] == "auto_explicit"
    assert pics[1]["figure_link"]["group"] == pics[2]["figure_link"]["group"]
    seen = set()
    html1 = caption_html(pics[1]["figure_link"], seen)
    html2 = caption_html(pics[2]["figure_link"], seen)
    assert "두 그림의 공통 설명" in html1 and "두 그림의 공통 설명" not in html2
    assert "직접 지정한 설명" in html1  # person-made link is marked apart from source text
    assert caption_html(pics[3]["figure_link"], set()) == ""  # unknown caption: no label in the export


def test_mismatch_is_reported_not_guessed(tmp_path):
    data, src = figures(tmp_path)
    ir = json.loads(rhwp.parse(str(src)).to_ir_json())
    data["links"] = data["links"][:-1]
    report = attach_links(ir, data)
    assert report["matched"] == 0 and report["mismatch"]
    assert all("figure_link" not in p for p in pictures(ir))
