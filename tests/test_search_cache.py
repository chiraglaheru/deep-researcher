"""SEARCH_CACHE_DIR behaviour for backend.research.searcher."""

import pytest

from backend.research import searcher


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-key")


def _fake_serpapi(monkeypatch, calls):
    """Stand in for the serpapi client, counting real calls."""

    class FakeSearch:
        def __init__(self, params):
            calls.append(params)

        def get_dict(self):
            return {
                "organic_results": [
                    {
                        "title": "Rust",
                        "link": "https://example.com/rust",
                        "snippet": "systems language",
                        "date": "2026-01-01",
                    }
                ]
            }

    import serpapi

    monkeypatch.setattr(serpapi, "GoogleSearch", FakeSearch)


def test_cache_serves_the_second_call_without_hitting_serpapi(monkeypatch, tmp_path):
    monkeypatch.setenv("SEARCH_CACHE_DIR", str(tmp_path / "serp"))
    calls = []
    _fake_serpapi(monkeypatch, calls)

    first = searcher.search("web", "rust game engine", n=3)
    second = searcher.search("web", "rust game engine", n=3)

    assert len(calls) == 1, "second call should be served from the cache"
    assert first == second
    assert len(first) == 1


def test_cache_is_keyed_by_query_and_n(monkeypatch, tmp_path):
    monkeypatch.setenv("SEARCH_CACHE_DIR", str(tmp_path / "serp"))
    calls = []
    _fake_serpapi(monkeypatch, calls)

    searcher.search("web", "alpha", n=3)
    searcher.search("web", "beta", n=3)
    searcher.search("web", "alpha", n=9)

    assert len(calls) == 3, "different query or n must be a different cache entry"

    searcher.search("web", "alpha", n=3)
    assert len(calls) == 3, "an exact repeat should hit the cache"


def test_cache_is_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("SEARCH_CACHE_DIR", raising=False)
    calls = []
    _fake_serpapi(monkeypatch, calls)

    searcher.search("web", "rust game engine", n=3)
    searcher.search("web", "rust game engine", n=3)

    assert len(calls) == 2, "without SEARCH_CACHE_DIR every call reaches SerpApi"


def test_errors_are_not_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("SEARCH_CACHE_DIR", str(tmp_path / "serp"))
    calls = []

    class FailingSearch:
        def __init__(self, params):
            calls.append(params)

        def get_dict(self):
            return {"error": "something broke"}

    import serpapi

    monkeypatch.setattr(serpapi, "GoogleSearch", FailingSearch)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            searcher.search("web", "boom", n=3)

    assert len(calls) == 2, "a failed search must not be served from cache"