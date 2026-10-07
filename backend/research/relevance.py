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
from datetime import date

from . import config
from .chunker import Chunk

_THIS_YEAR = date.today().year

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

# Knowledge-graph types that are almost never research material for technical
# questions: a passage carrying an entity typed as one of these is the wrong
# "Attention" (song, film, product), not the mechanism being researched.
_WRONG_ENTITY_TYPES = ("song", "film", "movie", "tv series", "video game",
                       "product", "album", "novel")

# Question words signalling that recent evidence outranks older evidence.
_RECENCY_TERMS = frozenset({"latest", "recent", "current", "update", "updated",
                            "now", "today", "newest"})


def _publication_year(value: str | None) -> int | None:
    match = re.search(r"(19|20)\d{2}", str(value or ""))
    return int(match.group()) if match else None


def uncovered_questions(bank: list[str], records, limit: int = 5) -> list[str]:
    """Related questions with little overlap against extracted evidence.

    The bank comes free with every web search (People Also Ask / related
    searches). Anything already covered by findings is dropped, so recovery
    targets genuine gaps instead of re-asking answered questions.
    """
    covered: set[str] = set()
    for record in records or []:
        covered |= set(terms(f"{record.claim} {record.detail} "
                             f"{record.dimension}"))
    out: list[str] = []
    for question in bank or []:
        words = set(terms(question))
        if not words or question in out:
            continue
        if len(words & covered) / len(words) < 0.4:
            out.append(question)
        if len(out) >= limit:
            break
    return out


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


def dimensions(question: str, limit: int = 16) -> list[str]:
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


def target_variant_mismatch(record_targets: list[str] | None, claim: str,
                              question_targets: list[str] | None) -> str | None:
    """Detect evidence about a related-but-different variant of the target.

    Returns a human-readable reason, or None when the evidence looks directly
    applicable. Two deterministic rules, both domain-agnostic:

    * version digits: when both sides name digit-bearing variants (7B vs
      Large 2, 2.5 vs 3) and the sets are disjoint, the evidence is about a
      different release than the one under investigation;
    * architecture class: mixture-of-experts evidence for a dense-model
      question (or the reverse) is background, not a direct finding.

    Generic background without specific variants never mismatches.
    """
    if not question_targets:
        return None
    rtext = " ".join([*(record_targets or []), claim or ""]).lower()
    qtext = " ".join(question_targets).lower()

    def version_sig(text: str) -> set[str]:
        # Years are dates, not versions. Bare numbers are ignored. Tokens
        # shaped like measurements (12kg, 9.8m/s) are ignored, while version
        # tokens (7b, 2.5, qwen3) count -- including joined pairs so
        # "Large 2" and "Large2" produce the same signature.
        words = re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)?", text)
        words = [w for w in words if not re.fullmatch(r"(19|20)\d{2}", w)]
        sigs: set[str] = set()
        for word in words:
            if re.fullmatch(r"\d+[a-z]{2,}", word):
                continue                            # measurement, e.g. 12kg
            if re.search(r"\d", word):
                sigs.add(word)
        for first, second in zip(words, words[1:]):
            if re.fullmatch(r"\d{1,2}", second) and re.fullmatch(r"[a-z]+", first):
                sigs.add(first + second)            # e.g. large + 2
        return sigs

    rsigs, qsigs = version_sig(rtext), version_sig(qtext)
    if rsigs and qsigs and rsigs.isdisjoint(qsigs):
        return ("about a related variant, not the exact target under "
                "investigation")

    def has_moe(text: str) -> bool:
        return "moe" in text or "mixture" in text

    if (has_moe(rtext) and "dense" in qtext and not has_moe(qtext)) or \
       ("dense" in rtext and has_moe(qtext) and "dense" not in qtext):
        return ("about a different architecture class than the target "
                "under investigation")
    return None


def search_keywords(question: str, subquestion: str = "",
                    limit: int = 8) -> str:
    """Keyword query for external search: content words only.

    Topic terms shared with the main question come first, then the
    sub-question's own distinctive terms. Basic English words never appear:
    ``terms()`` already strips stopwords and short noise, so a query reads
    like "mistral large throughput" instead of a full sentence.
    """
    main = list(dict.fromkeys(terms(question or "")))
    if not (subquestion or "").strip():
        return " ".join(main[:limit])
    sub = list(dict.fromkeys(terms(subquestion)))
    main_set = set(main)
    shared = [w for w in sub if w in main_set]
    extra = [w for w in sub if w not in main_set]
    picked = (shared + extra)[:limit]
    if not picked:
        picked = main[:limit]
    return " ".join(picked)


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
    # Full entity phrases ("acme widget pro") discriminate far better than their
    # individual words: a song titled "Attention" shares one generic term with
    # an attention-mechanism question but never the full entity phrase.
    target_phrases = [t.lower() for t in targets(question) if len(t.split()) > 1]

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

        lowered = chunk.content.lower()
        if any(phrase in lowered for phrase in target_phrases):
            score *= 1.4
            reasons.append("names a compared entity")

        if len(overlap) == 1 and len(query_terms) > 3:
            # One shared generic term ("attention" in a song title) is how
            # irrelevant results slip in beside technically relevant ones.
            score *= 0.5
            reasons.append("matches only one question term")

        if _NUMBER.search(chunk.content):
            score *= 1.2
            reasons.append("contains quantitative claims")

        head = chunk.content[:200]
        if _BOILERPLATE.search(head):
            score *= 0.25
            reasons.append("possible boilerplate")

        if chunk.retrieval_status == "partial":
            score *= 0.9

        if chunk.retrieval_status == "metadata_only":
            # A snippet is not a document: rank it below anything actually read
            # so broader searches convert into usable evidence, not more stubs.
            score *= 0.55
        elif chunk.retrieval_status == "full":
            score *= 1.08

        entity_type = (chunk.entity_type or "").lower()
        if entity_type and any(wrong in entity_type for wrong in _WRONG_ENTITY_TYPES):
            # The search engine itself typed this source's entity as media, not
            # material: downrank hard so song lyrics never outrank benchmarks.
            score *= 0.3
            reasons.append(f"entity typed as {chunk.entity_type}")

        if (query_terms & _RECENCY_TERMS
                or str(_THIS_YEAR) in question or str(_THIS_YEAR - 1) in question):
            # "latest"/"recent" questions must not be answered with old pages.
            year = _publication_year(chunk.publication_date)
            if year is not None and year >= _THIS_YEAR - 1:
                score *= 1.15
                reasons.append("recently published")

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