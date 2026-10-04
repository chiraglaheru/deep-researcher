"""Markdown export.

Two paths, because the app is a web app and the browser cannot write to the
user's disk on its own:

  * the server writes a .md file into EXPORT_DIR, so a CLI or scripted run ends
    up with a real artefact on disk
  * the same file is served over HTTP, and the browser downloads it

Nothing here tries to work around browser restrictions: the download button is a
normal anchor pointing at a real endpoint.

The export is the report plus front matter recording what was actually read, so
a saved artefact is self-describing months later.
"""
from __future__ import annotations

import datetime
import os
import re
import threading
from pathlib import Path

from . import config

_write_lock = threading.Lock()
# Remembers the most recent export per question so /api/report/download can
# serve it without the client having to pass a path.
_last: dict[str, dict] = {}


def slugify(text: str, limit: int = 70) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug[:limit].rstrip("-") or "research-report")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def output_dir() -> Path:
    directory = Path(config.export_dir())
    return directory if directory.is_absolute() else _repo_root() / directory


def build_document(question: str, report: str, stats: dict | None = None,
                   retrieval: dict | None = None) -> str:
    """Report plus provenance front matter."""
    today = datetime.date.today().isoformat()
    stats = stats or {}
    retrieval = retrieval or {}

    lines = [
        "---",
        f"title: {_yaml(question.strip().rstrip('?'))}",
        f"generated: {today}",
        f"sources: {stats.get('sources', 0)}",
        f"findings: {stats.get('records', 0)}",
    ]
    if retrieval.get("retrieved"):
        lines.append(f"full_text_sources: {retrieval.get('full_text', 0)}")
        lines.append(f"partial_sources: {retrieval.get('partial', 0)}")
        lines.append(f"words_retrieved: {retrieval.get('total_words', 0)}")
    lines += ["---", ""]
    return "\n".join(lines) + (report or "")


def _yaml(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(question: str, report: str, stats: dict | None = None,
         retrieval: dict | None = None) -> dict:
    """Write the markdown file. Returns metadata including the filename."""
    filename = f"{slugify(question)}-{datetime.date.today().isoformat()}.md"

    if not config.export_enabled():
        meta = {"written": False, "reason": "export disabled", "filename": filename}
        _last[_key(question)] = meta
        return meta

    content = build_document(question, report, stats, retrieval)
    directory = output_dir()
    meta = {"written": False, "filename": filename, "bytes": len(content.encode())}

    try:
        with _write_lock:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            path.write_text(content, encoding="utf-8")
        meta.update(written=True, path=str(path), url=f"/exports/{filename}")
    except OSError as exc:
        meta.update(reason=f"could not write file: {exc}")

    _last[_key(question)] = meta
    return meta


def _key(question: str) -> str:
    return slugify(question)


def last_export(question: str) -> dict:
    return dict(_last.get(_key(question), {}))


def read(question: str) -> str | None:
    """Contents of the last export for this question, if it exists."""
    meta = _last.get(_key(question))
    if not meta or not meta.get("written"):
        return None
    try:
        return Path(meta["path"]).read_text(encoding="utf-8")
    except OSError:
        return None


def static_dir() -> Path:
    return output_dir()


def ensure_dir() -> None:
    try:
        output_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        pass