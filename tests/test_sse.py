"""SSE contract for GET /api/research."""

import json

import pytest
from fastapi.testclient import TestClient

from backend import main


def parse_frames(text):
    """Turn raw SSE text back into the decoded event dicts."""
    events = []
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        line = next(
            (ln for ln in frame.split("\n") if ln.startswith("data: ")),
            None,
        )
        assert line is not None, f"frame is not a data frame: {frame!r}"
        events.append(json.loads(line[6:]))
    return events


@pytest.fixture
def client():
    return TestClient(main.app)


def install_fake(monkeypatch, events, calls=None, raises=None):

    async def fake_deep_research(question, max_rounds=3):
        if calls is not None:
            calls.append({"question": question, "max_rounds": max_rounds})
        for event in events:
            yield event
        if raises is not None:
            raise raises

    monkeypatch.setattr(main, "deep_research", fake_deep_research)


def test_content_type_is_event_stream(client, monkeypatch):
    install_fake(monkeypatch, [{"type": "report", "data": "x", "sources": []}])

    response = client.get("/api/research?q=test")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_every_frame_is_data_json_blank_line(client, monkeypatch):
    install_fake(
        monkeypatch,
        [
            {"type": "plan", "data": {"subquestions": []}},
            {"type": "report", "data": "done", "sources": []},
        ],
    )

    raw = client.get("/api/research?q=test").text

    assert raw.startswith("data: ")
    for frame in raw.split("\n\n"):
        if frame.strip():
            assert frame.startswith("data: ")
            json.loads(frame[6:])


def test_done_is_always_last(client, monkeypatch):
    install_fake(
        monkeypatch,
        [
            {"type": "plan", "data": {"subquestions": []}},
            {"type": "evidence", "count": 3},
            {"type": "report", "data": "final", "sources": []},
        ],
    )

    events = parse_frames(client.get("/api/research?q=test").text)

    assert [e["type"] for e in events] == ["plan", "evidence", "report", "done"]
    assert events[-1]["type"] == "done"


def test_generator_error_produces_error_event_then_done(client, monkeypatch):
    install_fake(
        monkeypatch,
        [{"type": "plan", "data": {"subquestions": []}}],
        raises=RuntimeError("all models failed"),
    )

    response = client.get("/api/research?q=test")

    assert response.status_code == 200
    events = parse_frames(response.text)

    assert [e["type"] for e in events] == ["plan", "error", "done"]
    assert events[-1]["type"] == "done"
    assert "all models failed" in events[-2]["message"]


def test_done_is_last_even_when_the_first_event_errors(client, monkeypatch):
    install_fake(monkeypatch, [], raises=RuntimeError("boom"))

    events = parse_frames(client.get("/api/research?q=test").text)

    assert [e["type"] for e in events] == ["error", "done"]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, 1), (1, 1), (2, 2), (3, 3), (4, 4), (99, 4), (-5, 1)],
)
def test_rounds_are_clamped(client, monkeypatch, requested, expected):
    calls = []
    install_fake(monkeypatch, [{"type": "report", "data": "x", "sources": []}], calls=calls)

    client.get(f"/api/research?q=test&rounds={requested}")

    assert calls[0]["max_rounds"] == expected


def test_rounds_defaults_to_two_when_omitted(client, monkeypatch):
    calls = []
    install_fake(monkeypatch, [{"type": "report", "data": "x", "sources": []}], calls=calls)

    client.get("/api/research?q=test")

    assert calls[0]["max_rounds"] == 2


def test_question_reaches_the_pipeline_intact(client, monkeypatch):
    calls = []
    install_fake(monkeypatch, [{"type": "report", "data": "x", "sources": []}], calls=calls)

    client.get("/api/research?q=C%2B%2B+vs+Rust")

    assert calls[0]["question"] == "C++ vs Rust"


def test_missing_q_returns_422(client, monkeypatch):
    install_fake(monkeypatch, [{"type": "report", "data": "x", "sources": []}])

    assert client.get("/api/research").status_code == 422

# --- CORS --------------------------------------------------------------------
#
# The frontend uses a relative fetch() URL, so it is same-origin and CORS does
# not normally apply. CORS only bites when the page is served from a different
# origin than the API -- a dev server on another port, or a teammate on the LAN.
# The allowlist used to be four hardcoded localhost entries, so anything else
# was silently refused.

EP = "/api/report/markdown?q=x"


def _preflight(client, origin):
    return client.options(EP, headers={"Origin": origin,
                                       "Access-Control-Request-Method": "GET"})


@pytest.mark.parametrize("origin", [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5500",      # documented dev-server port
    "http://127.0.0.1:5500",
    "http://localhost:5173",      # Vite default
    "http://127.0.0.1:3000",      # common React dev port
    "http://localhost:8080",
])
def test_loopback_origins_on_any_port_are_allowed(origin):
    from fastapi.testclient import TestClient
    from backend import main

    response = _preflight(TestClient(main.app), origin)

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize("origin", [
    "http://evil.example.com",
    "https://attacker.test",
])
def test_remote_origins_are_still_refused(origin):
    """Loopback flexibility must not turn into a wildcard."""
    from fastapi.testclient import TestClient
    from backend import main

    response = _preflight(TestClient(main.app), origin)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_extra_origins_are_configurable(monkeypatch):
    """A LAN address cannot be guessed, so it has to be settable."""
    from fastapi.testclient import TestClient
    import importlib

    monkeypatch.setenv("CORS_ORIGINS", "http://192.168.1.50:8000,https://x.test")

    import backend.main as main_module
    importlib.reload(main_module)
    try:
        client = TestClient(main_module.app)
        response = _preflight(client, "http://192.168.1.50:8000")
        assert response.headers.get("access-control-allow-origin") == \
            "http://192.168.1.50:8000"
    finally:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        importlib.reload(main_module)


def test_static_paths_are_absolute_not_cwd_relative():
    """Regression: a relative mount breaks whenever cwd is not the repo root.

    ``StaticFiles(directory="frontend")`` resolves against the process working
    directory, so importing the app from any other directory raised
    ``RuntimeError: Directory 'frontend' does not exist`` at import time.
    """
    from backend.main import _frontend_dir

    frontend = _frontend_dir()

    assert frontend.is_absolute(), "frontend path must be absolute"
    assert frontend.is_dir(), f"frontend directory missing at {frontend}"
    assert (frontend / "index.html").is_file()
    assert (frontend / "app.js").is_file()


def test_app_imports_from_any_working_directory(tmp_path, monkeypatch):
    import importlib

    monkeypatch.chdir(tmp_path)          # simulate a different launch directory
    import backend.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient

    assert TestClient(main_module.app).get("/").status_code == 200
