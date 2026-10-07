"""Tier 0 (Gutenberg, PMC BioC, OA resolver) and Tier 1 (Tavily, Firecrawl).

All network I/O is stubbed. Proves the fallback order (free OA copy first,
then Tavily, then Firecrawl), per-run paid caps, and that failures degrade to
the honest metadata-only path instead of breaking the run.
"""
import json

import pytest

from backend.research import config
from backend.research.fetch import Fetcher, FULL


class _Response:
    def __init__(self, text="", status_code=200, url="http://x/",
                 content_type="text/html"):
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers = {"content-type": content_type}
        self._body = text.encode()
        self._truncated = False

    def iter_content(self, size):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Session:
    def __init__(self, routes=None, default=None):
        self.routes = routes or {}
        self.default = default
        self.headers = {}

    def get(self, url, **kwargs):
        self.requests = getattr(self, "requests", []) + [url]
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response
        if self.default is not None:
            return self.default
        return _Response(text="", status_code=404)


GUTENBERG_TXT = """Title: Test Book
Author: Jane Doe, John Smith
Language: English

*** START OF THIS PROJECT GUTENBERG EBOOK TEST BOOK ***

Chapter one text here, with enough words to count as a real document
for the purposes of this test suite and then some more words to push past
any minimum length guard, with additional sentences about nothing in
particular except filling space with perfectly readable prose that goes on
for a while longer to resemble the opening pages of a genuine book.

*** END OF THIS PROJECT GUTENBERG EBOOK TEST BOOK ***
"""


def test_gutenberg_reads_full_text(monkeypatch):
    monkeypatch.setattr(config, "fetch_respect_robots", lambda: False)
    session = _Session(routes={
        "https://www.gutenberg.org/ebooks/1":
            _Response(text="<html><body>ebook page</body></html>",
                      url="https://www.gutenberg.org/ebooks/1"),
        "https://www.gutenberg.org/cache/epub/1/pg1.txt":
            _Response(text=GUTENBERG_TXT, url="https://x/pg1.txt",
                      content_type="text/plain"),
    })
    doc = Fetcher(session=session).fetch("https://www.gutenberg.org/ebooks/1")

    assert doc.status == FULL
    assert doc.method == "gutenberg"
    assert "Chapter one text" in doc.text
    assert "PROJECT GUTENBERG" not in doc.text
    assert doc.title == "Test Book"
    assert doc.authors == ["Jane Doe", "John Smith"]


def test_gutenberg_falls_through_when_txt_missing(monkeypatch):
    monkeypatch.setattr(config, "fetch_respect_robots", lambda: False)
    landing = ("<html><head><title>Book</title></head><body><article><p>"
               + ("readable landing text. " * 60) + "</p></article></body></html>")
    session = _Session(default=_Response(text=landing, url="https://x/"))
    doc = Fetcher(session=session).fetch("https://www.gutenberg.org/ebooks/2")

    assert doc.status == FULL
    assert doc.method == "html"


BIOC = {"documents": [{"passages": [
    {"infons": {"section_type": "INTRO"},
     "text": "Background on the topic with substantial discussion of prior "
             "work and the motivation for the present study across many "
             "sentences of scientific prose that easily exceed minimums."},
    {"infons": {},
     "text": "A plain passage with further methodological detail describing "
             "the experimental setup, the measurements taken, and the analysis "
             "performed on the resulting data set in full."},
]}]}


def test_bioc_returns_sectioned_full_text(monkeypatch):
    monkeypatch.setattr(config, "fetch_respect_robots", lambda: False)
    session = _Session(routes={
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/":
            _Response(text="<html><body>article</body></html>",
                      url="https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"),
        "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/":
            _Response(text=json.dumps(BIOC), url="https://x/bioc",
                      content_type="application/json"),
    })
    doc = Fetcher(session=session).fetch(
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/")

    assert doc.status == FULL
    assert doc.method == "pmc"
    assert "## INTRO" in doc.text
    assert "Background on the topic with substantial discussion" in doc.text


def test_bioc_failure_falls_back_to_article_html(monkeypatch):
    monkeypatch.setattr(config, "fetch_respect_robots", lambda: False)
    landing = ("<html><head><title>Paper</title></head><body><article><p>"
               + ("article body text. " * 60) + "</p></article></body></html>")
    session = _Session(
        routes={"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/":
                _Response(text="", status_code=404)},
        default=_Response(text=landing, url="https://x/paper"))
    doc = Fetcher(session=session).fetch(
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/")

    assert doc.status == FULL
    assert doc.method == "html"


def test_oa_resolver_prefers_openalex_pdf(monkeypatch):
    import backend.research.oa as oa_module

    def fake_get(url, params=None, **kwargs):
        class Resp:
            status_code = 200

            def json(self):
                assert "openalex" in url
                return {"best_oa_location": {
                    "pdf_url": "https://arxiv.org/pdf/1.pdf"}}
        return Resp()

    monkeypatch.setattr("requests.get", fake_get)
    assert oa_module.resolve_pdf_url("https://arxiv.org/abs/2404.02258") == \
        "https://arxiv.org/pdf/1.pdf"


def test_oa_resolver_returns_empty_without_identifiers(monkeypatch):
    import backend.research.oa as oa_module

    def boom(url, params=None, **kwargs):
        raise AssertionError("no network should be attempted")
    monkeypatch.setattr("requests.get", boom)

    assert oa_module.resolve_pdf_url("https://blog.dev/some-post") == ""


def test_tavily_recovers_failed_fetch(monkeypatch):
    import backend.research.webextract as webextract_module

    monkeypatch.setattr(config, "fetch_respect_robots", lambda: False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(
        webextract_module, "tavily_extract",
        lambda url: ("Walled Page", "recovered body text " * 60))
    monkeypatch.setattr("backend.research.oa.resolve_pdf_url",
                        lambda url: "")

    session = _Session(default=_Response(text="", status_code=403))
    fetcher = Fetcher(session=session)
    doc = fetcher.fetch("https://walled.dev/article")

    assert doc.status == FULL
    assert doc.method == "tavily"
    assert "recovered body text" in doc.text
    assert fetcher.tavily_used == 1


def test_firecrawl_runs_only_after_tavily_fails(monkeypatch):
    import backend.research.webextract as webextract_module
    import backend.research.fetch as fetch_module

    monkeypatch.setattr(config, "fetch_respect_robots", lambda: False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(webextract_module, "tavily_extract", lambda url: None)
    monkeypatch.setattr(
        webextract_module, "firecrawl_scrape",
        lambda url: ("Walled", "firecrawl body text " * 60))
    monkeypatch.setattr("backend.research.oa.resolve_pdf_url", lambda url: "")

    session = _Session(default=_Response(text="", status_code=403))
    fetcher = Fetcher(session=session)
    doc = fetcher.fetch("https://walled.dev/article")

    assert doc.method == "firecrawl"
    assert fetcher.tavily_used == 1
    assert fetcher.firecrawl_used == 1


def test_paid_fallbacks_capped_per_run(monkeypatch):
    import backend.research.webextract as webextract_module
    import backend.research.fetch as fetch_module

    monkeypatch.setattr(config, "fetch_respect_robots", lambda: False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("TAVILY_MAX_PER_RUN", "1")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")
    monkeypatch.setattr("backend.research.oa.resolve_pdf_url", lambda url: "")
    calls = []
    monkeypatch.setattr(
        webextract_module, "tavily_extract",
        lambda url: calls.append(url) or ("T", "body text " * 60))

    session = _Session(default=_Response(text="", status_code=403))
    fetcher = Fetcher(session=session)
    fetcher.fetch("https://walled.dev/a")
    doc = fetcher.fetch("https://walled.dev/b")

    assert calls == ["https://walled.dev/a"]
    assert doc.status != FULL


def test_no_keys_means_no_paid_calls(monkeypatch):
    import backend.research.fetch as fetch_module

    monkeypatch.setattr(config, "fetch_respect_robots", lambda: False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr("backend.research.oa.resolve_pdf_url", lambda url: "")

    session = _Session(default=_Response(text="", status_code=403))
    doc = Fetcher(session=session).fetch("https://walled.dev/article")

    assert doc.status == "failed"
