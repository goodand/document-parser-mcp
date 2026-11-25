"""Build public synthetic samples (no business content).

synthetic-merged.html -> (Hancom) synthetic-merged.hwpx : merged cell holding a
nested table, Korean numerals/units, one picture.
synthetic-35img.html -> synthetic-35img.hwpx : 35 small tables for 31+ asset test.
"""

from __future__ import annotations

import base64
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def merged_html() -> str:
    (HERE / "teal.png").write_bytes(png(120, 60, (0, 100, 110)))
    return """<html><head><meta charset="utf-8"></head><body>
<p>합성 검증 문서 — 병합 셀 안의 표</p>
<table border="1">
<tr><td>구분</td><td colspan="2">사업비 합계</td></tr>
<tr><td rowspan="2" colspan="2"><p>병합 셀 안의 표</p>
  <table border="1"><tr><td>내부 가</td><td>일억 이천만 원</td></tr>
  <tr><td>내부 나</td><td>3,500㎡</td></tr></table></td><td>이십오 개</td></tr>
<tr><td>12.5%</td></tr>
</table>
<p>그림 한 장</p>
<p><img src="teal.png" width="120" height="60"></p>
<p>끝</p></body></html>"""


def many_html(n: int = 35) -> str:
    parts = ["<html><head><meta charset='utf-8'></head><body><p>표 35개 합성 문서</p>"]
    for i in range(n):
        parts.append(f"<p>표 {i + 1}</p><table border='1'><tr><td>항목 {i + 1}</td>"
                     f"<td>{(i + 1) * 100:,}원</td></tr><tr><td>단위</td><td>{i + 1}개</td></tr></table>")
    parts.append("</body></html>")
    return "".join(parts)


if __name__ == "__main__":
    (HERE / "synthetic-merged.html").write_text(merged_html(), "utf-8")
    (HERE / "synthetic-35tables.html").write_text(many_html(), "utf-8")
    print("ok", file=sys.stderr)
    _ = base64
