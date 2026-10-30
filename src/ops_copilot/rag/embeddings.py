"""Embedding providers.

An embedding turns text into a vector so that "payments are slow" and "p99 latency
spike on payments-api" land close together even though they share no words.

Three interchangeable providers (``OPS_COPILOT_EMBEDDING_PROVIDER``):

* ``local``   — all-MiniLM-L6-v2 via ONNX (ships with ChromaDB). Free, offline after a
                one-time ~80 MB model download, 384 dims. Good default for a laptop.
* ``voyage``  — Voyage AI (``voyage-3``), the embedding provider Anthropic recommends.
                Best quality; needs ``VOYAGE_API_KEY``.
* ``hashing`` — deterministic feature-hashing of word uni/bi-grams. No model, no
                network, instant. Weak semantics but perfect for unit tests and CI
                where downloading models is not possible. It is also a nice teaching
                baseline: it shows what a *lexical* vector space looks like compared
                to a learned one.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

from ..config import EmbeddingProviderName, get_settings


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


# --------------------------------------------------------------------------- hashing
_TOKEN = re.compile(r"[a-z0-9_\-\.]+")


class HashingEmbeddings:
    name = "hashing"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        toks = _TOKEN.findall(text.lower())
        return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for f in self._features(text):
            h = int(hashlib.blake2b(f.encode(), digest_size=8).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


# --------------------------------------------------------------------------- local ONNX
class LocalMiniLMEmbeddings:
    name = "local"
    dim = 384

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        self._ef = ONNXMiniLM_L6_V2()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._ef(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


# --------------------------------------------------------------------------- voyage
class VoyageEmbeddings:
    name = "voyage"
    dim = 1024

    def __init__(self, api_key: str, model: str = "voyage-3") -> None:
        import voyageai  # optional dependency: pip install "ops-copilot[voyage]"

        self._client = voyageai.Client(api_key=api_key)
        self._model = model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):  # Voyage batch limit
            res = self._client.embed(list(texts[i : i + 64]), model=self._model, input_type="document")
            out.extend(res.embeddings)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed([text], model=self._model, input_type="query").embeddings[0]


# --------------------------------------------------------------------------- factory
def get_embeddings(provider: EmbeddingProviderName | None = None) -> EmbeddingProvider:
    s = get_settings()
    provider = provider or s.embedding_provider
    if provider == "hashing":
        return HashingEmbeddings()
    if provider == "voyage":
        if not s.voyage_api_key:
            raise RuntimeError("VOYAGE_API_KEY is required for the voyage embedding provider")
        return VoyageEmbeddings(s.voyage_api_key, s.voyage_model)
    return LocalMiniLMEmbeddings()
