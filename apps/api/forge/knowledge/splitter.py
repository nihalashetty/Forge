"""Pluggable chunking strategies for knowledge ingestion.

Three strategies, all targeting ~chunk_size characters with overlap and all fully
offline (no network, no model download):

- ``recursive`` (default): LangChain's RecursiveCharacterTextSplitter when the
  ``knowledge`` extra is installed, falling back to the dependency-free recursive
  character splitter below. Best general-purpose choice.
- ``section``: split on Markdown headers / heading lines so each chunk is a whole
  section. Meaningful when the document is well structured (docs, wikis, crawled
  pages which are concatenated under ``# {url}`` headers). Oversized sections are
  recursively sub-split so a chunk never blows past chunk_size.
- ``sentence``: split on sentence boundaries (abbreviation-aware regex) then pack
  sentences into chunks with sentence-level overlap. Meaningful when meaning lives
  at the sentence level (FAQs, transcripts, prose).

``chunk_text`` is the single dispatch entrypoint; ``split_text`` is kept as the
pure-Python recursive splitter (public API + the universal fallback).
"""

from __future__ import annotations

import re

# Canonical strategy names (kept in sync with packages/schemas/forge/project.json
# rag_defaults.chunking_strategy and KbSourceCreate.chunking_strategy).
CHUNK_STRATEGIES = ("recursive", "section", "sentence")
DEFAULT_STRATEGY = "recursive"

_SEPARATORS = ["\n\n", "\n", ". ", " "]

# Markdown ATX headers (# .. ######) anchor section boundaries.
_HEADER_RE = re.compile(r"^#{1,6}[ \t]+\S.*$", re.MULTILINE)

# Sentence boundary: end punctuation followed by whitespace then an opener
# (capital letter, digit, or quote/paren). Lookarounds keep the delimiter attached
# to the left sentence.
_SENT_BOUNDARY = re.compile(r'(?<=[.!?])["\')\]]*\s+(?=[A-Z0-9"\'(\[])')

# Common abbreviations whose trailing period is NOT a sentence end. Compared
# lowercased with the trailing dot stripped.
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg", "ie",
    "e.g", "i.e", "inc", "ltd", "co", "corp", "no", "fig", "al", "approx", "dept",
    "est", "u.s", "u.k", "a.m", "p.m", "vol", "pp",
})


def chunk_text(
    text: str, *, strategy: str = DEFAULT_STRATEGY, chunk_size: int = 1000, overlap: int = 200
) -> list[str]:
    """Split ``text`` using the named strategy. Unknown/empty strategy -> recursive."""
    text = (text or "").strip()
    if not text:
        return []
    strategy = (strategy or DEFAULT_STRATEGY).strip().lower()
    if strategy == "section":
        return _split_sections(text, chunk_size, overlap)
    if strategy == "sentence":
        return _split_sentences(text, chunk_size, overlap)
    return _split_recursive(text, chunk_size, overlap)


def split_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Dependency-free recursive character splitter (~chunk_size with overlap).

    Kept as a stable public API and as the universal fallback for every strategy.
    """
    text = (text or "").strip()
    if len(text) <= chunk_size:
        return [text] if text else []

    # Find the best separator that produces pieces, then greedily pack into chunks.
    pieces = _split_on_separators(text, _SEPARATORS, chunk_size)
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if len(cur) + len(p) + 1 <= chunk_size:
            cur = f"{cur} {p}".strip() if cur else p
        else:
            if cur:
                chunks.append(cur)
            # carry overlap from the tail of the previous chunk
            tail = cur[-overlap:] if overlap and cur else ""
            cur = (f"{tail} {p}").strip() if tail else p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def _split_on_separators(text: str, seps: list[str], chunk_size: int) -> list[str]:
    if len(text) <= chunk_size or not seps:
        return [text]
    sep = seps[0]
    parts = text.split(sep) if sep in text else [text]
    out: list[str] = []
    for part in parts:
        if len(part) <= chunk_size:
            out.append(part)
        else:
            out.extend(_split_on_separators(part, seps[1:], chunk_size))
    return out


def _split_recursive(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Recursive-character strategy: prefer LangChain's splitter, else fall back."""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=min(overlap, max(chunk_size - 1, 0))
        )
        chunks = [c.strip() for c in splitter.split_text(text) if c and c.strip()]
        if chunks:
            return chunks
    except Exception:  # noqa: BLE001 - knowledge extra absent / splitter error -> fallback
        pass
    return split_text(text, chunk_size, overlap)


def _split_sections(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Each Markdown section (header + body up to the next header) becomes a chunk;
    oversized sections are recursively sub-split. Falls back to recursive when the
    document has no headers to key off."""
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return _split_recursive(text, chunk_size, overlap)

    sections: list[str] = []
    # Any preamble before the first header is its own section.
    if matches[0].start() > 0:
        pre = text[: matches[0].start()].strip()
        if pre:
            sections.append(pre)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sec = text[m.start():end].strip()
        if sec:
            sections.append(sec)

    chunks: list[str] = []
    for sec in sections:
        if len(sec) <= chunk_size:
            chunks.append(sec)
        else:
            chunks.extend(_split_recursive(sec, chunk_size, overlap))
    return [c for c in chunks if c.strip()]


def _split_into_sentences(text: str) -> list[str]:
    raw = _SENT_BOUNDARY.split(text)
    sentences: list[str] = []
    for piece in raw:
        s = piece.strip()
        if not s:
            continue
        # Re-merge if the previous sentence ended on a known abbreviation (the
        # boundary regex split too eagerly after e.g. "Dr." or "U.S.").
        if sentences:
            last_word = re.split(r"\s+", sentences[-1])[-1].rstrip(".").lower()
            if last_word in _ABBREVIATIONS:
                sentences[-1] = f"{sentences[-1]} {s}"
                continue
        sentences.append(s)
    return sentences


def _split_sentences(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Pack whole sentences into ~chunk_size chunks, carrying trailing sentences
    forward as overlap. A single over-long sentence is recursively sub-split."""
    sentences = _split_into_sentences(text)
    if not sentences:
        return _split_recursive(text, chunk_size, overlap)

    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for s in sentences:
        if len(s) > chunk_size:
            if cur:
                chunks.append(" ".join(cur))
                cur, cur_len = [], 0
            chunks.extend(_split_recursive(s, chunk_size, overlap))
            continue
        if cur and cur_len + len(s) + 1 > chunk_size:
            chunks.append(" ".join(cur))
            # Sentence-level overlap: carry the trailing sentences up to ~overlap chars.
            carry: list[str] = []
            carry_len = 0
            for prev in reversed(cur):
                if carry_len + len(prev) + 1 > overlap:
                    break
                carry.insert(0, prev)
                carry_len += len(prev) + 1
            cur, cur_len = carry, carry_len
        cur.append(s)
        cur_len += len(s) + 1
    if cur:
        chunks.append(" ".join(cur))
    return [c for c in chunks if c.strip()]
