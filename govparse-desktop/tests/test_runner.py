import sys
import threading
import time

import pytest

from gpdesk.runner import CancelToken, Cancelled, RunTimeout, run, new_hancom_pids


def test_run_ok():
    r = run([sys.executable, "-c", "print('hi')"], timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == "hi"


def test_timeout_kills_tree():
    t0 = time.time()
    with pytest.raises(RunTimeout):
        run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
    assert time.time() - t0 < 15


def test_cancel():
    tok = CancelToken()
    threading.Timer(0.5, tok.cancel).start()
    with pytest.raises(Cancelled):
        run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=60, token=tok)


def test_new_hancom_pids_only_ours():
    before = {(10, 1.0, False), (11, 2.0, False)}  # user's windows
    # 12: our automation server; 13: user opened a document meanwhile
    after = before | {(12, 5.0, True), (13, 6.0, False)}
    assert new_hancom_pids(before, after) == {12}
    # pid reused by a different start time counts as new
    assert new_hancom_pids({(10, 1.0, True)}, {(10, 9.0, True)}) == {10}
    # a pre-existing automation server (another tool) is never ours
    assert new_hancom_pids({(14, 1.0, True)}, {(14, 1.0, True)}) == set()


def test_unresolved_ownership_kills_nothing():
    """Rule taken from govparse-claude-lab/scripts/hancom_export.py: no snapshot, no termination."""
    after = {(12, 5.0, True)}
    assert new_hancom_pids(None, after) == set()
    assert new_hancom_pids({(1, 1.0, True)}, None) == set()
