"""API schemas — request/response models for the HTTP layer.

These are separate from domain models to decouple serialisation concerns
from business logic.  The API layer maps between these schemas and domain
models — domain models never leak directly into HTTP responses.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class EventFilterRequest(BaseModel):
    """Query parameters for the GET /events endpoint."""

    start_date: date | None = Field(
        default=None,
        description="Inclusive start date (YYYY-MM-DD). Defaults to 7 days ago.",
    )
    end_date: date | None = Field(
        default=None,
        description="Inclusive end date (YYYY-MM-DD). Defaults to today.",
    )
    country_code: str | None = Field(
        default=None,
        max_length=3,
        description="ISO country code filter (e.g., US, IRQ).",
    )
    event_root_codes: list[str] | None = Field(
        default=None,
        description="CAMEO root event code filters.",
    )
    geo_country: str | None = Field(
        default=None,
        max_length=3,
        description="ActionGeo country code filter.",
    )
    geo_state: str | None = Field(
        default=None,
        description="Reverse-geocoded state/province filter.",
    )
    geo_city: str | None = Field(
        default=None,
        description="Reverse-geocoded city filter.",
    )
    theme_category: str | None = Field(
        default=None,
        description="GKG theme category filter.",
    )
    limit: int = Field(
        default=1000,
        ge=1,
        le=100_000,
        description="Maximum number of records to return.",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class EventResponse(BaseModel):
    """Serialised representation of a single GDELT event."""

    global_event_id: int
    sql_date: date
    actor1_country_code: str | None = None
    actor2_country_code: str | None = None
    event_root_code: str | None = None
    event_code: str | None = None
    goldstein_scale: float | None = None
    num_mentions: int = 0
    num_sources: int = 0
    avg_tone: float | None = None
    themes: list[str] = []
    persons: list[str] = []
    organizations: list[str] = []
    mentions_count: int = 0
    avg_confidence: float | None = None
    action_geo_country_code: str | None = None
    action_geo_lat: float | None = None
    action_geo_long: float | None = None
    source_url: str | None = None


class EventListResponse(BaseModel):
    """Paginated list of events."""

    count: int = Field(description="Number of events in this response.")
    data: list[EventResponse] = Field(description="List of event records.")


class EventCountResponse(BaseModel):
    """Daily-aggregated event statistics for charting."""

    date: date
    count: int
    avg_goldstein_scale: float | None = None
    total_mentions: int = 0
    avg_tone: float | None = None


class EventCountListResponse(BaseModel):
    """List of daily event counts."""

    count: int = Field(description="Number of date records.")
    data: list[EventCountResponse] = Field(description="Daily aggregated counts.")


class EntityCountResponse(BaseModel):
    """Count of a named entity or person."""

    name: str
    count: int


class EntityCountListResponse(BaseModel):
    """A list of aggregated entity counts."""

    count: int = Field(description="Number of entities returned.")
    data: list[EntityCountResponse] = Field(description="List of entity count records.")


class EventClusterResponse(BaseModel):
    """Serialised representation of an event cluster."""

    cluster_id: int
    label: str
    event_count: int
    avg_goldstein_scale: float | None = None
    top_country_codes: list[str] = Field(default_factory=list)
    top_event_codes: list[str] = Field(default_factory=list)
    event_ids: list[int] = Field(default_factory=list)


class ClusterListResponse(BaseModel):
    """List of event clusters."""

    count: int = Field(description="Number of clusters in this response.")
    data: list[EventClusterResponse] = Field(description="List of cluster records.")


class ForecastPointResponse(BaseModel):
    """Serialised representation of a forecasted data point."""

    date: date
    predicted_count: float
    lower_bound: float | None = None
    upper_bound: float | None = None


class ForecastResponse(BaseModel):
    """Serialised time-series forecast result."""

    country_code: str | None = None
    horizon_days: int
    model_type: str
    historical_summary: dict = Field(default_factory=dict)
    predictions: list[ForecastPointResponse]


class MapAggregationResponse(BaseModel):
    """Serialised representation of a geospatial aggregation."""
    lat: float
    lon: float
    intensity: float
    country_code: str | None = None


class MapEventDetailResponse(BaseModel):
    """Serialised representation of an individual event for the map."""
    global_event_id: int
    sql_date: date
    lat: float
    lon: float
    actor1_country_code: str | None = None
    actor2_country_code: str | None = None
    event_root_code: str | None = None
    quad_class: int | None = None
    actor1_type_code: str | None = None
    actor2_type_code: str | None = None
    event_code: str | None = None
    actor1_geo_country_code: str | None = None
    actor2_geo_country_code: str | None = None
    goldstein_scale: float | None = None
    num_mentions: int = 0
    num_sources: int = 0
    avg_tone: float | None = None
    source_url: str | None = None
    actor1_type: str | None = None
    actor2_type: str | None = None
    themes: list[str] = []
    persons: list[str] = []
    organizations: list[str] = []
    gkg_record_id: str | None = None


class MapDataResponse(BaseModel):
    """Unified response for the geospatial event map."""
    zoom: float
    is_aggregated: bool
    count: int
    data: list[MapAggregationResponse] | list[MapEventDetailResponse]


class EntityGroupResponse(BaseModel):
    countries: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    persons: list[str] = Field(default_factory=list)

class EventAnalysisResponse(BaseModel):
    """Serialised representation of an LLM intelligence analysis."""
    summary: str
    sentiment: str
    entities: EntityGroupResponse = Field(default_factory=EntityGroupResponse)
    themes: list[str] = Field(default_factory=list)
    confidence: float
    images: list[str] = Field(default_factory=list, description="Related image URLs for the sidebar.")
    embeds: list[str] = Field(default_factory=list, description="Related embedded media URLs.")


class LiveStreamChannelResponse(BaseModel):
    """Live stream metadata for a single YouTube channel."""

    id: str
    name: str
    video_id: str | None = None
    embed_url: str | None = None
    can_embed: bool = True
    status: str = "ok"
    error: str | None = None


class LiveStreamGroupResponse(BaseModel):
    """Live stream metadata for a country group."""

    group_key: str
    label: str
    channels: list[LiveStreamChannelResponse]



class BigQueryHealthDetail(BaseModel):
    """Nested detail for BigQuery connectivity in health check."""

    connected: bool
    project: str
    dataset: str
    latency_ms: int | None = None
    error: str | None = None


class HotTierHealthDetail(BaseModel):
    """Nested detail for local hot-tier readiness in health check."""

    path: str
    available: bool
    parquet_files: int
    cutoff_days: int
    coverage_days: int = 0
    total_rows: int = 0
    last_updated_at: str | None = None


class HealthResponse(BaseModel):
    """Health-check response with comprehensive service diagnostics."""

    status: str = Field(description="Overall service status: healthy | degraded | unhealthy.")
    environment: str = Field(description="Runtime environment (development, staging, production).")
    version: str = Field(description="Application version string.")
    bigquery: BigQueryHealthDetail = Field(description="BigQuery connection details.")
    hot_tier: HotTierHealthDetail = Field(description="DuckDB hot-tier readiness details.")
    uptime_seconds: float = Field(description="Seconds since the application started.")


class RuntimeSettingsResponse(BaseModel):
    """Read-only runtime settings exposed for frontend diagnostics/control panels."""

    hot_tier_cutoff_days: int
    cold_tier_max_window_days: int
    cold_tier_monthly_query_limit: int
    bq_max_scan_bytes: int
    default_lookback_days: int
    default_query_limit: int
    realtime_fetch_interval_minutes: int
    daily_batch_cron_utc: str
    nightly_ai_cron_utc: str


class RiskScoreResponse(BaseModel):
    """Risk score response for a country and date range."""

    score: int
    trend: str = "stable"
    country_code: str | None = None
    country_name: str | None = None
    country_display: str | None = None
    conflict_ratio: float
    avg_goldstein: float | None = None
    avg_tone: float | None = None
    total_events: int


class CountryDelta(BaseModel):
    """Week-over-week deltas for a single country."""
    event_delta_pct: float
    conflict_delta: float
    tone_delta: float
    score_delta: int


class AnalyticsDeltaResponse(BaseModel):
    """Week-over-week deltas for top countries."""
    data: dict[str, CountryDelta]

# ---------------------------------------------------------------------------
# 15.1 — Global Pulse
# ---------------------------------------------------------------------------
 
class SentimentOverview(BaseModel):
    """Hostile / Neutral / Positive splits for the donut chart."""
    hostile: float = Field(default=0.0, description="Percentage of events that are hostile (QuadClass 3, 4).")
    neutral: float = Field(default=0.0, description="Percentage of events that are neutral (QuadClass 1 with neutral tone).")
    positive: float = Field(default=0.0, description="Percentage of events that are positive (QuadClass 2 or QuadClass 1 with positive tone).")

class GlobalPulseResponse(BaseModel):
    """Live global aggregates for the stats ticker (15.1)."""
 
    total_events_today: int = Field(description="Total events in the current day window.")
    most_active_country: str | None = Field(description="Country code with the highest event count.")
    most_active_name: str | None = Field(description="Full name for most-active country.")
    most_active_display: str | None = Field(description="Display string for most-active country.")
    most_active_count: int = Field(description="Event count for the most-active country.")
    most_hostile_country: str | None = Field(description="Country code with the lowest avg AvgTone.")
    most_hostile_name: str | None = Field(description="Full name for most-hostile country.")
    most_hostile_display: str | None = Field(description="Display string for most-hostile country.")
    avg_global_tone: float | None = Field(description="Mean AvgTone across all events in the window.")
    global_conflict_ratio: float = Field(description="Fraction of events with QuadClass 3 or 4.")
    sentiment: SentimentOverview | None = Field(default=None, description="Sentiment distribution for the donut chart.")
 
 
# ---------------------------------------------------------------------------
# 15.2 — Top Threat Countries
# ---------------------------------------------------------------------------
 
class ThreatCountryEntry(BaseModel):
    """One row in the top-threat list (15.2)."""
 
    country_code: str
    country_name: str | None = None
    country_display: str | None = None
    score: int = Field(description="0–100 risk score (higher = more dangerous).")
    avg_goldstein: float | None = Field(default=None, description="Average Goldstein scale for the country.")
    conflict_ratio: float
    total_events: int
 
 
class TopThreatCountriesResponse(BaseModel):
    """Ranked list of highest-threat countries."""

    count: int
    data: list[ThreatCountryEntry]

# ---------------------------------------------------------------------------
# PHASE 4 — Activity Spikes & Anomalies
# ---------------------------------------------------------------------------

class SpikeAlertEntry(BaseModel):
    """One row in the activity spikes list."""
    country_code: str
    country_name: str | None = None
    country_display: str | None = None
    events_24h: int
    baseline_avg: float
    spike_ratio: float
    top_cameo_root: str | None = None

class SpikeAlertResponse(BaseModel):
    """List of active activity spikes."""
    count: int
    data: list[SpikeAlertEntry]

class AnomalyEntry(BaseModel):
    """Anomaly detection result for a single country."""
    country_name: str | None = None
    country_display: str | None = None
    is_anomaly: bool
    score: float
    reason: str | None = None

class AnomalyResponse(BaseModel):
    """Cached anomaly detection results for all countries."""
    data: dict[str, AnomalyEntry]


class CountryBriefingEntry(BaseModel):
    """Nightly generated briefing entry for a single country/region code."""

    briefing: str
    generated_at: str
    source: str
    summary: str


class BriefingsResponse(BaseModel):
    """Cached nightly country briefings keyed by country code."""

    count: int
    data: dict[str, CountryBriefingEntry]