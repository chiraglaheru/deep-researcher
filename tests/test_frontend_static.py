"""Static assertions for the frontend stream guards (audit fixes 5-6).

The SSE consumer must never let one malformed frame or one malformed source
row abort a healthy run. Structural assertions on app.js, mirroring the
style of the other frontend tests.
"""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.js"
SOURCE = APP.read_text()


def test_json_parse_is_wrapped_per_frame():
    """A bare JSON.parse in the stream loop aborted the whole run on one
    non-JSON keepalive frame."""
    idx = SOURCE.index("JSON.parse(line.slice(6))")
    fenced = SOURCE[idx - 400: idx + 120]
    assert "try {" in fenced and "catch" in fenced, \
        "the frame parser must catch and skip malformed frames"
    assert "const data = JSON.parse(line.slice(6));" not in SOURCE, \
        "the bare assignment must not come back"


def test_source_status_is_guarded_everywhere():
    """A missing status field must render as 'unknown', not crash."""
    assert "(source.status || \"unknown\").replace" in SOURCE
    assert "status-${source.status || \"unknown\"}" in SOURCE
    assert "source.status.replace" not in SOURCE


def test_article_links_are_scheme_checked():
    """Scraped URLs must not reach href unchecked (javascript: risk)."""
    assert "function safeExternalUrl" in SOURCE
    assert "title.href = source.url || \"#\"" not in SOURCE
    line = next(l for l in SOURCE.splitlines() if "title.href = " in l)
    assert "safeExternalUrl" in line, f"unguarded href assignment: {line}"


def test_stat_tile_call_signature_matches_definition():
    """statTile(value, label, hint): the big text is the value, not the label."""
    assert "function statTile(value, label, hint)" in SOURCE
