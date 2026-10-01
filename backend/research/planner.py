import datetime
from .llm import ask

SOURCES = ("web", "news", "scholar", "github")

SYSTEM = """You are a research planner. Break the user's question into 3-5 sub-questions.
For each, give 1-2 concrete search queries and the best source for each:
- web: docs, blogs, comparisons   - news: recent events/announcements
- scholar: papers, benchmarks     - github: repos, issues, ecosystem activity
Return JSON only:
{"subquestions":[{"question":"...","searches":[{"source":"web","query":"..."}]}]}
Today's date is {today}. Include the current year in queries where recency matters."""


def make_plan(question: str) -> dict:
    plan = ask(SYSTEM.replace("{today}", str(datetime.date.today())), question, json_mode=True)
    for sq in plan.get("subquestions", []):
        sq["searches"] = [s for s in sq.get("searches", []) if s.get("source") in SOURCES and s.get("query")]
    return plan
