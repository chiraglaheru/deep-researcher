# 02 — File Reference

Every active file, its public surface, and the one thing it is responsible for. Line counts are from `dev-dxhize-v2` at `ea0ead2`.

## Backend — interface

### `backend/main.py` — 197 lines
FastAPI app. Owns HTTP, nothing else.

| Symbol | Purpose |
|---|---|
| `research(q, rounds=2)` | `GET /api/research`. Clamps `rounds` to 1–4. Returns `StreamingResponse(text/event-stream)`; every frame is `data: {json}\n\n`; always ends `done`. Wraps the generator so any exception becomes an `error` event followed by `done` |
| `download(q)` | `GET /api/report/download`. Serves the server-written export as an attachment, or 404 |
| `report_markdown(q)` | `GET /api/report/markdown`. Same text as JSON |
| `_exports_dir()` | Resolves `EXPORT_DIR`, mounting `/exports` only if creatable |

`GET /api/config` advertises limits and throttle defaults so the UI never caps
below what the backend permits. `GET /api/throttle` reports live pacing state.
`/api/research` takes `?throttle=0|1`, applied to that run only.

CORS allows any loopback origin on any port by default, plus `CORS_ORIGINS` for
real hostnames or LAN addresses; remote origins are still refused. Static paths
resolve from `__file__`, so the app boots from any working directory.

### `backend/research/researcher.py` — 103 lines
Translates LangGraph node updates into the SSE contract. The only module that imports `graph.py`.

`deep_research(question, max_rounds=3)` is an async generator over `graph.astream(init, stream_mode="updates")`. Maps node name → event:

| Node | Event |
|---|---|
| `planner` | `plan` |
| `search_worker` | `results` |
| `collect` | `evidence` |
| `retrieve` | `retrieval` |
| `analyse` | `analysis` |
| `gap_check` | `gap` |
| `contradiction_check` | `contradictions` |
| `synthesizer` | `status` **then** `report` |

`status` is emitted before `report` so a client can render the state before receiving a potentially 100 KB body.

## Backend — orchestration

### `backend/research/graph.py` — 532 lines
The DAG. Owns sequencing, routing and state shape — not the work itself.

| Symbol | Purpose |
|---|---|
| `State` | `TypedDict(total=False)`; three keys carry `operator.add` reducers |
| `planner(state)` | Calls `make_plan`, builds `pending`, seeds `seen` and `budget` |
| `route(state)` | After planner: fan out `Send` per pending search, else `retrieve` |
| `route_after_gap(state)` | After gap check: fan out again, else `contradiction_check`. **Separate from `route` on purpose** |
| `search_worker(task)` | One SerpApi call via `asyncio.to_thread`; failure recorded, not raised |
| `collect(state)` | Rebuilds a `Collector` from accumulated `raw` |
| `retrieve(state)` | Skips already-read URLs, fetches the rest, folds results into `retrieval_index`, mines references, re-chunks the union |
| `_aggregate_retrieval(index, chunks, references_used)` | Derives all counters from the one index so they cannot disagree |
| `analyse(state)` | Skips sources already extracted, ranks, extracts, merges across rounds, synthesises only new sources |
| `_unprocessed_chunks(state, chunks)` | Filters out sources already read, keeping any whose grade improved |
| `gap_check(state)` | Enforces `max_rounds`; asks for ≤3 targeted follow-ups, filtered against `seen` |
| `contradiction_check(state)` | `find_conflicts` over findings, or snippet fallback if <2 findings |
| `synthesizer(state)` | `generate_report` in a thread, then `save_export`, then builds completion state |
| `build_graph()` | Registers nodes/edges, returns `g.compile()` |

### `backend/research/config.py` — 319 lines
Every tunable, env-driven with a working default. Read on each access, never cached, so tests and `.env` changes take effect without restart. 34 getters, all clamped. Pure leaf module.

## Backend — LLM infrastructure

### `backend/research/llm.py` — 622 lines
The only path to a model. Owns retry, fallback, health, budget, and mock mode.

| Symbol | Purpose |
|---|---|
| `LLMChainError` | Every model in the chain failed |
| `CallBudget(total, reserve)` | Bounds LLM calls per run; `take()`, `available()`, `snapshot()` |
| `ask(system, user, json_mode, role)` | **The entry point.** Chain resolution → per-model attempts → return |
| `_models(role)` | `[role override, default] + LLM_FALLBACKS`, deduped, order preserved |
| `_chain_for_call(role, approx_tokens)` | Returns `(models, skipped)`; drops cooling and oversized models, records why |
| `classify_rate_limit(msg)` | → `daily` / `request-too-large` / `per-minute` |
| `oversized_request_size(msg)` | Extracts `Requested 9838` from a provider body |
| `_cool_down` / `_clear_cooldown` / `_remaining` | Per-model cooldown table, lock-guarded |
| `_note_ceiling` / `_exceeds_ceiling` | Learned prompt ceilings: a model that rejected a size is skipped for that size |
| `_retryable(exc)` | Transient only — `TRANSIENT` tuple or HTTP ≥500 |
| `oversized_cooldown_s()` | 900 s; long because retrying an oversized prompt can never succeed |

**Retry policy:** rate limit → advance immediately, never retry same model. Transient 5xx → retry same model with `1s, 2s, 4s + jitter`. Non-retryable provider error (`Auth`, `BadRequest`, `NotFound`) → advance. Programming errors propagate.

### `backend/research/planner.py` — 280 lines
Plans, deduplicates, and replaces sub-questions.

`make_plan(question)` → `ask()` → sub-questions, then:
- `_clean_plan` drops malformed sub-questions and any carrying no usable search
- `dedupe_subquestions` removes restatements using two signals with independent
  thresholds — rare-term overlap (IDF-weighted, so shared entity names do not
  trigger it) and character trigrams (blind to morphology, catches rewordings)
- `regenerate` asks for replacements covering ground no survivor reaches, and
  returns `[]` on any failure

Measured on real plans: a reworded duplicate scores ~0.61, genuinely distinct
dimensions 0.05–0.09, so the 0.45 threshold sits in a wide gap.

### `backend/research/searcher.py` — 139 lines
`search(source, query, n=6)` → SerpApi. Maps source to engine: `web`→`google`, `news`→`google_news`, `scholar`→`google_scholar`, `github`→`google` with `site:github.com`. Normalises to `{title, url, snippet, date, type, query}`. Optional on-disk cache under `SEARCH_CACHE_DIR`. Full mock mode.

### `backend/research/collector.py` — 60 lines
`Collector` — dedupe by normalised URL (`www.` and trailing slash folded away), drop rows with no URL, flag ≥3 years as `stale`. `context(limit=40)` renders the numbered block a model sees. `year_of(date)` parses loose date strings.

## Backend — retrieval (all deterministic)

### `backend/research/fetch.py` — 724 lines
Retrieves the real document behind a search result. Owns HTTP, parsing, and quality grading.

| Symbol | Purpose |
|---|---|
| `FetchedDoc` | `status` ∈ `full`/`partial`/`metadata_only`/`failed`, plus `method`, `limitation`, `links`, `pages`, `authors`, `doi` |
| `Fetcher` | `fetch(url)`, `fetch_many(urls)`. Thread pool, per-run budget, `Retry-After` honoured once on 429/5xx |
| `github_repo(url)` | Parsed, not regexed — handles scheme, `www.`, fragments, `/tree/main`, and reserved paths |
| `snippet_only_doc(result)` | Builds an explicitly-downgraded record for an unreadable source |
| `_from_pdf` | pypdf, per page, `\f` page markers preserved |
| `_from_arxiv` | arXiv API for authoritative metadata, then the abs page |
| `_from_github` | API (stars/licence/topics/push date) + raw README |
| `_from_html` | `lxml`, noise tags removed, `_structured_text` preserves headings/tables/code |
| `_html_metadata` | `<meta>`, OpenGraph, and JSON-LD walk for title/publisher/date/authors |

Respects `robots.txt`. Never attempts to defeat a paywall or login — downgrades and records the reason instead.

### `backend/research/chunker.py` — 262 lines
Structure-aware splitting with provenance.

`Chunk` carries `chunk_id`, `source_id`, `source_url`, `source_title`, `source_type`, `publication_date`, `section` (heading path like `3.2 Attention`), `page`, `content`, `char_start`, `retrieval_status`, `publisher`, `authors`, `doi`.

`chunk_document()` groups blocks to `CHUNK_TARGET_CHARS`, hard-caps at `CHUNK_MAX_CHARS`, splits an oversized block on sentence boundaries, and samples evenly (`_spread`) when a document exceeds `CHUNKS_PER_SOURCE`.

### `backend/research/relevance.py` — 231 lines
Deterministic scoring. No model calls at all.

| Symbol | Purpose |
|---|---|
| `targets(question)` | The things compared: `React Native, Flutter, native Android` |
| `dimensions(question)` | The aspects analysed: `performance, app size, …` — drives report sections |
| `rank(chunks, question, plan, top_k)` | IDF-weighted term overlap, boosts for dimension coverage and quantitative content, penalty for boilerplate |
| `diversify(scored, per_source_cap)` | Round-robin so one verbose document cannot flood the batch |

### `backend/research/references.py` — 139 lines
Bounded reference discovery. `candidates()` mines outbound links/DOIs from documents actually read, scores them deterministically against the question, skips social/account hosts, and caps at `REFERENCES_MAX` / `REFERENCES_PER_SOURCE` with depth 1. No uncontrolled crawling.

## Backend — evidence

### `backend/research/evidence.py` — 270 lines
The structured evidence layer. Leaf module.

| Symbol | Purpose |
|---|---|
| `Evidence` | `claim`, `detail`, verbatim `quote`, `dimension`, `targets`, full source metadata, `section`, `page`, `chunk_id`, `retrieval_status`, `quality`, `limitations`, `confidence` |
| `quote_is_supported(quote, chunk, 0.72)` | Sliding-window word overlap — the anti-fabrication guard |
| `dedupe(records)` | Keeps the best-attested of near-identical claims |
| `statistics(records)` | Counts by quality, retrieval status, source type, dimension |
| `source_type_label` | Human label for the report |

`usable_url` prefers DOI over URL, so citations always resolve to something canonical.

### `backend/research/extract.py` — 469 lines
Batched LLM extraction — the highest-value/highest-risk module.

| Symbol | Purpose |
|---|---|
| `plan_batches(scored)` | Packs chunks to `EXTRACT_BATCH_CHARS`, keeping a source's chunks together |
| `extract_evidence(...)` | One call per batch; returns `(records, notes)` |
| `_harvest(reply, batch, by_id)` | **Both guards.** Discards records citing a chunk not in the batch; discards quotes not findable in the passage when a number is involved |
| `synthesise_sources(records, budget)` | Per-source synthesis, several sources per call; what the source *does not* support |
| `find_conflicts(records, budget)` | Batched disagreement detection over findings |
| `findings_from_contradictions(...)` | Promotes disagreements to first-class evidence |

Extraction prompt rules: ground every record in a verbatim quote; keep conditions attached to numbers; a vendor's claim about its own product is never `strong`; returning nothing for a passage is correct and cheap.

## Backend — synthesis and output

### `backend/research/report.py` — 674 lines
Section-wise writing. Owns citation integrity and completion state.

| Symbol | Purpose |
|---|---|
| `assign_references(records)` | `url → n`, ordered by evidence strength. Runs **before any model call** |
| `render_references(records, refs)` | Deterministic reference list, sequential `1..N`, discloses partial/metadata-only retrieval |
| `generate_report(...)` | Returns `ReportResult(markdown, status, missing_sections, notes)` |
| `compose(...)` | Internal closure enforcing the missing-section invariant centrally |
| `classify_status` / `status_headline` / `partial_banner` | `completed` / `partial` / `failed` |
| `_select_for_dimension(records, dim, targets)` | Picks the evidence a section should see |
| `_write_section(...)` | One section, one call, given only relevant evidence |
| `_evidence_table(records, refs)` | Deterministic Evidence-by-Source table |
| `write_report(...)` | Markdown-only wrapper, kept for compatibility |
| `_write_report_legacy(...)` | Original single-shot prompt, used when no evidence exists |

Sections: Executive Summary, Scope and Methodology, one per requested dimension, Where the Evidence Conflicts, Scenario Analysis, Decision Framework, Evidence Quality and Limitations, Conclusion, Evidence by Source, References.

Seven prompt systems, each carrying a shared discipline block: cite immediately after the specific claim; never invent a reference number; state conditions with numbers; vendor claims are interested parties; one benchmark is not a general ranking; say the evidence is insufficient when it is.

### `backend/research/report_prompts.py` — 13 lines
The original single-call prompt, kept verbatim for the no-evidence fallback.

### `backend/research/export.py` — 125 lines
Markdown export with YAML front matter recording what was actually read. `save()` writes to `EXPORT_DIR`, remembers the path per question so `/api/report/download` can serve it. Thread-locked.

### `backend/research/throttle.py` — 193 lines
Process-wide pacing. `Throttle` bounds calls per minute and in flight; the
registry holds one instance per kind (`llm`, `search`) shared by every run.
Verified spacing is exact (5.0s at 12/min) and a slot is released even when the
call raises, so a provider error cannot leak one.

## Frontend

### `frontend/index.html` — 217 lines
Static shell: question box, depth selector, and cards for Source Retrieval, Evidence Extraction, Research Plan, Searches, Evidence Check, Where Sources Disagree, status banner, and the report. Loads `markdown.js` then `app.js`.

### `frontend/app.js` — 820 lines
SSE consumer. Reads `response.body` as a stream, splits on `\n\n`, parses `data:` frames. Renders retrieval stat tiles (with per-source status badges), evidence notes, plan, searches, gaps, contradictions, the status banner, and the report.

`renderStatus()` is the honest-state renderer: coloured banner, list of missing sections, and `is-partial` / `is-failed` on the report card.

### `frontend/markdown.js` — 325 lines
Hand-written markdown renderer. No CDN, so the page stays offline-capable and loads no third-party script.

Escapes everything first, then adds structure. Autolinks are extracted **before** escaping so reference URLs stay clickable. Citations become `#ref-N` anchors. The References section buffers each numbered entry with its indented continuation lines into one `<li>`, inside a single `<ol>` — otherwise each entry gets its own list and every reference is renumbered `1.`.

### `frontend/style.css` — 749 lines
Dark theme plus the retrieval stat tiles, status badges, reference-list anchors, and completion-state colours.

## Tests — 228 passing

| File | Lines | Covers |
|---|---|---|
| `test_throttle.py` | 279 | Pacing, concurrency ceiling, slot release on failure, wiring |
| `test_planner_dedup.py` | 296 | Sub-question dedup and regeneration, incremental extraction |
| `test_llm.py` | 589 | Retry, fallback, three rate-limit kinds, cooldowns, prompt ceilings, 8-thread concurrency |
| `test_deep_research.py` | 866 | Fetch strategies, chunking, evidence guards, relevance, stats accumulation, status, export |
| `test_mock_pipeline.py` | 292 | Whole graph on mocks |
| `test_frontend_render.py` | 125 | Real `markdown.js` via dukpy: citations, numbering, XSS |
| `test_sse.py` | 341 | SSE contract, ordering, clamping, CORS, 422 |
| `test_pipeline.py` | 109 | Graph wiring, collector; live SerpApi (marked `live`) |
| `test_search_cache.py` | 94 | Cache |
| `test_{collector,github,news,serpapi}.py` | 664 | Legacy modules, off-path |
| `conftest.py` | 57 | `sys.path`, `live` marker, env cleanup, throttling disabled by default |

## Off-path (dead but tested)

`backend/tools/{github,news,serpapi}.py` — 322 lines. `backend/evidence/collector.py` — 44 lines. Nothing on the live path imports them; `searcher.py` calls `serpapi.GoogleSearch` directly. About a third of the codebase, kept alive only by their tests.