"""Process a verified backfill and atomically publish dashboard analytics."""
from __future__ import annotations

import asyncio
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, UTC
import json
from pathlib import Path
import shutil
import pandas as pd

from backend.infrastructure.config.settings import Settings
from scripts.nightly_ai import (
    build_forecasts_dataframe, build_briefings_payload,
    run_anomaly_detection, build_theme_category_counts,
)


def main() -> None:
    settings = Settings()
    cache = Path(settings.cache_path)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    report_path = cache / "processing_status.json"
    previous = json.loads(report_path.read_text()) if args.resume else None
    backfill = json.loads((cache / "backfill_status.json").read_text())
    assert backfill["state"] == "complete" and not backfill["errors"], "Backfill is incomplete"
    start, end = date.fromisoformat(backfill["start"]), date.fromisoformat(backfill["end"])
    if previous:
        assert previous["start"] == str(start) and previous["end"] == str(end)
        assert previous["input_rows"] == backfill["total_rows"]
    run_id = previous["run_id"] if previous else datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    staging = cache / f"processing-{run_id}"
    inputs = staging / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    expected_days = (end - start).days + 1
    for i in range(expected_days):
        day = start + timedelta(days=i)
        source = Path(settings.hot_tier_path) / f"events_{day:%Y%m%d}.parquet"
        assert source.is_file(), f"Missing {source}"
        link = inputs / source.name
        if not link.exists():
            link.symlink_to(source)
    model_settings = settings.model_copy(update={"hot_tier_path": str(inputs)})
    status = {"state": "running", "phase": "starting", "run_id": run_id,
              "start": str(start), "end": str(end), "input_days": expected_days,
              "input_rows": backfill["total_rows"], "results": previous["results"] if previous else {}}

    def checkpoint(phase: str) -> None:
        status["phase"] = phase
        status["updated_at"] = datetime.now(UTC).isoformat()
        temporary = report_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(status, indent=2))
        temporary.replace(report_path)
        print(json.dumps(status), flush=True)

    try:
        checkpoint("forecasts")
        forecasts = (
            pd.read_parquet(staging / "forecasts.parquet")
            if "forecasts" in status["results"] else build_forecasts_dataframe(model_settings)
        )
        assert not forecasts.empty, "No forecasts generated"
        forecasts.to_parquet(staging / "forecasts.parquet", index=False)
        status["results"]["forecasts"] = {
            "countries": int(forecasts["country_code"].nunique()),
            "points": len(forecasts),
            "models": dict(Counter(forecasts["model_type"])),
        }

        checkpoint("anomalies")
        anomalies = (
            json.loads((staging / "anomalies.json").read_text())
            if "anomalies" in status["results"] else run_anomaly_detection(model_settings)
        )
        (staging / "anomalies.json").write_text(json.dumps(anomalies))
        status["results"]["anomalies"] = {
            "countries": len(anomalies),
            "flagged": sum(bool(v.get("is_anomaly")) for v in anomalies.values()),
        }

        checkpoint("themes")
        themes = (
            json.loads((staging / "theme_categories.json").read_text())
            if "themes" in status["results"] else build_theme_category_counts(model_settings)
        )
        (staging / "theme_categories.json").write_text(json.dumps(themes))
        status["results"]["themes"] = themes

        checkpoint("briefings")
        briefings = (
            json.loads((staging / "briefings.json").read_text())
            if "briefings" in status["results"] else asyncio.run(build_briefings_payload(model_settings))
        )
        assert briefings, "No country briefings generated"
        (staging / "briefings.json").write_text(json.dumps(briefings, indent=2))
        status["results"]["briefings"] = {
            "countries": len(briefings),
            "sources": dict(Counter(v["source"] for v in briefings.values())),
        }

        checkpoint("publishing")
        backups = staging / "previous"
        backups.mkdir(exist_ok=True)
        for name in ("forecasts.parquet", "anomalies.json", "theme_categories.json", "briefings.json"):
            destination = cache / name
            if destination.is_file():
                shutil.copy2(destination, backups / name)
            (staging / name).replace(destination)
        status["state"] = "complete"
        checkpoint("complete")
    except Exception as exc:
        status["state"] = "failed"
        status["error"] = f"{type(exc).__name__}: {exc}"
        checkpoint("failed")
        raise


if __name__ == "__main__":
    main()
