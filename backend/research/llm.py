"""LLM access with per-model retries and a fallback chain across models."""
import json
import logging
import os
import random
import re
import threading
import time

import litellm

try:  # litellm's provider exceptions subclass openai's hierarchy, not litellm.APIError
    from openai import OpenAIError as ProviderError
except ImportError:  # pragma: no cover - openai ships with litellm
    ProviderError = litellm.APIError

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini/gemini-3.8-flash"  # any litellm model string works

MAX_ATTEMPTS = 2
BACKOFF_BASE_SECONDS = 1.0
CALL_TIMEOUT_SECONDS = 60
LOG_MESSAGE_CHARS = 120
LOG_MESSAGE_CHARS_RATE_LIMIT = 300
# When every model is cooling down, wait this long for the soonest if it is
# close enough that waiting beats failing outright.
WAIT_FOR_COOLDOWN_S = 10.0

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


def _now() -> float:
    """Monotonic clock, indirected so tests can move time without waiting."""
    return time.monotonic()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def max_attempts() -> int:
    return max(1, int(_env_float("LLM_MAX_ATTEMPTS", MAX_ATTEMPTS)))


def daily_cooldown_s() -> float:
    return _env_float("LLM_DAILY_COOLDOWN_S", 3600.0)


def per_minute_cooldown_s() -> float:
    return _env_float("LLM_PER_MINUTE_COOLDOWN_S", 60.0)


def overload_cooldown_s() -> float:
    return _env_float("LLM_OVERLOAD_COOLDOWN_S", 30.0)


# --- model health ---------------------------------------------------------
#
# Per-process record of which models are currently unusable, so a model that
# just returned 429/503 is not immediately retried by the next pipeline stage.

_health_lock = threading.Lock()
_cooldowns: dict[str, tuple[float, str]] = {}   # model -> (until, reason)


def reset_health() -> None:
    """Forget every cooldown. Intended for tests."""
    with _health_lock:
        _cooldowns.clear()


def _cool_down(model: str, seconds: float, reason: str) -> None:
    with _health_lock:
        _cooldowns[model] = (_now() + seconds, reason)


def _clear_cooldown(model: str) -> None:
    with _health_lock:
        _cooldowns.pop(model, None)


def _remaining(model: str) -> tuple[float, str] | None:
    """Seconds left on this model's cooldown, or None if it is usable."""
    with _health_lock:
        entry = _cooldowns.get(model)
        if entry is None:
            return None
        until, reason = entry
        left = until - _now()
        if left <= 0:
            _cooldowns.pop(model, None)
            return None
        return left, reason


# --- 429 classification ----------------------------------------------------
#
# Google's RESOURCE_EXHAUSTED bodies carry a quota id naming the window
# ("PerDay" / "PerMinute") and sometimes a retry delay, e.g.
#   "retryDelay": "34s"   or   "Please retry in 34s"
# UNVERIFIED against a live 429: the parser accepts several spellings because
# only a truncated prefix has been observed on this project.

_DAILY = re.compile(r"per[\s_-]?day|daily", re.I)
_MINUTE = re.compile(r"per[\s_-]?minute|per[\s_-]?min", re.I)
_DELAY_FIELD = re.compile(r'"retryDelay"\s*:\s*"?(\d+)\s*([smh])', re.I)
_RETRY_IN = re.compile(r"retry in\s+(\d+)\s*([smh])", re.I)
_DELAY_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0}


def _parse_retry_delay(message: str) -> float | None:
    for pattern in (_DELAY_FIELD, _RETRY_IN):
        match = pattern.search(message)
        if match:
            return float(match.group(1)) * _DELAY_UNITS[match.group(2).lower()]
    return None


def classify_rate_limit(message: str) -> tuple[str, float | None]:
    """Return (kind, retry_delay_seconds). kind is 'daily' or 'per-minute'."""
    delay = _parse_retry_delay(message)
    if _DAILY.search(message):
        return "daily", delay
    return "per-minute", delay


def _rate_limit_summary(exc: BaseException) -> str:
    kind, delay = classify_rate_limit(str(exc))
    summary = f"429 {kind} quota"
    if delay is not None:
        summary += f", retry in {delay:.0f}s"
    return summary


def _chain_for_call(role: str) -> list[str]:
    """The chain for this call, with cooling-down models skipped."""
    chain = _models(role)
    active, cooling = [], []

    for model in chain:
        left = _remaining(model)
        if left is None:
            active.append(model)
        else:
            seconds, reason = left
            cooling.append((model, seconds, reason))
            log.info("skipping %s (cooldown %.0fs: %s)", model, seconds, reason)

    if not active and cooling:
        # Everything is cooling down. Rather than fail outright, try the model
        # whose cooldown ends soonest, waiting first if it is nearly ready.
        model, seconds, reason = min(cooling, key=lambda entry: entry[1])
        log.info(
            "all %d models cooling down; trying %s (%.0fs left) anyway",
            len(cooling), model, seconds,
        )
        if seconds <= WAIT_FOR_COOLDOWN_S:
            sleep(seconds)
        active.append(model)

    return active


def _backoff(attempt: int) -> float:
    """1s, 2s, 4s plus jitter."""
    return BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)


def _describe(exc: BaseException, chars: int = LOG_MESSAGE_CHARS) -> str:
    return f"{type(exc).__name__}: {str(exc)[:chars]}"


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
    picks = ids[:4] or [1]
    combined = f"[{', '.join(str(n) for n in picks)}]"      # real-model style
    separate = ", ".join(f"[{n}]" for n in picks[:2])        # legacy style
    topic = _mock_topic(user) or "the topic"
    return (
        f"## Short answer\n"
        f"Based on the gathered evidence, {topic} is best approached incrementally: "
        f"start with the well-sourced basics, validate against the documented "
        f"limitations, and only then commit to the more speculative directions. "
        f"I recommend treating it as a good default rather than a universal answer "
        f"{separate}.\n\n"
        f"## Key findings\n"
        f"* The primary documentation describes the core mechanics clearly {combined}.\n"
        f"* Independent write-ups agree on the fundamentals {combined}.\n"
        f"* Recent material suggests the ecosystem is still moving {combined}.\n\n"
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
    configured = _models(role)
    chain = _chain_for_call(role)
    attempts = max_attempts()
    last: BaseException | None = None

    for model in chain:
        for attempt in range(1, attempts + 1):
            try:
                r = litellm.completion(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    timeout=CALL_TIMEOUT_SECONDS,
                    num_retries=0,  # retries are handled here, not by litellm
                    **kw,
                )
                out = r.choices[0].message.content
                _clear_cooldown(model)
                return json.loads(out) if json_mode else out

            except litellm.RateLimitError as exc:
                # A quota will not clear on a second immediate try: move on,
                # and keep this model out of later stages until it recovers.
                last = exc
                kind, delay = classify_rate_limit(str(exc))
                if kind == "daily":
                    seconds, reason = daily_cooldown_s(), "per-day quota"
                else:
                    seconds, reason = (delay or per_minute_cooldown_s()), "per-minute quota"
                _cool_down(model, seconds, reason)
                log.warning(
                    "llm %s attempt %d/%d failed %s | %s | cooling down %.0fs",
                    model, attempt, attempts,
                    _describe(exc, LOG_MESSAGE_CHARS_RATE_LIMIT),
                    _rate_limit_summary(exc), seconds,
                )
                break

            except ProviderError as exc:
                last = exc
                log.warning("llm %s attempt %d/%d failed %s", model, attempt, attempts, _describe(exc))
                if _retryable(exc) and attempt < attempts:
                    sleep(_backoff(attempt))
                    continue
                if _retryable(exc):
                    # Overloaded across every attempt: park it briefly so the
                    # next pipeline stage does not walk straight back into it.
                    _cool_down(model, overload_cooldown_s(), "overloaded")
                    log.info(
                        "cooling down %s for %.0fs after repeated overload",
                        model, overload_cooldown_s(),
                    )
                break

            # Non-provider exceptions (TypeError, KeyError, ...) propagate on purpose.

    raise LLMChainError(
        f"All {len(chain)} models failed; last error {_describe(last)}"
    ) from last