"""SerpApi tool layer: one search() function, normalized output.
Uses the `google-search-results` package (import name: serpapi.GoogleSearch)."""
import hashlib
import json
import os
from pathlib import Path


def _run(params: dict) -> dict:
    from serpapi import GoogleSearch
    params = {**params, "api_key": os.environ["SERPAPI_KEY"]}
    r = GoogleSearch(params).get_dict()
    err = r.get("error")
    if err and "hasn't returned any results" not in err:
        raise RuntimeError(f"SerpApi: {err}")
    return r


def _cache_path(source: str, query: str, n: int) -> Path | None:
    """Cache file for this exact search, or None when caching is off."""
    directory = os.environ.get("SEARCH_CACHE_DIR")
    if not directory:
        return None
    key = hashlib.sha1(f"{source}\x00{query}\x00{n}".encode()).hexdigest()
    return Path(directory) / f"{key}.json"


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


def search(source: str, query: str, n: int = 6) -> list[dict]:
    if source == "news":
        rows = _cached_run(source, query, n, {"engine": "google_news", "q": query}).get("news_results", [])[:n]
        return [_item(x.get("title"), x.get("link"), x.get("snippet"),
                      x.get("iso_date") or x.get("date"), "news", query) for x in rows]
    if source == "scholar":
        rows = _cached_run(source, query, n, {"engine": "google_scholar", "q": query}).get("organic_results", [])[:n]
        return [_item(x.get("title"), x.get("link"), x.get("snippet"),
                      (x.get("publication_info") or {}).get("summary"), "scholar", query) for x in rows]
    q = f"site:github.com {query}" if source == "github" else query
    rows = _cached_run(source, query, n, {"engine": "google", "q": q}).get("organic_results", [])[:n]
    return [_item(x.get("title"), x.get("link"), x.get("snippet"), x.get("date"), source, query) for x in rows]