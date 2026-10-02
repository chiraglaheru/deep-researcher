# Deep Researcher

An agentic research system. It takes a question, plans sub-questions, searches several sources in parallel, collects and deduplicates evidence, checks whether the evidence is sufficient and searches again as needed, and then synthesises a final cited report.

## Overview

```text
                    ┌──────────────┐
   research  ──────▶│   Planner    │  question → 3-5 sub-questions + searches
   question          └──────┬───────┘
                           │ pending searches
                           ▼
                 ┌─────────────────────┐
                 │   route (LangGraph) │  one parallel worker per search
                 └──┬───┬───┬───┬──────┘
                    ▼   ▼   ▼   ▼
              ┌──────────────┐
              │ search_worker│  web / news / scholar / github
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │   collect    │  dedupe by URL, flag stale
              └──────┬───────┘
                     ▼
              ┌──────────────┐   not sufficient → follow-up searches, loop
              │  gap_check   │ ─────────────────────────────┐
              └──────┬───────┘                              │
                     │ sufficient / out of rounds           │
                     ▼                                      │
          ┌────────────────────┐                             │
          │ contradiction_check│  sources that disagree      │
          └─────────┬──────────┘                             │
                    ▼                                        │
             ┌────────────┐                                  │
             │ synthesizer│  final cited report              │
             └────────────┘                                  │
                    └────────────────────────────────────────┘
```

Orchestration is a real [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph` in `backend/research/graph.py`. Fan-out uses `Send`, and the gap-check loop re-enters the same search graph until the evidence is sufficient or the round limit is reached.

The backend is FastAPI; the frontend is plain HTML/CSS/JS with no build step. FastAPI also serves the frontend, so there is no second server to run.

## Features

- Breaks a question into 3–5 sub-questions, each with its own searches
- Runs every search in a source round concurrently
- Sources: Google web, Google News, Google Scholar, and GitHub, all through SerpApi
- Deduplicates evidence by normalised URL and flags sources older than three years as stale
- Loops back for more searching when the evidence is thin, up to a configurable round limit
- Detects genuine disagreements between sources
- Streams every stage to the browser over SSE — no polling, no waiting for the whole run
- Retries and falls back across several models when one is rate-limited or overloaded
- Skips models that just failed instead of re-trying them at the next stage
- Optional mock mode runs the entire pipeline with zero API calls
- Dev-only SerpApi response cache

## Requirements

- Python 3.10+ (developed and tested on 3.14.7)
- A Gemini API key
- A SerpApi key
- A GitHub token is optional

## Setup

```bash
git clone <repository-url>
cd deep-researcher
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the template and fill in the keys:

```bash
cp .env.example .env
```

`.env` is gitignored. Never commit it.

### Credentials

| Variable | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | yes | used by `litellm` for the `gemini/...` model prefix |
| `SERPAPI_KEY` | yes | all four search sources |
| `GITHUB_TOKEN` | no | raises GitHub API rate limits |

### Model chain

Calls try models in order and move on when one is unavailable.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_MODEL` | `gemini/gemini-3.6-flash` | primary model |
| `LLM_FALLBACKS` | — | comma-separated fallbacks, tried in order |
| `LLM_MODEL_DEFAULT` | falls back to `LLM_MODEL` | planner, gap check |
| `LLM_MODEL_JUDGE` | falls back to `LLM_MODEL` | contradiction check |
| `LLM_MODEL_SYNTH` | falls back to `LLM_MODEL` | report synthesis |

### Resilience

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_MAX_ATTEMPTS` | `2` | attempts per model for transient 5xx / connection errors |
| `LLM_DAILY_COOLDOWN_S` | `3600` | how long to park a model after a daily-quota 429 |
| `LLM_PER_MINUTE_COOLDOWN_S` | `60` | fallback cooldown when a 429 reports no retry delay |
| `LLM_OVERLOAD_COOLDOWN_S` | `30` | cooldown after a model exhausts every attempt on 503 |

A per-model health tracker, guarded by a lock and timed with `time.monotonic()`, remembers which models just failed. Cooling models are skipped when the chain is built, so a model that returned 429s or 503s does not get retried immediately in the next stage.

### Caching

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEARCH_CACHE_DIR` | — | dev-only SerpApi response cache; unset disables it |

Entries are keyed by a sha1 of the source, query and result count. A relative path resolves against the repository root, not the process working directory. Mock mode bypasses the cache.

### Mock mode

Everything above is optional. With these set, the pipeline runs end to end with no API calls at all — useful for frontend work, offline development and tests.

| Variable | Purpose |
| --- | --- |
| `LLM_MOCK=1` | return canned replies shaped like each caller's parser |
| `LLM_MOCK_FAIL` | `planner`, `gap`, `judge`, `synth` or `all` — force a failure |
| `LLM_MOCK_DELAY_MS` | artificial latency so streaming is visible (default 400) |
| `SEARCH_MOCK=1` | return deterministic synthetic results |
| `SEARCH_MOCK_FAIL` | `web`, `news`, `scholar`, `github` or `all` |
| `SEARCH_MOCK_DELAY_MS` | artificial latency (default 300) |

## Running

```bash
source venv/bin/activate
uvicorn backend.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

The frontend is served by the same process. To develop the frontend separately, run it on port 5500, which is already allowed by the CORS configuration:

```bash
python3 -m http.server 5500 --directory frontend
```

The frontend requests `/api/research` with a relative path, so it works under either setup.

## API

### `GET /api/research`

| Parameter | Default | Notes |
| --- | --- | --- |
| `q` | required | the research question; missing returns 422 |
| `rounds` | `2` | clamped to `1`–`4` |

Responds with `text/event-stream`. Every frame is `data: {json}\n\n`, and the stream always ends with a `done` frame.

```bash
curl -N "http://127.0.0.1:8000/api/research?q=What%20is%20FastAPI%3F&rounds=1"
```

### Events

| Type | Payload |
| --- | --- |
| `plan` | `data` = `{"subquestions":[{"question","searches":[{"source","query"}]}]}` |
| `results` | `source`, `query`, `count`, `error` — one per search |
| `evidence` | `count` of unique evidence collected so far |
| `gap` | `data` = `{"sufficient","missing","follow_ups"}` |
| `contradictions` | `data` = list of `{topic,side_a,sources_a,side_b,sources_b}` |
| `report` | `data` = markdown, `sources` = the evidence used |
| `error` | `message` |
| `done` | always last |

A failing search does not stop the run: it is reported in that search's `results` event with `error` set. A pipeline-level failure emits `error` followed by `done`.

A typical `rounds=1` run:

```text
plan → results ×N → evidence → gap → contradictions → report → done
```

With more rounds, a `gap` reporting `sufficient: false` is followed by another batch of `results` and a second `evidence`.

## How it works

### Planner — `backend/research/planner.py`

Sends the question to the model and validates the reply: it must be a JSON object with a `subquestions` list, and searches are filtered to known sources with a non-empty query. Invalid shapes are rejected before the search stage begins.

### Search — `backend/research/searcher.py`

`search(source, query, n)` maps each source to a SerpApi engine — `google`, `google_news`, `google_scholar`, and `google` with a `site:github.com` prefix — and normalises every row to `{title, link, snippet, source}`. The results are then sent to the collector.

### Evidence — `backend/research/collector.py`

Deduplicates by URL with `www.` and trailing slashes normalised away, drops rows without a URL, and marks anything three years or older as stale. `context(limit=40)` renders the numbered block that the model sees when it judges sufficiency and contradictions.

### Gap check — `backend/research/graph.py`

Asks whether the evidence is sufficient. If not, it may request up to three follow-up searches; anything already run is filtered out, and the loop stops at the round limit.

### Contradiction check

Asks for claims where sources genuinely disagree, returning an empty list when they do not. Skipped entirely when there is less than two sources.

### Synthesis — `backend/research/report.py`

Writes the report from the numbered evidence only, citing as `[n]`, with sections for the short answer, key findings, disagreements and caveats.

### LLM layer — `backend/research/llm.py`

Every call goes through one `ask()`:

- up to `LLM_MAX_ATTEMPTS` attempts per model with exponential backoff and jitter
- `RateLimitError` moves to the next model without retrying the same one
- `NotFoundError`, `AuthenticationError` and `BadRequestError` skip immediately
- programming errors such as `TypeError` and `KeyError` propagate untouched
- 60s per-call timeout, with `litellm`'s own retries disabled so they do not multiply
- failures log a `WARNING` naming the model, the exception class and a short summary
- exhausting the chain raises `LLMChainError`, which the SSE layer turns into an `error` event

Each run makes `rounds + 2` model calls: planner, one gap check per non-final round, contradiction check, synthesis.

## Project structure

```text
deep-researcher/
├── backend/
│   ├── main.py                  FastAPI app, CORS, SSE endpoint, static mount
│   ├── research/
│   │   ├── graph.py             LangGraph pipeline
│   │   ├── llm.py               retries, fallback chain, model health, mock mode
│   │   ├── planner.py           question → sub-questions
│   │   ├── searcher.py          SerpApi wrapper, response cache, mock mode
│   │   ├── collector.py         dedupe, freshness, contradiction detection
│   │   ├── researcher.py        graph → SSE events
│   │   └── report.py            report synthesis
│   ├── tools/                   legacy single-purpose search tools
│   └── evidence/                legacy evidence collector
├── frontend/
│   ├── index.html
│   ├── app.js                   SSE consumer
│   └── style.css
├── tests/
│   ├── test_sse.py              SSE contract, ordering, clamping, 422
│   ├── test_llm.py              retry, fallback, cooldowns, 429 classification
│   ├── test_mock_pipeline.py    whole graph on mocks
│   ├── test_search_cache.py     search cache
│   ├── test_pipeline.py         graph wiring, collector, live SerpApi
│   └── test_{collector,github,news,serpapi}.py
├── conftest.py                  sys.path setup, `live` marker, env cleanup
├── .env.example
└── requirements.txt
```

`backend/tools/` and `backend/evidence/` are the pre-LangGraph layer. They are still covered by tests but are no longer on the live path.

## Testing

```bash
pytest -q
```

111 tests. All but one run offline. `test_serpapi_live` is marked `live` and spends one SerpApi credit; skip it with:

```bash
pytest -q -m "not live"
```

A `live` marker on a test tells the autouse `clean_env` fixture to leave `SERPAPI_KEY` alone, so the test can reach the real API.

## Tech stack

- **Backend** Python, FastAPI, Uvicorn, Pydantic
- **Orchestration** LangGraph
- **Models** `litellm`, Google Gemini
- **Search** SerpApi (`google-search-results`)
- **Frontend** HTML, CSS, vanilla JavaScript — no build step, no framework
- **Tests** pytest, FastAPI `TestClient`

## Development workflow

```bash
git switch -c feature/your-feature
venv/bin/python -m pytest -q
git diff --check
git add .
git commit -m "Describe your change"
git push -u origin feature/your-feature
```

Then open a pull request.

Mock mode makes it possible to work on the frontend with no API spend:

```bash
LLM_MOCK=1 SEARCH_MOCK=1 uvicorn backend.main:app --reload
```

## Known gaps

- `README` aside, there is no CI workflow, so nothing runs the suite on a pull request.
- The 429 parser matches several spellings of Google's quota id and retry delay because no live 429 was available to test against. A misclassification degrades to the shorter per-minute cooldown instead of the more precise daily backoff.
- The cooldown tracker is per-process: it resets on restart, and multiple uvicorn workers would each rediscover the same failing models independently.
- There is no blocked-domain list and no relevance filtering, so low-quality sources reach the model.
- `graph.py` catches broad `Exception` around search calls, which will also mask genuine bugs in that block.
- The report renders as plain text in the browser, so markdown headings appear literally.
- The SerpApi cache rarely hits across runs, because the planner is nondeterministic and generates different queries each time. It reliably prevents duplicate calls within a run.

## Contributing

1. Create a feature branch.
2. Make focused changes.
3. Add or update tests.
4. Run the full suite.
5. Push and open a pull request.

Never commit API keys or other secrets.
