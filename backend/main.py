import json

from fastapi import FastAPI, HTTPException
from google.genai.errors import APIError
from pydantic import BaseModel

from backend.research.planner import create_research_plan
from backend.research.searcher import search_subquestion as search_subquestion_tool
from backend.research.researcher import research_subquestion as run_researcher
from backend.research.report import synthesize_report
from backend.tools import github, news, serpapi

SEARCH_ERRORS = (serpapi.SerpApiError, github.GitHubError, news.SerpApiError)
RESEARCH_ERRORS = (APIError,)


app = FastAPI()


class ResearchRequest(BaseModel):
    question: str


@app.get("/")
def home():
    return {"message": "Deep Researcher API is running!"}


@app.get("/health")
def health():
    return {"status": "ok"}


def _failed_subquestion(subquestion: str, stage: str, exc: Exception) -> dict:
    return {
        "subquestion": subquestion,
        "finding": None,
        "error": f"{stage} failed: {type(exc).__name__}: {exc}",
    }


def _research_subquestion(subquestion: str) -> dict:
    """Research one subquestion, turning a documented API failure into an entry.

    Only the search and researcher error types each step documents are caught,
    so a programming bug still propagates.
    """
    try:
        search_results = search_subquestion_tool(subquestion)
    except SEARCH_ERRORS as exc:
        return _failed_subquestion(subquestion, "search", exc)

    try:
        finding = run_researcher(subquestion, search_results)
    except RESEARCH_ERRORS as exc:
        return _failed_subquestion(subquestion, "researcher", exc)

    return {
        "subquestion": subquestion,
        "finding": finding,
    }


@app.post("/research")
def research(request: ResearchRequest):
    try:
        plan = json.loads(create_research_plan(request.question))
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Research planner returned invalid JSON",
        )

    findings = [
        _research_subquestion(subquestion)
        for subquestion in plan["subquestions"]
    ]

    successful = [item for item in findings if item["finding"] is not None]

    final_report = synthesize_report(
        request.question,
        successful,
    )

    return {
        "question": request.question,
        "plan": plan,
        "report": final_report,
        "findings": findings,
    }