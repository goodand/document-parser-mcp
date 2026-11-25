"""GUI results must be reproducible with no LLM and no network API."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = [p for p in (ROOT / "gpdesk").rglob("*.py") if p.name != "engine_cli.py"] + [ROOT / "worker.py"] + list(ROOT.glob("*.cs"))
BAD = re.compile(r"^\s*(import|from)\s+(requests|httpx|urllib|http|socket|anthropic|openai|aiohttp|mcp)\b", re.M)


def test_no_network_or_llm_client_in_gui_path():
    hits = [p.name for p in SHIPPED if BAD.search(p.read_text("utf-8"))]
    assert hits == []
    for p in ROOT.glob("*.cs"):
        assert not re.search(r"WebClient|HttpClient|WebRequest", p.read_text("utf-8")), p.name
