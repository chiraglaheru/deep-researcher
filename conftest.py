import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


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