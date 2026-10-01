import pytest

from research import searcher

EVIDENCE_FIELDS = {"title", "url", "snippet", "source"}

GOOGLE_RESULT = {
    "title": "Rust game engines",
    "url": "https://example.com/rust",
    "snippet": "A comparison of engines",
    "date": "2026-01-01",
    "position": 1,
}

GITHUB_RESULT = {
    "name": "bevy",
    "url": "https://github.com/bevyengine/bevy",
    "description": "A game engine",
    "stars": 4200,
}

NEWS_RESULT = {
    "title": "Bevy 1.0 released",
    "url": "https://news.example.com/bevy",
    "snippet": "The long awaited release",
    "date": "01/02/2026",
}

GOOGLE_EVIDENCE = {
    "title": "Rust game engines",
    "url": "https://example.com/rust",
    "snippet": "A comparison of engines",
    "source": "google",
}

GITHUB_EVIDENCE = {
    "title": "bevy",
    "url": "https://github.com/bevyengine/bevy",
    "snippet": "A game engine",
    "source": "github",
}

NEWS_EVIDENCE = {
    "title": "Bevy 1.0 released",
    "url": "https://news.example.com/bevy",
    "snippet": "The long awaited release",
    "source": "news",
}


TOOL_MODULES = {"google": "serpapi", "github": "github", "news": "news"}

TOOL_ERRORS = {
    "google": searcher.serpapi.SerpApiError,
    "github": searcher.github.GitHubError,
    "news": searcher.news.SerpApiError,
}


class FakeTools:
    """Stand-in for the three search tools; records calls instead of hitting APIs."""

    def __init__(self):
        self.calls = []
        self.results = {"google": [], "github": [], "news": []}
        self.errors = {}

    def returns(self, source, items):
        self.results[source] = items

    def fails(self, source, error=None):
        if error is None:
            error = TOOL_ERRORS[source](f"{source} is down")
        self.errors[source] = error

    @property
    def sources_called(self):
        return [source for source, _ in self.calls]


@pytest.fixture
def fake_tools(monkeypatch):
    tools = FakeTools()

    def make_runner(source):
        def run(query):
            tools.calls.append((source, query))
            if source in tools.errors:
                raise tools.errors[source]
            return tools.results[source]

        return run

    for source, module_name in TOOL_MODULES.items():
        monkeypatch.setattr(
            getattr(searcher, module_name), f"search_{source}", make_runner(source)
        )

    tools.returns("google", [dict(GOOGLE_RESULT)])
    tools.returns("github", [dict(GITHUB_RESULT)])
    tools.returns("news", [dict(NEWS_RESULT)])
    return tools


def test_general_query_uses_google_only(fake_tools):
    evidence = searcher.search_subquestion("How do I learn integration?")

    assert fake_tools.sources_called == ["google"]
    assert evidence == [GOOGLE_EVIDENCE]


def test_github_query_uses_google_and_github(fake_tools):
    evidence = searcher.search_subquestion("Rust game engine repositories")

    assert fake_tools.sources_called == ["google", "github"]
    assert evidence == [GOOGLE_EVIDENCE, GITHUB_EVIDENCE]


def test_news_query_uses_google_and_news(fake_tools):
    evidence = searcher.search_subquestion("Latest Rust game engine developments")

    assert fake_tools.sources_called == ["google", "news"]
    assert evidence == [GOOGLE_EVIDENCE, NEWS_EVIDENCE]


def test_comparison_query_uses_all_three_tools_in_order(fake_tools):
    evidence = searcher.search_subquestion("Rust vs C++ for game engines")

    assert fake_tools.sources_called == ["google", "github", "news"]
    assert evidence == [GOOGLE_EVIDENCE, GITHUB_EVIDENCE, NEWS_EVIDENCE]


def test_github_failure_still_returns_google_results(fake_tools):
    fake_tools.fails("github", searcher.github.SearchRequestError("rate limited"))

    evidence = searcher.search_subquestion("Rust game engine repositories")

    assert fake_tools.sources_called == ["google", "github"]
    assert evidence == [GOOGLE_EVIDENCE]


def test_news_failure_still_returns_google_results(fake_tools):
    fake_tools.fails("news", searcher.news.MissingApiKeyError("no SERPAPI_KEY"))

    evidence = searcher.search_subquestion("Latest Rust game engine developments")

    assert fake_tools.sources_called == ["google", "news"]
    assert evidence == [GOOGLE_EVIDENCE]


def test_google_failure_does_not_crash_when_another_tool_succeeds(fake_tools):
    fake_tools.fails("google", searcher.serpapi.SearchRequestError("bad key"))

    evidence = searcher.search_subquestion("Rust game engine repositories")

    assert fake_tools.sources_called == ["google", "github"]
    assert evidence == [GITHUB_EVIDENCE]


def test_every_tool_failing_returns_empty_list(fake_tools):
    for source in ("google", "github", "news"):
        fake_tools.fails(source)

    assert searcher.search_subquestion("Rust vs C++ for game engines") == []


def test_unexpected_programming_error_is_not_swallowed(fake_tools):
    fake_tools.fails("github", AttributeError("tool returned a broken result"))

    with pytest.raises(AttributeError):
        searcher.search_subquestion("Rust game engine repositories")


def test_output_has_exactly_title_url_snippet_source(fake_tools):
    evidence = searcher.search_subquestion("Rust vs C++ for game engines")

    assert evidence
    assert all(set(item) == EVIDENCE_FIELDS for item in evidence)
    assert all("date" not in item for item in evidence)
    assert [item["source"] for item in evidence] == ["google", "github", "news"]


def test_missing_and_extra_fields_are_normalized(fake_tools):
    fake_tools.returns("github", [{"name": "wgpu", "stars": 7, "language": "Rust"}])

    evidence = searcher.search_subquestion("wgpu repositories")

    assert evidence[1] == {
        "title": "wgpu",
        "url": "",
        "snippet": "",
        "source": "github",
    }


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_blank_query_raises_value_error_without_searching(query, fake_tools):
    with pytest.raises(ValueError):
        searcher.search_subquestion(query)

    assert fake_tools.calls == []
