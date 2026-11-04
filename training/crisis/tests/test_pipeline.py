from datetime import date
import numpy as np
import pandas as pd
import pytest
import torch

from training.crisis.data import Config, FEATURES, country_features, prepare
from training.crisis.model import CausalBlock, CrisisTCN, focal_loss
from training.crisis.train import choose_threshold, evaluate


def raw(days=200):
    dates = pd.date_range("2025-01-01", periods=days)
    counts = np.full(days, 20)
    counts[np.arange(days) % 19 == 0] = 100
    return pd.DataFrame({"date": dates, "country_code": "US", "event_count": counts,
                         "conflict_count": counts // 4, "avg_tone": -1., "avg_goldstein": -.5,
                         "mentions": counts * 3, "sources": counts * 2})


def test_future_changes_do_not_change_past_features():
    original = raw()
    changed = original.copy()
    changed.loc[150:, ["event_count", "mentions", "sources"]] *= 100
    a = country_features(original, original.date)
    b = country_features(changed, changed.date)
    np.testing.assert_allclose(a[FEATURES].iloc[:150], b[FEATURES].iloc[:150], equal_nan=True)


def test_labels_use_next_three_days_and_censor_tail():
    frame = raw(50)
    frame.event_count = 20
    frame.loc[40:41, "event_count"] = 100
    result = country_features(frame, frame.date)
    assert not result.label.iloc[36]
    assert result.label.iloc[37] == 1  # onset at t+3
    assert result.label.iloc[39] == 1
    assert result.onset.iloc[40] and not result.onset.iloc[41]
    assert result.label.iloc[-3:].isna().all()


def test_splits_purge_future_labels_at_calendar_boundaries():
    x, y, metadata, latest, spec = prepare(raw())
    assert len(x) == len(y) == len(metadata)
    for name, boundary in (("train", spec["validation_start"]), ("validation", spec["test_start"])):
        assert metadata.loc[metadata.split == name, "outcome_end"].max() < boundary
    assert set(metadata.split) == {"train", "validation", "test"}
    assert latest[0]["window"].shape == (28, len(FEATURES))
    assert np.isfinite(x).all()


def test_missing_entire_day_is_rejected():
    with pytest.raises(ValueError, match="Missing entire feed"):
        prepare(raw().drop(index=90))


def test_duplicate_country_day_is_rejected():
    frame = raw()
    with pytest.raises(ValueError, match="Duplicate"):
        prepare(pd.concat([frame, frame.iloc[[0]]]))


def test_model_blocks_are_causal():
    torch.manual_seed(1)
    block = CausalBlock(3, 4, 2).eval()
    x = torch.randn(2, 3, 28)
    changed = x.clone()
    changed[:, :, 15:] += 100
    with torch.no_grad():
        torch.testing.assert_close(block(x)[:, :, :15], block(changed)[:, :, :15])


def test_focal_loss_has_finite_gradients_for_extreme_logits():
    logits = torch.tensor([-100., 100., 0., 1.], requires_grad=True)
    loss = focal_loss(logits, torch.tensor([1., 0., 1., 0.]))
    loss.backward()
    assert torch.isfinite(loss) and torch.isfinite(logits.grad).all()


def test_checkpoint_roundtrip(tmp_path):
    model = CrisisTCN(len(FEATURES)).eval()
    x = torch.randn(3, 28, len(FEATURES))
    torch.save(model.state_dict(), tmp_path / "model.pt")
    restored = CrisisTCN(len(FEATURES)).eval()
    restored.load_state_dict(torch.load(tmp_path / "model.pt", weights_only=True))
    with torch.no_grad():
        torch.testing.assert_close(model(x), restored(x))


def test_threshold_and_metrics_handle_imbalanced_labels():
    labels = np.array([0, 0, 0, 1])
    scores = np.array([.1, .2, .3, .9])
    threshold = choose_threshold(labels, scores)
    result = evaluate(labels, scores, threshold)
    assert result["precision"] == result["recall"] == result["average_precision"] == 1.


def test_published_scores_suppress_ongoing_spikes_and_use_refresh_date(tmp_path):
    import json
    from training.crisis.predict import publish_latest
    _, _, _, latest, spec = prepare(raw())
    latest[0]["ongoing_spike"] = True
    model = CrisisTCN(len(FEATURES))
    result = publish_latest(model, latest, np.zeros(len(FEATURES)), np.ones(len(FEATURES)),
                            spec, {}, .5, tmp_path, as_of="2026-11-03")
    assert result["as_of"] == "2026-11-03"
    assert result["horizon_end"] == "2026-11-06"
    assert result["data"][0]["score"] is None
    assert result["data"][0]["alert"] is False
    assert json.loads((tmp_path / "latest.json").read_text()) == result


def test_export_rejects_missing_and_duplicate_days():
    from training.crisis.export_daily import validate
    frame = raw(20)
    validate(frame, frame.date.min().date(), frame.date.max().date())
    with pytest.raises(ValueError, match="Missing entire source"):
        validate(frame.drop(index=5), frame.date.min().date(), frame.date.max().date())
    with pytest.raises(ValueError, match="Duplicate"):
        validate(pd.concat([frame, frame.iloc[[0]]]), frame.date.min().date(), frame.date.max().date())
