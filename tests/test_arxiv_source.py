"""arXiv as a first-class planner source.

Free Atom API, no SerpApi credits, every hit fetchable full text. Parsing
and failure behaviour are pinned; no network is used.
"""
import pytest

from backend.research import searcher

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2604.00001v2</id>
    <title>Predicting Aqueous
    Solubility with Graph Networks</title>
    <summary>  We predict solubility from
    molecular graphs.  </summary>
    <published>2026-04-01T00:00:00Z</published>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <link href="http://arxiv.org/abs/2604.00001v2" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/2604.00001v2" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2604.00002v1</id>
    <title>Second Paper</title>
    <summary>Another abstract.</summary>
    <published>2026-03-15T00:00:00Z</published>
    <author><name>Grace Hopper</name></author>
    <link href="http://arxiv.org/abs/2604.00002v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setenv("SEARCH_CACHE_DIR", "")


def test_arxiv_search_parses_atom_feed(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        captured["url"] = url
        captured["params"] = params

        class Resp:
            text = ATOM

            def raise_for_status(self):
                return None
        return Resp()

    monkeypatch.setattr("requests.get", fake_get)

    items = searcher._arxiv_search("solubility prediction", 6)

    assert captured["url"] == searcher.ARXIV_API
    assert captured["params"]["search_query"] == "all:solubility prediction"
    assert len(items) == 2

    first = items[0]
    assert first["type"] == "arxiv"
    assert first["title"] == "Predicting Aqueous Solubility with Graph Networks"
    assert first["snippet"] == "We predict solubility from molecular graphs."
    assert first["date"] == "2026-04-01"
    assert first["url"] == "http://arxiv.org/abs/2604.00001v2"
    assert first["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert first["pdf_url"] == "http://arxiv.org/pdf/2604.00001v2"


def test_arxiv_uses_the_id_when_no_link_element(monkeypatch):
    feed = ATOM.replace(
        '<link href="http://arxiv.org/abs/2604.00002v1" rel="alternate" type="text/html"/>',
        "")
    monkeypatch.setattr("requests.get", lambda *a, **k: type(
        "R", (), {"text": feed, "raise_for_status": lambda self: None})())

    items = searcher._arxiv_search("q", 6)

    assert items[1]["url"] == "http://arxiv.org/abs/2604.00002v1"


def test_arxiv_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("requests.get", boom)

    assert searcher._arxiv_search("q", 6) == []
    assert searcher._arxiv_search("", 6) == []


def test_arxiv_malformed_xml_returns_empty(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: type(
        "R", (), {"text": "<feed><broken", "raise_for_status": lambda self: None})())

    assert searcher._arxiv_search("q", 6) == []


def test_arxiv_is_a_planner_source():
    from backend.research.planner import SOURCES

    assert "arxiv" in SOURCES
