"""Open-access resolution for paywalled or stubborn URLs.

Before paying (in time or money) to extract a page, check whether a free,
legal copy already exists: OpenAlex maps DOIs/arXiv IDs to every known
location including open-access PDFs, Semantic Scholar adds its own OA links,
and Unpaywall covers the remainder. All three are free; Unpaywall only asks
for an email address.

Nothing here raises: a miss simply means "fetch the original URL".
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote

log = logging.getLogger(__name__)

_ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?$", re.I)
_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
_TIMEOUT = 12


def _get_json(url: str, params: dict | None = None) -> dict | None:
    import requests
    try:
        resp = requests.get(url, params=params or {}, timeout=_TIMEOUT,
                            headers={"User-Agent": "deep-researcher/2.0",
                                     "Accept": "application/json"})
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        log.debug("oa lookup failed for %s: %s", url, exc)
        return None


def _oa_pdf(work: dict) -> str:
    """Best open-access PDF URL from an OpenAlex work object."""
    best = (work.get("best_oa_location") or {})
    url = best.get("pdf_url") or ""
    if url:
        return url
    for location in work.get("locations") or []:
        if location.get("is_oa"):
            url = location.get("pdf_url") or ""
            if url:
                return url
    return ""


def _openalex_for_doi(doi: str) -> str:
    data = _get_json(f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='')}")
    if not data or "error" in data:
        return ""
    return _oa_pdf(data)


def _openalex_for_arxiv(arxiv_id: str) -> str:
    clean = arxiv_id.split("v")[0]
    data = _get_json(f"https://api.openalex.org/works/https://arxiv.org/abs/{clean}")
    if not data or "error" in data:
        return ""
    return _oa_pdf(data)


def _semantic_scholar_pdf(identifier: str, kind: str) -> str:
    """Open-access PDF via Semantic Scholar (DOI or arXiv ID)."""
    data = _get_json(f"https://api.semanticscholar.org/graph/v1/paper/{kind}:{quote(identifier, safe='')}",
                     {"fields": "title,openAccessPdf,externalIds"})
    if not data:
        return ""
    pdf = data.get("openAccessPdf") or {}
    return pdf.get("url") or ""


def _unpaywall_pdf(doi: str) -> str:
    email = (os.environ.get("UNPAYWALL_EMAIL") or "").strip()
    if not email:
        return ""
    data = _get_json(f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                     {"email": email})
    if not data:
        return ""
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or ""


def resolve_pdf_url(url: str) -> str:
    """A free open-access PDF for this URL, or "" when none is found.

    Only identifier-bearing URLs are resolved (arXiv IDs, DOIs): title-only
    fuzzy matching belongs to a search step, not the fetcher.
    """
    target = url or ""
    arxiv = _ARXIV_ID.search(target)
    if arxiv:
        arxiv_id = arxiv.group(1)
        pdf = _openalex_for_arxiv(arxiv_id)
        if not pdf:
            pdf = _semantic_scholar_pdf(arxiv_id, "ARXIV")
        if pdf:
            log.info("open-access copy found for %s", url)
        return pdf
    doi_match = _DOI.search(target)
    if doi_match:
        doi = doi_match.group(1)
        for pdf in (_openalex_for_doi(doi),
                    _semantic_scholar_pdf(doi, "DOI"),
                    _unpaywall_pdf(doi)):
            if pdf:
                log.info("open-access copy found for %s", url)
                return pdf
    return ""
