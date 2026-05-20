"""Hot-tier maintenance: summarize coverage, prune old days, backfill missing days."""

from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from pathlib import Path

from backend.infrastructure.config.settings import Settings
from scripts.daily_bq_pull import run_for_date

DATE_RE = re.compile(r"^events_(\d{8})\.parquet$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hot-tier maintenance utilities")
    parser.add_argument(
        "--cutoff-days",
        type=int,
        default=90,
        help="Keep only the most recent N days (default: 90).",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete hot-tier files older than the cutoff window.",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill missing days using daily_bq_pull.",
    )
    parser.add_argument(
        "--include-recent",
        action="store_true",
        help="Also backfill days after the latest file up to yesterday.",
    )
    return parser.parse_args()


def iter_event_dates(hot_dir: Path) -> list[date]:
    dates: list[date] = []
    for path in hot_dir.glob("events_*.parquet"):
        match = DATE_RE.match(path.name)
        if not match:
            continue
        raw = match.group(1)
        try:
            dates.append(date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])))
        except ValueError:
            continue
    return sorted(set(dates))


def compute_missing_between(start: date, end: date, available: set[date]) -> list[date]:
    missing: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor not in available:
            missing.append(cursor)
        cursor += timedelta(days=1)
    return missing


def prune_old_files(hot_dir: Path, keep_after: date) -> list[Path]:
    removed: list[Path] = []
    for path in hot_dir.glob("events_*.parquet"):
        match = DATE_RE.match(path.name)
        if not match:
            continue
        raw = match.group(1)
        try:
            file_date = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            continue
        if file_date < keep_after:
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def main() -> int:
    args = parse_args()
    settings = Settings()
    hot_dir = Path(settings.hot_tier_path)
    if not hot_dir.exists():
        print(f"Hot-tier path not found: {hot_dir}")
        return 1

    dates = iter_event_dates(hot_dir)
    if not dates:
        print("No events_YYYYMMDD.parquet files found in hot tier.")
        return 0

    available = set(dates)
    min_date = dates[0]
    max_date = dates[-1]
    total_days = len(dates)

    missing_in_range = compute_missing_between(min_date, max_date, available)

    missing_recent: list[date] = []
    if args.include_recent:
        yesterday = date.today() - timedelta(days=1)
        if max_date < yesterday:
            missing_recent = compute_missing_between(max_date + timedelta(days=1), yesterday, available)

    print("Hot-tier summary")
    print(f"- Range: {min_date.isoformat()} -> {max_date.isoformat()}")
    print(f"- Days on disk: {total_days}")
    print(f"- Gaps in range: {len(missing_in_range)}")
    if missing_in_range:
        print("  Missing:", ", ".join(d.isoformat() for d in missing_in_range))
    if missing_recent:
        print(f"- Missing recent: {len(missing_recent)}")
        print("  Missing recent:", ", ".join(d.isoformat() for d in missing_recent))

    if args.prune:
        keep_after = max_date - timedelta(days=args.cutoff_days - 1)
        removed = prune_old_files(hot_dir, keep_after)
        print(f"Pruned {len(removed)} files older than {keep_after.isoformat()}.")

    if args.backfill:
        targets = missing_in_range + missing_recent
        if not targets:
            print("No missing days to backfill.")
            return 0
        for target in targets:
            print(f"Backfilling {target.isoformat()}...")
            run_for_date(target)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
