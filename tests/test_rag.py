import json
from pathlib import Path

import pytest

from govparse.rag import export_document


def setup_export(tmp_path, kids, *, name="p00020-00022.json", max_chars=1800):
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    (parsed / name).write_text(
        json.dumps({"number of pages": 2, "kids": kids}, ensure_ascii=False), encoding="utf-8"
    )
    images = tmp_path / "images"
    images.mkdir()
    (images / "p0021_t0.png").write_bytes(b"existing image")
    (images / "p0022_t0.png").write_bytes(b"other page")
    result = export_document(
        parsed, "문서.hwp", tmp_path / "원문.pdf", images, tmp_path / "rag", max_chars
    )
    records = [
        json.loads(line)
        for line in Path(result["jsonl_path"]).read_text(encoding="utf-8").splitlines()
    ]
    return result, records


def node(kind, text, page=1, **extra):
    return {
        "type": kind,
        "content": text,
        "page number": page,
        "id": 5,
        "bounding box": [1, 2, 3, 4],
        **extra,
    }


def test_lossless_nested_list_and_heading_context(tmp_path):
    heading = node("heading", "사업 목적", **{"heading level": 2})
    item = node("list item", "첫 항목\n한글", kids=[node("paragraph", "자식 내용")])
    _, records = setup_export(
        tmp_path, [heading, {"type": "list", "list items": [item]}, node("paragraph", "자식 내용")]
    )
    assert [r["text"] for r in records] == ["사업 목적", "첫 항목\n한글", "자식 내용", "자식 내용"]
    assert len({r["id"] for r in records}) == 4
    assert records[2]["heading_context"][0]["text"] == "사업 목적"
    assert records[1]["source"]["page"] == 21
    assert records[1]["source"]["local_page"] == 1
    assert records[1]["source"]["element_path"] == "/kids/1/list items/0"
    assert records[1]["source"]["bbox"] == [1, 2, 3, 4]


def test_table_is_atomic_full_structure_and_page_image_candidates(tmp_path):
    text = "긴 셀 내용" * 50
    table = {
        "type": "table",
        "id": 3,
        "page number": 1,
        "rows": [{"cells": [{"column span": 2, "kids": [node("paragraph", text)]}]}],
    }
    _, records = setup_export(tmp_path, [table, node("paragraph", "표 바깥")])
    assert len(records) == 2
    assert records[0]["structure"] == table
    assert text in records[0]["text"]
    assert records[0]["review_required"] is True
    assert records[0]["image_candidates"][0]["match"] == "same_page_only"
    assert records[0]["image_candidates"][0]["source_fingerprint_verified"] is False
    assert len(records[0]["image_candidates"]) == 1
    assert records[0]["image_candidates"][0]["path"].endswith("p0021_t0.png")


def test_oversize_not_split_and_cross_page_not_merged(tmp_path):
    text = "문맥 " * 500
    _, records = setup_export(
        tmp_path,
        [node("paragraph", text), node("paragraph", "다음 쪽 이어지는 내용", 2)],
        max_chars=100,
    )
    assert len(records) == 2
    assert records[0]["text"] == text
    assert "oversized_block" in records[0]["flags"]
    assert "possible_continuation_next_page" in records[0]["flags"]
    assert records[0]["next_id"] == records[1]["id"]
    assert records[1]["previous_id"] == records[0]["id"]


def test_missing_page_does_not_become_offset_page(tmp_path):
    _, records = setup_export(tmp_path, [{"type": "paragraph", "content": "위치 미상"}])
    assert records[0]["source"]["page"] is None
    assert "missing_page" in records[0]["flags"]


def test_rerun_stable_ids_and_manifest(tmp_path):
    result, records = setup_export(tmp_path, [node("paragraph", "내용")])
    again = export_document(
        tmp_path / "parsed",
        "문서.hwp",
        tmp_path / "원문.pdf",
        tmp_path / "images",
        tmp_path / "again",
    )
    repeated = [
        json.loads(line)
        for line in Path(again["jsonl_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert [r["id"] for r in records] == [r["id"] for r in repeated]
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["record_count"] == 1
    assert manifest["selected_json_files"] == ["p00020-00022.json"]
    assert manifest["coverage_status"] == "partial_or_unverified"
    assert manifest["semantic_paragraph_reconstruction"] is False


def test_heading_context_resets_at_same_level_and_pointer_resolves(tmp_path):
    kids = [
        node("heading", "큰 제목", **{"heading level": 1}),
        node("heading", "이전 절", **{"heading level": 2}),
        node("heading", "다음 절", **{"heading level": 2}),
        node("paragraph", "본문"),
    ]
    _, records = setup_export(tmp_path, kids)
    assert [h["text"] for h in records[-1]["heading_context"]] == ["큰 제목", "다음 절"]
    assert records[-1]["heading_context"][-1]["record_id"] == records[2]["id"]
    source = records[-1]["source"]
    original = json.loads(Path(source["json_path"]).read_text(encoding="utf-8"))
    current = original
    for part in source["element_path"].strip("/").split("/"):
        current = current[int(part)] if isinstance(current, list) else current[part]
    assert current["content"] == records[-1]["text"]
    assert len(source["json_sha256"]) == 64


def test_invalid_max_chars_rejected(tmp_path):
    with pytest.raises(ValueError, match="max_chars"):
        export_document(tmp_path, "x.pdf", tmp_path / "x.pdf", tmp_path, tmp_path, 0)


def test_same_and_missing_ids_unique_across_files(tmp_path):
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    for start in (0, 1):
        payload = {
            "number of pages": 1,
            "kids": [
                node("paragraph", "동일"),
                {"type": "paragraph", "content": "동일", "page number": 1},
            ],
        }
        (parsed / f"p{start:05}-{start + 1:05}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    result = export_document(
        parsed, "x.pdf", tmp_path / "x.pdf", tmp_path / "images", tmp_path / "rag"
    )
    records = [
        json.loads(line)
        for line in Path(result["jsonl_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert len({r["id"] for r in records}) == 4


def test_table_text_includes_all_subtrees_once(tmp_path):
    table = {
        "type": "table",
        "content": "표 설명",
        "page number": 1,
        "kids": [node("paragraph", "캡션")],
        "rows": [
            {
                "cells": [
                    {
                        "kids": [
                            node("paragraph", "셀 본문"),
                            {
                                "type": "table",
                                "content": "중첩 설명",
                                "rows": [{"cells": [node("paragraph", "중첩 셀")]}],
                            },
                        ]
                    }
                ]
            }
        ],
    }
    _, records = setup_export(tmp_path, [table])
    assert len(records) == 1
    assert records[0]["text"].splitlines() == ["표 설명", "캡션", "셀 본문", "중첩 설명", "중첩 셀"]
    assert records[0]["structure"] == table


def test_exports_publish_distinct_immutable_directories(tmp_path):
    first, _ = setup_export(tmp_path, [node("paragraph", "원문")])
    prior = Path(first["jsonl_path"]).read_bytes()
    second = export_document(
        tmp_path / "parsed",
        "문서.hwp",
        tmp_path / "원문.pdf",
        tmp_path / "images",
        tmp_path / "rag",
    )
    assert Path(first["jsonl_path"]).parent != Path(second["jsonl_path"]).parent
    assert Path(first["jsonl_path"]).read_bytes() == prior
    assert Path(second["manifest_path"]).is_file()


def test_export_failure_does_not_publish_partial_run(tmp_path, monkeypatch):
    first, _ = setup_export(tmp_path, [node("paragraph", "원문")])
    published = set((tmp_path / "rag").glob("run_*"))
    original = Path.write_text

    def fail_manifest(path, *args, **kwargs):
        if path.name == "manifest.json":
            raise OSError("simulated write failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_manifest)
    with pytest.raises(OSError, match="simulated"):
        export_document(
            tmp_path / "parsed",
            "문서.hwp",
            tmp_path / "원문.pdf",
            tmp_path / "images",
            tmp_path / "rag",
        )
    assert set((tmp_path / "rag").glob("run_*")) == published
    assert Path(first["manifest_path"]).is_file()
