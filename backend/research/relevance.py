"""Deterministic relevance scoring and dimension extraction.

None of this costs an LLM call. Deciding which chunks are about the question is
arithmetic over term overlap and IDF weights, and extracting the comparison
dimensions ("performance, app size, hiring demand") is string work. Spending
model calls on either would be waste.

Relevance scoring is what keeps the final report honest about *why* a passage
was included: the score is computed from the question, not chosen by a model
that might be tempted by a confident-sounding passage.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from . import config
from .chunker import Chunk

_STOPWORDS = frozenset("""
a about above after again against all am an and any are aren as at be because been
before being below between both but by can cannot could couldn did didn do does
doesn doing don down during each few for from further had hadn has hasn have haven
having he her here hers herself him himself his how i if in into is isn it its
itself just me more most mustn my myself no nor not of off on once only or other
ought our ours ourselves out over own same shan she should shouldn so some such than
that the their theirs them themselves then there these they this those through to too
under until up very was wasn we were weren what when where which while who whom why
with won would wouldn you your yours yourself yourselves
""".split())

# Phrases that mark a passage as navigation or boilerplate rather than content.
_BOILERPLATE = re.compile(
    r"^\s*(cookie|privacy policy|terms of service|all rights reserved|"
    r"skip to (main )?content|subscribe|sign in|log in|advertisement|"
    r"related articles?|table of contents)\b", re.I)

# A quantitative claim is worth more than an opinion, so numeric density counts.
# Domain-agnostic by construction: a percentage, a decimal, a number carrying a
# short unit, or a large magnitude. Deliberately not a list of specific units,
# which would only ever fire for one field.
_NUMBER = re.compile(
    r"\d+(?:[.,]\d+)?\s*%"          # a percentage
    r"|\b\d+(?:[.,]\d+)+\b"         # a decimal or a comma-grouped figure
    r"|\b\d+\s*[a-z]{1,4}\b"        # a number carrying a short unit
    r"|\b\d{4,}\b"                  # a large magnitude
)

_SPLIT_DIMENSIONS = re.compile(r",|\band\b|\bplus\b|;|/")


@dataclass
class Scored:
    chunk: Chunk
    score: float
    reasons: list[str]


def terms(text: str) -> list[str]:
    """Lowercase content words, minus stopwords and 1-2 letter noise."""
    words = re.findall(r"[a-z0-9][a-z0-9+#.\-]*", (text or "").lower())
    return [w.strip(".-") for w in words
            if len(w) > 2 and w not in _STOPWORDS and not w.isdigit()]


_ANALYSE_CLAUSE = re.compile(
    r"\b(?:analy[sz]e|consider|covering|address|examine|covering|"
    r"looking at|with respect to|in terms of)\b(.*)", re.I | re.S)


def targets(question: str, limit: int = 8) -> list[str]:
    """The entities the question asks to be compared."""
    text = _strip_question_wrapper(question or "")
    out: list[str] = []
    for part in _SPLIT_DIMENSIONS.split(text):
        part = re.sub(r"\b(development|framework|platform|approach|option)s?\b", "",
                      part.strip(" .:;-"), flags=re.I).strip()
        words = part.split()
        if not words or len(words) > 5:
            continue
        if len(part) < 2:
            continue
        if part.lower() in {t.lower() for t in out}:
            continue
        out.append(part)
        if len(out) >= limit:
            break
    return out


def dimensions(question: str, limit: int = 10) -> list[str]:
    """The aspects to analyse: "performance, app size, hiring demand".

    These decide which sections the final report needs, so they are read from
    the sentence that actually asks for analysis rather than from the whole
    prompt, which would also return the comparison targets.
    """
    found: list[str] = []
    for match in _ANALYSE_CLAUSE.finditer(question or ""):
        clause = match.group(1)
        # Stop at the first instruction about how to present the answer.
        clause = re.split(r"\.\s|\buse\b|\bgive\b|\bprovide\b|\bcite\b",
                          clause, maxsplit=1, flags=re.I)[0]
        for part in _SPLIT_DIMENSIONS.split(clause):
            part = part.strip(" .:;-")
            words = part.split()
            if not words or len(words) > 5:
                continue
            cleaned = " ".join(w for w in words if w.lower() not in _STOPWORDS)
            if len(cleaned) < 3:
                continue
            if cleaned.lower() in {d.lower() for d in found}:
                continue
            found.append(cleaned)
            if len(found) >= limit:
                return found
    return found


def _strip_question_wrapper(question: str) -> str:
    """Pull the comparison targets out of 'compare A, B and C for D'.

    The purpose clause is cut at a *standalone* preposition only. Matching on
    word boundaries alone would split compound terms, because a hyphen is a
    non-word character: "stream-of-consciousness" would lose everything after
    the "of". Requiring surrounding spaces keeps such names intact.
    """
    text = question.strip()
    text = re.sub(r"^\s*(please\s+)?(compare|contrast|analyse|analyze|research|"
                  r"investigate|evaluate|explore|what is|how do|explain)\b",
                  "", text, flags=re.I).strip(" :?.-")
    if len(text.split()) > 12:
        # "for", "to", "in", "on", "under", "within", "during", "when", "while"
        # -- but never "of", which usually belongs to a compound noun.
        text = re.sub(r" (?:for|to|in|on|under|within|during|when|while)\b.*$", "",
                      text, flags=re.I)
        text = re.sub(r"^\s*(?:the\s+)?[\w\s]*?\beffects?\s+of\s+", "", text, flags=re.I)
    return text or question


def rank(chunks: list[Chunk], question: str, plan: dict | None = None,
         top_k: int | None = None) -> list[Scored]:
    """Score every chunk against the question; return the best ones."""
    if not chunks:
        return []

    dims = dimensions(question)
    subquestions = [sq.get("question", "") for sq in (plan or {}).get("subquestions", [])]
    query_terms = set(terms(question)) | {t for q in subquestions for t in terms(q)}
    dim_terms = {d: set(terms(d)) for d in dims}
    for dim in dim_terms.values():
        query_terms |= dim

    if not query_terms:
        return [Scored(c, 0.0, ["no query terms"]) for c in chunks]

    # IDF over this corpus: a term in every chunk carries no signal.
    doc_freq = Counter()
    chunk_terms = []
    for chunk in chunks:
        bag = set(terms(chunk.content)) | set(terms(chunk.section))
        chunk_terms.append(bag)
        doc_freq.update(bag)
    total = len(chunks)

    def idf(term: str) -> float:
        return math.log(1 + total / (1 + doc_freq.get(term, 0)))

    scored: list[Scored] = []
    for chunk, bag in zip(chunks, chunk_terms):
        if not bag:
            continue
        overlap = query_terms & bag
        if not overlap:
            continue

        score = sum(idf(t) for t in overlap) / math.sqrt(len(overlap) + 1)
        reasons = [f"query terms: {', '.join(sorted(overlap)[:6])}"]

        # Density matters more than raw count for long passages.
        density = len(overlap) / max(1, len(bag)) ** 0.5
        score *= 1 + min(density, 0.5)

        for dim, dterms in dim_terms.items():
            hits = dterms & bag
            if hits and len(hits) == len(dterms):
                score *= 1.35
                reasons.append(f"covers dimension '{dim}'")
                break

        if _NUMBER.search(chunk.content):
            score *= 1.2
            reasons.append("contains quantitative claims")

        head = chunk.content[:200]
        if _BOILERPLATE.search(head):
            score *= 0.25
            reasons.append("possible boilerplate")

        if chunk.retrieval_status == "partial":
            score *= 0.9

        scored.append(Scored(chunk, score, reasons))

    scored.sort(key=lambda s: (-s.score, s.chunk.chunk_id))

    limit = top_k if top_k is not None else config.relevance_top_chunks()
    floor = config.relevance_min_score()
    kept = [s for s in scored[:limit] if s.score >= floor]

    if not kept and scored:
        kept = scored[: min(limit, 4)]        # never return nothing at all
    return kept


def diversify(scored: list[Scored], per_source_cap: int | None = None) -> list[Scored]:
    """Round-robin across sources so one verbose document cannot fill the batch."""
    cap = per_source_cap if per_source_cap is not None else config.chunks_per_source()
    buckets: dict[str, list[Scored]] = {}
    for item in scored:
        buckets.setdefault(item.chunk.source_id, []).append(item)

    for items in buckets.values():
        items.sort(key=lambda s: -s.score)

    out: list[Scored] = []
    while buckets:
        for source_id in list(buckets):
            items = buckets[source_id]
            if not items or len([o for o in out if o.chunk.source_id == source_id]) >= cap:
                buckets.pop(source_id, None)
                continue
            out.append(items.pop(0))
            if not items:
                buckets.pop(source_id, None)
    return out


def source_context(question: str, plan: dict | None = None) -> str:
    """Compact terms blob handed to the planner/extractor prompts."""
    dims = dimensions(question)
    subs = [sq.get("question", "") for sq in (plan or {}).get("subquestions", [])]
    lines = [f"RESEARCH QUESTION: {question}"]
    if dims:
        lines.append("DIMENSIONS TO COVER: " + "; ".join(dims))
    if subs:
        lines.append("SUB-QUESTIONS:\n" + "\n".join(f"- {s}" for s in subs))
    return "\n".join(lines)