"""Application settings — single source of truth for all configuration.

Reads from environment variables with sensible defaults for local development.
Uses Pydantic BaseSettings for automatic env-var parsing and validation.
"""

import json as _json
import warnings
from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field, model_validator


class Settings(BaseSettings):
    """Central configuration for the GNIEM backend.

    All values are read from environment variables. A `.env` file in the
    project root is loaded automatically when present.
    """

    # --- GCP / BigQuery ---
    gcp_project_id: str = Field(
        ...,
        description="Google Cloud project ID used for BigQuery billing.",
    )
    google_application_credentials: str | None = Field(
        default=None,
        description=(
            "Path to the GCP service account JSON key file. "
            "If None, Application Default Credentials (ADC) are used."
        ),
    )
    gdelt_dataset: str = Field(
        default="gdelt-bq.gdeltv2",
        description="Fully-qualified BigQuery dataset for GDELT 2.1.",
    )
    gdelt_table: str = Field(
        default="events",
        description="Table name within the GDELT dataset.",
    )
    hot_tier_path: str = Field(
        default="/data/hot_tier",
        description="Local directory path containing hot-tier Parquet files.",
    )
    cache_path: str = Field(
        default="/data/cache",
        description="Local directory path for cached query artifacts and counters.",
    )
    bq_max_scan_bytes: int = Field(
        default=2_000_000_000,
        ge=1,
        description=(
            "Maximum allowed BigQuery bytes scanned for a single query. "
            "Queries above this threshold are aborted after dry run."
        ),
    )
    hot_tier_cutoff_days: int = Field(
        default=90,
        ge=1,
        le=365,
        description="Requests newer than this many days use hot-tier DuckDB.",
    )
    cold_tier_max_window_days: int = Field(
        default=90,
        ge=1,
        le=365,
        description="Maximum allowed cold-tier date window in days.",
    )
    cold_tier_monthly_query_limit: int = Field(
        default=10,
        ge=1,
        le=999999,
        description="Maximum cold-tier queries allowed per user per month.",
    )

    # --- AI / LLM ---
    openrouter_api_key: str | None = Field(default=None)
    openrouter_model_name: str = Field(
        default="google/gemma-4-26b-a4b-it:free",
        pattern=r"^.+:free$",
        description="OpenRouter free model ID; paid model IDs are rejected.",
    )
    openrouter_fallback_model_name: str = Field(
        default="nvidia/nemotron-3.5-super-vl:free", pattern=r"^.+:free$",
        description="Free-only model used if the primary OpenRouter model is unavailable.",
    )
    groq_api_key: str | None = Field(
        default=None,
        description="Groq API key used for on-demand event analysis and briefings.",
    )
    jina_api_key: str | None = Field(
        default=None,
        description="Optional Jina AI Reader API key for higher scraping limits.",
    )
    groq_model_name: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq model to use for analysis (e.g., llama-3.3-70b-versatile).",
    )
    apify_api_token: str | None = Field(
        default=None,
        description="Apify API token used to run article extraction actors.",
    )
    apify_actor_id: str = Field(
        default="apify/web-scraper",
        description="Apify actor used to extract source article text.",
    )
    scraper_timeout_seconds: int = Field(
        default=120,
        ge=1,
        description="Maximum time in seconds to wait for Apify article extraction.",
    )
    action_geo_country_codes_path: str = Field(
        default="data/LOOKUP-COUNTRIES.txt",
        description=(
            "Path to local ActionGeo 2-letter country/region code lookup file "
            "used by nightly briefings."
        ),
    )
    action_geo_country_codes_url: str = Field(
        default="http://data.gdeltproject.org/api/v2/guides/LOOKUP-COUNTRIES.TXT",
        description=(
            "Source URL used to download ActionGeo country code lookup "
            "when local file is missing."
        ),
    )
    cameo_country_codes_path: str = Field(
        default="data/CAMEO.country.txt",
        description=(
            "Path to local CAMEO 3-letter country/region code lookup file "
            "used as fallback context for nightly briefings."
        ),
    )
    cameo_country_codes_url: str = Field(
        default="https://www.gdeltproject.org/data/lookups/CAMEO.country.txt",
        description=(
            "Source URL used to download CAMEO country code lookup "
            "when local file is missing."
        ),
    )
    gdelt_country_codes_path: str = Field(
        default="data/gdelt_country_codes.txt",
        description=(
            "Path to local GDELT CAMEO country/region code lookup file. "
            "Can be relative to repository root or absolute."
        ),
    )
    gdelt_country_codes_url: str = Field(
        default="https://www.gdeltproject.org/data/lookups/CAMEO.country.txt",
        description="Source URL used to download country code lookup when local file is missing.",
    )

    # --- Query Defaults ---
    default_lookback_days: int = Field(
        default=7,
        ge=1,
        le=365,
        description="Default number of days to look back when no date range is specified.",
    )
    default_query_limit: int = Field(
        default=10_000,
        ge=1,
        le=100_000,
        description="Maximum number of rows returned by a single query.",
    )

    # --- Application ---
    environment: str = Field(
        default="development",
        description="Runtime environment: development | staging | production.",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG | INFO | WARNING | ERROR | CRITICAL.",
    )
    api_v1_prefix: str = Field(
        default="/api/v1",
        description="URL prefix for the v1 API.",
    )
    # Stored as a plain string to avoid pydantic-settings JSON decode issues.
    # Use the `cors_origins` property for the parsed list.
    cors_origins_raw: str = Field(
        default='["http://localhost:3000","http://localhost:5173","http://127.0.0.1:5173"]',
        alias="CORS_ORIGINS",
        description="Allowed CORS origins (JSON array string or comma-separated).",
    )

    @property
    def cors_origins(self) -> list[str]:
        """Parse cors_origins_raw into a list of origin strings."""
        if self.environment == "development":
            return ["*"]
        raw = self.cors_origins_raw.strip()
        if raw.startswith("["):
            try:
                return _json.loads(raw)
            except _json.JSONDecodeError:
                inner = raw.strip("[]")
                return [s.strip().strip("\"'") for s in inner.split(",") if s.strip()]
        return [s.strip() for s in raw.split(",") if s.strip()]

    @model_validator(mode="after")
    def ensure_required_dirs(self) -> "Settings":
        """Create required local directories for runtime artifacts.

        This keeps startup deterministic for writable paths: missing
        directories are created automatically. If a path is not writable
        (for example `/data` on local dev machines), startup is not blocked
        here; downstream repositories still enforce their own preconditions.
        """
        for path_str in (self.hot_tier_path, self.cache_path):
            try:
                Path(path_str).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                warnings.warn(
                    f"Could not auto-create directory '{path_str}': {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )

        # Routed repository writes cold-tier parquet cache under this subdir.
        cold_queries_dir = Path(self.cache_path) / "cold_queries"
        try:
            cold_queries_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.warn(
                f"Could not auto-create directory '{cold_queries_dir}': {exc}",
                RuntimeWarning,
                stacklevel=2,
            )

        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "populate_by_name": True,
        "extra": "ignore",
    }


# Module-level singleton — import this instance everywhere.
# In tests, override via FastAPI dependency injection.
settings = Settings()
