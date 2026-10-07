"""Structure-aware chunking with provenance.

Large documents are never truncated whole. They are split on their own logical
structure -- headings, paragraphs, list items, table rows, PDF pages -- so every
chunk knows which section of which source it came from and which page it sat on.

That provenance is the whole point: it is what lets a claim in the final report
be walked back to a specific passage of a specific document, rather than to an
opaque blob of text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from . import config

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# Unmarked headings in PDFs and plain text: "3.2 Attention", "4 Results",
# "Introduction". Deliberately conservative so ordinary sentences are not
# mistaken for headings.
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,3})[.)]?\s+([A-Z][^\n]{0,90})$")
_NAMED_HEADING = re.compile(
    r"^(Abstract|Introduction|Background|Related Work|Methodology|Methods|Results|"
    r"Discussion|Conclusion|Conclusions|References|Bibliography|Appendix|Evaluation|"
    r"Limitations|Threats to Validity|Acknowledg(?:e)?ments|Setup|Implementation|"
    r"Performance|Comparison|Use Cases|Architecture|Summary|Overview)$", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass
class Chunk:
    """One retrievable unit of source material, with its origin intact."""

    chunk_id: str
    source_id: str
    source_url: str
    source_title: str
    source_type: str = "web"
    publication_date: str = ""
    section: str = ""                 # heading path, e.g. "3.2 Attention"
    page: int = 0                     # 1-based for PDFs, else 0
    content: str = ""
    char_start: int = 0               # offset into the cleaned document
    retrieval_status: str = "full"    # full | partial | metadata_only
    retrieval_limitation: str = ""    # why it is not a clean full read
    source_publisher: str = ""
    authors: list[str] = field(default_factory=list)
    doi: str = ""
    entity: str = ""                  # knowledge-graph entity for this source
    entity_type: str = ""             # ... and its type (song, product, ...)
    cited_by: int = 0                 # Scholar citation count, when known

    @property
    def word_count(self) -> int:
        return len(self.content.split())

    def as_dict(self) -> dict:
        data = asdict(self)
        data["word_count"] = self.word_count
        return data

    def label(self) -> str:
        """Compact provenance string injected into prompts."""
        bits = [self.source_title or self.source_url]
        if self.section:
            bits.append(self.section)
        if self.page:
            bits.append(f"p.{self.page}")
        return " — ".join(bits)


def chunk_document(
    text: str,
    *,
    source_id: str,
    source_url: str,
    source_title: str,
    source_type: str = "web",
    publication_date: str = "",
    retrieval_status: str = "full",
    retrieval_limitation: str = "",
    source_publisher: str = "",
    authors: list[str] | None = None,
    doi: str = "",
    pages: int = 0,
    entity: str = "",
    entity_type: str = "",
    cited_by: int = 0,
) -> list[Chunk]:
    """Split one document into provenance-carrying chunks.

    ``pages`` > 0 marks the source as a PDF: page boundaries are honoured and
    per-chunk page numbers are recorded.
    """
    if not text or not text.strip():
        return []

    target = config.chunk_target_chars()
    maximum = config.chunk_max_chars()
    blocks = _blocks(text, paginated=pages > 0)

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_len = 0
    section = ""
    page = 1 if pages > 0 else 0        # page numbers are meaningless for HTML/markdown
    char_start = 0

    def flush() -> None:
        nonlocal buffer, buffer_len, char_start
        if not buffer:
            return
        content = "\n\n".join(buffer).strip()
        if content:
            chunks.append(Chunk(
                chunk_id=f"{source_id}#{len(chunks)}",
                source_id=source_id,
                source_url=source_url,
                source_title=source_title,
                source_type=source_type,
                publication_date=publication_date,
                section=section,
                page=page,
                content=content,
                char_start=char_start,
                retrieval_status=retrieval_status,
                retrieval_limitation=retrieval_limitation,
                source_publisher=source_publisher,
                authors=list(authors or []),
                doi=doi,
                entity=entity,
                entity_type=entity_type,
                cited_by=cited_by,
            ))
        buffer, buffer_len = [], 0
        char_start += len(content) + 2

    for block_section, block_page, block_text, offset in blocks:
        if buffer and (section, page) != (block_section, block_page):
            flush()

        section, page = block_section, block_page

        # A block larger than the hard cap is split on sentence boundaries
        # rather than dropped or blindly sliced mid-word.
        if len(block_text) > maximum:
            flush()
            for piece in _split_long(block_text, maximum):
                buffer, buffer_len = [piece], len(piece)
                flush()
            continue

        if buffer_len + len(block_text) > target and buffer:
            flush()

        if not buffer:
            char_start = offset

        buffer.append(block_text)
        buffer_len += len(block_text) + 2

    flush()

    # Keep this document from crowding out the rest of the corpus.
    limit = config.chunks_per_source()
    if len(chunks) > limit:
        chunks = _spread(chunks, limit)
    return chunks


def _spread(chunks: list[Chunk], limit: int) -> list[Chunk]:
    """Evenly sample when over budget, so early bias does not eat the tail."""
    if limit <= 0:
        return []
    step = len(chunks) / limit
    picked = [chunks[int(i * step)] for i in range(limit)]
    for index, chunk in enumerate(picked):
        chunk.chunk_id = f"{chunk.source_id}#{index}"
    return picked


def _blocks(text: str, paginated: bool) -> list[tuple[str, int, str, int]]:
    """Split text into (section_path, page, block_text, char_offset) tuples."""
    blocks: list[tuple[str, int, str, int]] = []
    headings: list[str] = []
    buffer: list[str] = []
    page = 1 if paginated else 0       # page 0 means "not a paginated document"
    offset = 0

    def path() -> str:
        return " > ".join(h for h in headings if h)[:220]

    def emit() -> None:
        nonlocal buffer, offset
        body = "\n\n".join(buffer).strip()
        if body:
            blocks.append((path(), page, body, offset))
            offset += len(body) + 2
        buffer = []

    lines = text.split("\n")
    cursor = 0
    for line in lines:
        cursor += len(line) + 1

        if paginated and "\f" in line:
            for part in line.split("\f"):
                if part.strip():
                    buffer.append(part.strip())
            page += 1
            emit()
            continue

        heading = _heading_of(line)
        if heading is not None:
            level, title = heading
            emit()
            del headings[level - 1:]
            while len(headings) < level - 1:
                headings.append("")
            headings.append(title)
            headings = headings[:level]
            continue

        if not line.strip():
            continue
        buffer.append(line.strip())

    emit()
    return blocks


def _heading_of(line: str) -> tuple[int, str] | None:
    """(level, title) if this line is a heading, else None."""
    stripped = line.strip()
    if not stripped:
        return None

    match = _HEADING.match(stripped)
    if match:
        return len(match.group(1)), match.group(2)

    # PDF and plain text carry no markup, so fall back to shape: a short line
    # that is either a known section name or a numbered heading.
    if len(stripped) > 92 or not stripped:
        return None
    if _NAMED_HEADING.match(stripped) and len(stripped.split()) <= 6:
        return 2, stripped
    numbered = _NUMBERED_HEADING.match(stripped)
    if numbered and not numbered.group(2).endswith("."):
        level = numbered.group(1).count(".") + 1
        return min(level, 6), numbered.group(2).strip()
    return None


def _split_long(text: str, maximum: int) -> list[str]:
    """Sentence-aware fallback for a single oversized block."""
    sentences = _SENTENCE_SPLIT.split(text)
    pieces, current = [], ""
    for sentence in sentences:
        while len(sentence) > maximum:              # a single monster sentence
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:maximum])
            sentence = sentence[maximum:]
        if current and len(current) + len(sentence) + 1 > maximum:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return [p for p in pieces if p.strip()]