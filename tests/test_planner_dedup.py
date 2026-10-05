"""Sub-question deduplication and incremental evidence extraction.

Two related concerns. A planner asked to split one question into sub-questions
often returns the same sub-question twice, which wastes a parallel search slot
and skews the evidence base. And each research round re-chunks the whole corpus,
so without tracking what has been read an extra round re-pays for documents
already processed.
"""
import asyncio

import pytest

from backend.research.chunker import Chunk
from backend.research.planner import dedupe_subquestions, make_plan, regenerate


def plan_of(*questions):
    return {"subquestions": [
        {"question": q, "searches": [{"source": "web", "query": "q"}]}
        for q in questions]}


# --- what must NOT be deduplicated -----------------------------------------

@pytest.mark.parametrize("questions", [
    # Four genuinely different dimensions of the same comparison.
    ("How do React Native and Flutter compare on runtime performance and app size?",
     "What is the state of developer tooling and AI assistance for each framework?",
     "How mature is each package ecosystem and what are the upgrade risks?",
     "What does the job market demand for these skills look like?"),
    # Different macroeconomics dimensions, no technology vocabulary at all.
    ("Compare the employment effects of the 2008 crisis and the 2020 recession",
     "Compare how wages recovered after each recession across OECD countries",
     "Compare the distributional effect of each recession on household inequality",
     "Compare the policy responses used by central banks in each episode"),
    ("Compare efficacy of CRISPR and antisense oligonucleotides in inherited disease",
     "Compare the safety profiles and off-target risk of each gene therapy approach",
     "Compare delivery mechanisms for reaching target tissue in each modality",
     "Compare the regulatory approval pathway for each class of therapy"),
])
def test_distinct_subquestions_are_all_kept(questions):
    """Shared entity names must not be mistaken for a restatement."""
    clean, rejected = dedupe_subquestions(plan_of(*questions))

    assert len(clean["subquestions"]) == len(questions)
    assert rejected == []


# --- what must be deduplicated ---------------------------------------------

def test_exact_duplicate_is_dropped():
    q = "Compare React Native and Flutter performance benchmarks in 2026"
    clean, rejected = dedupe_subquestions(plan_of(q, q))

    assert len(clean["subquestions"]) == 1
    assert len(rejected) == 1
    assert rejected[0]["duplicate_of"] == q


def test_reworded_duplicate_is_dropped():
    """Same question, different words: the character-overlap signal catches it."""
    clean, rejected = dedupe_subquestions(plan_of(
        "Compare React Native and Flutter performance benchmarks in 2026",
        "React Native versus Flutter performance benchmark comparison 2026",
        "What is the hiring demand for React Native and Flutter developers",
    ))

    assert len(clean["subquestions"]) == 2, "the paraphrase must be dropped"
    assert len(rejected) == 1


def test_a_run_of_duplicates_collapses_to_one():
    clean, rejected = dedupe_subquestions(plan_of(
        "Compare tool performance",
        "Compare tool performance benchmarks",
        "Compare tool performance benchmark results",
    ))

    assert len(clean["subquestions"]) == 1
    assert len(rejected) == 2


def test_first_occurrence_is_the_one_kept():
    """Ordering matters: the planner puts the broadest question first."""
    first = "Compare the frameworks on performance and size"
    clean, rejected = dedupe_subquestions(plan_of(first, first + " in 2026"))

    assert clean["subquestions"][0]["question"] == first


def test_malformed_subquestions_are_dropped():
    """A sub-question with no usable search would never be dispatched."""
    plan = {"subquestions": [
        {"question": "", "searches": [{"source": "web", "query": "x"}]},
        {"question": "   ", "searches": [{"source": "web", "query": "x"}]},
        "not a dict",
        {"question": "Valid question?",
         "searches": [{"source": "web", "query": "x"}]},
        {"question": "Bad searches", "searches": [{"source": "nope", "query": "x"}]},
    ]}
    clean, _ = dedupe_subquestions(plan)

    assert [s["question"] for s in clean["subquestions"]] == ["Valid question?"]


def test_questions_without_searches_are_kept_only_as_a_last_resort():
    """Dropping them must never empty the plan -- that would stop all research."""
    plan = {"subquestions": [
        {"question": "Only question, no searches",
         "searches": [{"source": "bogus", "query": "x"}]},
    ]}
    clean, _ = dedupe_subquestions(plan)

    assert [s["question"] for s in clean["subquestions"]] == \
        ["Only question, no searches"]


def test_dedup_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PLAN_DEDUP_THRESHOLD", "1.0")
    monkeypatch.setenv("PLAN_DEDUP_GRAM_THRESHOLD", "1.0")
    q = "Compare tool performance"
    clean, rejected = dedupe_subquestions(plan_of(q, q))

    assert len(clean["subquestions"]) == 2
    assert rejected == []


# --- regeneration ------------------------------------------------------------

def test_replacements_are_requested_for_dropped_duplicates(monkeypatch):
    seen = {}

    def fake_ask(system, user, json_mode=False, role="default"):
        seen["system"] = system
        seen["user"] = user
        return {"subquestions": [
            {"question": "How does ecosystem maturity compare?",
             "searches": [{"source": "web", "query": "ecosystem"}]}]}

    monkeypatch.setattr("backend.research.planner.ask", fake_ask)

    kept = [{"question": "How does raw speed compare?", "searches": []}]
    rejected = [{"question": "How fast is it?", "duplicate_of": kept[0]["question"],
                 "similarity": 0.9}]

    extras = regenerate("Compare two frameworks", kept, rejected)

    assert len(extras) == 1
    assert "already kept" in seen["user"].lower()
    assert "How fast is it?" in seen["user"], "it must be told what to avoid"
    assert "How does raw speed compare?" in seen["user"]


def test_regeneration_failure_leaves_the_plan_intact(monkeypatch):
    """A failed regeneration must not lose the surviving sub-questions."""
    def boom(system, user, json_mode=False, role="default"):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("backend.research.planner.ask", boom)

    assert regenerate("q", [{"question": "a"}], [{"question": "b"}]) == []


def test_unusable_replacement_reply_is_ignored(monkeypatch):
    monkeypatch.setattr("backend.research.planner.ask",
                        lambda *a, **k: "not a dict")
    assert regenerate("q", [{"question": "a"}], [{"question": "b"}]) == []


def test_make_plan_deduplicates_and_regenerates(monkeypatch):
    """End to end: duplicate in, replacement out, and collisions filtered again."""
    replies = [
        {"subquestions": [
            {"question": "Compare raw speed of the two options",
             "searches": [{"source": "web", "query": "speed"}]},
            {"question": "Compare raw speed of the two options",
             "searches": [{"source": "web", "query": "speed"}]},
            {"question": "Compare raw speed of the two options",
             "searches": [{"source": "web", "query": "speed"}]},
        ]},
        {"subquestions": [
            # The replacement collides with a kept question, so it must be cut.
            {"question": "Compare raw speed of the two options",
             "searches": [{"source": "web", "query": "speed"}]},
            {"question": "Compare ecosystem maturity and upgrade risk",
             "searches": [{"source": "web", "query": "ecosystem"}]},
        ]},
    ]
    calls = {"n": 0}

    def fake_ask(system, user, json_mode=False, role="default"):
        reply = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        return reply

    monkeypatch.setattr("backend.research.planner.ask", fake_ask)

    plan = make_plan("Compare two options")
    questions = [s["question"] for s in plan["subquestions"]]

    assert len(questions) == 2
    assert "Compare ecosystem maturity and upgrade risk" in questions
    assert all(q in questions for q in ["Compare raw speed of the two options",
                                       "Compare ecosystem maturity and upgrade risk"])


def test_all_duplicates_still_yield_a_usable_plan(monkeypatch):
    """Better one sub-question than no plan at all."""
    monkeypatch.setattr("backend.research.planner.ask", lambda *a, **k: {
        "subquestions": [
            {"question": "Only question", "searches": [{"source": "web", "query": "x"}]},
            {"question": "Only question again", "searches": [{"source": "web", "query": "y"}]},
        ]})
    monkeypatch.setattr("backend.research.planner.ask", lambda *a, **k: {
        "subquestions": [
            {"question": "Only question", "searches": [{"source": "web", "query": "x"}]},
            {"question": "Only question again", "searches": [{"source": "web", "query": "y"}]},
        ]})

    plan = make_plan("q")
    assert len(plan["subquestions"]) == 1


# --- incremental extraction --------------------------------------------------

def _chunk(url, grade="full"):
    return Chunk(chunk_id=f"c_{url}", source_id=url, source_url=url,
                 source_title="t", content="body text", retrieval_status=grade)


def test_first_round_processes_everything():
    from backend.research.graph import _unprocessed_chunks

    chunks = [_chunk("https://a.dev"), _chunk("https://b.dev")]
    fresh, upgraded = _unprocessed_chunks({}, chunks)

    assert len(fresh) == 2
    assert upgraded == []


def test_second_round_skips_sources_already_extracted():
    from backend.research.graph import _unprocessed_chunks

    chunks = [_chunk("https://a.dev"), _chunk("https://b.dev"), _chunk("https://a.dev")]
    state = {"extracted_sources": {"https://a.dev": "full",
                                   "https://b.dev": "full"}}

    fresh, upgraded = _unprocessed_chunks(state, chunks)

    assert fresh == [], "already-processed sources must not be re-paid for"
    assert upgraded == []


def test_source_is_reprocessed_when_its_grade_improves():
    """A source that was snippet-only and later fetched properly is new material."""
    from backend.research.graph import _unprocessed_chunks

    state = {"extracted_sources": {"https://c.dev": "metadata_only",
                                   "https://a.dev": "full"}}
    chunks = [_chunk("https://c.dev", "full"), _chunk("https://a.dev", "full")]

    fresh, upgraded = _unprocessed_chunks(state, chunks)

    assert [c.source_url for c in fresh] == ["https://c.dev"], \
        "only the source whose grade improved may be re-read"
    assert upgraded == [{"url": "https://c.dev",
                         "from": "metadata_only", "to": "full"}]


def test_grade_comparison_is_directional():
    from backend.research.graph import _grade_rank

    assert _grade_rank("full") > _grade_rank("partial")
    assert _grade_rank("partial") > _grade_rank("metadata_only")
    assert _grade_rank("metadata_only") > _grade_rank("failed")


def test_analyse_does_not_re_extract_when_nothing_is_new(monkeypatch):
    """The whole point: an extra round must not cost a full re-extraction."""
    from backend.research import graph as G

    chunks = [_chunk("https://a.dev"), _chunk("https://b.dev")]

    def explode(*a, **k):
        raise AssertionError("extraction must not run for already-seen sources")

    monkeypatch.setattr(G, "extract_evidence", explode)
    monkeypatch.setattr(G.config, "analysis_enabled", lambda: True)

    state = {"question": "q", "chunks": chunks, "notes": [],
             "extracted": [], "extracted_sources": {"https://a.dev": "full",
                                                    "https://b.dev": "full"}}
    out = asyncio.run(G.analyse(state))

    assert out["extracted"] == []
    assert any("no new or improved sources" in n for n in out["notes"])
