"""Figure review session used by the desktop GUI (worker.py serve).

Protocol: one JSON object per line on stdin, one reply per line on stdout.
  {"cmd":"open","src":...,"out":...}      extract + link + locate, write figures.json
  {"cmd":"decide","action":link|share|unlink|none|undecided|undo,
   "occ_ids":[...],"para_refs":[...]}     apply a person's decision, rewrite figures.json
  {"cmd":"rerun"}                         re-extract the same source (decisions kept)
Reply: {"ok":true,"file":<figures.json>} or {"ok":false,"error":...}
All outputs (review panel, JSON+PNG, HTML) read the same figures.json.
"""

from __future__ import annotations

import hashlib
import json
import sys
import traceback
from pathlib import Path

from .figures import DecisionStore, classify_text, extract, link_figures, summarize, to_json
from .locate import locate


def _thumb(data: bytes, dst: Path, width: int = 96) -> bool:
    try:
        import pymupdf

        pix = pymupdf.Pixmap(data)
        if pix.alpha or (pix.colorspace and pix.colorspace.n > 3):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        n = (pix.width // width).bit_length() - 1  # halve n times, never below `width`
        if n >= 1:
            pix.shrink(n)
        pix.save(str(dst))
        return True
    except Exception:  # noqa: BLE001 - unsupported formats (wmf/emf) get no thumbnail
        return False


_FORMATS = ((b"\x89PNG", "PNG"), (b"\xff\xd8", "JPG"), (b"GIF8", "GIF"), (b"BM", "BMP"),
            (b"\x01\x00\x00\x00", "EMF"), (b"\xd7\xcd\xc6\x9a", "WMF"))


def _source_kind(o, data: bytes | None) -> str:
    """What the document itself says: picture format (from the bytes) and where it is placed."""
    fmt = next((name for sig, name in _FORMATS if data and data.startswith(sig)), None)
    if fmt is None:
        ext = Path(o.asset_path or "").suffix.lstrip(".").upper()
        fmt = ext or "형식 미상"
    where = "본문" if not o.path else ("표 안 (중첩 표)" if len(o.path) > 1 else "표 안")
    if o.in_group:
        where += " · 묶음 개체 안"
    return f"그림({fmt}) · {where}"


class FigureService:
    def __init__(self, decisions_root: Path) -> None:
        self.decisions_root = Path(decisions_root)
        self.doc = None
        self.store = None
        self.out: Path | None = None
        self.src: Path | None = None
        self.renderer_error: str | None = None
        self._renderer = None

    def _open(self, src: Path, out: Path) -> None:
        self.src, self.out = src, out
        out.mkdir(parents=True, exist_ok=True)
        self.note = None
        if src.read_bytes()[:2] == b"PK":
            self.doc = extract(src)
        else:
            # HWP (OLE): structure is read from a Hancom-made HWPX copy, cached by source hash.
            # Decisions stay keyed by the ORIGINAL file's SHA-256.
            from .desktop_worker import _sha, app_dir, hancom

            sha = _sha(src)
            conv = app_dir() / "cache" / f"{sha}.hwpx"
            if not conv.exists():
                conv.parent.mkdir(parents=True, exist_ok=True)
                tmp = conv.with_suffix(".tmp.hwpx")
                try:
                    hancom("save", src, tmp, "HWPX")
                except Exception as e:  # noqa: BLE001
                    raise ValueError(f"HWP를 HWPX로 변환하지 못했습니다(한컴 필요): {e}") from e
                tmp.replace(conv)
            self.doc = extract(conv)
            self.doc.source_sha256 = sha
            self.note = "HWP 원본 · 한컴이 만든 HWPX 변환본에서 그림 위치를 읽었습니다. 원본은 수정하지 않습니다."
        self.store = DecisionStore(self.decisions_root, self.doc.source_sha256)
        self._renderer, self.renderer_error = None, None
        self._locations = None  # occ_id -> location; depends only on the document, not on captions
        try:
            import rhwp

            self._renderer = rhwp.parse(str(src))
        except Exception as e:  # noqa: BLE001
            self.renderer_error = str(e)

    def _write(self) -> Path:
        links = link_figures(self.doc, self.store)
        if self._locations is None:  # rendering every page is slow (TIPS ~20 s); do it once per open
            by_sha = {hashlib.sha256(b).hexdigest(): b for b in self.doc.asset_bytes.values()}
            locate(links, self._renderer, self.renderer_error, asset_bytes=by_sha)
            self._locations = {l.occ.occ_id: l.location for l in links}
        else:
            for l in links:
                l.location = self._locations.get(l.occ.occ_id, l.location)
        thumbs = self.out / "thumbs"
        thumbs.mkdir(exist_ok=True)
        full = self.out / "assets"
        full.mkdir(exist_ok=True)
        rows = to_json(links)
        for row, l in zip(rows, links):
            o = l.occ
            t = thumbs / f"{o.asset_ref or 'none'}.png"
            data = self.doc.asset_bytes.get(o.asset_ref)
            row["thumb"] = str(t) if (t.exists() or (data and _thumb(data, t))) else ""
            row["asset_file"] = ""
            if data:  # full-size copy for per-image OCR / viewing
                ext = {b"\x89PNG": ".png", b"\xff\xd8": ".jpg", b"GIF8": ".gif", b"BM": ".bmp"}
                suffix = next((v for k, v in ext.items() if data.startswith(k)), ".bin")
                f = full / f"{o.asset_ref}{suffix}"
                if not f.exists():
                    f.write_bytes(data)
                row["asset_file"] = str(f)
            row["source_kind"] = _source_kind(o, data)
            row["path_text"] = " → ".join(
                ("표" if i == 0 else "안쪽 표") + f" {s.row + 1}행 {s.col + 1}열 칸"
                + (" (병합된 칸)" if s.row_span * s.col_span > 1 else "")
                for i, s in enumerate(o.path)) or "본문 (표 밖)"
            refs = list(self.doc.containers.get(o.container, []))
            if o.path and o.path[-1].host_container:
                refs += self.doc.containers.get(o.path[-1].host_container, [])
            pick = []
            for r in refs[:400]:
                p = self.doc.paragraphs[r]
                if p.text:
                    pick.append({"ref": r, "text": p.text, "kind": classify_text(p.text)})
            row["pick_list"] = pick
            row["group_key"] = o.container
        payload = {"source": str(self.src), "source_sha256": self.doc.source_sha256,
                   "summary": summarize(links), "renderer_error": self.renderer_error,
                   "note": getattr(self, "note", None),
                   "links": rows}
        f = self.out / "figures.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        tmp.replace(f)
        return f

    def handle(self, cmd: dict) -> dict:
        try:
            c = cmd.get("cmd")
            if c == "open":
                self._open(Path(cmd["src"]), Path(cmd["out"]))
            elif c == "rerun":
                self._open(self.src, self.out)
            elif c == "decide":
                a, ids, refs = cmd["action"], cmd.get("occ_ids", []), cmd.get("para_refs", [])
                if a == "undo":
                    self.store.undo()
                elif a == "share":
                    self.store.share(ids, refs)
                elif a == "text":
                    for oid in ids:
                        self.store.set_text(oid, cmd.get("text", ""), cmd.get("origin", "typed"))
                else:
                    for oid in ids:
                        {"link": lambda o: self.store.link(o, refs),
                         "unlink": self.store.unlink, "none": self.store.mark_none,
                         "undecided": self.store.mark_undecided}[a](oid)
                self.store.save()
            else:
                return {"ok": False, "error": f"unknown cmd {c}"}
            return {"ok": True, "file": str(self._write())}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "trace": traceback.format_exc()[-800:]}


def serve(decisions_root: Path, reply_to=None) -> int:
    """reply_to: stream on the real stdout. desktop_worker passes one after moving fd 1 to stderr,
    so native libraries cannot corrupt the reply stream either."""
    svc = FigureService(decisions_root)
    if reply_to is None:
        reply_to = sys.stdout
    sys.stdout = sys.stderr  # stray library prints must never corrupt the reply stream
    while True:
        line = sys.stdin.readline()  # readline: answer each line without waiting for EOF
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        reply = svc.handle(json.loads(line))
        reply_to.write(json.dumps(reply, ensure_ascii=False) + "\n")
        reply_to.flush()
    return 0
