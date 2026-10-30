"""Heading-aware chunking.

Why not fixed-size windows? Runbooks are structured: "Symptoms", "Diagnosis",
"Mitigation", "Escalation". A chunk that straddles two sections is confusing to both
the embedder and the LLM. So we:

1. split the document on markdown headings (``#``..``###``),
2. within a section, pack paragraphs greedily up to ``chunk_size`` (approximate
   tokens), splitting oversized paragraphs by sentence,
3. carry an ``overlap`` tail from the previous chunk so facts that sit on a boundary
   are retrievable from either side,
4. prefix each chunk with its heading path (``Title > Section``) so a chunk is
   self-describing when read in isolation — this measurably improves retrieval.
"""

from __future__ import annotations

import re

from .models import Chunk, Document

_HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def approx_tokens(text: str) -> int:
    """Cheap token estimate (~1.3 tokens per word for English technical text)."""
    return int(len(text.split()) * 1.3) + 1


def _sections(doc: Document) -> list[tuple[str, str]]:
    """Return ``[(heading, body), ...]``; the preamble before the first heading uses the title."""
    text = doc.text
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [(doc.title, text.strip())]
    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        pre = text[: matches[0].start()].strip()
        if pre:
            sections.append((doc.title, pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        heading = m.group(2).strip()
        if body:
            sections.append((heading, body))
    return sections


def _pack(units: list[str], size: int) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for u in units:
        t = approx_tokens(u)
        if buf and buf_tokens + t > size:
            out.append("\n\n".join(buf))
            buf, buf_tokens = [], 0
        buf.append(u)
        buf_tokens += t
    if buf:
        out.append("\n\n".join(buf))
    return out


def _split_oversized(paragraph: str, size: int) -> list[str]:
    if approx_tokens(paragraph) <= size:
        return [paragraph]
    sentences = _SENTENCE.split(paragraph)
    return _pack(sentences, size)


def _tail(text: str, overlap_tokens: int) -> str:
    words = text.split()
    n = int(overlap_tokens / 1.3)
    return " ".join(words[-n:]) if n > 0 and words else ""


def chunk_document(doc: Document, chunk_size: int = 350, overlap: int = 50) -> list[Chunk]:
    chunks: list[Chunk] = []
    prev_tail = ""
    for heading, body in _sections(doc):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        units: list[str] = []
        for p in paragraphs:
            units.extend(_split_oversized(p, chunk_size))
        for piece in _pack(units, chunk_size):
            prefix = f"{doc.title} > {heading}\n" if heading != doc.title else f"{doc.title}\n"
            text = prefix + (prev_tail + "\n" if prev_tail else "") + piece
            idx = len(chunks)
            chunks.append(
                Chunk(
                    id=f"{doc.id}#{idx}",
                    doc_id=doc.id,
                    index=idx,
                    heading=heading,
                    text=text,
                    metadata={
                        "title": doc.title,
                        "kind": doc.kind,
                        "service": doc.service,
                        "owner": doc.owner,
                        "updated": doc.updated,
                        "heading": heading,
                        "tags": ",".join(doc.tags),
                    },
                )
            )
            prev_tail = _tail(piece, overlap)
    return chunks


def chunk_documents(docs: list[Document], chunk_size: int = 350, overlap: int = 50) -> list[Chunk]:
    out: list[Chunk] = []
    for d in docs:
        out.extend(chunk_document(d, chunk_size, overlap))
    return out
