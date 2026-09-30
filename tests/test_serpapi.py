import pytest
import requests

from tools import serpapi


class FakeGoogleSearch:
    payload = None
    error = None
    last_params = None

    def __init__(self, params, timeout=None):
        FakeGoogleSearch.last_params = params

    def get_json(self):
        if FakeGoogleSearch.error is not None:
            raise FakeGoogleSearch.error
        return FakeGoogleSearch.payload


@pytest.fixture
def fake_client(monkeypatch):
    def install(payload=None, error=None):
        FakeGoogleSearch.payload = payload
        FakeGoogleSearch.error = error
        FakeGoogleSearch.last_params = None
        monkeypatch.setattr(serpapi, "GoogleSearch", FakeGoogleSearch)
        return FakeGoogleSearch

    return install


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-key-123")
    return "test-key-123"


def test_normalizes_results(api_key, fake_client):
    fake_client(
        {
            "organic_results": [
                {
                    "title": "  Transformers  ",
                    "link": "https://example.com/a",
                    "snippet": " A snippet ",
                    "position": 1,
                },
                {
                    "title": "Second",
                    "url": "https://example.com/b",
                    "snippet": "Another",
                },
            ]
        }
    )

    results = serpapi.search_google("transformers", 10)

    assert results == [
        {"title": "Transformers", "url": "https://example.com/a", "snippet": "A snippet"},
        {"title": "Second", "url": "https://example.com/b", "snippet": "Another"},
    ]
    assert all(set(r) == {"title", "url", "snippet"} for r in results)


def test_drops_incomplete_results(api_key, fake_client):
    fake_client(
        {
            "organic_results": [
                {"title": "No snippet", "link": "https://example.com/a", "snippet": ""},
                {"title": "", "link": "https://example.com/b", "snippet": "s"},
                {"link": "https://example.com/c", "snippet": "s"},
                {"title": "Keep", "link": "https://example.com/d", "snippet": "s"},
            ]
        }
    )

    results = serpapi.search_google("transformers")

    assert results == [
        {"title": "Keep", "url": "https://example.com/d", "snippet": "s"}
    ]


def test_respects_num_results(api_key, fake_client):
    fake_client(
        {
            "organic_results": [
                {"title": f"R{i}", "link": f"https://example.com/{i}", "snippet": "s"}
                for i in range(5)
            ]
        }
    )

    assert len(serpapi.search_google("transformers", 2)) == 2


def test_no_organic_results_returns_empty_list(api_key, fake_client):
    fake_client({"search_metadata": {}})
    assert serpapi.search_google("transformers") == []


def test_forwards_api_key_from_environment(api_key, fake_client):
    client = fake_client({"organic_results": []})

    serpapi.search_google("transformers", 3)

    assert client.last_params["api_key"] == "test-key-123"
    assert client.last_params["engine"] == "google"
    assert client.last_params["num"] == 3
    assert client.last_params["q"] == "transformers"


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_empty_query_raises_value_error(query, api_key, fake_client):
    fake_client({"organic_results": []})
    with pytest.raises(ValueError):
        serpapi.search_google(query)


@pytest.mark.parametrize("num_results", [0, -1])
def test_invalid_num_results_raises_value_error(num_results, api_key, fake_client):
    fake_client({"organic_results": []})
    with pytest.raises(ValueError):
        serpapi.search_google("transformers", num_results)


def test_missing_api_key_raises(fake_client):
    fake_client({"organic_results": []})
    with pytest.raises(serpapi.MissingApiKeyError):
        serpapi.search_google("transformers")


def test_blank_api_key_raises(monkeypatch, fake_client):
    monkeypatch.setenv("SERPAPI_KEY", "   ")
    fake_client({"organic_results": []})
    with pytest.raises(serpapi.MissingApiKeyError):
        serpapi.search_google("transformers")


def test_api_error_payload_raises(api_key, fake_client):
    fake_client({"error": "Invalid API key."})
    with pytest.raises(serpapi.SearchRequestError):
        serpapi.search_google("transformers")


def test_network_error_raises(api_key, fake_client):
    fake_client(error=requests.ConnectionError("dns failure"))
    with pytest.raises(serpapi.SearchRequestError):
        serpapi.search_google("transformers")


def test_malformed_response_raises(api_key, fake_client):
    fake_client(error=ValueError("not json"))
    with pytest.raises(serpapi.SearchRequestError):
        serpapi.search_google("transformers")


def test_unexpected_response_shape_raises(api_key, fake_client):
    fake_client(["not", "a", "dict"])
    with pytest.raises(serpapi.SearchRequestError):
        serpapi.search_google("transformers")
