import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.research.researcher import deep_research

app = FastAPI()


def _cors_origins() -> list[str]:
    """Extra origins from CORS_ORIGINS, comma separated.

    Needed as soon as anyone reaches the app by anything other than
    localhost:8000 -- a LAN address, a container port, or a separate dev server
    on a different port. None of those are guessable, so they are configurable.
    """
    raw = os.environ.get("CORS_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _cors_origin_regex() -> str | None:
    """Pattern for loopback origins on any port.

    On by default so a teammate running a dev server on 5173/3000/8080 is not
    blocked by a hardcoded port list. Loopback only, so this grants nothing to a
    remote host; set CORS_ORIGINS for anything else.
    """
    configured = os.environ.get("CORS_ORIGIN_REGEX")
    if configured is not None:
        return configured.strip() or None
    return r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=_cors_origin_regex(),
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.get("/api/research")
async def research(q: str, rounds: int = 2):
    async def gen():
        try:
            async for ev in deep_research(q, max(1, min(rounds, 4))):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield 'data: {"type":"done"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/report/download")
async def download(q: str):
    """Serve the markdown export for a question as a file download.

    The browser cannot write to the user's disk, so the file the server already
    wrote is served back with a download disposition rather than pretending the
    page generated the file locally.
    """
    from backend.research.export import read, slugify

    content = read(q)
    if content is None:
        raise HTTPException(status_code=404,
                            detail="no exported report found for this question")
    filename = f"{slugify(q)}.md"
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/report/markdown")
async def report_markdown(q: str):
    """Raw markdown text, for clients that want to render it themselves."""
    from backend.research.export import read

    content = read(q)
    if content is None:
        raise HTTPException(status_code=404,
                            detail="no exported report found for this question")
    return {"markdown": content}


def _exports_dir() -> Path:
    from backend.research.export import output_dir
    return output_dir()


def _frontend_dir() -> Path:
    """Absolute path to the bundled frontend.

    A relative "frontend" resolves against the *process* working directory, so
    it works when uvicorn is started from the repo root and fails with
    "Directory 'frontend' does not exist" the moment anything starts the app
    from anywhere else -- a different shell, an IDE runner, or pytest.
    """
    return Path(__file__).resolve().parents[1] / "frontend"


# Only mount when the directory exists, so a fresh checkout still boots.
try:
    _exports_dir().mkdir(parents=True, exist_ok=True)
    app.mount("/exports", StaticFiles(directory=str(_exports_dir())), name="exports")
except OSError:
    pass

_frontend = _frontend_dir()
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
else:                                            # pragma: no cover
    raise RuntimeError(
        f"frontend directory not found at {_frontend}. "
        "The repository looks incomplete -- re-clone it."
    )