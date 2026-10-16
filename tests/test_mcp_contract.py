"""상수가 '선언' 이 아니라 '실제로 쓰이는지' 를 검사한다.

적대적 검토에서 드러난 사각지대다. 기존 테스트는 상수값만 봤고, 구현이
그 상수를 무시해도 통과했다. 실제로 output_format 은 받기만 하고 버려져
항상 json,markdown 이 쓰이고 있었다 — markdown 은 colspan 을 잃어 예산표
헤더 계층이 무너지므로 제외하기로 한 포맷이다.
"""

from __future__ import annotations

import inspect
import os

import pytest

from govparse import chunking
from govparse import mcp_server as M

# ---------------------------------------------------------------- format 전달


def test_run_jar_accepts_format_argument():
    """chunking.run_jar 가 포맷을 인자로 받아야 한다 (하드코딩 금지)."""
    sig = inspect.signature(chunking.run_jar)
    assert "fmt" in sig.parameters or "output_format" in sig.parameters, (
        "run_jar 가 포맷을 인자로 받지 않는다 — 호출자가 지정할 수 없다"
    )


def test_run_jar_passes_format_to_cli(monkeypatch, tmp_path):
    """요청한 포맷이 실제 CLI 인자로 전달되는지 확인."""
    captured: dict = {}

    class _Proc:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **_kw):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(chunking.subprocess, "run", fake_run)
    chunking.run_jar(tmp_path / "x.pdf", tmp_path / "out", heap="1g", fmt="json,html")

    cmd = captured["cmd"]
    assert "--format" in cmd
    value = cmd[cmd.index("--format") + 1]
    assert value == "json,html", f"요청한 포맷이 아니라 {value!r} 가 전달됐다"
    assert "markdown" not in value
    assert "--use-struct-tree" in cmd


def test_parse_document_default_is_the_constant():
    """파라미터 기본값이 DEFAULT_FORMAT 과 같아야 한다."""
    sig = inspect.signature(M.parse_document)
    assert sig.parameters["output_format"].default == M.DEFAULT_FORMAT


def test_parse_document_forwards_format(monkeypatch, tmp_path):
    """parse_document 가 output_format 을 하류로 넘기는지."""
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    d = tmp_path / "02_normalized"
    d.mkdir(parents=True)
    (d / "부처__문서.pdf").write_bytes(b"%PDF-1.4\n")

    seen: dict = {}

    def fake_parse(pdf, report, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(chunking, "parse_document", fake_parse)
    M.parse_document("부처__문서.pdf", output_format="json")

    assert seen.get("fmt") == "json", f"포맷이 전달되지 않았다: {seen}"


# ---------------------------------------------------------------- snap 전달


def test_snap_tolerance_reaches_find_tables(monkeypatch, tmp_path):
    """SNAP_TOLERANCE 가 실제로 find_tables 호출에 들어가는지.

    상수만 검사하면 구현이 다른 값을 써도 통과한다. 이 값은 실버셋 156건
    전수 검증으로 정해진 것이라 어긋나면 자릿수 밀림이 되살아난다.
    """
    import pymupdf

    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    norm = tmp_path / "02_normalized"
    norm.mkdir(parents=True)

    pdf_path = norm / "부처__문서.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(pdf_path))
    doc.close()

    parsed = tmp_path / "04_parsed" / pdf_path.stem
    parsed.mkdir(parents=True, exist_ok=True)
    (parsed / "p00000-00001.json").write_text('{"kids": []}', encoding="utf-8")

    captured: dict = {}
    original = pymupdf.Page.find_tables

    def spy(self, **kwargs):
        captured.update(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "find_tables", spy)
    M.get_tables("부처__문서.pdf", recover_missing=True)

    assert captured.get("snap_tolerance") == M.SNAP_TOLERANCE, (
        f"find_tables 에 snap_tolerance 가 {captured.get('snap_tolerance')} 로 전달됐다"
    )


# ---------------------------------------------------------------- 부작용


def test_prepare_corpus_does_not_leak_env(tmp_path, monkeypatch):
    """도구 호출이 프로세스 환경변수를 영구 변경하면 안 된다.

    MCP 서버는 한 프로세스에서 여러 요청을 처리한다. 환경변수를 남기면
    다음 요청이 엉뚱한 경로를 쓴다.
    """
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path / "data"))
    src = tmp_path / "src"
    src.mkdir()
    before = dict(os.environ)

    M.prepare_corpus(str(src))

    after = dict(os.environ)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert not changed, f"환경변수가 남았다: {changed}"


def test_no_module_level_mutable_state():
    """전역 가변 상태를 두면 동시 요청에서 서로 덮어쓴다."""
    assert not hasattr(M, "_STATE"), (
        "_STATE 전역이 남아 있다 — 동시 호출 시 경합한다"
    )


# ---------------------------------------------------------------- 입력 검증


@pytest.mark.parametrize(
    "bad",
    ["../secret.pdf", "a/b.pdf", "a\\b.pdf", "..\\..\\x.pdf"],
)
def test_doc_name_rejects_path_separators(bad, tmp_path, monkeypatch):
    """doc_name 은 파일명이지 경로가 아니다. 코퍼스 밖을 가리키면 거부한다."""
    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    (tmp_path / "02_normalized").mkdir(parents=True)
    out = M.parse_document(bad)
    assert "이름" in out or "없습니다" in out
    assert "파싱 완료" not in out
