"""Export small, partition-pruned country/day feature tables from GDELT."""
import argparse
from datetime import date, datetime, timedelta, UTC
import json
import os
from pathlib import Path

import pandas as pd
from google.cloud import bigquery

SQL = """
SELECT PARSE_DATE('%Y%m%d', CAST(SQLDATE AS STRING)) AS date,
       ActionGeo_CountryCode AS country_code,
       COUNT(DISTINCT GLOBALEVENTID) AS event_count,
       COUNT(DISTINCT IF(QuadClass >= 3, GLOBALEVENTID, NULL)) AS conflict_count,
       AVG(AvgTone) AS avg_tone, AVG(GoldsteinScale) AS avg_goldstein,
       SUM(NumMentions) AS mentions, SUM(NumSources) AS sources
FROM `gdelt-bq.gdeltv2.events_partitioned`
WHERE _PARTITIONDATE BETWEEN @start AND @end
  AND SQLDATE BETWEEN @start_int AND @end_int
  AND REGEXP_CONTAINS(ActionGeo_CountryCode, r'^[A-Z]{2}$')
GROUP BY date, country_code
ORDER BY date, country_code
"""


def validate(frame, start, end):
    dates = pd.to_datetime(frame.date).dt.date
    expected = set(pd.date_range(start, end).date)
    missing = expected - set(dates)
    if missing:
        raise ValueError(f"Missing entire source days: {sorted(missing)}")
    if frame.duplicated(["date", "country_code"]).any():
        raise ValueError("Duplicate country/day rows")
    if (frame.event_count <= 0).any():
        raise ValueError("Source rows must have positive event counts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID"), required=False)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat, default=datetime.now(UTC).date() - timedelta(days=1))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--append", action="store_true", help="Keep existing history and query only new completed days")
    parser.add_argument("--output", type=Path, default=Path("/data/crisis/daily_features.parquet"))
    parser.add_argument("--max-query-bytes", type=int, default=2_000_000_000)
    parser.add_argument("--max-total-bytes", type=int, default=20_000_000_000)
    args = parser.parse_args()
    existing = pd.read_parquet(args.output) if args.append and args.output.exists() else None
    start = args.start or args.end - timedelta(days=args.days - 1)
    data_start = start
    if existing is not None:
        existing_dates = pd.to_datetime(existing.date).dt.date
        if existing_dates.max() > args.end:
            raise ValueError("Append would truncate existing history")
        data_start = existing_dates.min()
        validate(existing, data_start, existing_dates.max())
        start = existing_dates.max() + timedelta(days=1)
    if not args.project or (start > args.end and existing is None) or args.end >= datetime.now(UTC).date():
        parser.error("Supply a project and a range of completed UTC days")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    chunks = args.output.parent / "chunks"
    chunks.mkdir(exist_ok=True)
    client = bigquery.Client(project=args.project)
    frames, jobs = ([] if existing is None else [existing]), []
    total_estimated = 0
    cursor = start
    while cursor <= args.end:
        end = min(cursor + timedelta(days=29), args.end)
        target = chunks / f"daily_{cursor}_{end}.parquet"
        if target.exists():
            frame = pd.read_parquet(target)
            validate(frame, cursor, end)
            frames.append(frame)
            print(f"Cached {cursor} to {end}: {len(frame)} rows", flush=True)
            cursor = end + timedelta(days=1)
            continue
        params = [bigquery.ScalarQueryParameter("start", "DATE", cursor),
                  bigquery.ScalarQueryParameter("end", "DATE", end),
                  bigquery.ScalarQueryParameter("start_int", "INT64", int(cursor.strftime("%Y%m%d"))),
                  bigquery.ScalarQueryParameter("end_int", "INT64", int(end.strftime("%Y%m%d")))]
        estimate = client.query(SQL, job_config=bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False, query_parameters=params))
        scanned = int(estimate.total_bytes_processed or 0)
        total_estimated += scanned
        if scanned > args.max_query_bytes or total_estimated > args.max_total_bytes:
            raise RuntimeError(f"Export scan budget exceeded before execution: {scanned}, total {total_estimated}")
        job = client.query(SQL, job_config=bigquery.QueryJobConfig(
            query_parameters=params, maximum_bytes_billed=args.max_query_bytes,
            labels={"app": "gniem", "task": "crisis-training"}))
        frame = pd.DataFrame([dict(row) for row in job.result()])
        validate(frame, cursor, end)
        temp = target.with_suffix(".parquet.tmp")
        frame.to_parquet(temp, index=False)
        temp.replace(target)
        jobs.append({"start": str(cursor), "end": str(end), "job_id": job.job_id,
                     "estimated_bytes": scanned, "billed_bytes": int(job.total_bytes_billed or 0)})
        frames.append(frame)
        print(json.dumps(jobs[-1]), flush=True)
        cursor = end + timedelta(days=1)
    result = pd.concat(frames, ignore_index=True).sort_values(["date", "country_code"])
    validate(result, data_start, args.end)
    temp = args.output.with_suffix(".parquet.tmp")
    result.to_parquet(temp, index=False)
    temp.replace(args.output)
    metadata = {"start": str(data_start), "end": str(args.end), "rows": len(result),
                "countries": int(result.country_code.nunique()), "source": "gdelt-bq.gdeltv2.events_partitioned",
                "availability": "same UTC ingestion partition and SQLDATE; complete UTC days only",
                "jobs_executed_this_run": jobs, "estimated_bytes_this_run": total_estimated}
    args.output.with_suffix(".manifest.json").write_text(json.dumps(metadata, indent=2))
    print(json.dumps(metadata), flush=True)


if __name__ == "__main__":
    main()
