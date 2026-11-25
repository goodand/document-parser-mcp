"""Large-section IR fallback used only for table-image matching."""

from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile

from govparse.large_hwpx import build_table_ir
from govparse.table_images import _all_ir_tables


HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def test_large_section_keeps_table_body_locator(tmp_path):
    source = tmp_path / "large.hwpx"
    table = f"""<hp:tbl id="t1" rowCnt="1" colCnt="1">
      <hp:tr><hp:tc><hp:cellAddr rowAddr="0" colAddr="0"/>
      <hp:cellSpan rowSpan="1" colSpan="1"/><hp:subList>
      <hp:p><hp:run><hp:t>사업계획</hp:t></hp:run></hp:p>
      </hp:subList></hp:tc></hp:tr></hp:tbl>"""
    section = (
        f'<hp:sec xmlns:hp="{HP}"><!--' + ("x" * (33 * 1024 * 1024)) + "-->"
        f"<hp:p><hp:run>{table}</hp:run></hp:p></hp:sec>"
    ).encode("utf-8")
    with ZipFile(source, "w", compression=ZIP_DEFLATED) as package:
        package.writestr("Contents/section0.xml", section)

    ir = build_table_ir(source)
    tables = _all_ir_tables(ir)

    assert len(tables) == 1
    _, stable_id, result = tables[0]
    assert stable_id == "s0/b1/t0"
    assert result["cells"][0]["blocks"][0]["text"] == "사업계획"
