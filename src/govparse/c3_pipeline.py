"""Product orchestration of the source-preserving C3 experiments.

Author: chatgpt_gpt6. Automatic representation checks do not establish semantics.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from .conversion_lineage import verify_conversion
from .normalize import sha256_of


def run_c3(source: Path, root: Path, output_kind: str = "structure") -> dict:
    pdf = root / "03_converted" / "hwp2pdf" / f"{source.stem}.pdf"
    base = {"route": "c3", "method": "C1_native_pyhwp_rhwp+C2_pdf+C3_controls",
            "complete": False}
    if not pdf.is_file():
        return base | {"status": "held", "reason": "Source-lineaged PDF required",
                       "next_tool": "convert_hwp"}
    lineage = verify_conversion(source, pdf, pdf.with_suffix(".source.json"))
    if lineage["status"] != "verified":
        return base | {"status": "held", "reason": "PDF conversion lineage unverified",
                       "lineage": lineage, "next_tool": "convert_hwp"}
    executable = "hwp5proc.exe" if os.name == "nt" else "hwp5proc"
    local = Path(sys.executable).parent / executable
    pyhwp = str(local) if local.is_file() else shutil.which(executable)
    if not pyhwp:
        return base | {"status": "unsupported", "reason": "pyhwp hwp5proc unavailable",
                       "next_action": "Install/sync project dependencies; do not substitute rhwp-only as C3"}
    output = root / "04_parsed" / source.stem / "c3" / uuid.uuid4().hex
    output.parent.mkdir(parents=True, exist_ok=True)
    source_hash, pdf_hash = sha256_of(source), sha256_of(pdf)
    log_path = output.parent / f"{output.name}.log"
    commands = [
        [sys.executable, "-m", "govparse.c3_build", "--source", str(source),
         "--pdf", str(pdf), "--output", str(output), "--pyhwp", pyhwp],
        [sys.executable, "-m", "govparse.c3_controls", "--output", str(output)],
    ]
    try:
        with log_path.open("w", encoding="utf-8") as log:
            for command in commands:
                subprocess.run(command, check=True, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               timeout=900, env=dict(os.environ, PYTHONUTF8="1"))
        if source_hash != sha256_of(source) or pdf_hash != sha256_of(pdf):
            raise ValueError("Source or PDF changed during C3 run")
        if verify_conversion(source, pdf, pdf.with_suffix(".source.json"))["status"] != "verified":
            raise ValueError("Conversion lineage changed during C3 run")
        core = json.loads((output / "document.json").read_text(encoding="utf-8"))
        controls = json.loads((output / "controls.json").read_text(encoding="utf-8"))
        verification = json.loads((output / "verification.json").read_text(encoding="utf-8"))
        mismatches = json.loads((output / "mismatches.json").read_text(encoding="utf-8"))
        # Additional representations have their own namespace; core remains byte-for-byte on disk.
        combined = dict(core, combination_annotations={"controls": controls["entries"]})
        combined_path = output / "combined_document.json"
        combined_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
        reopened = json.loads(combined_path.read_text(encoding="utf-8"))
        reopened.pop("combination_annotations")
        if reopened != core:
            raise ValueError("Composition changed core")
        report = base | {"status": "held", "artifacts_ready": True,
            "reason": "Structural/representation checks completed; local visual links and semantic judgments require review",
            "c3_document": str(combined_path), "verification": str(output / "verification.json"),
            "mismatches": str(output / "mismatches.json"), "counts": core["meta"]["counts"],
            "phases": {"C1": "measured_with_holds" if mismatches or core["rhwp_comparison_holds"] else "checked",
                       "C2": "lineage_verified_local_links_held",
                       "C3": "consumer_projection_checked_semantics_held"},
            "mismatch_count": len(mismatches), "control_counts": controls["counts"],
            "core_unchanged": True, "commands": commands, "author": "chatgpt_gpt6",
            "versions": {name: importlib.metadata.version(name) for name in ("govparse", "pyhwp", "rhwp-python")},
            "source_sha256": source_hash, "pdf_sha256": pdf_hash,
            "next_action": "Review mismatches and source-linked PDF regions using c3-hwp-parsing; retain unresolved claims as held"}
        if output_kind == "rag":
            rag = output / "paragraphs.jsonl"
            with rag.open("w", encoding="utf-8") as stream:
                for key, paragraph in core["paragraphs"].items():
                    stream.write(json.dumps({"id": key, "text": paragraph["plain_text_projection"],
                        "anchor": paragraph["anchor"], "source_sha256": source_hash,
                        "text_verification": paragraph["text_verification"],
                        "semantic_status": "held", "c3_document": str(combined_path)}, ensure_ascii=False) + "\n")
            report["rag_path"] = str(rag)
            report["rag_scope"] = "paragraph projection; table relationships and controls remain in c3_document"
        (output / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as exc:
        return base | {"status": "held", "artifacts_ready": False,
                       "reason": str(exc), "log": str(log_path), "output": str(output)}
