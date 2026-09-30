from fastapi import FastAPI
from pydantic import BaseModel

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
    return {
        "message": "Research started",
        "question": request.question
    }