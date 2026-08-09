"""Single source of configuration.

This module is the only place in the backend that reads the environment.
Everything else imports `settings`. Missing required values raise at import
time, so a misconfigured container dies at startup instead of discovering the
problem on a user's first question.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- runtime ----------
    app_env: str = "dev"
    log_level: str = "INFO"
    service_name: str = "agentic-rag-api"

    # ---------- Supabase ----------
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    # Only needed for projects still issuing legacy HS256 tokens. Projects using
    # asymmetric signing keys verify against the published JWKS instead.
    supabase_jwt_secret: str = ""
    supabase_db_url: str
    storage_bucket: str = "documents"

    # ---------- OpenAI ----------
    openai_api_key: str
    chat_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # ---------- RAG ----------
    # Chunk sizes are in characters, not tokens: a character budget needs no
    # tokeniser download at container start. ~4 chars/token is the working
    # approximation, so 3200 chars lands near the 800-token target in CLAUDE.md.
    chunk_chars: int = 3200
    chunk_overlap_chars: int = 480
    retrieval_top_k: int = 8
    candidate_pool: int = 30
    max_refine_loops: int = 2
    max_upload_mb: int = 50
    min_chars_per_page: int = 40
    history_turns: int = 8
    embed_batch_size: int = 100

    # ---------- ingestion worker ----------
    worker_enabled: bool = True
    worker_poll_seconds: int = 5
    worker_stale_seconds: int = 300
    worker_max_attempts: int = 3

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
