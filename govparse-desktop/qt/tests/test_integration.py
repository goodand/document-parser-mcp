"""End to end with the real worker and the synthetic samples (no fakes). Runs on every OS.

Hancom-dependent steps are excluded; they are covered by the Windows live tests in tests/.
"""

import json
from pathlib import Path

import pytest

from conftest import SAMPLES, FakeDialogs, FakePlatform, find, text

pytestmark = pytest.mark.integration


@pytest.fixture
def real(qtbot, user_dirs, tmp_path):
    from gpqt.app import MainWindow
    from gpqt.worker import WorkerClient

    dialogs = FakeDialogs()
    w = MainWindow(worker=WorkerClient(), dialogs=dialogs, platform=FakePlatform())
    qtbot.addWidget(w)
    w.show()
    return w, dialogs


def test_open_synthetic_figures(real, qtbot):
    w, _ = real
    w.open_document(str(SAMPLES / "synthetic-figures.hwpx"))
    qtbot.waitUntil(lambda: text(w, "stStructure").startswith("구조 읽기 완료"), timeout=60000)
    assert "표 3 (중첩 1)" in text(w, "stStructure") and "그림 6" in text(w, "stStructure")
    qtbot.waitUntil(lambda: text(w, "lblPage") == "1 / 1", timeout=60000)


def test_json_export_then_result_view(real, qtbot, tmp_path):
    w, dialogs = real
    w.open_document(str(SAMPLES / "synthetic-figures.hwpx"))
    qtbot.waitUntil(lambda: text(w, "lblPage") == "1 / 1", timeout=60000)
    dialogs.save_target = str(tmp_path / "합성-json")
    w.export()
    qtbot.waitUntil(lambda: "저장 완료" in text(w, "statusText"), timeout=120000)
    out = tmp_path / "합성-json"
    meta = json.loads((out / "document.json").read_text("utf-8"))["govparse_desktop"]
    assert meta["table_images"]["tables"] == 3
    assert meta["table_images"]["source_page_tables"] == 3
    assert (tmp_path / "합성-json.zip").is_file()
    w.switch_tab(result=True)
    lst = find(w, "assetList")
    qtbot.waitUntil(lambda: lst.topLevelItemCount() == 7, timeout=60000)   # 4 pictures + 3 tables
    # the zip alone is enough: unzip elsewhere, remove the original folder, reopen, see a table PNG (engine review 6)
    import shutil
    import zipfile

    with zipfile.ZipFile(tmp_path / "합성-json.zip") as z:
        z.extractall(tmp_path / "받은 사람")
    shutil.rmtree(out)
    w.load_result(str(tmp_path / "받은 사람" / "합성-json" / "document.json"))
    qtbot.waitUntil(lambda: "저장 후 변경 없음" in text(w, "resNotes"), timeout=60000)
    _select_first_table_and_check_pixels(w, qtbot, kind="원본 쪽")


def test_moved_result_reopens(real, qtbot, tmp_path):
    import shutil

    w, dialogs = real
    w.open_document(str(SAMPLES / "synthetic-35tables.hwpx"))
    qtbot.waitUntil(lambda: text(w, "lblPage") != "– / –", timeout=60000)
    dialogs.save_target = str(tmp_path / "a" / "r")
    (tmp_path / "a").mkdir()
    w.export()
    qtbot.waitUntil(lambda: "저장 완료" in text(w, "statusText"), timeout=120000)
    moved = tmp_path / "옮긴 곳 b" / "r"
    shutil.copytree(tmp_path / "a" / "r", moved)
    shutil.rmtree(tmp_path / "a")      # the old folder is gone, so nothing can still be read from it (engine review 5)
    w.load_result(str(moved / "document.json"))
    qtbot.waitUntil(lambda: "저장 후 변경 없음" in text(w, "resNotes"), timeout=60000)
    assert find(w, "assetList").topLevelItemCount() == 35
    _select_first_table_and_check_pixels(w, qtbot)


def _select_first_table_and_check_pixels(w, qtbot, kind="표 이미지"):
    """The picture shown for a table row is the table PNG of this folder: not blank, read from the shown path."""
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    lst = find(w, "assetList")
    row = next(lst.topLevelItem(i) for i in range(lst.topLevelItemCount()) if lst.topLevelItem(i).text(1) == kind)
    lst.setCurrentItem(row)
    f = Path(row.data(0, Qt.UserRole)["file"])
    assert f.is_file() and ("받은 사람" in str(f) or "옮긴 곳 b" in str(f))
    shown = find(w, "assetPreview").pixmap().toImage()
    assert not shown.isNull() and QImage(str(f)).size() == w.asset_preview_source_size()
    assert len({shown.pixel(x, y) for x in range(0, shown.width(), 7) for y in range(0, shown.height(), 7)}) > 1


