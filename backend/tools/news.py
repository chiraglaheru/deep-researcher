"""Google News search tool backed by the SerpApi client."""

import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from serpapi import SerpApiClient
from serpapi.serp_api_client_exception import SerpApiClientException

API_KEY_ENV = "SERPAPI_KEY"
DEFAULT_NUM_RESULTS = 10
REQUEST_TIMEOUT_SECONDS = 30

_PROJECT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_PROJECT_ENV_PATH if _PROJECT_ENV_PATH.is_file() else None)


class SerpApiError(Exception):
    """Base error for the SerpApi news search tool."""


class MissingApiKeyError(SerpApiError):
    """Raised when SERPAPI_KEY is not available in the environment."""


class SearchRequestError(SerpApiError):
    """Raised when SerpApi fails to return news results."""


def get_api_key() -> str | None:
    """Return the configured SerpApi key, or None when it is not set."""
    key = os.getenv(API_KEY_ENV)
    key = key.strip() if key else None
    return key or None


def _extract_results(data: dict[str, Any], num_results: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for item in data.get("news_results") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("link") or item.get("url") or "").strip()
        if not title or not url:
            continue
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": str(item.get("snippet") or "").strip(),
                "date": str(item.get("date") or "").strip(),
            }
        )
        if len(results) >= num_results:
            break
    return results


def search_news(query: str, num_results: int = DEFAULT_NUM_RESULTS) -> list[dict]:
    """Search Google News via SerpApi and return clean news results.

    Each result has title, url, snippet and date, where snippet and date fall
    back to an empty string when the upstream payload omits them.

    Raises ValueError for an empty query or an invalid num_results,
    MissingApiKeyError when SERPAPI_KEY is unset, and SearchRequestError
    when the upstream search fails.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if num_results < 1:
        raise ValueError("num_results must be at least 1")

    api_key = get_api_key()
    if not api_key:
        raise MissingApiKeyError(
            f"{API_KEY_ENV} is not set. Add it to the .env file before searching."
        )

    search = SerpApiClient(
        {
            "q": query.strip(),
            "engine": "google_news",
            "num": num_results,
            "api_key": api_key,
        },
    )

    try:
        data = search.get_json()
    except SerpApiClientException as exc:
        raise SearchRequestError(f"SerpApi request failed: {exc}") from exc
    except requests.RequestException as exc:
        raise SearchRequestError(f"Could not reach SerpApi: {exc}") from exc
    except ValueError as exc:
        raise SearchRequestError("SerpApi returned a malformed response") from exc

    if not isinstance(data, dict):
        raise SearchRequestError("SerpApi returned an unexpected response format")

    error = data.get("error")
    if error:
        raise SearchRequestError(f"SerpApi error: {error}")

    return _extract_results(data, num_results)
