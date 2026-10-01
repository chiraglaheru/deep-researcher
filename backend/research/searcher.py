"""Search orchestration: route a research subquestion to the right search tools.

Routing is rule-based, no LLM is involved. Google always runs as the default
source, and GitHub or Google News are added when the query carries a code or a
recency signal. Every selected tool runs independently and only the errors a
tool documents are absorbed, so one failing source cannot take down the rest of
the search and a genuine bug still surfaces.
"""

import re
from typing import Any, Callable

from tools import github, news, serpapi

GOOGLE = "google"
GITHUB = "github"
NEWS = "news"

EVIDENCE_FIELDS = ("title", "url", "snippet", "source")

_TOKEN_PATTERN = re.compile(r"[^a-z0-9+#]+")

GITHUB_TERMS = frozenset(
    {
        "api",
        "apis",
        "cli",
        "code",
        "codebase",
        "codebases",
        "coding",
        "commit",
        "commits",
        "compiler",
        "debug",
        "debugging",
        "dependency",
        "dependencies",
        "developer",
        "developers",
        "framework",
        "frameworks",
        "github",
        "gitlab",
        "implement",
        "implementing",
        "implementation",
        "implementations",
        "install",
        "installation",
        "library",
        "libraries",
        "module",
        "modules",
        "opensource",
        "package",
        "packages",
        "programming",
        "refactor",
        "refactoring",
        "repository",
        "repositories",
        "repo",
        "repos",
        "sdk",
        "sdks",
        "software",
        "source",
    }
)

NEWS_TERMS = frozenset(
    {
        "announce",
        "announced",
        "announcement",
        "announcements",
        "breaking",
        "current",
        "currently",
        "developments",
        "event",
        "events",
        "launch",
        "launched",
        "launches",
        "latest",
        "month",
        "news",
        "recent",
        "recently",
        "release",
        "released",
        "releases",
        "today",
        "tonight",
        "trending",
        "trends",
        "update",
        "updated",
        "updates",
        "upcoming",
        "week",
    }
)

COMPARISON_TERMS = frozenset(
    {
        "alternative",
        "alternatives",
        "better",
        "compare",
        "compared",
        "comparison",
        "difference",
        "differences",
        "instead",
        "versus",
        "vs",
    }
)


def _text(value: Any) -> str:
    """Return a stripped string for a raw field, or "" when it is absent."""
    return str(value).strip() if value is not None else ""


def _terms(query: str) -> set[str]:
    """Return the lowercase word tokens of a query."""
    return {token for token in _TOKEN_PATTERN.split(query.lower()) if token}


def _to_evidence(
    result: dict[str, Any],
    source: str,
    title_key: str = "title",
    snippet_key: str = "snippet",
) -> dict[str, str]:
    """Map a tool result to EVIDENCE_FIELDS, dropping every other field."""
    return {
        "title": _text(result.get(title_key)),
        "url": _text(result.get("url")),
        "snippet": _text(result.get(snippet_key)),
        "source": source,
    }


def _select_sources(query: str) -> list[str]:
    """Return the tools to run for a query, in call order.

    Google is always first. GitHub is added for code-shaped queries and News
    for recency-shaped ones. A comparison query ("Rust vs C++ ...") fits neither
    shape cleanly, so it is treated as both an implementation question and a
    recency question and fans out to every tool.
    """
    terms = _terms(query)
    comparison = bool(terms & COMPARISON_TERMS)
    selected = [GOOGLE]
    if comparison or terms & GITHUB_TERMS:
        selected.append(GITHUB)
    if comparison or terms & NEWS_TERMS:
        selected.append(NEWS)
    return selected


def _run_google(query: str) -> list[dict[str, str]]:
    return [_to_evidence(result, GOOGLE) for result in serpapi.search_google(query)]


def _run_github(query: str) -> list[dict[str, str]]:
    return [
        _to_evidence(result, GITHUB, title_key="name", snippet_key="description")
        for result in github.search_github(query)
    ]


def _run_news(query: str) -> list[dict[str, str]]:
    return [_to_evidence(result, NEWS) for result in news.search_news(query)]


_RUNNERS: dict[str, Callable[[str], list[dict[str, str]]]] = {
    GOOGLE: _run_google,
    GITHUB: _run_github,
    NEWS: _run_news,
}

_SOURCE_ERRORS: dict[str, tuple[type[Exception], ...]] = {
    GOOGLE: (serpapi.SerpApiError,),
    GITHUB: (github.GitHubError,),
    NEWS: (news.SerpApiError,),
}


def search_subquestion(query: str) -> list[dict[str, str]]:
    """Search one research subquestion and return merged, normalized evidence.

    Google always runs; GitHub and News run when the query signals code or
    recent information. Results are appended in the order the tools ran
    (Google, then GitHub, then News). A tool that raises one of the errors it
    documents is skipped so the other tools still contribute results; any other
    exception is a bug in this code and propagates to the caller.

    Every result has exactly title, url, snippet and source, with "github",
    "google" or "news" as the source. The news date is intentionally not part
    of this shape.

    Raises ValueError for an empty or whitespace-only query, before any search
    runs.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    results: list[dict[str, str]] = []
    for source in _select_sources(query):
        try:
            results.extend(_RUNNERS[source](query.strip()))
        except _SOURCE_ERRORS[source]:
            continue
    return results
