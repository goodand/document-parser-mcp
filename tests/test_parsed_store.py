"""Behavioral regression coverage for active parse runs and legacy migration."""

import json

import pytest
from pypdf import PdfReader, PdfWriter

from govparse import chunking
from govparse.parsed_store import adopt_legacy, page_offset, selected_json_files


def pdf_at(path, pages=4):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    writer.write(path)
    return path


def json_at(path, pages, text="sample"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"number of pages": pages, "kids": [
        {"type": "paragraph", "page number": 1, "content": text}
    ]}), encoding="utf-8")
    return path


def test_legacy_overlap_is_rejected_even_when_content_matches(tmp_path):
    json_at(tmp_path / "p00000-00004.json", 4)
    json_at(tmp_path / "p00000-00002.json", 2)
    json_at(tmp_path / "p00002-00004.json", 2)
    with pytest.raises(ValueError, match="(?i)overlap|중복|겹"):
        selected_json_files(tmp_path)


def test_legacy_nonoverlap_and_offsets(tmp_path):
    a = json_at(tmp_path / "p00000-00002.json", 2)
    b = json_at(tmp_path / "p00002-00004.json", 2)
    assert selected_json_files(tmp_path) == [a, b]
    assert page_offset(b) == 2
    assert page_offset(tmp_path / "ordinary-document.json") == 0


def test_adoption_selects_explicit_full_set_without_deleting_legacy(tmp_path):
    source = pdf_at(tmp_path / "source.pdf")
    folder = tmp_path / "parsed"
    a = json_at(folder / "p00000-00002.json", 2)
    b = json_at(folder / "p00002-00004.json", 2)
    stale = json_at(folder / "p00000-00004.json", 4)
    adopt_legacy(folder, [b, a], source)
    assert selected_json_files(folder) == [a, b]
    assert stale.exists()
    a.write_text(a.read_text(encoding="utf8").replace("sample", "changed"), encoding="utf8")
    with pytest.raises(ValueError):
        selected_json_files(folder)


@pytest.mark.parametrize("ranges", [[(0, 2)], [(0, 3), (2, 4)], [(0, 5)]])
def test_adoption_requires_exact_source_coverage(tmp_path, ranges):
    source = pdf_at(tmp_path / "source.pdf")
    folder = tmp_path / "parsed"
    files = [json_at(folder / f"p{a:05d}-{b:05d}.json", b-a) for a, b in ranges]
    with pytest.raises(ValueError):
        adopt_legacy(folder, files, source)


def test_adoption_rejects_foreign_path_and_declared_count_mismatch(tmp_path):
    source = pdf_at(tmp_path / "source.pdf")
    outside = json_at(tmp_path / "p00000-00004.json", 4)
    with pytest.raises(ValueError):
        adopt_legacy(tmp_path / "parsed", [outside], source)
    bad = json_at(tmp_path / "parsed" / "p00000-00004.json", 2)
    with pytest.raises(ValueError):
        adopt_legacy(bad.parent, [bad], source)


def run_parse(root, source, size=2, whole=False):
    report = chunking.Report()
    chunking.parse_document(source, report, "1g", size, whole, False, data_root=root)
    return report


def test_repeated_runs_and_failed_retry_preserve_active(tmp_path, monkeypatch):
    source = pdf_at(tmp_path / "02_normalized" / "source.pdf")
    fail = False
    seen_inputs = []

    def fake_jar(pdf, out, **kwargs):
        seen_inputs.append(pdf)
        pages = len(PdfReader(pdf).pages)
        json_at(out / f"{pdf.stem}.json", pages, "failed" if fail else "good")
        return (False, "deliberate failure") if fail else (True, "")

    monkeypatch.setattr(chunking, "run_jar", fake_jar)
    run_parse(tmp_path, source, size=2)
    folder = tmp_path / "04_parsed" / "source"
    first = selected_json_files(folder)
    assert len(first) == 2
    run_parse(tmp_path, source, size=4)
    second = selected_json_files(folder)
    assert len(second) == 1 and second != first
    assert all(p.exists() for p in first)
    fail = True
    report = run_parse(tmp_path, source, size=4)
    assert any(not item.ok for item in report.chunks)
    assert selected_json_files(folder) == second
    assert len({p.parent for p in seen_inputs}) == 3


def test_failed_whole_and_recursive_parents_are_not_selected(tmp_path, monkeypatch):
    source = pdf_at(tmp_path / "02_normalized" / "source.pdf")

    def fake_jar(pdf, out, **kwargs):
        pages = len(PdfReader(pdf).pages)
        json_at(out / f"{pdf.stem}.json", pages)
        return (pages == 1, "OutOfMemoryError" if pages > 1 else "")

    monkeypatch.setattr(chunking, "run_jar", fake_jar)
    report = run_parse(tmp_path, source, size=4, whole=True)
    files = selected_json_files(tmp_path / "04_parsed" / "source")
    assert len(files) == 4
    assert [page_offset(p) for p in files] == [0, 1, 2, 3]
    assert len(report.chunks) == 4 and all(c.ok for c in report.chunks)


def test_same_name_source_replacement_does_not_reuse_old_slices(tmp_path, monkeypatch):
    source = pdf_at(tmp_path / "02_normalized" / "source.pdf", 2)
    widths = []

    def fake_jar(pdf, out, **kwargs):
        reader = PdfReader(pdf)
        widths.append(float(reader.pages[0].mediabox.width))
        json_at(out / f"{pdf.stem}.json", len(reader.pages))
        return True, ""

    monkeypatch.setattr(chunking, "run_jar", fake_jar)
    run_parse(tmp_path, source)
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=144, height=72)
    writer.write(source)
    run_parse(tmp_path, source)
    assert widths == [72, 144]


def test_walk_includes_nested_list_items_and_table_cells():
    tree = {"type": "list", "list items": [{"type": "list item", "kids": [
        {"type": "paragraph", "content": "목록 본문"},
        {"type": "table", "rows": [{"cells": [{"content": "셀"}]}]},
    ]}]}
    assert chunking._text_of(tree) == "목록 본문 셀"


def test_successful_whole_run_is_selected_once(tmp_path, monkeypatch):
    source = pdf_at(tmp_path / "02_normalized" / "source.pdf")

    def fake_jar(pdf, out, **kwargs):
        json_at(out / f"{pdf.stem}.json", len(PdfReader(pdf).pages))
        return True, ""

    monkeypatch.setattr(chunking, "run_jar", fake_jar)
    run_parse(tmp_path, source, whole=True)
    folder = tmp_path / "04_parsed" / "source"
    assert [p.name for p in selected_json_files(folder)] == ["source.json"]
    assert json.loads((folder / "_active_parse.json").read_text())["origin"] == "parser_run"


def test_stitch_artifacts_are_isolated_between_audits(tmp_path, monkeypatch):
    source = pdf_at(tmp_path / "02_normalized" / "source.pdf")
    paths = []

    def fake_jar(pdf, out, **kwargs):
        paths.append((pdf, out))
        json_at(out / f"{pdf.stem}.json", len(PdfReader(pdf).pages))
        return True, ""

    monkeypatch.setattr(chunking, "run_jar", fake_jar)
    report = chunking.Report(chunks=[
        chunking.ChunkResult(source.name, "agency", 0, 0, 2, ok=True, last_type="table"),
        chunking.ChunkResult(source.name, "agency", 2, 2, 4, ok=True,
                             first_type="table", first_table_has_header=False),
    ])
    for _ in range(2):
        chunking.audit_boundaries(source, report, True, "1g", data_root=tmp_path)
    assert len(paths) == 2 and paths[0][0] != paths[1][0]
    assert all(pdf.parent.name == out.parent.name for pdf, out in paths)


def test_adopted_source_change_is_rejected(tmp_path):
    source = pdf_at(tmp_path / "source.pdf")
    folder = tmp_path / "parsed"
    item = json_at(folder / "p00000-00004.json", 4)
    adopt_legacy(folder, [item], source)
    pdf_at(source, 5)
    with pytest.raises(ValueError, match="(?i)source|원본"):
        selected_json_files(folder)


def test_node_outside_declared_range_is_rejected(tmp_path):
    source = pdf_at(tmp_path / "source.pdf")
    item = json_at(tmp_path / "parsed" / "p00000-00004.json", 4)
    data = json.loads(item.read_text(encoding="utf8"))
    data["kids"][0]["page number"] = 5
    item.write_text(json.dumps(data), encoding="utf8")
    with pytest.raises(ValueError):
        adopt_legacy(item.parent, [item], source)


def test_manifest_source_page_count_cannot_bless_partial_outputs(tmp_path):
    source = pdf_at(tmp_path / "source.pdf")
    folder = tmp_path / "parsed"
    item = json_at(folder / "p00000-00004.json", 4)
    manifest = adopt_legacy(folder, [item], source)
    data = json.loads(manifest.read_text(encoding="utf8"))
    assert data["origin"] == "legacy_adoption"
    data["source"]["pages"] = 2
    manifest.write_text(json.dumps(data), encoding="utf8")
    with pytest.raises(ValueError, match="page count"):
        selected_json_files(folder)


@pytest.mark.parametrize("failure", ["missing", "invalid", "wrong_count", "partial"])
def test_bad_success_artifacts_never_replace_active(tmp_path, monkeypatch, failure):
    source = pdf_at(tmp_path / "02_normalized" / "source.pdf")
    folder = tmp_path / "04_parsed" / "source"
    old = json_at(folder / "p00000-00004.json", 4, "old")
    adopt_legacy(folder, [old], source)

    def fake_jar(pdf, out, **kwargs):
        out.mkdir(parents=True, exist_ok=True)
        if failure == "partial" and page_offset(pdf) == 0:
            json_at(out / f"{pdf.stem}.json", len(PdfReader(pdf).pages))
            return True, ""
        if failure == "partial":
            return False, "deliberate failure"
        if failure == "invalid":
            (out / f"{pdf.stem}.json").write_text("{broken", encoding="utf8")
        elif failure == "wrong_count":
            json_at(out / f"{pdf.stem}.json", 99)
        return True, ""

    monkeypatch.setattr(chunking, "run_jar", fake_jar)
    try:
        run_parse(tmp_path, source)
    except ValueError:
        pass
    assert selected_json_files(folder) == [old]
