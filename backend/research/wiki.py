"""Wikipedia pre-retrieval, before any LLM call.

The pipeline used to send only SerpApi snippets to the first model call.
Wikipedia gives a stable, license-clear baseline for entities, definitions and
timelines at zero API cost, so it is fetched deterministically in ``retrieve``
before the planner output ever reaches a model.

Only en.wikipedia.org is used. Failures are silent by design: Wikipedia is a
bonus baseline, never a hard dependency.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .relevance import search_keywords

log = logging.getLogger(__name__)

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "deep-researcher/2.0 (research baseline; contact: local run)"


@dataclass
class WikiDoc:
    title: str
    url: str
    text: str
    summary_chars: int = 0


def _search_titles(query: str, limit: int = 5) -> list[str]:
    import requests

    try:
        resp = requests.get(
            API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "format": "json",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        hits = (data.get("query") or {}).get("search") or []
        return [h.get("title", "") for h in hits if h.get("title")]
    except Exception as exc:
        log.info("wikipedia search failed: %s", exc)
        return []


def _fetch_extracts(titles: list[str], chars: int = 6000) -> list[WikiDoc]:
    import requests
    from bs4 import BeautifulSoup

    if not titles:
        return []

    out: list[WikiDoc] = []

    skip_sections = {
        "references",
        "notes",
        "citations",
        "bibliography",
        "sources",
        "external links",
        "further reading",
        "see also",
        "works cited",
    }

    try:
        for title in titles:
            resp = requests.get(
                API,
                params={
                    "action": "parse",
                    "page": title,
                    "prop": "text",
                    "redirects": 1,
                    "format": "json",
                    "formatversion": 2,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=12,
            )

            if resp.status_code != 200:
                continue

            data = resp.json()
            page = (data.get("parse") or {})
            html = page.get("text") or ""
            if isinstance(html, dict):
                html = html.get("*") or ""
            html = html.strip()

            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")

            # Remove Wikipedia junk that is not article content.
            for tag in soup.select(
                ".mw-editsection, table, style, script, "
                ".reference, sup.reference, .reflist, .navbox, "
                ".metadata, .infobox, .sidebar, .hatnote, .ambox, "
                ".vertical-navbox, .mw-references-wrap, .catlinks"
            ):
                tag.decompose()

            paragraphs = []

            for element in soup.find_all(["h2", "h3", "h4", "p"]):
                if element.name.startswith("h"):
                    heading = element.get_text(" ", strip=True)
                    heading = re.sub(r"\[.*?\]", "", heading).strip()

                    if heading.lower() in skip_sections:
                        break

                    continue

                text = element.get_text(" ", strip=True)

                # Remove citation markers such as [1], [23], [citation needed].
                text = re.sub(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", "", text)
                text = re.sub(r"\[\s*citation needed\s*\]", "", text,
                              flags=re.IGNORECASE)
                text = re.sub(r"\s+", " ", text).strip()

                if text:
                    paragraphs.append(text)

            extract = "\n\n".join(paragraphs).strip()

            if not extract:
                continue

            extract = extract[:chars].strip()

            url = f"https://en.wikipedia.org/wiki/{_slug(title)}"

            out.append(
                WikiDoc(
                    title=title,
                    url=url,
                    text=extract,
                    summary_chars=len(extract),
                )
            )

        return out

    except Exception as exc:
        log.info("wikipedia extracts failed: %s", exc)
        return []


def _slug(title: str) -> str:
    slug = title.strip().replace(" ", "_")
    return re.sub(r"[\"#<>?\[\]]", "", slug)


def fetch_wikipedia(question: str, max_docs: int = 3,
                    chars_per_doc: int = 6000,
                    subquestions: list[str] | None = None) -> list:
    """Return FetchedDoc list for Wikipedia, or [] on any failure.

    Searches once for the main question and once per planned sub-question,
    pooling distinct titles up to ``max_docs``. The question's own hits keep
    priority; each sub-question then contributes its top titles, so coverage
    follows the plan instead of a single shot. Called from ``retrieve``.
    Never raises.
    """
    from .fetch import FetchedDoc, FULL

    try:
        from . import config as _config
        if not _config.wiki_enabled():
            return []
        max_docs = min(max_docs, _config.wiki_max_docs())
        per_query = _config.wiki_titles_per_query()
    except Exception:
        per_query = 2

    if not (question or "").strip() or max_docs <= 0:
        return []
    try:
        # Keyword-only queries: relevant content words shared with the main
        # question first, never basic English filler.
        queries = [search_keywords(question)] + [
            search_keywords(question, sq) for sq in (subquestions or [])
            if (sq or "").strip()]
        queries = [q for q in queries if q]
        seen: list[str] = []
        lowered: set[str] = set()
        for query in queries:
            for title in _search_titles(query[:300], limit=per_query):
                key = title.lower()
                if key not in lowered:
                    lowered.add(key)
                    seen.append(title)
                if len(seen) >= max_docs:
                    break
            if len(seen) >= max_docs:
                break
        docs = _fetch_extracts(seen[:max_docs], chars=chars_per_doc)
        out = []
        for doc in docs:
            text = f"# {doc.title}\n\n{doc.text}".strip()
            out.append(FetchedDoc(
                url=doc.url,
                final_url=doc.url,
                status=FULL,
                method="wikipedia",
                title=doc.title,
                text=text,
                publisher="Wikipedia",
                date="",
                limitation="",
            ))
        if out:
            log.info("wikipedia: %d baseline doc(s) for %r", len(out), question[:60])
        return out
    except Exception as exc:
        log.info("wikipedia baseline skipped: %s", exc)
        return []
