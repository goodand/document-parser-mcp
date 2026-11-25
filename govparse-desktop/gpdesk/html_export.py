"""Render the govparse source IR (rhwp HwpDocument) to self-contained HTML.

Paragraphs and nested tables are emitted recursively in body order. Cells keep
their row/col/row_span/col_span; covered grid slots are not emitted. All
document text is escaped. Pictures resolve through ``assets`` (bin uri -> relative
path); unresolved pictures leave a visible "그림 미포함" marker.
"""

from __future__ import annotations

from html import escape
from typing import Any

CSS = """
body{font-family:'KoPubWorld Dotum','Pretendard','Malgun Gothic',sans-serif;color:#1F2A37;
background:#F4F7F9;margin:0;line-height:1.5}
main{max-width:1100px;margin:24px auto;background:#fff;border:1px solid #E3E8EE;padding:24px 32px}
table{border-collapse:collapse;margin:6px 0;width:100%}
td{border:1px solid #9AA5B1;padding:4px 6px;vertical-align:top}
td p{margin:0}
.fig{margin:8px 0}.fig img{display:block;max-width:100%}
.figcap{font-size:13px;margin-top:4px}.fig-cap-src{font-weight:600}.fig-cap-user{color:#00646E}
.fig-meta{color:#63707E;font-weight:400}
.missing{color:#63707E;border:1px dashed #E3E8EE;padding:4px 8px;font-size:13px}
"""


def table_grid(table: dict[str, Any]) -> list[list[dict | None]]:
    rows, cols = int(table.get("rows") or 0), int(table.get("cols") or 0)
    grid: list[list[dict | None]] = [[None] * cols for _ in range(rows)]
    for c in table.get("cells") or []:
        r0, c0 = c["row"], c["col"]
        for r in range(r0, min(rows, r0 + max(1, c.get("row_span", 1)))):
            for k in range(c0, min(cols, c0 + max(1, c.get("col_span", 1)))):
                grid[r][k] = c
    return grid


def _text_of(node: dict) -> str:
    return node.get("text") or node.get("description") or ""


def _render_nodes(nodes: list, assets: dict[str, str], out: list[str], seen: set | None = None) -> None:
    seen = set() if seen is None else seen
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        kind = n.get("kind")
        if kind == "table":
            _render_table(n, assets, out, seen)
        elif kind == "picture":
            uri = (n.get("image") or {}).get("uri", "")
            path = assets.get(uri)
            from .figure_ir import caption_html

            if n.get("figure_link"):
                cap = caption_html(n["figure_link"], seen)
            elif n.get("caption_text"):
                cap = f'<div class="figcap fig-cap-src">{escape(n["caption_text"])} <span class="fig-meta">[원문 캡션]</span></div>'
            else:
                cap = ""
            if path:
                alt = escape(n.get("description") or uri)
                out.append(f'<div class="fig"><img src="{escape(path, quote=True)}" alt="{alt}">{cap}</div>')
            else:
                out.append(f'<div class="fig"><p class="missing">그림 미포함 ({escape(uri)})</p>{cap}</div>')
        elif kind == "caption":
            _render_nodes(n.get("blocks") or [], assets, out, seen)
        elif kind == "list_item":
            marker = escape(n.get("marker") or "")
            out.append(f"<p>{marker} {escape(_text_of(n))}</p>")
        elif kind == "field":
            if n.get("cached_value"):
                out.append(f"<p>{escape(str(n['cached_value']))}</p>")
        else:
            out.append(f"<p>{escape(_text_of(n))}</p>")


def _render_table(t: dict, assets: dict[str, str], out: list[str], seen: set) -> None:
    cells = sorted(t.get("cells") or [], key=lambda c: (c["row"], c["col"]))
    by_row: dict[int, list[dict]] = {}
    for c in cells:
        by_row.setdefault(c["row"], []).append(c)
    out.append("<table>")
    for r in range(int(t.get("rows") or 0)):
        out.append("<tr>")
        for c in by_row.get(r, []):
            attrs = ""
            if c.get("row_span", 1) > 1:
                attrs += f' rowspan="{int(c["row_span"])}"'
            if c.get("col_span", 1) > 1:
                attrs += f' colspan="{int(c["col_span"])}"'
            out.append(f"<td{attrs}>")
            _render_nodes(c.get("blocks") or [], assets, out, seen)
            out.append("</td>")
        out.append("</tr>")
    out.append("</table>")


def render_html(ir: dict, assets: dict[str, str], title: str) -> str:
    out = [
        "<!doctype html>",
        '<html lang="ko"><head><meta charset="utf-8">',
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body><main>",
    ]
    _render_nodes(ir.get("body") or [], assets, out)
    out.append("</main></body></html>")
    return "\n".join(out)
