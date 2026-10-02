"""LLM access with per-model retries and a fallback chain across models."""
import json
import logging
import os
import random
import time

import litellm

try:  # litellm's provider exceptions subclass openai's hierarchy, not litellm.APIError
    from openai import OpenAIError as ProviderError
except ImportError:  # pragma: no cover - openai ships with litellm
    ProviderError = litellm.APIError

log = logging.getLogger(__name__)

MODEL = os.getenv("LLM_MODEL", "gemini/gemini-3.8-flash")  # any litellm model string works

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
    """Role-specific model first, then the default, then LLM_FALLBACKS (comma-separated)."""
    chain = [os.getenv(f"LLM_MODEL_{role.upper()}", MODEL), MODEL]
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


def ask(system: str, user: str, json_mode: bool = False, role: str = "default"):
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