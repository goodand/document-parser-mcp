"""Conservative whole-page TOC exclusion. Author: chatggpt_gpt6.

PDF adapter supplies physical pages. Other adapters must supply real page boundaries.
No document-specific names, offsets or page numbers are used.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

LEADER = re.compile(r"^.*[가-힣A-Za-z].*?[.·‧․…ㆍ_]{4,}\s*\d{1,5}\s*$")
HEADING = re.compile(r"^(목차|차례|contents|tableofcontents)$", re.I)
FURNITURE = re.compile(r"^[\W\divxlcdmⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$", re.I)


def classify_pages(texts):
    results = []
    for index, text in enumerate(texts):
        lines = [s.strip() for s in text.splitlines() if s.strip()]
        headings = [s for s in lines if HEADING.fullmatch(re.sub(r"\s", "", s))]
        entries = [s for s in lines if LEADER.fullmatch(s)]
        other = [s for s in lines if s not in entries and s not in headings and not FURNITURE.fullmatch(s)]
        # Long non-entry lines are a veto, even if TOC entries dominate the page.
        long_body = [s for s in other if len(re.sub(r"\s", "", s)) >= 32]
        density = len(entries) / max(1, len(entries) + len(other))
        eligible = len(entries) >= 3 and density >= .65 and not long_body
        results.append({"source_page": index + 1, "action": "keep",
                        "heading": bool(headings), "entry_count": len(entries),
                        "entry_density": round(density, 4), "eligible": eligible,
                        "body_veto": long_body, "entry_examples": entries[:3],
                        "reason": "no_toc_evidence"})
    # Connected runs of eligible pages require an explicit TOC heading seed.
    i = 0
    while i < len(results):
        if not results[i]["eligible"]:
            i += 1
            continue
        j = i
        while j < len(results) and results[j]["eligible"]:
            j += 1
        if any(p["heading"] for p in results[i:j]):
            for p in results[i:j]:
                p.update(action="drop", reason="heading_seeded_dense_toc_run")
        i = j
    for p, text in zip(results, texts):
        if p["action"] != "drop" and (p["heading"] or p["entry_count"] or not text.strip()):
            p.update(action="hold", reason="mixed_or_unseeded_or_unreadable")
    return results


def filter_pdf(source, output_dir, write_pdf=False):
    import pymupdf
    source, output_dir = Path(source).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "body.pdf"
    if target == source:
        raise ValueError("Source overwrite is forbidden")
    if (output_dir / "manifest.json").exists() or (write_pdf and target.exists()):
        raise FileExistsError("Use a new output directory")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with pymupdf.open(source) as doc:
        texts = ["\n".join("".join(span["text"] for span in line["spans"])
                           for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"] if block["type"] == 0
                           for line in block["lines"]) for page in doc]
        pages = classify_pages(texts)
        kept = [p["source_page"] for p in pages if p["action"] != "drop"]
        if not kept:
            raise ValueError("Refusing to remove every page")
        manifest = {"author": "chatggpt_gpt6", "version": 1, "source": str(source),
                    "source_sha256": source_hash, "page_basis": "physical_pdf_1based",
                    "pages": pages, "output_to_source_pages": kept,
                    "removed_pages": [p["source_page"] for p in pages if p["action"] == "drop"],
                    "limitations": ["No OCR; unreadable pages retained", "Leaderless TOCs require further detection", "Native HWP/HWPX page deletion is not implemented"],
                    "pdf_written": write_pdf}
        with (output_dir / "body_pages.jsonl").open("w", encoding="utf-8") as f:
            for page in kept:
                f.write(json.dumps({"source_page": page, "text": texts[page-1]}, ensure_ascii=False) + "\n")
        if write_pdf:
            with pymupdf.open() as body:
                for page in kept:
                    body.insert_pdf(doc, from_page=page-1, to_page=page-1)
                body.save(target)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output_dir")
    parser.add_argument("--write-pdf", action="store_true")
    args = parser.parse_args()
    result = filter_pdf(args.source, args.output_dir, args.write_pdf)
    print(json.dumps({"removed_pages": result["removed_pages"], "kept": len(result["output_to_source_pages"])}, ensure_ascii=False))
