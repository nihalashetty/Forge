"""A small recursive character splitter (~chunk_size with overlap). No heavy deps."""

from __future__ import annotations

_SEPARATORS = ["\n\n", "\n", ". ", " "]


def split_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    text = (text or "").strip()
    if len(text) <= chunk_size:
        return [text] if text else []

    # Find the best separator that produces pieces, then greedily pack into chunks.
    pieces = _split_recursive(text, _SEPARATORS, chunk_size)
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


def _split_recursive(text: str, seps: list[str], chunk_size: int) -> list[str]:
    if len(text) <= chunk_size or not seps:
        return [text]
    sep = seps[0]
    parts = text.split(sep) if sep in text else [text]
    out: list[str] = []
    for part in parts:
        if len(part) <= chunk_size:
            out.append(part)
        else:
            out.extend(_split_recursive(part, seps[1:], chunk_size))
    return out
