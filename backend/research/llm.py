"""LLM access with per-model retries and a fallback chain across models."""
import json
import logging
import os
import random
import re
import time

import litellm

try:  # litellm's provider exceptions subclass openai's hierarchy, not litellm.APIError
    from openai import OpenAIError as ProviderError
except ImportError:  # pragma: no cover - openai ships with litellm
    ProviderError = litellm.APIError

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini/gemini-3.8-flash"  # any litellm model string works

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
CALL_TIMEOUT_SECONDS = 60
LOG_MESSAGE_CHARS = 120

# Provider failures that are worth retrying on the same model.
TRANSIENT = (
    litellm.ServiceUnavailableError,
    litellm.InternalServerError,
    litellm.APIConnectionError,
    litellm.Timeout,
)


class LLMChainError(RuntimeError):
    """Every model in the chain failed."""


def _models(role: str) -> list[str]:
    """Role-specific model first, then the default, then LLM_FALLBACKS (comma-separated).

    Read from the environment on every call so a changed .env takes effect
    without restarting, and so tests can drive the chain.
    """
    default = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    chain = [os.getenv(f"LLM_MODEL_{role.upper()}", default), default]
    chain += [m.strip() for m in os.getenv("LLM_FALLBACKS", "").split(",") if m.strip()]
    return list(dict.fromkeys(chain))  # dedupe, keep order


def sleep(seconds: float) -> None:
    """Indirection so tests can patch out the wait."""
    time.sleep(seconds)


def _backoff(attempt: int) -> float:
    """1s, 2s, 4s plus jitter."""
    return BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:LOG_MESSAGE_CHARS]}"


def _retryable(exc: BaseException) -> bool:
    """True for transient provider failures and 5xx responses."""
    if isinstance(exc, TRANSIENT):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 500


# ---------------------------------------------------------------------------
# Mock mode (dev/test only). Off unless LLM_MOCK=1.
# ---------------------------------------------------------------------------

_MOCK_MARKER = "followup-pass-2"
_MOCK_WARNED = False


def _mock_enabled() -> bool:
    return os.environ.get("LLM_MOCK", "") == "1"


def _mock_kind(system: str, role: str) -> str:
    """Planner and gap_check share role='default', so sniff the system prompt."""
    if role == "judge":
        return "judge"
    if role == "synth":
        return "synth"
    text = system.lower()
    if "research lead" in text:
        return "gap"
    if "checking evidence for contradictions" in text:
        return "judge"
    if "you write research reports" in text:
        return "synth"
    return "planner"


def _mock_topic(user: str) -> str:
    """The research question, for planner-style prompts."""
    for line in user.splitlines():
        if line.startswith("Question:"):
            return line.split(":", 1)[1].strip()
    return user.strip().splitlines()[0][:80] if user.strip() else "the topic"


def _mock_evidence_ids(user: str) -> list[int]:
    return [int(n) for n in re.findall(r"\[(\d+)\]", user)]


def _mock_planner(question: str) -> dict:
    topic = question.strip().rstrip("?") or "the topic"
    return {"subquestions": [
        {"question": f"What is {topic}?",
         "searches": [{"source": "web", "query": f"{topic} overview"},
                      {"source": "news", "query": f"{topic} latest news"}]},
        {"question": f"How does {topic} compare to the alternatives?",
         "searches": [{"source": "scholar", "query": f"{topic} benchmark"},
                      {"source": "web", "query": f"{topic} vs alternatives comparison"}]},
        {"question": f"What is the current state of {topic}?",
         "searches": [{"source": "github", "query": f"{topic} repository"}]},
    ]}


def _mock_gap(user: str) -> dict:
    """First round asks for more; once the follow-up results are in the evidence, stop."""
    if _MOCK_MARKER in user:
        return {"sufficient": True, "missing": "", "follow_ups": []}
    topic = _mock_topic(user).rstrip("?") or "the topic"
    return {"sufficient": False,
            "missing": "no primary source for the follow-up question",
            "follow_ups": [
                {"source": "news", "query": f"{topic} {_MOCK_MARKER}"},
                {"source": "web", "query": f"{topic} {_MOCK_MARKER} primary source"},
            ]}


def _mock_judge(user: str) -> dict:
    ids = _mock_evidence_ids(user)
    if len(ids) < 2:
        return {"contradictions": []}
    a, b = ids[0], ids[1]
    return {"contradictions": [
        {"topic": "maturity and adoption",
         "side_a": "The first source calls it widely adopted in production.",
         "sources_a": [a],
         "side_b": "The second source says adoption is still limited to experiments.",
         "sources_b": [b]},
    ]}


def _mock_synth(user: str) -> str:
    ids = _mock_evidence_ids(user)
    picks = ids[:3] or [1]
    cite = ", ".join(f"[{n}]" for n in picks)
    topic = _mock_topic(user) or "the topic"
    return (
        f"## Short answer\n"
        f"Based on the gathered evidence, {topic} is best approached incrementally: "
        f"start with the well-sourced basics {cite}, validate against the documented "
        f"limitations, and only then commit to the more speculative directions. "
        f"I recommend treating it as a good default rather than a universal answer.\n\n"
        f"## Key findings\n"
        f"* The primary documentation describes the core mechanics clearly {cite}.\n"
        f"* Independent write-ups agree on the fundamentals {cite}.\n"
        f"* Recent material suggests the ecosystem is still moving {cite}.\n\n"
        f"## Where sources disagree\n"
        f"One disagreement was detected between the collected sources: they differ on "
        f"how mature the ecosystem is. Treat maturity claims as unsettled.\n\n"
        f"## Freshness & caveats\n"
        f"Some sources are older than the freshness threshold and are marked STALE in "
        f"the evidence list. The mock dataset is synthetic, so these notes exist only "
        f"to exercise the report pipeline end to end."
    )


def _mock_reply(kind: str, user: str, json_mode: bool) -> object:
    if kind == "gap":
        return _mock_gap(user)
    if kind == "judge":
        return _mock_judge(user)
    if kind == "synth":
        return _mock_synth(user)
    return _mock_planner(user)


def _mock(kind: str, user: str, json_mode: bool):
    global _MOCK_WARNED

    if not _MOCK_WARNED:
        _MOCK_WARNED = True
        log.warning("LLM MOCK MODE ACTIVE: no real model calls")

    failing = os.environ.get("LLM_MOCK_FAIL", "").strip()
    if failing and (failing == "all" or failing == kind):
        raise LLMChainError(f"LLM mock failure for role: {kind}")

    delay_ms = os.environ.get("LLM_MOCK_DELAY_MS", "400")
    try:
        delay = max(0.0, float(delay_ms) / 1000.0)
    except ValueError:
        delay = 0.4
    if delay:
        sleep(delay)

    return _mock_reply(kind, user, json_mode)


def ask(system: str, user: str, json_mode: bool = False, role: str = "default"):
    if _mock_enabled():
        return _mock(_mock_kind(system, role), user, json_mode)

    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    chain = _models(role)
    last: BaseException | None = None

    for model in chain:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                r = litellm.completion(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    timeout=CALL_TIMEOUT_SECONDS,
                    num_retries=0,  # retries are handled here, not by litellm
                    **kw,
                )
                out = r.choices[0].message.content
                return json.loads(out) if json_mode else out

            except litellm.RateLimitError as exc:
                # A quota will not clear on a second immediate try: move on.
                last = exc
                log.warning("llm %s attempt %d/%d failed %s", model, attempt, MAX_ATTEMPTS, _describe(exc))
                break

            except ProviderError as exc:
                last = exc
                log.warning("llm %s attempt %d/%d failed %s", model, attempt, MAX_ATTEMPTS, _describe(exc))
                if _retryable(exc) and attempt < MAX_ATTEMPTS:
                    sleep(_backoff(attempt))
                    continue
                break

            # Non-provider exceptions (TypeError, KeyError, ...) propagate on purpose.

    raise LLMChainError(
        f"All {len(chain)} models failed; last error {_describe(last)}"
    ) from last