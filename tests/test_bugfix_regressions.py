"""Regression tests for the six bug-audit fixes.

1. Thin runs: the Collector contradiction fallback must degrade, never crash.
2. Reference synthesis: one call per source, nothing silently dropped.
3. Wikipedia: a mid-list failure keeps what was already fetched.
4. (Nested-throttle deadlock covered in test_serpapi_integration.py.)
5-6. Frontend: frame/status guards are asserted structurally in
     tests/test_frontend_static.py.
"""
import pytest

from backend.research.collector import Collector
from backend.research.extract import synthesise_sources
from backend.research.evidence import Evidence
from backend.research import collector as collector_module


def _collector_with(n):
    col = Collector()
    rows = [{"title": f"T{i}", "url": f"https://a.dev/{i}", "snippet": "s",
             "date": "2025", "type": "web", "query": "q"}
            for i in range(n)]
    col.add(rows)
    return col


# --- 1. contradiction fallback -------------------------------------------------

def test_contradiction_fallback_degrades_when_model_fails(monkeypatch):
    """A dead model must not kill a thin run during the fallback."""
    monkeypatch.setattr(collector_module, "ask",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("all models failed")))

    assert _collector_with(2).find_contradictions() == []


def test_contradiction_fallback_degrades_on_malformed_reply(monkeypatch):
    for reply in (None, ["not", "a", "dict"], {"contradictions": "nope"}):
        monkeypatch.setattr(collector_module, "ask", lambda *a, **k: reply)
        assert _collector_with(2).find_contradictions() == []


def test_contradiction_fallback_returns_real_findings(monkeypatch):
    found = [{"topic": "x", "side_a": "a", "side_b": "b"}]
    monkeypatch.setattr(collector_module, "ask",
                        lambda *a, **k: {"contradictions": found})

    assert _collector_with(2).find_contradictions() == found


def test_collector_single_source_skips_the_model_entirely(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not call the model under two sources")

    monkeypatch.setattr(collector_module, "ask", boom)

    assert _collector_with(1).find_contradictions() == []


# --- 2. per-source synthesis ---------------------------------------------------

def _record(source_id, quality="strong", url=None):
    return Evidence(claim=f"finding from {source_id}", source_id=source_id,
                    source_url=url or f"https://{source_id}.dev/x",
                    source_title=f"Source {source_id}", quality=quality,
                    retrieval_status="full")


def test_synthesis_summarises_every_valuable_source(monkeypatch):
    """Pre-fix, one batched call stored only the first source and dropped
    the other three silently."""
    import backend.research.extract as extract_module

    calls = []

    def fake_ask(system, user, json_mode=False, role="default"):
        calls.append(user)
        sid = user.split("=== SOURCE ", 1)[1].split(" ===", 1)[0]
        return {"summary": f"what {sid} establishes", "weight": "strong"}

    monkeypatch.setattr(extract_module, "ask", fake_ask)
    records = [_record("s1"), _record("s2"), _record("s3"), _record("s4")]

    out = synthesise_sources(records)

    assert sorted(out) == ["s1", "s2", "s3", "s4"]
    assert all(v["summary"] for v in out.values())
    assert len(calls) == 4, "one call per source, each about one source"
    for payload in calls:
        assert payload.count("=== SOURCE ") == 1


def test_synthesis_expense_only_on_valuable_sources(monkeypatch):
    import backend.research.extract as extract_module

    calls = []
    monkeypatch.setattr(extract_module, "ask",
                        lambda *a, **k: calls.append(1) or
                        {"summary": "s", "weight": "weak"})

    out = synthesise_sources([_record("weak1", quality="weak"),
                              _record("spec1", quality="speculative")])

    assert out == {}
    assert calls == [], "weak-only sources must not spend model calls"


def test_synthesis_respects_budget(monkeypatch):
    import backend.research.extract as extract_module

    class TinyBudget:
        def __init__(self):
            self.left = 2

        def take(self, n=1):
            if self.left < n:
                return False
            self.left -= n
            return True

    monkeypatch.setattr(extract_module, "ask",
                        lambda *a, **k: {"summary": "s", "weight": "strong"})

    records = [_record("s1"), _record("s2"), _record("s3"), _record("s4")]
    out = synthesise_sources(records, budget=TinyBudget())

    assert len(out) == 2, "budget must cap the number of synthesis calls"


# --- 3. Wikipedia partial failure ----------------------------------------------

def test_wiki_keeps_docs_when_a_later_title_fails(monkeypatch):
    from backend.research import wiki as wiki_module

    class OkResponse:
        status_code = 200

        def __init__(self, html):
            self._html = html

        def json(self):
            return {"parse": {"text": self._html}}

    good = "<h2>Body</h2><p>" + ("useful sentence. " * 30) + "</p>"

    def fake_get(url, params=None, **kwargs):
        title = (params or {}).get("page", "")
        if title == "C":
            raise ValueError("truncated json")
        return OkResponse(good)

    monkeypatch.setattr("requests.get", fake_get)

    docs = wiki_module._fetch_extracts(["A", "B", "C"], chars=6000)

    assert [d.title for d in docs] == ["A", "B"], \
        "documents fetched before the failure must survive"
