from gpdesk.jobs import JobRegistry


def test_late_completion_of_A_does_not_touch_B(tmp_path):
    a_dir, b_dir = tmp_path / "x", tmp_path / "y"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "same.hwp").write_bytes(b"AAAA")
    (b_dir / "same.hwp").write_bytes(b"BBBB")
    reg = JobRegistry()
    a = reg.open(a_dir / "same.hwp")
    b = reg.open(b_dir / "same.hwp")
    assert a.job_id != b.job_id and a.sha256 != b.sha256
    assert reg.is_current(b.job_id) and not reg.is_current(a.job_id)
    assert reg.accept(a.job_id, a.sha256) is False
    assert reg.accept(b.job_id, b.sha256) is True
    assert reg.get(a.job_id).source == a_dir / "same.hwp"


def test_sha_mismatch_rejected(tmp_path):
    f = tmp_path / "d.hwp"
    f.write_bytes(b"1")
    reg = JobRegistry()
    j = reg.open(f)
    assert reg.accept(j.job_id, "0" * 64) is False
