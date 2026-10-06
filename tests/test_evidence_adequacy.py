"""Evidence adequacy: the pipeline must tell signal from noise and holes from coverage.

Covers the observed failures, not assumed ones:
- single-generic-term chunks (a song sharing one word with the question) rank
  below entity-matching passages, and snippet-only sources rank below read ones;
- related-variant evidence (another release, another architecture class) is
  capped and labelled instead of spending strong findings on the wrong target;
- dimensions without adequate findings are reported as holes, not polished over;
- gap_check aims recovery at those holes;
- per-source synthesis skips weak-only groups instead of spending quota.
"""
import asyncio

from backend.research import config
from backend.research.chunker import Chunk
from backend.research.evidence import Evidence
from backend.research.relevance import (Scored, rank,
                                        target_variant_mismatch)


QUESTION = ("Compare Mistral Large 2 and Llama 3 for batch inference. "
            "Analyse throughput and context window.")


def _chunk(chunk_id, content, status="full", url="https://a.dev/doc"):
    return Chunk(chunk_id=chunk_id, source_id="s1", source_url=url,
                 source_title="Doc", content=content,
                 retrieval_status=status)


def test_entity_phrase_beats_single_generic_term():
    song = _chunk("s1#0", "Attention by Charlie Puth lyrics: you've been running "
                           "round, running round, running round my head.")
    tech = _chunk("s1#1", "Mistral Large 2 attention throughput for batch "
                          "inference measured at 1800 tokens per second.")
    kept = rank([song, tech], QUESTION, None, top_k=10)

    assert [k.chunk.chunk_id for k in kept][0] == "s1#1"


def test_snippet_only_sources_rank_below_read_ones():
    same = "Mistral Large 2 batch inference throughput measured at 1800 tokens/s."
    stub = _chunk("s1#0", same, status="metadata_only")
    read = _chunk("s1#1", same, status="full")
    kept = rank([stub, read], QUESTION, None, top_k=10)

    assert [k.chunk.chunk_id for k in kept] == ["s1#1", "s1#0"]


def test_variant_mismatch_cases():
    assert target_variant_mismatch(["Mistral 7B"], "Mistral 7B is fastest",
                                   ["Mistral Large 2"])
    assert target_variant_mismatch(["Qwen3-Next"], "Qwen3-Next results",
                                   ["Qwen2.5"])
    assert target_variant_mismatch(["MoE models"], "MoE routing wins",
                                   ["dense models"])
    assert not target_variant_mismatch(["Mistral Large 2"],
                                       "Mistral Large 2 hits 60fps in 2026",
                                       ["Mistral Large 2"])
    assert not target_variant_mismatch(["transformers"], "attention paper",
                                       ["Mistral Large 2"])
    assert not target_variant_mismatch([], "runs at 60fps", ["Mistral Large 2"])
    assert not target_variant_mismatch(["Mistral 7B"], "claim",
                                       ["Mistral 7B", "Llama 3"])


def test_extraction_caps_off_target_strong_findings(monkeypatch):
    """A strong finding about the wrong variant keeps its text but loses rank."""
    from backend.research import extract as extract_module

    content = ("The benchmark states Mistral 7B is fastest in all runs "
               "on the test cluster.")
    chunk = Chunk(chunk_id="s1#0", source_id="s1",
                  source_url="https://blog.dev/x", source_title="Blog",
                  content=content, retrieval_status="full")
    scored = [Scored(chunk, 9.0, ["test"])]

    reply = {"evidence": [{
        "chunk": "s1#0",
        "claim": "Mistral 7B is fastest in all runs",
        "quote": "Mistral 7B is fastest in all runs",
        "quality": "strong", "confidence": 0.9,
        "targets": ["Mistral 7B"],
    }]}
    monkeypatch.setattr(extract_module, "ask",
                        lambda *a, **k: reply)

    records, _ = extract_module.extract_evidence(
        scored, "Compare Mistral Large 2 and Llama 3 for batch inference. "
                "Analyse throughput.")

    assert len(records) == 1
    assert records[0].quality == "moderate"
    assert "applicability" in records[0].limitations


def test_evidence_holes_name_thin_dimensions():
    from backend.research.report import evidence_holes

    records = [Evidence(claim=f"throughput finding {i}",
                        source_url=f"https://a.dev/{i}",
                        quality="strong", dimension="throughput")
               for i in range(2)]
    holes = evidence_holes(records, ["throughput", "context window"])

    assert any("context window" in h for h in holes)
    assert not any(h.startswith("Evidence hole: throughput") for h in holes)


def test_gap_check_aims_recovery_at_holes(monkeypatch):
    from backend.research import graph as graph_module

    prompts = []

    def fake_ask(system, user, json_mode=False, role="default"):
        prompts.append(user)
        return {"sufficient": True, "missing": "", "follow_ups": []}

    monkeypatch.setattr(graph_module, "ask", fake_ask)
    state = {"question": QUESTION, "max_rounds": 3, "round": 1, "seen": [],
             "plan": {"subquestions": []}, "raw": [],
             "extracted": [Evidence(claim="throughput finding",
                                    source_url="https://a.dev",
                                    quality="strong", dimension="throughput",
                                    evidence_id="E1")]}
    asyncio.run(graph_module.gap_check(state))

    assert prompts, "gap_check must still consult the model"
    assert "UNDER-EVIDENCED DIMENSIONS" in prompts[0]
    assert "context window" in prompts[0]


def test_synthesis_skips_weak_only_sources(monkeypatch):
    """Quota is not spent assessing sources with nothing assessable."""
    from backend.research import extract as extract_module

    def explode(*a, **k):
        raise AssertionError("no model call should be made")

    monkeypatch.setattr(extract_module, "ask", explode)
    records = [Evidence(claim="a rumor", source_id="s9",
                        source_url="https://blog.dev/x", quality="weak"),
               Evidence(claim="a guess", source_id="s9",
                        source_url="https://blog.dev/x", quality="speculative")]

    assert extract_module.synthesise_sources(records) == {}


def test_section_evidence_stays_selective():
    assert config.report_evidence_per_section() == 40
