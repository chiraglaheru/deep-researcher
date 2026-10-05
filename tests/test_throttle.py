"""Request pacing.

Free-tier providers do not fail politely: one 429 can cost the rest of the day.
Spacing calls out bounds the total sent in any window by construction, which is
the only way to stay under a tokens-per-minute quota without discovering it the
expensive way.
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.research.throttle import Throttle, get, reset_all, snapshot


@pytest.fixture
def client():
    """The API tests need the app, but must not trigger a real research run."""
    from backend import main

    def fake_deep_research(question, max_rounds=3):
        async def gen():
            yield {"type": "status", "data": {"status": "completed",
                                              "missing_sections": []}}
            yield {"type": "report", "data": "# report", "sources": []}

        return gen()

    original = main.deep_research
    main.deep_research = fake_deep_research
    try:
        yield TestClient(main.app)
    finally:
        main.deep_research = original


def test_calls_are_spaced_by_the_configured_interval():
    throttle = Throttle(per_minute=600, max_concurrent=1)   # 100ms apart
    started = time.monotonic()

    for _ in range(5):
        with throttle.slot():
            pass

    elapsed = time.monotonic() - started
    assert elapsed >= 0.35, f"5 calls at 600/min should take ~0.4s, took {elapsed:.2f}s"
    assert throttle.stats.calls == 5
    assert throttle.stats.waited_s > 0


def test_disabled_throttle_does_not_wait():
    throttle = Throttle(per_minute=1, enabled=False)
    started = time.monotonic()

    for _ in range(5):
        with throttle.slot():
            pass

    assert time.monotonic() - started < 0.05, "a disabled throttle must not block"
    assert throttle.active is False


def test_zero_per_minute_means_no_pacing():
    throttle = Throttle(per_minute=0, max_concurrent=1)
    assert throttle.interval == 0
    started = time.monotonic()
    for _ in range(5):
        with throttle.slot():
            pass
    assert time.monotonic() - started < 0.05


def test_concurrency_ceiling_is_enforced():
    """Two runs at once must not jointly exceed a shared provider budget."""
    throttle = Throttle(per_minute=0, max_concurrent=1)
    peak = 0
    current = 0
    guard = threading.Lock()

    def worker():
        nonlocal peak, current
        with throttle.slot():
            with guard:
                current += 1
                peak = max(peak, current)
            time.sleep(0.05)
            with guard:
                current -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1, f"max_concurrent=1 was exceeded: peak {peak}"


def test_parallel_callers_are_paced_as_one_stream():
    """Per-call pacing in isolation would not bound a shared quota."""
    throttle = Throttle(per_minute=600, max_concurrent=1)
    order = []

    def worker(index):
        with throttle.slot():
            order.append(index)
            time.sleep(0.01)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(order) == 6
    assert throttle.stats.calls == 6
    assert throttle.stats.waited_s > 0, "concurrent callers must share one pace"


def test_slot_is_released_when_the_call_raises():
    """A failing call must not permanently consume a concurrency slot."""
    throttle = Throttle(per_minute=0, max_concurrent=1)

    with pytest.raises(RuntimeError):
        with throttle.slot():
            raise RuntimeError("provider exploded")

    # Would hang if the semaphore leaked.
    with throttle.slot():
        pass


def test_configure_changes_behaviour_live():
    throttle = Throttle(per_minute=0, max_concurrent=1, enabled=False)
    assert throttle.active is False

    throttle.configure(per_minute=6000, max_concurrent=2, enabled=True)
    assert throttle.active is True
    assert throttle.per_minute == 6000
    assert throttle.max_concurrent == 2

    throttle.configure(enabled=False)
    assert throttle.active is False


# --- wiring -----------------------------------------------------------------

def test_llm_and_search_get_separate_throttles():
    reset_all()
    assert get("llm") is get("llm"), "the same instance must be reused"
    assert get("llm") is not get("search"), "llm and search are paced separately"


def test_ask_goes_through_the_llm_throttle(monkeypatch):
    """The pacer must wrap the actual provider call, not sit beside it."""
    import litellm

    from backend.research import llm

    reset_all()
    throttle = get("llm")
    entered = []
    real_slot = throttle.slot

    @contextmanager_check := __import__("contextlib").contextmanager
    def _tracked():
        with real_slot():
            entered.append(1)
            yield

    monkeypatch.setattr(throttle, "slot", _tracked)

    def fake_completion(**kwargs):
        return type("R", (), {"choices": [type("C", (), {
            "message": type("M", (), {"content": "ok"})()})()]})()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setenv("LLM_MODEL", "groq/test")
    monkeypatch.setenv("LLM_FALLBACKS", "")

    llm.ask("sys", "user")
    assert entered, "ask() must hold a throttle slot around the provider call"


def test_search_goes_through_the_search_throttle(monkeypatch):
    from backend.research import searcher

    reset_all()
    throttle = get("search")
    entered = []
    real_slot = throttle.slot

    @__import__("contextlib").contextmanager
    def _tracked():
        with real_slot():
            entered.append(1)
            yield

    monkeypatch.setattr(throttle, "slot", _tracked)
    monkeypatch.setattr(searcher, "_search_uncapped",
                        lambda source, query, n: [])

    searcher.search("web", "q")
    assert entered, "search() must hold a throttle slot around the request"


def test_snapshot_reports_live_state():
    reset_all()
    throttle = get("llm")
    with throttle.slot():
        pass

    data = snapshot()
    assert "llm" in data
    assert data["llm"]["calls"] >= 1
    assert "per_minute" in data["llm"]


# --- API --------------------------------------------------------------------

def test_throttling_is_on_by_default_for_free_tiers(monkeypatch):
    """Guards the default the user relies on while providers are free-tier."""
    from backend.research import config as config_module

    monkeypatch.delenv("THROTTLE_LLM", raising=False)
    assert config_module.throttle_llm() is True
    assert config_module.throttle_llm_per_minute() > 0


def test_throttle_parameter_overrides_for_a_run(client, monkeypatch):
    """The frontend choice must reach the pipeline, and not leak to later runs."""
    from backend.research import throttle as throttle_module

    monkeypatch.setenv("THROTTLE_LLM", "1")
    throttle_module.reset_all()
    assert get("llm").enabled is True

    body = client.get("/api/research?q=test&rounds=1&throttle=0")
    assert '"type":"done"' in body.text.replace(" ", "")

    # Restored afterwards, so one unthrottled request does not disable pacing
    # for every later run.
    assert get("llm").enabled is True, "the configured default must be restored"


def test_report_event_carries_throttle_state(client, monkeypatch):
    import json

    monkeypatch.setenv("THROTTLE_LLM", "1")
    from backend.research import throttle as throttle_module

    throttle_module.reset_all()
    body = client.get("/api/research?q=test&rounds=1&throttle=1").text

    frames = [json.loads(line[6:]) for line in body.splitlines()
              if line.startswith("data: ")]
    assert any("throttle" in f for f in frames), \
        "the UI needs throttle state to render the pacing panel"


def test_throttle_status_endpoint(client, monkeypatch):
    monkeypatch.setenv("THROTTLE_LLM", "1")
    monkeypatch.setenv("THROTTLE_LLM_PER_MINUTE", "30")
    body = client.get("/api/throttle").json()

    assert "configured" in body
    assert body["configured"]["enabled"] is True
    assert body["configured"]["per_minute"] == 30
    assert "live" in body


def test_public_config_advertises_throttle(client, monkeypatch):
    monkeypatch.setenv("THROTTLE_LLM", "1")
    body = client.get("/api/config").json()

    assert "throttle" in body
    assert body["throttle"]["enabled"] is True
    assert "search_enabled" in body["throttle"]
