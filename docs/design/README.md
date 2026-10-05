# Deep Researcher — Design Document Hub

Navigation for the system design of the **v2 research pipeline**.

> [!success] On `dev-dxhize-v2`
> These documents describe branch **`dev-dxhize-v2`** at `ea0ead2`, which is the
> v2 pipeline (merged to `main` via PR #16) plus request pacing, configurable
> research depth, incremental analysis and sub-question dedup.
> 228 tests pass (`pytest -q -m "not live"`).

## Documents

| # | Document | What it answers |
|---|---|---|
| 00 | [Overview](00-Overview.md) | What it does, the flow, the key mechanisms |
| 01 | [Architecture](01-Architecture.md) | How the layers fit; what LangGraph contributes and what it doesn't |
| 02 | [File Reference](02-File-Reference.md) | Every file, its public functions, its single responsibility |
| 03 | [Data Stream](03-Data-Stream.md) | The byte-level journey from internet to final markdown, with parallel branches |
| 04 | [Configuration](04-Configuration.md) | Every tunable, its default, and the trade-off it controls |
| — | `DeepResearcher.canvas` | Obsidian canvas for a visual, draggable layout |

## Opening the canvas in Obsidian

1. Open Obsidian → **Open folder as vault** → select `DeepResearchermap` (or `Documents`).
2. In the file explorer, click `DeepResearcher.canvas`.
3. Drag nodes, zoom out, follow the coloured edges.

The canvas contains groups for each layer (Interface, Orchestration, Retrieval, Evidence, Synthesis, Frontend, Storage), a node per module, and labelled edges showing what data flows between them. Dashed edges are SSE events travelling to the browser.

## How to read this set

- **New to the codebase?** Start with `00-Overview.md`, then 01.
- **Want to change something?** 02 tells you which file owns it; 04 tells you which knob to turn.
- **Debugging a run?** 03 shows exactly where data lives at each stage.
- **Reviewing the design?** 01's "what LangGraph does not do" section is the honest bit.

## One-paragraph summary

A FastAPI app streams a LangGraph pipeline over SSE, with model calls paced against free-tier provider quotas. The pipeline plans searches, runs them in parallel through SerpApi, then **downloads and parses the real documents** (PDF via pypdf, arXiv via its API, GitHub via API + README, HTML via lxml), chunks them on their own structure with provenance, scores relevance deterministically, extracts evidence through batched LLM calls whose quotes are verified against the source text, reconciles contradictions, and writes a section-wise report whose reference numbers are assigned before any model runs so every citation resolves to a real URL. Completion state (`completed` / `partial` / `failed`) is surfaced in the API, the UI, and the exported file.