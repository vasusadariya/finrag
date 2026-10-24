"""Typed application settings loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = Field(min_length=1)
    database_url_ro: str = Field(min_length=1)
    edgar_user_agent: str = Field(min_length=1)

    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None

    log_level: str = "INFO"

    # Cross-encoder rerank adds ~8 s/query on CPU and ~1.1 GB of RAM for no measurable gain on our
    # eval (docs/decisions.md); turn it off on small hosts. Hybrid (B) retrieval is used instead.
    rerank_enabled: bool = True
    sqlguard_url: str = "http://127.0.0.1:8081"
    max_concurrent_runs: int = Field(default=2, ge=1)  # agent runs in flight (CPU-bound models)
    cors_origins: str = "http://localhost:3000"  # comma separated

    # LLM model chains (comma separated; first = preferred, the rest are fallbacks when it is
    # overloaded). The plan's gemini-2.5-* models are closed to new users; these were verified
    # to support tool calling.
    llm_main_models: str = "gemini-3.5-flash,gemini-3.1-flash-lite"
    llm_cheap_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    # minimum retrieval confidence (0-1) to answer instead of refusing; tuned on the eval set
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    def model_chains(self) -> dict[str, list[str]]:
        def split(v: str) -> list[str]:
            return [m.strip() for m in v.split(",") if m.strip()]

        return {"main": split(self.llm_main_models), "cheap": split(self.llm_cheap_models)}

    def require_llm_key(self) -> str:
        """Return the primary LLM key or fail with a clear message."""
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for LLM calls")
        return self.gemini_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # fields come from the environment
