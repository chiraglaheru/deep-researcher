"""Evidence collection: normalize raw search results into clean, unique evidence.

Pure and offline: no LLM calls, no API requests. The caller supplies the
search results, and this module only reshapes and deduplicates them.
"""

from typing import Any

EVIDENCE_FIELDS = ("title", "url", "snippet", "source", "date")


def _text(value: Any) -> str:
    """Return a stripped string for a raw field, or "" when it is absent."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("title") or ""
    return str(value).strip() if value is not None else ""


def _normalize(result: dict[str, Any]) -> dict[str, str]:
    """Reduce a raw result to EVIDENCE_FIELDS, dropping every other key."""
    return {field: _text(result.get(field)) for field in EVIDENCE_FIELDS}


def collect_evidence(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize raw search results into deduplicated evidence.

    Every result keeps only the fields in EVIDENCE_FIELDS, with missing values
    replaced by an empty string. Results without a URL are skipped, and
    results sharing a URL are treated as duplicates where only the first
    occurrence is kept, in the input order.
    """
    evidence: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for result in results or []:
        if not isinstance(result, dict):
            continue
        item = _normalize(result)
        if not item["url"] or item["url"] in seen_urls:
            continue
        seen_urls.add(item["url"])
        evidence.append(item)

    return evidence
