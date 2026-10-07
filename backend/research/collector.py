"""Evidence layer: dedupe, date/freshness, contradiction detection."""
import datetime
import logging
import re
from urllib.parse import urlparse
from .llm import ask

log = logging.getLogger(__name__)

THIS_YEAR = datetime.date.today().year


def _key(url):
    p = urlparse(url or "")
    return (p.netloc.replace("www.", "") + p.path.rstrip("/")).lower()


def year_of(date):
    if not date:
        return None
    if "ago" in str(date).lower():
        return THIS_YEAR
    m = re.search(r"(19|20)\d{2}", str(date))
    return int(m.group()) if m else None


CONTRA_SYS = """You are checking evidence for contradictions. Given numbered sources, find claims where
sources genuinely disagree (not just different emphasis). Return JSON only:
{"contradictions":[{"topic":"...","side_a":"...","sources_a":[1],"side_b":"...","sources_b":[2]}]}
Return an empty list if none."""


class Collector:
    def __init__(self):
        self.items = {}

    def add(self, results):
        new = []
        for r in results:
            k = _key(r.get("url"))
            if not k or k in self.items:
                continue
            r["id"] = len(self.items) + 1
            r["year"] = year_of(r.get("date"))
            r["stale"] = bool(r["year"] and THIS_YEAR - r["year"] >= 3)
            self.items[k] = r
            new.append(r)
        return new

    def list(self):
        return list(self.items.values())

    def context(self, limit=40):
        lines = []
        for r in self.list()[:limit]:
            tag = f"{r['type']}, {r['year'] or 'undated'}{', STALE' if r['stale'] else ''}"
            lines.append(f"[{r['id']}] ({tag}) {r['title']} - {r['snippet']}")
        return "\n".join(lines)

    def find_contradictions(self):
        if len(self.items) < 2:
            return []
        try:
            out = ask(CONTRA_SYS, self.context(), json_mode=True, role="judge")
        except Exception as exc:
            log.warning("contradiction fallback failed: %s", type(exc).__name__)
            return []
        if not isinstance(out, dict):
            return []
        found = out.get("contradictions", [])
        return found if isinstance(found, list) else []
