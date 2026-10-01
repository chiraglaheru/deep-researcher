import asyncio
import datetime
import os

import pytest
from dotenv import load_dotenv

import backend.research.collector as C
import backend.research.graph as G
import backend.research.planner as P
from backend.research.collector import Collector, year_of
from backend.research.researcher import deep_research

load_dotenv()


def test_year_of():
    assert year_of("Mar 5, 2025") == 2025
    assert year_of(None) is None
    assert year_of("3 days ago") == datetime.date.today().year


def test_collector_dedupes_and_flags_stale():
    col = Collector()
    new = col.add([
        {"title": "A", "url": "https://www.x.com/a/", "snippet": "", "date": "2019", "type": "web", "query": "q"},
        {"title": "A dup", "url": "https://x.com/a", "snippet": "", "date": "2025", "type": "web", "query": "q"},
    ])
    assert len(new) == 1
    assert new[0]["stale"] is True


def test_planner_drops_unknown_sources(monkeypatch):
    monkeypatch.setattr(P, "ask", lambda *a, **k: {"subquestions": [
        {"question": "q", "searches": [
            {"source": "web", "query": "a"},
            {"source": "tiktok", "query": "b"},
        ]}]})
    plan = P.make_plan("anything")
    assert plan["subquestions"][0]["searches"] == [{"source": "web", "query": "a"}]


def test_graph_runs_end_to_end_with_mocks(monkeypatch):
    monkeypatch.setattr(G, "make_plan", lambda q: {"subquestions": [{"question": "a", "searches": [
        {"source": "web", "query": "rust"}, {"source": "news", "query": "cpp"}]}]})

    def fake_search(source, query, n=6):
        return [{"title": f"{source}{query}", "url": f"https://x.com/{source}/{query}",
                 "snippet": "s", "date": "2025", "type": source, "query": query}]
    monkeypatch.setattr(G, "search", fake_search)

    calls = {"n": 0}

    def fake_ask(system, user, json_mode=False, role="default"):
        calls["n"] += 1
        return {"sufficient": calls["n"] > 1, "missing": "x",
                "follow_ups": [{"source": "scholar", "query": "bench"}]}
    monkeypatch.setattr(G, "ask", fake_ask)
    monkeypatch.setattr(C, "ask", lambda *a, **k: {"contradictions": []})
    monkeypatch.setattr(G, "write_report", lambda q, col, c: f"REPORT {len(col.list())}")

    async def run():
        return [ev async for ev in deep_research("q", 3)]

    events = asyncio.run(run())
    types = [e["type"] for e in events]
    assert types[0] == "plan"
    assert types[-1] == "report"
    assert "gap" in types
    assert events[-1]["data"] == "REPORT 3"  # 2 first-round + 1 follow-up


@pytest.mark.skipif(not os.getenv("SERPAPI_API_KEY"), reason="no SERPAPI_API_KEY")
def test_serpapi_live():
    """Uses 1 SerpApi credit. Confirms your key and the engine setup work."""
    from backend.research.searcher import search
    results = search("web", "rust game engine", n=3)
    assert len(results) > 0
    assert results[0]["url"]