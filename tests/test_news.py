import pytest
import requests

from tools import news


class FakeSerpApiClient:
    payload = None
    error = None
    last_params = None

    def __init__(self, params, timeout=None):
        FakeSerpApiClient.last_params = params

    def get_json(self):
        if FakeSerpApiClient.error is not None:
            raise FakeSerpApiClient.error
        return FakeSerpApiClient.payload


@pytest.fixture
def fake_client(monkeypatch):
    def install(payload=None, error=None):
        FakeSerpApiClient.payload = payload
        FakeSerpApiClient.error = error
        FakeSerpApiClient.last_params = None
        monkeypatch.setattr(news, "SerpApiClient", FakeSerpApiClient)
        return FakeSerpApiClient

    return install


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-key-123")
    return "test-key-123"


def test_normalizes_results(api_key, fake_client):
    fake_client(
        {
            "news_results": [
                {
                    "title": "  Space station stops leaking  ",
                    "link": "https://news.example.com/a",
                    "snippet": "  A news snippet  ",
                    "date": "01/02/2026, 10:30 PM",
                    "source": {"name": "Ars Technica"},
                    "thumbnail": "https://news.example.com/a.jpg",
                },
                {
                    "title": "Second story",
                    "url": "https://news.example.com/b",
                    "snippet": "Another",
                    "date": None,
                },
            ]
        }
    )

    results = news.search_news("space station", 10)

    assert results == [
        {
            "title": "Space station stops leaking",
            "url": "https://news.example.com/a",
            "snippet": "A news snippet",
            "date": "01/02/2026, 10:30 PM",
        },
        {
            "title": "Second story",
            "url": "https://news.example.com/b",
            "snippet": "Another",
            "date": "",
        },
    ]
    assert all(set(r) == {"title", "url", "snippet", "date"} for r in results)


def test_returns_result_with_empty_snippet(api_key, fake_client):
    fake_client(
        {
            "news_results": [
                {
                    "title": "No snippet available",
                    "link": "https://news.example.com/a",
                    "date": "01/02/2026",
                }
            ]
        }
    )

    results = news.search_news("space station")

    assert results == [
        {
            "title": "No snippet available",
            "url": "https://news.example.com/a",
            "snippet": "",
            "date": "01/02/2026",
        }
    ]


def test_returns_result_with_empty_snippet_and_date(api_key, fake_client):
    fake_client(
        {"news_results": [{"title": "Bare", "link": "https://news.example.com/a"}]}
    )

    results = news.search_news("space station")

    assert len(results) == 1
    assert results[0]["snippet"] == ""
    assert results[0]["date"] == ""


def test_drops_incomplete_results(api_key, fake_client):
    fake_client(
        {
            "news_results": [
                {"title": "", "link": "https://news.example.com/a", "snippet": "s"},
                {"title": "no-url", "snippet": "s"},
                "junk",
                {"title": "keep", "link": "https://news.example.com/keep"},
            ]
        }
    )

    assert [r["title"] for r in news.search_news("q")] == ["keep"]


def test_respects_num_results(api_key, fake_client):
    fake_client(
        {
            "news_results": [
                {"title": f"N{i}", "link": f"https://news.example.com/{i}"}
                for i in range(5)
            ]
        }
    )

    assert len(news.search_news("q", 2)) == 2


def test_reads_news_results_not_organic_results(api_key, fake_client):
    fake_client(
        {
            "news_results": [
                {"title": "news", "link": "https://news.example.com/a", "snippet": "s"}
            ],
            "organic_results": [
                {"title": "web", "link": "https://example.com/b", "snippet": "s"}
            ],
        }
    )

    results = news.search_news("q")

    assert [r["title"] for r in results] == ["news"]


def test_no_news_results_returns_empty_list(api_key, fake_client):
    fake_client({"search_metadata": {}})
    assert news.search_news("q") == []


def test_forwards_api_key_and_engine_from_environment(api_key, fake_client):
    client = fake_client({"news_results": []})

    news.search_news("  space station  ", 5)

    assert client.last_params["api_key"] == "test-key-123"
    assert client.last_params["engine"] == "google_news"
    assert client.last_params["num"] == 5
    assert client.last_params["q"] == "space station"


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_empty_query_raises_value_error(query, api_key, fake_client):
    fake_client({"news_results": []})
    with pytest.raises(ValueError):
        news.search_news(query)


@pytest.mark.parametrize("num_results", [0, -1])
def test_invalid_num_results_raises_value_error(num_results, api_key, fake_client):
    fake_client({"news_results": []})
    with pytest.raises(ValueError):
        news.search_news("q", num_results)


def test_missing_api_key_raises(fake_client):
    fake_client({"news_results": []})
    with pytest.raises(news.MissingApiKeyError):
        news.search_news("q")


def test_blank_api_key_raises(monkeypatch, fake_client):
    monkeypatch.setenv("SERPAPI_KEY", "   ")
    fake_client({"news_results": []})
    with pytest.raises(news.MissingApiKeyError):
        news.search_news("q")


def test_api_error_payload_raises(api_key, fake_client):
    fake_client({"error": "Invalid API key."})
    with pytest.raises(news.SearchRequestError):
        news.search_news("q")


def test_network_error_raises(api_key, fake_client):
    fake_client(error=requests.ConnectionError("dns failure"))
    with pytest.raises(news.SearchRequestError):
        news.search_news("q")


def test_malformed_response_raises(api_key, fake_client):
    fake_client(error=ValueError("not json"))
    with pytest.raises(news.SearchRequestError):
        news.search_news("q")


def test_unexpected_response_shape_raises(api_key, fake_client):
    fake_client(["not", "a", "dict"])
    with pytest.raises(news.SearchRequestError):
        news.search_news("q")
