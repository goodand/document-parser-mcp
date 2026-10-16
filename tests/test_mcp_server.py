"""govparse MCP 서버 테스트.

이 프로젝트는 휴리스틱 지표로 여섯 번 오판한 이력이 있다. 전부 '형태에서
의미를 역추정하고 검증 없이 신뢰' 한 경우였다. 그래서 여기서는 불변식
(invariant) 위주로 검사한다 — 정답을 미리 알 수 있는 것만 테스트한다.

외부 의존(JVM, 한컴 COM, 실제 PDF)이 필요한 부분은 단위 테스트 대상이
아니다. 그 경계는 doctor() 가 보고하는 것으로 갈음한다.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

from govparse import mcp_server as M

# ---------------------------------------------------------------- 경로 규약


def test_data_root_env_override(tmp_path, monkeypatch):
    """GOVPARSE_DATA 가 있으면 그것을 쓴다 — 다른 PC 이식의 전제."""
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    assert M._data_root() == tmp_path


def test_data_root_default_when_unset(monkeypatch):
    monkeypatch.delenv("GOVPARSE_DATA", raising=False)
    from govparse.config import DATA

    assert M._data_root() == DATA


def test_corpus_dir_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path / "새폴더"))
    d = M._corpus_dir()
    assert d.is_dir()


# ---------------------------------------------------------------- java 탐지


def test_find_java_uses_path(monkeypatch):
    monkeypatch.setattr(M.shutil, "which", lambda _n: r"C:\x\java.exe")
    assert M.find_java() == r"C:\x\java.exe"


def test_find_java_falls_back_to_java_home(tmp_path, monkeypatch):
    """PATH 에 없어도 JAVA_HOME 으로 찾아야 한다.

    MCP 서버는 클라이언트가 띄우므로 PATH 가 사용자 셸과 다르다.
    이걸 빠뜨려 java 가 있는데 '없음' 으로 보고한 실제 버그가 있었다.
    """
    exe = "java.exe" if platform.system() == "Windows" else "java"
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / exe).write_text("")
    monkeypatch.setattr(M.shutil, "which", lambda _n: None)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path))
    assert M.find_java() == str(tmp_path / "bin" / exe)


def test_find_java_returns_none_when_absent(monkeypatch):
    from govparse import config

    monkeypatch.setattr(M.shutil, "which", lambda _n: None)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", Path("Z:/no-java-here"))
    assert M.find_java() is None


def test_find_java_uses_bundled_runtime(tmp_path, monkeypatch):
    from govparse import config

    executable = "java.exe" if platform.system() == "Windows" else "java"
    java = tmp_path / ".runtime" / "jdk-21" / "bin" / executable
    java.parent.mkdir(parents=True)
    java.touch()
    monkeypatch.setattr(M.shutil, "which", lambda _n: None)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    assert M.find_java() == str(java)


# ---------------------------------------------------------------- 실측 상수

def test_snap_tolerance_is_one():
    """실버셋 156건 전수 검증으로 정해진 값.

    기본값은 가까운 괘선을 병합해 열 경계를 밀어내고, 값의 끝자리가 옆 칸으로
    넘어간다(35,695|35,695 -> 35,69|535,695). 1 로 바꾸면 오류 12건이 사라지고
    깨지는 것은 없었다. 이 값을 바꾸려면 재검증이 필요하다.
    """
    assert M.SNAP_TOLERANCE == 1


def test_default_format_excludes_markdown():
    """Markdown 은 colspan 을 표현하지 못해 예산표 헤더 계층이 무너진다."""
    assert "markdown" not in M.DEFAULT_FORMAT
    assert "json" in M.DEFAULT_FORMAT
    assert "html" in M.DEFAULT_FORMAT


# ---------------------------------------------------------------- 인벤토리


def test_load_manifest_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    assert M._load_manifest() == []


def _write_manifest(tmp_path: Path, rows: list[str]) -> None:
    d = tmp_path / "05_reports"
    d.mkdir(parents=True, exist_ok=True)
    header = "agency,normalized_name,format_actual,size_bytes,ext_declared,ext_mismatch,is_document"
    (d / "manifest.csv").write_text(
        "\n".join([header, *rows]) + "\n", encoding="utf-8-sig"
    )


def test_load_manifest_filters_non_documents(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    _write_manifest(
        tmp_path,
        [
            "금융위,금융위__a.pdf,pdf,1000,pdf,0,1",
            "금융위,금융위__b.txt,unknown,10,txt,1,0",
        ],
    )
    rows = M._load_manifest()
    assert len(rows) == 1
    assert rows[0]["normalized_name"] == "금융위__a.pdf"


# ---------------------------------------------------------------- 도구 동작


def test_list_documents_without_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    out = M.list_documents()
    assert "prepare_corpus" in out


def test_list_documents_filters_by_agency(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    _write_manifest(
        tmp_path,
        [
            "금융위,금융위__a.pdf,pdf,1048576,pdf,0,1",
            "기후부,기후부__b.hwp,hwp,2097152,hwp,0,1",
        ],
    )
    out = M.list_documents(agency="기후부")
    assert "기후부__b.hwp" in out
    assert "금융위__a.pdf" not in out


def test_list_documents_filters_by_format(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    _write_manifest(
        tmp_path,
        [
            "금융위,금융위__a.pdf,pdf,1048576,pdf,0,1",
            "기후부,기후부__b.hwp,hwp,2097152,hwp,0,1",
        ],
    )
    out = M.list_documents(doc_format="PDF")  # 대소문자 무관해야 함
    assert "금융위__a.pdf" in out
    assert "기후부__b.hwp" not in out


def test_prepare_corpus_rejects_missing_dir(tmp_path):
    out = M.prepare_corpus(str(tmp_path / "없는폴더"))
    assert "없습니다" in out


@pytest.mark.skipif(platform.system() == "Windows", reason="비Windows 경로 검사")
def test_convert_hwp_refuses_on_non_windows():
    out = M.convert_hwp("아무거나.hwp")
    assert "Windows" in out


def test_parse_document_rejects_missing_doc(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    (tmp_path / "02_normalized").mkdir(parents=True)
    out = M.parse_document("없는문서.pdf")
    assert "없습니다" in out


def test_parse_document_requires_conversion_for_hwp(tmp_path, monkeypatch):
    """HWP 를 바로 파싱하려 하면 변환을 먼저 하라고 안내해야 한다."""
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    d = tmp_path / "02_normalized"
    d.mkdir(parents=True)
    (d / "부처__문서.hwp").write_bytes(b"\xd0\xcf\x11\xe0")
    out = M.parse_document("부처__문서.hwp")
    assert "convert_hwp" in out


def test_get_tables_without_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    (tmp_path / "02_normalized").mkdir(parents=True)
    out = M.get_tables("없는문서.pdf")
    assert "parse_document" in out


# ---------------------------------------------------------------- 서버 등록


def test_all_tools_registered():
    import asyncio

    tools = asyncio.run(M.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "doctor",
        "prepare_corpus",
        "list_documents",
        "convert_hwp",
        "parse_document",
        "get_tables",
        "extract_table_images",
        "export_rag_chunks",
        "parse_hwp_source",
        "get_hwp_element",
        "search_hwp_source",
        "export_hwp_rag_chunks",
        "export_structure_aware_text_chunks",
        "inspect_c3_odl_figures",
        "get_best_available",
        "get_visual_evidence",
        "diagnose_hwp_pdf_tables",
        "recover_pdf_table_text",
    }


def test_every_tool_has_description():
    """설명이 없으면 클라이언트가 언제 쓸지 판단할 수 없다."""
    import asyncio

    for t in asyncio.run(M.mcp.list_tools()):
        assert t.description, f"{t.name} 에 설명이 없다"
        assert len(t.description) > 40, f"{t.name} 설명이 너무 짧다"


def test_doctor_reports_without_raising(tmp_path, monkeypatch):
    """환경이 어떻든 doctor 는 죽지 않고 보고해야 한다."""
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    out = M.doctor()
    assert "플랫폼" in out
    assert "java" in out
    assert str(tmp_path) in out


def test_doctor_flags_missing_java(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    monkeypatch.setattr(M, "find_java", lambda: None)
    out = M.doctor()
    assert "FAIL java" in out


def test_state_isolated_between_data_roots(tmp_path, monkeypatch):
    """데이터 루트를 바꾸면 인벤토리도 따라가야 한다 (워크스페이스 분리)."""
    a, b = tmp_path / "a", tmp_path / "b"
    _write_manifest(a, ["금융위,금융위__a.pdf,pdf,1000,pdf,0,1"])
    _write_manifest(b, ["기후부,기후부__b.pdf,pdf,1000,pdf,0,1"])

    monkeypatch.setenv("GOVPARSE_DATA", str(a))
    assert "금융위__a.pdf" in M.list_documents()
    monkeypatch.setenv("GOVPARSE_DATA", str(b))
    assert "기후부__b.pdf" in M.list_documents()


def test_os_environ_not_polluted_by_import():
    """import 만으로 환경변수를 건드리면 안 된다."""
    before = dict(os.environ)
    import importlib

    importlib.reload(M)
    assert dict(os.environ) == before


def test_parse_document_switches_all_output_paths_with_data_root(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    from govparse import chunking

    seen = []

    def fake_parse_range(
        _pdf, _reader, _start, _end, _report, _heap, chunk_dir, out_dir, **_kwargs
    ):
        seen.append((chunk_dir, out_dir))

    monkeypatch.setattr(chunking, "parse_range", fake_parse_range)
    for root in (tmp_path / "a", tmp_path / "b"):
        pdf = root / "02_normalized" / "sample.pdf"
        pdf.parent.mkdir(parents=True)
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with pdf.open("wb") as file:
            writer.write(file)
        monkeypatch.setenv("GOVPARSE_DATA", str(root))
        output = M.parse_document("sample.pdf")
        assert str(root / "04_parsed" / "sample") in output

    for root, (chunk_dir, out_dir) in zip((tmp_path / "a", tmp_path / "b"), seen, strict=True):
        assert chunk_dir.parent == root / "03_converted" / "sample" / "_runs"
        assert out_dir.parent == root / "04_parsed" / "sample" / "_runs"
        assert chunk_dir.name == out_dir.name
    assert seen[0][0].name != seen[1][0].name


def test_get_tables_uses_selected_root(tmp_path, monkeypatch):
    import json

    for root, value in ((tmp_path / "a", "first"), (tmp_path / "b", "second")):
        out = root / "04_parsed" / "sample"
        out.mkdir(parents=True)
        (out / "sample.json").write_text(json.dumps({
            "kids": [{"type": "table", "page number": 1, "rows": [
                {"cells": [{"type": "paragraph", "content": value}]}
            ]}]
        }), encoding="utf-8")

    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path / "a"))
    result_a = M.get_tables("sample.pdf", recover_missing=False)
    assert "first" in result_a and "second" not in result_a
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path / "b"))
    result_b = M.get_tables("sample.pdf", recover_missing=False)
    assert "second" in result_b and "first" not in result_b
