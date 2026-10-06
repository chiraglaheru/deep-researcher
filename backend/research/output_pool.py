"""Custom memory pool for final output processing.

The report is written section by section, one model call each. When the
orchestrator model fails mid-report the fallbacks take over -- but a fresh
model call starts with no memory of what was already written, so the stream
either restarts, skips the failed section (losing its context for every later
section), or drifts in tone and citations.

The pool fixes that by keeping every *post-processed* section -- the final
markdown exactly as it will be streamed -- in a per-run store:

* completed entries are immutable: once written they are never edited,
  only read back as verbatim copies;
* a fallback retrying a failed section receives a read-only snapshot of the
  pool (which sections already exist and what they establish), so it can
  continue the report instead of restarting it;
* later sections (executive summary, conclusion) are grounded in the same
  snapshot, so a mid-report failure cannot silently change what they summarise.

Nothing here touches the evidence layer: the pool stores output text, and
readers get copies, never references into the live store.
"""
from __future__ import annotations


class OutputMemoryPool:
    """Ordered, append-only store of finished report sections for one run."""

    def __init__(self, question: str = ""):
        self.question = question
        self._order: list[str] = []
        self._sections: dict[str, str] = {}
        self._failed: dict[str, str] = {}

    # -- writes ----------------------------------------------------------

    def complete(self, heading: str, markdown: str) -> None:
        """Record a finished section verbatim. Never overwrites."""
        key = (heading or "").strip()
        text = markdown or ""
        if not key or not text:
            return
        if key in self._sections:
            return                      # immutable: first write wins
        self._order.append(key)
        self._sections[key] = text
        self._failed.pop(key, None)

    def fail(self, heading: str, reason: str) -> None:
        """Remember a section that could not be written, with why."""
        key = (heading or "").strip()
        if not key or key in self._sections:
            return
        self._failed.setdefault(key, reason or "no output")

    # -- reads (copies only: callers can never mutate the pool) ----------

    def snapshot(self) -> dict[str, str]:
        """Verbatim copies of every completed section, in write order."""
        return {key: self._sections[key] for key in self._order}

    def headings(self) -> list[str]:
        return list(self._order)

    def failures(self) -> dict[str, str]:
        return dict(self._failed)

    def __len__(self) -> int:
        return len(self._order)

    def context(self, max_chars: int = 4000,
                exclude: str = "") -> str:
        """Compact read-only digest for grounding a fallback retry.

        Lists completed section headings plus a short excerpt of each, so a
        fallback model knows what already exists without re-reading full
        sections. ``exclude`` skips the section currently being written.
        """
        lines: list[str] = []
        budget = max(0, max_chars)
        for key in self._order:
            if key == (exclude or "").strip():
                continue
            excerpt = " ".join(self._sections[key].split())
            if len(excerpt) > 400:
                excerpt = excerpt[:400].rstrip() + " ..."
            entry = f"- {key}: {excerpt}"
            if budget and len("\n".join(lines)) + len(entry) > budget:
                lines.append("- ... (earlier sections omitted for length)")
                break
            lines.append(entry)
        return "\n".join(lines)
