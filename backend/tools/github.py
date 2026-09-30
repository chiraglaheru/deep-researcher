"""GitHub repository search tool backed by the GitHub REST API."""

import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

API_TOKEN_ENV = "GITHUB_TOKEN"
DEFAULT_NUM_RESULTS = 10
REQUEST_TIMEOUT_SECONDS = 30
SEARCH_URL = "https://api.github.com/search/repositories"
MAX_PER_PAGE = 100

_PROJECT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_PROJECT_ENV_PATH if _PROJECT_ENV_PATH.is_file() else None)


class GitHubError(Exception):
    """Base error for the GitHub search tool."""


class SearchRequestError(GitHubError):
    """Raised when GitHub fails to return results."""


def get_api_token() -> str | None:
    """Return the configured GitHub token, or None when it is not set."""
    token = os.getenv(API_TOKEN_ENV)
    token = token.strip() if token else None
    return token or None


def _build_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "deep-researcher",
    }
    token = get_api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _extract_results(data: dict[str, Any], num_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("full_name") or "").strip()
        url = str(item.get("html_url") or "").strip()
        if not name or not url:
            continue
        results.append(
            {
                "name": name,
                "url": url,
                "description": str(item.get("description") or "").strip(),
                "stars": int(item.get("stargazers_count") or 0),
            }
        )
        if len(results) >= num_results:
            break
    return results


def search_github(query: str, num_results: int = DEFAULT_NUM_RESULTS) -> list[dict]:
    """Search GitHub repositories and return clean name/url/description/stars results.

    Raises ValueError for an empty query or an invalid num_results, and
    SearchRequestError when GitHub fails to return results. Authentication is
    optional; requests run unauthenticated at a lower rate limit when
    GITHUB_TOKEN is unset.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if num_results < 1:
        raise ValueError("num_results must be at least 1")

    params = {"q": query.strip(), "per_page": min(num_results, MAX_PER_PAGE)}

    try:
        response = requests.get(
            SEARCH_URL,
            params=params,
            headers=_build_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise SearchRequestError(f"Could not reach GitHub: {exc}") from exc

    if response.status_code != 200:
        raise SearchRequestError(
            f"GitHub API error (HTTP {response.status_code}): {_error_message(response)}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise SearchRequestError("GitHub returned a malformed response") from exc

    if not isinstance(data, dict):
        raise SearchRequestError("GitHub returned an unexpected response format")

    return _extract_results(data, num_results)


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:200] or "no details available"
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    return "no details available"
