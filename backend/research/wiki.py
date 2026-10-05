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

    if not titles:
        return []
    try:
        resp = requests.get(
            API,
            params={
                "action": "query",
                "prop": "extracts",
                "exintro": 0,
                "explaintext": 1,
                "exsectionformat": "plain",
                "exchars": chars,
                "titles": "|".join(titles),
                "redirects": 1,
                "format": "json",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        pages = ((data.get("query") or {}).get("pages") or {}).values()
        out: list[WikiDoc] = []
        for page in pages:
            title = page.get("title", "")
            extract = (page.get("extract") or "").strip()
            pageid = page.get("pageid", "")
            if not title or not extract or "missing" in page:
                continue
            url = f"https://en.wikipedia.org/wiki/{_slug(title)}"
            out.append(WikiDoc(title=title, url=url, text=extract,
                               summary_chars=len(extract)))
        return out
    except Exception as exc:
        log.info("wikipedia extracts failed: %s", exc)
        return []


def _slug(title: str) -> str:
    slug = title.strip().replace(" ", "_")
    return re.sub(r"[\"#<>?\[\]]", "", slug)


def fetch_wikipedia(question: str, max_docs: int = 3,
                    chars_per_doc: int = 6000) -> list:
    """Return FetchedDoc list for Wikipedia, or [] on any failure.

    Called from ``retrieve`` before any LLM work. Never raises.
    """
    from .fetch import FetchedDoc, FULL

    try:
        from . import config as _config
        if not _config.wiki_enabled():
            return []
        max_docs = min(max_docs, _config.wiki_max_docs())
    except Exception:
        pass

    if not (question or "").strip() or max_docs <= 0:
        return []
    try:
        titles = _search_titles(question.strip()[:300], limit=max_docs)
        docs = _fetch_extracts(titles[:max_docs], chars=chars_per_doc)
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
