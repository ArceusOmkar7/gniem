"""DuckDB hot-tier repository backed by local Parquet files.

This repository is intended for recent-data queries served from the local
hot tier. It implements the same IEventRepository contract used by the
BigQuery repository.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import structlog

from backend.domain.models.event import (
    Event,
    EventCountByDate,
    EventFilter,
    MapAggregation,
    MapEventDetail,
)
from backend.domain.ports.ports import IEventRepository
from backend.infrastructure.config.settings import Settings
from backend.infrastructure.services.lookup_service import lookup_service

logger = structlog.get_logger(__name__)

THEME_CATEGORY_MAP = {
    "POLITICS": ["ELECTIONS", "GOVERNMENT", "LEGISLATION", "POLITICAL"],
    "ECONOMY": ["ECON_", "TRADE", "MARKET", "SANCTIONS", "WB_ECONOMY"],
    "HEALTH": ["HEALTH_", "PANDEMIC", "DISEASE", "WB_HEALTH"],
    "ENVIRONMENT": ["ENV_", "CLIMATE", "NATURAL_DISASTER", "FLOOD", "DROUGHT"],
    "TECHNOLOGY": ["CYBER_", "ARTIFICIAL_INTEL", "INTERNET"],
    "ENERGY": ["ENERGY_", "OIL", "NUCLEAR", "SOLAR"],
    "HUMAN_RIGHTS": ["HUMAN_RIGHTS", "REFUGEES", "DISCRIMINATION"],
    "SECURITY": ["TERROR", "WEAPONS", "TAX_MILITARY"],
}

POPULAR_NEWS_THEME = "POPULAR_NEWS"
POPULAR_MENTION_THRESHOLD = 10


def compute_risk_score(conflict_ratio, avg_goldstein, avg_tone) -> int:
    # conflict_ratio: 0.0–1.0, higher = more conflict
    # avg_goldstein: -10 to +10, more negative = more conflict
    # avg_tone: typically -30 to +30, more negative = more hostile
    conflict_ratio = float(conflict_ratio or 0.0)
    avg_goldstein = float(avg_goldstein) if avg_goldstein is not None else 0.0
    avg_tone = float(avg_tone) if avg_tone is not None else 0.0

    goldstein_score = max(0, min(100, ((-avg_goldstein + 10) / 20) * 100))
    tone_score = max(0, min(100, ((-avg_tone + 30) / 60) * 100))
    conflict_score = conflict_ratio * 100
    return round(conflict_score * 0.4 + goldstein_score * 0.35 + tone_score * 0.25)


def _build_theme_filter(theme_category: str | None) -> tuple[str, list[Any]]:
    if not theme_category:
        return "", []

    normalized = theme_category.upper()
    if normalized == POPULAR_NEWS_THEME:
        return "NumMentions > ?", [POPULAR_MENTION_THRESHOLD]

    prefixes = THEME_CATEGORY_MAP.get(normalized)
    if not prefixes:
        return "", []

    clauses = " OR ".join(["array_to_string(themes, ';') ILIKE ?" for _ in prefixes])
    return f"({clauses})", [f"%{p}%" for p in prefixes]


class DuckDbRepositoryError(Exception):
    """Raised when hot-tier DuckDB repository preconditions are not met."""


class DuckDbRepository(IEventRepository):
    """DuckDB-backed implementation of the event repository for hot-tier data."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        hot_tier_dir = Path(settings.hot_tier_path)
        # Use relative path if the absolute path doesn't exist (helpful for Dev container vs local Windows)
        if not hot_tier_dir.exists():
            rel_path = Path("gdelt_global_news_trends") / settings.hot_tier_path.lstrip("/\\")
            if rel_path.exists():
                hot_tier_dir = rel_path

        self._parquet_glob = str(hot_tier_dir / "*.parquet")

        if not hot_tier_dir.exists() or not hot_tier_dir.is_dir():
            raise DuckDbRepositoryError(
                f"Hot-tier path does not exist or is not a directory: {hot_tier_dir}"
            )

        if not any(hot_tier_dir.glob("*.parquet")):
            raise DuckDbRepositoryError(
                f"No parquet files found in hot-tier path: {hot_tier_dir}"
            )

    def _get_con(self):
        """Standardized connection for DuckDB queries."""
        return duckdb.connect(database=":memory:")

    def get_ingestion_stats(self) -> dict[str, Any]:
        """Returns row count, coverage days, and last data date time for the hot tier."""
        hot_tier_dir = Path(self._settings.hot_tier_path)
        # Handle the same relative path workaround for stats
        if not hot_tier_dir.exists():
            rel_path = Path("gdelt_global_news_trends") / self._settings.hot_tier_path.lstrip("/\\")
            if rel_path.exists():
                hot_tier_dir = rel_path

        if not hot_tier_dir.exists() or not any(hot_tier_dir.glob("*.parquet")):
            return {"total_rows": 0, "coverage_days": 0, "last_updated_at": None}
 
        try:
            sql = f"""
                SELECT 
                    COUNT(*) AS cnt,
                    COUNT(DISTINCT SQLDATE) AS days,
                    MAX(SQLDATE) AS max_date
                FROM read_parquet('{self._parquet_glob}')
            """
            rows = self._get_con().execute(sql).fetchall()
            if not rows or not rows[0]:
                return {"total_rows": 0, "coverage_days": 0, "last_updated_at": None}
            
            total_rows = int(rows[0][0])
            coverage_days = int(rows[0][1])
            max_date_val = rows[0][2]
            
            if max_date_val:
                # Format SQLDATE (e.g. 20260331) back into a proper isoformat datetime string
                d_str = str(max_date_val)
                if len(d_str) == 8:
                    y, m, d = int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8])
                    last_updated = datetime(y, m, d).isoformat()
                else:
                    last_updated = datetime.now().isoformat()
            else:
                last_updated = datetime.now().isoformat()
 
            return {
                "total_rows": total_rows, 
                "coverage_days": coverage_days,
                "last_updated_at": last_updated
            }
        except Exception as e:
            logger.error("failed_to_get_ingestion_stats", error=str(e))
            return {"total_rows": 0, "coverage_days": 0, "last_updated_at": None}
 

    # ------------------------------------------------------------------
    # IEventRepository implementation
    # ------------------------------------------------------------------

    def get_events(self, filters: EventFilter) -> list[Event]:
        start_date, end_date = self._resolve_dates(filters)
        limit = filters.limit or self._settings.default_query_limit
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        where_clauses = ["SQLDATE >= ?", "SQLDATE < ?"]
        params: list[Any] = [start_int, end_exclusive_int]

        if filters.country_code:
            where_clauses.append(
                "(Actor1CountryCode = ? OR ActionGeo_CountryCode = ?)"
            )
            cc = filters.country_code.upper()
            params.extend([cc, cc])

        if filters.event_root_codes:
            placeholders = ", ".join(["?" for _ in filters.event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(filters.event_root_codes)

        if filters.geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(filters.geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(filters.theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        self._apply_geo_state_city_filter(
            where_clauses,
            params,
            filters.geo_state,
            filters.geo_city,
        )

        sql = f"""
            SELECT
                GLOBALEVENTID,
                SQLDATE,
                Actor1CountryCode,
                Actor2CountryCode,
                EventRootCode,
                EventCode,
                GoldsteinScale,
                NumMentions,
                NumSources,
                AvgTone,
                themes,
                persons,
                organizations,
                mentions_count,
                avg_confidence,
                ActionGeo_CountryCode,
                ActionGeo_Lat,
                ActionGeo_Long,
                SOURCEURL
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where_clauses)}
            ORDER BY SQLDATE DESC
            LIMIT ?
        """
        params.append(limit)

        rows = self._query(sql, params)
        return [self._row_to_event(row) for row in rows]

    def get_events_by_region(
        self,
        country_code: str,
        filters: EventFilter,
    ) -> list[Event]:
        region_filter = filters.model_copy(update={"country_code": country_code.upper()})
        return self.get_events(region_filter)

    def get_event_counts_by_date(
        self,
        country_code: str | None,
        filters: EventFilter,
    ) -> list[EventCountByDate]:
        start_date, end_date = self._resolve_dates(filters)
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        where_clauses = ["SQLDATE >= ?", "SQLDATE < ?"]
        params: list[Any] = [start_int, end_exclusive_int]

        if country_code:
            where_clauses.append(
                "(Actor1CountryCode = ? OR ActionGeo_CountryCode = ?)"
            )
            cc = country_code.upper()
            params.extend([cc, cc])

        if filters.event_root_codes:
            placeholders = ", ".join(["?" for _ in filters.event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(filters.event_root_codes)

        if filters.geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(filters.geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(filters.theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        self._apply_geo_state_city_filter(
            where_clauses,
            params,
            filters.geo_state,
            filters.geo_city,
        )

        sql = f"""
            SELECT
                SQLDATE,
                COUNT(*) AS event_count,
                AVG(GoldsteinScale) AS avg_goldstein,
                SUM(NumMentions) AS total_mentions,
                AVG(AvgTone) AS avg_tone
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where_clauses)}
            GROUP BY SQLDATE
            ORDER BY SQLDATE ASC
        """

        rows = self._query(sql, params)
        return [self._row_to_count(row) for row in rows]

    def get_top_people(
        self,
        filters: EventFilter,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        start_date, end_date = self._resolve_dates(filters)
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        # Apply row-level filters first (before UNNEST) to avoid exploding arrays
        where_clauses = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "persons IS NOT NULL",
            "persons <> []",
        ]
        params: list[Any] = [start_int, end_exclusive_int]

        if filters.country_code:
            where_clauses.append(
                "(Actor1CountryCode = ? OR ActionGeo_CountryCode = ?)"
            )
            cc = filters.country_code.upper()
            params.extend([cc, cc])

        if filters.event_root_codes:
            placeholders = ", ".join(["?" for _ in filters.event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(filters.event_root_codes)

        if filters.geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(filters.geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(filters.theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        # Push filters into a subquery so DuckDB reads and filters rows first,
        # then UNNESTs the smaller set of `persons`. After UNNEST we still
        # filter out empty person strings.
        sub_where = ' AND '.join(where_clauses)
        sql = f"""
            SELECT
                person AS name,
                SUM(NumMentions) AS count
            FROM (
                SELECT GLOBALEVENTID, NumMentions, persons
                FROM read_parquet('{self._parquet_glob}')
                WHERE {sub_where}
            ) AS ev, UNNEST(ev.persons) AS people(person)
            WHERE person IS NOT NULL AND person != ''
            GROUP BY person
            ORDER BY count DESC
            LIMIT ?
        """

        params.append(limit)
        rows = self._query(sql, params)
        return [
            {
                "name": row.get("name") or "Unknown",
                "count": int(row.get("count") or 0),
            }
            for row in rows
        ]

    def get_top_organizations(
        self,
        filters: EventFilter,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        start_date, end_date = self._resolve_dates(filters)
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        where_clauses = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "organizations IS NOT NULL",
            "organizations <> []",
        ]
        params: list[Any] = [start_int, end_exclusive_int]

        if filters.country_code:
            where_clauses.append(
                "(Actor1CountryCode = ? OR ActionGeo_CountryCode = ?)"
            )
            cc = filters.country_code.upper()
            params.extend([cc, cc])

        if filters.event_root_codes:
            placeholders = ", ".join(["?" for _ in filters.event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(filters.event_root_codes)

        if filters.geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(filters.geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(filters.theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        sub_where = " AND ".join(where_clauses)
        sql = f"""
            SELECT
                org AS name,
                SUM(NumMentions) AS count
            FROM (
                SELECT GLOBALEVENTID, NumMentions, organizations
                FROM read_parquet('{self._parquet_glob}')
                WHERE {sub_where}
            ) AS ev, UNNEST(ev.organizations) AS orgs(org)
            WHERE org IS NOT NULL AND org != ''
            GROUP BY org
            ORDER BY count DESC
            LIMIT ?
        """

        params.append(limit)
        rows = self._query(sql, params)
        return [
            {
                "name": row.get("name") or "Unknown",
                "count": int(row.get("count") or 0),
            }
            for row in rows
        ]

    def get_top_sources(
        self,
        filters: EventFilter,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        start_date, end_date = self._resolve_dates(filters)
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        where_clauses = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "SOURCEURL IS NOT NULL",
            "SOURCEURL <> ''",
        ]
        params: list[Any] = [start_int, end_exclusive_int]

        if filters.country_code:
            where_clauses.append(
                "(Actor1CountryCode = ? OR ActionGeo_CountryCode = ?)"
            )
            cc = filters.country_code.upper()
            params.extend([cc, cc])

        if filters.event_root_codes:
            placeholders = ", ".join(["?" for _ in filters.event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(filters.event_root_codes)

        if filters.geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(filters.geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(filters.theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        sub_where = ' AND '.join(where_clauses)
        sql = f"""
            SELECT
                source_domain AS name,
                COUNT(*) AS count
            FROM (
                SELECT
                    split_part(
                        REPLACE(REPLACE(REPLACE(LOWER(SOURCEURL), 'https://', ''), 'http://', ''), 'www.', ''),
                        '/',
                        1
                    ) AS source_domain
                FROM read_parquet('{self._parquet_glob}')
                WHERE {sub_where}
            ) AS sources
            WHERE source_domain IS NOT NULL AND source_domain != ''
            GROUP BY source_domain
            ORDER BY count DESC
            LIMIT ?
        """

        params.append(limit)
        rows = self._query(sql, params)
        return [
            {
                "name": row.get("name") or "Unknown",
                "count": int(row.get("count") or 0),
            }
            for row in rows
        ]

    def get_top_cities(
        self,
        filters: EventFilter,
        limit: int = 10,
        max_points: int = 2000,
    ) -> list[dict[str, Any]]:
        from backend.infrastructure.services.reverse_geocode_service import reverse_geocode_service

        start_date, end_date = self._resolve_dates(filters)
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        where_clauses = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "ActionGeo_Lat IS NOT NULL",
            "ActionGeo_Long IS NOT NULL",
        ]
        params: list[Any] = [start_int, end_exclusive_int]

        if filters.country_code:
            where_clauses.append(
                "(Actor1CountryCode = ? OR ActionGeo_CountryCode = ?)"
            )
            cc = filters.country_code.upper()
            params.extend([cc, cc])

        if filters.event_root_codes:
            placeholders = ", ".join(["?" for _ in filters.event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(filters.event_root_codes)

        if filters.geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(filters.geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(filters.theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        sql = f"""
            SELECT
                ActionGeo_Lat AS lat,
                ActionGeo_Long AS lon,
                COUNT(*) AS count
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where_clauses)}
            GROUP BY ActionGeo_Lat, ActionGeo_Long
            ORDER BY count DESC
            LIMIT ?
        """

        rows = self._query(sql, params + [max_points])
        if not rows:
            return []

        coords = [(row["lat"], row["lon"]) for row in rows]
        geo_results = reverse_geocode_service.lookup_batch(coords)

        city_counts: dict[str, int] = {}
        for row, geo in zip(rows, geo_results):
            city = (geo.get("city") or "").strip()
            state = (geo.get("state") or "").strip()
            if not city:
                continue
            label = f"{city}, {state}" if state else city
            city_counts[label] = city_counts.get(label, 0) + int(row.get("count") or 0)

        ranked = sorted(city_counts.items(), key=lambda x: x[1], reverse=True)
        return [
            {"name": name, "count": count}
            for name, count in ranked[:limit]
        ]

    def get_map_aggregations(
        self,
        bbox_n: float,
        bbox_s: float,
        bbox_e: float,
        bbox_w: float,
        filters: EventFilter,
        grid_precision: int = 2,
    ) -> list[MapAggregation]:
        start_date, end_date = self._resolve_dates(filters)
        limit = filters.limit or self._settings.default_query_limit
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        # Normalize longitudes to [-180, 180] for DuckDB
        # If view is wider than 360 degrees, skip longitude filtering
        if abs(bbox_e - bbox_w) >= 360:
            norm_w = -180.0
            norm_e = 180.0
            is_full_world = True
        else:
            norm_w = ((bbox_w + 180) % 360) - 180
            norm_e = ((bbox_e + 180) % 360) - 180
            is_full_world = False

        where_clauses = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "ActionGeo_Lat IS NOT NULL",
            "ActionGeo_Long IS NOT NULL",
            "ActionGeo_Lat <= ?",
            "ActionGeo_Lat >= ?",
        ]
        params: list[Any] = [
            start_int,
            end_exclusive_int,
            bbox_n,
            bbox_s,
        ]

        if not is_full_world:
            if norm_w <= norm_e:
                where_clauses.append("ActionGeo_Long <= ?")
                where_clauses.append("ActionGeo_Long >= ?")
                params.extend([norm_e, norm_w])
            else:
                # Crosses International Date Line
                where_clauses.append("(ActionGeo_Long <= ? OR ActionGeo_Long >= ?)")
                params.extend([norm_e, norm_w])

        if filters.event_root_codes:
            placeholders = ", ".join(["?" for _ in filters.event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(filters.event_root_codes)

        if filters.geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(filters.geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(filters.theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        self._apply_geo_state_city_filter(
            where_clauses,
            params,
            filters.geo_state,
            filters.geo_city,
        )

        sql = f"""
            SELECT
                ROUND(ActionGeo_Lat, ?) AS lat,
                ROUND(ActionGeo_Long, ?) AS lon,
                MODE(ActionGeo_CountryCode) AS country_code,
                COUNT(*) AS intensity
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where_clauses)}
            GROUP BY lat, lon
            LIMIT ?
        """
        all_params = [grid_precision, grid_precision, *params, limit]

        rows = self._query(sql, all_params)
        return [
            MapAggregation(
                lat=row["lat"],
                lon=row["lon"],
                intensity=row["intensity"],
                country_code=row.get("country_code"),
            )
            for row in rows
        ]

    def get_event_details(
        self,
        bbox_n: float,
        bbox_s: float,
        bbox_e: float,
        bbox_w: float,
        filters: EventFilter,
        min_mentions: int = 1,
    ) -> list[MapEventDetail]:
        start_date, end_date = self._resolve_dates(filters)
        limit = filters.limit or self._settings.default_query_limit
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        # Normalize longitudes to [-180, 180] for DuckDB
        # If view is wider than 360 degrees, skip longitude filtering
        if abs(bbox_e - bbox_w) >= 360:
            norm_w = -180.0
            norm_e = 180.0
            is_full_world = True
        else:
            norm_w = ((bbox_w + 180) % 360) - 180
            norm_e = ((bbox_e + 180) % 360) - 180
            is_full_world = False

        where_clauses = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "ActionGeo_Lat IS NOT NULL",
            "ActionGeo_Long IS NOT NULL",
            "ActionGeo_Lat <= ?",
            "ActionGeo_Lat >= ?",
            "NumMentions >= ?",
        ]
        params: list[Any] = [
            start_int,
            end_exclusive_int,
            bbox_n,
            bbox_s,
            min_mentions,
        ]

        if not is_full_world:
            if norm_w <= norm_e:
                where_clauses.append("ActionGeo_Long <= ?")
                where_clauses.append("ActionGeo_Long >= ?")
                params.extend([norm_e, norm_w])
            else:
                # Crosses International Date Line
                where_clauses.append("(ActionGeo_Long <= ? OR ActionGeo_Long >= ?)")
                params.extend([norm_e, norm_w])

        if filters.event_root_codes:
            placeholders = ", ".join(["?" for _ in filters.event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(filters.event_root_codes)

        if filters.geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(filters.geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(filters.theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        self._apply_geo_state_city_filter(
            where_clauses,
            params,
            filters.geo_state,
            filters.geo_city,
        )

        sql = f"""
            SELECT
                GLOBALEVENTID,
                SQLDATE,
                ActionGeo_Lat AS lat,
                ActionGeo_Long AS lon,
                Actor1CountryCode,
                Actor2CountryCode,
                EventRootCode,
                QuadClass,
                Actor1Type1Code AS actor1_type_code,
                Actor2Type1Code AS actor2_type_code,
                EventCode,
                Actor1Geo_CountryCode,
                Actor2Geo_CountryCode,
                GoldsteinScale,
                NumMentions,
                NumSources,
                AvgTone,
                SOURCEURL,
                Actor1Type1Code AS Actor1Type,
                Actor2Type1Code AS Actor2Type,
                themes,
                persons,
                organizations
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where_clauses)}
            LIMIT ?
        """
        params.append(limit)

        rows = self._query(sql, params)
        return [self._row_to_map_detail(row) for row in rows]

    def get_event_by_id(self, event_id: int) -> Event | None:
        sql = f"""
            SELECT
                GLOBALEVENTID,
                SQLDATE,
                Actor1CountryCode,
                Actor2CountryCode,
                EventRootCode,
                EventCode,
                GoldsteinScale,
                NumMentions,
                NumSources,
                AvgTone,
                themes,
                persons,
                organizations,
                mentions_count,
                avg_confidence,
                ActionGeo_CountryCode,
                ActionGeo_Lat,
                ActionGeo_Long,
                SOURCEURL
            FROM read_parquet('{self._parquet_glob}')
            WHERE GLOBALEVENTID = ?
            LIMIT 1
        """

        rows = self._query(sql, [event_id])
        if not rows:
            return None
        
        return self._row_to_event(rows[0])
        return self._row_to_event(rows[0])

    def get_risk_score(
        self,
        country_code: str,
        start_date: date,
        end_date: date,
        geo_state: str | None = None,
        geo_city: str | None = None,
        theme_category: str | None = None,
    ) -> dict[str, Any]:
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        where_clauses = [
            "ActionGeo_CountryCode = ?",
            "SQLDATE >= ?",
            "SQLDATE < ?",
        ]
        params: list[Any] = [country_code.upper(), start_int, end_exclusive_int]

        theme_clause, theme_params = _build_theme_filter(theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        self._apply_geo_state_city_filter(
            where_clauses,
            params,
            geo_state,
            geo_city,
        )

        sql = f"""
            SELECT
                COUNT(*) AS total_events,
                CASE
                    WHEN COUNT(*) = 0 THEN 0.0
                    ELSE SUM(CASE WHEN QuadClass IN (3, 4) THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
                END AS conflict_ratio,
                AVG(GoldsteinScale) AS avg_goldstein,
                AVG(AvgTone) AS avg_tone,
                COALESCE(SUM(NumMentions), 0) AS total_mentions
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where_clauses)}
        """

        rows = self._query(sql, params)
        if not rows:
            return {
                "total_events": 0,
                "conflict_ratio": 0.0,
                "avg_goldstein": None,
                "avg_tone": None,
                "total_mentions": 0,
            }

        row = rows[0]
        return {
            "total_events": int(row.get("total_events") or 0),
            "conflict_ratio": float(row.get("conflict_ratio") or 0.0),
            "avg_goldstein": row.get("avg_goldstein"),
            "avg_tone": row.get("avg_tone"),
            "total_mentions": int(row.get("total_mentions") or 0),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_geo_name(value: str | None) -> str:
        return (value or "").strip().casefold()

    def _apply_geo_state_city_filter(
        self,
        where_clauses: list[str],
        params: list[Any],
        geo_state: str | None,
        geo_city: str | None,
    ) -> None:
        if not geo_state and not geo_city:
            return

        from backend.infrastructure.services.reverse_geocode_service import reverse_geocode_service

        lookup_where = list(where_clauses)
        lookup_params = list(params)

        if "ActionGeo_Lat IS NOT NULL" not in lookup_where:
            lookup_where.append("ActionGeo_Lat IS NOT NULL")
        if "ActionGeo_Long IS NOT NULL" not in lookup_where:
            lookup_where.append("ActionGeo_Long IS NOT NULL")

        sql = f"""
            SELECT GLOBALEVENTID, ActionGeo_Lat, ActionGeo_Long
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(lookup_where)}
        """

        rows = self._query(sql, lookup_params)
        if not rows:
            where_clauses.append("1 = 0")
            return

        coords = [(row["ActionGeo_Lat"], row["ActionGeo_Long"]) for row in rows]
        geo_results = reverse_geocode_service.lookup_batch(coords)

        target_state = self._normalize_geo_name(geo_state)
        target_city = self._normalize_geo_name(geo_city)
        matched_ids: list[int] = []

        for row, geo in zip(rows, geo_results):
            if target_state and self._normalize_geo_name(geo.get("state")) != target_state:
                continue
            if target_city and self._normalize_geo_name(geo.get("city")) != target_city:
                continue
            matched_ids.append(row["GLOBALEVENTID"])

        if not matched_ids:
            where_clauses.append("1 = 0")
            return

        placeholders = ", ".join(["?" for _ in matched_ids])
        where_clauses.append(f"GLOBALEVENTID IN ({placeholders})")
        params.extend(matched_ids)

    def _query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        """Execute a DuckDB query and return rows as dictionaries."""
        logger.debug("duckdb_query", sql_preview=sql[:200], params_count=len(params))
        conn = duckdb.connect(database=":memory:", read_only=False)
        try:
            result = conn.execute(sql, params)
            columns = [col[0] for col in (result.description or [])]
            values = result.fetchall()
        finally:
            conn.close()
        return [dict(zip(columns, row)) for row in values]

    def _resolve_dates(self, filters: EventFilter) -> tuple[date, date]:
        """Fill in default start/end dates when not supplied by the caller.

        If no dates are provided, we look back from the latest date present in
        the local hot tier, rather than today, to avoid blank dashboards.
        """
        if filters.start_date and filters.end_date:
            return filters.start_date, filters.end_date

        end = filters.end_date
        if not end:
            try:
                res = self._get_con().execute(f"SELECT MAX(SQLDATE) FROM read_parquet('{self._parquet_glob}')").fetchone()
                if res and res[0]:
                    latest_raw = str(res[0])
                    end = date(int(latest_raw[:4]), int(latest_raw[4:6]), int(latest_raw[6:8]))
                else:
                    end = date.today()
            except Exception as e:
                logger.error("failed_to_resolve_max_date", error=str(e))
                end = date.today()

        start = filters.start_date or (
            end - timedelta(days=self._settings.default_lookback_days)
        )
        return start, end

    @staticmethod
    def _sql_date_bounds(start_date: date, end_date: date) -> tuple[int, int]:
        end_date_exclusive = end_date + timedelta(days=1)
        return int(start_date.strftime("%Y%m%d")), int(end_date_exclusive.strftime("%Y%m%d"))

    @staticmethod
    def _row_to_event(row: dict[str, Any]) -> Event:
        sql_date_raw = row.get("SQLDATE")
        if isinstance(sql_date_raw, int):
            sql_date_str = str(sql_date_raw)
            parsed_date = date(
                int(sql_date_str[:4]),
                int(sql_date_str[4:6]),
                int(sql_date_str[6:8]),
            )
        else:
            parsed_date = sql_date_raw

        return Event(
            global_event_id=row["GLOBALEVENTID"],
            sql_date=parsed_date,
            actor1_country_code=row.get("Actor1CountryCode"),
            actor2_country_code=row.get("Actor2CountryCode"),
            event_root_code=row.get("EventRootCode"),
            event_code=row.get("EventCode"),
            goldstein_scale=row.get("GoldsteinScale"),
            num_mentions=row.get("NumMentions", 0),
            num_sources=row.get("NumSources", 0),
            avg_tone=row.get("AvgTone"),
            themes=row.get("themes", []),
            persons=row.get("persons", []),
            organizations=row.get("organizations", []),
            mentions_count=int(row.get("mentions_count") or 0),
            avg_confidence=row.get("avg_confidence"),
            action_geo_country_code=row.get("ActionGeo_CountryCode"),
            action_geo_lat=row.get("ActionGeo_Lat"),
            action_geo_long=row.get("ActionGeo_Long"),
            source_url=row.get("SOURCEURL"),
        )

    @staticmethod
    def _row_to_count(row: dict[str, Any]) -> EventCountByDate:
        sql_date_raw = row.get("SQLDATE")
        if isinstance(sql_date_raw, int):
            sql_date_str = str(sql_date_raw)
            parsed_date = date(
                int(sql_date_str[:4]),
                int(sql_date_str[4:6]),
                int(sql_date_str[6:8]),
            )
        else:
            parsed_date = sql_date_raw

        return EventCountByDate(
            date=parsed_date,
            count=row.get("event_count", 0),
            avg_goldstein_scale=row.get("avg_goldstein"),
            total_mentions=row.get("total_mentions", 0),
            avg_tone=row.get("avg_tone"),
        )

    @staticmethod
    def _row_to_map_detail(row: dict[str, Any]) -> MapEventDetail:
        sql_date_raw = row.get("SQLDATE")
        if isinstance(sql_date_raw, int):
            sql_date_str = str(sql_date_raw)
            parsed_date = date(
                int(sql_date_str[:4]),
                int(sql_date_str[4:6]),
                int(sql_date_str[6:8]),
            )
        else:
            parsed_date = sql_date_raw

        return MapEventDetail(
            global_event_id=row["GLOBALEVENTID"],
            sql_date=parsed_date,
            lat=row["lat"],
            lon=row["lon"],
            actor1_country_code=row.get("Actor1CountryCode"),
            actor2_country_code=row.get("Actor2CountryCode"),
            event_root_code=row.get("EventRootCode"),
            quad_class=row.get("QuadClass"),
            actor1_type_code=row.get("actor1_type_code"),
            actor2_type_code=row.get("actor2_type_code"),
            event_code=row.get("EventCode"),
            actor1_geo_country_code=row.get("Actor1Geo_CountryCode"),
            actor2_geo_country_code=row.get("Actor2Geo_CountryCode"),
            goldstein_scale=row.get("GoldsteinScale"),
            num_mentions=row.get("NumMentions", 0),
            num_sources=row.get("NumSources", 0),
            avg_tone=row.get("AvgTone"),
            source_url=row.get("SOURCEURL"),
            actor1_type=row.get("Actor1Type"),
            actor2_type=row.get("Actor2Type"),
            themes=row.get("themes", []),
            persons=row.get("persons", []),
            organizations=row.get("organizations", []),
        )

    # ------------------------------------------------------------------
    # 15.1 — Global Pulse
    # ------------------------------------------------------------------
 
    def get_global_pulse(
        self,
        start_date: date,
        end_date: date,
        event_root_codes: list[str] | None = None,
        geo_country: str | None = None,
        geo_state: str | None = None,
        geo_city: str | None = None,
        theme_category: str | None = None,
    ) -> dict[str, Any]:
        """Return global aggregate stats for the stats ticker."""
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        where_clauses = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "ActionGeo_CountryCode IS NOT NULL",
            "ActionGeo_CountryCode != ''",
        ]
        params = [start_int, end_exclusive_int]

        if event_root_codes:
            placeholders = ", ".join(["?" for _ in event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(event_root_codes)

        if geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        self._apply_geo_state_city_filter(
            where_clauses,
            params,
            geo_state,
            geo_city,
        )

        where_clause = " AND ".join(where_clauses)
 
        # Query 1 — global aggregates + most-active country
        sql_global = f"""
            SELECT
                COUNT(*)                                                              AS total_events,
                MODE(ActionGeo_CountryCode)                                           AS most_active_country,
                AVG(AvgTone)                                                          AS avg_global_tone,
                SUM(CASE WHEN QuadClass IN (3, 4) THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS conflict_ratio,
                -- Sentiment counts
                SUM(CASE WHEN QuadClass IN (3, 4) THEN 1 ELSE 0 END) AS hostile_count,
                SUM(CASE WHEN QuadClass = 2 OR (QuadClass = 1 AND AvgTone > 2.0) THEN 1 ELSE 0 END) AS positive_count,
                SUM(CASE WHEN QuadClass = 1 AND AvgTone <= 2.0 THEN 1 ELSE 0 END) AS neutral_count
            FROM read_parquet('{self._parquet_glob}')
            WHERE {where_clause}
        """
        global_rows = self._query(sql_global, params)
        g = global_rows[0] if global_rows else {}
 
        # Query 2 — most-active country event count
        most_active_cc = g.get("most_active_country")
        most_active_count = 0
        if most_active_cc:
            sql_count = f"""
                SELECT COUNT(*) AS cnt
                FROM read_parquet('{self._parquet_glob}')
                WHERE {where_clause} AND ActionGeo_CountryCode = ?
            """
            count_rows = self._query(sql_count, params + [most_active_cc])
            most_active_count = int(count_rows[0]["cnt"]) if count_rows else 0
 
        # Query 3 — most hostile country (lowest avg AvgTone, min 10 events)
        sql_hostile = f"""
            SELECT ActionGeo_CountryCode AS country_code
            FROM read_parquet('{self._parquet_glob}')
            WHERE {where_clause} AND AvgTone IS NOT NULL
            GROUP BY ActionGeo_CountryCode
            HAVING COUNT(*) >= 10
            ORDER BY AVG(AvgTone) ASC
            LIMIT 1
        """
        hostile_rows = self._query(sql_hostile, params)
        most_hostile_cc = hostile_rows[0]["country_code"] if hostile_rows else None
 
        avg_tone = g.get("avg_global_tone")
        conflict_ratio = float(g.get("conflict_ratio") or 0.0)
 
        total = int(g.get("total_events") or 0)
        sentiment = {
            "hostile": 0.0,
            "neutral": 0.0,
            "positive": 0.0
        }
        if total > 0:
            sentiment["hostile"] = round((float(g.get("hostile_count") or 0) / total) * 100, 1)
            sentiment["neutral"] = round((float(g.get("neutral_count") or 0) / total) * 100, 1)
            sentiment["positive"] = round((float(g.get("positive_count") or 0) / total) * 100, 1)

        return {
            "total_events_today": total,
            "most_active_country": most_active_cc,
            "most_active_display": lookup_service.get_country_display(most_active_cc) if most_active_cc else None,
            "most_active_count": most_active_count,
            "most_hostile_country": most_hostile_cc,
            "most_hostile_display": lookup_service.get_country_display(most_hostile_cc) if most_hostile_cc else None,
            "avg_global_tone": float(avg_tone) if avg_tone is not None else None,
            "global_conflict_ratio": conflict_ratio,
            "sentiment": sentiment,
        }
 
    # ------------------------------------------------------------------
    # 15.2 — Top Threat Countries
    # ------------------------------------------------------------------
 
    def get_top_threat_countries(
        self,
        start_date: date,
        end_date: date,
        limit: int = 5,
        geo_country: str | None = None,
        geo_state: str | None = None,
        geo_city: str | None = None,
        theme_category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the top-N countries ranked by computed risk score."""
        start_int, end_exclusive_int = self._sql_date_bounds(start_date, end_date)

        where_clauses = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "ActionGeo_CountryCode IS NOT NULL",
            "ActionGeo_CountryCode != ''",
        ]
        params: list[Any] = [start_int, end_exclusive_int]

        if geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        self._apply_geo_state_city_filter(
            where_clauses,
            params,
            geo_state,
            geo_city,
        )

        sql = f"""
            SELECT
                ActionGeo_CountryCode                                                     AS country_code,
                COUNT(*)                                                                  AS total_events,
                SUM(CASE WHEN QuadClass IN (3, 4) THEN 1 ELSE 0 END) * 1.0 / COUNT(*)   AS conflict_ratio,
                AVG(GoldsteinScale)                                                       AS avg_goldstein,
                AVG(AvgTone)                                                              AS avg_tone
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where_clauses)}
            GROUP BY ActionGeo_CountryCode
            ORDER BY total_events DESC
            LIMIT 50
        """
        rows = self._query(sql, params)
 
        scored: list[dict[str, Any]] = []
        for row in rows:
            cc = row["country_code"]
            cr = float(row.get("conflict_ratio") or 0.0)
            gs = row.get("avg_goldstein")
            at = row.get("avg_tone")
            score = compute_risk_score(cr, gs, at)
            scored.append({
                "country_code": cc,
                "country_name": lookup_service.get_country_name(cc),
                "country_display": lookup_service.get_country_display(cc),
                "score": score,
                "avg_goldstein": gs,
                "conflict_ratio": cr,
                "total_events": int(row.get("total_events") or 0),
            })
 
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]
 
    # ------------------------------------------------------------------
    # Week-over-week Deltas
    # ------------------------------------------------------------------

    def get_analytics_deltas(self) -> dict[str, Any]:
        """Calculate WoW deltas for the top 20 countries by event volume."""
        today = date.today()
        seven_days_ago = today - timedelta(days=7)
        fourteen_days_ago = today - timedelta(days=14)

        start_7d, end_7d = self._sql_date_bounds(seven_days_ago, today)
        start_prior, end_prior = self._sql_date_bounds(fourteen_days_ago, seven_days_ago - timedelta(days=1))

        sql_top_20 = f"""
            SELECT ActionGeo_CountryCode, COUNT(*) as cnt
            FROM read_parquet('{self._parquet_glob}')
            WHERE SQLDATE >= ? AND SQLDATE <= ?
              AND ActionGeo_CountryCode IS NOT NULL
              AND ActionGeo_CountryCode != ''
            GROUP BY ActionGeo_CountryCode
            ORDER BY cnt DESC
            LIMIT 20
        """
        top_20_rows = self._query(sql_top_20, [int(fourteen_days_ago.strftime("%Y%m%d")), int(today.strftime("%Y%m%d"))])
        top_20_ccs = [row["ActionGeo_CountryCode"] for row in top_20_rows]

        if not top_20_ccs:
            return {}

        def get_stats(start_int: int, end_int: int):
            sql_stats = f"""
                SELECT
                    ActionGeo_CountryCode,
                    COUNT(*) AS total_events,
                    SUM(CASE WHEN QuadClass IN (3, 4) THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS conflict_ratio,
                    AVG(GoldsteinScale) AS avg_goldstein,
                    AVG(AvgTone) AS avg_tone
                FROM read_parquet('{self._parquet_glob}')
                WHERE SQLDATE >= ? AND SQLDATE < ?
                  AND ActionGeo_CountryCode IN ({','.join(['?' for _ in top_20_ccs])})
                GROUP BY ActionGeo_CountryCode
            """
            return self._query(sql_stats, [start_int, end_int, *top_20_ccs])

        stats_7d = {row["ActionGeo_CountryCode"]: row for row in get_stats(start_7d, end_7d)}
        stats_prior = {row["ActionGeo_CountryCode"]: row for row in get_stats(start_prior, end_prior)}

        deltas = {}
        for cc in top_20_ccs:
            curr = stats_7d.get(cc, {"total_events": 0, "conflict_ratio": 0.0, "avg_goldstein": 0.0, "avg_tone": 0.0})
            prev = stats_prior.get(cc, {"total_events": 0, "conflict_ratio": 0.0, "avg_goldstein": 0.0, "avg_tone": 0.0})

            e_curr = curr["total_events"]
            e_prev = prev["total_events"]
            event_delta_pct = ((e_curr - e_prev) / max(1, e_prev)) * 100

            c_curr = float(curr["conflict_ratio"] or 0.0)
            c_prev = float(prev["conflict_ratio"] or 0.0)
            conflict_delta = (c_curr - c_prev) * 100

            t_curr = float(curr["avg_tone"] or 0.0)
            t_prev = float(prev["avg_tone"] or 0.0)
            tone_delta = t_curr - t_prev

            s_curr = compute_risk_score(c_curr, curr["avg_goldstein"], t_curr)
            s_prev = compute_risk_score(c_prev, prev["avg_goldstein"], t_prev)
            score_delta = s_curr - s_prev

            deltas[cc] = {
                "event_delta_pct": round(event_delta_pct, 1),
                "conflict_delta": round(conflict_delta, 1),
                "tone_delta": round(tone_delta, 1),
                "score_delta": int(score_delta),
            }

        return deltas

    # ------------------------------------------------------------------
    # PHASE 4 — Activity Spikes & Anomalies
    # ------------------------------------------------------------------

    def get_activity_spikes(self) -> list[dict[str, Any]]:
        """Identifies countries with >= 2.0x event spike vs 7-day average."""
        today = date.today()
        yesterday = today - timedelta(days=1)
        seven_days_ago = today - timedelta(days=8)

        today_int = int(today.strftime("%Y%m%d"))
        yesterday_int = int(yesterday.strftime("%Y%m%d"))
        baseline_start_int = int(seven_days_ago.strftime("%Y%m%d"))

        sql_current = f"""
            SELECT ActionGeo_CountryCode, COUNT(*) as cnt
            FROM read_parquet('{self._parquet_glob}')
            WHERE SQLDATE IN (?, ?)
              AND ActionGeo_CountryCode IS NOT NULL
              AND ActionGeo_CountryCode != ''
            GROUP BY ActionGeo_CountryCode
        """
        current_rows = self._query(sql_current, [yesterday_int, today_int])
        current_map = {row["ActionGeo_CountryCode"]: row["cnt"] for row in current_rows}

        if not current_map:
            return []

        sql_baseline = f"""
            SELECT 
                ActionGeo_CountryCode, 
                COUNT(*) * 1.0 / 7.0 as avg_daily
            FROM read_parquet('{self._parquet_glob}')
            WHERE SQLDATE >= ? AND SQLDATE < ?
              AND ActionGeo_CountryCode IS NOT NULL
              AND ActionGeo_CountryCode != ''
            GROUP BY ActionGeo_CountryCode
        """
        baseline_rows = self._query(sql_baseline, [baseline_start_int, yesterday_int])
        baseline_map = {row["ActionGeo_CountryCode"]: row["avg_daily"] for row in baseline_rows}

        spikes = []
        for cc, count in current_map.items():
            baseline = baseline_map.get(cc, 0)
            if baseline > 0:
                ratio = count / baseline
                if ratio >= 2.0 and count >= 10:
                    sql_top = f"""
                        SELECT EventRootCode, COUNT(*) as c
                        FROM read_parquet('{self._parquet_glob}')
                        WHERE SQLDATE IN (?, ?) AND ActionGeo_CountryCode = ?
                        GROUP BY EventRootCode
                        ORDER BY c DESC
                        LIMIT 1
                    """
                    top_rows = self._query(sql_top, [yesterday_int, today_int, cc])
                    top_code = top_rows[0]["EventRootCode"] if top_rows else None

                    spikes.append({
                        "country_code": cc,
                        "country_name": lookup_service.get_country_name(cc),
                        "country_display": lookup_service.get_country_display(cc),
                        "events_24h": count,
                        "baseline_avg": round(baseline, 1),
                        "spike_ratio": round(ratio, 2),
                        "top_cameo_root": top_code
                    })

        spikes.sort(key=lambda x: x["spike_ratio"], reverse=True)
        return spikes

    def get_anomalies(self) -> dict[str, Any]:
        """Returns pre-computed anomaly detection results from cache."""
        cache_path = Path(self._settings.cache_path) / "anomalies.json"
        if not cache_path.exists():
            return {}
        
        try:
            with cache_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("failed_to_load_anomalies_cache", error=str(e))
            return {}

    def get_briefings(self) -> dict[str, Any]:
        """Returns pre-computed nightly country briefings from cache."""
        cache_path = Path(self._settings.cache_path) / "briefings.json"
        if not cache_path.exists():
            return {}

        try:
            with cache_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                return payload
            logger.warning("briefings_cache_invalid_shape", expected="dict", actual=type(payload).__name__)
            return {}
        except Exception as e:
            logger.error("failed_to_load_briefings_cache", error=str(e))
            return {}

    def get_geo_drill(
        self,
        start_date=None,
        end_date=None,
        country_code: str | None = None,
        state_name: str | None = None,
    ) -> dict:
        from backend.infrastructure.services.reverse_geocode_service import reverse_geocode_service

        start_date, end_date = self._resolve_dates(
            EventFilter(start_date=start_date, end_date=end_date)
        )
        start_int, end_excl = self._sql_date_bounds(start_date, end_date)

        if not country_code:
            sql = f"""
                SELECT ActionGeo_CountryCode, COUNT(*) as event_count
                FROM read_parquet('{self._parquet_glob}')
                WHERE SQLDATE >= ?
                  AND SQLDATE < ?
                  AND ActionGeo_CountryCode IS NOT NULL
                  AND ActionGeo_CountryCode != ''
                GROUP BY ActionGeo_CountryCode
                ORDER BY event_count DESC
                LIMIT 40
            """
            rows = self._query(sql, [start_int, end_excl])
            items = [
                {
                    "name": lookup_service.get_country_name(row["ActionGeo_CountryCode"]) or row["ActionGeo_CountryCode"],
                    "code": row["ActionGeo_CountryCode"],
                    "display": lookup_service.get_country_display(row["ActionGeo_CountryCode"]) or row["ActionGeo_CountryCode"],
                    "count": row["event_count"],
                }
                for row in rows
            ]
            return {
                "level": "country",
                "items": items,
            }

        where = [
            "SQLDATE >= ?",
            "SQLDATE < ?",
            "ActionGeo_Lat IS NOT NULL",
            "ActionGeo_Long IS NOT NULL",
        ]
        params: list[Any] = [start_int, end_excl]

        if country_code:
            where.append("ActionGeo_CountryCode = ?")
            params.append(country_code.upper())

        availability_sql = f"""
            SELECT COUNT(*) AS available_count
            FROM read_parquet('{self._parquet_glob}')
            WHERE SQLDATE >= ?
              AND SQLDATE < ?
              AND ActionGeo_CountryCode = ?
              AND ActionGeo_Lat IS NOT NULL
              AND ActionGeo_Long IS NOT NULL
        """
        availability_rows = self._query(
            availability_sql, [start_int, end_excl, country_code.upper()]
        )
        state_available = bool(availability_rows and availability_rows[0]["available_count"])
        state_reason = None
        if not state_available:
            state_reason = (
                "State drill requires ActionGeo lat/long; refresh hot-tier data (daily pull or realtime fetch)."
            )

        sql = f"""
            SELECT ActionGeo_Lat, ActionGeo_Long, ActionGeo_CountryCode, COUNT(*) as event_count
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where)}
            GROUP BY ActionGeo_Lat, ActionGeo_Long, ActionGeo_CountryCode
            ORDER BY event_count DESC
            LIMIT 2000
        """
        rows = self._query(sql, params) if state_available else []

        coords = [(r["ActionGeo_Lat"], r["ActionGeo_Long"]) for r in rows]
        geo_results = reverse_geocode_service.lookup_batch(coords)

        from collections import defaultdict

        counts: dict = defaultdict(int)

        for row, geo in zip(rows, geo_results):
            count = row["event_count"]
            if not country_code:
                key = geo["country_code"] or row.get("ActionGeo_CountryCode", "")
                if key:
                    counts[key] += count
            elif not state_name:
                key = geo["state"]
                if key:
                    counts[key] += count
            else:
                if geo["state"] == state_name:
                    key = geo["city"]
                    if key:
                        counts[key] += count

        limit = 20 if not state_name else 15
        sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]

        items = [{"name": k, "count": v} for k, v in sorted_items]

        payload = {
            "level": "state" if not state_name else "city",
            "items": items,
            "state_available": state_available,
        }
        if state_reason:
            payload["state_reason"] = state_reason
        return payload

    def get_daily_trend(
        self,
        start_date: date,
        end_date: date,
        event_root_codes: list[str] | None = None,
        geo_country: str | None = None,
        geo_state: str | None = None,
        geo_city: str | None = None,
        theme_category: str | None = None,
    ) -> list[dict]:
        """Return per-day total events vs conflict events for the given window.

        Conflict = QuadClass >= 3 (Material/Verbal Conflict).
        Returns [{date, total, conflict}] sorted ascending.
        """
        start_int = int(start_date.strftime("%Y%m%d"))
        end_excl = end_date + timedelta(days=1)
        end_int = int(end_excl.strftime("%Y%m%d"))

        where_clauses = ["SQLDATE >= ?", "SQLDATE < ?"]
        params: list[Any] = [start_int, end_int]

        if event_root_codes:
            placeholders = ", ".join(["?" for _ in event_root_codes])
            where_clauses.append(f"EventRootCode IN ({placeholders})")
            params.extend(event_root_codes)

        if geo_country:
            where_clauses.append("ActionGeo_CountryCode = ?")
            params.append(geo_country.upper())

        theme_clause, theme_params = _build_theme_filter(theme_category)
        if theme_clause:
            where_clauses.append(theme_clause)
            params.extend(theme_params)

        self._apply_geo_state_city_filter(
            where_clauses,
            params,
            geo_state,
            geo_city,
        )

        sql = f"""
            SELECT
                CAST(SQLDATE AS VARCHAR) AS day,
                COUNT(*)                 AS total,
                SUM(CASE WHEN QuadClass >= 3 THEN 1 ELSE 0 END) AS conflict
            FROM read_parquet('{self._parquet_glob}')
            WHERE {' AND '.join(where_clauses)}
            GROUP BY SQLDATE
            ORDER BY SQLDATE ASC
        """
        try:
            rows = self._query(sql, params)
        except Exception as exc:
            logger.error("get_daily_trend_failed", error=str(exc))
            return []

        result = []
        for row in rows:
            day_str = str(row.get("day"))
            formatted = f"{day_str[:4]}-{day_str[4:6]}-{day_str[6:8]}" if len(day_str) == 8 else day_str
            result.append({
                "date": formatted,
                "total": int(row.get("total") or 0),
                "conflict": int(row.get("conflict") or 0),
            })
        return result

