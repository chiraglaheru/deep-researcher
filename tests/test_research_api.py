import json

import pytest
from fastapi.testclient import TestClient

from backend import main


PLAN = {
    "subquestions": [
        "What is the current state of the technology?",
        "Which open source projects implement it?",
        "What changed most recently?",
    ]
}


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def fake_research_components(monkeypatch):
    """Mock all research components so tests never call external APIs."""

    def install(plan=None, error=None):
        calls = []

        def create_research_plan(question):
            calls.append(question)

            if error is not None:
                raise error

            return plan if plan is not None else json.dumps(PLAN)

        def search_subquestion(subquestion):
            return [
                {
                    "title": subquestion,
                    "url": "https://example.com",
                    "snippet": "Test evidence",
                    "source": "google",
                }
            ]

        def research_subquestion(subquestion, search_results):
            return f"Finding for: {subquestion}"

        def synthesize_report(question, findings):
            return "Final research report"

        monkeypatch.setattr(
            main,
            "create_research_plan",
            create_research_plan,
        )

        monkeypatch.setattr(
            main,
            "search_subquestion_tool",
            search_subquestion,
        )

        monkeypatch.setattr(
            main,
            "run_researcher",
            research_subquestion,
        )

        monkeypatch.setattr(
            main,
            "synthesize_report",
            synthesize_report,
        )

        return calls

    return install


def test_research_returns_full_research_response(
    client,
    fake_research_components,
):
    calls = fake_research_components()

    response = client.post(
        "/research",
        json={"question": "Tell me about this technology"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["question"] == "Tell me about this technology"
    assert data["plan"] == PLAN

    assert data["findings"] == [
        {
            "subquestion": "What is the current state of the technology?",
            "finding": (
                "Finding for: What is the current state of the technology?"
            ),
        },
        {
            "subquestion": "Which open source projects implement it?",
            "finding": (
                "Finding for: Which open source projects implement it?"
            ),
        },
        {
            "subquestion": "What changed most recently?",
            "finding": "Finding for: What changed most recently?",
        },
    ]

    assert data["report"] == "Final research report"

    assert calls == ["Tell me about this technology"]


def test_research_handles_empty_subquestion_list(
    client,
    fake_research_components,
):
    fake_research_components(
        plan=json.dumps({"subquestions": []})
    )

    response = client.post(
        "/research",
        json={"question": "anything?"},
    )

    assert response.status_code == 200

    assert response.json() == {
        "question": "anything?",
        "plan": {"subquestions": []},
        "findings": [],
        "report": "Final research report",
    }


def test_planner_failure_propagates(
    client,
    fake_research_components,
):
    fake_research_components(
        error=RuntimeError("planner is down")
    )

    with pytest.raises(RuntimeError):
        client.post(
            "/research",
            json={"question": "Is coding still relevant in 2026?"},
        )


def test_malformed_plan_returns_502(
    client,
    fake_research_components,
):
    fake_research_components(
        plan="not json at all"
    )

    response = client.post(
        "/research",
        json={"question": "Is coding still relevant in 2026?"},
    )

    assert response.status_code == 502

    assert response.json() == {
        "detail": "Research planner returned invalid JSON"
    }


def test_research_requires_a_question(
    client,
    fake_research_components,
):
    calls = fake_research_components()

    response = client.post(
        "/research",
        json={},
    )

    assert response.status_code == 422
    assert calls == []
