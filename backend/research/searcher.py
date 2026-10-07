"""SerpApi tool layer: one search() function, normalized output.
Uses the `google-search-results` package (import name: serpapi.GoogleSearch)."""
import datetime
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger(__name__)


def _search_timeout_s() -> float:
    """Hard ceiling per SerpApi call. Env-overridable, independent of fetch."""
    try:
        return max(1.0, float(os.environ.get("SEARCH_TIMEOUT_S", 60.0)))
    except (TypeError, ValueError):
        return 60.0


def _run(params: dict) -> dict:
    from serpapi import GoogleSearch
    params = {**params, "api_key": os.environ["SERPAPI_KEY"]}
    r = _call_with_timeout(params, _search_timeout_s())
    err = r.get("error")
    if err and "hasn't returned any results" not in err:
        raise RuntimeError(f"SerpApi: {err}")
    return r


def _call_with_timeout(params: dict, timeout: float) -> dict:
    """Run one SerpApi call with a hard ceiling.

    The client sets no timeout of its own, and the graph waits for every
    parallel search before moving on -- so one hung socket used to freeze
    the whole run at the search step with no error and no event. Daemon
    thread, so the abandoned socket can never hold up process shutdown.
    """
    from serpapi import GoogleSearch
    outcome: dict = {}

    def target():
        try:
            outcome["result"] = GoogleSearch(params).get_dict()
        except BaseException as exc:  # noqa: BLE001 -- re-raised below
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        log.warning("SerpApi: no response within %.0fs; failing this search "
                    "instead of stalling the run", timeout)
        raise TimeoutError(f"SerpApi: timed out after {timeout:.0f}s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result", {})


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


def _item(title, url, snippet, date, kind, query, extra=None):
    row = {"title": title or "", "url": url, "snippet": snippet or "", "date": date, "type": kind, "query": query}
    if extra:
        row.update(extra)
    return row


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


def search_full(source: str, query: str, n: int = 6) -> tuple[list[dict], dict]:
    """Items plus response-level extras, for stages that use more than links.

    ``meta`` carries ``related_questions`` and ``related_searches`` (free
    follow-up discovery), ``knowledge_graph`` (entity + type for
    disambiguation) and ``answer_box`` when Google returned them. Empty in
    mock mode and for engines that do not provide them.
    """
    if _mock_enabled():
        return _mock_search(source, query, n), {}
    from .throttle import get
    with get("search").slot():
        return _run_search(source, query, n)


def _scholar_extras(x: dict) -> dict:
    """Authority metadata SerpApi ships with Scholar rows, defensively read."""
    pub = x.get("publication_info") or {}
    inline = x.get("inline_links") or {}
    cited = inline.get("cited_by") or {}
    total = cited.get("total", x.get("cited_by", 0))
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = 0
    pdf_url = None
    for resource in x.get("resources") or []:
        if isinstance(resource, dict) and "pdf" in str(resource.get("file_format", "")).lower():
            pdf_url = resource.get("link") or None
            break
    return {
        "cited_by": total,
        "scholar_id": x.get("result_id") or cited.get("cites_id") or "",
        "pdf_url": pdf_url,
        "scholar_summary": pub.get("summary") or "",
    }


def _scholar_item(x: dict, query: str) -> dict:
    pub = x.get("publication_info") or {}
    extra = _scholar_extras(x)
    return _item(x.get("title"), x.get("link"), x.get("snippet"),
                 pub.get("summary"), "scholar", query, extra)


def _knowledge_meta(resp: dict) -> dict:
    """Response-level extras: questions, related searches, entity, answer."""
    meta: dict = {}
    questions = []
    for entry in (resp.get("related_questions") or [])[:8]:
        text = (entry.get("question") or "").strip() if isinstance(entry, dict) else ""
        if text:
            questions.append(text)
    if questions:
        meta["related_questions"] = questions
    searches = []
    for entry in (resp.get("related_searches") or [])[:8]:
        text = (entry.get("query") or "").strip() if isinstance(entry, dict) else ""
        if text:
            searches.append(text)
    if searches:
        meta["related_searches"] = searches
    graph = resp.get("knowledge_graph") or {}
    if isinstance(graph, dict) and graph.get("title"):
        meta["knowledge_graph"] = {
            "title": graph.get("title", ""),
            "type": graph.get("type", ""),
            "description": (graph.get("description") or "")[:500],
        }
    box = resp.get("answer_box") or {}
    if isinstance(box, dict) and box.get("link"):
        meta["answer_box"] = {
            "title": box.get("title", ""),
            "link": box.get("link", ""),
            "text": box.get("answer") or box.get("snippet") or "",
        }
    return meta


def _ai_overview_refs(page_token: str) -> list[dict]:
    """Reference links from a full AI Overview, or [] on any failure.

    The page token expires within a minute, so this is fetched immediately
    (never cached) inside the search's own throttle slot. Only links are
    kept: the generated prose never enters the pipeline.

    Deliberately takes NO inner throttle slot: this always runs inside the
    outer search slot, and the shared semaphore is non-reentrant -- a second
    acquire on the same thread with max_concurrent=1 would hang forever.
    """
    if not page_token:
        return []
    try:
        resp = _run({"engine": "google_ai_overview",
                     "page_token": page_token})
    except Exception:
        return []
    refs = []
    overview = resp.get("ai_overview") or resp
    for entry in (overview.get("references") or [])[:6]:
        if not isinstance(entry, dict):
            continue
        link = (entry.get("link") or "").strip()
        if link:
            refs.append({"title": (entry.get("title") or "").strip()[:200],
                         "link": link})
    return refs


def _run_search(source: str, query: str, n: int = 6) -> tuple[list[dict], dict]:
    if source == "news":
        rows = _cached_run(source, query, n, {"engine": "google_news", "q": query}).get("news_results", [])[:n]
        items = _drop_blocked([_item(x.get("title"), x.get("link"), x.get("snippet"),
                      x.get("iso_date") or x.get("date"), "news", query) for x in rows], source)
        return items, {}
    if source == "scholar":
        rows = _cached_run(source, query, n, {"engine": "google_scholar", "q": query}).get("organic_results", [])[:n]
        return [_scholar_item(x, query) for x in rows], {}
    if source == "patent":
        resp = _cached_run(source, query, n, {"engine": "google_patents", "q": query})
        rows = (resp.get("organic_results") or resp.get("patents_results") or [])[:n]
        return [_item(x.get("title"), x.get("link"), x.get("snippet") or x.get("description"),
                      x.get("publication_date") or x.get("date"), "patent", query) for x in rows], {}
    q = f"site:github.com {query}" if source == "github" else query
    resp = _cached_run(source, query, n, {"engine": "google", "q": q})
    rows = resp.get("organic_results", [])[:n]
    items = _drop_blocked([_item(x.get("title"), x.get("link"), x.get("snippet"), x.get("date"), source, query) for x in rows], source)
    meta = _knowledge_meta(resp)
    token = ((resp.get("ai_overview") or {}).get("page_token") or "")
    if token:
        from . import config as _config
        if _config.ai_overview_enabled():
            refs = _ai_overview_refs(token)
            if refs:
                meta["ai_overview_refs"] = refs
    graph = meta.get("knowledge_graph") or {}
    if graph:
        for row in items:
            row["entity"] = graph.get("title", "")
            row["entity_type"] = graph.get("type", "")
    box = meta.get("answer_box") or {}
    if box.get("link") and box.get("text"):
        items.append(_item(box.get("title") or query, box["link"],
                           f"Direct answer: {box['text']}", "", source, query,
                           {"answer": True}))
    return items, meta


def _search_uncapped(source: str, query: str, n: int = 6) -> list[dict]:
    return _run_search(source, query, n)[0]


def expand_query(query: str) -> str:
    """Top autocomplete suggestion for a draft query, or "" when none fits.

    Used to sharpen vague planner queries with the phrasing real searchers
    use. Never raises: callers keep the original query on any failure.
    """
    if _mock_enabled() or not (query or "").strip():
        return ""
    try:
        from .throttle import get
        with get("search").slot():
            resp = _cached_run("autocomplete", query, 5,
                               {"engine": "google_autocomplete", "q": query})
    except Exception:
        return ""
    for suggestion in resp.get("suggestions", []) or []:
        value = ((suggestion or {}).get("value") or "").strip()
        if value and value.lower() != query.lower() and len(value.split()) <= 12:
            return value
    return ""


def cited_by_search(scholar_id: str, query: str = "", n: int = 6) -> list[dict]:
    """Forward citation chase: documents citing the given Scholar article.

    The Scholar ``cites`` parameter lists citers, optionally searched within
    via ``q``. Returns scholar-normalized items; [] on any failure.
    """
    if _mock_enabled() or not scholar_id:
        return []
    params = {"engine": "google_scholar", "cites": scholar_id}
    if query:
        params["q"] = query
    try:
        from .throttle import get
        with get("search").slot():
            resp = _cached_run("scholar-cites", scholar_id + "\x00" + query, n, params)
    except Exception:
        return []
    return [_scholar_item(x, query) for x in resp.get("organic_results", [])[:n]]