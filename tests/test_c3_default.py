"""C3 default must not silently report a single-parser result as complete."""
import json

from govparse import mcp_server as M


def test_hwp_default_requires_c3_even_without_prebuilt_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    source = tmp_path / "02_normalized" / "sample.hwp"
    source.parent.mkdir()
    source.write_bytes(b"test source")
    monkeypatch.setattr("govparse.normalize.detect_format", lambda _: "hwp")
    monkeypatch.setattr(M, "parse_hwp_source", lambda _: '{"legacy": true}')
    result = json.loads(M.get_best_available(source.name, "structure"))
    assert result["route"] == "c3"
    assert result["status"] == "held"
    assert result["next_tool"] == "convert_hwp"


def test_hwpx_does_not_claim_hwp_c3(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    source = tmp_path / "02_normalized" / "sample.hwpx"
    source.parent.mkdir()
    source.write_bytes(b"test source")
    monkeypatch.setattr("govparse.normalize.detect_format", lambda _: "hwpx")
    monkeypatch.setattr(M, "parse_hwp_source", lambda _: '{"legacy": true}')
    result = json.loads(M.get_best_available(source.name, "structure"))
    assert result["route"] == "rhwp_source"


def test_c3_rejects_unproven_pdf_before_running_parser(tmp_path, monkeypatch):
    from govparse.c3_pipeline import run_c3
    source = tmp_path / "sample.hwp"
    source.write_bytes(b"source")
    pdf = tmp_path / "03_converted/hwp2pdf/sample.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = run_c3(source, tmp_path)
    assert result["status"] == "held"
    assert result["lineage"]["status"] == "unverified"


def test_missing_pyhwp_never_silently_uses_rhwp(tmp_path, monkeypatch):
    from govparse import c3_pipeline as C
    from govparse.conversion_lineage import record_conversion
    source = tmp_path / "sample.hwp"
    source.write_bytes(b"source")
    pdf = tmp_path / "03_converted/hwp2pdf/sample.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    record_conversion(source, pdf, pdf.with_suffix(".source.json"))
    monkeypatch.setattr(C.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(C.shutil, "which", lambda _: None)
    result = C.run_c3(source, tmp_path)
    assert result["status"] == "unsupported"
    assert result["complete"] is False


def test_formula_projection_is_emitted_once_and_native_retained():
    from lxml import etree
    from collections import Counter
    from govparse.c3_build import segment, projection
    node = etree.fromstring(b'<FieldFormula command="SUM(A1:A2)"><Text>42</Text></FieldFormula>')
    formulas = []
    value = segment(node, 0, {}, {}, Counter(), formulas)
    assert projection([value]) == "42"
    assert formulas[0]["command"] == "SUM(A1:A2)"
    assert value["attributes"]["command"] == "SUM(A1:A2)"


def test_missing_middle_table_cannot_shift_remaining_rhwp_pointers():
    from govparse.c3_build import eligible_rhwp_tables
    native = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
    rhwp = [("/body/0", {"id": "A"}), ("/body/1", {"id": "C"})]
    assert eligible_rhwp_tables(native, rhwp) == []


def test_existing_partial_run_is_never_reused(tmp_path, monkeypatch):
    from govparse import c3_build
    stale = tmp_path / "run"
    stale.mkdir()
    (stale / "raw").mkdir()
    (stale / "raw/pyhwp.xml").write_text("stale source")
    monkeypatch.setattr("sys.argv", ["c3_build", "--source", str(tmp_path / "new.hwp"),
        "--pdf", str(tmp_path / "new.pdf"), "--output", str(stale), "--pyhwp", "unused"])
    import pytest
    with pytest.raises(FileExistsError):
        c3_build.main()
    assert (stale / "raw/pyhwp.xml").read_text() == "stale source"
