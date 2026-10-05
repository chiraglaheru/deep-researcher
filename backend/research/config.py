"""Tunable limits for the research pipeline.

Every knob is environment-driven with a working default, so the pipeline runs
with no configuration at all. Values are read on each access (not cached at
import) so tests and the .env file can change them without a restart.
"""
import os

# Rough English prose ratio. Deliberately conservative: underestimating tokens
# is how a batch silently gets truncated by the provider.
CHARS_PER_TOKEN = 3.5


def _int(name: str, default: int, low: int = 0, high: int = 10**9) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _float(name: str, default: float, low: float = 0.0, high: float = 1e9) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- stage toggles ---------------------------------------------------------

def fetch_enabled() -> bool:
    """Retrieve real source documents behind search results. Default on."""
    return _bool("RESEARCH_FETCH", True)


def analysis_enabled() -> bool:
    """LLM evidence extraction over retrieved chunks. Default on."""
    return _bool("RESEARCH_ANALYZE", True)


# --- search / source limits ------------------------------------------------

def max_sources() -> int:
    """Distinct sources kept after dedupe, across all searches."""
    return _int("RESEARCH_MAX_SOURCES", 30, 1, 200)


def results_per_search() -> int:
    return _int("RESEARCH_RESULTS_PER_SEARCH", 6, 1, 20)


def rounds_ceiling() -> int:
    """Hard upper bound on research depth, shared by the API and the UI."""
    return _int("RESEARCH_MAX_ROUNDS_LIMIT", 10, 1, 50)


def max_rounds() -> int:
    """Default research depth (gap-check loop passes)."""
    return _int("RESEARCH_MAX_ROUNDS", 2, 1, rounds_ceiling())


# --- retrieval limits ------------------------------------------------------

def fetch_max_sources() -> int:
    """How many sources we actually attempt to download."""
    return _int("FETCH_MAX_SOURCES", 22, 0, 100)


def fetch_max_bytes() -> int:
    """Per-document download ceiling. Bigger responses are truncated."""
    return _int("FETCH_MAX_BYTES", 4_000_000, 10_000)


def fetch_timeout_s() -> float:
    return _float("FETCH_TIMEOUT_S", 20.0, 1.0, 300.0)


def fetch_max_workers() -> int:
    return _int("FETCH_MAX_WORKERS", 6, 1, 24)


def fetch_max_pages() -> int:
    """PDF pages parsed. Keeps huge papers bounded."""
    return _int("FETCH_MAX_PAGES", 40, 1, 500)


def fetch_respect_robots() -> bool:
    """Honour robots.txt. Default on: we are a crawler, so behave like one."""
    return _bool("FETCH_RESPECT_ROBOTS", True)


def fetch_max_chars() -> int:
    """Text kept from one document after cleaning."""
    return _int("FETCH_MAX_CHARS", 400_000, 1_000)


# --- chunking limits -------------------------------------------------------

def chunk_target_chars() -> int:
    return _int("CHUNK_TARGET_CHARS", 1400, 200, 20_000)


def chunk_max_chars() -> int:
    return _int("CHUNK_MAX_CHARS", 2600, 400, 40_000)


def chunks_per_source() -> int:
    """Cap on chunks kept per source, so one long doc cannot dominate."""
    return _int("CHUNKS_PER_SOURCE", 6, 1, 60)


# --- relevance + extraction ------------------------------------------------

def relevance_top_chunks() -> int:
    """Chunks that survive deterministic relevance scoring."""
    return _int("RELEVANCE_TOP_CHUNKS", 70, 4, 2000)


def relevance_min_score() -> float:
    return _float("RELEVANCE_MIN_SCORE", 0.0, 0.0, 100.0)


def extract_batch_chars() -> int:
    """Chars of chunk text per extraction call. Drives evidence per LLM call."""
    return _int("EXTRACT_BATCH_CHARS", 13_000, 1_000, 200_000)


def extract_max_batches() -> int:
    """Hard ceiling on extraction calls, whatever the evidence volume."""
    return _int("EXTRACT_MAX_BATCHES", 14, 1, 200)


def max_evidence_records() -> int:
    """Cap on structured evidence kept for the report."""
    return _int("MAX_EVIDENCE_RECORDS", 160, 4, 2000)


# --- reference discovery ---------------------------------------------------

def references_enabled() -> bool:
    """Mine outbound links/DOIs from fetched documents for extra leads."""
    return _bool("RESEARCH_REFERENCES", True)


def references_max() -> int:
    """Total extra sources pulled in by reference discovery."""
    return _int("REFERENCES_MAX", 6, 0, 60)


def references_max_per_source() -> int:
    return _int("REFERENCES_PER_SOURCE", 3, 0, 20)


def references_max_depth() -> int:
    """Citation-traversal depth. 1 = references of retrieved docs only."""
    return _int("REFERENCES_MAX_DEPTH", 1, 0, 3)


# --- report generation -----------------------------------------------------

def report_depth() -> str:
    """'deep' (default) or 'brief'."""
    return os.environ.get("REPORT_DEPTH", "deep").strip().lower() or "deep"


def report_max_tokens() -> int:
    return _int("REPORT_MAX_TOKENS", 14_000, 500, 64_000)


def report_min_words() -> int:
    """Depth guidance only. The model is told to earn its length, not hit it."""
    return _int("REPORT_MIN_WORDS", 3_000, 0, 100_000)


def report_max_sections() -> int:
    """Cap on generated body sections, to bound the writing calls."""
    return _int("REPORT_MAX_SECTIONS", 12, 1, 60)


def report_evidence_per_section() -> int:
    """Evidence records handed to each section writer."""
    return _int("REPORT_EVIDENCE_PER_SECTION", 60, 4, 500)


def report_quality_floor() -> float:
    """Sections scoring below this on evidence support are not written."""
    return _float("REPORT_QUALITY_FLOOR", 0.0, 0.0, 1.0)


# --- export ----------------------------------------------------------------

def export_dir() -> str:
    return os.environ.get("EXPORT_DIR", "exports")


def export_enabled() -> bool:
    return _bool("EXPORT_ENABLED", True)


# --- global budgets (Sec 36: no uncontrolled crawling) ---------------------

# Measured on a representative run: extraction is ~8 batches plus ~5 per-source
# synthesis calls, so each extra round costs roughly this many model calls, on
# top of a fixed plan + contradiction + report cost. Used only to size a default
# budget; an explicit RESEARCH_LLM_BUDGET always wins.
LLM_FIXED_CALL_ESTIMATE = 17
LLM_CALLS_PER_ROUND_ESTIMATE = 13


def budget_estimate(rounds: int) -> int:
    """Rough number of model calls a run of this depth is expected to need."""
    return LLM_FIXED_CALL_ESTIMATE + LLM_CALLS_PER_ROUND_ESTIMATE * max(1, rounds)


def total_llm_budget(rounds: int | None = None) -> int:
    """Ceiling on model calls for one research run.

    Defaults to a value scaled to the requested depth. A flat ceiling interacts
    badly with the round limit: more rounds means more extraction, so a budget
    sized for two rounds silently starves synthesis at four and the report comes
    back partial. Set RESEARCH_LLM_BUDGET to pin it explicitly.
    """
    if os.environ.get("RESEARCH_LLM_BUDGET", "").strip():
        return _int("RESEARCH_LLM_BUDGET", 40, 4, 500)
    return budget_estimate(rounds if rounds else max_rounds())


def budget_is_explicit() -> bool:
    return bool(os.environ.get("RESEARCH_LLM_BUDGET", "").strip())


def total_fetch_budget() -> int:
    """Ceiling on documents downloaded for one research run."""
    return _int("RESEARCH_FETCH_BUDGET", 28, 0, 500)


def wall_clock_budget_s() -> float:
    """Soft ceiling on a research run. 0 disables the check."""
    return _float("RESEARCH_TIME_BUDGET_S", 600.0, 0.0, 86_400.0)