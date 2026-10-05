"""Server-side markdown -> HTML compilation.

The browser used to parse the full report in one synchronous JS pass, which
froze the UI on ~15k-word reports with 350+ citations, and forced every future
client to reimplement the custom [n] citation scheme. The server now compiles
once with this dependency-free engine and streams pre-rendered HTML fragments
over SSE; the frontend only injects them.

Semantics mirror frontend/markdown.js so existing reports render identically:
escaped-first, citation links to #ref-n with data-ref for hover cards, single
<ol class='reference-list'>, tables, code, blockquotes, lists.
"""
from __future__ import annotations

import html
import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_REF_ENTRY = re.compile(r"^\s*(\d{1,3})\.\s+(.*)$")
_TABLE_DIV = re.compile(r"^[|\s:-]+$")
_HR = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
_CODE_FENCE = re.compile(r"^\s*```")
_BLOCKQUOTE = re.compile(r"^\s*>\s?")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_CITE_PAIR = re.compile(r"(?<![\w\]])\[(\d{1,3})\]\s*\[(\d{1,3})\]")
_CITE_SINGLE = re.compile(r"(?<![\w\]])\[(\d{1,3})\](?!\()")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BARE_URL = re.compile(r"(^|[\s(>])(https?://[^\s<)]+)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(^|[^*])\*([^*\n]+)\*(?!\*)")
_STRIKE = re.compile(r"~~([^~]+)~~")
_INLINE_CODE = re.compile(r"`([^`]+)`")


def escape(text: str | None) -> str:
    return html.escape(str(text or ""), quote=True)


def safe_url(url: str) -> str:
    value = str(url or "").strip()
    if re.match(r"^(https?:|mailto:|#)", value, re.I):
        return value
    return ""


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug[:60]


def _citation(number: str) -> str:
    return (
        f'<a class="citation" href="#ref-{number}" data-ref="{number}" '
        f'title="Go to reference {number}">[{number}]</a>'
    )


def render_inline(text: str) -> str:
    links: list[str] = []

    def _pull_autolink(m: re.Match) -> str:
        links.append(m.group(1))
        return f"@@LINK{len(links) - 1}@@"

    raw = _AUTOLINK.sub(_pull_autolink, str(text or ""))
    out = escape(raw)

    codes: list[str] = []

    def _pull_code(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"@@CODE{len(codes) - 1}@@"

    out = _INLINE_CODE.sub(_pull_code, out)
    cites: list[str] = []

    def _pull_pair(m: re.Match) -> str:
        cites.append(m.group(1))
        first = f"@@CITE{len(cites) - 1}@@"
        cites.append(m.group(2))
        second = f"@@CITE{len(cites) - 1}@@"
        return first + second

    def _pull_single(m: re.Match) -> str:
        cites.append(m.group(1))
        return f"@@CITE{len(cites) - 1}@@"

    out = _CITE_PAIR.sub(_pull_pair, out)
    out = _CITE_SINGLE.sub(_pull_single, out)

    def _md_link(m: re.Match) -> str:
        url = safe_url(m.group(2))
        if not url:
            return m.group(1)
        return (f'<a href="{escape(url)}" target="_blank" '
                f'rel="noopener noreferrer">{m.group(1)}</a>')

    out = _MD_LINK.sub(_md_link, out)

    def _bare(m: re.Match) -> str:
        lead, url = m.group(1), m.group(2)
        clean = safe_url(url)
        if not clean:
            return m.group(0)
        return (f'{lead}<a href="{escape(clean)}" target="_blank" '
                f'rel="noopener noreferrer">{escape(clean)}</a>')

    out = _BARE_URL.sub(_bare, out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"\1<em>\2</em>", out)
    out = _STRIKE.sub(r"<del>\1</del>", out)
    out = re.sub(r"@@CITE(\d+)@@", lambda m: _citation(cites[int(m.group(1))]), out)
    out = re.sub(r"@@CODE(\d+)@@",
                 lambda m: "<code>" + codes[int(m.group(1))] + "</code>", out)

    def _restore_link(m: re.Match) -> str:
        url = links[int(m.group(1))]
        return (f'<a href="{escape(url)}" target="_blank" '
                f'rel="noopener noreferrer">{escape(url)}</a>')

    return re.sub(r"@@LINK(\d+)@@", _restore_link, out)


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_divider(line: str) -> bool:
    return "-" in line and bool(_TABLE_DIV.match(line))


def render_markdown_html(markdown: str) -> str:
    """Compile report markdown to sanitized HTML. Never raises on bad input."""
    if not markdown:
        return ""
    lines = str(markdown).replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    lst: str | None = None
    in_code = False
    code_lines: list[str] = []
    table_rows: list[dict] = []
    in_refs = False
    ref_open = False
    pending: dict | None = None

    def flush_para() -> None:
        nonlocal para
        if para:
            out.append("<p>" + render_inline(" ".join(para)) + "</p>")
            para = []

    def flush_list() -> None:
        nonlocal lst
        if lst:
            out.append(f"</{lst}>")
            lst = None

    def flush_ref() -> None:
        nonlocal pending, ref_open
        if not pending:
            return
        if not ref_open:
            out.append("<ol class='reference-list'>")
            ref_open = True
        out.append(f'<li id="ref-{pending["number"]}">' +
                   " ".join(pending["parts"]) + "</li>")
        pending = None

    def leave_refs() -> None:
        nonlocal ref_open, in_refs
        flush_ref()
        if ref_open:
            out.append("</ol>")
            ref_open = False
        in_refs = False

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        rows = [r for r in table_rows if not r["divider"]]
        body = rows if (table_rows[0].get("divider")) else rows[1:]
        head = rows[0]["cells"] if rows else []
        out.append("<table><thead><tr>" +
                   "".join(f"<th>{render_inline(c)}</th>" for c in head) +
                   "</tr></thead><tbody>" +
                   "".join("<tr>" + "".join(f"<td>{render_inline(c)}</td>"
                                            for c in r["cells"]) + "</tr>"
                           for r in body) +
                   "</tbody></table>")
        table_rows = []

    def flush_all() -> None:
        flush_para()
        flush_list()
        flush_table()

    for line in lines:
        if _CODE_FENCE.match(line):
            if in_code:
                out.append("<pre><code>" + escape("\n".join(code_lines)) +
                           "</code></pre>")
                code_lines = []
                in_code = False
            else:
                flush_all()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.strip().startswith("|"):
            flush_para()
            flush_list()
            table_rows.append({"cells": _table_cells(line),
                               "divider": _is_divider(line)})
            continue
        flush_table()

        heading = _HEADING.match(line)
        if heading:
            if in_refs:
                leave_refs()
            flush_all()
            level = min(len(heading.group(1)) + 1, 6)
            text = heading.group(2).strip()
            if re.match(r"^references$", text.strip(), re.I):
                in_refs = True
                ref_open = False
                pending = None
            out.append(f'<h{level} id="{escape(_slug(text))}">' +
                       render_inline(text) + f"</h{level}>")
            continue
        if _HR.match(line):
            flush_all()
            out.append("<hr>")
            continue
        if line.lstrip().startswith(">"):
            flush_all()
            out.append("<blockquote>" +
                       render_inline(_BLOCKQUOTE.sub("", line)) +
                       "</blockquote>")
            continue
        ref_entry = _REF_ENTRY.match(line)
        if in_refs and ref_entry:
            flush_para()
            flush_list()
            flush_table()
            flush_ref()
            pending = {"number": ref_entry.group(1),
                       "parts": [render_inline(ref_entry.group(2))]}
            continue
        if in_refs and pending and line.strip() and not re.match(r"^\s*[-*+|]", line):
            pending["parts"].append(render_inline(line.strip()))
            continue
        if in_refs and pending and not line.strip():
            flush_ref()
            continue
        bullet = _BULLET.match(line)
        ordered = _ORDERED.match(line)
        if bullet or ordered:
            flush_para()
            flush_table()
            kind = "ul" if bullet else "ol"
            if lst != kind:
                flush_list()
                out.append(f"<{kind}>")
                lst = kind
            out.append("<li>" + render_inline((bullet or ordered).group(1)) + "</li>")
            continue
        if not line.strip():
            flush_all()
            continue
        flush_list()
        para.append(line.strip())

    if in_code and code_lines:
        out.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
    if in_refs:
        leave_refs()
    flush_all()
    return "\n".join(out)
