"""SerpApi tool layer: one search() function, normalized output.
Uses the `google-search-results` package (import name: serpapi.GoogleSearch)."""
import os


def _run(params: dict) -> dict:
    from serpapi import GoogleSearch
    params = {**params, "api_key": os.environ["SERPAPI_API_KEY"]}
    r = GoogleSearch(params).get_dict()
    err = r.get("error")
    if err and "hasn't returned any results" not in err:
        raise RuntimeError(f"SerpApi: {err}")
    return r


def _item(title, url, snippet, date, kind, query):
    return {"title": title or "", "url": url, "snippet": snippet or "", "date": date, "type": kind, "query": query}


def search(source: str, query: str, n: int = 6) -> list[dict]:
    if source == "news":
        rows = _run({"engine": "google_news", "q": query}).get("news_results", [])[:n]
        return [_item(x.get("title"), x.get("link"), x.get("snippet"),
                      x.get("iso_date") or x.get("date"), "news", query) for x in rows]
    if source == "scholar":
        rows = _run({"engine": "google_scholar", "q": query}).get("organic_results", [])[:n]
        return [_item(x.get("title"), x.get("link"), x.get("snippet"),
                      (x.get("publication_info") or {}).get("summary"), "scholar", query) for x in rows]
    q = f"site:github.com {query}" if source == "github" else query
    rows = _run({"engine": "google", "q": q}).get("organic_results", [])[:n]
    return [_item(x.get("title"), x.get("link"), x.get("snippet"), x.get("date"), source, query) for x in rows]