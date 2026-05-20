"""15-minute realtime ingestion job for GDELT Events, Mentions, and GKG CSV feeds.

This script reads lastupdate.txt, pulls the latest Events, Mentions, and GKG 
CSV zips, joins them in pandas to provide enriched insights (themes, persons, orgs),
deduplicates by GLOBALEVENTID, and appends to realtime_buffer.parquet.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import duckdb
import httpx
import pandas as pd

from backend.infrastructure.config.settings import Settings

EVENTS_COLUMNS: list[str] = [
    "GLOBALEVENTID",
    "SQLDATE",
    "MonthYear",
    "Year",
    "Actor1CountryCode",
    "Actor2CountryCode",
    "Actor1Type1Code",
    "Actor2Type1Code",
    "EventCode",
    "EventBaseCode",
    "EventRootCode",
    "QuadClass",
    "GoldsteinScale",
    "NumMentions",
    "NumSources",
    "AvgTone",
    "Actor1Geo_CountryCode",
    "Actor2Geo_CountryCode",
    "ActionGeo_CountryCode",
    "ActionGeo_Lat",
    "ActionGeo_Long",
    "SOURCEURL",
    "themes",
    "persons",
    "organizations",
    "mentions_count",
    "avg_confidence",
]

# GDELT Events 2.1 CSV positional indexes.
EVENT_COLUMN_INDEX: dict[str, int] = {
    "GLOBALEVENTID": 0,
    "SQLDATE": 1,
    "MonthYear": 2,
    "Year": 3,
    "Actor1CountryCode": 7,
    "Actor2CountryCode": 17,
    "Actor1Type1Code": 12,
    "Actor2Type1Code": 22,
    "EventCode": 26,
    "EventBaseCode": 27,
    "EventRootCode": 28,
    "QuadClass": 29,
    "GoldsteinScale": 30,
    "NumMentions": 31,
    "NumSources": 32,
    "AvgTone": 34,
    "Actor1Geo_CountryCode": 37,
    "Actor2Geo_CountryCode": 45,
    "ActionGeo_CountryCode": 53,
    "ActionGeo_Lat": 56,
    "ActionGeo_Long": 57,
    "SOURCEURL": 60,
}

# GKG 2.1 CSV positional indexes.
GKG_COLUMN_INDEX: dict[str, int] = {
    "DocumentIdentifier": 4,
    "Themes": 7,
    "Persons": 11,
    "Organizations": 13,
}

# Mentions 2.1 CSV positional indexes.
MENTIONS_COLUMN_INDEX: dict[str, int] = {
    "GLOBALEVENTID": 0,
    "MentionIdentifier": 5,
    "Confidence": 11,
}

NUMERIC_COLUMNS = {
    "GLOBALEVENTID",
    "SQLDATE",
    "MonthYear",
    "Year",
    "QuadClass",
    "GoldsteinScale",
    "NumMentions",
    "NumSources",
    "AvgTone",
    "ActionGeo_Lat",
    "ActionGeo_Long",
    "mentions_count",
    "avg_confidence",
}


def parse_lastupdate_urls(lastupdate_text: str) -> dict[str, str]:
    """Extract Events, Mentions, and GKG export URLs from lastupdate.txt."""
    urls = {}
    for raw_line in lastupdate_text.strip().splitlines():
        line = raw_line.strip()
        if "export.CSV" in line:
            urls["events"] = line.split()[-1]
        elif "mentions.CSV" in line:
            urls["mentions"] = line.split()[-1]
        elif "gkg.csv" in line.lower():
            urls["gkg"] = line.split()[-1]
    return urls


def parse_lastupdate_events_url(lastupdate_text: str) -> str:
    """Extract the Events export URL from lastupdate.txt."""
    urls = parse_lastupdate_urls(lastupdate_text)
    if "events" not in urls:
        raise ValueError("Events export URL not found in lastupdate.txt")
    return urls["events"]


def fetch_csv_zip_to_df(url: str, col_map: dict[str, int], sep: str = "\t") -> pd.DataFrame:
    """Download, unzip, and parse a GDELT CSV into a projected DataFrame."""
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
                with archive.open(archive.namelist()[0]) as handle:
                    raw_df = pd.read_csv(handle, sep=sep, header=None, dtype=str, low_memory=False)
        
        projected = {}
        for col, idx in col_map.items():
            if idx < raw_df.shape[1]:
                projected[col] = raw_df.iloc[:, idx]
            else:
                projected[col] = None
        return pd.DataFrame(projected)
    except Exception as e:
        print(f"Failed to fetch/parse {url}: {e}")
        return pd.DataFrame()


def parse_events_zip_to_dataframe(zipped_bytes: bytes) -> pd.DataFrame:
    """Parse a zipped GDELT Events TSV payload into a projected DataFrame."""
    try:
        with zipfile.ZipFile(io.BytesIO(zipped_bytes)) as archive:
            with archive.open(archive.namelist()[0]) as handle:
                raw_df = pd.read_csv(handle, sep="\t", header=None, dtype=str, low_memory=False)
        projected = {}
        for col, idx in EVENT_COLUMN_INDEX.items():
            if idx < raw_df.shape[1]:
                projected[col] = raw_df.iloc[:, idx]
            else:
                projected[col] = None
        return pd.DataFrame(projected)
    except Exception as e:
        print(f"Failed to parse events zip payload: {e}")
        return pd.DataFrame()


def load_recent_event_ids(hot_tier_path: str, max_rows: int = 1000) -> set[int]:
    """Load up to max_rows recent event ids from local hot-tier parquet files."""
    hot_dir = Path(hot_tier_path)
    parquet_files = list(hot_dir.glob("*.parquet"))
    if not parquet_files:
        return set()

    glob_path = str(hot_dir / "*.parquet")
    conn = duckdb.connect(database=":memory:")
    try:
        rows = conn.execute(
            f"""
            SELECT CAST(GLOBALEVENTID AS BIGINT) AS event_id
            FROM read_parquet('{glob_path}')
            WHERE GLOBALEVENTID IS NOT NULL
            ORDER BY SQLDATE DESC
            LIMIT ?
            """,
            [max_rows],
        ).fetchall()
        return {int(row[0]) for row in rows if row and row[0] is not None}
    finally:
        conn.close()


def dedupe_against_recent(df: pd.DataFrame, recent_ids: set[int]) -> pd.DataFrame:
    """Drop incoming rows that already exist in recent hot-tier history."""
    if df.empty or not recent_ids:
        return df
    mask = ~df["GLOBALEVENTID"].astype("int64").isin(recent_ids)
    return df.loc[mask].copy()


def append_realtime_buffer(df: pd.DataFrame, hot_tier_path: str) -> Path:
    """Append deduped rows to realtime_buffer.parquet and dedupe within buffer."""
    hot_dir = Path(hot_tier_path)
    hot_dir.mkdir(parents=True, exist_ok=True)

    buffer_file = hot_dir / "realtime_buffer.parquet"
    if buffer_file.exists():
        current = pd.read_parquet(buffer_file)
        merged = pd.concat([current, df], ignore_index=True)
    else:
        merged = df.copy()

    if "GLOBALEVENTID" in merged.columns:
        merged = merged.drop_duplicates(subset=["GLOBALEVENTID"], keep="last")

    merged.to_parquet(buffer_file, index=False)
    return buffer_file


def run_realtime_fetch() -> Path | None:
    """Run the enriched realtime ingestion process."""
    settings = Settings()

    try:
        with httpx.Client(timeout=20) as client:
            r = client.get("http://data.gdeltproject.org/gdeltv2/lastupdate.txt")
            r.raise_for_status()
            urls = parse_lastupdate_urls(r.text)
    except Exception as e:
        print(f"Failed to fetch lastupdate.txt: {e}")
        return None

    missing = [key for key in ("events", "mentions", "gkg") if key not in urls]
    if missing:
        print(f"Missing URLs in lastupdate.txt: {', '.join(missing)}")
        return None

    # 1. Fetch all three streams
    events_df = fetch_csv_zip_to_df(urls["events"], EVENT_COLUMN_INDEX)
    mentions_df = fetch_csv_zip_to_df(urls["mentions"], MENTIONS_COLUMN_INDEX)
    gkg_df = fetch_csv_zip_to_df(urls["gkg"], GKG_COLUMN_INDEX)

    if events_df.empty:
        print("Events DataFrame is empty, aborting.")
        return None

    # 2. Process Mentions (aggregate by event)
    if not mentions_df.empty:
        mentions_df["Confidence"] = pd.to_numeric(mentions_df["Confidence"], errors="coerce")
        m_agg = mentions_df.groupby("GLOBALEVENTID").agg({
            "Confidence": "mean",
            "MentionIdentifier": "first" # Link to first mention for GKG
        }).reset_index()
        m_agg.columns = ["GLOBALEVENTID", "avg_confidence", "top_mention_url"]
        
        m_counts = mentions_df.groupby("GLOBALEVENTID").size().reset_index(name="mentions_count")
        m_agg = m_agg.merge(m_counts, on="GLOBALEVENTID")
    else:
        m_agg = pd.DataFrame(columns=["GLOBALEVENTID", "avg_confidence", "top_mention_url", "mentions_count"])

    # 3. Process GKG (clean lists)
    if not gkg_df.empty:
        def split_gkg(val):
            if not isinstance(val, str): return []
            return [i.split(',')[0] for i in val.split(';') if i]

        gkg_df["themes"] = gkg_df["Themes"].apply(split_gkg)
        gkg_df["persons"] = gkg_df["Persons"].apply(split_gkg)
        gkg_df["organizations"] = gkg_df["Organizations"].apply(split_gkg)
    else:
        gkg_df["themes"] = []
        gkg_df["persons"] = []
        gkg_df["organizations"] = []
        gkg_df["DocumentIdentifier"] = None

    # 4. Join Enrichment
    enriched_df = events_df.merge(m_agg, on="GLOBALEVENTID", how="left")
    enriched_df = enriched_df.merge(
        gkg_df[["DocumentIdentifier", "themes", "persons", "organizations"]],
        left_on="top_mention_url",
        right_on="DocumentIdentifier",
        how="left"
    )

    # 5. Final cleanup
    for col in NUMERIC_COLUMNS:
        if col in enriched_df.columns:
            enriched_df[col] = pd.to_numeric(enriched_df[col], errors="coerce")
    
    # Ensure all final columns exist with correct types
    for col in EVENTS_COLUMNS:
        if col not in enriched_df.columns:
            enriched_df[col] = None
    
    # Handle list columns defaults for empty joins
    for col in ["themes", "persons", "organizations"]:
        enriched_df[col] = enriched_df[col].apply(lambda x: x if isinstance(x, list) else [])

    recent_ids = load_recent_event_ids(settings.hot_tier_path, max_rows=1000)
    new_df = dedupe_against_recent(enriched_df, recent_ids)
    
    if new_df.empty:
        print("No new enriched rows after dedupe.")
        return None

    # Final project to stable schema
    out_file = append_realtime_buffer(new_df[EVENTS_COLUMNS], settings.hot_tier_path)
    print(f"Realtime enriched fetch success: appended {len(new_df)} rows to {out_file}")
    return out_file


if __name__ == "__main__":
    run_realtime_fetch()
