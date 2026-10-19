"""Chunker (FR-7). Pure: text in, chunks out.

- Documents are split by PAGE first (chunks never span pages), then by PARAGRAPH
  (blank-line separated). Paragraphs are packed greedily into chunks of about
  `TARGET_TOKENS` (500). A new chunk starts with the last ~`OVERLAP_TOKENS` (50) of
  the previous one, so a sentence cut at a boundary is still searchable whole. A
  paragraph longer than the target is split at sentence ends, and a sentence longer
  than the target into word windows (with the same overlap).
- CSVs are chunked by ROW GROUPS: the header line is repeated at the top of every
  chunk and rows are added while the chunk stays within the target. `page` is None.
- Tokens are estimated as words plus punctuation marks (`\\w+|[^\\w\\s]`), which is
  close to the WordPiece count of bge-small (512-token window) for this kind of text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_TOKENS = 500
OVERLAP_TOKENS = 50
_TOKEN = re.compile(r"\w+|[^\w\s]")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    page: int | None
    chunk_index: int
    text: str


def estimate_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


def _tail(text: str, tokens: int) -> str:
    """The last words of `text` amounting to about `tokens` tokens."""
    if tokens <= 0:
        return ""
    words = text.split()
    out: list[str] = []
    n = 0
    for w in reversed(words):
        out.append(w)
        n += estimate_tokens(w)
        if n >= tokens:
            break
    return " ".join(reversed(out))


def _word_windows(text: str, target: int, overlap: int) -> list[str]:
    words = text.split()
    pieces, start = [], 0
    while start < len(words):
        n, end = 0, start
        while end < len(words) and (n + estimate_tokens(words[end]) <= target or end == start):
            n += estimate_tokens(words[end])
            end += 1
        pieces.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        back, k = 0, end
        while k > start + 1 and back < overlap:
            k -= 1
            back += estimate_tokens(words[k])
        start = k if k > start else end
    return pieces


def _pieces(paragraph: str, target: int, overlap: int) -> list[str]:
    """Paragraph -> pieces no longer than `target` tokens."""
    if estimate_tokens(paragraph) <= target:
        return [paragraph]
    out: list[str] = []
    for sentence in _SENTENCE_END.split(paragraph):
        sentence = sentence.strip()
        if not sentence:
            continue
        if estimate_tokens(sentence) <= target:
            out.append(sentence)
        else:
            out.extend(_word_windows(sentence, target, overlap))
    return out


def chunk_page_text(text: str, *, target: int = TARGET_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[str]:
    """One page -> chunk texts."""
    paragraphs = [p.strip() for p in _PARAGRAPH_BREAK.split(text) if p.strip()]
    pieces = [piece for p in paragraphs for piece in _pieces(p, target, overlap)]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for piece in pieces:
        n = estimate_tokens(piece)
        if current and size + n > target:
            chunks.append("\n\n".join(current))
            tail = _tail(chunks[-1], overlap)
            current, size = ([tail], estimate_tokens(tail)) if tail else ([], 0)
            if size + n > target:  # the overlap would push this piece over the target
                current, size = [], 0
        current.append(piece)
        size += n
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_pages(pages: list[str], *, target: int = TARGET_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[Chunk]:
    """Pages (1-based in order) -> chunks with page numbers. Empty pages give none."""
    out: list[Chunk] = []
    for page_no, text in enumerate(pages, start=1):
        for t in chunk_page_text(text or "", target=target, overlap=overlap):
            out.append(Chunk(page=page_no, chunk_index=len(out), text=t))
    return out


def chunk_csv(text: str, *, target: int = TARGET_TOKENS) -> list[Chunk]:
    """CSV -> row groups, each starting with the header line. `page` is None."""
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return []
    header, rows = lines[0], lines[1:]
    if not rows:
        return [Chunk(page=None, chunk_index=0, text=header)]
    out: list[Chunk] = []
    group: list[str] = []
    size = estimate_tokens(header)
    for row in rows:
        n = estimate_tokens(row)
        if group and size + n > target:
            out.append(Chunk(page=None, chunk_index=len(out), text="\n".join([header, *group])))
            group, size = [], estimate_tokens(header)
        group.append(row)
        size += n
    out.append(Chunk(page=None, chunk_index=len(out), text="\n".join([header, *group])))
    return out


def chunk_document(text_content: str | None, mime_type: str, transcribed_pages: list[str] | None = None, *,
                   target: int = TARGET_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[Chunk]:
    """Chunks for a document. Pages come from `documents.text_content` (pages joined
    with "\\f"), or from the extractor's transcription when there is no text."""
    if mime_type == "text/csv":
        return chunk_csv(text_content or "", target=target)
    if text_content and text_content.strip():
        pages = text_content.split("\f")
    else:
        pages = list(transcribed_pages or [])
    return chunk_pages(pages, target=target, overlap=overlap)
