"""SerpApi tool layer: one search() function, normalized output.
Uses the `google-search-results` package (import name: serpapi.GoogleSearch)."""
import datetime
import hashlib
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(params: dict) -> dict:
    from serpapi import GoogleSearch
    params = {**params, "api_key": os.environ["SERPAPI_KEY"]}
    r = GoogleSearch(params).get_dict()
    err = r.get("error")
    if err and "hasn't returned any results" not in err:
        raise RuntimeError(f"SerpApi: {err}")
    return r


def _cache_path(source: str, query: str, n: int) -> Path | None:
    """Cache file for this exact search, or None when caching is off.

    A relative SEARCH_CACHE_DIR resolves against the repo root, not the CWD,
    so the cache lands in the same place however the app is launched.
    """
    directory = os.environ.get("SEARCH_CACHE_DIR")
    if not directory:
        return None
    key = hashlib.sha1(f"{source}\x00{query}\x00{n}".encode()).hexdigest()
    path = Path(directory)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path / f"{key}.json"


def _cached_run(source: str, query: str, n: int, params: dict) -> dict:
    """Serve from SEARCH_CACHE_DIR when possible; otherwise call SerpApi."""
    path = _cache_path(source, query, n)
    if path is None:
        return _run(params)
    if path.is_file():
        return json.loads(path.read_text())
    result = _run(params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result))
    return result


def _item(title, url, snippet, date, kind, query):
    return {"title": title or "", "url": url, "snippet": snippet or "", "date": date, "type": kind, "query": query}


def _blocked_host(url: str, blocked: tuple[str, ...]) -> bool:
    """True when this URL lives on a noise host (social/video pages)."""
    from urllib.parse import urlparse
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    return bool(host) and any(bad in host for bad in blocked)


def _drop_blocked(rows: list[dict], source: str) -> list[dict]:
    """Strip social/video noise from web/news results before they cost budget.

    Scholar and GitHub are exempt: their hosts are the signal. Filtering runs
    on normalized items (after the disk cache), so changing the blocklist
    never requires invalidating cached SerpApi responses.
    """
    if source not in ("web", "news") or not rows:
        return rows
    from . import config as _config
    blocked = _config.search_blocked_hosts()
    if not blocked:
        return rows
    kept = [r for r in rows if not _blocked_host(r.get("url", ""), blocked)]
    dropped = len(rows) - len(kept)
    if dropped:
        import logging as _logging
        _logging.getLogger(__name__).info(
            "search filtered %d %s result(s) on blocked hosts", dropped, source)
    return kept


_SEARCH_MOCK_WARNED = False


def _mock_enabled() -> bool:
    return os.environ.get("SEARCH_MOCK", "") == "1"


def _mock_delay() -> None:
    raw = os.environ.get("SEARCH_MOCK_DELAY_MS", "300")
    try:
        seconds = max(0.0, float(raw) / 1000.0)
    except ValueError:
        seconds = 0.3
    if seconds:
        __import__("time").sleep(seconds)


def _mock_results(source: str, query: str, n: int) -> list[dict]:
    """Deterministic synthetic results, with the awkward cases included on purpose.

    Contains a duplicate URL (www + trailing slash, so the collector must merge
    it), an empty URL (must be skipped), a junk domain (kept today: there is no
    blocked list) and one clearly stale date.
    """
    slug = hashlib.sha1(f"{source}\x00{query}".encode()).hexdigest()[:10]
    year = datetime.date.today().year
    rows = [
        {"title": f"{query} — official reference",
         "url": f"https://example.com/{source}/{slug}",
         "snippet": f"Background on {query} from {source}.",
         "date": f"{year}-01-15"},
        {"title": f"{query} — deeper analysis",
         "url": f"https://docs.example.org/{slug}",
         "snippet": f"A closer look at {query}, including trade-offs.",
         "date": f"{year}-05-02"},
        {"title": f"{query} — duplicate listing",
         "url": f"https://www.example.com/{source}/{slug}/",
         "snippet": f"Duplicate of the primary reference for {query}.",
         "date": f"{year}-02-01"},
        {"title": f"{query} — link without a URL",
         "url": "",
         "snippet": f"This listing for {query} has no URL and must be skipped.",
         "date": f"{year}-03-03"},
        {"title": f"{query} — chatter",
         "url": f"https://social-chatter.example/{slug}",
         "snippet": f"Unfiltered opinions about {query}.",
         "date": f"{year}-04-04"},
        {"title": f"{query} — archived",
         "url": f"https://archive.example/{slug}",
         "snippet": f"An older, still-relevant note on {query}.",
         "date": "2015-06-01"},
    ]
    return [_item(r["title"], r["url"], r["snippet"], r["date"], source, query)
            for r in rows[:n]]


def _mock_search(source: str, query: str, n: int) -> list[dict]:
    global _SEARCH_MOCK_WARNED

    if not _SEARCH_MOCK_WARNED:
        _SEARCH_MOCK_WARNED = True
        import logging
        logging.getLogger(__name__).warning("SEARCH MOCK MODE ACTIVE")

    failing = os.environ.get("SEARCH_MOCK_FAIL", "").strip()
    if failing and (failing == "all" or failing == source):
        raise RuntimeError(f"SerpApi: mock failure for source {source}")

    _mock_delay()
    return _mock_results(source, query, n)


def search(source: str, query: str, n: int = 6) -> list[dict]:
    if _mock_enabled():
        return _mock_search(source, query, n)

    # Throttling sits around the network call rather than around each engine
    # branch, so every source is paced by the same pacer. Cache hits still cost
    # a slot, which keeps the rate a ceiling on the provider regardless.
    from .throttle import get
    with get("search").slot():
        return _search_uncapped(source, query, n)


def _search_uncapped(source: str, query: str, n: int = 6) -> list[dict]:
    if source == "news":
        rows = _cached_run(source, query, n, {"engine": "google_news", "q": query}).get("news_results", [])[:n]
        return _drop_blocked([_item(x.get("title"), x.get("link"), x.get("snippet"),
                      x.get("iso_date") or x.get("date"), "news", query) for x in rows], source)
    if source == "scholar":
        rows = _cached_run(source, query, n, {"engine": "google_scholar", "q": query}).get("organic_results", [])[:n]
        return [_item(x.get("title"), x.get("link"), x.get("snippet"),
                      (x.get("publication_info") or {}).get("summary"), "scholar", query) for x in rows]
    q = f"site:github.com {query}" if source == "github" else query
    rows = _cached_run(source, query, n, {"engine": "google", "q": q}).get("organic_results", [])[:n]
    return _drop_blocked([_item(x.get("title"), x.get("link"), x.get("snippet"), x.get("date"), source, query) for x in rows], source)