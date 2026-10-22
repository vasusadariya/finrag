"""Typed application settings loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(min_length=1)
    database_url_ro: str = Field(min_length=1)
    edgar_user_agent: str = Field(min_length=1)

    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None

    log_level: str = "INFO"

    def require_llm_key(self) -> str:
        """Return the primary LLM key or fail with a clear message."""
        if not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for LLM calls")
        return self.gemini_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # fields come from the environment
