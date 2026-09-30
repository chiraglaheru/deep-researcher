import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Keep ambient credentials from leaking into tests."""
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
