"""Regression tests for the pipeline resilience audit.

Covers what the audit actually found, not what was assumed:
1. Token spikes: a full 70-chunk ranking must fit inside EXTRACT_MAX_BATCHES
   at 6k-char batches, and the budget estimate must cover real call counts.
2. Domain noise: social/video hosts are dropped from web/news, while scholar,
   GitHub and Wikipedia always pass through.
3. Stream truncation: long section headings reach the document unaltered.
"""
from backend.research import config
from backend.research.chunker import Chunk
from backend.research.relevance import Scored


def _scored(n, chars=1400):
    out = []
    for i in range(n):
        chunk = Chunk(chunk_id=f"s1#{i}", source_id="s1",
                      source_url="https://a.dev/doc", source_title="Doc",
                      content="x" * chars)
        out.append(Scored(chunk, 1.0, ["test"]))
    return out


def test_full_ranking_fits_inside_max_batches():
    """At 6k batches a worst-case ranking must not silently lose its tail."""
    from backend.research.extract import plan_batches

    batches = plan_batches(_scored(config.relevance_top_chunks()))

    assert len(batches) <= config.extract_max_batches()
    assert sum(len(b) for b in batches) == config.relevance_top_chunks()


def test_budget_estimate_covers_small_batches():
    """The auto budget must reflect ~16 extraction batches, not the old ~8."""
    assert config.budget_estimate(2) >= 53
    assert config.total_llm_budget(2) >= config.budget_estimate(2)


def test_blocked_hosts_dropped_from_web_and_news():
    from backend.research.searcher import _drop_blocked

    rows = [
        {"title": "vid", "url": "https://www.youtube.com/watch?v=1"},
        {"title": "post", "url": "https://x.com/someone/status/1"},
        {"title": "wiki", "url": "https://en.wikipedia.org/wiki/X"},
        {"title": "docs", "url": "https://docs.example.org/guide"},
    ]

    web = _drop_blocked(list(rows), "web")
    assert {r["title"] for r in web} == {"wiki", "docs"}

    news = _drop_blocked(list(rows), "news")
    assert {r["title"] for r in news} == {"wiki", "docs"}

    # Scholar and GitHub are never filtered: their hosts are the signal.
    assert len(_drop_blocked(list(rows), "scholar")) == 4
    assert len(_drop_blocked(list(rows), "github")) == 4


def test_blocklist_is_env_configurable(monkeypatch):
    from backend.research.searcher import _drop_blocked

    monkeypatch.setenv("SEARCH_BLOCKED_HOSTS", "example.org")
    rows = [{"title": "a", "url": "https://docs.example.org/guide"},
            {"title": "b", "url": "https://other.dev/x"}]

    assert [r["title"] for r in _drop_blocked(rows, "web")] == ["b"]

    monkeypatch.setenv("SEARCH_BLOCKED_HOSTS", "")
    assert len(_drop_blocked(rows, "web")) == 2


def test_long_headings_pass_through_unaltered(monkeypatch):
    """No length gate may cut section headings anywhere in the pipeline."""
    from backend.research import report as report_module
    from backend.research.evidence import Evidence

    long_heading = "W" * 100
    monkeypatch.setattr(
        report_module, "_write_section",
        lambda system, question, heading, instruction, recs, r, t,
        budget=None, min_words=350, failed=None:
        f"## {long_heading}\n\nprose [{sorted(r.values())[0]}]\n")

    records = [Evidence(claim="finding one", source_title="A",
                        source_url="https://a.dev", quality="strong",
                        retrieval_status="full", confidence=0.9)]

    class Col:
        def context(self):
            return ""

    out = report_module.write_report("Compare A and B. Analyse performance.",
                                     Col(), [], records=records)

    assert long_heading in out
