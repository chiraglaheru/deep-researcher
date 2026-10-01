from research import collector

RESULT_FIELDS = {"title", "url", "snippet", "source"}

GOOGLE_RESULT = {
    "title": "Rust game engines",
    "url": "https://example.com/rust",
    "snippet": "A comparison of engines",
    "source": "google",
}

GITHUB_RESULT = {
    "title": "bevy",
    "url": "https://github.com/bevyengine/bevy",
    "snippet": "A game engine",
    "source": "github",
}

NEWS_RESULT = {
    "title": "Bevy 1.0 released",
    "url": "https://news.example.com/bevy",
    "snippet": "The long awaited release",
    "source": "news",
}


def test_combines_results_from_multiple_searches():
    google = [
        dict(GOOGLE_RESULT),
        {
            "title": "Second",
            "url": "https://example.com/2",
            "snippet": "Another page",
            "source": "google",
        },
    ]
    github = [dict(GITHUB_RESULT)]
    news = [dict(NEWS_RESULT)]

    collected = collector.collect_results(google + github + news)

    assert [item["url"] for item in collected] == [
        "https://example.com/rust",
        "https://example.com/2",
        "https://github.com/bevyengine/bevy",
        "https://news.example.com/bevy",
    ]
    assert [item["source"] for item in collected] == [
        "google",
        "google",
        "github",
        "news",
    ]


def test_removes_duplicate_urls_across_searches():
    results = [
        dict(GOOGLE_RESULT),
        dict(GITHUB_RESULT),
        dict(NEWS_RESULT),
        dict(GOOGLE_RESULT),
    ]

    collected = collector.collect_results(results)

    assert [item["url"] for item in collected] == [
        "https://example.com/rust",
        "https://github.com/bevyengine/bevy",
        "https://news.example.com/bevy",
    ]


def test_keeps_the_first_occurrence_of_a_duplicate_url():
    first = {
        "title": "First title",
        "url": "https://example.com/a",
        "snippet": "First snippet",
        "source": "google",
    }
    second = dict(first, title="Second title", snippet="Second snippet", source="news")

    collected = collector.collect_results([first, second])

    assert collected == [first]


def test_empty_input_returns_empty_list():
    assert collector.collect_results([]) == []


def test_none_input_returns_empty_list():
    assert collector.collect_results(None) == []


def test_missing_fields_become_empty_strings_and_extra_fields_are_dropped():
    collected = collector.collect_results(
        [{"url": "https://example.com/a", "thumbnail": "https://example.com/a.jpg"}]
    )

    assert collected == [
        {"title": "", "url": "https://example.com/a", "snippet": "", "source": ""}
    ]


def test_invalid_entries_are_skipped_without_crashing():
    collected = collector.collect_results(
        [
            "not a dict",
            None,
            42,
            ["https://example.com/list"],
            {"url": "https://example.com/a", "source": "google"},
            "",
            dict(GITHUB_RESULT),
        ]
    )

    assert [item["url"] for item in collected] == [
        "https://example.com/a",
        "https://github.com/bevyengine/bevy",
    ]


def test_results_without_a_usable_url_are_skipped():
    collected = collector.collect_results(
        [
            {"title": "No url", "source": "google"},
            {"title": "Empty url", "url": "", "source": "google"},
            {"title": "Blank url", "url": "   ", "source": "google"},
            {"title": "None url", "url": None, "source": "google"},
            dict(NEWS_RESULT),
        ]
    )

    assert collected == [NEWS_RESULT]


def test_all_entries_invalid_returns_empty_list():
    assert collector.collect_results(["junk", None, 7, {}]) == []


def test_output_has_exactly_the_result_fields():
    collected = collector.collect_results([dict(GOOGLE_RESULT), dict(NEWS_RESULT)])

    assert all(set(item) == RESULT_FIELDS for item in collected)


def test_values_are_stripped():
    collected = collector.collect_results(
        [
            {
                "title": "  Rust game engines  ",
                "url": "  https://example.com/rust  ",
                "snippet": "  A comparison of engines  ",
                "source": "  google  ",
            }
        ]
    )

    assert collected == [GOOGLE_RESULT]


def test_input_list_is_not_mutated():
    results = [dict(GOOGLE_RESULT), dict(GOOGLE_RESULT)]

    collector.collect_results(results)

    assert results == [dict(GOOGLE_RESULT), dict(GOOGLE_RESULT)]
