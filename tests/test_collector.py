from evidence import collector

EVIDENCE_FIELDS = {"title", "url", "snippet", "source", "date"}


def test_removes_duplicate_urls_keeping_first_occurrence():
    results = [
        {
            "title": "First",
            "url": "https://example.com/a",
            "snippet": "keep me",
            "source": "Example",
            "date": "2026-01-01",
        },
        {"title": "Duplicate", "url": "https://example.com/a", "snippet": "drop me"},
        {"title": "Second", "url": "https://example.com/b"},
    ]

    evidence = collector.collect_evidence(results)

    assert [item["url"] for item in evidence] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert evidence[0]["title"] == "First"
    assert evidence[0]["snippet"] == "keep me"


def test_missing_fields_become_empty_strings_and_extra_fields_are_ignored():
    evidence = collector.collect_evidence(
        [{"url": "https://example.com/a", "thumbnail": "https://example.com/a.jpg"}]
    )

    assert evidence == [
        {
            "title": "",
            "url": "https://example.com/a",
            "snippet": "",
            "source": "",
            "date": "",
        }
    ]
    assert all(set(item) == EVIDENCE_FIELDS for item in evidence)


def test_skips_results_without_a_url():
    evidence = collector.collect_evidence(
        [
            {"title": "No link", "snippet": "drop me"},
            {"title": "Blank link", "url": "   "},
            "junk",
            {"title": "Keep", "url": "https://example.com/a"},
        ]
    )

    assert [item["title"] for item in evidence] == ["Keep"]
