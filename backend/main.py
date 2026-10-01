import json
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from backend.research.researcher import deep_research

app = FastAPI()


@app.get("/api/research")
async def research(q: str, rounds: int = 3):
    async def gen():
        try:
            async for ev in deep_research(q, max(1, min(rounds, 4))):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield 'data: {"type":"done"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream")


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
