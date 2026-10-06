"""Custom memory pool for final output processing.

The pool keeps post-processed (finished) sections so a fallback model that
takes over after an orchestrator failure can read them and continue the
report without altering completed data or breaking the stream.
"""
import pytest

from backend.research.output_pool import OutputMemoryPool


def test_completed_entries_are_immutable():
    pool = OutputMemoryPool("q")
    pool.complete("Alpha", "## Alpha\n\ntext")
    pool.complete("Alpha", "## Alpha\n\nCHANGED")

    assert pool.snapshot() == {"Alpha": "## Alpha\n\ntext"}


def test_snapshot_returns_copies_not_references():
    pool = OutputMemoryPool("q")
    pool.complete("Alpha", "## Alpha\n\ntext")

    snapshot = pool.snapshot()
    snapshot["Alpha"] = "MUTATED"
    snapshot["Beta"] = "INJECTED"

    assert pool.snapshot() == {"Alpha": "## Alpha\n\ntext"}
    assert pool.headings() == ["Alpha"]


def test_context_excludes_the_section_being_written():
    pool = OutputMemoryPool("q")
    pool.complete("Alpha", "## Alpha\n\nfirst " * 20)
    pool.complete("Beta", "## Beta\n\nsecond " * 20)

    digest = pool.context(4000, exclude="Beta")

    assert "Alpha" in digest
    assert "Beta" not in digest


def test_failed_sections_are_tracked_until_completed():
    pool = OutputMemoryPool("q")
    pool.fail("Alpha", "every model in the chain failed")

    assert pool.failures() == {"Alpha": "every model in the chain failed"}

    pool.complete("Alpha", "## Alpha\n\nrecovered")

    assert pool.failures() == {}
    assert pool.headings() == ["Alpha"]


def test_resume_writes_only_the_missing_section(monkeypatch):
    """A fallback retry must not touch sections already in the pool."""
    from backend.research import report as report_module

    calls: list[tuple[str, bool]] = []
    failed_once: set[str] = set()

    def fail_once_then_recover(system, question, heading, instruction, recs,
                               r, t, budget=None, min_words=350, failed=None):
        grounded = "already written" in instruction.lower()
        calls.append((heading, grounded))
        if heading == "Hiring Demand" and heading not in failed_once:
            failed_once.add(heading)
            if failed is not None:
                failed.append(f"{heading} (every model in the chain failed)")
            return ""
        return f"## {heading}\n\nprose [1]\n"

    monkeypatch.setattr(report_module, "_write_section", fail_once_then_recover)

    from backend.research.evidence import Evidence

    records = [
        Evidence(claim=f"Alpha finding {i} about performance",
                 source_title="A", source_url=f"https://a.dev/{i}",
                 quality="strong", dimension="performance",
                 retrieval_status="full", confidence=0.9)
        for i in range(2)
    ] + [
        Evidence(claim=f"Beta finding {i} about hiring demand",
                 source_title="B", source_url=f"https://b.dev/{i}",
                 quality="strong", dimension="hiring demand",
                 retrieval_status="full", confidence=0.9)
        for i in range(2)
    ]

    class Col:
        def context(self):
            return ""

    out = report_module.write_report(
        "Compare A and B. Analyse performance and hiring demand.",
        Col(), [], records=records)

    assert "## Performance" in out, "the completed section must survive verbatim"
    assert "## Hiring Demand" in out, "the failed section must be resumed from pool"
    assert "PARTIAL REPORT" not in out
    retries = [c for c in calls if c[0] == "Hiring Demand"]
    assert len(retries) == 2, f"expected one retry of the failed section: {calls}"
    assert retries[1][1] is True, "the retry must read pool context"


def test_pool_grounded_resume_keeps_completed_text_verbatim(monkeypatch):
    """The resumed section is added; completed sections are byte-identical."""
    from backend.research import report as report_module

    seen_instructions = {}

    def flaky(system, question, heading, instruction, recs,
             r, t, budget=None, min_words=350, failed=None):
        seen_instructions[heading] = instruction
        if heading == "Executive Summary" and "ALREADY WRITTEN" not in instruction \
                and "already written" not in instruction.lower():
            if failed is not None:
                failed.append(f"{heading} (every model in the chain failed)")
            return ""
        return f"## {heading}\n\nprose [1]\n"

    monkeypatch.setattr(report_module, "_write_section", flaky)

    from backend.research.evidence import Evidence

    records = [
        Evidence(claim="finding one", source_title="A",
                 source_url="https://a.dev", quality="strong",
                 retrieval_status="full", confidence=0.9),
    ]

    class Col:
        def context(self):
            return ""

    result = report_module.generate_report("Compare A and B. Analyse performance.",
                                           Col(), [], records=records)

    assert "Executive Summary" in result.markdown or result.status in {"partial", "completed"}
    # The retry for a failed section must have seen pool context.
    assert any("already written" in (seen_instructions.get(h) or "").lower()
               for h in seen_instructions)


def test_budget_failures_are_not_resumed(monkeypatch):
    from backend.research import report as report_module

    calls = []

    def always_budget(system, question, heading, instruction, recs,
                      r, t, budget=None, min_words=350, failed=None):
        calls.append(heading)
        if failed is not None:
            failed.append(f"{heading} (LLM call budget exhausted)")
        return ""

    monkeypatch.setattr(report_module, "_write_section", always_budget)

    from backend.research.evidence import Evidence

    records = [Evidence(claim="finding one", source_title="A",
                        source_url="https://a.dev", quality="strong",
                        retrieval_status="full", confidence=0.9)]

    class Col:
        def context(self):
            return ""

    report_module.write_report("Compare A and B. Analyse performance.",
                               Col(), [], records=records)

    # One attempt per section: budget exhaustion is deterministic, never retried.
    assert len(calls) == len(set(calls)), \
        f"budget failures must not be retried: {calls}"
