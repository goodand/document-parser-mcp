"""Build an rhwp-shaped IR straight from HWPX (OWPML) XML.

Used only when the preview renderer (rhwp) refuses a document, e.g. a section XML
larger than its fixed 32 MB entry limit. Shape follows rhwp's IR: every hp:p becomes
a paragraph node with its own text, followed by the tables/pictures anchored in it.
Pictures carry ``bin://N`` from the HWPX binary item id ``imageN`` and, when the
document declares one, ``caption_text`` from hp:caption.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from .figures import HP, HC, _PARSER, _own_text


def _caption_text(pic) -> str | None:
    cap = pic.find(HP + "caption")
    if cap is None:
        return None
    t = " ".join(_own_text(p) for p in cap.findall(f"{HP}subList/{HP}p")).strip()
    return t or None


def _blocks_of_container(container) -> list[dict]:
    out: list[dict] = []
    for p in container.findall(HP + "p"):
        out.append({"kind": "paragraph", "text": _own_text(p), "inlines": [], "prov": {}})
        _anchored(p, out)
    return out


def _anchored(el, out: list[dict]) -> None:
    for ch in el:
        if ch.tag == HP + "tbl":
            out.append(_table(ch))
        elif ch.tag == HP + "pic":
            img = ch.find(f".//{HC}img")
            ref = img.get("binaryItemIDRef") if img is not None else ""
            m = re.search(r"(\d+)$", ref or "")
            node = {"kind": "picture", "image": {"uri": f"bin://{int(m.group(1))}" if m else "",
                                                  "mime_type": None},
                    "caption": None, "description": None, "prov": {}, "asset_ref": ref}
            cap = _caption_text(ch)
            if cap:
                node["caption_text"] = cap
            out.append(node)
        elif ch.tag in (HP + "caption", HP + "subList"):
            continue
        else:
            _anchored(ch, out)


def _table(tbl) -> dict:
    cells = []
    for tr in tbl.findall(HP + "tr"):
        for tc in tr.findall(HP + "tc"):
            a, s = tc.find(HP + "cellAddr"), tc.find(HP + "cellSpan")
            sub = tc.find(HP + "subList")
            cells.append({"row": int(a.get("rowAddr")), "col": int(a.get("colAddr")),
                          "row_span": int(s.get("rowSpan")) if s is not None else 1,
                          "col_span": int(s.get("colSpan")) if s is not None else 1,
                          "blocks": _blocks_of_container(sub) if sub is not None else []})
    text = " ".join(b.get("text", "") for c in cells for b in c["blocks"] if b.get("kind") == "paragraph")
    return {"kind": "table", "rows": int(tbl.get("rowCnt") or 0), "cols": int(tbl.get("colCnt") or 0),
            "cells": cells, "text": text, "caption": None, "prov": {"table_id": tbl.get("id")}}


def build_ir(source: Path) -> dict:
    body: list[dict] = []
    with zipfile.ZipFile(source) as z:
        secs = sorted((n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                      key=lambda n: int(re.findall(r"\d+", n)[-1]))
        for name in secs:
            root = etree.fromstring(z.read(name), _PARSER)
            body.extend(_blocks_of_container(root))
            del root
    return {"schema_name": "HwpDocument", "schema_version": "1.1-owpml",
            "source": {"uri": str(source), "builder": "gpdesk.owpml_ir"}, "body": body}
