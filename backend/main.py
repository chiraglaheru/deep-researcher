import json

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backend.research.planner import create_research_plan
from backend.research.searcher import search_subquestion as search_subquestion_tool
from backend.research.researcher import research_subquestion as run_researcher
from backend.research.report import synthesize_report


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
    try:
        plan = json.loads(create_research_plan(request.question))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail="Research planner returned invalid JSON",
        )

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

    final_report = synthesize_report(
        request.question,
        findings,
    )

    return {
        "question": request.question,
        "plan": plan,
        "report": final_report,
        "findings": findings,
    }