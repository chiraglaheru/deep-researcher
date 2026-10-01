"""LangGraph orchestration:
planner -> [search_worker x N in parallel] -> collect -> gap_check --(gaps)--> search_worker ...
                                                          `--(done)--> contradiction_check -> synthesizer
"""
import asyncio, operator
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from .planner import make_plan, SOURCES
from .searcher import search
from .collector import Collector
from .report import write_report
from .llm import ask

GAP_SYS = """You are a research lead. Given the question and evidence so far, decide if more searching
is needed. Only request searches that fill a SPECIFIC gap: no primary source, one-sided evidence,
only stale/undated results, missing recent news, or an unanswered sub-question. Return JSON only:
{"sufficient":true|false,"missing":"what is missing","follow_ups":[{"source":"web|news|scholar|github","query":"..."}]}
At most 3 follow_ups."""


class State(TypedDict, total=False):
    question: str
    max_rounds: int
    round: int
    plan: dict
    pending: list                         # searches to run next
    seen: list                            # "source|query" already run
    raw: Annotated[list, operator.add]    # parallel workers append here
    log: Annotated[list, operator.add]
    evidence: list
    gap: dict
    contradictions: list
    report: str


def _col(state) -> Collector:
    col = Collector()
    col.add([dict(r) for r in state.get("raw", [])])
    return col


def planner(state):
    plan = make_plan(state["question"])
    pending = [{"source": s["source"], "query": s["query"]}
               for sq in plan.get("subquestions", []) for s in sq["searches"]]
    return {"plan": plan, "pending": pending, "round": 1,
            "seen": [f'{p["source"]}|{p["query"]}' for p in pending]}


def route(state):
    """Fan out one parallel worker per pending search, or move on if nothing is pending."""
    if state.get("pending"):
        return [Send("search_worker", p) for p in state["pending"]]
    return "contradiction_check"


async def search_worker(task):
    try:
        res, err = await asyncio.to_thread(search, task["source"], task["query"]), None
    except Exception as e:
        res, err = [], str(e)
    return {"raw": res, "log": [{**task, "count": len(res), "error": err}]}


def collect(state):
    return {"evidence": _col(state).list()}


def gap_check(state):
    rnd, seen = state["round"], list(state["seen"])
    if rnd >= state["max_rounds"]:
        return {"gap": {"sufficient": False, "missing": "round limit reached"}, "pending": []}
    gap = ask(GAP_SYS, f"Question: {state['question']}\n\nEvidence:\n{_col(state).context()}", True)
    new = []
    if not gap.get("sufficient"):
        for f in gap.get("follow_ups", [])[:3]:
            key = f'{f.get("source")}|{f.get("query")}'
            if f.get("source") in SOURCES and f.get("query") and key not in seen:
                new.append({"source": f["source"], "query": f["query"]})
                seen.append(key)
    return {"gap": gap, "pending": new, "seen": seen, "round": rnd + 1}


def contradiction_check(state):
    return {"contradictions": _col(state).find_contradictions()}


def synthesizer(state):
    return {"report": write_report(state["question"], _col(state), state["contradictions"])}


def build_graph():
    g = StateGraph(State)
    for name, fn in [("planner", planner), ("search_worker", search_worker), ("collect", collect),
                     ("gap_check", gap_check), ("contradiction_check", contradiction_check),
                     ("synthesizer", synthesizer)]:
        g.add_node(name, fn)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", route, ["search_worker", "contradiction_check"])
    g.add_edge("search_worker", "collect")
    g.add_edge("collect", "gap_check")
    g.add_conditional_edges("gap_check", route, ["search_worker", "contradiction_check"])
    g.add_edge("contradiction_check", "synthesizer")
    g.add_edge("synthesizer", END)
    return g.compile()


graph = build_graph()
