from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET

from govparse import hwp_restore, hwp_source


HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"


def test_independent_arc_curve_and_connector_survive_table_roundtrip(tmp_path):
    source = tmp_path / "vector-kinds.hwpx"
    template = Path(hwp_restore.__file__).with_name("blank_table.hwpx")
    with ZipFile(template) as original, ZipFile(source, "w") as output:
        for item in original.infolist():
            data = original.read(item)
            if item.filename == "Contents/section0.xml":
                section = ET.fromstring(data)
                run = section.find(".//{*}tc/{*}subList/{*}p/{*}run")
                arc = ET.SubElement(run, HP + "arc", id="600", type="NORMAL")
                for name, x, y in (("center", "50", "50"), ("ax1", "100", "50"), ("ax2", "50", "0")):
                    ET.SubElement(arc, HC + name, x=x, y=y)
                curve = ET.SubElement(run, HP + "curve", id="601")
                ET.SubElement(curve, HP + "seg", type="CURVE", x1="0", y1="0", x2="50", y2="90")
                group = ET.SubElement(run, HP + "container", id="602")
                for parent, shape_id, subject in ((run, "603", "805"), (run, "604", "0"), (group, "605", "806")):
                    line = ET.SubElement(parent, HP + "connectLine", id=shape_id, type="STRAIGHT_NOARROW")
                    ET.SubElement(line, HP + "startPt", x="2", y="3", subjectIDRef=subject, subjectIdx="0")
                    ET.SubElement(line, HP + "endPt", x="12", y="17", subjectIDRef="0", subjectIdx="0")
                data = ET.tostring(section, encoding="UTF-8", xml_declaration=True)
            output.writestr(item, data)

    manifest = hwp_source.extract_source(source, tmp_path)
    restored = tmp_path / "restored.hwpx"
    hwp_restore.restore_body(tmp_path / manifest["document_path"], restored)

    with ZipFile(restored) as package:
        section = ET.fromstring(package.read("Contents/section0.xml"))
    shapes = [child for child in section.findall(".//{*}tc/{*}subList/{*}p/{*}run/*")
              if child.tag != HP + "t"]
    assert [shape.tag for shape in shapes] == [HP + "arc", HP + "curve", HP + "connectLine"]
    assert [(p.tag, p.get("x"), p.get("y")) for p in shapes[0]] == [
        (HC + "center", "50", "50"), (HC + "ax1", "100", "50"), (HC + "ax2", "50", "0"),
    ]
    assert dict(shapes[1][0].attrib) == dict(type="CURVE", x1="0", y1="0", x2="50", y2="90")
    assert [(p.tag, p.get("x"), p.get("y"), p.get("subjectIDRef")) for p in shapes[2]] == [
        (HP + "startPt", "2", "3", "0"), (HP + "endPt", "12", "17", "0"),
    ]
