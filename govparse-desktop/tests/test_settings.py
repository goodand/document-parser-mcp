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


def test_rapidocr_runs_from_the_app_when_bundled(monkeypatch, tmp_path):
    """Another PC has no govparse engine: the copy shipped inside the app must be used, not looked up."""
    import sys

    models = tmp_path / "models" / "rapidocr"
    models.mkdir(parents=True)
    monkeypatch.setattr(ocr_engines, "bundled_models", lambda: models)
    monkeypatch.setattr(ocr_engines, "_bundled_code", lambda: True)

    def no_engine():
        raise RuntimeError("no engine on this PC")

    monkeypatch.setattr("gpdesk.config.load", no_engine)
    python, model_dir, env = ocr_engines.rapidocr_runtime()
    assert Path(python) == Path(sys.executable) and model_dir == models and "PYTHONPATH" not in env
    assert ocr_engines.is_available("rapidocr") == (True, "")


def test_rapidocr_uses_the_installed_engine_when_not_bundled(monkeypatch, tmp_path):
    from gpdesk.config import EngineConfig

    monkeypatch.setattr(ocr_engines, "bundled_models", lambda: None)
    src = tmp_path / "src"
    src.mkdir()
    cfg = EngineConfig(tmp_path / "python.exe", src, tmp_path / "m", tmp_path / "d")
    monkeypatch.setattr("gpdesk.config.load", lambda: cfg)
    python, model_dir, env = ocr_engines.rapidocr_runtime()
    assert Path(python) == cfg.engine_python and model_dir == cfg.ocr_models and env["PYTHONPATH"] == str(src)


def test_rapidocr_unavailable_without_bundle_and_engine(monkeypatch):
    monkeypatch.setattr(ocr_engines, "bundled_models", lambda: None)

    def no_engine():
        raise RuntimeError("no engine on this PC")

    monkeypatch.setattr("gpdesk.config.load", no_engine)
    ok, why = ocr_engines.is_available("rapidocr")
    assert ok is False and why


# ---- 미리보기 방식 (rhwp | hancom) ----------------------------------------------

def test_preview_defaults_to_rhwp_and_rejects_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    assert settings.load()["preview"] == "rhwp"
    settings.update(preview="hancom")
    assert settings.load()["preview"] == "hancom"
    with pytest.raises(ValueError):
        settings.update(preview="pdfjs")


def test_preview_choices_mark_hancom_unavailable_without_hancom(monkeypatch):
    from gpdesk import desktop_worker as w

    monkeypatch.setattr(w, "hancom_installed", lambda: False)
    by_id = {c["id"]: c for c in w.preview_choices()}
    assert by_id["rhwp"]["available"] and not by_id["hancom"]["available"]
    assert "한컴" in by_id["hancom"]["reason"]


class _Doc:
    page_count = 3

    def render_png(self, page, scale, max_pixels):
        return b"rhwp-png"


def _fake_source(monkeypatch, w):
    class S:
        def __init__(self, path):
            self.path, self.doc = Path(path), _Doc()
    monkeypatch.setattr(w, "Source", S)


def _fake_pdf(monkeypatch, w, tmp_path):
    import pymupdf

    pdf = tmp_path / "h.pdf"
    d = pymupdf.open(); d.new_page(); d.new_page(); d.save(str(pdf)); d.close()
    monkeypatch.setattr(w, "hancom_pdf", lambda p: pdf)


@pytest.mark.parametrize("choice,installed,expected", [
    ("rhwp", True, (3, "rhwp")),
    ("hancom", True, (2, "hancom-pdf")),
    ("hancom", False, (3, "rhwp")),          # chosen but Hancom missing: fall back, do not fail
])
def test_preview_follows_setting(monkeypatch, tmp_path, choice, installed, expected):
    from gpdesk import desktop_worker as w

    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    settings.update(preview=choice)
    monkeypatch.setattr(w, "hancom_installed", lambda: installed)
    _fake_source(monkeypatch, w)
    _fake_pdf(monkeypatch, w, tmp_path)
    assert w.preview(tmp_path / "x.hwpx", tmp_path / "out.png", 0) == expected
    assert (tmp_path / "out.png").stat().st_size > 0


def test_worker_settings_mode_lists_preview_choices(monkeypatch, tmp_path):
    from gpdesk import desktop_worker as w

    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    out = w._dispatch(["settings-set", "preview", "hancom"])
    assert out["settings"]["preview"] == "hancom"
    assert [c["id"] for c in out["previews"]] == ["rhwp", "hancom"]
