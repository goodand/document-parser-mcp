"""Figure occurrences and caption linking against the synthetic fixture's ground truth."""

import hashlib
import shutil
import zipfile
from pathlib import Path

import pytest

from gpdesk.figures import DecisionStore, classify_text, extract, link_figures, summarize

FIX = Path(__file__).resolve().parents[1] / "samples" / "synthetic-figures.hwpx"

# ground truth: (asset ref, cell path as (row, col, row_span, col_span) per table level)
TRUTH = [
    ("image1", [(1, 0, 2, 2), (0, 0, 1, 1)]),
    ("image2", [(1, 0, 2, 2), (1, 0, 1, 1)]),
    ("image3", [(1, 0, 2, 2), (1, 0, 1, 1)]),
    ("image2", [(1, 0, 2, 2), (1, 1, 1, 1)]),
    ("image1", []),
    ("image4", [(0, 0, 1, 1)]),
]
HUMAN = {0: "explicit", 1: "shared", 2: "shared", 3: "none", 4: "none", 5: "label_C"}


@pytest.fixture(scope="module")
def doc():
    return extract(FIX)


@pytest.fixture(scope="module")
def occ(doc):
    return doc.occurrences


def test_occurrences_match_truth(occ):
    assert [(o.asset_ref, [(c.row, c.col, c.row_span, c.col_span) for c in o.path]) for o in occ] == TRUTH
    assert len({o.occ_id for o in occ}) == 6
    assert occ[1].order_in_cell == 0 and occ[2].order_in_cell == 1


def test_assets_vs_occurrences(occ):
    hashes = {o.asset_ref: o.asset_sha256 for o in occ}
    assert len(hashes) == 4, "one asset per file, reuse kept as separate occurrences"
    with zipfile.ZipFile(FIX) as z:
        assert hashes["image2"] == hashlib.sha256(z.read("BinData/image2.png")).hexdigest()


def test_classify_text():
    assert classify_text("그림입니다.") == "figure_marker"
    assert classify_text("원본 그림의 이름: a.png") == "figure_marker"
    assert classify_text("photo_0042.png") == "file_name"
    assert classify_text("<그림 B> 두 그림의 공통 설명") == "numbered_label"
    assert classify_text("[그림 3] 로드맵") == "numbered_label"
    assert classify_text("< 장기추적 자료의 양 >") == "bracket_title"
    assert classify_text("《 시스템 구성도 》") == "bracket_title"
    assert classify_text("") == "empty"
    assert classify_text("사업 목적은 다음과 같다.") == "nearby_text"


def test_auto_links_only_explicit(doc):
    links = link_figures(doc, DecisionStore.memory("x" * 64))
    st = [l.status for l in links]
    assert st[0] == "auto_explicit" and links[0].caption_text == "<그림 A> 사업 구성도"
    assert all(s == "needs_review" for s in st[1:])
    # suggested, never confirmed: numbered label below the pair
    assert links[1].candidates[0].text == "<그림 B> 두 그림의 공통 설명"
    assert links[1].candidates[0].kind == "numbered_label"
    # markers and file names are shown as excluded, not proposed as captions
    kinds4 = {c.text: c.kind for c in links[3].candidates}
    assert kinds4["그림입니다."] == "figure_marker" and kinds4["photo_0042.png"] == "file_name"
    assert all(not c.proposed for c in links[3].candidates if c.kind in ("figure_marker", "file_name"))
    # unrelated sentence in the neighbour cell never becomes a candidate
    assert all(c.text != "이십오 개" for l in links for c in l.candidates)


def test_wrong_auto_and_unresolved_counts(doc):
    links = link_figures(doc, DecisionStore.memory("x" * 64))
    wrong = sum(1 for i, l in enumerate(links) if l.status.startswith("auto") and HUMAN[i] != "explicit")
    unresolved = sum(1 for l in links if l.status == "needs_review")
    assert wrong == 0 and unresolved == 5


def test_decisions_share_none_undo_and_persist(doc, occ, tmp_path):
    sha = hashlib.sha256(FIX.read_bytes()).hexdigest()
    store = DecisionStore(tmp_path / "d", sha)
    cap = link_figures(doc, store)[1].candidates[0].para_ref
    store.share([occ[1].occ_id, occ[2].occ_id], [cap])
    store.mark_none(occ[3].occ_id)
    store.link(occ[4].occ_id, [cap])
    store.undo()  # reverts the last link only
    store.save()
    again = DecisionStore(tmp_path / "d", sha)  # reopen / re-extract
    links = link_figures(extract(FIX), again)
    assert links[1].status == links[2].status == "user_shared"
    assert links[1].group == links[2].group and links[1].caption_text == "<그림 B> 두 그림의 공통 설명"
    assert links[3].status == "user_no_caption"
    assert links[4].status == "needs_review"
    s = summarize(links)
    assert s == {"total": 6, "auto_explicit": 1, "user_confirmed": 3, "needs_review": 2,
                 "location_unverified": 6}


def test_changed_source_does_not_reuse_decisions(doc, occ, tmp_path):
    sha = hashlib.sha256(FIX.read_bytes()).hexdigest()
    store = DecisionStore(tmp_path / "d", sha)
    store.mark_none(occ[3].occ_id)
    store.save()
    other = DecisionStore(tmp_path / "d", "0" * 64)
    assert link_figures(doc, other)[3].status == "needs_review"


def test_user_can_pick_paragraph_outside_candidates(doc, occ, tmp_path):
    store = DecisionStore.memory(doc.source_sha256)
    far = [p for p in doc.paragraphs.values() if p.text == "끝"][0].para_ref
    store.link(occ[4].occ_id, [far])
    l = link_figures(doc, store)[4]
    assert l.status == "user_linked" and l.caption_text == "끝"


def test_source_never_modified(tmp_path):
    src = tmp_path / "f.hwpx"
    shutil.copy2(FIX, src)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    extract(src)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before


def test_frame_table_picture_looks_outside_the_frame(doc):
    """A picture alone in a 1x1 frame table: candidates come from around the frame."""
    l = link_figures(doc, DecisionStore.memory(doc.source_sha256))[5]
    assert l.status == "needs_review"
    top = l.candidates[0]
    assert top.text == "<그림 C> 틀 표 속 그림" and top.proposed and top.position == "before_frame"


def test_decision_file_rejects_other_schema_or_bad_entries(doc, occ, tmp_path):
    """Pattern from human_review_app_v1/capture_server.py: schema-versioned, validated decision file."""
    import json

    store = DecisionStore(tmp_path / "d", doc.source_sha256)
    store.mark_none(occ[3].occ_id)
    store.save()
    data = json.loads(store.path.read_text("utf-8"))
    assert data["schema_version"] == 1
    data["decisions"]["bogus"] = {"action": "explode"}
    store.path.write_text(json.dumps(data), "utf-8")
    again = DecisionStore(tmp_path / "d", doc.source_sha256)
    assert "bogus" not in again.decisions and occ[3].occ_id in again.decisions
    data["schema_version"] = 99
    store.path.write_text(json.dumps(data), "utf-8")
    assert DecisionStore(tmp_path / "d", doc.source_sha256).decisions == {}
