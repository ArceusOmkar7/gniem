"""Nightly AI precompute job for forecasts, country briefings, and anomaly detection.

This script reads hot-tier parquet data and produces:
1) CACHE_PATH/forecasts.parquet (30-day predictions for top countries)
2) CACHE_PATH/briefings.json (country-level briefing text cache)
3) CACHE_PATH/anomalies.json (IsolationForest anomaly detection results)
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import httpx
import pandas as pd
import numpy as np
from sklearn.ensemble import IsolationForest

from backend.domain.models.event import EventCountByDate
from backend.domain.services.forecasting_service import ForecastingService
from backend.infrastructure.config.settings import Settings

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


def sql_date_int(d: date) -> int:
    """Convert date to SQLDATE integer format (YYYYMMDD)."""
    return int(d.strftime("%Y%m%d"))


def sql_date_to_date(value: int) -> date:
    """Convert SQLDATE integer format (YYYYMMDD) to date."""
    raw = str(int(value))
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))


def latest_available_sql_date(
    conn: duckdb.DuckDBPyConnection,
    parquet_glob: str,
) -> date:
    """Return latest SQLDATE available in hot-tier parquet files."""
    row = conn.execute(
        f"""
        SELECT MAX(SQLDATE) AS max_sql_date
        FROM read_parquet('{parquet_glob}')
        WHERE SQLDATE IS NOT NULL
        """
    ).fetchone()

    if not row or row[0] is None:
        raise ValueError("Could not resolve latest SQLDATE from hot-tier parquet files.")

    return sql_date_to_date(int(row[0]))


def top_countries_by_volume(
    conn: duckdb.DuckDBPyConnection,
    parquet_glob: str,
    start_sql_date: int,
    end_sql_date_exclusive: int,
    limit: int,
) -> list[str]:
    """Get top countries by ActionGeo event volume in given window."""
    rows = conn.execute(
        f"""
        SELECT
            ActionGeo_CountryCode AS country_code,
            COUNT(*) AS event_count
        FROM read_parquet('{parquet_glob}')
        WHERE SQLDATE >= ?
          AND SQLDATE < ?
          AND ActionGeo_CountryCode IS NOT NULL
          AND ActionGeo_CountryCode <> ''
        GROUP BY ActionGeo_CountryCode
        ORDER BY event_count DESC
        LIMIT ?
        """,
        [start_sql_date, end_sql_date_exclusive, limit],
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def fetch_conflict_daily_counts(
    conn: duckdb.DuckDBPyConnection,
    parquet_glob: str,
    country_code: str,
    start_sql_date: int,
    end_sql_date_exclusive: int,
) -> list[EventCountByDate]:
    """Get daily conflict counts (QuadClass 3/4) for forecasting."""
    rows = conn.execute(
        f"""
        SELECT
            SQLDATE,
            COUNT(*) AS event_count,
            AVG(GoldsteinScale) AS avg_goldstein,
            SUM(NumMentions) AS total_mentions,
            AVG(AvgTone) AS avg_tone
        FROM read_parquet('{parquet_glob}')
        WHERE SQLDATE >= ?
          AND SQLDATE < ?
          AND ActionGeo_CountryCode = ?
          AND QuadClass IN (3, 4)
        GROUP BY SQLDATE
        ORDER BY SQLDATE ASC
        """,
        [start_sql_date, end_sql_date_exclusive, country_code],
    ).fetchall()

    out: list[EventCountByDate] = []
    for sql_date_raw, event_count, avg_goldstein, total_mentions, avg_tone in rows:
        sql_date_str = str(int(sql_date_raw))
        parsed_date = date(
            int(sql_date_str[:4]),
            int(sql_date_str[4:6]),
            int(sql_date_str[6:8]),
        )
        out.append(
            EventCountByDate(
                date=parsed_date,
                count=int(event_count),
                avg_goldstein_scale=(float(avg_goldstein) if avg_goldstein is not None else None),
                total_mentions=int(total_mentions or 0),
                avg_tone=(float(avg_tone) if avg_tone is not None else None),
            )
        )
    return out


def build_country_event_summary(
    conn: duckdb.DuckDBPyConnection,
    parquet_glob: str,
    country_code: str,
    start_sql_date: int,
    end_sql_date_exclusive: int,
) -> str:
    """Build a compact factual summary for a country's recent events."""
    metrics = conn.execute(
        f"""
        SELECT
            COUNT(*) AS event_count,
            AVG(GoldsteinScale) AS avg_goldstein,
            AVG(AvgTone) AS avg_tone,
            SUM(NumMentions) AS total_mentions,
            SUM(NumSources) AS total_sources
        FROM read_parquet('{parquet_glob}')
        WHERE SQLDATE >= ?
          AND SQLDATE < ?
          AND ActionGeo_CountryCode = ?
        """,
        [start_sql_date, end_sql_date_exclusive, country_code],
    ).fetchone()

    top_codes = conn.execute(
        f"""
        SELECT EventRootCode, COUNT(*) AS c
        FROM read_parquet('{parquet_glob}')
        WHERE SQLDATE >= ?
          AND SQLDATE < ?
          AND ActionGeo_CountryCode = ?
          AND EventRootCode IS NOT NULL
          AND EventRootCode <> ''
        GROUP BY EventRootCode
        ORDER BY c DESC
        LIMIT 5
        """,
        [start_sql_date, end_sql_date_exclusive, country_code],
    ).fetchall()

    event_count = int(metrics[0] or 0) if metrics else 0
    avg_goldstein = float(metrics[1]) if metrics and metrics[1] is not None else 0.0
    avg_tone = float(metrics[2]) if metrics and metrics[2] is not None else 0.0
    total_mentions = int(metrics[3] or 0) if metrics else 0
    total_sources = int(metrics[4] or 0) if metrics else 0
    top_event_codes = [str(row[0]) for row in top_codes if row and row[0]]

    return (
        f"Country {country_code}. "
        f"Recent events: {event_count}. "
        f"Avg Goldstein: {avg_goldstein:.2f}. "
        f"Avg tone: {avg_tone:.2f}. "
        f"Total mentions: {total_mentions}. "
        f"Total sources: {total_sources}. "
        f"Top root codes: {', '.join(top_event_codes) if top_event_codes else 'n/a'}."
    )


def fallback_briefing(country_code: str, summary: str) -> str:
    """Deterministic briefing used when external LLM call is unavailable."""
    return (
        f"Geopolitical briefing for {country_code}: "
        f"{summary} "
        "Interpretation: monitor changes in tone, Goldstein score, and event mix "
        "for escalation or de-escalation signals."
    )


def load_gdelt_country_code_labels(codes_file: Path) -> dict[str, str]:
    """Load GDELT CAMEO country/region code labels from tab-separated file."""
    if not codes_file.exists():
        return {}

    labels: dict[str, str] = {}
    with codes_file.open("r", encoding="utf-8") as handle:
        for i, line in enumerate(handle):
            raw = line.strip()
            if not raw:
                continue
            if i == 0 and raw.upper().startswith("CODE"):
                continue
            parts = raw.split("\t", maxsplit=1)
            if len(parts) != 2:
                continue
            code = parts[0].strip().upper()
            label = parts[1].strip()
            if code and label:
                labels[code] = label
    return labels


def resolve_country_codes_file_path(configured_path: str, repo_root: Path) -> Path:
    """Resolve configured country-code file path to an absolute path."""
    configured = Path(configured_path)
    if configured.is_absolute():
        return configured
    return repo_root / configured


async def ensure_country_codes_file(
    configured_path: str,
    configured_url: str,
    repo_root: Path,
) -> Path | None:
    """Ensure country-code lookup file exists; download from configured URL if missing."""
    codes_file = resolve_country_codes_file_path(configured_path, repo_root)
    if codes_file.exists():
        return codes_file

    codes_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(configured_url)
            response.raise_for_status()
        codes_file.write_text(response.text, encoding="utf-8")
        print(f"Downloaded country code lookup to {codes_file}")
        return codes_file
    except Exception as exc:
        print(f"Could not download country code lookup from {configured_url}: {exc}")
        return None


async def load_country_labels_for_actiongeo(
    settings: Settings,
    repo_root: Path,
) -> dict[str, str]:
    """Load label map for ActionGeo 2-letter codes, fallback to CAMEO map if needed."""
    action_file = await ensure_country_codes_file(
        configured_path=settings.action_geo_country_codes_path,
        configured_url=settings.action_geo_country_codes_url,
        repo_root=repo_root,
    )
    action_labels = load_gdelt_country_code_labels(action_file) if action_file else {}
    if action_labels:
        return action_labels

    cameo_file = await ensure_country_codes_file(
        configured_path=settings.cameo_country_codes_path,
        configured_url=settings.cameo_country_codes_url,
        repo_root=repo_root,
    )
    cameo_labels = load_gdelt_country_code_labels(cameo_file) if cameo_file else {}
    if cameo_labels:
        return cameo_labels

    legacy_file = await ensure_country_codes_file(
        configured_path=settings.gdelt_country_codes_path,
        configured_url=settings.gdelt_country_codes_url,
        repo_root=repo_root,
    )
    return load_gdelt_country_code_labels(legacy_file) if legacy_file else {}


def is_low_quality_briefing(text: str) -> bool:
    """Reject generic or code-confusion responses that degrade briefing quality."""
    patterns = [
        r"unable to (determine|verify)",
        r"not a recognized country code",
        r"could refer to",
        r"assuming (it )?refers to",
        r"without more context",
        r"appears to be (a code|coded|nonsensical|cryptic)",
    ]
    normalized = text.lower()
    return any(re.search(p, normalized) for p in patterns)


@dataclass
class GroqBriefingClient:
    """Small async client for Groq Chat Completions API."""

    api_key: str
    model: str = "llama-3.3-70b-versatile"
    codebook_context: str = ""
    base_url: str = "https://api.groq.com/openai/v1"
    fallback_model: str | None = None

    async def generate_briefing(
        self,
        country_code: str,
        country_label: str,
        summary: str,
    ) -> str | None:
        prompt = (
            "Use only the provided structured metrics to generate a concise geopolitical "
            "briefing.\n"
            f"Code: {country_code}\n"
            f"Label: {country_label}\n"
            f"Metrics: {summary}\n"
            "Rules:"
            " 1) Treat code+label as authoritative; do not reinterpret the location."
            " 2) Do not claim the code is invalid or ambiguous."
            " 3) Do not invent events, actors, or historical facts not present in metrics."
            " 4) Use the label in the first sentence and include the code in parentheses once."
            " 5) Keep output 90-130 words in one paragraph."
            " 6) Focus on trend/risk interpretation from tone, Goldstein, mentions, and event mix."
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a geopolitical analyst for GDELT ActionGeo data. "
                        "ActionGeo codes can be countries, regions, or city-level entities. "
                        "Use provided code+label exactly and stay grounded in provided metrics.\n\n"
                        "Authoritative code mapping for this run:\n"
                        f"{self.codebook_context or 'No mapping provided.'}"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 1024,
        }
        if self.model.startswith("openai/gpt-oss-"):
            payload["reasoning_effort"] = "low"
        if "openrouter.ai" in self.base_url:
            payload["reasoning"] = {"enabled": False}
            models = [payload.pop("model")]
            if self.fallback_model and self.fallback_model not in models:
                models.append(self.fallback_model)
            if not all(model.endswith(":free") for model in models):
                raise ValueError("OpenRouter briefing models must be free variants")
            payload["models"] = models

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            print(f"Groq briefing generation failed for {country_code}: {exc}")
            return None


def build_forecasts_dataframe(settings: Settings) -> pd.DataFrame:
    """Compute 30-day forecasts for top 50 countries and return as dataframe."""
    hot_dir = Path(settings.hot_tier_path)
    parquet_files = list(hot_dir.glob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in hot-tier path: {hot_dir}")

    conn = duckdb.connect(database=":memory:")
    parquet_glob = str(hot_dir / "*.parquet")

    anchor_date = latest_available_sql_date(conn, parquet_glob)
    forecast_horizon = 30
    training_start = anchor_date - timedelta(days=90)
    training_start_int = sql_date_int(training_start)
    end_exclusive_int = sql_date_int(anchor_date + timedelta(days=1))

    top_countries = top_countries_by_volume(
        conn,
        parquet_glob,
        training_start_int,
        end_exclusive_int,
        limit=50,
    )

    forecasting_service = ForecastingService()
    rows: list[dict] = []
    generated_at = datetime.now(UTC).isoformat()

    for country_code in top_countries:
        historical = fetch_conflict_daily_counts(
            conn,
            parquet_glob,
            country_code,
            training_start_int,
            end_exclusive_int,
        )
        result = forecasting_service.forecast(
            historical_counts=historical,
            horizon_days=forecast_horizon,
            country_code=country_code,
        )

        for point in result.predictions:
            rows.append(
                {
                    "country_code": country_code,
                    "date": point.date,
                    "predicted_count": point.predicted_count,
                    "lower_bound": point.lower_bound,
                    "upper_bound": point.upper_bound,
                    "model_type": result.model_type,
                    "horizon_days": forecast_horizon,
                    "generated_at": generated_at,
                }
            )

    conn.close()
    return pd.DataFrame(rows)


async def build_briefings_payload(settings: Settings) -> dict[str, dict[str, str]]:
    """Compute briefings for top 30 countries over last 7 days."""
    hot_dir = Path(settings.hot_tier_path)
    parquet_files = list(hot_dir.glob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in hot-tier path: {hot_dir}")

    conn = duckdb.connect(database=":memory:")
    parquet_glob = str(hot_dir / "*.parquet")

    anchor_date = latest_available_sql_date(conn, parquet_glob)
    lookback_start = anchor_date - timedelta(days=7)
    start_int = sql_date_int(lookback_start)
    end_exclusive_int = sql_date_int(anchor_date + timedelta(days=1))

    top_countries = top_countries_by_volume(
        conn,
        parquet_glob,
        start_int,
        end_exclusive_int,
        limit=30,
    )

    repo_root = Path(__file__).resolve().parents[1]
    code_labels = await load_country_labels_for_actiongeo(settings, repo_root)

    codebook_lines: list[str] = []
    for code in top_countries:
        normalized = code.upper()
        label = code_labels.get(normalized, normalized)
        codebook_lines.append(f"{normalized} -> {label}")
    codebook_context = "\n".join(codebook_lines)

    groq_client = None
    provider = "openrouter" if settings.openrouter_api_key else "groq"
    groq_key = settings.openrouter_api_key or settings.groq_api_key
    if isinstance(groq_key, str) and groq_key.strip():
        groq_client = GroqBriefingClient(
            api_key=groq_key.strip(),
            model=(settings.openrouter_model_name if provider == "openrouter" else settings.groq_model_name),
            base_url=("https://openrouter.ai/api/v1" if provider == "openrouter" else "https://api.groq.com/openai/v1"),
            fallback_model=settings.openrouter_fallback_model_name if provider == "openrouter" else None,
            codebook_context=codebook_context,
        )

    payload: dict[str, dict[str, str]] = {}
    generated_at = datetime.now(UTC).isoformat()

    for country_code in top_countries:
        normalized_code = country_code.upper()
        country_label = code_labels.get(normalized_code, normalized_code)
        summary = build_country_event_summary(
            conn,
            parquet_glob,
            country_code,
            start_int,
            end_exclusive_int,
        )

        briefing_text = None
        source = "fallback"
        if groq_client is not None:
            briefing_text = await groq_client.generate_briefing(
                country_code=normalized_code,
                country_label=country_label,
                summary=summary,
            )
            if briefing_text:
                source = provider
            # Free OpenRouter models share a 20-request/minute platform limit.
            if provider == "openrouter":
                await asyncio.sleep(3.2)

        if briefing_text and is_low_quality_briefing(briefing_text):
            source = "fallback_invalid_groq"
            briefing_text = None

        if not briefing_text:
            briefing_text = fallback_briefing(country_code, summary)

        payload[country_code] = {
            "briefing": briefing_text,
            "generated_at": generated_at,
            "source": source,
            "summary": summary,
        }

    conn.close()
    return payload


def run_anomaly_detection(settings: Settings) -> dict[str, dict]:
    """Run IsolationForest anomaly detection for all countries with enough data."""
    hot_dir = Path(settings.hot_tier_path)
    parquet_glob = str(hot_dir / "*.parquet")
    conn = duckdb.connect(database=":memory:")

    anchor_date = latest_available_sql_date(conn, parquet_glob)
    anchor_int = sql_date_int(anchor_date)
    lookback_90d = anchor_date - timedelta(days=90)
    lookback_90d_int = sql_date_int(lookback_90d)

    # 1. Fetch daily features for all countries
    sql = f"""
        SELECT
            ActionGeo_CountryCode AS country_code,
            SQLDATE,
            COUNT(*) AS event_count,
            AVG(CASE WHEN QuadClass IN (3, 4) THEN 1 ELSE 0 END) AS conflict_ratio,
            AVG(GoldsteinScale) AS avg_goldstein,
            AVG(AvgTone) AS avg_tone,
            SUM(NumMentions) AS num_mentions_sum
        FROM read_parquet('{parquet_glob}')
        WHERE SQLDATE >= ? AND SQLDATE <= ?
          AND ActionGeo_CountryCode IS NOT NULL
          AND ActionGeo_CountryCode != ''
        GROUP BY country_code, SQLDATE
        ORDER BY country_code, SQLDATE ASC
    """
    df = conn.execute(sql, [lookback_90d_int, anchor_int]).df()
    conn.close()

    if df.empty:
        return {}

    results = {}
    feature_cols = ["event_count", "conflict_ratio", "avg_goldstein", "avg_tone", "num_mentions_sum"]
    
    from backend.infrastructure.services.lookup_service import lookup_service

    for cc, group in df.groupby("country_code"):
        if len(group) < 14:
            continue
        
        # Fill NaNs
        group = group.fillna(0)
        
        # Score the most recent available day in the hot tier.
        anchor_row = group[group["SQLDATE"] == anchor_int]
        if anchor_row.empty:
            continue
            
        X = group[feature_cols].values
        
        # Fit IsolationForest
        clf = IsolationForest(contamination=0.05, random_state=42)
        clf.fit(X)
        
        # Score today
        anchor_X = anchor_row[feature_cols].values
        score = float(clf.decision_function(anchor_X)[0])
        is_anomaly = score < -0.1
        
        reason = None
        if is_anomaly:
            # Compare features to historical mean/std
            means = group[feature_cols].mean()
            stds = group[feature_cols].std().replace(0, 1) # avoid div by zero
            z_scores = (anchor_row[feature_cols].iloc[0] - means) / stds
            
            # Find most deviant feature
            z_abs = np.abs(z_scores.values)
            most_deviant_idx = np.argmax(z_abs)
            feat_name = feature_cols[most_deviant_idx]
            z_val = z_scores[feat_name]
            
            feat_display = feat_name.replace("_", " ").capitalize()
            reason = f"{feat_display} {abs(z_val):.1f}σ {'above' if z_val > 0 else 'below'} mean"

        results[cc] = {
            "country_name": lookup_service.get_country_name(cc),
            "country_display": lookup_service.get_country_display(cc),
            "is_anomaly": bool(is_anomaly),
            "score": round(score, 3),
            "reason": reason
        }

    return results


def build_theme_category_counts(settings: Settings) -> dict:
    """
    Count events per theme category by scanning the 'themes' column
    in hot-tier parquet (already populated from GKG via realtime_fetcher).
    Output: { "POLITICS": 12345, "ECONOMY": 8901, ... }
    Written to: data/cache/theme_categories.json
    """
    parquet_glob = str(Path(settings.hot_tier_path) / "*.parquet")
    conn = duckdb.connect(database=":memory:")

    try:
        anchor_date = latest_available_sql_date(conn, parquet_glob)
        start_date = anchor_date - timedelta(days=7)
        start_int = sql_date_int(start_date)
        end_int = sql_date_int(anchor_date + timedelta(days=1))

        theme_type = conn.execute(
            f"DESCRIBE SELECT themes FROM read_parquet('{parquet_glob}')"
        ).fetchone()[1]
        theme_array = (
            "string_split(themes, ';')"
            if theme_type == "VARCHAR" else "themes"
        )
        # Keep the existing first-matching-category rule and duplicate counts,
        # while aggregating inside DuckDB instead of materializing every theme.
        cases = []
        for category, prefixes in THEME_CATEGORY_MAP.items():
            checks = " OR ".join(
                "contains(upper(theme), '" + prefix.replace("'", "''") + "')"
                for prefix in prefixes
            )
            cases.append(f"WHEN {checks} THEN '{category}'")
        rows = conn.execute(
            f"""
            WITH source AS (
                SELECT themes FROM read_parquet('{parquet_glob}')
                WHERE SQLDATE >= ? AND SQLDATE < ? AND themes IS NOT NULL
            ), expanded AS (
                SELECT unnest({theme_array}) AS theme FROM source
            ), classified AS (
                SELECT CASE {' '.join(cases)} END AS category FROM expanded
            )
            SELECT category, COUNT(*) FROM classified
            WHERE category IS NOT NULL GROUP BY category
            """, [start_int, end_int]
        ).fetchall()
    finally:
        conn.close()

    category_counts = {cat: 0 for cat in THEME_CATEGORY_MAP}

    category_counts.update({category: int(count) for category, count in rows})

    return category_counts


def run_nightly_ai() -> tuple[Path, Path, Path]:
    """Run nightly forecast, briefing, and anomaly precompute and write cache outputs."""
    settings = Settings()
    cache_dir = Path(settings.cache_path)
    cache_dir.mkdir(parents=True, exist_ok=True)

    forecasts_df = build_forecasts_dataframe(settings)
    forecasts_path = cache_dir / "forecasts.parquet"
    forecasts_df.to_parquet(forecasts_path, index=False)

    briefings = asyncio.run(build_briefings_payload(settings))
    briefings_path = cache_dir / "briefings.json"
    with briefings_path.open("w", encoding="utf-8") as handle:
        json.dump(briefings, handle, indent=2)

    anomalies = run_anomaly_detection(settings)
    anomalies_path = cache_dir / "anomalies.json"
    with anomalies_path.open("w", encoding="utf-8") as handle:
        json.dump(anomalies, handle, indent=2)

    theme_categories = build_theme_category_counts(settings)
    theme_categories_path = cache_dir / "theme_categories.json"
    with theme_categories_path.open("w", encoding="utf-8") as handle:
        json.dump(theme_categories, handle, indent=2)

    print(
        f"Nightly AI success: forecasts={forecasts_path}, briefings={briefings_path}, "
        f"anomalies={anomalies_path}, theme_categories={theme_categories_path}, countries={len(briefings)}"
    )
    return forecasts_path, briefings_path, anomalies_path


if __name__ == "__main__":
    run_nightly_ai()
