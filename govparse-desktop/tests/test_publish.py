import hashlib

import pytest

from gpdesk.publish import PublishError, Stager, check_relative_refs, unique_target


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_block_same_path_as_source(tmp_path):
    src = tmp_path / "a.hwp"
    src.write_bytes(b"x")
    with pytest.raises(PublishError):
        Stager(src, src)
    with pytest.raises(PublishError):
        Stager(src, tmp_path / "A.HWP")


def test_unique_target_suggests_new_name(tmp_path):
    (tmp_path / "out").mkdir()
    assert unique_target(tmp_path / "out").name == "out (2)"
    assert unique_target(tmp_path / "new").name == "new"
    (tmp_path / "r.hwpx").write_bytes(b"")
    assert unique_target(tmp_path / "r.hwpx").name == "r (2).hwpx"


def test_existing_target_not_overwritten_and_abort(tmp_path):
    src = tmp_path / "a.hwp"
    src.write_bytes(b"orig")
    before = sha(src)
    prev = tmp_path / "result"
    prev.mkdir()
    (prev / "doc.json").write_text("old")
    st = Stager(src, prev)
    assert st.final != prev and st.staging.parent == prev.parent
    (st.staging / "doc.json").write_text("new")
    st.abort()
    assert not st.staging.exists() and (prev / "doc.json").read_text() == "old"
    assert sha(src) == before


def test_failure_during_write_keeps_previous(tmp_path):
    src = tmp_path / "a.hwp"
    src.write_bytes(b"orig")
    prev = tmp_path / "r"
    prev.mkdir()
    (prev / "f").write_text("old")
    st = Stager(src, tmp_path / "r2")
    with pytest.raises(OSError):
        with st:
            (st.staging / "x").write_text("partial")
            raise OSError(28, "No space left on device")
    assert not (tmp_path / "r2").exists() and not st.staging.exists()
    assert (prev / "f").read_text() == "old"


def test_relative_refs_checked(tmp_path):
    d = tmp_path / "s"
    (d / "assets").mkdir(parents=True)
    (d / "assets" / "a.png").write_bytes(b"png")
    assert check_relative_refs(d, ["assets/a.png"]) == []
    bad = ["assets/missing.png", "../escape.png", "C:/abs.png"]
    assert check_relative_refs(d, bad) == bad


def test_commit_publishes(tmp_path):
    src = tmp_path / "a.hwp"
    src.write_bytes(b"o")
    st = Stager(src, tmp_path / "out")
    with st:
        (st.staging / "doc.json").write_text("{}")
    assert (tmp_path / "out" / "doc.json").exists() and st.committed


def test_single_file_target(tmp_path):
    src = tmp_path / "a.hwp"
    src.write_bytes(b"o")
    st = Stager(src, tmp_path / "out.hwpx", single_file=True)
    with st:
        st.staged_file.write_bytes(b"PK")
    assert (tmp_path / "out.hwpx").read_bytes() == b"PK"


def test_concurrent_exports_same_name_never_overwrite(tmp_path):
    """TLC trace (formal/job-routing): StartExport(b,t1) StartExport(a,t1) Commit(a) Commit(b)."""
    src = tmp_path / "a.hwp"
    src.write_bytes(b"o")
    b = Stager(src, tmp_path / "r.hwpx", single_file=True)
    a = Stager(src, tmp_path / "r.hwpx", single_file=True)
    a.staged_file.write_bytes(b"A")
    b.staged_file.write_bytes(b"B")
    pa, pb = a.commit(), b.commit()
    assert pa != pb and pa.read_bytes() == b"A" and pb.read_bytes() == b"B"


def test_concurrent_folder_exports_same_name(tmp_path):
    src = tmp_path / "a.hwp"
    src.write_bytes(b"o")
    b = Stager(src, tmp_path / "out")
    a = Stager(src, tmp_path / "out")
    (a.staging / "f").write_text("A")
    (b.staging / "f").write_text("B")
    pa, pb = a.commit(), b.commit()
    assert pa != pb and (pa / "f").read_text() == "A" and (pb / "f").read_text() == "B"
