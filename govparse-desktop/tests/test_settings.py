"""Settings store (per-OS location) and OCR engine selection."""

import json
from pathlib import Path

import pytest

from gpdesk import ocr_engines, settings


def test_settings_location_per_os(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(settings.sys, "platform", "win32")
    assert settings.settings_path() == tmp_path / "local" / "GovParse Desktop" / "settings.json"
    monkeypatch.setattr(settings.sys, "platform", "darwin")
    monkeypatch.setattr(settings.Path, "home", lambda: tmp_path / "home")
    assert settings.settings_path() == tmp_path / "home" / "Library" / "Application Support" / "GovParse Desktop" / "settings.json"


def test_defaults_save_and_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "s" / "settings.json")
    assert settings.load()["ocr_engine"] == "rapidocr"          # default when no file
    settings.update(ocr_engine="windows")
    assert json.loads((tmp_path / "s" / "settings.json").read_text("utf-8"))["ocr_engine"] == "windows"
    assert settings.load()["ocr_engine"] == "windows"


def test_unknown_engine_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    with pytest.raises(ValueError):
        settings.update(ocr_engine="does-not-exist")


def test_broken_settings_file_falls_back_to_defaults(monkeypatch, tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("{not json", "utf-8")
    monkeypatch.setattr(settings, "settings_path", lambda: f)
    assert settings.load()["ocr_engine"] == "rapidocr"


def test_engine_list_shape_and_os_rules(monkeypatch):
    monkeypatch.setattr(ocr_engines.sys, "platform", "darwin")
    engines = {e["id"]: e for e in ocr_engines.list_engines()}
    assert set(engines) >= {"rapidocr", "windows"}
    for e in engines.values():
        assert {"id", "label", "description", "available", "reason"} <= set(e)
    assert engines["windows"]["available"] is False and "Windows" in engines["windows"]["reason"]


def test_ocr_runs_the_chosen_engine(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setitem(ocr_engines.RUNNERS, "rapidocr", lambda png: calls.append(("rapidocr", png)) or {"status": "ok", "texts": ["a"]})
    monkeypatch.setitem(ocr_engines.RUNNERS, "windows", lambda png: calls.append(("windows", png)) or {"status": "ok", "texts": ["b"]})
    monkeypatch.setattr(ocr_engines, "is_available", lambda engine_id: (True, ""))
    png = tmp_path / "x.png"
    png.write_bytes(b"")
    assert ocr_engines.run(png)["engine"] == "rapidocr"
    settings.update(ocr_engine="windows")
    r = ocr_engines.run(png)
    assert r["engine"] == "windows" and r["texts"] == ["b"] and calls[-1][0] == "windows"


def test_unavailable_engine_reports_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(ocr_engines, "is_available", lambda engine_id: (False, "모델 없음"))
    r = ocr_engines.run(tmp_path / "x.png")
    assert r["status"] == "not_available" and "모델 없음" in r["error"] and r["engine"] == "rapidocr"


def test_worker_settings_modes(monkeypatch, tmp_path):
    from gpdesk import desktop_worker as dw

    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(ocr_engines, "is_available", lambda engine_id: (engine_id == "rapidocr", "" if engine_id == "rapidocr" else "없음"))
    got = dw._dispatch(["settings"])
    assert got["settings"]["ocr_engine"] == "rapidocr" and {e["id"] for e in got["engines"]} >= {"rapidocr", "windows"}
    got = dw._dispatch(["settings-set", "ocr_engine", "windows"])
    assert got["settings"]["ocr_engine"] == "windows"


def test_worker_ocr_goes_through_engine_choice(monkeypatch, tmp_path):
    from gpdesk import desktop_worker as dw
    import pymupdf

    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    seen = {}
    monkeypatch.setattr(ocr_engines, "run", lambda png, **kw: seen.setdefault("png", png) and {"status": "ok", "texts": ["x"], "engine": "rapidocr"})
    png = tmp_path / "a.png"
    pymupdf.open().new_page(width=50, height=20).get_pixmap().save(str(png))
    r = dw.ocr(png)
    assert r["engine"] == "rapidocr" and seen["png"] == png  # RapidOCR: the engine preprocesses, so it gets the original
