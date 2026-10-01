import json
from types import SimpleNamespace

import pytest

from backend.research.planner import create_research_plan

VALID_PLAN = {
    "subquestions": [
        "What is the current state of Rust game engines?",
        "Which open source engines exist?",
    ]
}


class FakeModels:
    def __init__(self, text, error=None):
        self.text = text
        self.error = error
        self.calls = []

    def generate_content(self, model, contents):
        self.calls.append({"model": model, "contents": contents})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text)


class FakeClient:
    def __init__(self, text, error=None):
        self.models = FakeModels(text, error)


@pytest.fixture
def gemini(monkeypatch):
    """Swap the module-level Gemini client, so no API call is ever made."""

    def install(text=None, error=None):
        fake = FakeClient(text, error)
        monkeypatch.setattr("backend.research.planner.client", fake)
        return fake

    return install


def test_valid_response_returns_the_json_string(gemini):
    fake = gemini(text=json.dumps(VALID_PLAN))

    plan = create_research_plan("Is coding still relevant in 2026?")

    assert plan == json.dumps(VALID_PLAN)
    assert json.loads(plan) == VALID_PLAN
    assert fake.models.calls[0]["model"] == "gemini-3.5-flash-lite"
    assert "Is coding still relevant in 2026?" in fake.models.calls[0]["contents"]


def test_malformed_json_raises_value_error(gemini):
    gemini(text="Sure! Here are some subquestions: what is Rust?")

    with pytest.raises(ValueError, match="malformed JSON"):
        create_research_plan("Is coding still relevant in 2026?")


def test_missing_subquestions_raises_value_error(gemini):
    gemini(text=json.dumps({"steps": ["research", "summarize"]}))

    with pytest.raises(ValueError, match="subquestions"):
        create_research_plan("Is coding still relevant in 2026?")


def test_subquestions_must_be_a_list(gemini):
    gemini(text=json.dumps({"subquestions": "just one question"}))

    with pytest.raises(ValueError, match="must be a list"):
        create_research_plan("Is coding still relevant in 2026?")


@pytest.mark.parametrize(
    "subquestions",
    [
        ["a valid question", 42],
        ["a valid question", {"question": "nested"}],
        ["   "],
        ["a valid question", "\t\n"],
    ],
)
def test_non_string_or_blank_subquestions_raise_value_error(gemini, subquestions):
    gemini(text=json.dumps({"subquestions": subquestions}))

    with pytest.raises(ValueError, match="non-empty string"):
        create_research_plan("Is coding still relevant in 2026?")


def test_plan_must_be_a_json_object(gemini):
    gemini(text=json.dumps(["a valid question"]))

    with pytest.raises(ValueError, match="must be a JSON object"):
        create_research_plan("Is coding still relevant in 2026?")


def test_empty_response_raises_value_error(gemini):
    gemini(text="")

    with pytest.raises(ValueError, match="empty response"):
        create_research_plan("Is coding still relevant in 2026?")


def test_empty_subquestion_list_is_accepted_as_written(gemini):
    gemini(text=json.dumps({"subquestions": []}))

    plan = create_research_plan("Is coding still relevant in 2026?")

    assert json.loads(plan) == {"subquestions": []}


def test_extra_plan_fields_are_preserved(gemini):
    gemini(text=json.dumps({"subquestions": ["a valid question"], "notes": "kept"}))

    plan = create_research_plan("Is coding still relevant in 2026?")

    assert json.loads(plan) == {
        "subquestions": ["a valid question"],
        "notes": "kept",
    }
