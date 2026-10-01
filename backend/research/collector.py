"""Collect raw results from several searches into one clean, deduplicated list.

Pure and offline: no LLM calls, no API requests. The caller supplies the merged
search results, and this module only normalizes, filters and deduplicates them.
"""

from typing import Any

RESULT_FIELDS = ("title", "url", "snippet", "source")


def _text(value: Any) -> str:
    """Return a stripped string for a raw field, or "" when it is absent."""
    return str(value).strip() if value is not None else ""


def collect_results(results: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Normalize and deduplicate merged search results.

    Accepts the results of any number of searches already merged into one list.
    Every result is reduced to RESULT_FIELDS, with missing values replaced by an
    empty string and any extra field dropped. Entries that are not dictionaries
    and results without a usable URL are skipped, and results sharing a URL are
    treated as duplicates where only the first occurrence is kept, in the input
    order. Returns [] when there is nothing usable to collect.
    """
    collected: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for result in results or []:
        if not isinstance(result, dict):
            continue
        url = _text(result.get("url"))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        collected.append(
            {
                "title": _text(result.get("title")),
                "url": url,
                "snippet": _text(result.get("snippet")),
                "source": _text(result.get("source")),
            }
        )

    return collected
