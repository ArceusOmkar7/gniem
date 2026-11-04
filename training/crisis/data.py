"""Causal features, future rule labels, and calendar-date purged splits."""
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

FEATURES = ["log_events", "log_conflicts", "conflict_ratio", "tone", "goldstein",
            "log_mentions", "log_sources", "log_baseline", "activity_ratio", "weekday_sin", "weekday_cos"]


@dataclass(frozen=True)
class Config:
    sequence_days: int = 28
    baseline_days: int = 7
    horizon_days: int = 3
    spike_multiplier: float = 2.0
    minimum_spike_events: int = 10
    minimum_train_events: int = 100


def country_features(group, dates, config=Config()):
    """Absent country rows become zeros only after global feed-day validation."""
    frame = group.drop(columns=["country_code"]).set_index("date").reindex(dates).rename_axis("date").fillna(0)
    counts = frame.event_count.astype(float)
    baseline = counts.shift(1).rolling(config.baseline_days, min_periods=config.baseline_days).mean()
    spike = (baseline > 0) & (counts >= config.spike_multiplier * baseline) & (counts >= config.minimum_spike_events)
    onset = spike & ~spike.shift(1, fill_value=False)
    frame["baseline"] = baseline
    frame["spike"] = spike
    frame["onset"] = onset
    frame["log_events"] = np.log1p(counts)
    frame["log_conflicts"] = np.log1p(frame.conflict_count.clip(lower=0))
    frame["conflict_ratio"] = frame.conflict_count / counts.clip(lower=1)
    frame["tone"] = frame.avg_tone / 10.0
    frame["goldstein"] = frame.avg_goldstein / 10.0
    frame["log_mentions"] = np.log1p(frame.mentions.clip(lower=0))
    frame["log_sources"] = np.log1p(frame.sources.clip(lower=0))
    frame["log_baseline"] = np.log1p(baseline)
    frame["activity_ratio"] = (counts / baseline.clip(lower=1)).clip(0, 10)
    frame["weekday_sin"] = np.sin(2 * np.pi * frame.index.day_of_week / 7)
    frame["weekday_cos"] = np.cos(2 * np.pi * frame.index.day_of_week / 7)
    # Labels may use future outcomes. Model inputs never use these columns.
    future = pd.concat([onset.shift(-h) for h in range(1, config.horizon_days + 1)], axis=1)
    frame["label"] = future.any(axis=1).astype(float)
    frame.loc[frame.index[-config.horizon_days:], "label"] = np.nan
    return frame


def prepare(raw, config=Config()):
    raw = raw.copy()
    raw["date"] = pd.to_datetime(raw.date).dt.normalize()
    if raw.duplicated(["date", "country_code"]).any():
        raise ValueError("Duplicate country/day input")
    dates = pd.date_range(raw.date.min(), raw.date.max(), freq="D")
    if set(dates) != set(raw.date.unique()):
        raise ValueError("Missing entire feed days; do not fabricate zero-activity days")
    if len(dates) < 120:
        raise ValueError("At least 120 complete days are required for chronological evaluation")
    decisions = dates[config.sequence_days - 1:-config.horizon_days]
    val_start = decisions[int(len(decisions) * .60)]
    test_start = decisions[int(len(decisions) * .80)]
    # Country eligibility is determined from training history alone.
    totals = raw[raw.date < val_start].groupby("country_code").event_count.sum()
    countries = sorted(totals[totals >= config.minimum_train_events].index)
    if not countries:
        raise ValueError("No eligible training countries")
    grouped = dict(tuple(raw.groupby("country_code")))
    samples, labels, metadata, latest = [], [], [], []
    for code in countries:
        frame = country_features(grouped[code], dates, config)
        values = frame[FEATURES].to_numpy(dtype=np.float32)
        for t in range(config.sequence_days - 1, len(dates) - config.horizon_days):
            # Onset prediction is evaluated only when a spike is not already underway.
            if bool(frame.spike.iloc[t]) or not np.isfinite(values[t-config.sequence_days+1:t+1]).all():
                continue
            decision, outcome_end = dates[t], dates[t + config.horizon_days]
            if decision < val_start and outcome_end < val_start:
                split = "train"
            elif decision >= val_start and decision < test_start and outcome_end < test_start:
                split = "validation"
            elif decision >= test_start:
                split = "test"
            else:
                continue  # purge labels crossing a calendar split boundary
            samples.append(values[t-config.sequence_days+1:t+1])
            labels.append(frame.label.iloc[t])
            metadata.append({"country_code": code, "date": str(decision.date()), "outcome_end": str(outcome_end.date()),
                             "split": split, "ratio": float(frame.activity_ratio.iloc[t])})
        latest.append({"country_code": code, "window": values[-config.sequence_days:],
                       "ongoing_spike": bool(frame.spike.iloc[-1]),
                       "event_count": int(frame.event_count.iloc[-1]), "baseline": float(frame.baseline.iloc[-1])})
    if not samples:
        raise ValueError("No eligible sequences")
    return (np.stack(samples), np.array(labels, dtype=np.float32), pd.DataFrame(metadata), latest,
            {"config": asdict(config), "features": FEATURES, "data_start": str(dates[0].date()),
             "data_end": str(dates[-1].date()), "validation_start": str(val_start.date()),
             "test_start": str(test_start.date()), "countries": countries})
