"""Central configuration.

Everything tunable lives here and is loaded from environment variables / `.env`
via pydantic-settings, so the same code runs in a laptop, CI, or a container.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

EmbeddingProviderName = Literal["local", "voyage", "hashing"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="OPS_COPILOT_",
        extra="ignore",
    )

    # --- LLM -----------------------------------------------------------------
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model_strong: str = "claude-sonnet-5-5"  # specialist agents
    model_fast: str = "claude-haiku-5-5"  # supervisor routing + reviewer/judge
    max_tokens: int = 2048
    temperature: float = 0.0

    # --- RAG -----------------------------------------------------------------
    knowledge_base_dir: Path = REPO_ROOT / "data" / "knowledge_base"
    fixtures_dir: Path = REPO_ROOT / "data" / "fixtures"
    index_dir: Path = REPO_ROOT / "data" / ".index"
    drafts_dir: Path = REPO_ROOT / "data" / ".drafts"
    embedding_provider: EmbeddingProviderName = "local"
    voyage_api_key: str | None = Field(default=None, validation_alias="VOYAGE_API_KEY")
    voyage_model: str = "voyage-3"
    chunk_size: int = 350  # approx tokens (we count words*1.3)
    chunk_overlap: int = 50
    retrieval_k: int = 6
    bm25_weight: float = 1.0
    vector_weight: float = 1.0

    # --- Agents --------------------------------------------------------------
    max_review_rounds: int = 2
    max_tool_calls: int = 8

    # --- API -----------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @property
    def chroma_dir(self) -> Path:
        return self.index_dir / "chroma"

    @property
    def bm25_path(self) -> Path:
        return self.index_dir / "bm25.pkl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
