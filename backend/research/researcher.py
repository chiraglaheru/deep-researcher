"""Streaming bridge from the graph to the SSE contract.

The event types the original pipeline emitted -- plan, results, evidence, gap,
contradictions, report -- are all still emitted, with the same payloads. The
retrieval and analysis stages add new events around them so the browser can show
what the pipeline is actually doing instead of sitting on a spinner for minutes.

"evidence" still reports the number of collected sources, as before; the deeper
structured findings are reported under "analysis" so nothing existing shifts
meaning underneath a client that already works.
"""
from .graph import graph
from . import config


async def deep_research(question: str, max_rounds: int = 3):
    evidence = []
    stats: dict = {}
    init = {
        "question": question,
        "max_rounds": max_rounds,
        "raw": [],
        "log": [],
        "notes": [],
    }

    async for chunk in graph.astream(init, stream_mode="updates"):
        for node, upd in chunk.items():
            if node == "planner":
                yield {"type": "plan", "data": upd["plan"]}

            elif node == "search_worker":
                yield {"type": "results", **upd["log"][0]}

            elif node == "collect":
                evidence = upd["evidence"]
                yield {"type": "evidence", "count": len(evidence)}

            elif node == "retrieve":
                stats = upd.get("retrieval_stats") or {}
                if stats.get("enabled"):
                    yield {
                        "type": "retrieval",
                        "data": {
                            "attempted": stats.get("attempted", 0),
                            "retrieved": stats.get("retrieved", 0),
                            "full_text": stats.get("full_text", 0),
                            "partial": stats.get("partial", 0),
                            "metadata_only": stats.get("metadata_only", 0),
                            "failed": stats.get("failed", 0),
                            "words": stats.get("total_words", 0),
                            "chunks": stats.get("chunks", 0),
                            "references_followed": stats.get("references_followed", 0),
                            "sources": stats.get("sources", [])[:40],
                        },
                    }
                else:
                    yield {"type": "retrieval", "data": {
                        "enabled": False,
                        "note": stats.get("note", "full-text retrieval unavailable")}}

            elif node == "analyse":
                records = upd.get("extracted") or []
                yield {
                    "type": "analysis",
                    "data": {
                        "records": len(records),
                        "dimensions": sorted({r.dimension for r in records if r.dimension}),
                        "sources_summarised": len(upd.get("source_synth") or {}),
                        "notes": list(upd.get("notes") or [])[:8],
                    },
                }

            elif node == "gap_check":
                yield {"type": "gap", "data": upd["gap"]}

            elif node == "contradiction_check":
                yield {"type": "contradictions", "data": upd["contradictions"]}

            elif node == "synthesizer":
                export = upd.get("export") or {}
                if not export:
                    export = _export_meta(question)
                completion = upd.get("status") or {"status": "completed",
                                                   "missing_sections": [],
                                                   "headline": "COMPLETE REPORT"}
                # Emitted as its own event so a client can render the state
                # before it has the (potentially very large) report body.
                yield {"type": "status", "data": completion}
                yield {
                    "type": "report",
                    "data": upd["report"],
                    "sources": evidence,
                    "stats": upd.get("evidence_stats") or {},
                    "retrieval": stats,
                    "export": export,
                    "status": completion,
                }


def _export_meta(question: str) -> dict:
    """Where the markdown export was written, so the UI can offer a download."""
    from .export import last_export
    return last_export(question)