"""Join figures.json (XML occurrences + decisions) onto the rhwp IR used by exports.

Both lists are in document order. The join is accepted only when the counts match
and every pair agrees on the asset number (IR ``bin://N`` <-> HWPX ``imageN``).
Otherwise nothing is attached and the mismatch is reported.
"""

from __future__ import annotations

import re
from html import escape


def _ir_pictures(ir: dict) -> list[dict]:
    out: list[dict] = []

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

    walk(ir.get("body"))
    return out


def _num(s: str) -> str | None:
    m = re.search(r"(\d+)$", s or "")
    return str(int(m.group(1))) if m else None


def attach_links(ir: dict, figures: dict) -> dict:
    pics = _ir_pictures(ir)
    links = figures.get("links", [])
    mismatch = []
    if len(pics) != len(links):
        mismatch.append(f"그림 수 불일치: IR {len(pics)} / 원문 XML {len(links)}")
    else:
        for i, (p, l) in enumerate(zip(pics, links)):
            a, b = _num((p.get("image") or {}).get("uri", "")), _num(l["occurrence"]["asset_ref"])
            if a != b:
                mismatch.append(f"{i + 1}번째 그림 자산 불일치: IR {a} / XML {b}")
    if mismatch:
        return {"matched": 0, "mismatch": mismatch}
    for p, l in zip(pics, links):
        p["figure_link"] = {
            "occ_id": l["occurrence"]["occ_id"], "status": l["status"], "group": l["group"],
            "caption_text": l["caption_text"], "caption_refs": l["caption_refs"],
            "evidence": l["evidence"], "path_text": l.get("path_text", ""),
            "parent_item": l["occurrence"]["parent_item"],
            "asset_ref": l["occurrence"]["asset_ref"], "size": l["occurrence"].get("size_hwpunit"),
        }
    return {"matched": len(pics), "mismatch": []}


_LABEL = {
    "auto_explicit": "원문 캡션",
    "user_linked": "직접 지정한 설명",
    "user_shared": "직접 지정한 설명 · 공통",
    "user_no_caption": "설명 없는 그림",
    "user_undecided": "결정 불가",
    "needs_review": "검토 필요",
}


def caption_html(link: dict | None, seen_groups: set) -> str:
    if not link:
        return ""
    st = link["status"]
    label = escape(_LABEL.get(st, st))
    if st == "user_shared" and link.get("group"):
        if link["group"] in seen_groups:
            return f'<div class="figcap fig-meta">{label} (위 공통 설명 참조)</div>'
        seen_groups.add(link["group"])
    if link.get("caption_text"):
        cls = "fig-cap-src" if st == "auto_explicit" else "fig-cap-user"
        return (f'<div class="figcap {cls}">{escape(link["caption_text"])}'
                f' <span class="fig-meta">[{label}]</span></div>')
    if st in ("needs_review", "user_undecided"):
        return ""  # nothing known about a caption: say nothing in the exported document
    return f'<div class="figcap fig-meta">[{label}]</div>'
