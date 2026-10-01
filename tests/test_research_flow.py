from fastapi.testclient import TestClient

from backend import main


client = TestClient(main.app)

def fake_report(question, findings):
    return "Final research report"

def test_research_flow(monkeypatch):
    def fake_plan(question):
        return '{"subquestions": ["What is FastAPI?", "Why use FastAPI?"]}'

    def fake_search(subquestion):
        return [
            {
                "title": subquestion,
                "url": "https://example.com",
                "snippet": "Test evidence",
                "source": "google",
            }
        ]

    

    def fake_research(subquestion, search_results):
        return f"Finding for: {subquestion}"

    monkeypatch.setattr(main, "create_research_plan", fake_plan)
    monkeypatch.setattr(main, "search_subquestion_tool", fake_search)
    monkeypatch.setattr(main, "run_researcher", fake_research)
    monkeypatch.setattr(main, "synthesize_report", fake_report)

    response = client.post(
        "/research",
        json={"question": "Tell me about FastAPI"},
    )

    assert response.status_code == 200
    assert response.json()["report"] == "Final research report"

    data = response.json()

    assert data["question"] == "Tell me about FastAPI"

    assert data["plan"]["subquestions"] == [
        "What is FastAPI?",
        "Why use FastAPI?",
    ]

    assert data["findings"] == [
        {
            "subquestion": "What is FastAPI?",
            "finding": "Finding for: What is FastAPI?",
        },
        {
            "subquestion": "Why use FastAPI?",
            "finding": "Finding for: Why use FastAPI?",
        },
    ]