from html.parser import HTMLParser

from conftest import synthetic_ir
from gpdesk.html_export import render_html, table_grid


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.depth, self.max_depth = [], 0, 0

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "table":
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1


def test_spans_nesting_and_escape():
    html = render_html(synthetic_ir(), {"bin://1": "assets/embedded/bin1.png"}, title="합성")
    p = Collect()
    p.feed(html)
    tds = [a for t, a in p.tags if t == "td"]
    assert any(a.get("colspan") == "2" and "rowspan" not in a for a in tds)
    assert any(a.get("rowspan") == "2" and a.get("colspan") == "2" for a in tds)
    assert p.max_depth == 2, "nested table must sit inside the merged cell"
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "일억 이천만 원" in html and "3,500㎡" in html
    imgs = [a for t, a in p.tags if t == "img"]
    assert imgs and imgs[0]["src"] == "assets/embedded/bin1.png"
    assert "http://" not in html and "https://" not in html


def test_reading_order_preserved():
    html = render_html(synthetic_ir(), {}, title="t")
    keys = ("제목", "구분", "병합 셀 안의 표", "내부 가", "이십오 개", "12.5%", "끝")
    order = [html.index(s) for s in keys]
    assert order == sorted(order)


def test_missing_picture_is_reported_not_dropped():
    assert "그림 미포함" in render_html(synthetic_ir(), {}, title="t")


def test_grid_positions():
    g = table_grid(synthetic_ir()["body"][1])
    assert g[1][0] is g[2][1] and g[1][0]["row_span"] == 2
    assert g[0][1] is g[0][2]
