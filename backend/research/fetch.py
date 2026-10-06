"""Retrieve the actual document behind a search result.

The pipeline used to treat a search snippet as if it were the source. This
module fetches the real thing and, crucially, records *how much* of it we
actually got. A snippet, a paywalled stub and a fully-parsed RFC are three
different evidence strengths, and that difference is carried all the way into
the report instead of being quietly flattened.

Retrieval strategies, in the order they are tried:

  pdf      -> pypdf, page by page
  arxiv    -> arXiv API for authoritative metadata + abstract, then the abs page
  github   -> GitHub API for repo metadata, raw README for content
  plain    -> text/markdown/json served as-is
  html     -> lxml parse, boilerplate stripped, structure preserved

Nothing here tries to defeat a paywall, a login wall or a robots.txt rule. When
those block us the document is downgraded and the reason is preserved.
"""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin, urlparse, unquote
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 deep-researcher/2.0"
)

# Status values, weakest last. Index order is meaningful for ranking.
FULL = "full"              # whole usable document text extracted
PARTIAL = "partial"        # some of it: truncated, JS-rendered, paywalled, partial PDF
METADATA = "metadata_only"  # we only ever got the snippet/abstract
FAILED = "failed"          # nothing usable

_ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?$", re.I)
_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")


_GITHUB_RESERVED = frozenset({
    "orgs", "sponsors", "topics", "collections", "settings", "features",
    "marketplace", "apps", "notifications", "explore", "search", "about",
    "pricing", "login", "join", "new", "pulls", "issues", "codespaces",
})


def github_repo(url: str) -> tuple[str, str] | None:
    """(owner, repo) for a GitHub repository URL, else None.

    Parsed rather than regexed: the host may carry a scheme, a ``www.`` prefix
    or a trailing fragment, and the repo name may be followed by extra path
    segments (``/tree/main``) which are not part of the repo name.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0].lower() in _GITHUB_RESERVED:
        return None
    return parts[0], parts[1].removesuffix(".git")

_INVISIBLE = dict.fromkeys(
    map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f"), None)

# Text that means "the rest of this article is not for you".
_PAYWALL_MARKERS = (
    "subscribe to continue", "sign in to read", "become a member",
    "members only", "this article is for subscribers", "paywall",
    "create an account to continue", "you've reached your limit",
    "subscribers only", "start your free trial to read",
)

# Elements that never carry research content.
_NOISE_TAGS = (
    "script", "style", "noscript", "nav", "footer", "header", "aside", "form",
    "iframe", "svg", "button", "input", "select", "textarea", "template",
)

_robots_cache: dict[str, RobotFileParser | None] = {}


@dataclass
class FetchedDoc:
    """One retrieval attempt, with its provenance and its limits."""

    url: str
    final_url: str = ""
    status: str = FAILED
    method: str = "none"            # pdf | arxiv | github | plain | html | none
    title: str = ""
    text: str = ""
    publisher: str = ""
    authors: list[str] = field(default_factory=list)
    date: str = ""
    doi: str = ""
    content_type: str = ""
    pages: int = 0
    bytes_read: int = 0
    truncated: bool = False
    limitation: str = ""            # why this is not FULL. Human-readable.
    links: list[str] = field(default_factory=list)   # outbound, for reference discovery

    @property
    def usable(self) -> bool:
        return self.status in (FULL, PARTIAL) and bool(self.text.strip())

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def as_dict(self) -> dict:
        data = asdict(self)
        data.pop("text", None)       # keep the log small; the pipeline holds text
        data["word_count"] = self.word_count
        return data


class Fetcher:
    """Fetches documents. One instance per research run so budgets are shared."""

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT,
                                     "Accept-Language": "en;q=0.9"})
        self.budget = config.total_fetch_budget()
        self.fetched = 0

    # -- budget ------------------------------------------------------------
    def exhausted(self) -> bool:
        return self.fetched >= self.budget

    # -- robots ------------------------------------------------------------
    def _allowed(self, url: str) -> tuple[bool, str]:
        if not config.fetch_respect_robots():
            return True, ""
        host = urlparse(url).netloc
        if host not in _robots_cache:
            parser: RobotFileParser | None = None
            try:
                resp = self.session.get(f"{urlparse(url).scheme}://{host}/robots.txt",
                                        timeout=10)
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
                elif resp.status_code in (401, 403):
                    parser = RobotFileParser()
                    parser.parse(["User-agent: *", "Disallow: /"])
            except requests.RequestException:
                parser = None       # unreachable robots.txt: do not block
            _robots_cache[host] = parser
        parser = _robots_cache[host]
        if parser is None:
            return True, ""
        if parser.can_fetch(USER_AGENT, url):
            return True, ""
        return False, "blocked by robots.txt"

    # -- http --------------------------------------------------------------
    def _get(self, url: str) -> requests.Response:
        timeout = config.fetch_timeout_s()
        with self.session.get(url, timeout=timeout, stream=True,
                              allow_redirects=True) as resp:
            self.fetched += 1
            cap = config.fetch_max_bytes()
            buf = bytearray()
            for piece in resp.iter_content(64 * 1024):
                buf.extend(piece)
                if len(buf) >= cap:
                    break
            resp._body = bytes(buf)          # noqa: SLF001 - requests has no public hook
            resp._truncated = len(buf) >= cap   # noqa: SLF001
            return resp

    @staticmethod
    def _body(resp: requests.Response) -> tuple[bytes, bool]:
        return getattr(resp, "_body", b""), getattr(resp, "_truncated", False)

    # -- entry point -------------------------------------------------------
    def fetch(self, url: str) -> FetchedDoc:
        doc = FetchedDoc(url=url)
        try:
            allowed, why = self._allowed(url)
            if not allowed:
                doc.status, doc.limitation = FAILED, why
                return doc

            resp = self._get(url)
            doc.final_url = str(resp.url)
            doc.content_type = resp.headers.get("content-type", "")
            body, truncated = self._body(resp)

            # A 429 or a transient 5xx is worth one retry: the origin is asking
            # us to slow down, not refusing us permanently.
            if resp.status_code in (429, 500, 502, 503, 504):
                retry = self._retry_after(resp)
                if retry is not None:
                    time.sleep(retry)
                    resp = self._get(url)
                    doc.final_url = str(resp.url)
                    doc.content_type = resp.headers.get("content-type", "")
                    body, truncated = self._body(resp)

            if resp.status_code >= 400:
                doc.status = FAILED
                doc.limitation = f"HTTP {resp.status_code}"
                return doc

            if not body.strip():
                doc.status, doc.limitation = FAILED, "empty response body"
                return doc

            doc = self._dispatch(doc, body, truncated, resp)

            if len(doc.text) > config.fetch_max_chars():
                doc.text = doc.text[: config.fetch_max_chars()]
                doc.truncated = True
                doc.status = PARTIAL
                doc.limitation = _join(doc.limitation,
                                       "text truncated to configured character limit")
            return doc

        except requests.Timeout:
            doc.status, doc.limitation = FAILED, "timed out"
        except requests.RequestException as exc:
            doc.status, doc.limitation = FAILED, f"network error: {type(exc).__name__}"
        except Exception as exc:                      # never let one URL kill the run
            log.warning("fetch failed for %s: %s", url, exc, exc_info=True)
            doc.status, doc.limitation = FAILED, f"{type(exc).__name__}: {exc}"[:200]
        return doc

    @staticmethod
    def _retry_after(resp: requests.Response, attempts: int = 2) -> float | None:
        """Seconds to wait before retrying a rate-limited or transient failure."""
        if attempts <= 0:
            return None
        header = resp.headers.get("Retry-After", "")
        try:
            if header.strip().isdigit():
                return min(float(header), 10.0)
        except ValueError:
            pass
        return min(2.0 ** (attempts - 1), 8.0)

    def _dispatch(self, doc, body: bytes, truncated: bool, resp) -> FetchedDoc:
        doc.bytes_read = len(body)
        head = body[:2048].lower()

        if "application/pdf" in doc.content_type or head[:5] == b"%pdf-":
            return self._from_pdf(doc, body)
        if _ARXIV_ID.search(doc.final_url or doc.url):
            return self._from_arxiv(doc, body)
        match = github_repo(doc.final_url or doc.url)
        if match and "text/html" in doc.content_type:
            return self._from_github(doc, match[0], match[1], body)
        if any(t in doc.content_type for t in ("text/plain", "text/markdown",
                                               "application/json", "text/csv")):
            return self._from_plain(doc, body)
        if "text/html" in doc.content_type or b"<html" in head:
            doc = self._from_html(doc, body, truncated)
            if "patents.google.com" in (doc.final_url or doc.url):
                doc.method = "patent"
            return doc
        return self._from_plain(doc, body)             # unknown type: try as text

    # -- pdf ---------------------------------------------------------------
    def _from_pdf(self, doc: FetchedDoc, body: bytes) -> FetchedDoc:
        doc.method = "pdf"
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(body))
            max_pages = config.fetch_max_pages()
            pages = []
            for index, page in enumerate(reader.pages):
                if index >= max_pages:
                    doc.truncated = True
                    break
                try:
                    pages.append(page.extract_text() or "")
                except Exception:                       # one bad page, keep going
                    pages.append("")
            doc.pages = len(reader.pages)
            # \f marks a page break so the chunker can attribute chunks to pages.
            doc.text = _clean("\n\f\n".join(pages))
            info = getattr(reader, "metadata", None) or {}
            doc.title = _str(info.get("/Title"))
            doc.authors = _authors(info.get("/Author"))
            doc.date = _str(info.get("/CreationDate"))
            doc.status = FULL if not doc.truncated else PARTIAL
            if doc.truncated:
                doc.limitation = f"parsed first {max_pages} of {doc.pages} pages"
            if not doc.text.strip():
                doc.status, doc.limitation = METADATA, "PDF contains no extractable text (likely scanned)"
            return doc
        except Exception as exc:
            # A truncated download is missing its trailer, which pypdf reports
            # as "EOF marker not found". That is a cut-off file in transit, not
            # a JSON/parse bug elsewhere: say so plainly instead of leaking the
            # raw parser error into the limitation.
            if "eof" in str(exc).lower():
                doc.status, doc.method = FAILED, "pdf"
                doc.limitation = ("PDF truncated mid-download (trailer/EOF marker "
                                  "missing); nothing could be parsed")
            else:
                doc.status, doc.method = FAILED, "pdf"
                doc.limitation = f"PDF parse failed: {type(exc).__name__}"
            return doc

    # -- arxiv -------------------------------------------------------------
    def _from_arxiv(self, doc: FetchedDoc, body: bytes) -> FetchedDoc:
        """arXiv metadata from the API is authoritative; the abs page gives the rest."""
        doc.method = "arxiv"
        match = _ARXIV_ID.search(doc.final_url or doc.url)
        arxiv_id = match.group(1) if match else ""
        if arxiv_id:
            api = self._arxiv_api(arxiv_id)
            if api:
                doc.title = api["title"]
                doc.authors = api["authors"]
                doc.date = api["date"]
                doc.doi = api["doi"]
                doc.publisher = api["publisher"]
                doc.text = api["abstract"]
                doc.status = FULL
        if doc.status != FULL:
            doc = self._from_html(doc, body, truncated=False)
        # PDFs served from /pdf/ give the whole paper.
        if "text/html" not in doc.content_type and len(doc.text) > 2000:
            doc.status = FULL
        if doc.title and not doc.text.strip():
            doc.status = METADATA
        return doc

    def _arxiv_api(self, arxiv_id: str) -> dict | None:
        clean = arxiv_id.split("v")[0]
        url = f"http://export.arxiv.org/api/query?id_list={clean}&max_results=1"
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                return None
            root = ET.fromstring(resp.text)
        except Exception:
            return None
        ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        entry = root.find("a:entry", ns)
        if entry is None:
            return None
        title = _str(entry.findtext("a:title", "", ns))
        summary = _str(entry.findtext("a:summary", "", ns))
        published = _str(entry.findtext("a:published", "", ns))
        doi = _str(entry.findtext("arxiv:doi", "", ns))
        authors = [_str(a.findtext("a:name", "", ns))
                   for a in entry.findall("a:author", ns)]
        return {"title": title, "abstract": _clean(summary), "date": published[:10],
                "doi": doi, "authors": [a for a in authors if a],
                "publisher": "arXiv (preprint)"}

    # -- github ------------------------------------------------------------
    def _from_github(self, doc: FetchedDoc, owner: str, repo: str, body: bytes) -> FetchedDoc:
        doc.method = "github"
        owner, repo = owner, repo.rstrip(".git")
        parts = [doc.title, owner, repo]
        try:
            resp = self.session.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                timeout=12,
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                doc.title = data.get("full_name") or f"{owner}/{repo}"
                doc.authors = [data.get("owner", {}).get("login")] if data.get("owner") else []
                doc.date = (data.get("pushed_at") or "")[:10]
                doc.publisher = "GitHub"
                stars = data.get("stargazers_count")
                topics = data.get("topics") or []
                parts = [
                    f"Repository: {doc.title}",
                    f"Description: {data.get('description') or '(none)'}",
                    f"Stars: {stars if stars is not None else 'unknown'}",
                    f"Forks: {data.get('forks_count', 'unknown')}",
                    f"License: {(data.get('license') or {}).get('spdx_id') or 'unspecified'}",
                    f"Language: {data.get('language') or 'unspecified'}",
                    f"Topics: {', '.join(topics) if topics else '(none)'}",
                    f"Open issues: {data.get('open_issues_count', 'unknown')}",
                    f"Last push: {doc.date or 'unknown'}",
                    "",
                ]
        except Exception:
            pass                          # metadata is a bonus; README still matters

        readme = self._github_readme(owner, repo)
        if readme:
            parts.append(readme)
            doc.links = _github_links(readme)
        doc.text = _clean("\n".join(parts))
        doc.status = FULL if readme else METADATA
        if not readme:
            doc.limitation = "README not retrievable; repository metadata only"
        return doc

    def _github_readme(self, owner: str, repo: str) -> str:
        headers = {"Accept": "application/vnd.github.raw"}
        token = _github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for ref in ("HEAD", "main", "master"):
            try:
                resp = self.session.get(
                    f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/README.md",
                    timeout=15, headers=headers,
                )
                if resp.status_code == 200 and len(resp.text) > 200:
                    return resp.text
            except requests.RequestException:
                continue
        return ""

    # -- plain -------------------------------------------------------------
    def _from_plain(self, doc: FetchedDoc, body: bytes) -> FetchedDoc:
        doc.method = "plain"
        text = body.decode("utf-8", errors="replace")
        if not doc.title:
            first = next((ln.strip("# ").strip() for ln in text.splitlines()
                          if ln.strip()), "")
            doc.title = first[:200]
        doc.text = _clean_markup(text)
        doc.publisher = doc.publisher or urlparse(doc.final_url or doc.url).netloc
        doc.status = FULL
        return doc

    # -- html --------------------------------------------------------------
    def _from_html(self, doc: FetchedDoc, body: bytes, truncated: bool) -> FetchedDoc:
        doc.method = "html"
        html = body.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")

        meta = _html_metadata(soup, doc.final_url or doc.url)
        doc.title = doc.title or meta["title"]
        doc.publisher = doc.publisher or meta["publisher"]
        doc.date = doc.date or meta["date"]
        doc.authors = doc.authors or meta["authors"]
        if not doc.doi:
            doi_match = _DOI.search(html)
            doc.doi = doi_match.group(1) if doi_match else ""

        for tag in soup(_NOISE_TAGS):
            tag.decompose()

        root = _content_root(soup)
        if root is None:
            doc.status, doc.limitation = METADATA, "no article container found in HTML"
            doc.text = _clean(soup.get_text(" "))
            return doc

        doc.links = _outbound_links(root, doc.final_url or doc.url)
        text = _clean(_structured_text(root))
        doc.text = text

        lowered = text[:4000].lower()
        if any(marker in lowered for marker in _PAYWALL_MARKERS):
            doc.status = PARTIAL
            doc.limitation = _join(doc.limitation, "paywall or login wall detected; "
                                                    "text may be truncated by the publisher")
        elif len(text) < 600 and len(html) > 20_000:
            doc.status = PARTIAL
            doc.limitation = _join(doc.limitation,
                                   "page returned little text for a large HTML body; "
                                   "likely JavaScript-rendered")
        elif truncated:
            doc.status = PARTIAL
            doc.limitation = _join(doc.limitation, "download hit the byte cap")
        else:
            doc.status = FULL
        return doc

    # -- batch -------------------------------------------------------------
    def fetch_many(self, urls: list[str]) -> list[FetchedDoc]:
        """Fetch concurrently, respecting the shared budget and per-URL isolation."""
        unique = list(dict.fromkeys(u for u in urls if u))[: config.fetch_max_sources()]
        if not unique:
            return []
        workers = min(config.fetch_max_workers(), len(unique))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            docs = list(pool.map(self._safe_fetch, unique))
        return [d for d in docs if d.status != FAILED]

    def _safe_fetch(self, url: str) -> FetchedDoc:
        if self.exhausted():
            return FetchedDoc(url=url, status=FAILED, limitation="fetch budget exhausted")
        return self.fetch(url)


def snippet_only_doc(result: dict, reason: str = "") -> FetchedDoc:
    """A source we could not read, represented honestly by its search metadata.

    Used instead of dropping the source entirely. It is marked ``metadata_only``
    so the report can say plainly that only the snippet was available and no
    claim should lean on it heavily.
    """
    return FetchedDoc(
        url=result.get("url", ""),
        final_url=result.get("url", ""),
        status=METADATA,
        method="search-metadata",
        title=result.get("title", "") or result.get("url", ""),
        text=result.get("snippet", "") or "",
        publisher=urlparse(result.get("url", "")).netloc.removeprefix("www."),
        date=result.get("date", "") or "",
        limitation=reason or "the document could not be retrieved; only the "
                            "search-result snippet is available",
    )


# --- helpers ---------------------------------------------------------------

def _structured_text(root) -> str:
    """Render a content subtree as text that still carries its heading structure.

    The chunker recovers sections from markdown-style heading markers, so
    headings must survive extraction rather than dissolving into flat prose.
    Tables become pipe rows, lists keep their items, and code keeps its
    indentation so fenced blocks stay legible.
    """
    out: list[str] = []

    def walk(node) -> None:
        name = getattr(node, "name", None)
        if name is None:
            text = str(node).strip()
            if text:
                out.append(text)
            return
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            heading = node.get_text(" ").strip()
            if heading:
                out.append(f"\n{'#' * int(name[1])} {heading}\n")
            return
        if name == "table":
            for row in node.find_all("tr"):
                cells = [c.get_text(" ").strip() for c in row.find_all(["td", "th"])]
                cells = [c for c in cells if c]
                if cells:
                    out.append("| " + " | ".join(cells) + " |")
            out.append("")
            return
        if name == "pre":
            out.append("```\n" + node.get_text() + "\n```")
            return
        if name in ("p", "li", "blockquote", "figcaption", "dd", "dt", "code", "summary"):
            text = node.get_text(" ").strip()
            if not text:
                return
            out.append(f"- {text}" if name == "li" else text)
            return
        for child in getattr(node, "children", []):
            walk(child)

    walk(root)
    return "\n\n".join(out)


def _clean(text: str) -> str:
    """Normalise HTML-derived text: collapse whitespace, drop indentation noise.

    Form feeds are page markers, so they are preserved rather than stripped.
    """
    return "\f".join(_clean_part(part) for part in text.translate(_INVISIBLE).split("\f"))


def _clean_part(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(ln.strip() for ln in text.split("\n")).strip()


def _clean_markup(text: str) -> str:
    """Same, but keeps indentation so fenced code blocks survive."""
    text = text.translate(_INVISIBLE)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _join(existing: str, addition: str) -> str:
    return f"{existing}; {addition}" if existing else addition


def _str(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value else ""


def _authors(raw) -> list[str]:
    if not raw:
        return []
    text = _str(raw)
    return [a.strip() for a in re.split(r"[,;]| and ", text) if a.strip()][:12]


def _github_token() -> str:
    import os
    return (os.environ.get("GITHUB_TOKEN") or "").strip()


def _content_root(soup: BeautifulSoup):
    """Pick the densest plausible article container without an LLM in the loop."""
    for selector in ("article", "main", "[role=main]", "#main-content",
                     ".post-content", ".entry-content", ".article-body",
                     "#content", ".content"):
        found = soup.select_one(selector)
        if found and len(found.get_text(" ").split()) > 120:
            return found
    best, best_words = None, 0
    for tag in soup.find_all(["div", "section", "body"]):
        words = len(tag.get_text(" ").split())
        # Prefer the deepest node that still holds most of the text: less chrome.
        if words > best_words:
            best, best_words = tag, words
    return best or soup.body or soup


def _html_metadata(soup: BeautifulSoup, base_url: str) -> dict:
    """Title, publisher, date and authors from meta tags and JSON-LD. Deterministic."""
    out = {"title": "", "publisher": "", "date": "", "authors": []}

    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or \
                  soup.find("meta", attrs={"name": name})
            if tag and tag.get("content", "").strip():
                return tag["content"].strip()
        return ""

    out["title"] = meta("og:title", "twitter:title") or _str(soup.title.text if soup.title else "")
    if not out["title"]:
        h1 = soup.find("h1")
        out["title"] = _str(h1.get_text()) if h1 else ""
    out["publisher"] = meta("og:site_name", "application-name")
    out["date"] = meta("article:published_time", "og:published_time",
                       "datePublished", "date", "dc.date", "citation_publication_date")
    out["date"] = out["date"][:10]

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_jsonld(data):
            if isinstance(node, dict):
                out["authors"] = out["authors"] or _jsonld_authors(node.get("author"))
                out["date"] = out["date"] or _str(node.get("datePublished"))[:10]
                pub = node.get("publisher")
                if isinstance(pub, dict):
                    out["publisher"] = out["publisher"] or _str(pub.get("name"))
                elif pub:
                    out["publisher"] = out["publisher"] or _str(pub)

    if not out["publisher"]:
        out["publisher"] = urlparse(base_url).netloc.replace("www.", "")
    return out


def _walk_jsonld(node):
    """Yield every dict in a JSON-LD blob (handles @graph and arrays)."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_jsonld(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)


def _jsonld_authors(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return _authors(raw)
    if isinstance(raw, list):
        names = []
        for item in raw:
            if isinstance(item, dict):
                names.append(_str(item.get("name")))
            else:
                names.append(_str(item))
        return [n for n in names if n][:12]
    if isinstance(raw, dict):
        return [_str(raw.get("name"))] if raw.get("name") else []
    return []


def _outbound_links(root, base_url: str) -> list[str]:
    """Absolute outbound links, for reference discovery. Same-host links dropped."""
    host = urlparse(base_url).netloc.replace("www.", "")
    seen, out = set(), []
    for tag in root.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.replace("www.", "") == host:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


def _github_links(readme: str) -> list[str]:
    links, seen = [], set()
    for match in re.finditer(r"https?://[^\s)\]\"'>]+", readme):
        url = match.group(0).rstrip(".,;:")
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links[:200]