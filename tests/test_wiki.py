from unittest.mock import patch, Mock

import backend.research.wiki as wiki_module
from backend.research.wiki import _fetch_extracts


def test_wikipedia_fetch_preserves_title_order():
    titles = [
        "Alan Turing",
        "Timeline of artificial intelligence",
        "History of artificial intelligence",
    ]

    responses = {
        "Alan Turing": "<p>Alan Turing content.</p>",
        "Timeline of artificial intelligence": "<p>AI timeline content.</p>",
        "History of artificial intelligence": "<p>AI history content.</p>",
    }

    requested_titles = []

    def mock_get(url, params=None, **kwargs):
        title = params["page"]
        requested_titles.append(title)

        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "parse": {
                "title": title,
                "text": responses[title],
            }
        }
        return response

    with patch("requests.get", side_effect=mock_get):
        docs = _fetch_extracts(titles)

    assert requested_titles == titles
    assert [doc.title for doc in docs] == titles
    assert [doc.text for doc in docs] == [
        "Alan Turing content.",
        "AI timeline content.",
        "AI history content.",
    ]

def test_fetch_wikipedia_pools_subquestion_titles(monkeypatch):
    """One keyword query per sub-question; distinct titles pooled under the cap."""
    monkeypatch.setenv("WIKI_ENABLED", "1")
    monkeypatch.setenv("WIKI_MAX_DOCS", "3")
    monkeypatch.setenv("WIKI_TITLES_PER_QUERY", "2")

    main = "Compare Mistral Large 2 and Llama 3 for batch inference"
    sub_one = "What is the throughput of Mistral Large 2?"
    sub_two = "How does context window affect Llama 3?"

    from backend.research.relevance import search_keywords
    main_q = search_keywords(main)
    sub_one_q = search_keywords(main, sub_one)
    sub_two_q = search_keywords(main, sub_two)

    # Keyword-only: no basic English words in any query.
    filler = {"what", "is", "the", "how", "does", "for", "and", "with"}
    for query in (main_q, sub_one_q, sub_two_q):
        assert query, "every query must carry keywords"
        assert not (set(query.split()) & filler), query
    # Topic terms shared with the main question lead each sub-question query.
    assert "mistral" in sub_one_q and "throughput" in sub_one_q
    assert "llama" in sub_two_q and "context" in sub_two_q

    by_query = {
        main_q: ["Alpha", "Beta"],
        sub_one_q: ["Beta", "Gamma"],
        sub_two_q: ["Delta"],
    }
    searches = []

    def fake_search(query, limit=5):
        searches.append(query)
        return by_query[query]

    fetched = []

    def fake_extracts(titles, chars=6000):
        fetched.extend(titles)
        return []

    monkeypatch.setattr(wiki_module, "_search_titles", fake_search)
    monkeypatch.setattr(wiki_module, "_fetch_extracts", fake_extracts)

    wiki_module.fetch_wikipedia(main, subquestions=[sub_one, sub_two])

    assert searches == [main_q, sub_one_q]
    # Question hits first, duplicates dropped, capped at WIKI_MAX_DOCS
    # ("sub two" is never searched once the pool is full).
    assert fetched == ["Alpha", "Beta", "Gamma"]
