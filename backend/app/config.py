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

    # Weather provider, on by default.
    #
    # "wttr.in" is the default because it is the only keyless option that clears
    # the commercial bar: Apache-2.0, no API key, no non-commercial clause, and
    # self-hostable - so a shipped product can point WEATHER_BASE_URL at its own
    # instance and depend on nobody. Open-Meteo is offered too but its keyless
    # tier is non-commercial, so it must be selected deliberately.
    #
    # Options: "wttr.in" | "open-meteo" | "" (empty = fall through to search)
    weather_provider: str = "wttr.in"
    weather_api_key: str = ""

    # Point this at a self-hosted wttr.in to remove the last third-party
    # dependency from weather answers.
    weather_base_url: str = "https://wttr.in"

    # Bring-your-own search key. Terms are then between the user and the
    # provider, which is what makes this safe to ship commercially.
    # Provider: "brave" or "tavily".
    search_provider: str = ""
    search_api_key: str = ""

    # Last-resort fallback, used only when the local SearXNG is unavailable (still
    # installing, failed to start) and no API key is set. It keeps web search from
    # ever being simply "not available" - the user always gets an answer.
    #
    # Note for a commercial build: scraping these front ends is against their
    # terms of service. The mitigation is that it is genuinely a fallback - Buddy
    # runs its own SearXNG, which is what serves search in practice - but it can
    # be turned off outright if you would rather search fail than scrape.
    allow_scraping_fallback: bool = True

    # Buddy installs and runs its own SearXNG on this port, so search works out
    # of the box with no setup. Set searxng_managed=False to point at an instance
    # you run yourself instead.
    searxng_managed: bool = True
    searxng_port: int = 8888
    #: Where the managed install lives. Empty means "beside the database".
    searxng_install_dir: str = ""
    #: Only consulted when searxng_managed is False.
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
