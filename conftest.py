"""Test bootstrap: make the repository root importable.

Tests import ``backend.research.*``, which requires the *repository root* on
``sys.path`` -- not ``backend/``. Relying on pytest's implicit rootdir insertion
works from the repository root but breaks when pytest is invoked from
elsewhere, from an installed console script, or under a different import mode.
Both paths are added explicitly so the suite is location-independent.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = REPO_ROOT / "backend"

for path in (REPO_ROOT, BACKEND_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: hits a real paid API; keep credentials visible to the test",
    )


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, request):
    """Keep ambient credentials from leaking into tests.

    Tests marked ``live`` need the real key, so SERPAPI_KEY is left alone.
    """
    if request.node.get_closest_marker("live") is None:
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    # Mock mode is opt-in; an ambient LLM_MOCK/SEARCH_MOCK would silently turn
    # real requests into canned data and make a run look like it passed.
    if request.node.get_closest_marker("live") is None:
        for name in ("LLM_MOCK", "SEARCH_MOCK",
                     "LLM_MOCK_FAIL", "SEARCH_MOCK_FAIL"):
            monkeypatch.delenv(name, raising=False)
