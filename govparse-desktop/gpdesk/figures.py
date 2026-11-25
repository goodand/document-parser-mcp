"""Picture occurrences and caption links for HWPX, read straight from the OWPML XML.

Two questions are answered separately:
  where   - occurrence: section, table/cell path (with spans), order inside the cell,
            the paragraph it sits in. One asset (file hash) may have many occurrences.
  caption - which paragraph(s) describe it. Only a caption the document itself
            declares (hp:pic/hp:caption) is linked automatically. Everything else is a
            *candidate* with a kind and a reason, and stays "needs_review" until a person
            decides. Person decisions are stored per source SHA-256, outside the source.

No document names, object ids or counts are hard-coded here.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lxml import etree

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"
_PARSER = etree.XMLParser(huge_tree=True, remove_blank_text=False)

# ------------------------------------------------------------------ text kinds
_IMG_EXT = r"(png|jpe?g|gif|bmp|tiff?|wmf|emf|svg)"
_KINDS = [
    ("figure_marker", re.compile(r"^(그림입니다|원본 그림의 (이름|크기))")),
    ("file_name", re.compile(rf"^\S+\.{_IMG_EXT}$", re.I)),
    ("table_title", re.compile(r"^[<\[(〈【]\s*표\s*[\dA-Za-z-]*\s*[>\])〉】]")),
    ("numbered_label", re.compile(
        r"^[<\[(〈【]?\s*(그림|사진|도표|도|Figure|Fig\.?)\s*[\dA-Za-zⅠ-Ⅻ가-힣.-]{0,4}\s*[>\])〉】.:]", re.I)),
    ("bracket_title", re.compile(r"^[<〈《【\[「].{2,}[>〉》】\]」]$")),
    ("heading", re.compile(r"^(\(\d+\)|\d+\.|[□■○◦ㅇ∘▪◾￭√❍➀-➉①-⑳]|[가-하]\.)\s")),
]
# No caption recommendation: a format-only rule (numbered label / bracketed title) was measured mostly
# wrong on TIPS (section titles in brackets), so nothing is proposed. Kinds remain as plain labels.
PROPOSE: set[str] = set()
REASON = {
    "numbered_label": "그림 번호 표식으로 시작함",
    "bracket_title": "괄호로 감싼 제목 형식",
    "figure_marker": "한글이 넣는 그림 표식 문구(설명 아님)",
    "file_name": "그림 파일 이름(설명 아님)",
    "table_title": "표 제목 표식(그림 설명 아님)",
    "heading": "항목 제목·글머리 문단",
    "nearby_text": "같은 셀의 인접 문장일 뿐 설명 근거 없음",
    "empty": "빈 문단",
}


def classify_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "empty"
    for kind, rx in _KINDS:
        if rx.search(t):
            return kind
    return "nearby_text"


# ------------------------------------------------------------------ model
@dataclass
class CellStep:
    table_id: str
    row: int
    col: int
    row_span: int
    col_span: int
    table_rows: int
    table_cols: int
    host_para: str = ""       # paragraph (outside the table) that anchors the table
    host_container: str = ""


@dataclass
class Para:
    para_ref: str
    text: str
    section: int
    container: str  # id of the subList/section that holds it
    path: list[CellStep]
    role: str = "body"  # body | caption


@dataclass
class Occurrence:
    occ_id: str
    section: int
    pic_id: str
    asset_ref: str
    asset_path: str
    asset_sha256: str | None
    path: list[CellStep]
    container: str
    order_in_cell: int
    para_ref: str
    parent_item: str
    explicit_caption: str | None
    explicit_caption_ref: str | None
    size_hwpunit: tuple[int, int] | None
    in_group: bool


@dataclass
class FigureDoc:
    source: Path
    source_sha256: str
    occurrences: list[Occurrence]
    paragraphs: dict[str, Para]
    containers: dict[str, list[str]]  # container -> ordered para refs
    asset_bytes: dict[str, bytes] = field(repr=False, default_factory=dict)


def _own_text(p) -> str:
    out: list[str] = []

    def scan(el):
        for ch in el:
            tag = ch.tag
            if tag in (HP + "tbl", HP + "pic", HP + "subList", HP + "caption", HP + "container"):
                continue
            if tag == HP + "t":
                out.append("".join(ch.itertext()))
            else:
                scan(ch)

    scan(p)
    return "".join(out).strip()


def _manifest(z: zipfile.ZipFile) -> dict[str, str]:
    try:
        hpf = etree.fromstring(z.read("Contents/content.hpf"), _PARSER)
    except KeyError:
        return {}
    return {i.get("id"): i.get("href") for i in hpf.iter("{*}item") if i.get("id")}


def _parent_item(path: list[CellStep], rows_text) -> str:
    for step in reversed(path):
        title = rows_text(step)
        if title:
            return title
    return ""


def extract(source: Path) -> FigureDoc:
    source = Path(source)
    raw = source.read_bytes()
    if not raw.startswith(b"PK"):
        raise ValueError("그림과 설명 검토는 현재 HWPX에서 지원합니다. HWP는 한글에서 HWPX로 저장 후 여세요.")
    sha = hashlib.sha256(raw).hexdigest()
    occs: list[Occurrence] = []
    paras: dict[str, Para] = {}
    containers: dict[str, list[str]] = {}
    assets: dict[str, bytes] = {}
    full_rows: dict[str, list[tuple[int, str]]] = {}  # table id -> [(row, text)] full-width rows
    with zipfile.ZipFile(source) as z:
        manifest = _manifest(z)
        secs = sorted((n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                      key=lambda n: int(re.findall(r"\d+", n)[-1]))
        for name in secs:
            sec = int(re.findall(r"\d+", name)[-1])
            root = etree.fromstring(z.read(name), _PARSER)
            counters = {"p": 0, "c": 0}
            pic_in_container: dict[str, int] = {}

            def new_para(p, container, path, role="body"):
                ref = f"s{sec}:p{counters['p']}"
                counters["p"] += 1
                paras[ref] = Para(ref, _own_text(p), sec, container, list(path), role)
                if role == "body":
                    containers.setdefault(container, []).append(ref)
                return ref

            def walk_container(el, path):
                counters["c"] += 1
                cid = f"s{sec}:c{counters['c']}"
                for p in el.findall(HP + "p"):
                    ref = new_para(p, cid, path)
                    scan(p, cid, path, ref, False)

            def table(tbl, path, host_cid, host_para):
                tid = tbl.get("id") or f"s{sec}:t{counters['c']}"
                rows, cols = int(tbl.get("rowCnt") or 0), int(tbl.get("colCnt") or 0)
                for tr in tbl.findall(HP + "tr"):
                    for tc in tr.findall(HP + "tc"):
                        a, s = tc.find(HP + "cellAddr"), tc.find(HP + "cellSpan")
                        step = CellStep(tid, int(a.get("rowAddr")), int(a.get("colAddr")),
                                        int(s.get("rowSpan")) if s is not None else 1,
                                        int(s.get("colSpan")) if s is not None else 1, rows, cols,
                                        host_para, host_cid)
                        sub = tc.find(HP + "subList")
                        if step.col_span == cols and cols > 1 and sub is not None:
                            title = " ".join(_own_text(p) for p in sub.findall(HP + "p")).strip()
                            if title:
                                full_rows.setdefault(tid, []).append((step.row, title))
                        if sub is not None:
                            walk_container(sub, path + [step])

            def picture(pic, cid, path, para_ref, in_group):
                img = pic.find(f".//{HC}img")
                ref = img.get("binaryItemIDRef") if img is not None else ""
                href = manifest.get(ref, "")
                data = None
                if href:
                    try:
                        data = assets.setdefault(ref, z.read(href))
                    except KeyError:
                        data = None
                order = pic_in_container.get(cid, 0)
                pic_in_container[cid] = order + 1
                cap_text = cap_ref = None
                cap = pic.find(HP + "caption")
                if cap is not None:
                    cps = cap.findall(f"{HP}subList/{HP}p")
                    refs = [new_para(cp, f"{cid}:cap", path, "caption") for cp in cps]
                    txt = " ".join(paras[r].text for r in refs).strip()
                    if txt:
                        cap_text, cap_ref = txt, refs[0]
                sz = pic.find(HP + "sz")
                occs.append(Occurrence(
                    occ_id=f"s{sec}-pic{pic.get('id') or len(occs)}-{len(occs)}",
                    section=sec, pic_id=pic.get("id") or "", asset_ref=ref, asset_path=href,
                    asset_sha256=hashlib.sha256(data).hexdigest() if data else None,
                    path=list(path), container=cid, order_in_cell=order, para_ref=para_ref,
                    parent_item="", explicit_caption=cap_text, explicit_caption_ref=cap_ref,
                    size_hwpunit=(int(sz.get("width")), int(sz.get("height"))) if sz is not None else None,
                    in_group=in_group))

            def scan(el, cid, path, para_ref, in_group):
                for ch in el:
                    if ch.tag == HP + "tbl":
                        table(ch, path, cid, para_ref)
                    elif ch.tag == HP + "pic":
                        picture(ch, cid, path, para_ref, in_group)
                    elif ch.tag == HP + "container":
                        scan(ch, cid, path, para_ref, True)
                    elif ch.tag in (HP + "subList", HP + "drawText"):
                        sub = ch if ch.tag == HP + "subList" else ch.find(HP + "subList")
                        if sub is not None:
                            walk_container(sub, path)
                    elif ch.tag != HP + "caption":
                        scan(ch, cid, path, para_ref, in_group)

            walk_container(root, [])

    def rows_text(step: CellStep) -> str:
        above = [t for r, t in full_rows.get(step.table_id, []) if r < step.row]
        return above[-1] if above else ""

    for o in occs:
        o.parent_item = _parent_item(o.path, rows_text)
    return FigureDoc(source, sha, occs, paras, containers, assets)


# ------------------------------------------------------------------ decisions
SCHEMA_VERSION = 1
_ACTIONS = {"link", "share", "unlink", "none", "undecided", "text"}
_TEXT_ORIGINS = {"ocr": "그림 OCR 결과로 지정", "typed": "직접 입력"}


def _valid_decision(v) -> bool:
    if not isinstance(v, dict) or v.get("action") not in _ACTIONS:
        return False
    if v["action"] == "text":
        return isinstance(v.get("text"), str) and bool(v["text"].strip()) and v.get("origin") in _TEXT_ORIGINS
    if v["action"] in ("link", "share"):
        refs = v.get("para_refs")
        return isinstance(refs, list) and bool(refs) and all(isinstance(r, str) for r in refs)
    return True


class DecisionStore:
    """Person decisions keyed by source SHA-256; never written into the source."""

    def __init__(self, root: Path | None, source_sha256: str) -> None:
        self.root, self.sha = (Path(root) if root else None), source_sha256
        self.decisions: dict[str, dict] = {}
        self.history: list[dict[str, dict | None]] = []
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except ValueError:
                data = {}
            if data.get("schema_version") == SCHEMA_VERSION and data.get("source_sha256") == source_sha256:
                self.decisions = {k: v for k, v in (data.get("decisions") or {}).items() if _valid_decision(v)}
                self.history = data.get("history", [])

    @classmethod
    def memory(cls, sha: str) -> "DecisionStore":
        return cls(None, sha)

    @property
    def path(self) -> Path | None:
        return self.root / f"{self.sha}.json" if self.root else None

    def _apply(self, changes: dict[str, dict | None]) -> None:
        self.history.append({k: self.decisions.get(k) for k in changes})
        for k, v in changes.items():
            if v is None:
                self.decisions.pop(k, None)
            else:
                self.decisions[k] = {**v, "by": "user", "at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def link(self, occ_id: str, para_refs: list[str]) -> None:
        self._apply({occ_id: {"action": "link", "para_refs": list(para_refs)}})

    def share(self, occ_ids: list[str], para_refs: list[str]) -> None:
        group = "g-" + hashlib.sha1("|".join(sorted(occ_ids)).encode()).hexdigest()[:8]
        self._apply({o: {"action": "share", "group": group, "para_refs": list(para_refs)}
                     for o in occ_ids})

    def set_text(self, occ_id: str, text: str, origin: str) -> None:
        """Caption written by a person (typed) or taken from the picture's OCR text (ocr)."""
        text = (text or "").strip()
        if not text:
            raise ValueError("설명 내용이 비어 있습니다.")
        if origin not in _TEXT_ORIGINS:
            raise ValueError(f"알 수 없는 설명 출처: {origin}")
        self._apply({occ_id: {"action": "text", "text": text, "origin": origin}})

    def unlink(self, occ_id: str) -> None:
        self._apply({occ_id: {"action": "unlink"}})

    def mark_none(self, occ_id: str) -> None:
        self._apply({occ_id: {"action": "none"}})

    def mark_undecided(self, occ_id: str, note: str = "") -> None:
        self._apply({occ_id: {"action": "undecided", "note": note}})

    def undo(self) -> bool:
        if not self.history:
            return False
        for k, v in self.history.pop().items():
            if v is None:
                self.decisions.pop(k, None)
            else:
                self.decisions[k] = v
        return True

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "source_sha256": self.sha, "decisions": self.decisions,
                                   "history": self.history[-200:]}, ensure_ascii=False, indent=1),
                       "utf-8")
        tmp.replace(self.path)


# ------------------------------------------------------------------ linking
@dataclass
class Candidate:
    para_ref: str
    text: str
    kind: str
    position: str  # before | after | same | before_frame | after_frame | same_frame
    distance: int
    proposed: bool
    reason: str


@dataclass
class FigureLink:
    occ: Occurrence
    status: str  # auto_explicit|user_linked|user_shared|user_no_caption|user_undecided|needs_review
    caption_refs: list[str]
    caption_text: str
    evidence: str
    group: str | None
    candidates: list[Candidate]
    before: list[str]
    after: list[str]
    location: dict = field(default_factory=lambda: {"verified": False, "note": "원본 화면 위치 미확인"})


def _candidates(doc: FigureDoc, o: Occurrence, window: int = 2) -> tuple[list[Candidate], list[str], list[str]]:
    refs = doc.containers.get(o.container, [])
    i = refs.index(o.para_ref) if o.para_ref in refs else -1
    out: list[Candidate] = []

    def add(ref, pos, dist):
        txt = doc.paragraphs[ref].text
        kind = classify_text(txt)
        if kind == "empty":
            return False
        out.append(Candidate(ref, txt, kind, pos, dist, kind in PROPOSE, REASON[kind]))
        return True

    before, after = [], []
    if i >= 0:
        add(o.para_ref, "same", 0)
        n = 0
        for j in range(i - 1, -1, -1):
            if add(refs[j], "before", i - j):
                before.insert(0, doc.paragraphs[refs[j]].text)
                n += 1
                if n == window:
                    break
        n = 0
        for j in range(i + 1, len(refs)):
            if add(refs[j], "after", j - i):
                after.append(doc.paragraphs[refs[j]].text)
                n += 1
                if n == window:
                    break
    if not out and o.path and o.path[-1].host_container:
        # picture alone in a (frame) table cell: look around the table in its host container
        step = o.path[-1]
        hrefs = doc.containers.get(step.host_container, [])
        if step.host_para in hrefs:
            k = hrefs.index(step.host_para)
            add(step.host_para, "same_frame", 0)
            n = 0
            for j in range(k - 1, -1, -1):
                if add(hrefs[j], "before_frame", k - j):
                    before.insert(0, doc.paragraphs[hrefs[j]].text)
                    n += 1
                    if n == window:
                        break
            n = 0
            for j in range(k + 1, len(hrefs)):
                if add(hrefs[j], "after_frame", j - k):
                    after.append(doc.paragraphs[hrefs[j]].text)
                    n += 1
                    if n == window:
                        break
    out.sort(key=lambda c: c.distance)  # nearest first; no ranking by text format
    return out, before, after


def link_figures(doc: FigureDoc, store: DecisionStore) -> list[FigureLink]:
    links = []
    for o in doc.occurrences:
        cands, before, after = _candidates(doc, o)
        d = store.decisions.get(o.occ_id) if store.sha == doc.source_sha256 else None
        group = None
        if d and d["action"] in ("link", "share"):
            refs = [r for r in d["para_refs"] if r in doc.paragraphs]
            status = "user_shared" if d["action"] == "share" else "user_linked"
            text = " / ".join(doc.paragraphs[r].text for r in refs)
            evidence, group = "사용자 확인", d.get("group")
        elif d and d["action"] == "text":
            status, refs, text, evidence = "user_linked", [], d["text"], _TEXT_ORIGINS[d["origin"]]
        elif d and d["action"] == "none":
            status, refs, text, evidence = "user_no_caption", [], "", "사용자 확인: 설명 없음"
        elif d and d["action"] == "undecided":
            status, refs, text, evidence = "user_undecided", [], "", "사용자: 결정 불가 " + d.get("note", "")
        elif o.explicit_caption and not (d and d["action"] == "unlink"):
            status, refs, text = "auto_explicit", [o.explicit_caption_ref], o.explicit_caption
            evidence = "원문 캡션(hp:caption)"
        else:
            status, refs, text = "needs_review", [], ""
            evidence = "사용자가 연결 해제" if d else (
                "명시 캡션 없음")
        links.append(FigureLink(o, status, refs, text, evidence, group, cands, before, after))
    return links


def summarize(links: list[FigureLink]) -> dict:
    return {
        "total": len(links),
        "auto_explicit": sum(l.status == "auto_explicit" for l in links),
        "user_confirmed": sum(l.status in ("user_linked", "user_shared", "user_no_caption") for l in links),
        "needs_review": sum(l.status in ("needs_review", "user_undecided") for l in links),
        "location_unverified": sum(not l.location.get("verified") for l in links),
    }


def to_json(links: list[FigureLink]) -> list[dict]:
    out = []
    for l in links:
        o = asdict(l.occ)
        out.append({"occurrence": o, "status": l.status, "caption_refs": l.caption_refs,
                    "caption_text": l.caption_text, "evidence": l.evidence, "group": l.group,
                    "candidates": [asdict(c) for c in l.candidates], "before": l.before,
                    "after": l.after, "location": l.location})
    return out
