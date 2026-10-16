"""Windows MCP contract tests for paths, Java, and per-table recovery."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pymupdf

from govparse import chunking
from govparse import mcp_server as M


def test_same_table_uses_page_coordinates():
    known = [[50, 700, 200, 750]]
    assert M._same_table((50, 50, 200, 100), known, 800)
    assert not M._same_table((50, 200, 200, 250), known, 800)
    assert not M._same_table((50, 50, 200, 100), [[0, 0, 0, 0]], 800)


def test_java_home_is_used_when_path_has_no_java(tmp_path, monkeypatch):
    home = tmp_path / "jdk"
    java = home / "bin" / "java.exe"
    java.parent.mkdir(parents=True)
    java.touch()
    monkeypatch.setenv("JAVA_HOME", str(home))
    monkeypatch.setattr(chunking.shutil, "which", lambda _: None)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(chunking.subprocess, "run", fake_run)
    ok, _ = chunking.run_jar(tmp_path / "input.pdf", tmp_path / "out", "1g")
    assert ok
    assert captured["cmd"][0] == str(java)


def test_same_page_missing_table_is_recovered_and_cropped(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    source = tmp_path / "02_normalized" / "agency__doc.pdf"
    source.parent.mkdir(parents=True)
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=800)
    page.draw_rect(pymupdf.Rect(50, 50, 200, 100), fill=(1, 0, 0))
    page.draw_rect(pymupdf.Rect(50, 200, 200, 250), fill=(0, 0, 1))
    pdf.save(str(source))
    pdf.close()

    out_dir = tmp_path / "04_parsed" / source.stem
    out_dir.mkdir(parents=True)
    table = {
        "type": "table",
        "page number": 1,
        "bounding box": [50, 700, 200, 750],
        "rows": [{"cells": [{"kids": [{"content": "known-value"}]}]}],
    }
    (out_dir / "p00000-00001.json").write_text(
        json.dumps({"kids": [table]}), encoding="utf-8"
    )

    candidates = [
        SimpleNamespace(bbox=(50, 50, 200, 100), extract=lambda: [["known-value"]]),
        SimpleNamespace(bbox=(50, 200, 200, 250), extract=lambda: [["missing-value"]]),
    ]
    monkeypatch.setattr(
        pymupdf.Page,
        "find_tables",
        lambda self, **kwargs: SimpleNamespace(tables=candidates),
    )

    result = M.get_tables(source.name, recover_missing=True)
    assert "missing-value" in result
    assert result.count("known-value") == 1

    M.extract_table_images(source.name, only_missed=True)
    images = list((tmp_path / "06_table_images" / source.stem).glob("*.png"))
    assert [image.name for image in images] == ["p0001_t1.png"]
    crop = pymupdf.Pixmap(str(images[0]))
    red, green, blue = crop.pixel(crop.width // 2, crop.height // 2)[:3]
    assert blue > red and blue > green
