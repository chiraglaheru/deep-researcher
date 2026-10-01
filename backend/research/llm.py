import json, os
import litellm

MODEL = os.getenv("LLM_MODEL", "gemini/gemini-3.8-flash")  # any litellm model string works


def _models(role: str) -> list[str]:
    """Role-specific model first, then the default, then LLM_FALLBACKS (comma-separated)."""
    chain = [os.getenv(f"LLM_MODEL_{role.upper()}", MODEL), MODEL]
    chain += [m.strip() for m in os.getenv("LLM_FALLBACKS", "").split(",") if m.strip()]
    return list(dict.fromkeys(chain))  # dedupe, keep order


def ask(system: str, user: str, json_mode: bool = False, role: str = "default"):
    kw = {"response_format": {"type": "json_object"}} if json_mode else {}
    last = None
    for model in _models(role):
        try:
            r = litellm.completion(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
                **kw,
            )
            out = r.choices[0].message.content
            return json.loads(out) if json_mode else out
        except (litellm.RateLimitError, litellm.NotFoundError) as e:  # quota used up / model retired
            last = e
    raise last