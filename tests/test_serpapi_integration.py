"""SerpApi integrations beyond plain result links.

Covers what the pipeline now extracts from responses it already pays for:
related questions/searches, knowledge-graph entities, answer boxes, Scholar
authority metadata, patents, autocomplete expansion, and forward citation
chasing. All SerpApi I/O is faked at the cache layer; no credits spent.
"""
import asyncio

from backend.research import searcher
from backend.research import relevance


def _google_response():
    return {
        "organic_results": [
            {"title": "Benchmark doc", "link": "https://docs.example.org/bench",
             "snippet": "throughput numbers", "date": "2026-01-01"},
        ],
        "related_questions": [
            {"question": "What is the throughput limit?"},
            {"question": ""},
        ],
        "related_searches": [{"query": "throughput vs latency"}],
        "knowledge_graph": {"title": "Attention", "type": "Song",
                            "description": "a pop song"},
        "answer_box": {"title": "Answer", "link": "https://docs.example.org/a",
                       "answer": "1800 tokens per second"},
    }


def test_search_full_captures_meta_and_stamps_entities(monkeypatch):
    monkeypatch.setattr(searcher, "_cached_run", lambda *a, **k: _google_response())

    items, meta = searcher.search_full("web", "attention mechanism")

    assert meta["related_questions"] == ["What is the throughput limit?"]
    assert meta["related_searches"] == ["throughput vs latency"]
    assert meta["knowledge_graph"]["type"] == "Song"
    assert meta["answer_box"]["link"] == "https://docs.example.org/a"
    assert items[0]["entity"] == "Attention"
    assert items[0]["entity_type"] == "Song"
    # The answer box becomes a bonus item without consuming a result slot.
    assert items[-1]["url"] == "https://docs.example.org/a"
    assert items[-1]["snippet"].startswith("Direct answer:")


def test_search_stays_backward_compatible(monkeypatch):
    monkeypatch.setattr(searcher, "_cached_run", lambda *a, **k: _google_response())

    items = searcher.search("web", "attention mechanism")

    assert isinstance(items, list)
    assert items[0]["url"] == "https://docs.example.org/bench"


def test_scholar_extras_carry_authority_metadata(monkeypatch):
    def fake_cache(source, query, n, params):
        assert params["engine"] == "google_scholar"
        return {"organic_results": [{
            "title": "Paper", "link": "https://arxiv.org/abs/1",
            "snippet": "abstract",
            "publication_info": {"summary": "A Li 2025"},
            "result_id": "ABC123",
            "inline_links": {"cited_by": {"total": 412, "cites_id": "CITES9"}},
            "resources": [{"file_format": "PDF", "link": "https://arxiv.org/pdf/1"},
                          {"file_format": "HTML", "link": "https://x.dev"}],
        }]}
    monkeypatch.setattr(searcher, "_cached_run", fake_cache)

    (item,), _ = searcher.search_full("scholar", "attention")

    assert item["cited_by"] == 412
    assert item["scholar_id"] == "ABC123"
    assert item["pdf_url"] == "https://arxiv.org/pdf/1"


def test_patent_source_returns_patent_items(monkeypatch):
    def fake_cache(source, query, n, params):
        assert params["engine"] == "google_patents"
        return {"organic_results": [{
            "title": "Attention patent", "link": "https://patents.google.com/x",
            "snippet": "claims", "publication_date": "2024-05-01"}]}
    monkeypatch.setattr(searcher, "_cached_run", fake_cache)

    items, _ = searcher.search_full("patent", "attention mechanism")

    assert items[0]["type"] == "patent"
    assert "patents.google.com" in items[0]["url"]


def test_expand_query_returns_top_suggestion(monkeypatch):
    monkeypatch.setattr(
        searcher, "_cached_run",
        lambda *a, **k: {"suggestions": [{"value": "attention mechanism transformer"},
                                         {"value": "attention"}]})

    assert searcher.expand_query("attention") == "attention mechanism transformer"


def test_expand_query_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(searcher, "_cached_run", boom)

    assert searcher.expand_query("anything") == ""
    assert searcher.expand_query("") == ""


def test_cited_by_search_builds_scholar_items(monkeypatch):
    seen = {}

    def fake_cache(source, query, n, params):
        seen.update(params)
        return {"organic_results": [{
            "title": "Citer", "link": "https://c.dev", "snippet": "s",
            "publication_info": {"summary": "B 2025"},
            "inline_links": {"cited_by": {"total": 3}}}]}
    monkeypatch.setattr(searcher, "_cached_run", fake_cache)

    items = searcher.cited_by_search("CITES9", "attention")

    assert seen["cites"] == "CITES9"
    assert items[0]["type"] == "scholar"
    assert items[0]["cited_by"] == 3
    assert searcher.cited_by_search("") == []


def test_wrong_entity_type_is_downranked():
    from backend.research.chunker import Chunk

    def chunk(cid, content, etype):
        return Chunk(chunk_id=cid, source_id="s", source_url="https://x.dev",
                     source_title="T", content=content, entity="Attention",
                     entity_type=etype)

    song = chunk("s#0", "Attention lyrics running round my head attention",
                 "Song")
    tech = chunk("s#1", "attention mechanism transformer throughput measured",
                 "")
    kept = relevance.rank(
        [song, tech],
        "Compare attention mechanisms in transformers. Analyse throughput.",
        None, top_k=10)

    assert [k.chunk.chunk_id for k in kept][0] == "s#1"


def test_recent_chunks_win_recency_questions():
    from backend.research.chunker import Chunk

    old = Chunk(chunk_id="s#0", source_id="s", source_url="https://a.dev",
                source_title="A", content="flutter android benchmark results compared",
                publication_date="2021-03-01")
    new = Chunk(chunk_id="s#1", source_id="s", source_url="https://b.dev",
                source_title="B", content="flutter android benchmark results compared",
                publication_date="2026-05-01")
    kept = relevance.rank(
        [old, new],
        "Compare the latest flutter and android benchmark results. Analyse performance.",
        None, top_k=10)

    assert [k.chunk.chunk_id for k in kept][0] == "s#1"


def test_uncovered_questions_skips_answered_ones():
    from backend.research.evidence import Evidence
    from backend.research.relevance import uncovered_questions

    records = [Evidence(claim="throughput reaches 1800 tokens per second",
                        dimension="throughput")]
    bank = ["What is the throughput limit?", "Who sang the hit single of 2017?"]

    out = uncovered_questions(bank, records)

    assert out == ["Who sang the hit single of 2017?"]


def test_forward_candidates_chases_top_cited(monkeypatch):
    from backend.research import references
    from backend.research import searcher as searcher_module

    calls = []

    def fake_cited(scholar_id, query="", n=6):
        calls.append(scholar_id)
        return [{"url": "https://new.dev/paper", "title": "Citer"}]

    monkeypatch.setattr(searcher_module, "cited_by_search", fake_cited)
    sources = [
        {"url": "https://old.dev/a", "scholar_id": "ID1", "cited_by": 400},
        {"url": "https://old.dev/b", "scholar_id": "ID2", "cited_by": 5},
        {"url": "https://plain.dev/c"},
    ]

    out = references.forward_candidates(sources, "attention", set(), {})

    assert out == ["https://new.dev/paper"]
    assert calls[0] == "ID1"


def test_planner_expands_queries_when_enabled(monkeypatch):
    import backend.research.planner as planner_module
    from backend.research import searcher as searcher_module

    monkeypatch.setenv("SEARCH_EXPAND_QUERIES", "1")
    monkeypatch.setattr(planner_module, "ask", lambda *a, **k: {"subquestions": [
        {"question": "Throughput?", "searches": [
            {"source": "web", "query": "attention benchmarks"}]}]})
    monkeypatch.setattr(searcher_module, "expand_query",
                        lambda q: "attention mechanism transformer benchmarks")

    plan = planner_module.make_plan("Compare attention mechanisms.")

    assert plan["subquestions"][0]["searches"][0]["query"] == \
        "attention mechanism transformer benchmarks"


def test_planner_keeps_draft_when_expansion_fails(monkeypatch):
    import backend.research.planner as planner_module
    from backend.research import searcher as searcher_module

    monkeypatch.setenv("SEARCH_EXPAND_QUERIES", "1")
    monkeypatch.setattr(planner_module, "ask", lambda *a, **k: {"subquestions": [
        {"question": "Throughput?", "searches": [
            {"source": "web", "query": "attention benchmarks"}]}]})
    monkeypatch.setattr(searcher_module, "expand_query",
                        lambda q: (_ for _ in ()).throw(RuntimeError("down")))

    plan = planner_module.make_plan("Compare attention mechanisms.")

    assert plan["subquestions"][0]["searches"][0]["query"] == "attention benchmarks"


def test_gap_check_surfaces_unanswered_questions(monkeypatch):
    from backend.research import graph as graph_module
    from backend.research.evidence import Evidence

    prompts = []

    def fake_ask(system, user, json_mode=False, role="default"):
        prompts.append(user)
        return {"sufficient": True, "missing": "", "follow_ups": []}

    monkeypatch.setattr(graph_module, "ask", fake_ask)
    state = {"question": "Compare A and B. Analyse performance.",
             "max_rounds": 3, "round": 1, "seen": [],
             "plan": {"subquestions": []}, "raw": [],
             "paa": ["What is the latency record in trapped ion systems?"],
             "extracted": [Evidence(claim="throughput finding",
                                    dimension="performance",
                                    evidence_id="E1")]}
    asyncio.run(graph_module.gap_check(state))

    assert any("ASKED ELSEWHERE BUT UNANSWERED HERE" in p for p in prompts)
