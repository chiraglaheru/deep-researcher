"""Bounded reference discovery.

A retrieved document often points at material that matters more than the
document itself: the benchmark it cites, the repo it benchmarks against, the
paper it builds on. Those links are mined here and treated as new leads.

Three things keep this from becoming uncontrolled crawling:

  * a hard ceiling on how many extra sources are added (REFERENCES_MAX)
  * depth is capped at 1 by default, so references of references are not chased
  * candidates are filtered deterministically against the research question
    before spending a single fetch or LLM call on them

An inbound link is a weak signal on its own, so nothing is accepted purely for
being linked to -- it has to also look relevant to the question.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import unquote, urlparse

from . import config
from .fetch import FetchedDoc
from .relevance import Scored, terms

log = logging.getLogger(__name__)

# Hosts that are navigation or account walls rather than research material.
_SKIP_HOSTS = (
    "twitter.com", "x.com", "facebook.com", "instagram.com", "linkedin.com",
    "reddit.com", "youtube.com", "tiktok.com", "pinterest.com", "medium.com",
    "substack.com", "shop.", "amazon.", "ebay.", "paypal.", "stackoverflow.com",
    "patreon.com", "discord.", "t.me", "bit.ly", "t.co", "goo.gl",
)

# Hosts that tend to host primary or citable material, plus markers of a
# benchmark/study. Deliberately domain-agnostic: naming a specific vendor or
# framework here would bias reference discovery toward that domain for every
# question, including ones where it is irrelevant.
_INTERESTING = (
    "github.com", "arxiv.org", "doi.org", "dl.acm.org", "ieeexplore.ieee.org",
    "acm.org", "springer.com", "sciencedirect.com", "nature.com", "sciencedb",
    ".edu", ".gov", ".int", "stackoverflow.blog", "npmjs.com", "pypi.org",
    "readthedocs", "docs.", "bench", "benchmark", "study", "survey", "paper",
    "report", "standard", "spec",
)

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


def candidates(docs: list[FetchedDoc], question: str,
               already: set[str]) -> list[str]:
    """Rank outbound links from retrieved documents by likely relevance."""
    if not config.references_enabled():
        return []

    per_source = config.references_max_per_source()
    total = config.references_max()
    depth = config.references_max_depth()
    if total <= 0 or per_source <= 0 or depth < 1:
        return []

    query_terms = set(terms(question))
    found: dict[str, float] = {}

    for doc in docs:
        if not doc.links:
            continue
        scored: list[tuple[float, str]] = []
        for url in doc.links:
            score = _score(url, query_terms)
            if score > 0:
                scored.append((score, url))
        scored.sort(key=lambda pair: -pair[0])
        for _, url in scored[:per_source]:
            found.setdefault(url, 1.0)

    known = {_normalise(u) for u in already}
    out = []
    for url in found:
        if _normalise(url) in known:
            continue
        out.append(url)
        if len(out) >= total:
            break
    return out


def _score(url: str, query_terms: set[str]) -> float:
    """Deterministic relevance heuristic for a candidate link. No LLM call."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if not host or any(bad in host for bad in _SKIP_HOSTS):
        return 0.0

    haystack = f"{host}{parsed.path}".lower()
    if _DOI_RE.match(url):
        return 5.0                                   # a DOI is almost always citable

    score = 0.0
    if any(token in haystack for token in _INTERESTING):
        score += 2.5
    if parsed.path.endswith((".pdf", ".html", ".htm")) or "abs" in parsed.path:
        score += 0.5

    # Query-term overlap in the URL itself is weak but real evidence of topic fit.
    url_terms = set(terms(unquote(parsed.path.replace("/", " ").replace("-", " "))))
    if url_terms and query_terms:
        overlap = url_terms & query_terms
        score += 1.5 * len(overlap) / max(1, len(query_terms))

    # Deep paths are usually specific content, not a landing page.
    depth = len([p for p in parsed.path.split("/") if p])
    score += min(depth, 4) * 0.2
    return score


def _normalise(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def doi_url(doi: str) -> str:
    return f"https://doi.org/{doi}" if doi else ""


def arxiv_from_doi(doi: str) -> str:
    """arXiv DOIs embed the identifier, which gives a free full-text link."""
    match = re.search(r"arxiv\.(\d{4}\.\d{4,5})", doi or "")
    return f"https://arxiv.org/abs/{match.group(1)}" if match else ""


def forward_candidates(sources: list[dict], question: str,
                       known: set[str], index: dict) -> list[str]:
    """Follow citations forward: documents citing highly-cited papers.

    Outbound links point backward in time; citers are usually newer, so a
    heavily-cited paper is a bridge to the state of the art. Bounded by
    SCHOLAR_FORWARD_MAX and skipped for URLs already retrieved.
    """
    from . import config as _config
    from .searcher import cited_by_search

    depth = _config.scholar_forward_max()
    if depth <= 0:
        return []
    ranked = sorted(
        (s for s in sources
         if s.get("scholar_id") and int(s.get("cited_by", 0) or 0) > 0),
        key=lambda s: -int(s.get("cited_by", 0) or 0),
    )[:depth]
    out: list[str] = []
    for source in ranked:
        try:
            hits = cited_by_search(source["scholar_id"], question)
        except Exception:
            continue
        for hit in hits:
            url = hit.get("url", "")
            if url and url not in known and url not in index and url not in out:
                out.append(url)
    if out:
        log.info("reference discovery: chasing %d forward citation(s)", len(out))
    return out


def describe(found: list[str], rejected: int) -> str:
    if not found and not rejected:
        return "no outbound references were followed"
    parts = []
    if found:
        parts.append(f"followed {len(found)} reference link(s)")
    if rejected:
        parts.append(f"skipped {rejected} that looked unrelated to the question")
    return "; ".join(parts) or "reference discovery ran but found nothing usable"