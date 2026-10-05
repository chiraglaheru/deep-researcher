"""Process-wide pacing for outbound calls.

Free-tier providers do not fail politely: they return 429 for the rest of the
day, or throttle throughput until the window resets. Sending a research run's
~40 model calls as fast as possible is what exhausts a daily quota in a handful
of runs.

This module spaces calls out instead of merely capping them. Two independent
knobs, because they solve different problems:

``per_minute``
    Minimum spacing between calls. This is what protects a tokens-per-minute or
    requests-per-minute budget, since the total sent in any window is bounded by
    construction.
``max_concurrent``
    Ceiling on calls in flight at once. A single run is already sequential for
    model calls, so this matters when two browser tabs run pipelines at once --
    without it, each run's pacing looks fine in isolation while together they
    blow through a shared quota.

The throttle is a module-level singleton per kind. It is shared by every run in
the process on purpose: the constraint is the provider's, not a request's.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ThrottleStats:
    calls: int = 0
    waited_s: float = 0.0
    max_wait_s: float = 0.0

    def as_dict(self) -> dict:
        return {"calls": self.calls,
                "waited_seconds": round(self.waited_s, 1),
                "max_wait_seconds": round(self.max_wait_s, 1)}


@dataclass
class Throttle:
    """Minimum spacing plus a concurrency ceiling."""

    name: str = "llm"
    per_minute: float = 0.0            # 0 disables pacing
    max_concurrent: int = 1
    enabled: bool = True
    _next_slot: float = 0.0
    _in_flight: int = 0
    _cond: threading.Condition = field(default_factory=threading.Condition)
    _slots: threading.Semaphore = field(init=False)
    stats: ThrottleStats = field(default_factory=ThrottleStats)

    def __post_init__(self) -> None:
        self._slots = threading.Semaphore(max(1, self.max_concurrent))

    @property
    def active(self) -> bool:
        """True when this throttle will actually hold a call back."""
        return bool(self.enabled) and (self.per_minute > 0 or self.max_concurrent < 999)

    @property
    def interval(self) -> float:
        return 60.0 / self.per_minute if self.per_minute > 0 else 0.0

    def configure(self, per_minute: float | None = None,
                  max_concurrent: int | None = None, enabled: bool | None = None) -> None:
        with self._cond:
            if per_minute is not None:
                self.per_minute = max(0.0, per_minute)
            if max_concurrent is not None:
                self.max_concurrent = max(1, max_concurrent)
                self._slots = threading.Semaphore(self.max_concurrent)
            if enabled is not None:
                self.enabled = bool(enabled)
            self._cond.notify_all()

    @contextmanager
    def slot(self):
        """Hold a call slot, waiting first if the rate requires it.

        Always yields, even when disabled, so a pacing problem can never turn
        into a failed request.
        """
        if not self.active:
            self.stats.calls += 1
            yield
            return

        acquired = False
        try:
            self._slots.acquire()
            acquired = True

            with self._cond:
                now = time.monotonic()
                earliest = max(now, self._next_slot)
                self._next_slot = earliest + self.interval
                self._in_flight += 1

            wait = earliest - time.monotonic()
            if wait > 0:
                self.stats.waited_s += wait
                self.stats.max_wait_s = max(self.stats.max_wait_s, wait)
                log.debug("throttle[%s]: waiting %.1fs before this call",
                          self.name, wait)
                time.sleep(wait)
            self.stats.calls += 1
            yield
        finally:
            if acquired:
                with self._cond:
                    self._in_flight = max(0, self._in_flight - 1)
                self._slots.release()

    def reset(self) -> None:
        with self._cond:
            self._next_slot = 0.0
            self._in_flight = 0
            self.stats = ThrottleStats()
            self._cond.notify_all()


# One throttle per kind, shared by every run in this process.
_registry: dict[str, Throttle] = {}
_registry_lock = threading.Lock()


def get(name: str) -> Throttle:
    from . import config

    with _registry_lock:
        existing = _registry.get(name)
        if existing is not None:
            return existing

        if name == "llm":
            throttle = Throttle(
                name="llm",
                per_minute=config.throttle_llm_per_minute(),
                max_concurrent=config.throttle_llm_max_concurrent(),
                enabled=config.throttle_llm(),
            )
        elif name == "search":
            throttle = Throttle(
                name="search",
                per_minute=config.throttle_search_per_minute(),
                max_concurrent=config.throttle_search_max_concurrent(),
                enabled=config.throttle_search(),
            )
        else:
            throttle = Throttle(name=name)

        _registry[name] = throttle
        return throttle


def reset_all() -> None:
    """Drop every throttle. Intended for tests."""
    with _registry_lock:
        _registry.clear()


def apply_config(enabled: bool | None = None) -> None:
    """Re-apply environment configuration to the live throttles."""
    from . import config

    get("llm").configure(
        per_minute=config.throttle_llm_per_minute(),
        max_concurrent=config.throttle_llm_max_concurrent(),
        enabled=config.throttle_llm() if enabled is None else enabled,
    )
    get("search").configure(
        per_minute=config.throttle_search_per_minute(),
        max_concurrent=config.throttle_search_max_concurrent(),
        enabled=config.throttle_search() if enabled is None else enabled,
    )


def snapshot() -> dict:
    with _registry_lock:
        return {name: {"enabled": t.active,
                       "per_minute": t.per_minute,
                       "max_concurrent": t.max_concurrent,
                       **t.stats.as_dict()}
                for name, t in _registry.items()}
