"""Refresh daily scores using frozen weights and training-only preprocessing."""
import argparse
from datetime import datetime, UTC
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from training.crisis.data import Config, FEATURES, country_features
from training.crisis.model import CrisisTCN, probabilities


def publish_latest(model, latest, mean, std, spec, models, threshold, cache, as_of=None):
    windows = np.stack([row["window"] for row in latest])
    if not np.isfinite(windows).all():
        raise ValueError("Latest feature windows contain non-finite values")
    scores = probabilities(model, ((windows - mean) / std).astype(np.float32))
    items = [{"country_code": row["country_code"], "score": None if row["ongoing_spike"] else float(score),
              "alert": bool(score >= threshold) if not row["ongoing_spike"] else False,
              "status": "ongoing_spike" if row["ongoing_spike"] else "eligible",
              "event_count": row["event_count"], "baseline_daily_events": row["baseline"]} for row, score in zip(latest, scores)]
    items.sort(key=lambda row: row["score"] if row["score"] is not None else -1, reverse=True)
    anchor = pd.Timestamp(as_of or spec["data_end"])
    payload = {"status": "research_candidate", "model": "tcn_focal", "as_of": str(anchor.date()),
               "training_as_of": spec["data_end"],
               "horizon_start": str((anchor + pd.Timedelta(days=1)).date()),
               "horizon_end": str((anchor + pd.Timedelta(days=3)).date()),
               "generated_at": datetime.now(UTC).isoformat(), "threshold": threshold,
               "score_kind": "uncalibrated_model_score", "test_metrics": models,
               "label": "First daily spike >=2x prior 7-day mean and >=10 events; next 3 days; exclude ongoing spikes",
               "data": items}
    cache.mkdir(parents=True, exist_ok=True)
    temporary = cache / "latest.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False))
    temporary.replace(cache / "latest.json")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("/data/crisis/daily_features.parquet"))
    parser.add_argument("--model", type=Path, default=Path("/data/crisis/model"))
    parser.add_argument("--cache", type=Path, default=Path("/data/cache/crisis_onset"))
    args = parser.parse_args()
    torch.set_num_threads(2)
    pre = json.loads((args.model / "preprocessing.json").read_text())
    report = json.loads((args.model / "metrics.json").read_text())
    spec = pre["spec"]
    config = Config(**spec["config"])
    raw = pd.read_parquet(args.input)
    raw["date"] = pd.to_datetime(raw.date).dt.normalize()
    dates = pd.date_range(raw.date.min(), raw.date.max())
    if set(dates) != set(raw.date.unique()) or raw.duplicated(["date", "country_code"]).any():
        raise ValueError("Incomplete or duplicate country/day history")
    groups = dict(tuple(raw.groupby("country_code")))
    latest = []
    for code in spec["countries"]:
        frame = country_features(groups.get(code, raw.iloc[:0]), dates, config)
        latest.append({"country_code": code, "window": frame[FEATURES].to_numpy(dtype=np.float32)[-config.sequence_days:],
                       "ongoing_spike": bool(frame.spike.iloc[-1]), "event_count": int(frame.event_count.iloc[-1]),
                       "baseline": float(frame.baseline.iloc[-1])})
    model = CrisisTCN(len(FEATURES))
    model.load_state_dict(torch.load(args.model / "tcn.pt", map_location="cpu", weights_only=True))
    result = publish_latest(model, latest, np.array(pre["mean"]), np.array(pre["std"]), spec,
                            report["models"], pre["threshold"], args.cache, as_of=dates[-1])
    (args.cache / "evaluation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"as_of": result["as_of"], "regions": len(result["data"]), "status": "refreshed"}))


if __name__ == "__main__":
    main()
