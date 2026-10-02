"""Retry and fallback behaviour for backend.research.llm.ask."""

import pytest
import litellm

from backend.research import llm


class FakeResponse:
    def __init__(self, content):
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Never actually wait between retries."""
    monkeypatch.setattr(llm, "sleep", lambda seconds: None)


def _service_unavailable():
    return litellm.ServiceUnavailableError("503 high demand", None, None)


def _rate_limited():
    return litellm.RateLimitError("429 quota exhausted", None, None)


def _unauthorized():
    return litellm.AuthenticationError("401 bad key", None, None)


def test_transient_503_is_retried_on_the_same_model(monkeypatch):
    """503 twice, then success: the caller gets the result."""
    calls = []
    errors = [_service_unavailable(), _service_unavailable()]

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if errors:
            raise errors.pop(0)
        return FakeResponse("recovered")

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    assert llm.ask("sys", "user") == "recovered"
    assert calls == ["gemini/gemini-3.8-flash"] * 3


def test_retries_stop_after_max_attempts(monkeypatch):
    """A model that never recovers is retried exactly MAX_ATTEMPTS times."""
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        raise _service_unavailable()

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "gemini/gemini-3.7-flash")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    with pytest.raises(llm.LLMChainError):
        llm.ask("sys", "user")

    assert calls.count("gemini/gemini-3.8-flash") == llm.MAX_ATTEMPTS
    assert calls.count("gemini/gemini-3.7-flash") == llm.MAX_ATTEMPTS


def test_rate_limit_moves_to_next_model_without_retrying(monkeypatch):
    """A quota will not clear: at most one attempt on that model."""
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "gemini/gemini-3.8-flash":
            raise _rate_limited()
        return FakeResponse("second model answered")

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "gemini/gemini-3.7-flash")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    assert llm.ask("sys", "user") == "second model answered"
    assert calls == ["gemini/gemini-3.8-flash", "gemini/gemini-3.7-flash"]


def test_authentication_error_moves_to_next_model_immediately(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "gemini/gemini-3.8-flash":
            raise _unauthorized()
        return FakeResponse("fell back")

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "gemini/gemini-3.7-flash")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    assert llm.ask("sys", "user") == "fell back"
    assert calls == ["gemini/gemini-3.8-flash", "gemini/gemini-3.7-flash"]


def test_not_found_error_moves_to_next_model_immediately(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "gemini/gemini-3.8-flash":
            raise litellm.NotFoundError("404 retired", None, None)
        return FakeResponse("fell back")

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "gemini/gemini-3.7-flash")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    assert llm.ask("sys", "user") == "fell back"
    assert len(calls) == 2


def test_every_model_failing_names_the_last_error_class(monkeypatch):
    def fake_completion(**kwargs):
        raise _unauthorized()

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "gemini/gemini-3.7-flash,gemini/gemini-3.6-flash")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    with pytest.raises(llm.LLMChainError) as excinfo:
        llm.ask("sys", "user")

    message = str(excinfo.value)
    assert "All 3 models failed" in message
    assert "AuthenticationError" in message


def test_programming_errors_are_not_swallowed(monkeypatch):
    """A TypeError inside the call must surface, not become a fallback."""

    def fake_completion(**kwargs):
        raise TypeError("result shape is wrong")

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "gemini/gemini-3.7-flash")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    with pytest.raises(TypeError, match="result shape is wrong"):
        llm.ask("sys", "user")


def test_json_mode_parses_the_response(monkeypatch):
    def fake_completion(**kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        return FakeResponse('{"contradictions": []}')

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    assert llm.ask("sys", "user", json_mode=True) == {"contradictions": []}


def test_litellm_internal_retries_are_disabled(monkeypatch):
    seen = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return FakeResponse("ok")

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    llm.ask("sys", "user")

    assert seen["num_retries"] == 0
    assert seen["timeout"] == llm.CALL_TIMEOUT_SECONDS
    assert "temperature" not in seen


def test_role_specific_model_is_preferred(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        return FakeResponse("ok")

    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("LLM_MODEL_SYNTH", "gemini/gemini-3.5-flash")
    monkeypatch.setenv("LLM_FALLBACKS", "")
    monkeypatch.setattr(litellm, "completion", fake_completion)

    llm.ask("sys", "user", role="synth")

    assert calls == ["gemini/gemini-3.5-flash"]