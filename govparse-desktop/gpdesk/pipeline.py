"""Per-job workspace: engine calls, Hancom preview, and the three export paths.

Everything here blocks and is meant for worker threads. Each step is a child
process (MCP session, Hancom COM, OCR) that the CancelToken can kill.
"""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import runner
from .bundle import OcrStatus, build_json_bundle, collect_table_pngs, parse_saved_count
from .config import EngineConfig
from .embedded import collect_picture_uris, extract_bindata
from .html_export import render_html
from .jobs import Job, sha256_file
from .publish import PublishError, Stager, check_relative_refs

HERE = Path(__file__).resolve().parent
OLE_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FORMATS = {"json": "JSON + PNG", "html": "HTML", "hwpx": "HWPX (.hwpx)", "hwp": "HWP (.hwp)"}


class EngineError(Exception):
    pass


@dataclass
class ExportReport:
    fmt: str
    job_id: str
    source_name: str
    final_path: Path
    file_count: int
    notes: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def _hancom(cfg: EngineConfig, args: list[str], timeout: float, token=None) -> runner.Result:
    cmd = [str(cfg.engine_python), str(HERE / "hancom_tool.py"), *args]
    r = runner.run_hancom(cmd, timeout=timeout, token=token, env=cfg.env())
    if r.returncode != 0:
        raise EngineError((r.stderr or r.stdout).strip()[-400:] or "한컴 작업 실패")
    return r


def verify_with_hancom(cfg: EngineConfig, path: Path, token=None, timeout: float = 300) -> int:
    r = _hancom(cfg, ["verify", str(path)], timeout, token)
    for line in r.stdout.splitlines():
        if line.startswith("pages="):
            return int(line[6:])
    return 0


def render_with_hancom(cfg: EngineConfig, src: Path, pdf: Path, token=None,
                       timeout: float = 900) -> int:
    """Uses the engine's verified worker (govparse.hwp_worker) in its own process."""
    pdf.parent.mkdir(parents=True, exist_ok=True)
    tmp = pdf.with_name(f".{pdf.stem}.{uuid.uuid4().hex[:8]}.pdf")
    cmd = [str(cfg.engine_python), "-m", "govparse.hwp_worker", str(src), str(tmp)]
    try:
        r = runner.run_hancom(cmd, timeout=timeout, token=token, env=cfg.env())
        if r.returncode != 0 or not tmp.is_file():
            raise EngineError((r.stderr or "PDF 미생성").strip()[-400:])
        tmp.replace(pdf)
    finally:
        tmp.unlink(missing_ok=True)
    import pymupdf

    with pymupdf.open(str(pdf)) as d:
        return d.page_count


class Workspace:
    def __init__(self, cfg: EngineConfig, job: Job) -> None:
        self.cfg, self.job = cfg, job
        self.root = Path(cfg.data_root) / "jobs" / job.job_id
        self.input_dir = self.root / "input"
        self.data = self.root / "data"
        self.doc_name: str | None = None
        self.tools: list[str] = []
        self.pdf: Path | None = None
        self.pages: int | None = None
        self.preview_error: str | None = None
        self._ir: dict | None = None

    # ------------------------------------------------------------ engine
    def call(self, calls: list[tuple[str, dict]], token=None, timeout: float = 1800) -> list[dict]:
        self.root.mkdir(parents=True, exist_ok=True)
        spec = self.root / f"calls-{uuid.uuid4().hex[:8]}.json"
        spec.write_text(json.dumps({"engine_python": str(self.cfg.engine_python),
                                    "engine_src": str(self.cfg.engine_src),
                                    "data_root": str(self.data), "calls": calls},
                                   ensure_ascii=False), "utf-8")
        try:
            r = runner.run([sys.executable, "-m", "gpdesk.engine_cli", str(spec)], timeout=timeout,
                           token=token, env=self.cfg.env(self.data), cwd=str(HERE.parent))
        finally:
            spec.unlink(missing_ok=True)
        if r.returncode != 0:
            raise EngineError(r.stderr.strip()[-600:] or "엔진 호출 실패")
        out = json.loads(r.stdout)
        self.tools = out["tools"]
        for res in out["results"]:
            if res["is_error"]:
                raise EngineError(f"{res['name']}: {res['text'][:400]}")
        return out["results"]

    def prepare(self, token=None) -> str:
        """Copy only the dropped file into a private input folder, then normalize."""
        self.input_dir.mkdir(parents=True, exist_ok=True)
        dst = self.input_dir / self.job.source.name
        shutil.copy2(self.job.source, dst)
        if sha256_file(dst) != self.job.sha256:
            raise EngineError("복사 중 원본이 바뀌었습니다. 다시 열어 주세요.")
        self.call([("prepare_corpus", {"source_dir": str(self.input_dir)}),
                   ("list_documents", {})], token)
        names = sorted(p.name for p in (self.data / "02_normalized").iterdir()
                       if p.name.endswith(self.job.source.name))
        if len(names) != 1:
            raise EngineError(f"정규화 결과를 하나로 특정할 수 없습니다: {names}")
        self.doc_name = names[0]
        return self.doc_name

    @property
    def pdf_path(self) -> Path:
        stem = Path(self.doc_name or self.job.source.name).stem
        return self.data / "03_converted" / "hwp2pdf" / f"{stem}.pdf"

    def preview_pdf(self, token=None) -> tuple[Path, int]:
        self.preview_error = None
        src = self.data / "02_normalized" / self.doc_name
        try:
            self.pages = render_with_hancom(self.cfg, src, self.pdf_path, token)
        except (EngineError, runner.RunTimeout) as e:
            self.preview_error = str(e)
            raise
        self.pdf = self.pdf_path
        return self.pdf, self.pages

    def ir(self, token=None) -> dict:
        if self._ir is None:
            res = self.call([("parse_hwp_source", {"doc_name": self.doc_name})], token)
            meta = json.loads(res[0]["text"])
            if "error" in meta:
                raise EngineError(meta["error"])
            self._ir = json.loads(Path(meta["document_json"]).read_text("utf-8"))
        return self._ir

    # ------------------------------------------------------------ exports
    def export(self, fmt: str, target: Path, token=None, ocr: bool = True,
               progress=lambda msg: None) -> ExportReport:
        target = Path(target)
        if fmt in ("hwpx", "hwp"):
            target = target.with_suffix("." + fmt)
        stager = Stager(self.job.source, target, single_file=fmt in ("hwpx", "hwp"))
        with stager:
            if fmt == "json":
                details = self._export_json(stager.staging, token, ocr, progress)
            elif fmt == "html":
                details = self._export_html(stager.staging, token, progress)
            elif fmt in ("hwpx", "hwp"):
                details = self._export_hangul(fmt, stager.staged_file, token, progress)
            else:
                raise PublishError(f"알 수 없는 형식: {fmt}")
            if token is not None:
                token.check()
        final = stager.final
        count = sum(1 for p in final.rglob("*") if p.is_file()) if final.is_dir() else 1
        return ExportReport(fmt, self.job.job_id, self.job.display_name, final, count,
                            details.pop("notes", []), details)

    def _embedded(self) -> dict:
        try:
            return extract_bindata(self.data / "02_normalized" / self.doc_name)
        except Exception:  # noqa: BLE001 - report as missing pictures instead
            return {}

    def _export_json(self, out: Path, token, ocr: bool, progress) -> dict:
        progress("원본 구조 읽는 중")
        ir = self.ir(token)
        notes: list[str] = []
        captures: list = []
        ocr_map: dict[str, OcrStatus] = {}
        if self.pdf is None or not self.pdf.exists():
            state = "unverified"
            notes.append(f"표 캡처 미확인: 미리보기 PDF 없음 ({self.preview_error or '변환 전'})")
        else:
            progress("표 영역 캡처 중")
            img_dir = self.data / "06_table_images" / self.pdf.stem
            shutil.rmtree(img_dir, ignore_errors=True)
            res = self.call([("extract_table_images",
                              {"doc_name": self.doc_name, "only_missed": False})], token)
            n = parse_saved_count(res[0]["text"])
            if n is None:
                state = "failed"
                notes.append(f"표 캡처 실패: {res[0]['text'][:200]}")
            else:
                captures = collect_table_pngs(img_dir, n)
                state = "ok" if n else "none_detected"
                notes.append(f"표 캡처 {n}장" if n else "감지된 표 영역 없음")
            if captures and ocr and self.cfg.ocr_models:
                progress(f"OCR {len(captures)}장")
                ocr_map = self._ocr([p for _, p in captures], token)
            elif captures:
                notes.append("OCR 미실행" + ("" if ocr else " (사용자 선택)"))
        build = build_json_bundle(out, ir, self.job.display_name, self.job.sha256,
                                  captures=captures, capture_state=state,
                                  embedded=self._embedded(), ocr=ocr_map)
        data = json.loads((out / "document.json").read_text("utf-8"))
        bad = check_relative_refs(out, [a["path"] for a in data["govparse_desktop"]["assets"]])
        if bad:
            raise PublishError(f"상대 경로 검증 실패: {bad[:3]}")
        if build["missing_pictures"]:
            notes.append(f"그림 {len(build['missing_pictures'])}개 추출 못 함")
        notes.append(f"내장 그림 {build['counts']['embedded_picture']}개")
        return {"notes": notes, **build}

    def _ocr(self, pngs: list[Path], token) -> dict[str, OcrStatus]:
        out = self.root / f"ocr-{uuid.uuid4().hex[:6]}.json"
        cmd = [str(self.cfg.engine_python), str(HERE / "ocr_tool.py"), str(self.cfg.ocr_models),
               str(out), *map(str, pngs)]
        try:
            runner.run(cmd, timeout=60 + 20 * len(pngs), token=token, env=self.cfg.env())
            raw = json.loads(out.read_text("utf-8")) if out.exists() else {}
        except runner.RunTimeout:
            raw = json.loads(out.read_text("utf-8")) if out.exists() else {}
        finally:
            out.unlink(missing_ok=True)
        return {p.name: OcrStatus(**raw[p.name]) if p.name in raw else
                OcrStatus("failed", [], "OCR 결과 없음") for p in pngs}

    def _export_html(self, out: Path, token, progress) -> dict:
        progress("HTML 생성 중")
        ir = self.ir(token)
        emb = self._embedded()
        assets: dict[str, str] = {}
        for uri in collect_picture_uris(ir):
            bid = uri[6:]
            if uri.startswith("bin://") and bid.isdigit() and int(bid) in emb:
                name, data = emb[int(bid)]
                rel = f"assets/bin{bid}{Path(name).suffix.lower()}"
                (out / "assets").mkdir(exist_ok=True)
                (out / rel).write_bytes(data)
                assets[uri] = rel
        (out / "index.html").write_text(
            render_html(ir, assets, Path(self.job.display_name).stem), "utf-8")
        bad = check_relative_refs(out, list(assets.values()))
        if bad:
            raise PublishError(f"상대 경로 검증 실패: {bad[:3]}")
        missing = len(collect_picture_uris(ir)) - len(assets)
        notes = [f"그림 {len(assets)}개 포함"] + ([f"그림 {missing}개 미포함"] if missing else [])
        return {"notes": notes, "pictures": len(assets), "missing_pictures": missing}

    def _export_hangul(self, fmt: str, staged: Path, token, progress) -> dict:
        progress("본문·표 복원 중")
        res = self.call([("restore_hwp_body", {"doc_name": self.doc_name})], token)
        meta = json.loads(res[0]["text"])
        if "error" in meta:
            raise EngineError(meta["error"])
        restored = Path(meta["hwpx_path"])
        pics = len(collect_picture_uris(self.ir(token)))
        notes = ["복원 범위: 본문 문단·표(중첩 포함)",
                 f"그림 {pics}개 제외 · 원본 글꼴/문단 서식·쪽 배치 제외"]
        diff = (meta.get("table_diff") or {}).get("json_to_hwpx") or {}
        notes.append(f"표 셀 비교(JSON→HWPX) 차이 {diff.get('different_records', '?')}건")
        if fmt == "hwpx":
            shutil.copy2(restored, staged)
        else:
            progress("한컴에서 HWP로 저장 중")
            _hancom(self.cfg, ["save", str(restored), str(staged), "HWP"], 900, token)
            if staged.read_bytes()[:8] != OLE_SIG:
                raise EngineError("HWP 저장 결과가 OLE 형식이 아닙니다.")
            progress("한글에서 다시 열어 확인 중")
            verify_with_hancom(self.cfg, staged, token)
            notes.append("OLE 시그니처·한글 재열기 확인")
        return {"notes": notes, "table_diff": meta.get("table_diff"), "pictures_omitted": pics}
