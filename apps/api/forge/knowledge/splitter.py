"""Pluggable chunking strategies for knowledge ingestion.

Four strategies, all targeting ~chunk_size characters with overlap:

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
- ``semantic``: split where the *meaning* shifts. Embeds each sentence and cuts at
  the largest drops in sentence-to-sentence similarity (percentile break-points), so
  a chunk stays on one topic. Needs an ``embed_fn`` (the ingest pipeline injects the
  project embedder) - without one, or for very short text, it falls back to recursive.
  It is the only strategy that isn't purely lexical; the others need no model.

``chunk_text`` is the single dispatch entrypoint; ``split_text`` is kept as the
pure-Python recursive splitter (public API + the universal fallback).
"""

from __future__ import annotations

import re
from collections.abc import Callable

# A callable that embeds a batch of texts -> one vector (list of floats) each. Matches the
# Embedder.embed signature in knowledge/embeddings.py, so ingest can pass `embedder.embed`.
EmbedFn = Callable[[list[str]], list]

# Canonical strategy names (kept in sync with packages/schemas/forge/project.json
# rag_defaults.chunking_strategy and KbSourceCreate.chunking_strategy).
CHUNK_STRATEGIES = ("recursive", "section", "sentence", "semantic")
DEFAULT_STRATEGY = "recursive"

# Break sentences into a new chunk when their similarity drop is in the top (100-N)% of drops.
# 95 => cut only at the sharpest ~5% of topic shifts (few, clean boundaries).
_SEMANTIC_BREAKPOINT_PERCENTILE = 95.0

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
    text: str, *, strategy: str = DEFAULT_STRATEGY, chunk_size: int = 1000, overlap: int = 200,
    embed_fn: EmbedFn | None = None,
) -> list[str]:
    """Split ``text`` using the named strategy. Unknown/empty strategy -> recursive.

    ``embed_fn`` is only used by the ``semantic`` strategy (the ingest pipeline passes the
    project embedder). Every other strategy ignores it and stays fully offline/lexical.
    """
    text = (text or "").strip()
    if not text:
        return []
    strategy = (strategy or DEFAULT_STRATEGY).strip().lower()
    if strategy == "section":
        return _split_sections(text, chunk_size, overlap)
    if strategy == "sentence":
        return _split_sentences(text, chunk_size, overlap)
    if strategy == "semantic":
        return _split_semantic(text, chunk_size, overlap, embed_fn)
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


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolated percentile over an already-sorted list (no numpy dependency)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (rank - lo)


def _split_semantic(
    text: str, chunk_size: int, overlap: int, embed_fn: EmbedFn | None
) -> list[str]:
    """Split on meaning-drift: embed each sentence, then cut between sentences whose
    similarity drop is among the sharpest (strictly above the break-point percentile). A single
    topic stays in one chunk; a topic shift starts a new one. Segments larger than chunk_size are
    recursively sub-split. Falls back to recursive when there's no embedder, too few
    sentences to compare, or embedding fails - so it can never hard-fail an ingest."""
    if embed_fn is None:
        return _split_recursive(text, chunk_size, overlap)
    sentences = _split_into_sentences(text)
    if len(sentences) < 3:
        return _split_recursive(text, chunk_size, overlap)
    try:
        vectors = list(embed_fn(sentences))
    except Exception:  # noqa: BLE001 - embedder failure -> lexical fallback, never abort ingest
        return _split_recursive(text, chunk_size, overlap)
    if len(vectors) != len(sentences):
        return _split_recursive(text, chunk_size, overlap)

    from forge.knowledge.embeddings import cosine

    # Distance (1 - cosine) between each adjacent sentence pair; a large value = a topic shift.
    # Materialize each vector to a list ONCE (each is otherwise re-listed as both a left and a
    # right neighbor).
    vecs = [list(v) for v in vectors]
    dists = [1.0 - cosine(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
    threshold = _percentile(sorted(dists), _SEMANTIC_BREAKPOINT_PERCENTILE)

    segments: list[str] = []
    cur = [sentences[0]]
    for i in range(1, len(sentences)):
        if dists[i - 1] > threshold:  # sharp enough drop -> boundary before this sentence
            segments.append(" ".join(cur))
            cur = []
        cur.append(sentences[i])
    if cur:
        segments.append(" ".join(cur))

    chunks: list[str] = []
    for seg in segments:
        if len(seg) <= chunk_size:
            chunks.append(seg)
        else:
            chunks.extend(_split_recursive(seg, chunk_size, overlap))
    return [c for c in chunks if c.strip()]
