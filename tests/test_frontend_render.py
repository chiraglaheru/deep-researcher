"""The browser-side markdown renderer.

The report is model output rendered with innerHTML, so these tests pin two things
that matter: citations resolve to real anchors, and reference numbering is
sequential. Both were wrong before -- each reference was rendered in its own
<ol>, so every entry was renumbered "1." and no anchor existed to jump to.

Skipped when dukpy (the JS engine used to execute the renderer) is unavailable.
"""
import re
from pathlib import Path

import pytest

dukpy = pytest.importorskip("dukpy", reason="dukpy not installed")

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "markdown.js"
SOURCE = FRONTEND.read_text()


@pytest.fixture(scope="module")
def render():
    # The browser globals at the bottom of the file are not available here.
    js = re.sub(r"^window\..*$", "", SOURCE, flags=re.M)

    def _render(markdown):
        import json
        return dukpy.evaljs(f"{js}\nrenderMarkdown({json.dumps(markdown)});")

    return _render


REFERENCES = """## References

1. **Alpha** — publisher; 2026-01-01; web page.  
   <https://a.dev/1>
   Cited from: Introduction

2. **Beta** — publisher; 2025-06-01; web page.  
   <https://b.dev/2>
   Cited from: Methods

3. **Gamma** — publisher; 2024-02-02; web page.  
   <https://c.dev/3>
   Cited from: Results
"""


def test_references_render_in_a_single_ordered_list(render):
    """One <ol> means the browser numbers them 1, 2, 3.

    A separate <ol> per entry restarts numbering, which is what produced the
    "1. 1. 1." output.
    """
    out = render(REFERENCES)

    assert out.count("<ol") == 1, f"expected one list, got:\n{out}"
    assert out.count("</ol>") == 1
    assert out.count("<li") == 3


def test_reference_heading_precedes_the_list(render):
    out = render(REFERENCES)

    assert out.index("References") < out.index("<ol"), \
        "the list must not be opened before its heading"
    assert "<ol></ol>" not in out and "<ol>\n" not in out.split("<ol")[1][:2]


def test_every_reference_gets_a_matching_anchor(render):
    out = render(REFERENCES)

    anchors = re.findall(r'id="ref-(\d+)"', out)
    assert anchors == ["1", "2", "3"]


def test_reference_urls_stay_canonical_and_clickable(render):
    out = render(REFERENCES)

    for url in ("https://a.dev/1", "https://b.dev/2", "https://c.dev/3"):
        assert f'href="{url}"' in out, f"{url} must be a real link"
        assert url in out, "the visible text must be the URL itself"
    assert "&lt;https://" not in out, "autolinks must survive escaping"


def test_citations_in_the_body_resolve_to_reference_anchors(render):
    body = "A claim [3] and another [1][2].\n\n" + REFERENCES
    out = render(body)

    links = set(re.findall(r'href="#(ref-\d+)"', out))
    targets = set(re.findall(r'id="(ref-\d+)"', out))

    assert links == {"ref-1", "ref-2", "ref-3"}
    assert links <= targets, f"dangling citations: {links - targets}"


def test_headings_tables_and_lists_still_render(render):
    out = render("## Performance\n\nBody.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n")

    assert "<h3" in out
    assert "<table>" in out
    assert "<td>1</td>" in out


def test_model_output_cannot_inject_markup(render):
    hostile = ("Text with <script>alert(1)</script> and "
               "<img src=x onerror=alert(1)> and "
               "<a href='javascript:alert(1)'>x</a>")
    out = render(hostile)

    # The tags must be escaped into inert text, not merely absent: the literal
    # string "onerror=" is fine inside &lt;img ...&gt;, unsafe as a real tag.
    assert "<script" not in out
    assert "<img" not in out
    assert "&lt;script&gt;" in out and "&lt;img" in out
    for href in re.findall(r'href="([^"]*)"', out):
        assert href.startswith(("http", "mailto", "#")), f"unsafe href {href}"


def test_bracket_text_is_not_mistaken_for_a_citation(render):
    """a[0] is array indexing; linking it would make a dangling #ref-0 anchor."""
    out = render("An array a[0] and a footnote [not-a-number] here.")

    assert 'class="citation"' not in out
    assert "#ref-0" not in out
