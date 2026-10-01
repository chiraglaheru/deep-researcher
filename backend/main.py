import json

from fastapi import FastAPI
from pydantic import BaseModel

from backend.research.planner import create_research_plan
from backend.research.searcher import search_subquestion as search_subquestion_tool
from backend.research.researcher import research_subquestion as run_researcher

app = FastAPI()


class ResearchRequest(BaseModel):
    question: str


@app.get("/")
def home():
    return {"message": "Deep Researcher API is running!"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/research")
def research(request: ResearchRequest):
    plan = json.loads(create_research_plan(request.question))

    findings = []

    for subquestion in plan["subquestions"]:
        search_results = search_subquestion_tool(subquestion)

        finding = run_researcher(
            subquestion,
            search_results,
        )

        findings.append(
            {
                "subquestion": subquestion,
                "finding": finding,
            }
        )

    return {
        "question": request.question,
        "plan": plan,
        "findings": findings,
    }