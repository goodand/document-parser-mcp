import sys
from types import SimpleNamespace

import numpy as np
import pytest

from govparse import image_ocr


def _install_fake_cv2(monkeypatch):
    source = np.zeros((20, 30, 3), dtype=np.uint8)
    calls = []

    def copy_make_border(image, top, bottom, left, right, border_type, value):
        calls.append((top, bottom, left, right, value))
        return np.pad(image, ((top, bottom), (left, right), (0, 0)), constant_values=255)

    def encode(extension, image):
        return True, np.frombuffer(b"padded-png", dtype=np.uint8)

    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        IMREAD_COLOR=1,
        BORDER_CONSTANT=0,
        imdecode=lambda encoded, mode: source,
        copyMakeBorder=copy_make_border,
        imencode=encode,
    ))
    return calls


def test_default_padding_translates_boxes_back_to_original_image(tmp_path, monkeypatch):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"source-png")
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    calls = _install_fake_cv2(monkeypatch)
    received = []

    def run_ocr(image_bytes, configured_model_dir):
        received.append((image_bytes, configured_model_dir))
        return {
            "boxes": [[[65, 66], [75, 66], [75, 76], [65, 76]]],
            "texts": ["사업"],
            "scores": [0.9],
            "runtime": {"engine": "RapidOCR"},
        }

    monkeypatch.setattr(image_ocr, "_run_ocr", run_ocr)

    result = image_ocr.recognize_image(image_path, model_dir=model_dir)

    assert calls == [(60, 60, 60, 60, (255, 255, 255))]
    assert received == [(b"padded-png", model_dir)]
    assert result["padding_px"] == 60
    assert result["image_width"] == 30
    assert result["image_height"] == 20
    assert result["coordinates"] == "original_image_pixels"
    assert result["boxes"] == [[[5.0, 6.0], [15.0, 6.0], [15.0, 16.0], [5.0, 16.0]]]


def test_raw_mode_uses_original_bytes_and_does_not_translate_boxes(tmp_path, monkeypatch):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"source-png")
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    calls = _install_fake_cv2(monkeypatch)
    received = []

    def run_ocr(image_bytes, configured_model_dir):
        received.append(image_bytes)
        return {"boxes": [[[1, 2], [4, 2], [4, 6]]], "texts": [], "scores": [], "runtime": {}}

    monkeypatch.setattr(image_ocr, "_run_ocr", run_ocr)

    result = image_ocr.recognize_image(image_path, raw=True, model_dir=model_dir)

    assert received == [b"source-png"]
    assert calls == []
    assert result["padding_px"] == 0
    assert result["boxes"] == [[[1, 2], [4, 2], [4, 6]]]


@pytest.mark.parametrize("padding", [-1, 257, True])
def test_invalid_padding_is_rejected(tmp_path, padding):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"source-png")
    model_dir = tmp_path / "models"
    model_dir.mkdir()

    with pytest.raises(ValueError, match="padding_px"):
        image_ocr.recognize_image(image_path, padding_px=padding, model_dir=model_dir)


def test_non_image_path_is_rejected(tmp_path):
    text_path = tmp_path / "file.txt"
    text_path.write_text("not an image")

    with pytest.raises(ValueError, match="지원하지 않는 이미지"):
        image_ocr.recognize_image(text_path)


@pytest.mark.parametrize("raw,limit,pad,size", [
    (False, 10, 2, (5, 10)),
    (False, 10, 0, (5, 10)),
    (False, None, 2, (11, 21)),
    (True, 10, 2, (11, 21)),
])
def test_scaled_coordinates_use_actual_rounded_dimensions(tmp_path, monkeypatch, raw, limit, pad, size):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "wide.png"
    path.write_bytes(cv2.imencode(".png", np.zeros((11, 21, 3), np.uint8))[1].tobytes())
    height, width = size
    margin = 0 if raw else pad

    def read(data, model_dir):
        pixels = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert pixels.shape[:2] == (height + 2 * margin, width + 2 * margin)
        return {"boxes": [[[margin + width / 2, margin + height / 2]]]}

    monkeypatch.setattr(image_ocr, "_run_ocr", read)
    result = image_ocr.recognize_image(path, raw=raw, padding_px=pad,
                                       max_side_px=limit, model_dir=tmp_path)
    assert result["boxes"] == [[[10.5, 5.5]]]
