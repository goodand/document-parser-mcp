"""Author: chatggpt_gpt6_extrahigh. Export source HWP paragraphs for retrieval.

This projection follows rhwp's structure and retains one record per paragraph
or list item. It does not reconstruct semantic paragraphs or assign PDF pages.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _paragraphs(ir: dict[str, Any], source_hash: str) -> Iterator[dict[str, Any]]:
    body = ir.get("body")
    if not isinstance(body, list):
        raise ValueError("rhwp IR에 body 배열이 없습니다")

    def visit(
        node: Any,
        pointer: str,
        body_index: int,
        table_ancestry: tuple[str, ...] = (),
        cells: tuple[dict[str, Any], ...] = (),
        paragraph_in_cell: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind in {"paragraph", "list_item"}:
            text = node.get("text")
            if not isinstance(text, str):
                raise ValueError(f"문단 text가 문자열이 아닙니다: {pointer}")
            nearest_cell = cells[-1] if cells else None
            locator = {
                "source_sha256": source_hash,
                "body_index": body_index,
                "json_pointer": pointer,
                "table_path": table_ancestry[-1] if table_ancestry else None,
                "table_ancestry": list(table_ancestry),
                "cell": nearest_cell["position"] if nearest_cell else None,
                "cell_ancestry": list(cells),
                "paragraph_in_cell": paragraph_in_cell if nearest_cell else None,
                "rhwp_provenance": node.get("prov"),
            }
            record = {
                # A full source hash plus a unique JSON pointer is injective for
                # records in this IR. No truncated hash/ordinal collision risk.
                "id": f"{source_hash}:{pointer}",
                "kind": kind,
                "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "source_sha256": source_hash,
                "source_locator": locator,
                "file_page": None,
                "retrieval_usable": bool(text.strip()),
            }
            if kind == "list_item":
                # The marker is formatter output and may need visual review;
                # never insert it into the literal paragraph text.
                record["rhwp_list_marker_unverified"] = node.get("marker")
            yield record
            return

        if kind == "table":
            ancestry = (*table_ancestry, pointer)
            table_cells = node.get("cells")
            if not isinstance(table_cells, list):
                raise ValueError(f"표 cells 배열이 없습니다: {pointer}")
            for cell_index, cell in enumerate(table_cells):
                if not isinstance(cell, dict):
                    raise ValueError(f"표 cell 객체가 아닙니다: {pointer}/cells/{cell_index}")
                row, col = cell.get("row"), cell.get("col")
                if type(row) is not int or type(col) is not int or row < 0 or col < 0:
                    raise ValueError(f"표 셀 위치가 잘못되었습니다: {pointer}/cells/{cell_index}")
                cell_path = f"{pointer}/cells/{cell_index}"
                cell_loc = {"table_path": pointer, "position": [row, col],
                            "json_pointer": cell_path}
                blocks = cell.get("blocks")
                if not isinstance(blocks, list):
                    raise ValueError(f"표 셀 blocks 배열이 없습니다: {cell_path}")
                direct_paragraph_index = 0
                for block_index, block in enumerate(blocks):
                    block_path = f"{cell_path}/blocks/{block_index}"
                    block_kind = block.get("kind") if isinstance(block, dict) else None
                    local_index = (direct_paragraph_index if block_kind in
                                   {"paragraph", "list_item"} else None)
                    yield from visit(block, block_path, body_index, ancestry,
                                     (*cells, cell_loc), local_index)
                    if local_index is not None:
                        direct_paragraph_index += 1
            return

        # A future IR container can carry structural children. Text runs and
        # formula instructions are deliberately not emitted as paragraphs.
        for key in ("blocks", "items", "list_items"):
            children = node.get(key)
            if isinstance(children, list):
                for index, child in enumerate(children):
                    yield from visit(child, f"{pointer}/{key}/{index}", body_index,
                                     table_ancestry, cells, paragraph_in_cell)

    for body_index, block in enumerate(body):
        yield from visit(block, f"/body/{body_index}", body_index)


def export_hwp_chunks(ir: dict[str, Any], source_hash: str, out_path: Path) -> dict:
    """Atomically write literal HWP paragraph/list-item records as JSONL.

    Empty paragraphs remain in the file and have ``retrieval_usable=False``.
    The source hash identifies the input HWP, not an inferred PDF conversion.
    """
    if not isinstance(ir, dict):
        raise ValueError("rhwp IR 루트가 객체가 아닙니다")
    if (not isinstance(source_hash, str) or len(source_hash) != 64
            or any(char not in "0123456789abcdef" for char in source_hash)):
        raise ValueError("source_hash는 소문자 SHA-256이어야 합니다")

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp = out_path.with_name(f".{out_path.name}.{uuid.uuid4().hex}.tmp")
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    try:
        with temp.open("xb") as handle:
            for record in _paragraphs(ir, source_hash):
                if record["id"] in seen_ids:
                    raise ValueError(f"중복 문단 위치: {record['id']}")
                seen_ids.add(record["id"])
                encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                           + "\n").encode("utf-8")
                handle.write(encoded)
                digest.update(encoded)
                counts[record["kind"]] += 1
                if not record["retrieval_usable"]:
                    counts["not_retrieval_usable"] += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, out_path)
    finally:
        temp.unlink(missing_ok=True)

    return {
        "author": "chatggpt_gpt6_extrahigh",
        "path": str(out_path),
        "source_sha256": source_hash,
        "jsonl_sha256": digest.hexdigest(),
        "chunks": counts["paragraph"] + counts["list_item"],
        "kind_counts": {"paragraph": counts["paragraph"], "list_item": counts["list_item"]},
        "not_retrieval_usable": counts["not_retrieval_usable"],
        "file_page_policy": "unassigned",
    }
