from .graph import graph


async def deep_research(question: str, max_rounds: int = 3):
    evidence = []
    init = {"question": question, "max_rounds": max_rounds, "raw": [], "log": []}
    async for chunk in graph.astream(init, stream_mode="updates"):
        for node, upd in chunk.items():
            if node == "planner":
                yield {"type": "plan", "data": upd["plan"]}
            elif node == "search_worker":
                yield {"type": "results", **upd["log"][0]}
            elif node == "collect":
                evidence = upd["evidence"]
                yield {"type": "evidence", "count": len(evidence)}
            elif node == "gap_check":
                yield {"type": "gap", "data": upd["gap"]}
            elif node == "contradiction_check":
                yield {"type": "contradictions", "data": upd["contradictions"]}
            elif node == "synthesizer":
                yield {"type": "report", "data": upd["report"], "sources": evidence}
