"""Work behind the desktop GUI (worker.py modes). Blocking; run as a child process.

IR source: rhwp when it can open the file; otherwise (e.g. a section XML over
rhwp's fixed 32 MB limit) the OWPML builder for HWPX. Preview: rhwp page render,
or a Hancom-made PDF (PowerShell COM, new automation instance only) as fallback.
Every export reads the same figure linkage (figures.json via FigureService).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import runner
from .bundle import build_json_bundle
from .figure_ir import attach_links
from .figure_service import FigureService
from .html_export import render_html
from .hwpx_restore import restore_with_figures, verify_restored

HERE = Path(__file__).resolve().parent
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def app_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GovParse Desktop"


def decisions_root() -> Path:
    return app_dir() / "decisions"


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------ Hancom
def hancom(mode: str, src: Path, dst: Path | None = None, fmt: str = "PDF", timeout: float = 900) -> int:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "hancom.ps1"),
           "-Mode", mode, "-Src", str(Path(src).resolve())]
    if dst is not None:
        cmd += ["-Dst", str(Path(dst).resolve()), "-Format", fmt]
    r = runner.run(cmd, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("한컴 작업 실패: " + (r.stderr or r.stdout).strip()[-300:])
    m = re.search(r"pages=(\d+)", r.stdout)
    return int(m.group(1)) if m else 0


def hancom_pdf(src: Path) -> Path:
    cache = app_dir() / "cache" / f"{_sha(src)}.pdf"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp.pdf")
        hancom("save", src, tmp, "PDF")
        tmp.replace(cache)
    return cache


# ------------------------------------------------------------------ source
class Source:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.is_hwpx = self.path.read_bytes()[:2] == b"PK"
        self.doc = None
        self.error = None
        if not os.environ.get("GPDESK_FORCE_OWPML"):
            try:
                import rhwp

                self.doc = rhwp.parse(str(self.path))
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
        else:
            self.error = "GPDESK_FORCE_OWPML"
        if self.doc is None and not self.is_hwpx:
            raise RuntimeError(f"HWP 문서를 열 수 없습니다: {self.error}")

    @property
    def builder(self) -> str:
        return "rhwp" if self.doc is not None else "gpdesk.owpml_ir"

    def ir(self) -> dict:
        if self.doc is not None:
            return json.loads(self.doc.to_ir_json())
        from .owpml_ir import build_ir

        return build_ir(self.path)

    def asset_bytes(self, ir: dict) -> dict[str, bytes]:
        """imageN -> original bytes."""
        if self.is_hwpx:
            from .figures import extract

            return extract(self.path).asset_bytes
        from types import SimpleNamespace

        out = {}
        for n in _pictures(ir):
            uri = (n.get("image") or {}).get("uri", "")
            m = re.search(r"(\d+)$", uri)
            if m and f"image{m.group(1)}" not in out:
                try:
                    out[f"image{m.group(1)}"] = self.doc.bytes_for_image(
                        SimpleNamespace(image=SimpleNamespace(uri=uri)))
                except Exception:  # noqa: BLE001
                    pass
        return out

    def pdf_bytes(self) -> bytes:
        if self.doc is not None:
            return self.doc.render_pdf()
        return hancom_pdf(self.path).read_bytes()


def _pictures(ir) -> list[dict]:
    out: list[dict] = []

    def walk(ns):
        for n in ns or []:
            if n.get("kind") == "picture":
                out.append(n)
            for c in n.get("cells", []) or []:
                walk(c.get("blocks"))

    walk(ir.get("body"))
    return out


def _link_figures(src: Source, ir: dict) -> dict:
    if not src.is_hwpx:
        return {"matched": 0, "mismatch": ["HWP: 그림-설명 연결은 IR 기반 검토로 표시"]}
    out = Path(tempfile.mkdtemp(prefix="gpfig-"))
    r = FigureService(decisions_root()).handle({"cmd": "open", "src": str(src.path), "out": str(out)})
    if not r["ok"]:
        return {"matched": 0, "mismatch": [r["error"]]}
    return attach_links(ir, json.loads(Path(r["file"]).read_text("utf-8")))


def _ext(data: bytes) -> str:
    for sig, e in ((b"\x89PNG", ".png"), (b"\xff\xd8", ".jpg"), (b"GIF8", ".gif"), (b"BM", ".bmp")):
        if data.startswith(sig):
            return e
    return ".bin"


def _write_assets(ir: dict, assets: dict[str, bytes], out: Path, sub: str) -> dict[str, str]:
    """Copy picture bytes next to the output; returns uri -> relative path."""
    rel: dict[str, str] = {}
    for n in _pictures(ir):
        uri = (n.get("image") or {}).get("uri", "")
        m = re.search(r"(\d+)$", uri)
        key = f"image{m.group(1)}" if m else None
        if key and key in assets and uri not in rel:
            path = f"{sub}/{key}{_ext(assets[key])}"
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            (out / path).write_bytes(assets[key])
            rel[uri] = path
        if uri in rel:
            n["asset"] = rel[uri]
    return rel


def img_srcs(html: str) -> list[str]:
    return re.findall(r'<img[^>]*src="([^"]+)"', html)


# ------------------------------------------------------------------ modes
def preview(path: Path, png: Path, page: int) -> tuple[int, str]:
    src = Source(path)
    if src.doc is not None:
        png.write_bytes(src.doc.render_png(int(page), scale=1.1, max_pixels=8000000))
        return src.doc.page_count, "rhwp"
    import pymupdf

    with pymupdf.open(str(hancom_pdf(src.path))) as pdf:
        p = pdf[min(int(page), pdf.page_count - 1)]
        z = 1.1 * 96 / 72
        p.get_pixmap(matrix=pymupdf.Matrix(z, z)).save(str(png))
        return pdf.page_count, "hancom-pdf"


def _notes_for_builder(src: Source) -> list[str]:
    if src.doc is None:
        return ["미리보기 프로그램(rhwp)이 문서를 열지 못해 원본 XML에서 직접 구조를 읽었습니다 (" + (src.error or "")[:80] + ")"]
    return []


def emit_progress(event: dict) -> None:
    """One machine-readable line on stderr; the GUI shows it in the status strip."""
    sys.stderr.write("@@progress " + json.dumps(event, ensure_ascii=False) + "\n")
    sys.stderr.flush()


# Per-page rendering for rhwp documents: lets the table search report progress page by page
# instead of waiting for one whole-document render_pdf() call (13 min for 1,182 pages).
PER_PAGE_SVG = True


def _table_pages(src: "Source", progress):
    """Yield (page number, PyMuPDF page) for the table search, reporting progress."""
    import pymupdf

    if src.doc is not None and PER_PAGE_SVG:
        n = src.doc.page_count
        for i in range(n):
            progress({"stage": "표 찾기", "done": i, "total": n, "detail": "쪽을 그리며 표를 찾는 중"})
            svg = src.doc.render_svg(i).encode("utf-8")
            with pymupdf.open(stream=svg, filetype="svg") as one:
                pdf = pymupdf.open(stream=one.convert_to_pdf(), filetype="pdf")
            yield i + 1, pdf[0]
        progress({"stage": "표 찾기", "done": n, "total": n})
        return
    progress({"stage": "쪽 그리기", "detail": "한컴 PDF 재사용" if src.doc is None else "전체 문서를 한 번에 그리는 중"})
    pdf = pymupdf.open(stream=src.pdf_bytes(), filetype="pdf")
    n = pdf.page_count
    for i, page in enumerate(pdf):
        progress({"stage": "표 찾기", "done": i, "total": n})
        yield i + 1, page
    progress({"stage": "표 찾기", "done": n, "total": n})


TABLE_REASONS = {
    "native_table_path_not_found": "원본에서 같은 표를 찾지 못함",
    "table_structure_or_text_not_exact": "원본 표와 구조·글자가 정확히 일치하지 않음",
    "rendered_pages_are_blank": "그려진 쪽이 비어 있음",
    "table_id_not_present_in_ir": "구조에 없는 표",
}
TABLE_REASON_PREFIX = {"isolation_failed": "표 분리 실패", "rhwp_render_failed": "표 그리기 실패",
                       "crop_failed": "여백 자르기 실패", "source_conversion_failed": "원본 변환 실패"}


def table_reason(reason: str | None) -> str:
    if not reason:
        return ""
    head = reason.split(":", 1)[0]
    return TABLE_REASONS.get(reason) or TABLE_REASON_PREFIX.get(head) or reason


def table_label(table_id: str) -> str:
    """'s0/b3/t1/c1_0/t0' -> '본문 2번째 표 › 2행 1열 칸 안 1번째 표' (the ID itself stays the key)."""
    parts = table_id.split("/")
    out = []
    for i, p in enumerate(parts):
        m = re.fullmatch(r"t(\d+)", p)
        if m and i == 2:
            out.append(f"본문 {int(m.group(1)) + 1}번째 표")
        elif m:
            cell = re.fullmatch(r"c(\d+)_(\d+)", parts[i - 1])
            where = f"{int(cell.group(1)) + 1}행 {int(cell.group(2)) + 1}열 칸 안 " if cell else ""
            out.append(f"{where}{int(m.group(1)) + 1}번째 표")
    return " › ".join(out) or table_id


def _table_renderer():
    """The engine's per-table renderer, or None for an engine without it."""
    try:
        from govparse.table_images import extract_table_images_by_id, iter_table_nodes
    except Exception:  # noqa: BLE001
        return None
    return extract_table_images_by_id, iter_table_nodes


def table_images(path: Path, ir: dict, out_dir: Path, progress) -> dict | None:
    """One PNG per table (several for a table split over pages), matched against the source XML by the engine.

    Annotates every IR table block with table_id and table_image {status, paths (result-relative), reason}.
    Returns the summary, or None when the engine has no per-table renderer.
    """
    import threading

    renderer = _table_renderer()
    if renderer is None:
        return None
    extract, iter_nodes = renderer
    nodes = list(iter_nodes(ir))
    total = len(nodes)
    digest = _sha(path)
    tags = {hashlib.sha256(f"{digest}:{sid}".encode()).hexdigest()[:12] for sid, _ in nodes}
    stop = threading.Event()

    def watch():  # the engine call has no callback: count tables whose PNGs have appeared
        while not stop.wait(1.0):
            seen = {p.name[6:18] for p in out_dir.glob("table-*.png")} if out_dir.is_dir() else set()
            progress({"stage": "표 이미지", "done": len(seen & tags), "total": total, "detail": "표마다 따로 그리는 중"})

    progress({"stage": "표 이미지", "done": 0, "total": total})
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        manifest = extract(path, ir, out_dir) if total else {"tables": {}}
    finally:
        stop.set()
        t.join()
    progress({"stage": "표 이미지", "done": total, "total": total})
    rel = out_dir.relative_to(out_dir.parents[1]).as_posix()  # assets/tables
    rows = manifest.get("tables", {})
    not_rendered = []
    for sid, node in nodes:
        rec = rows.get(sid) or {"status": "unmatched", "paths": [], "reason": "table_id_not_present_in_ir"}
        ok = rec.get("status") == "rendered"
        node["table_id"] = sid
        node["table_image"] = {"status": rec.get("status"), "paths": [f"{rel}/{p}" for p in rec.get("paths") or []] if ok else [],
                               "reason": None if ok else rec.get("reason")}
        if not ok:
            not_rendered.append({"table_id": sid, "label": table_label(sid), "status": rec.get("status"),
                                 "reason": rec.get("reason"), "reason_text": table_reason(rec.get("reason"))})
    return {"tables": total, "rendered": total - len(not_rendered), "not_rendered": len(not_rendered),
            "png": sum(len(n["table_image"]["paths"]) for _, n in nodes), "manifest": f"{rel}/manifest.json",
            "missing": not_rendered}


def run(mode: str, path: Path, out: Path, ocr: bool = False, progress=lambda e: None) -> dict:
    progress({"stage": "구조 읽기"})
    src = Source(path)
    ir = src.ir()
    assets = src.asset_bytes(ir)
    progress({"stage": "그림 연결"})
    figs = _link_figures(src, ir)
    notes = _notes_for_builder(src)
    if figs.get("mismatch"):
        notes += ["그림-설명 연결 미적용: " + m for m in figs["mismatch"]]
    rep: dict = {"mode": mode, "source": str(path), "source_sha256": _sha(path), "ir_builder": src.builder,
                 "figures": figs, "notes": notes}
    if mode == "html":
        rel = _write_assets(ir, assets, out, "assets")
        (out / "document.html").write_text(render_html(ir, rel, Path(path).stem), "utf-8")
        missing = [s for s in img_srcs((out / "document.html").read_text("utf-8")) if not (out / s).is_file()]
        rep.update(saved=not missing, files=1 + len(rel), missing_assets=missing)
    elif mode == "json":
        captures, state = [], "unverified"
        tables = None
        try:
            tables = table_images(Path(path), ir, out / "assets" / "tables", progress)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(out / "assets" / "tables", ignore_errors=True)
            notes.append(f"표별 이미지 실패 · 쪽 단위 표 캡처로 대신함 ({str(e)[:120]})")
        if tables is None and not any(n.startswith("표별 이미지") for n in notes):
            notes.append("표별 이미지: 이 엔진에는 표마다 그리는 기능이 없어 쪽 단위 표 캡처로 대신함")
        if tables is not None and tables["not_rendered"]:
            notes.append(f"표 이미지 미생성 {tables['not_rendered']}개 (표 {tables['tables']}개 중) · 결과 보기 목록에 사유 표시")
        try:
            if tables is not None:
                raise StopIteration  # every table has its own image or a recorded reason: no page captures
            import pymupdf

            cap_dir = out / ".captures"
            cap_dir.mkdir()
            for no, page in _table_pages(src, progress):
                for i, t in enumerate(page.find_tables().tables):
                    f = cap_dir / f"p{no:04d}_t{i}.png"
                    page.get_pixmap(clip=t.bbox, dpi=160).save(str(f))
                    captures.append((no, f))
            state = "ok" if captures else "none_detected"
        except StopIteration:
            state = "not_used"
        except Exception as e:  # noqa: BLE001
            notes.append(f"표 캡처 미확인: 미리보기 PDF를 만들지 못함 ({str(e)[:120]})")
        emb = {}
        for key, data in assets.items():
            m = re.search(r"(\d+)$", key)
            if m:
                emb[int(m.group(1))] = (key + _ext(data), data)
        for n in _pictures(ir):  # same relative names build_json_bundle writes
            m = re.search(r"(\d+)$", (n.get("image") or {}).get("uri", ""))
            if m and int(m.group(1)) in emb:
                n["asset"] = f"assets/embedded/bin{m.group(1)}{Path(emb[int(m.group(1))][0]).suffix}"
        progress({"stage": "저장"})
        summary = build_json_bundle(out, ir, Path(path).name, rep["source_sha256"], captures=captures,
                                    capture_state=state, embedded=emb, ocr={}, table_images=tables)
        shutil.rmtree(out / ".captures", ignore_errors=True)
        rep.update(saved=True, bundle=summary)
    elif mode in ("hwpx", "hwp"):
        hwpx = out / "document.hwpx"
        restore = restore_with_figures(ir, assets, hwpx)
        verify = verify_restored(hwpx, expected_pictures=len(_pictures(ir)))
        if mode == "hwpx":  # Hancom recomputes row heights when it saves HWP, so this only applies to HWPX
            notes.append("앱 미리보기는 표의 행 높이를 다시 계산하지 않아 그림이 겹쳐 보일 수 있습니다. 한글에서 열면 행이 늘어납니다.")
        notes += ["복원 범위: 본문 문단·중첩 표·그림·원문 캡션",
                  "원본 글꼴·문단 서식·쪽 배치는 복원하지 않음(행 높이 고정)",
                  "사용자가 정한 연결은 document.links.json 에만 기록"]
        rep.update(restore=restore, verify=verify, saved=verify["ok"])
        if mode == "hwp":
            hwp = out / "document.hwp"
            hancom("save", hwpx, hwp, "HWP")
            if hwp.read_bytes()[:8] != OLE:
                raise RuntimeError("HWP 저장 결과가 OLE 형식이 아닙니다.")
            rep["reopened"] = hancom("verify", hwp) >= 1
            hwpx.unlink()
            notes.append("한컴 HWP 저장 · OLE 시그니처 · 한글 재열기 확인")
    else:
        raise ValueError(f"unknown mode {mode}")
    rep["files"] = _file_manifest(out)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), "utf-8")
    return rep


def _file_manifest(out: Path) -> list[dict]:
    """sha256 of every published file, so a result loaded later can be checked for changes."""
    return [{"path": p.relative_to(out).as_posix(), "sha256": _sha(p), "bytes": p.stat().st_size}
            for p in sorted(out.rglob("*")) if p.is_file() and p.name != "report.json"]


def zip_result(folder: Path, target: Path, progress=lambda e: None) -> dict:
    """One .zip of a result folder for sending it on; the folder itself stays as the app's working copy.

    The zip holds the folder by name, so its relative image paths still resolve after unzipping.
    PNGs are stored (already compressed); text is deflated. Written to a temporary name and renamed,
    so a half-written zip is never left under the final name, and an existing file is never replaced.
    """
    import zipfile

    from .publish import unique_target

    folder, target = Path(folder), Path(target)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    tmp = target.parent / f".govparse-zip-{os.getpid()}-{folder.name}.tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for i, p in enumerate(files):
                if i % 50 == 0:
                    progress({"stage": "압축", "done": i, "total": len(files)})
                kind = zipfile.ZIP_STORED if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif") else zipfile.ZIP_DEFLATED
                z.write(p, (Path(folder.name) / p.relative_to(folder)).as_posix(), compress_type=kind)
        progress({"stage": "압축", "done": len(files), "total": len(files)})
        final = unique_target(target)
        os.rename(tmp, final)  # fails instead of overwriting
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"zip": str(final), "files": len(files), "bytes": final.stat().st_size}


def verify_manifest(folder: Path) -> dict | None:
    """None when the result has no manifest (older result, or files moved out of their folder)."""
    rep = folder / "report.json"
    if not rep.is_file():
        return None
    try:
        files = json.loads(rep.read_text("utf-8")).get("files")
    except ValueError:
        return None
    if not files:
        return None
    changed, missing = [], []
    for f in files:
        p = folder / f["path"]
        if not p.is_file():
            missing.append(f["path"])
        elif _sha(p) != f["sha256"]:
            changed.append(f["path"])
    return {"checked": len(files), "changed": changed, "missing": missing}


def convert(json_path: Path, out: Path, fmt: str) -> dict:
    """Existing JSON result -> HTML or HWPX, without the original file."""
    data = json.loads(Path(json_path).read_text("utf-8"))
    ir = data.get("source_ir") if isinstance(data.get("source_ir"), dict) else (
        data if isinstance(data.get("body"), list) else None)
    if ir is None:
        raise ValueError("GovParse 결과 JSON이 아닙니다 (source_ir 또는 body 없음).")
    base = Path(json_path).parent
    assets: dict[str, bytes] = {}
    for n in _pictures(ir):
        m = re.search(r"(\d+)$", (n.get("image") or {}).get("uri", ""))
        a = n.get("asset")
        if m and a and (base / a).is_file():
            assets[f"image{m.group(1)}"] = (base / a).read_bytes()
    rep = {"mode": f"convert-{fmt}", "from": str(json_path), "notes": [
        "기존 결과 JSON에서 변환: 원본 파일을 다시 읽지 않음"]}
    if fmt == "html":
        rel = _write_assets(ir, assets, out, "assets")
        (out / "document.html").write_text(render_html(ir, rel, Path(json_path).parent.name), "utf-8")
        rep.update(saved=True, pictures=len(rel))
    elif fmt == "hwpx":
        hwpx = out / "document.hwpx"
        rep.update(restore=restore_with_figures(ir, assets, hwpx),
                   verify=verify_restored(hwpx, expected_pictures=len(_pictures(ir))))
        rep["saved"] = rep["verify"]["ok"] or rep["restore"]["pictures"] == len(assets)
    else:
        raise ValueError(f"unknown format {fmt}")
    integ = verify_manifest(Path(json_path).parent)
    rep["source_integrity"] = integ
    if integ is None:
        rep["notes"].append("원본 결과의 무결성 미확인(검증 목록 없음)")
    elif integ["changed"] or integ["missing"]:
        rep["notes"].append(f"주의: 원본 결과가 저장 후 바뀌었습니다 (변경 {len(integ['changed'])} · 없음 {len(integ['missing'])})")
    rep["files"] = _file_manifest(out)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), "utf-8")
    return rep


def summary(path: Path) -> dict:
    """Read the document structure once and report what was found (shown while the user works)."""
    import time

    t0 = time.time()
    src = Source(path)
    ir = src.ir()
    counts = {"paragraphs": 0, "tables": 0, "nested_tables": 0, "pictures": 0}

    def walk(nodes, depth):
        for n in nodes or []:
            k = n.get("kind")
            if k == "table":
                counts["tables"] += 1
                counts["nested_tables"] += depth > 0
                for c in n.get("cells") or []:
                    walk(c.get("blocks"), depth + 1)
            elif k == "picture":
                counts["pictures"] += 1
            elif k == "paragraph" and (n.get("text") or "").strip():
                counts["paragraphs"] += 1

    walk(ir.get("body"), 0)
    note = ("rhwp로 쪽을 바로 그립니다." if src.doc is not None else
            "미리보기 프로그램(rhwp)이 이 문서를 열지 못해 한컴으로 전체 문서를 PDF로 바꾼 뒤 표시합니다. 큰 문서는 수 분 걸립니다.")
    return {**counts, "builder": src.builder, "source_sha256": _sha(path), "preview_note": note,
            "seconds": round(time.time() - t0, 1)}


def index(json_path: Path) -> dict:
    """Small asset index of a result JSON for the GUI result view (the JSON itself can be huge)."""
    data = json.loads(Path(json_path).read_text("utf-8"))
    base = Path(json_path).parent
    ir = data.get("source_ir") if isinstance(data.get("source_ir"), dict) else data
    if not isinstance(ir.get("body"), list):
        raise ValueError("GovParse 결과 JSON이 아닙니다 (source_ir 또는 body 없음).")
    meta = data.get("govparse_desktop") or {}
    text_by_path: dict[str, list[str]] = {}
    for n in _pictures(ir):
        link = n.get("figure_link") or {}
        label = {"auto_explicit": "설명 있음", "needs_review": "설명 미확인", "user_linked": "설명 지정함",
                 "user_shared": "설명 지정함(공통)", "user_no_caption": "설명 없는 그림"}.get(link.get("status"), "")
        line = " · ".join(x for x in (link.get("parent_item"), link.get("path_text"),
                                       (link.get("caption_text") or n.get("caption_text") or ""), label) if x)
        if n.get("asset") and line:
            text_by_path.setdefault(n["asset"], []).append(line)
    assets = []
    listed = meta.get("assets")
    if listed is None:  # result written by the first desktop build
        listed = [{"origin": "embedded_picture", "path": a} for a in dict.fromkeys(
            n["asset"] for n in _pictures(ir) if n.get("asset"))]
        listed += [{"origin": "rendered_table_capture", "page": c.get("page"), "path": c.get("path")}
                   for c in (data.get("desktop_export") or {}).get("table_captures", [])]
    for a in listed:
        f = base / a["path"]
        o = a.get("ocr") or {}
        row = {"origin": a["origin"], "page": a.get("page"), "path": a["path"], "file": str(f),
               "exists": f.is_file(), "ocr_status": o.get("status", "not_run"),
               "ocr_texts": o.get("texts", []),
               "text": " / ".join(text_by_path.get(a["path"], []))}
        if a["origin"] == "table_image":
            label = table_label(a["table_id"])
            row.update(table_id=a["table_id"], part=a["part"], parts=a["parts"], label=label,
                       text=label + (f" · {a['part']}/{a['parts']}쪽 조각" if a["parts"] > 1 else ""))
        assets.append(row)
    return {"assets": [a for a in assets if a["exists"]], "missing": [a["path"] for a in assets if not a["exists"]],
            "capture_state": meta.get("capture_state"), "source_name": meta.get("source_name"),
            "table_images": meta.get("table_images"),
            "integrity": verify_manifest(base), "convertible": True}


OCR_PAD = 60  # px of white margin; measured: text touching the picture border is cut by the detector
OCR_MAX_SIDE = 1000  # px; measured: a 2154x477 logo read as "소/ㅇ", the same logo at <=1200 px read correctly


def pad_for_ocr(image: Path, out_dir: Path) -> Path:
    """Copy of the picture on a white canvas with a margin. The original file is not touched."""
    import pymupdf

    src = pymupdf.Pixmap(str(image))
    if src.alpha or (src.colorspace and src.colorspace.n != 3):
        flat = pymupdf.Pixmap(pymupdf.csRGB, src) if src.colorspace and src.colorspace.n != 3 else src
        if flat.alpha:
            flat = pymupdf.Pixmap(flat, 0)  # drop alpha
        src = flat
    k = min(1.0, OCR_MAX_SIDE / max(src.width, src.height))  # shrink large pictures only
    w, h = round(src.width * k), round(src.height * k)
    doc = pymupdf.open()
    page = doc.new_page(width=w + 2 * OCR_PAD, height=h + 2 * OCR_PAD)
    page.insert_image(pymupdf.Rect(OCR_PAD, OCR_PAD, OCR_PAD + w, OCR_PAD + h), pixmap=src)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (Path(image).stem + ".png")
    page.get_pixmap(alpha=False).save(str(out))
    return out.rename(out_dir / Path(image).name) if out.name != Path(image).name else out


def ocr(png: Path) -> dict:
    """OCR one picture with the engine chosen in Settings.

    RapidOCR: the engine's shared function shrinks, pads and maps boxes back once, so it gets the
    original file. An older engine without that function answers needs_preprocess; then, and for
    Windows OCR, the picture is shrunk and padded here first.
    """
    from . import ocr_engines, settings

    png = Path(png)
    by_engine = settings.load()["ocr_engine"] == "rapidocr"
    if by_engine:
        res = ocr_engines.run(png)
        if res.get("status") != "needs_preprocess":
            return res
    work = Path(tempfile.mkdtemp(prefix="gpocr-"))
    try:
        png = pad_for_ocr(png, work / "pad")
    except Exception:  # noqa: BLE001 - unreadable by PyMuPDF: let the OCR engine try the original
        pass
    return ocr_engines.run(png, legacy=True) if by_engine else ocr_engines.run(png)


def _dispatch(argv: list[str]):
    """Do the work for one mode and return what the GUI should read (str or JSON-able)."""
    mode = argv[0]
    if mode == "preview":
        count, renderer = preview(Path(argv[1]), Path(argv[2]), int(argv[3]) if len(argv) > 3 else 0)
        return f"{count}|{renderer}"
    if mode == "convert":
        return convert(Path(argv[1]), Path(argv[2]), argv[3])
    if mode == "summary":
        return summary(Path(argv[1]))
    if mode == "index":
        return index(Path(argv[1]))
    if mode == "ocr":
        return ocr(Path(argv[1]))
    if mode == "zip":
        return zip_result(Path(argv[1]), Path(argv[2]), progress=emit_progress)
    if mode in ("settings", "settings-set"):
        from . import ocr_engines, settings

        if mode == "settings-set":
            settings.update(**{argv[1]: argv[2]})
        return {"settings": settings.load(), "engines": ocr_engines.list_engines(),
                "path": str(settings.settings_path())}
    return run(mode, Path(argv[1]), Path(argv[2]), progress=emit_progress)


def _claim_stdout():
    """Point fd 1 at stderr for the rest of the process and return a stream on the real stdout.

    Native libraries (MuPDF, rhwp) write warnings straight to fd 1; Python-level redirection does not
    catch those. Only the final result is written to the returned stream.
    """
    sys.stdout.flush()
    real = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real


def protected_main(work, argv: list[str]) -> int:
    real = _claim_stdout()
    result = work(argv)
    real.write(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str))
    real.write("\n")
    real.flush()
    return 0


def main(argv: list[str]) -> int:
    if argv[0] == "serve":
        from .figure_service import serve

        return serve(decisions_root(), reply_to=_claim_stdout())
    return protected_main(_dispatch, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
