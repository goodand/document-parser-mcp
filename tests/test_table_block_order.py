from lxml import etree as ET

from govparse.table_roundtrip import HP, cell_blocks


def test_cell_comparison_preserves_paragraph_breaks_and_table_position():
    def blocks(content):
        cell = ET.fromstring(f'<tc xmlns="{HP["p"]}"><subList>{content}</subList></tc>')
        return list(cell_blocks(cell))

    first = "<p><run><t>A</t></run></p>"
    second = "<p><run><t>B</t></run></p>"
    table = "<p><run><tbl/></run></p>"
    assert blocks(first + second) != blocks("<p><run><t>AB</t></run></p>")
    assert blocks(first + table + second) != blocks(first + second + table)
    assert blocks(first + table + second) == [
        ("paragraph", "A"), ("table", ""), ("paragraph", "B")
    ]

def test_native_confirmation_preserves_original_empty_paragraph():
    from govparse.table_roundtrip import ir_records, remove_table_placeholders

    blank = {"kind": "paragraph", "text": ""}
    inner = {"kind": "table", "rows": 1, "cols": 1, "cells": []}
    cell = {"row": 0, "col": 0, "row_span": 1, "col_span": 1,
            "blocks": [blank.copy(), inner]}
    body = [{"kind": "table", "rows": 1, "cols": 1, "cells": [cell]}]
    native = dict(ir_records(body))
    assert remove_table_placeholders(body, native) == 0
    cell["blocks"].insert(1, blank.copy())
    assert remove_table_placeholders(body, native) == 1
    assert dict(ir_records(body)) == native
    assert remove_table_placeholders(body, native) == 0

def test_multiple_table_placeholders_require_a_complete_native_match():
    from govparse.table_roundtrip import ir_records, remove_table_placeholders

    blank = {"kind": "paragraph", "text": ""}
    nested = {"kind": "table", "rows": 1, "cols": 1, "cells": []}
    cell = {"row": 0, "col": 0, "row_span": 1, "col_span": 1,
            "blocks": [blank.copy(), nested.copy(), nested.copy()]}
    body = [{"kind": "table", "rows": 1, "cols": 1, "cells": [cell]}]
    native = dict(ir_records(body))
    blocks = cell["blocks"]
    blocks.insert(1, blank.copy())
    blocks.insert(3, blank.copy())
    blocks.append({"kind": "paragraph", "text": "unmatched"})
    before = list(blocks)
    assert remove_table_placeholders(body, native) == 0
    assert blocks == before
    blocks.pop()
    assert remove_table_placeholders(body, native) == 2
    assert dict(ir_records(body)) == native
