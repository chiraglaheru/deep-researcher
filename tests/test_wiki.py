from unittest.mock import patch, Mock

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