import json

from fastapi import FastAPI
from pydantic import BaseModel

from research.planner import create_research_plan

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
    plan = create_research_plan(request.question)
    return {
        "question": request.question,
        "plan" : json.loads(plan)
        
    }