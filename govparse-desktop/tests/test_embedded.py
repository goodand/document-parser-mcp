import zipfile

from conftest import synthetic_ir
from gpdesk.embedded import bin_id_from_name, collect_picture_uris, extract_hwpx_bindata


def test_bin_id_mapping():
    assert bin_id_from_name("BIN004F.jpg") == 79
    assert bin_id_from_name("image79.png") == 79
    assert bin_id_from_name("readme") is None


def test_hwpx_bindata(tmp_path):
    p = tmp_path / "a.hwpx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("BinData/image1.png", b"\x89PNGdata")
    assert extract_hwpx_bindata(p) == {1: ("image1.png", b"\x89PNGdata")}


def test_picture_uris():
    assert collect_picture_uris(synthetic_ir()) == ["bin://1"]
