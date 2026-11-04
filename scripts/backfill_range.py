"""Resume an explicit date-range backfill while live ingestion keeps running."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from backend.infrastructure.config.settings import settings
from backend.infrastructure.data_access.bigquery_client import BigQueryClient
from scripts.daily_bq_pull import EVENTS_COLUMNS, fetch_enriched_data


def existing_rows(path: Path, day: date) -> int | None:
    if not path.is_file():
        return None
    try:
        parquet = pq.ParquetFile(path)
        if not set(EVENTS_COLUMNS).issubset(parquet.schema_arrow.names):
            return None
        dates = pd.read_parquet(path, columns=["SQLDATE"])["SQLDATE"]
        if len(dates) and dates.eq(int(day.strftime("%Y%m%d"))).all():
            return len(dates)
    except Exception:
        pass
    return None


def load_day(day: date) -> dict:
    target = Path(settings.hot_tier_path) / f"events_{day:%Y%m%d}.parquet"
    count = existing_rows(target, day)
    if count is not None:
        return {"date": day.isoformat(), "rows": count, "status": "existing"}
    temporary = target.with_suffix(".parquet.tmp")
    for attempt in range(2):
        try:
            client = BigQueryClient(settings)
            frame = fetch_enriched_data(client, settings.gdelt_dataset, day, int(day.strftime("%Y%m%d")))
            if frame.empty:
                raise RuntimeError("No events returned for requested day")
            for column in EVENTS_COLUMNS:
                if column not in frame:
                    frame[column] = None
            frame[EVENTS_COLUMNS].to_parquet(temporary, index=False)
            if existing_rows(temporary, day) != len(frame):
                raise RuntimeError("Written parquet failed validation")
            temporary.replace(target)
            return {"date": day.isoformat(), "rows": len(frame), "status": "loaded", "bytes": target.stat().st_size}
        except Exception:
            if attempt == 1:
                raise
            time.sleep(3)
    raise AssertionError("Unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--workers", type=int, default=2, choices=[1, 2, 3])
    args = parser.parse_args()
    days = (args.end - args.start).days + 1
    if not 1 <= days <= 31 or args.end >= date.today():
        parser.error("Use 1–31 complete historical days")
    Path(settings.hot_tier_path).mkdir(parents=True, exist_ok=True)
    report_path = Path(settings.cache_path) / "backfill_status.json"
    report = {"start": str(args.start), "end": str(args.end), "expected_days": days,
              "state": "running", "results": [], "errors": {}}

    def checkpoint() -> None:
        temporary = report_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(report_path)

    checkpoint()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        jobs = {executor.submit(load_day, args.start + timedelta(days=i)): args.start + timedelta(days=i)
                for i in range(days)}
        for future in as_completed(jobs):
            day = jobs[future]
            try:
                result = future.result()
                report["results"].append(result)
                print(json.dumps(result), flush=True)
            except Exception as exc:
                report["errors"][str(day)] = str(exc)
                print(json.dumps({"date": str(day), "error": str(exc)}), flush=True)
            checkpoint()
    report["state"] = "failed" if report["errors"] else "complete"
    report["total_rows"] = sum(r["rows"] for r in report["results"])
    checkpoint()
    print(json.dumps({"state": report["state"], "days": len(report["results"]),
                      "total_rows": report["total_rows"], "errors": report["errors"]}), flush=True)
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
