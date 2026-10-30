"""Data types shared across the RAG pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Document(BaseModel):
    """A whole markdown file from the knowledge base."""

    id: str
    title: str
    kind: str  # runbook | adr | postmortem | service
    service: str
    owner: str = ""
    updated: str = ""
    tags: list[str] = Field(default_factory=list)
    text: str
    path: str = ""


class Chunk(BaseModel):
    """A retrievable slice of a document. ``id`` is ``<doc_id>#<n>``."""

    id: str
    doc_id: str
    index: int
    heading: str
    text: str
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def citation(self) -> str:
        return f"[{self.id}]"


class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float
    sources: list[str] = Field(default_factory=list)  # which retrievers found it: bm25 / vector

    def render(self) -> str:
        """Compact text block handed to the LLM as evidence."""
        meta = self.chunk.metadata
        header = f"{self.chunk.citation} {meta.get('title', '')} — {self.chunk.heading}".strip()
        return f"{header}\n{self.chunk.text}"
