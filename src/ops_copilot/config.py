"""Central configuration.

Everything tunable lives here and is loaded from environment variables / `.env`
via pydantic-settings, so the same code runs in a laptop, CI, or a container.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root(module_file: Path = Path(__file__)) -> Path:
    """Directory holding ``data/`` and ``.env``.

    ``OPS_COPILOT_ROOT`` wins. Otherwise the source checkout this module lives in (running
    from the repo, or an editable install). Otherwise the current directory: a regular
    ``pip install`` puts the code in site-packages, away from the data.
    """
    if root := os.environ.get("OPS_COPILOT_ROOT"):
        return Path(root).resolve()
    checkout = module_file.resolve().parents[2]
    if (checkout / "src" / "ops_copilot").is_dir():
        return checkout
    return Path.cwd()


REPO_ROOT = project_root()

EmbeddingProviderName = Literal["local", "voyage", "hashing"]

MIN_API_TOKEN_LENGTH = 16


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="OPS_COPILOT_",
        extra="ignore",
    )

    # --- LLM -----------------------------------------------------------------
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
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
    # PORT is what hosts such as Render set for the port they route traffic to
    api_port: int = Field(default=8000, validation_alias=AliasChoices("OPS_COPILOT_API_PORT", "PORT"))
    api_token: SecretStr | None = None  # when set, every API call needs "Authorization: Bearer <token>"

    @field_validator("anthropic_api_key", "api_token", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `OPS_COPILOT_API_TOKEN=` (as in a copied .env.example) must mean "no token", not
        # SecretStr("") - an empty token would otherwise be treated as configured
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("api_token")
    @classmethod
    def _token_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_API_TOKEN_LENGTH:
            raise ValueError(
                f"OPS_COPILOT_API_TOKEN is too short ({len(value.get_secret_value())} characters); use at least "
                f"{MIN_API_TOKEN_LENGTH}, e.g. python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return value

    @property
    def chroma_dir(self) -> Path:
        return self.index_dir / "chroma"

    @property
    def bm25_path(self) -> Path:
        return self.index_dir / "bm25.pkl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
