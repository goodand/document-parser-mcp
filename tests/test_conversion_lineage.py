"""A PDF hash alone does not prove which HWP produced it."""

from __future__ import annotations

from govparse import convert, hwp_source
from govparse import mcp_server as mcp
from govparse.conversion_lineage import record_conversion, verify_conversion


def test_conversion_manifest_binds_both_files(tmp_path):
    hwp = tmp_path / "source.hwp"
    pdf = tmp_path / "rendered.pdf"
    manifest = tmp_path / "rendered.source.json"
    hwp.write_bytes(b"source version 1")
    pdf.write_bytes(b"pdf version 1")

    record_conversion(hwp, pdf, manifest)

    assert verify_conversion(hwp, pdf, manifest)["status"] == "verified"
    hwp.write_bytes(b"source version 2")
    assert verify_conversion(hwp, pdf, manifest)["status"] == "mismatch"
    hwp.write_bytes(b"source version 1")
    pdf.write_bytes(b"pdf version 2")
    assert verify_conversion(hwp, pdf, manifest)["status"] == "mismatch"


def test_absent_manifest_stays_unverified(tmp_path):
    hwp = tmp_path / "source.hwp"
    pdf = tmp_path / "rendered.pdf"
    hwp.write_bytes(b"hwp")
    pdf.write_bytes(b"pdf")

    result = verify_conversion(hwp, pdf, tmp_path / "missing.json")

    assert result["status"] == "unverified"
    assert result["reason"]


def test_mcp_conversion_rejects_stale_existing_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    monkeypatch.setattr(mcp.platform, "system", lambda: "Windows")
    monkeypatch.setattr(convert, "kill_hancom", lambda: None)
    src = tmp_path / "02_normalized" / "input.hwp"
    dst = tmp_path / "03_converted/hwp2pdf" / "input.pdf"
    src.parent.mkdir(parents=True)
    dst.parent.mkdir(parents=True)
    src.write_bytes(b"HWP")
    dst.write_bytes(b"old PDF")
    monkeypatch.setattr(convert, "convert_one", lambda *_: (True, 1, ""))

    result = mcp.convert_hwp(src.name)

    assert "새 PDF가 생성되지 않았습니다" in result
    assert dst.read_bytes() == b"old PDF"
    assert not dst.with_suffix(".source.json").exists()


def test_mcp_conversion_records_new_pdf_lineage(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    monkeypatch.setattr(mcp.platform, "system", lambda: "Windows")
    monkeypatch.setattr(convert, "kill_hancom", lambda: None)
    src = tmp_path / "02_normalized" / "input.hwp"
    dst = tmp_path / "03_converted/hwp2pdf" / "input.pdf"
    src.parent.mkdir(parents=True)
    dst.parent.mkdir(parents=True)
    src.write_bytes(b"HWP")
    dst.write_bytes(b"old PDF")

    def fake_convert(source, temp_dst, timeout):
        temp_dst.write_bytes(b"new PDF")
        return True, 1, ""

    monkeypatch.setattr(convert, "convert_one", fake_convert)
    result = mcp.convert_hwp(src.name)

    assert "변환 완료" in result
    assert dst.read_bytes() == b"new PDF"
    assert verify_conversion(src, dst, dst.with_suffix(".source.json"))["status"] == "verified"


def test_diagnosis_stops_when_hwp_pdf_lineage_mismatches(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    src = tmp_path / "02_normalized" / "input.hwp"
    pdf = tmp_path / "03_converted/hwp2pdf" / "input.pdf"
    src.parent.mkdir(parents=True)
    pdf.parent.mkdir(parents=True)
    src.write_bytes(b"old HWP")
    pdf.write_bytes(b"PDF")
    record_conversion(src, pdf, pdf.with_suffix(".source.json"))
    src.write_bytes(b"new HWP")
    monkeypatch.setattr(
        hwp_source, "_rhwp_parse",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not parse before lineage stop")),
    )

    result = json.loads(mcp.diagnose_hwp_pdf_tables(src.name, page=1))

    assert result["comparison_performed"] is False
    assert result["diagnostics"][0]["code"] == "hwp_pdf_lineage_mismatch"
    assert result["use_decision"]["pdf_table_structure"] == "held"
