import pytest
import requests

from tools import github


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def fake_get(monkeypatch):
    def install(response=None, error=None):
        captured = {}

        def _get(url, params=None, headers=None, timeout=None):
            captured.update({"url": url, "params": params, "headers": headers})
            if error is not None:
                raise error
            return response

        monkeypatch.setattr(github.requests, "get", _get)
        return captured

    return install


def payload(items):
    return {"total_count": len(items), "incomplete_results": False, "items": items}


def test_normalizes_results(fake_get):
    fake_get(
        FakeResponse(
            payload=payload(
                [
                    {
                        "name": "fastapi",
                        "full_name": "fastapi/fastapi",
                        "html_url": "https://github.com/fastapi/fastapi",
                        "description": "  FastAPI framework  ",
                        "stargazers_count": 102718,
                        "owner": {"login": "fastapi"},
                    },
                    {
                        "name": "  second  ",
                        "html_url": "https://github.com/o/second",
                        "description": None,
                        "stargazers_count": None,
                    },
                ]
            )
        )
    )

    results = github.search_github("fastapi", 10)

    assert results == [
        {
            "name": "fastapi",
            "url": "https://github.com/fastapi/fastapi",
            "description": "FastAPI framework",
            "stars": 102718,
        },
        {
            "name": "second",
            "url": "https://github.com/o/second",
            "description": "",
            "stars": 0,
        },
    ]
    assert all(set(r) == {"name", "url", "description", "stars"} for r in results)


def test_falls_back_to_full_name(fake_get):
    fake_get(
        FakeResponse(
            payload=payload(
                [{"full_name": "o/repo", "html_url": "https://github.com/o/repo"}]
            )
        )
    )

    assert github.search_github("repo")[0]["name"] == "o/repo"


def test_drops_incomplete_results(fake_get):
    fake_get(
        FakeResponse(
            payload=payload(
                [
                    {"name": "", "html_url": "https://github.com/a", "stargazers_count": 1},
                    {"name": "no-url", "stargazers_count": 1},
                    "junk",
                    {"name": "keep", "html_url": "https://github.com/keep"},
                ]
            )
        )
    )

    results = github.search_github("repo")

    assert [r["name"] for r in results] == ["keep"]


def test_respects_num_results(fake_get):
    fake_get(
        FakeResponse(
            payload=payload(
                [
                    {"name": f"r{i}", "html_url": f"https://github.com/{i}"}
                    for i in range(5)
                ]
            )
        )
    )

    assert len(github.search_github("repo", 2)) == 2


def test_no_results_returns_empty_list(fake_get):
    fake_get(FakeResponse(payload=payload([])))
    assert github.search_github("repo") == []


def test_caps_per_page(fake_get):
    captured = fake_get(FakeResponse(payload=payload([])))

    github.search_github("repo", 500)

    assert captured["params"]["per_page"] == 100
    assert captured["url"] == "https://api.github.com/search/repositories"


def test_sends_bearer_token_when_available(monkeypatch, fake_get):
    monkeypatch.setenv("GITHUB_TOKEN", "  ghp_test  ")
    captured = fake_get(FakeResponse(payload=payload([])))

    github.search_github("repo")

    assert captured["headers"]["Authorization"] == "Bearer ghp_test"


def test_omits_authorization_without_token(fake_get):
    captured = fake_get(FakeResponse(payload=payload([])))

    github.search_github("repo")

    assert "Authorization" not in captured["headers"]


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_empty_query_raises_value_error(query, fake_get):
    fake_get(FakeResponse(payload=payload([])))
    with pytest.raises(ValueError):
        github.search_github(query)


@pytest.mark.parametrize("num_results", [0, -1])
def test_invalid_num_results_raises_value_error(num_results, fake_get):
    fake_get(FakeResponse(payload=payload([])))
    with pytest.raises(ValueError):
        github.search_github("repo", num_results)


@pytest.mark.parametrize(
    "status_code,message",
    [
        (403, "API rate limit exceeded"),
        (401, "Bad credentials"),
        (422, "Validation Failed"),
        (500, "Server Error"),
    ],
)
def test_api_failure_raises_search_request_error(status_code, message, fake_get):
    fake_get(FakeResponse(status_code=status_code, payload={"message": message}))

    with pytest.raises(github.SearchRequestError) as exc_info:
        github.search_github("repo")

    assert message in str(exc_info.value)


def test_api_failure_without_json_body_raises(fake_get):
    fake_get(FakeResponse(status_code=502, text="<html>bad gateway</html>"))

    with pytest.raises(github.SearchRequestError) as exc_info:
        github.search_github("repo")

    assert "502" in str(exc_info.value)


def test_network_error_raises(fake_get):
    fake_get(error=requests.ConnectionError("dns failure"))

    with pytest.raises(github.SearchRequestError):
        github.search_github("repo")


def test_malformed_response_raises(fake_get):
    fake_get(FakeResponse(text="not json"))

    with pytest.raises(github.SearchRequestError):
        github.search_github("repo")


def test_unexpected_response_shape_raises(fake_get):
    fake_get(FakeResponse(payload=["not", "a", "dict"]))

    with pytest.raises(github.SearchRequestError):
        github.search_github("repo")
