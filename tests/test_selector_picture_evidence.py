import base64
from io import BytesIO

import pymupdf
from PIL import Image, ImageDraw

from govparse.selector_evidence.assess import assess
from govparse.selector_evidence.projected import verify


def test_assess_requires_content_in_every_fragment(tmp_path):
    valid = tmp_path / "content.png"
    blank = tmp_path / "blank.png"
    image = Image.new("RGB", (50, 50), "white")
    image.save(blank)
    ImageDraw.Draw(image).line((0, 0, 49, 49), fill="black", width=2)
    image.save(valid)
    ir = {"body": [{"kind": "table", "cells": []}]}

    complete = assess(ir, "s0/b0/t0", valid, True, relative_to=tmp_path)
    assert complete["eligible"] and complete["paths"] == ["content.png"]
    assert assess(ir, "s0/b0/t0", [valid, blank], True)["reason"] == "empty_or_missing"
    assert (
        assess(ir, "s0/b0/t0", tmp_path / "unavailable.png", True)["reason"] == "empty_or_missing"
    )
    assert assess(ir, "s0/b0/t0", valid, False)["reason"] == "partial_grid"
    assert assess(ir, "unknown", valid, True)["reason"] == "unknown_table"


def test_projected_picture_requires_the_correct_crop_and_resolution(tmp_path):
    image = Image.new("RGB", (60, 40), "white")
    ImageDraw.Draw(image).rectangle((5, 4, 35, 28), fill="navy")
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    raw = encoded.getvalue()
    ir = {"picture_assets": {"image0": {"bytes": base64.b64encode(raw).decode("ascii")}}}
    placements = [{"asset_id": "image0"}]
    bbox = pymupdf.Rect(35, 45, 118, 110)
    area = bbox + (-6, -6, 6, 6)
    candidate = tmp_path / "crop.png"
    with pymupdf.open() as document:
        page = document.new_page(width=160, height=160)
        page.insert_image(pymupdf.Rect(45, 56, 105, 96), stream=raw)
        page.get_pixmap(clip=area, dpi=144, alpha=False).save(candidate)
        verified, matches = verify(document, 1, bbox, candidate, ir, placements, dpi=144)
        assert verified and len(matches) == 1 and matches[0]["asset"] == "image0"
        assert not verify(document, 1, bbox, candidate, ir, placements, dpi=190)[0]
        assert not verify(document, 1, bbox, candidate, ir, placements * 2, dpi=144)[0]
        with Image.open(candidate) as stored:
            Image.new("RGB", stored.size, "white").save(tmp_path / "empty.png")
        assert not verify(document, 1, bbox, tmp_path / "empty.png", ir, placements, dpi=144)[0]
