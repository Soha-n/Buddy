"""Application settings, loaded from the environment or a .env file."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 127.0.0.1 rather than localhost: on Windows the latter can resolve to ::1
    # first and stall for a beat before falling back to IPv4.
    ollama_host: str = "http://127.0.0.1:11434"
    port: int = 8000
    log_level: str = "info"

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        # Tauri's dev server, so wrapping the app later needs no CORS change.
        "http://localhost:1420",
    ]

    # Overrides where we look for the Ollama model store when measuring disk.
    ollama_models: str | None = None

    # Relative to backend/ unless given as an absolute path.
    db_path: str = "data/buddy.db"

    # Where to look for a self-hosted SearXNG instance. Probed once per session:
    # if it answers JSON, it is preferred over scraping DuckDuckGo. Buddy does
    # not install or manage it - point this at your own instance and it is used
    # automatically.
    searxng_url: str = "http://127.0.0.1:8888"

    # ollama.com has no JSON search API, so live catalog search scrapes its
    # library page; these keep that scrape bounded and cache-friendly.
    live_catalog_timeout_s: float = 3.0
    live_catalog_cache_ttl_s: float = 6 * 60 * 60

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string from the .env file."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def ollama_base(self) -> str:
        return self.ollama_host.rstrip("/")


settings = Settings()
