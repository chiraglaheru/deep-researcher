"""Breadth scaling and AI Overview discovery.

Covers the scale-up: the planner aims for more sub-questions with duplicates
still removed, source caps are raised, and AI Overviews contribute reference
links only -- never generated prose.
"""
import asyncio

from backend.research import config
from backend.research import searcher


def test_planner_aims_for_more_subquestions():
    import backend.research.planner as planner_module

    assert "10-14" in planner_module.SYSTEM
    assert "2-3 concrete search queries" in planner_module.SYSTEM


def test_make_plan_keeps_many_distinct_subquestions(monkeypatch):
    import backend.research.planner as planner_module

    subs = [{"question": q, "searches": [{"source": "web", "query": q[:20]}]}
            for q in [
                "Throughput under sustained load?",
                "Latency percentiles at the edge?",
                "Memory footprint on small devices?",
                "Error handling and retry semantics?",
                "Authentication model for teams?",
                "Pricing tiers for startups?",
                "Command-line ergonomics?",
            ]]
    monkeypatch.setattr(planner_module, "ask",
                        lambda *a, **k: {"subquestions": subs})

    plan = planner_module.make_plan("Compare two options?")

    assert len(plan["subquestions"]) == 7


def test_duplicates_still_removed_at_scale(monkeypatch):
    import backend.research.planner as planner_module

    subs = [{"question": "Throughput under load?",
             "searches": [{"source": "web", "query": "throughput"}]}
            for _ in range(4)] + [
        {"question": "Latency under load?",
         "searches": [{"source": "web", "query": "latency"}]},
    ]
    calls = {"n": 0}

    def fake_ask(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"subquestions": subs}
        return {"subquestions": []}

    monkeypatch.setattr(planner_module, "ask", fake_ask)

    plan = planner_module.make_plan("Compare two options?")

    questions = [s["question"] for s in plan["subquestions"]]
    assert len(questions) == len(set(questions)) == 2


def test_source_caps_scaled_for_breadth():
    assert config.max_sources() == 120
    assert config.total_fetch_budget() == 100
    assert config.fetch_max_sources() == 80
    assert config.max_evidence_records() == 400
    assert config.ai_overview_enabled() is True


def _google_with_overview():
    return {
        "organic_results": [
            {"title": "Doc", "link": "https://docs.example.org/a",
             "snippet": "text", "date": "2026-01-01"},
        ],
        "ai_overview": {"page_token": "TOKEN123"},
    }


def test_ai_overview_contributes_links_only(monkeypatch):
    seen = {}

    def fake_cache(source, query, n, params):
        return _google_with_overview()

    def fake_run(params):
        seen.update(params)
        assert "page_token" not in params or params.get("engine") == \
            "google_ai_overview"
        return {"ai_overview": {"references": [
            {"title": "Cited paper", "link": "https://paper.dev/1"},
            {"title": "No link here"},
        ]}}

    monkeypatch.setattr(searcher, "_cached_run", fake_cache)
    monkeypatch.setattr(searcher, "_run", fake_run)

    items, meta = searcher.search_full("web", "some question")

    assert seen.get("engine") == "google_ai_overview"
    assert seen.get("page_token") == "TOKEN123"
    assert meta["ai_overview_refs"] == [
        {"title": "Cited paper", "link": "https://paper.dev/1"}]
    assert len(items) == 1  # prose never becomes an item


def test_ai_overview_failure_keeps_search_results(monkeypatch):
    def fake_cache(source, query, n, params):
        return _google_with_overview()

    def boom(params):
        raise RuntimeError("overview expired")

    monkeypatch.setattr(searcher, "_cached_run", fake_cache)
    monkeypatch.setattr(searcher, "_run", boom)

    items, meta = searcher.search_full("web", "some question")

    assert len(items) == 1
    assert "ai_overview_refs" not in meta


def test_ai_overview_disabled_skips_second_call(monkeypatch):
    monkeypatch.setenv("AI_OVERVIEW_ENABLED", "0")
    calls = []

    def fake_cache(source, query, n, params):
        return _google_with_overview()

    def fake_run(params):
        calls.append(params)
        return {}

    monkeypatch.setattr(searcher, "_cached_run", fake_cache)
    monkeypatch.setattr(searcher, "_run", fake_run)

    _, meta = searcher.search_full("web", "some question")

    assert calls == []
    assert "ai_overview_refs" not in meta


def test_search_worker_surfaces_ai_leads(monkeypatch):
    from backend.research import graph as graph_module

    def fake_full(source, query, n=6):
        return ([{"title": "T", "url": "https://x.dev", "snippet": "s",
                  "date": "", "type": source, "query": query}],
                {"ai_overview_refs": [
                    {"title": "Paper", "link": "https://paper.dev/1"},
                    {"title": "Empty", "link": ""}]})

    monkeypatch.setattr(graph_module, "search_full", fake_full)

    update = asyncio.run(graph_module.search_worker(
        {"source": "web", "query": "q"}))

    assert update["ai_leads"] == [{"title": "Paper",
                                   "url": "https://paper.dev/1"}]
    assert update["paa"] == []
