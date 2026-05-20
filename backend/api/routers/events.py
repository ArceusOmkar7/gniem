"""Events router — HTTP endpoints for GDELT event data.

Zero business logic.  Maps HTTP request parameters to domain filters,
delegates to the use case, and maps domain models to response schemas.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated
import threading
import time as _time

from fastapi import APIRouter, Depends, Query

from backend.api.schemas.schemas import (
    EventAnalysisResponse,
    EventCountListResponse,
    EntityCountResponse,
    EntityCountListResponse,
    EventCountResponse,
    EventFilterRequest,
    EventListResponse,
    EventResponse,
    RiskScoreResponse,
    GlobalPulseResponse, 
    ThreatCountryEntry, 
    TopThreatCountriesResponse
)
from backend.application.use_cases.analyze_event import AnalyzeEventUseCase
from backend.application.use_cases.get_events import GetEventsUseCase
from backend.domain.models.event import EventFilter
from backend.infrastructure.config.settings import settings
from backend.infrastructure.data_access.duckdb_repository import DuckDbRepository, compute_risk_score
from backend.infrastructure.services.lookup_service import lookup_service

router = APIRouter(prefix="/events", tags=["events"])


def _get_use_case() -> GetEventsUseCase:
    """Dependency stub — overridden at app startup via dependency_overrides."""
    raise NotImplementedError("GetEventsUseCase dependency not wired")


def _get_analyze_use_case() -> AnalyzeEventUseCase:
    """Dependency stub."""
    raise NotImplementedError("AnalyzeEventUseCase dependency not wired")


def _get_hot_repository() -> DuckDbRepository:
    """Dependency stub — overridden at app startup."""
    raise NotImplementedError("DuckDbRepository dependency not wired")


def _parse_event_root_codes(event_root_codes: str | None) -> list[str] | None:
    if not event_root_codes:
        return None
    codes = [c.strip() for c in event_root_codes.split(",") if c.strip()]
    return codes or None


@router.get(
    "",
    response_model=EventListResponse,
    summary="List GDELT events",
    description=(
        "Retrieve GDELT events matching optional filters. "
        "Defaults to the last 7 days with a 1 000-row limit."
    ),
)
def list_events(
    use_case: Annotated[GetEventsUseCase, Depends(_get_use_case)],
    start_date: date | None = Query(default=None, description="Inclusive start date"),
    end_date: date | None = Query(default=None, description="Inclusive end date"),
    country_code: str | None = Query(default=None, max_length=3, description="Country code"),
    event_root_codes: str | None = Query(default=None, description="CAMEO root codes (comma-separated)"),
    geo_country: str | None = Query(default=None, description="ActionGeo country code"),
    geo_state: str | None = Query(default=None, description="Reverse-geocoded state/province"),
    geo_city: str | None = Query(default=None, description="Reverse-geocoded city"),
    theme_category: str | None = Query(default=None, description="GKG theme category"),
    limit: int = Query(default=1000, ge=1, le=100_000, description="Row limit"),
) -> EventListResponse:
    codes = _parse_event_root_codes(event_root_codes)
    filters = EventFilter(
        start_date=start_date,
        end_date=end_date,
        country_code=country_code,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
        limit=limit,
    )
    events = use_case.execute(filters)
    data = [EventResponse.model_validate(e.model_dump()) for e in events]
    return EventListResponse(count=len(data), data=data)


@router.get(
    "/top-people",
    response_model=EntityCountListResponse,
    summary="Top people mentioned",
    description=(
        "Return the most frequently mentioned people from the GKG persons list "
        "based on current event filters and the hot-tier dataset."
    ),
)
def top_people(
    hot_repo: Annotated[DuckDbRepository, Depends(_get_hot_repository)],
    start_date: date | None = Query(default=None, description="Inclusive start date"),
    end_date: date | None = Query(default=None, description="Inclusive end date"),
    country_code: str | None = Query(default=None, max_length=3, description="Country code"),
    event_root_codes: str | None = Query(default=None, description="CAMEO root codes (comma-separated)"),
    geo_country: str | None = Query(default=None, description="ActionGeo country code"),
    geo_state: str | None = Query(default=None, description="Reverse-geocoded state/province"),
    geo_city: str | None = Query(default=None, description="Reverse-geocoded city"),
    theme_category: str | None = Query(default=None, description="GKG theme category"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of people to return."),
) -> EntityCountListResponse:
    codes = _parse_event_root_codes(event_root_codes)
    filters = EventFilter(
        start_date=start_date,
        end_date=end_date,
        country_code=country_code,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
        limit=limit,
    )
    # Check in-process cache first to avoid expensive re-scans/UNNESTs.
    now = _time.monotonic()
    cache_key = f"{filters.start_date}:{filters.end_date}:{filters.country_code}:{filters.event_root_codes}:{filters.geo_country}:{filters.geo_state}:{filters.geo_city}:{filters.theme_category}:{limit}"
    with _people_cache_lock:
        entry = _people_cache.get(cache_key)
        if entry is not None and (now - entry["ts"]) < _PEOPLE_TTL:
            return entry["data"]

    people = hot_repo.get_top_people(filters, limit=limit)
    response = EntityCountListResponse(count=len(people), data=[EntityCountResponse.model_validate(person) for person in people])

    with _people_cache_lock:
        _people_cache[cache_key] = {"ts": now, "data": response}
        # Evict stale keys
        stale = [k for k, v in _people_cache.items() if (now - v["ts"]) > _PEOPLE_TTL * 5]
        for k in stale:
            _people_cache.pop(k, None)

    return response


@router.get(
    "/top-sources",
    response_model=EntityCountListResponse,
    summary="Top news sources",
    description=(
        "Return the most active source domains from the hot-tier dataset based on current filters. "
        "The source domain is derived from the SOURCEURL field."
    ),
)
def top_sources(
    hot_repo: Annotated[DuckDbRepository, Depends(_get_hot_repository)],
    start_date: date | None = Query(default=None, description="Inclusive start date"),
    end_date: date | None = Query(default=None, description="Inclusive end date"),
    country_code: str | None = Query(default=None, max_length=3, description="Country code"),
    event_root_codes: str | None = Query(default=None, description="CAMEO root codes (comma-separated)"),
    geo_country: str | None = Query(default=None, description="ActionGeo country code"),
    geo_state: str | None = Query(default=None, description="Reverse-geocoded state/province"),
    geo_city: str | None = Query(default=None, description="Reverse-geocoded city"),
    theme_category: str | None = Query(default=None, description="GKG theme category"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of sources to return."),
) -> EntityCountListResponse:
    codes = _parse_event_root_codes(event_root_codes)
    filters = EventFilter(
        start_date=start_date,
        end_date=end_date,
        country_code=country_code,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
        limit=limit,
    )

    now = _time.monotonic()
    cache_key = f"{filters.start_date}:{filters.end_date}:{filters.country_code}:{filters.event_root_codes}:{filters.geo_country}:{filters.geo_state}:{filters.geo_city}:{filters.theme_category}:{limit}"
    with _source_cache_lock:
        entry = _source_cache.get(cache_key)
        if entry is not None and (now - entry["ts"]) < _SOURCES_TTL:
            return entry["data"]

    sources = hot_repo.get_top_sources(filters, limit=limit)
    response = EntityCountListResponse(count=len(sources), data=[EntityCountResponse.model_validate(source) for source in sources])

    with _source_cache_lock:
        _source_cache[cache_key] = {"ts": now, "data": response}
        stale = [k for k, v in _source_cache.items() if (now - v["ts"]) > _SOURCES_TTL * 5]
        for k in stale:
            _source_cache.pop(k, None)

    return response


@router.get(
    "/top-organizations",
    response_model=EntityCountListResponse,
    summary="Top organizations mentioned",
    description=(
        "Return the most frequently mentioned organizations from the GKG organizations list "
        "based on current event filters and the hot-tier dataset."
    ),
)
def top_organizations(
    hot_repo: Annotated[DuckDbRepository, Depends(_get_hot_repository)],
    start_date: date | None = Query(default=None, description="Inclusive start date"),
    end_date: date | None = Query(default=None, description="Inclusive end date"),
    country_code: str | None = Query(default=None, max_length=3, description="Country code"),
    event_root_codes: str | None = Query(default=None, description="CAMEO root codes (comma-separated)"),
    geo_country: str | None = Query(default=None, description="ActionGeo country code"),
    geo_state: str | None = Query(default=None, description="Reverse-geocoded state/province"),
    geo_city: str | None = Query(default=None, description="Reverse-geocoded city"),
    theme_category: str | None = Query(default=None, description="GKG theme category"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of organizations to return."),
) -> EntityCountListResponse:
    codes = _parse_event_root_codes(event_root_codes)
    filters = EventFilter(
        start_date=start_date,
        end_date=end_date,
        country_code=country_code,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
        limit=limit,
    )

    now = _time.monotonic()
    cache_key = f"{filters.start_date}:{filters.end_date}:{filters.country_code}:{filters.event_root_codes}:{filters.geo_country}:{filters.geo_state}:{filters.geo_city}:{filters.theme_category}:{limit}"
    with _org_cache_lock:
        entry = _org_cache.get(cache_key)
        if entry is not None and (now - entry["ts"]) < _ORGS_TTL:
            return entry["data"]

    orgs = hot_repo.get_top_organizations(filters, limit=limit)
    response = EntityCountListResponse(count=len(orgs), data=[EntityCountResponse.model_validate(org) for org in orgs])

    with _org_cache_lock:
        _org_cache[cache_key] = {"ts": now, "data": response}
        stale = [k for k, v in _org_cache.items() if (now - v["ts"]) > _ORGS_TTL * 5]
        for k in stale:
            _org_cache.pop(k, None)

    return response


@router.get(
    "/top-cities",
    response_model=EntityCountListResponse,
    summary="Top cities by event activity",
    description=(
        "Return the most active cities based on reverse-geocoded event coordinates "
        "from the hot-tier dataset and current filters."
    ),
)
def top_cities(
    hot_repo: Annotated[DuckDbRepository, Depends(_get_hot_repository)],
    start_date: date | None = Query(default=None, description="Inclusive start date"),
    end_date: date | None = Query(default=None, description="Inclusive end date"),
    country_code: str | None = Query(default=None, max_length=3, description="Country code"),
    event_root_codes: str | None = Query(default=None, description="CAMEO root codes (comma-separated)"),
    geo_country: str | None = Query(default=None, description="ActionGeo country code"),
    geo_state: str | None = Query(default=None, description="Reverse-geocoded state/province"),
    geo_city: str | None = Query(default=None, description="Reverse-geocoded city"),
    theme_category: str | None = Query(default=None, description="GKG theme category"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of cities to return."),
) -> EntityCountListResponse:
    codes = _parse_event_root_codes(event_root_codes)
    filters = EventFilter(
        start_date=start_date,
        end_date=end_date,
        country_code=country_code,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
        limit=limit,
    )

    now = _time.monotonic()
    cache_key = f"{filters.start_date}:{filters.end_date}:{filters.country_code}:{filters.event_root_codes}:{filters.geo_country}:{filters.geo_state}:{filters.geo_city}:{filters.theme_category}:{limit}"
    with _city_cache_lock:
        entry = _city_cache.get(cache_key)
        if entry is not None and (now - entry["ts"]) < _CITIES_TTL:
            return entry["data"]

    cities = hot_repo.get_top_cities(filters, limit=limit)
    response = EntityCountListResponse(count=len(cities), data=[EntityCountResponse.model_validate(city) for city in cities])

    with _city_cache_lock:
        _city_cache[cache_key] = {"ts": now, "data": response}
        stale = [k for k, v in _city_cache.items() if (now - v["ts"]) > _CITIES_TTL * 5]
        for k in stale:
            _city_cache.pop(k, None)

    return response


@router.get("/geo-drill")
def get_geo_drill(
    hot_repo: Annotated[DuckDbRepository, Depends(_get_hot_repository)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    country_code: str | None = Query(default=None, description="Filter to get states for a country"),
    state_name: str | None = Query(default=None, description="Filter to get cities for a state"),
):
    """
    Geo drill-down options derived from reverse-geocoded lat/long.
    - No params: returns top 40 countries by event count.
    - country_code only: returns top 20 states for that country.
    - country_code + state_name: returns top 15 cities for that state.
    Always queries hot tier only. Never queries BigQuery.
    """
    return hot_repo.get_geo_drill(
        start_date=start_date,
        end_date=end_date,
        country_code=country_code,
        state_name=state_name,
    )


@router.get(
    "/region/{country_code}",
    response_model=EventListResponse,
    summary="Events by region",
    description="Retrieve events for a specific country/region.",
)
def events_by_region(
    country_code: str,
    use_case: Annotated[GetEventsUseCase, Depends(_get_use_case)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    event_root_codes: str | None = Query(default=None),
    geo_country: str | None = Query(default=None),
    geo_state: str | None = Query(default=None),
    geo_city: str | None = Query(default=None),
    theme_category: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=100_000),
) -> EventListResponse:
    codes = _parse_event_root_codes(event_root_codes)
    events = use_case.get_by_region(
        country_code=country_code,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
    )
    data = [EventResponse.model_validate(e.model_dump()) for e in events]
    return EventListResponse(count=len(data), data=data)


@router.get(
    "/region/{country_code}/risk-score",
    response_model=RiskScoreResponse,
    summary="Country risk score",
    description="Compute a country risk score from hot-tier DuckDB data.",
)
def country_risk_score(
    country_code: str,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    geo_country: str | None = Query(default=None),
    geo_state: str | None = Query(default=None),
    geo_city: str | None = Query(default=None),
    theme_category: str | None = Query(default=None),
) -> RiskScoreResponse:
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=7))

    repository = DuckDbRepository(settings)
    metrics = repository.get_risk_score(
        country_code=country_code,
        start_date=start,
        end_date=end,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
    )
    score = compute_risk_score(
        metrics["conflict_ratio"],
        metrics["avg_goldstein"],
        metrics["avg_tone"],
    )

    return RiskScoreResponse(
        score=score,
        trend="stable",
        country_code=country_code.upper(),
        country_name=lookup_service.get_country_name(country_code.upper()),
        country_display=lookup_service.get_country_display(country_code.upper()),
        conflict_ratio=metrics["conflict_ratio"],
        avg_goldstein=metrics["avg_goldstein"],
        avg_tone=metrics["avg_tone"],
        total_events=metrics["total_events"],
    )


@router.get(
    "/region/{country_code}/stats",
    summary="Regional intelligence stats",
    description="Get top themes and entities for a specific region.",
)
def regional_stats(
    country_code: str,
    use_case: Annotated[GetEventsUseCase, Depends(_get_use_case)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    event_root_codes: str | None = Query(default=None),
    geo_country: str | None = Query(default=None),
    geo_state: str | None = Query(default=None),
    geo_city: str | None = Query(default=None),
    theme_category: str | None = Query(default=None),
):
    """Returns top themes, persons, and organizations for a country."""
    # This logic would ideally be in a service/use case, but for now we'll 
    # use the use_case to get events and aggregate them here for brevity.
    codes = _parse_event_root_codes(event_root_codes)
    events = use_case.get_by_region(
        country_code=country_code,
        start_date=start_date,
        end_date=end_date,
        limit=2000, # Use a larger sample for better stats
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
    )
    
    from collections import Counter
    themes = Counter()
    persons = Counter()
    orgs = Counter()
    
    for e in events:
        themes.update(e.themes)
        persons.update(e.persons)
        orgs.update(e.organizations)
        
    return {
        "country_code": country_code.upper(),
        "top_themes": [{"name": k, "count": v} for k, v in themes.most_common(10)],
        "top_persons": [{"name": k, "count": v} for k, v in persons.most_common(10)],
        "top_organizations": [{"name": k, "count": v} for k, v in orgs.most_common(10)],
    }


@router.get(
    "/counts/{country_code}",
    response_model=EventCountListResponse,
    summary="Daily event counts by country",
    description="Get daily-aggregated event counts for time-series charting.",
)
def event_counts_by_country(
    country_code: str,
    use_case: Annotated[GetEventsUseCase, Depends(_get_use_case)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    event_root_codes: str | None = Query(default=None),
    geo_country: str | None = Query(default=None),
    geo_state: str | None = Query(default=None),
    geo_city: str | None = Query(default=None),
    theme_category: str | None = Query(default=None),
) -> EventCountListResponse:
    codes = _parse_event_root_codes(event_root_codes)
    counts = use_case.get_daily_counts(
        country_code=country_code,
        start_date=start_date,
        end_date=end_date,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
    )
    data = [EventCountResponse.model_validate(c.model_dump()) for c in counts]
    return EventCountListResponse(count=len(data), data=data)


@router.get(
    "/counts",
    response_model=EventCountListResponse,
    summary="Global daily event counts",
    description="Get daily-aggregated event counts globally (no country filter).",
)
def event_counts_global(
    use_case: Annotated[GetEventsUseCase, Depends(_get_use_case)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    event_root_codes: str | None = Query(default=None),
    geo_country: str | None = Query(default=None),
    geo_state: str | None = Query(default=None),
    geo_city: str | None = Query(default=None),
    theme_category: str | None = Query(default=None),
) -> EventCountListResponse:
    codes = _parse_event_root_codes(event_root_codes)
    counts = use_case.get_daily_counts(
        country_code=None,
        start_date=start_date,
        end_date=end_date,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
    )
    data = [EventCountResponse.model_validate(c.model_dump()) for c in counts]
    return EventCountListResponse(count=len(data), data=data)


@router.get(
    "/{event_id}/analyze",
    response_model=EventAnalysisResponse,
    summary="Analyze event via LLM",
    description=(
        "Performs on-demand deep intelligence analysis for a single event. "
        "Scrapes the source article and uses an LLM to generate insights."
    ),
)
async def analyze_event(
    event_id: int,
    use_case: Annotated[AnalyzeEventUseCase, Depends(_get_analyze_use_case)],
) -> EventAnalysisResponse:
    analysis = await use_case.execute(event_id)
    return EventAnalysisResponse.model_validate(analysis.model_dump())

# ---------------------------------------------------------------------------
# In-process TTL caches (process-local, resets on restart — intentional)
# ---------------------------------------------------------------------------
_pulse_cache: dict = {}
_pulse_cache_lock = threading.Lock()
_PULSE_TTL = 60.0   # 60 s
 
_threat_cache: dict = {}
_threat_cache_lock = threading.Lock()
_THREAT_TTL = 120.0  # 2 min

# Cache for /top-people endpoint to avoid re-scanning and UNNEST work on
# repeated requests with the same filters. In-process only (resets on restart).
_people_cache: dict = {}
_people_cache_lock = threading.Lock()
_PEOPLE_TTL = 120.0  # seconds

_source_cache: dict = {}
_source_cache_lock = threading.Lock()
_SOURCES_TTL = 120.0  # seconds

_org_cache: dict = {}
_org_cache_lock = threading.Lock()
_ORGS_TTL = 120.0  # seconds

_city_cache: dict = {}
_city_cache_lock = threading.Lock()
_CITIES_TTL = 120.0  # seconds
 
# ---------------------------------------------------------------------------
# 15.1 — Global Pulse endpoint
# ---------------------------------------------------------------------------
 
@router.get(
    "/global-pulse",
    response_model=GlobalPulseResponse,
    summary="Global pulse stats",
    description=(
        "Returns live aggregate stats across all recent events: "
        "total count, most-active and most-hostile countries, "
        "average tone, and global conflict ratio. "
        "Cached in-process for 60 seconds."
    ),
)
def global_pulse(
    start_date: date | None = Query(default=None, description="Inclusive start date (defaults to 7 days ago)"),
    end_date: date | None = Query(default=None, description="Inclusive end date (defaults to today)"),
    event_root_codes: str | None = Query(default=None, description="CAMEO root codes (comma-separated)"),
    geo_country: str | None = Query(default=None),
    geo_state: str | None = Query(default=None),
    geo_city: str | None = Query(default=None),
    theme_category: str | None = Query(default=None),
) -> GlobalPulseResponse:
    now = _time.monotonic()
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=7))
    codes = _parse_event_root_codes(event_root_codes)
 
    cache_key = f"{start}:{end}:{codes}:{geo_country}:{geo_state}:{geo_city}:{theme_category}"
    with _pulse_cache_lock:
        entry = _pulse_cache.get(cache_key)
        if entry is not None and (now - entry["ts"]) < _PULSE_TTL:
            return entry["data"]
 
    repository = DuckDbRepository(settings)
    metrics = repository.get_global_pulse(
        start_date=start,
        end_date=end,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
    )
 
    most_active = metrics["most_active_country"]
    most_hostile = metrics["most_hostile_country"]

    response = GlobalPulseResponse(
        total_events_today=metrics["total_events_today"],
        most_active_country=most_active,
        most_active_name=lookup_service.get_country_name(most_active) if most_active else None,
        most_active_display=lookup_service.get_country_display(most_active) if most_active else None,
        most_active_count=metrics["most_active_count"],
        most_hostile_country=most_hostile,
        most_hostile_name=lookup_service.get_country_name(most_hostile) if most_hostile else None,
        most_hostile_display=lookup_service.get_country_display(most_hostile) if most_hostile else None,
        avg_global_tone=metrics["avg_global_tone"],
        global_conflict_ratio=metrics["global_conflict_ratio"],
        sentiment=metrics["sentiment"],
    )
 
    with _pulse_cache_lock:
        _pulse_cache[cache_key] = {"ts": now, "data": response}
        # Evict stale keys
        stale = [k for k, v in _pulse_cache.items() if (now - v["ts"]) > _PULSE_TTL * 5]
        for k in stale:
            _pulse_cache.pop(k, None)
 
    return response
 
 
# ---------------------------------------------------------------------------
# 15.2 — Top Threat Countries endpoint
# ---------------------------------------------------------------------------
 
@router.get(
    "/top-threat-countries",
    response_model=TopThreatCountriesResponse,
    summary="Top countries by threat level",
    description=(
        "Returns the top-N countries ranked by computed risk score "
        "(conflict ratio 40%, Goldstein scale 35%, AvgTone 25%). "
        "Draws from hot-tier DuckDB only. Cached for 120 seconds."
    ),
)
def top_threat_countries(
    limit: int = Query(default=5, ge=1, le=50, description="Number of countries to return"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    geo_country: str | None = Query(default=None),
    geo_state: str | None = Query(default=None),
    geo_city: str | None = Query(default=None),
    theme_category: str | None = Query(default=None),
) -> TopThreatCountriesResponse:
    now = _time.monotonic()
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=7))
 
    cache_key = f"{start}:{end}:{limit}:{geo_country}:{geo_state}:{geo_city}:{theme_category}"
    with _threat_cache_lock:
        entry = _threat_cache.get(cache_key)
        if entry is not None and (now - entry["ts"]) < _THREAT_TTL:
            return entry["data"]
 
    repository = DuckDbRepository(settings)
    rows = repository.get_top_threat_countries(
        start_date=start,
        end_date=end,
        limit=limit,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
    )
 
    data = [
        ThreatCountryEntry(
            country_code=r["country_code"],
            country_name=lookup_service.get_country_name(r["country_code"]),
            country_display=lookup_service.get_country_display(r["country_code"]),
            score=r["score"],
            avg_goldstein=r.get("avg_goldstein"),
            conflict_ratio=r["conflict_ratio"],
            total_events=r["total_events"],
        )
        for r in rows
    ]
    response = TopThreatCountriesResponse(count=len(data), data=data)
 
    with _threat_cache_lock:
        _threat_cache[cache_key] = {"ts": now, "data": response}
        stale = [k for k, v in _threat_cache.items() if (now - v["ts"]) > _THREAT_TTL * 5]
        for k in stale:
            _threat_cache.pop(k, None)
 
    return response
 

@router.get(
    "/daily-trend",
    summary="Daily event trend",
    description=(
        "Returns per-day total event counts and conflict event counts "
        "(QuadClass >= 3) for the given date window, suitable for a "
        "stacked area chart."
    ),
)
def daily_trend(
    start_date: date | None = Query(default=None, description="Inclusive start date"),
    end_date: date | None = Query(default=None, description="Inclusive end date"),
    event_root_codes: str | None = Query(default=None),
    geo_country: str | None = Query(default=None),
    geo_state: str | None = Query(default=None),
    geo_city: str | None = Query(default=None),
    theme_category: str | None = Query(default=None),
) -> dict:
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=30))
    codes = _parse_event_root_codes(event_root_codes)
    repository = DuckDbRepository(settings)
    rows = repository.get_daily_trend(
        start_date=start,
        end_date=end,
        event_root_codes=codes,
        geo_country=geo_country,
        geo_state=geo_state,
        geo_city=geo_city,
        theme_category=theme_category,
    )
    return {"data": rows}
