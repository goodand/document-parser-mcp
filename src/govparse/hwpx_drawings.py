"""Collect original HWPX drawing trees and their enclosing native table cells."""
import re
from xml.etree import ElementTree
from zipfile import ZipFile

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
SHAPES = {HP + name for name in (
    "container", "polygon", "rect", "line", "connectLine", "ellipse",
    "arc", "curve", "pic", "ole", "textart",
)}


def cell_drawings(source):
    objects = []

    def visit(node, cell_path, xml_path, section):
        if node.tag == HP + "tc":
            addr = node.find(HP + "cellAddr")
            if addr is not None:
                cell_path = [*cell_path, [int(addr.attrib["rowAddr"]), int(addr.attrib["colAddr"])]]
        if cell_path and node.tag in SHAPES:
            objects.append({
                "section": section,
                "child_indices": xml_path,
                "cells": cell_path,
                "kind": node.tag[len(HP):],
                "xml": ElementTree.tostring(node, encoding="unicode"),
            })
            return
        for index, child in enumerate(node):
            visit(child, cell_path, [*xml_path, index], section)

    with ZipFile(source) as package:
        for section in package.namelist():
            if re.fullmatch(r"Contents/section[0-9]+\.xml", section):
                with package.open(section) as stream:
                    visit(ElementTree.parse(stream).getroot(), [], [], section)
    return objects
