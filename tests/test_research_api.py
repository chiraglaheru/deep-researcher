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
def fake_planner(monkeypatch):
    """Replace the planner bound in main, so no Gemini call is ever made."""

    def install(plan=None, error=None):
        calls = []

        def create_research_plan(question):
            calls.append(question)
            if error is not None:
                raise error
            return plan if plan is not None else json.dumps(PLAN)

        monkeypatch.setattr(main, "create_research_plan", create_research_plan)
        return calls

    return install


def test_research_returns_the_question_and_the_parsed_plan(client, fake_planner):
    calls = fake_planner()

    response = client.post(
        "/research",
        json={"question": "Is coding still relevant in 2026?"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "question": "Is coding still relevant in 2026?",
        "plan": PLAN,
    }
    assert isinstance(response.json()["plan"], dict)
    assert calls == ["Is coding still relevant in 2026?"]


def test_research_returns_an_empty_subquestion_list_as_parsed_json(client, fake_planner):
    fake_planner(plan=json.dumps({"subquestions": []}))

    response = client.post("/research", json={"question": "anything?"})

    assert response.status_code == 200
    assert response.json() == {"question": "anything?", "plan": {"subquestions": []}}


def test_planner_failure_propagates(client, fake_planner):
    fake_planner(error=RuntimeError("planner is down"))

    with pytest.raises(RuntimeError):
        client.post("/research", json={"question": "Is coding still relevant in 2026?"})


def test_malformed_plan_json_propagates(client, fake_planner):
    fake_planner(plan="not json at all")

    with pytest.raises(json.JSONDecodeError):
        client.post("/research", json={"question": "Is coding still relevant in 2026?"})


def test_research_requires_a_question(client, fake_planner):
    calls = fake_planner()

    response = client.post("/research", json={})

    assert response.status_code == 422
    assert calls == []
