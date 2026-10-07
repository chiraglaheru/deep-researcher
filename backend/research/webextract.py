"""Paid extraction fallback for pages the direct fetcher cannot read.

Tier order is fixed: Tavily first, Firecrawl second. Both return clean
markdown/text for JS-rendered or bot-guarded pages. Callers enforce API keys
and per-run caps; nothing here raises.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def _timeout() -> float:
    try:
        from . import config as _config
        return _config.fetch_timeout_s()
    except Exception:
        return 20.0


def tavily_extract(url: str) -> tuple[str, str] | None:
    """(title, text) via Tavily Extract, or None on any failure."""
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key or not url:
        return None
    import requests
    try:
        resp = requests.post(
            "https://api.tavily.com/extract",
            json={"api_key": key, "urls": [url], "query": "",
                  "extract_depth": "advanced", "include_images": False},
            timeout=max(_timeout(), 45.0),
        )
        if resp.status_code in (401, 403):
            log.warning("tavily: key rejected (HTTP %s)", resp.status_code)
            return None
        if resp.status_code != 200:
            return None
        results = (resp.json().get("results") or [])
        if not results:
            return None
        first = results[0] or {}
        text = (first.get("raw_content") or "").strip()
        if len(text.split()) < 30:
            return None
        return (first.get("title") or "", text)
    except Exception as exc:
        log.debug("tavily extract failed for %s: %s", url, exc)
        return None


def firecrawl_scrape(url: str) -> tuple[str, str] | None:
    """(title, text) via Firecrawl Scrape, or None on any failure."""
    key = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    if not key or not url:
        return None
    import requests
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v2/scrape",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"]},
            timeout=max(_timeout(), 60.0),
        )
        if resp.status_code in (401, 403):
            log.warning("firecrawl: key rejected (HTTP %s)", resp.status_code)
            return None
        if resp.status_code != 200:
            return None
        data = (resp.json().get("data") or {})
        text = (data.get("markdown") or "").strip()
        if len(text.split()) < 30:
            return None
        metadata = data.get("metadata") or {}
        return (metadata.get("title") or data.get("title") or "", text)
    except Exception as exc:
        log.debug("firecrawl scrape failed for %s: %s", url, exc)
        return None
