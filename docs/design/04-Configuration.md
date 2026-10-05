# 04 — Configuration

Every tunable lives in `backend/research/config.py`, is read from the environment **on each access** (never cached at import), and has a working default. Nothing needs configuring to run.

Clamping is applied at read time — `max_sources()` is clamped to `1..200`, so a typo cannot produce an unbounded run.

## Model chain

| Variable | Default | Notes |
|---|---|---|
| `LLM_MODEL` | — | Primary. Must carry a litellm provider prefix |
| `LLM_FALLBACKS` | — | Comma-separated chain, tried in order |
| `LLM_MODEL_<ROLE>` | — | Optional override: `LLM_MODEL_SYNTH`, `LLM_MODEL_JUDGE`, `LLM_MODEL_DEFAULT` |

Chain construction in `llm._models(role)` is `[role_override, LLM_MODEL] + LLM_FALLBACKS`, deduped with order preserved. So the resolved chain is 1 + 1 + N unique models.

> [!important] Provider prefixes are mandatory
> A bare model name fails with `LLM Provider NOT provided`. The prefix is the **provider**, not always the vendor — NVIDIA models use `nvidia_nim/`, not `nvidia/`.
>
> A previous chain was effectively fictional: four of six entries were malformed or non-existent, so a rate limit on the primary left nothing working. **Verify a chain end-to-end before relying on it.**

## Resilience

| Variable | Default | Controls |
|---|---|---|
| `LLM_MAX_ATTEMPTS` | 2 | Attempts per model before advancing |
| `LLM_DAILY_COOLDOWN_S` | 3600 | Skip after a per-day quota |
| `LLM_PER_MINUTE_COOLDOWN_S` | 60 | Skip after a throughput throttle |
| `LLM_OVERLOAD_COOLDOWN_S` | 30 | Skip after repeated 5xx / overload |
| `LLM_OVERSIZED_COOLDOWN_S` | 900 | Skip after a request-too-large rejection |

`LLM_OVERSIZED_COOLDOWN_S` is long on purpose. When one request exceeds a provider's whole per-minute window, retrying the same prompt can never succeed, so a short value only produces a failure loop. The rejected size is also recorded as a **learned per-model ceiling**, so later oversized prompts skip the model without a failed round trip.

## Retrieval

| Variable | Default | Trade-off |
|---|---|---|
| `RESEARCH_FETCH` | 1 | Off = snippet-only mode, no document retrieval |
| `FETCH_MAX_SOURCES` | 22 | Documents attempted per run |
| `FETCH_MAX_WORKERS` | 6 | Concurrency; higher risks tripping rate limits |
| `FETCH_TIMEOUT_S` | 20 | Per-request timeout |
| `FETCH_MAX_BYTES` | 4,000,000 | Download ceiling per document |
| `FETCH_MAX_CHARS` | 400,000 | Text kept after cleaning |
| `FETCH_MAX_PAGES` | 40 | PDF pages parsed |
| `FETCH_RESPECT_ROBOTS` | 1 | Leave on — the pipeline is a crawler |

`RESEARCH_FETCH_BUDGET` (28) is the **per-run** fetch ceiling, held on the `Fetcher` instance in graph state rather than in module globals, so concurrent requests do not share it.

## Chunking and relevance

| Variable | Default | Trade-off |
|---|---|---|
| `CHUNK_TARGET_CHARS` | 1400 | Smaller = more precise chunks, more calls |
| `CHUNK_MAX_CHARS` | 2600 | Hard cap; oversized blocks split on sentences |
| `CHUNKS_PER_SOURCE` | 6 | Caps one verbose document's influence |
| `RELEVANCE_TOP_CHUNKS` | 70 | Passages reaching extraction |
| `RELEVANCE_MIN_SCORE` | 0.0 | Raise to prune weak matches |

## Evidence extraction

| Variable | Default | Trade-off |
|---|---|---|
| `RESEARCH_ANALYZE` | 1 | Off = skip extraction, legacy single-shot report |
| `EXTRACT_BATCH_CHARS` | 13,000 | **Evidence per LLM call.** Raise until the context limit bites |
| `EXTRACT_MAX_BATCHES` | 14 | Hard ceiling on extraction calls |
| `MAX_EVIDENCE_RECORDS` | 160 | Cap on records reaching the report |

`EXTRACT_BATCH_CHARS` is the throughput dial: it is how many characters of source text go into one request, and one request returns every finding those chunks support.

## References

| Variable | Default | Trade-off |
|---|---|---|
| `RESEARCH_REFERENCES` | 1 | Off = no outbound link mining |
| `REFERENCES_MAX` | 6 | Total extra sources from references |
| `REFERENCES_PER_SOURCE` | 3 | Leads per document |
| `REFERENCES_MAX_DEPTH` | 1 | 1 = references of retrieved docs only |

Depth is capped at 3 and defaults to 1. Chasing citations recursively is how a research tool turns into an unbounded crawler.

## Report

| Variable | Default | Trade-off |
|---|---|---|
| `REPORT_DEPTH` | `deep` | `brief` = 4 sections, shorter prompts |
| `REPORT_MAX_TOKENS` | 14,000 | Per-section ceiling; verified 5,000+ output tokens |
| `REPORT_MIN_WORDS` | 3,000 | Depth guidance, not a quota — the model earns its length |
| `REPORT_MAX_SECTIONS` | 12 | Bounds writing calls |
| `REPORT_EVIDENCE_PER_SECTION` | 60 | **The prompt-size dial.** Lower it for tight TPM providers |
| `REPORT_QUALITY_FLOOR` | 0.0 | Skip sections below this evidence support |

> [!warning] `REPORT_EVIDENCE_PER_SECTION` is the knob for provider TPM limits
> Groq's free tier caps at 8,000 tokens per minute. Section prompts measured 8,500–9,800 tokens at the default, exceeding the window on their own — which is why a section can fail for every model in a chain that all share that ceiling. Lower it, or prefer a wider-context model.

## Export

| Variable | Default | Notes |
|---|---|---|
| `EXPORT_ENABLED` | 1 | Off = no file written |
| `EXPORT_DIR` | `exports` | Relative paths resolve against the repo root |

## Sub-question planning

| Variable | Default | Trade-off |
|---|---|---|
| `PLAN_DEDUP_THRESHOLD` | 0.70 | Rare-term overlap above which a sub-question is a restatement. `1.0` disables |
| `PLAN_DEDUP_GRAM_THRESHOLD` | 0.45 | Character-overlap signal, catching rewordings. `1.0` disables |
| `PLAN_DEDUP_REGENERATE` | 1 | Ask the planner for replacements when duplicates are found |

## Incremental analysis

| Variable | Default | Trade-off |
|---|---|---|
| `ANALYSE_REUSE_SOURCES` | 1 | Skip sources already extracted from. `0` re-reads the whole corpus each round |

## Request pacing

| Variable | Default | Trade-off |
|---|---|---|
| `THROTTLE_LLM` | 1 | Paces model calls. Turn off once on paid plans |
| `THROTTLE_LLM_PER_MINUTE` | 12 | Ceiling on model calls per minute. `0` disables pacing |
| `THROTTLE_LLM_MAX_CONCURRENT` | 1 | Calls in flight; also serialises two concurrent runs |
| `THROTTLE_SEARCH` | 0 | SerpApi is a metered paid plan; leave off unless it throttles you |
| `THROTTLE_SEARCH_PER_MINUTE` | 60 | Search call ceiling |
| `THROTTLE_SEARCH_MAX_CONCURRENT` | 4 | Caps total concurrent searches across workers |

For a 43-call run: 30/min adds ~1.4 min, 20/min ~2.1 min, 12/min ~3.6 min on top
of an already ~20 minute run.

## Global budgets

| Variable | Default | Prevents |
|---|---|---|
| `RESEARCH_MAX_SOURCES` | 30 | Unbounded source collection |
| `RESEARCH_RESULTS_PER_SEARCH` | 6 | Unbounded rows per query |
| `RESEARCH_MAX_ROUNDS` | 2 | Unbounded gap-check loop |
| `RESEARCH_MAX_ROUNDS_LIMIT` | 10 | Hard ceiling on depth, shared by API and UI |
| `RESEARCH_LLM_BUDGET` | auto | Auto-sizes to depth (17 + 13/round). Set only to pin |
| `RESEARCH_FETCH_BUDGET` | 28 | Unbounded downloads |
| `RESEARCH_TIME_BUDGET_S` | 600 | Soft wall-clock ceiling |

`CallBudget` is created per run and passed through graph state. The ceiling
**auto-sizes to the requested depth** when `RESEARCH_LLM_BUDGET` is unset,
because a flat ceiling interacts badly with the round limit: more rounds means
more extraction, so a budget sized for two rounds silently starves synthesis at
four and the report returns partial. Pinned too low, the planner logs a warning
and adds a pipeline note.

## Search and dev

| Variable | Default | Notes |
|---|---|---|
| `SEARCH_CACHE_DIR` | `.cache/serp` | Unset = disabled |
| `SERPAPI_KEY` | — | Required for all search |
| `GITHUB_TOKEN` | — | Optional; raises GitHub API rate limits |
| `LLM_MOCK` / `SEARCH_MOCK` | off | Canned responses, no real calls |
| `LLM_MOCK_FAIL` / `SEARCH_MOCK_FAIL` | — | Force failure for one role/source |

Mock mode is opt-in and defaults **off**. A test that sets `LLM_MOCK` in the process environment would otherwise silently return canned data for real requests.

## Tuning recipes

**Run faster, smaller** — `REPORT_DEPTH=brief`, `REPORT_MAX_SECTIONS=5`, `FETCH_MAX_SOURCES=10`, `REPORT_EVIDENCE_PER_SECTION=25`

**Maximise depth** — `RESEARCH_LLM_BUDGET=70`, `RESEARCH_MAX_ROUNDS=3`, `EXTRACT_MAX_BATCHES=25`, `RELEVANCE_TOP_CHUNKS=140`

**Survive a tight TPM provider** — `REPORT_EVIDENCE_PER_SECTION=20`, `CHUNK_TARGET_CHARS=900`, `REPORT_MAX_SECTIONS=8`

**Debug with no API spend** — `LLM_MOCK=1`, `SEARCH_MOCK=1`; inspect `retrieval`/`analysis` events and the `status` event

**Stop throttling once paid** — `THROTTLE_LLM=0`, or use the UI selector

## Tuning order

If output is thin, check in this order — each step is cheaper than the next:

1. `status` event — is the run `partial`? Which sections are missing, and why?
2. `retrieval` event — how many documents were `full` vs `metadata_only`?
3. `analysis` event — how many findings survived, and were any discarded as unverified?
4. `RESEARCH_LLM_BUDGET` — did extraction get cut off before synthesis?