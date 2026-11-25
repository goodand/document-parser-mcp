import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def para(text):
    return {"kind": "paragraph", "text": text, "inlines": [], "prov": {}}


def cell(r, c, blocks, rs=1, cs=1):
    return {"row": r, "col": c, "row_span": rs, "col_span": cs, "blocks": blocks}


def synthetic_ir():
    """Merged cell holding a nested table, Korean numerals/units, one picture."""
    inner = {"kind": "table", "rows": 2, "cols": 2, "cells": [
        cell(0, 0, [para("내부 가")]), cell(0, 1, [para("일억 이천만 원")]),
        cell(1, 0, [para("내부 나")]), cell(1, 1, [para("3,500㎡")]),
    ]}
    outer = {"kind": "table", "rows": 3, "cols": 3, "cells": [
        cell(0, 0, [para("구분")]), cell(0, 1, [para("금액<script>alert(1)</script>")], cs=2),
        cell(1, 0, [para("병합 셀 안의 표"), inner], rs=2, cs=2), cell(1, 2, [para("이십오 개")]),
        cell(2, 2, [para("12.5%")]),
    ]}
    pic = {"kind": "picture", "image": {"uri": "bin://1", "mime_type": "image/png"}, "prov": {}}
    return {"schema_name": "HwpDocument", "body": [para("제목 & 개요"), outer, pic, para("끝")]}
