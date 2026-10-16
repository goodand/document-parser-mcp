"""Section context is lexical; source boundaries and table holds are invariant."""

import json

import pytest

from govparse.hwp_rag import export_hwp_chunks
from govparse.structure_aware_text import export_structure_aware_text_chunks


def project(tmp_path, texts):
    ir = {"body": [{"kind": "paragraph", "text": text} for text in texts]}
    path = tmp_path / "structure.jsonl"
    result = export_structure_aware_text_chunks(ir, "a" * 64, path)
    return result, [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_three_sections_preserve_literal_paragraphs_and_stop_at_next_heading(tmp_path):
    texts = ["1) 사업목적·내용", "목적‧본문\n원문", "", "2) 지원대상",
             "중소기업", "3) 지원 방식 : 보조금", "4) 사업 집행절차", "기관→기관"]
    result, chunks = project(tmp_path, texts)
    assert [chunk["text"] for chunk in chunks] == texts
    assert [chunk["priority_section"] for chunk in chunks] == [
        "사업목적", "사업목적", "사업목적", "지원대상", "지원대상", "지원 방식", None, None,
    ]
    assert chunks[0]["retrieval_usable"] is False
    assert chunks[2]["retrieval_usable"] is False
    assert chunks[5]["retrieval_usable"] is True
    assert chunks[1]["section_context"]["heading_source_locator"]["json_pointer"] == "/body/0"
    assert result["explicit_section_counts"] == {"사업목적": 1, "지원대상": 1, "지원 방식": 1}


def test_explicit_parenthetical_labels_and_prose_false_positives(tmp_path):
    _, chunks = project(tmp_path, ["지원방식 다양화", "사업목적에 따라 지원대상 결정",
                                  "◦ (지원대상) 특구사업자", "◦ (지원내용) 기술개발",
                                  "◦ (지원방식) 매년 14개 TP", "지원 방법 설명"])
    assert chunks[0]["priority_section"] is None
    assert chunks[1]["priority_section"] is None
    assert chunks[2]["priority_section"] == "지원대상"
    assert chunks[3]["priority_section"] is None
    assert chunks[4]["priority_section"] == "지원 방식"
    assert chunks[4]["section_context"]["heading_id"] == chunks[4]["id"]
    assert chunks[5]["priority_section"] == "지원 방식"


def test_business_heading_clears_previous_section(tmp_path):
    _, chunks = project(tmp_path, ["사업목적", "목적 문장", "나. 사업설명자료", "다음 사업",
                                  "사업목적", "목적 문장", "2) 사업개요", "일반 개요"])
    assert [chunk["priority_section"] for chunk in chunks] == [
        "사업목적", "사업목적", None, None, "사업목적", "사업목적", None, None,
    ]


def test_mcp_tool_uses_separate_sidecar_without_overwriting_existing_export(tmp_path, monkeypatch):
    from govparse import hwp_source, mcp_server

    sidecar = tmp_path / "sidecar"
    sidecar.mkdir()
    (sidecar / "document.json").write_text(
        json.dumps({"body": [{"kind": "paragraph", "text": "지원대상: 기업"}]}),
        encoding="utf-8",
    )
    original = sidecar / "chunks.jsonl"
    original.write_bytes(b"old projection")
    monkeypatch.setattr(mcp_server, "_hwp_source_paths", lambda name: (tmp_path / name, sidecar))
    monkeypatch.setattr(hwp_source, "extract_source", lambda *args: {"source_sha256": "c" * 64})
    result = json.loads(mcp_server.export_structure_aware_text_chunks("sample.hwp"))
    assert result["mode"] == "text_only_structure_aware"
    assert result["chunks"] == 1
    assert (sidecar / "structure_aware_text_chunks.jsonl").is_file()
    assert original.read_bytes() == b"old projection"
    assert "error" in json.loads(mcp_server.export_structure_aware_text_chunks("../sample.hwp"))


@pytest.mark.parametrize("fmt", ["hwp", "hwpx"])
def test_best_available_dispatch_preserves_default_and_selects_new_mode(tmp_path, monkeypatch, fmt):
    from govparse import mcp_server, normalize

    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    src = tmp_path / "02_normalized" / f"sample.{fmt}"
    src.parent.mkdir()
    src.write_bytes(b"source")
    monkeypatch.setattr(normalize, "detect_format", lambda path: fmt)
    calls = []

    def export_default(name):
        calls.append(("default", name))
        return json.dumps({"projection": "original"})

    def export_text(name):
        calls.append(("text", name))
        return json.dumps({"projection": "text"})

    monkeypatch.setattr(mcp_server, "export_hwp_rag_chunks", export_default)
    monkeypatch.setattr(mcp_server, "export_structure_aware_text_chunks", export_text)
    default = json.loads(mcp_server.get_best_available(src.name, "rag"))
    text = json.loads(mcp_server.get_best_available(
        src.name, "rag", rag_mode="text_only_structure_aware",
    ))
    assert default["status"] == text["status"] == "ready"
    assert default["result"]["projection"] == "original"
    assert "rag_mode" not in default
    assert text["result"]["projection"] == "text"
    assert text["rag_mode"] == "text_only_structure_aware"
    assert calls == [("default", src.name), ("text", src.name)]


def test_best_available_text_mode_rejects_pdf_and_invalid_modes(tmp_path, monkeypatch):
    from govparse import mcp_server

    monkeypatch.setenv("GOVPARSE_DATA", str(tmp_path))
    src = tmp_path / "02_normalized" / "sample.pdf"
    src.parent.mkdir()
    src.write_bytes(b"%PDF-1.4\n")

    def unexpected_export(*args, **kwargs):
        pytest.fail("unsupported PDF mode must not fall back to an existing export")

    monkeypatch.setattr(mcp_server, "export_rag_chunks", unexpected_export)
    result = json.loads(mcp_server.get_best_available(
        src.name, "rag", rag_mode="text_only_structure_aware",
    ))
    assert result["status"] == "unsupported"
    assert result["detected_format"] == "pdf"
    assert json.loads(mcp_server.get_best_available(
        src.name, "rag", rag_mode="typo",
    ))["status"] == "error"
    assert json.loads(mcp_server.get_best_available(
        src.name, "structure", rag_mode="text_only_structure_aware",
    ))["status"] == "error"


def test_cells_are_held_and_context_does_not_leak_between_cells_or_into_body(tmp_path):
    ir = {"body": [
        {"kind": "paragraph", "text": "1) 사업목적"},
        {"kind": "table", "cells": [
            {"row": 0, "col": 0, "blocks": [
                {"kind": "paragraph", "text": "지원대상: 기업"},
                {"kind": "table", "cells": [{"row": 0, "col": 0, "blocks": [
                    {"kind": "paragraph", "text": "내부 문자열"},
                ]}]},
            ]},
            {"row": 0, "col": 1, "blocks": [{"kind": "paragraph", "text": "→ 기관"}]},
        ]},
        {"kind": "paragraph", "text": "목적 본문"},
    ]}
    old_path, new_path = tmp_path / "chunks.jsonl", tmp_path / "text.jsonl"
    export_hwp_chunks(ir, "b" * 64, old_path)
    original = old_path.read_bytes()
    export_structure_aware_text_chunks(ir, "b" * 64, new_path)
    old = [json.loads(line) for line in original.decode().splitlines()]
    new = [json.loads(line) for line in new_path.read_text(encoding="utf-8").splitlines()]
    assert old_path.read_bytes() == original
    for before, after in zip(old, new, strict=True):
        for key in ("id", "text", "text_sha256", "source_locator"):
            assert before[key] == after[key]
        assert "image_path" not in after
    assert [item["priority_section"] for item in new] == [
        "사업목적", "지원대상", "지원대상", "사업목적", "사업목적",
    ]
    for item in new[1:4]:
        assert item["retrieval_usable"] is False
        assert item["review_required"] is True
        assert "table_cell_relationships_unverified" in item["review_reasons"]
    assert len(new[2]["source_locator"]["table_ancestry"]) == 2


def test_failed_export_preserves_existing_output(tmp_path):
    path = tmp_path / "text.jsonl"
    path.write_text("previous", encoding="utf-8")
    with pytest.raises(ValueError):
        export_structure_aware_text_chunks({"body": [{"kind": "paragraph", "text": 1}]},
                                           "a" * 64, path)
    assert path.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.glob("*.tmp")) == []


def business_header(ordinal, name):
    return {"kind": "table", "text": f"사   업   명\n({ordinal}) {name}\n사업 코드 정보",
            "cells": [{"row": 0, "col": 0, "blocks": [
                {"kind": "paragraph", "text": "사   업   명"},
                {"kind": "paragraph", "text": f"({ordinal}) {name}"},
            ]}]}


@pytest.mark.parametrize("count,classification", [
    (0, "unknown"), (1, "single_candidate"), (2, "multiple_business_sections"),
])
def test_document_business_multiplicity_is_explicit_candidate_evidence(
    tmp_path, count, classification,
):
    ir = {"body": [{"kind": "paragraph", "text": "문서 소개"}]}
    for ordinal in range(1, count + 1):
        ir["body"].extend([business_header(ordinal, f"사업 {ordinal} (1231-881)"),
                           {"kind": "paragraph", "text": "본문"}])
    path = tmp_path / "scopes.jsonl"
    manifest = export_structure_aware_text_chunks(ir, "a" * 64, path)
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    structure = manifest["business_structure"]
    assert structure["classification"] == classification
    assert structure["business_section_candidate_count"] == count
    assert structure["unassigned_paragraphs"] == 1
    assert structure["count_semantics"] == "business_description_candidates_not_grant_programs"
    assert chunks[0]["business_scope"] is None
    for ordinal, candidate in enumerate(structure["candidates"], 1):
        assert candidate["ordinal"] == ordinal
        assert candidate["raw_name"] == f"사업 {ordinal} (1231-881)"
        assert candidate["source_locator"]["json_pointer"] == f"/body/{2 * ordinal - 1}"
        assert all(chunk["business_scope"] == candidate
                   for chunk in chunks if chunk["source_locator"]["body_index"]
                   in (2 * ordinal - 1, 2 * ordinal))


def test_business_header_nested_tables_and_prose_cannot_open_scope(tmp_path):
    fake_text = "사 업 명\n(1) 가짜 사업"
    ir = {"body": [
        {"kind": "paragraph", "text": fake_text},
        {"kind": "table", "text": "일반 표\n" + fake_text, "cells": [
            {"row": 0, "col": 0, "blocks": [business_header(2, "내부 사업")]},
        ]},
        {"kind": "table", "text": "사 업 명\n사업 3", "cells": []},
    ]}
    path = tmp_path / "scopes.jsonl"
    manifest = export_structure_aware_text_chunks(ir, "a" * 64, path)
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert manifest["business_structure"]["classification"] == "unknown"
    assert all(chunk["business_scope"] is None for chunk in chunks)


def test_new_business_resets_section_context_in_header_cells_and_body(tmp_path):
    ir = {"body": [
        business_header(1, "첫 사업"),
        {"kind": "paragraph", "text": "지원대상: 첫 기업"},
        {"kind": "paragraph", "text": "첫 사업 대상 본문"},
        business_header(2, "다음 사업"),
        {"kind": "paragraph", "text": "새 사업 일반 본문"},
        {"kind": "paragraph", "text": "지원방식: 보조금"},
    ]}
    path = tmp_path / "scopes.jsonl"
    export_structure_aware_text_chunks(ir, "a" * 64, path)
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [chunk["priority_section"] for chunk in chunks] == [
        None, None, "지원대상", "지원대상", None, None, None, "지원 방식",
    ]
    assert [chunk["business_scope"]["ordinal"] for chunk in chunks] == [
        1, 1, 1, 1, 2, 2, 2, 2,
    ]


def test_business_header_with_no_paragraphs_still_resets_next_body_context(tmp_path):
    header = business_header(2, "빈 헤더 사업")
    header["cells"] = []
    ir = {"body": [business_header(1, "첫 사업"),
                   {"kind": "paragraph", "text": "사업목적"}, header,
                   {"kind": "paragraph", "text": "다음 본문"}]}
    path = tmp_path / "scopes.jsonl"
    export_structure_aware_text_chunks(ir, "a" * 64, path)
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert chunks[-1]["priority_section"] is None
    assert chunks[-1]["business_scope"]["ordinal"] == 2
