"""Train focal-loss TCN; select thresholds on validation and report untouched test results."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

import numpy as np
import pandas as pd
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, auc, precision_recall_curve,
                             precision_score, recall_score, f1_score, confusion_matrix)
from torch.utils.data import DataLoader, TensorDataset

from training.crisis.data import Config, prepare, FEATURES
from training.crisis.model import CrisisTCN, focal_loss, probabilities
from training.crisis.predict import publish_latest


def choose_threshold(y, scores):
    precision, recall, thresholds = precision_recall_curve(y, scores)
    if not len(thresholds):
        return .5
    f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
    return float(thresholds[int(np.argmax(f1))])


def evaluate(y, scores, threshold):
    prediction = scores >= threshold
    precision, recall, _ = precision_recall_curve(y, scores)
    return {"average_precision": float(average_precision_score(y, scores)),
            "pr_auc_trapezoid": float(auc(recall[::-1], precision[::-1])),
            "precision": float(precision_score(y, prediction, zero_division=0)),
            "recall": float(recall_score(y, prediction, zero_division=0)),
            "f1": float(f1_score(y, prediction, zero_division=0)),
            "threshold": float(threshold), "confusion_matrix_tn_fp_fn_tp": confusion_matrix(y, prediction, labels=[0, 1]).ravel().tolist(),
            "samples": len(y), "positive_rate": float(y.mean())}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("/data/crisis/daily_features.parquet"))
    parser.add_argument("--output", type=Path, default=Path("/data/crisis/model"))
    parser.add_argument("--cache", type=Path, default=Path("/data/cache/crisis_onset"))
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    torch.use_deterministic_algorithms(True)
    raw = pd.read_parquet(args.input)
    x, y, meta, latest, spec = prepare(raw)
    masks = {split: (meta.split == split).to_numpy() for split in ("train", "validation", "test")}
    for split, mask in masks.items():
        if len(np.unique(y[mask])) != 2:
            raise ValueError(f"{split} requires positive and negative labels; increase history")
    # Fit preprocessing on training sequences only, never validation or test.
    mean = x[masks["train"]].mean(axis=(0, 1))
    std = x[masks["train"]].std(axis=(0, 1))
    std[std < 1e-6] = 1
    x = ((x - mean) / std).astype(np.float32)
    model = CrisisTCN(len(FEATURES))
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.001)
    train_y = y[masks["train"]]
    alpha = float(np.clip(1 - train_y.mean(), .5, .95))
    loader = DataLoader(TensorDataset(torch.from_numpy(x[masks["train"]]), torch.from_numpy(train_y)),
                        batch_size=256, shuffle=True, generator=torch.Generator().manual_seed(args.seed))
    best_ap, best_weights, patience, history = -1., None, 0, []
    for epoch in range(args.epochs):
        model.train()
        losses = []
        for batch, target in loader:
            optimizer.zero_grad()
            loss = focal_loss(model(batch), target, alpha=alpha)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            losses.append(float(loss.detach()))
        validation_scores = probabilities(model, x[masks["validation"]])
        ap = float(average_precision_score(y[masks["validation"]], validation_scores))
        record = {"epoch": epoch + 1, "loss": float(np.mean(losses)), "validation_ap": ap}
        history.append(record)
        print(json.dumps(record), flush=True)
        if ap > best_ap + 1e-5:
            best_ap, best_weights, patience = ap, deepcopy(model.state_dict()), 0
        else:
            patience += 1
        if patience >= 4:
            break
    model.load_state_dict(best_weights)
    val, test = masks["validation"], masks["test"]
    tcn_val, tcn_test = probabilities(model, x[val]), probabilities(model, x[test])
    threshold = choose_threshold(y[val], tcn_val)
    logistic = LogisticRegression(class_weight="balanced", max_iter=500, random_state=args.seed)
    logistic.fit(x[masks["train"]].reshape(len(train_y), -1), train_y)
    logistic_val = logistic.predict_proba(x[val].reshape(val.sum(), -1))[:, 1]
    logistic_test = logistic.predict_proba(x[test].reshape(test.sum(), -1))[:, 1]
    ratio_val, ratio_test = meta.loc[val, "ratio"].to_numpy(), meta.loc[test, "ratio"].to_numpy()
    models = {
        "tcn_focal": evaluate(y[test], tcn_test, threshold),
        "logistic_regression": evaluate(y[test], logistic_test, choose_threshold(y[val], logistic_val)),
        "activity_ratio": evaluate(y[test], ratio_test, choose_threshold(y[val], ratio_val)),
        "no_spike": evaluate(y[test], np.zeros(test.sum()), 1.),
        # Eligible samples have no current spike, so this causal alert is always off.
        "current_spike_rule": evaluate(y[test], np.zeros(test.sum()), .5),
    }
    data_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    report = {"task": "3-day news-activity spike onset proxy; not verified real-world crises",
              "label": "First daily spike >=2x prior 7-day mean and >=10 events; next 3 days; exclude ongoing spikes",
              "seed": args.seed, "focal_alpha": alpha, "focal_gamma": 2., "spec": spec,
              "data_sha256": data_hash, "torch_version": torch.__version__,
              "splits": {name: {"samples": int(mask.sum()), "positives": int(y[mask].sum()),
                           "first_decision": str(meta.loc[mask, "date"].min()), "last_decision": str(meta.loc[mask, "date"].max()),
                           "last_outcome": str(meta.loc[mask, "outcome_end"].max())} for name, mask in masks.items()},
              "models": models, "history": history, "threshold_selection": "Maximum F1 on validation only",
              "interpretation": "Held-out chronological test. AP is non-interpolated PR area; trapezoidal PR-AUC also reported. Scores are not calibrated probabilities."}
    write_json(args.output / "metrics.json", report)
    write_json(args.cache / "evaluation.json", report)
    torch.save(model.state_dict(), args.output / "tcn.pt")
    write_json(args.output / "preprocessing.json", {"mean": mean.tolist(), "std": std.tolist(), "threshold": threshold, "spec": spec})
    pd.DataFrame({**{column: meta.loc[test, column].to_numpy() for column in ("country_code", "date", "outcome_end")},
                  "label": y[test], "tcn_score": tcn_test, "logistic_score": logistic_test,
                  "ratio_score": ratio_test}).to_parquet(args.output / "test_predictions.parquet", index=False)
    payload = publish_latest(model, latest, mean, std, spec, models, threshold, args.cache)
    items = payload["data"]
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 5))
    for name, scores in (("TCN focal", tcn_test), ("Logistic", logistic_test), ("Activity ratio", ratio_test)):
        precision, recall, _ = precision_recall_curve(y[test], scores)
        ax.plot(recall, precision, label=f"{name} (AP {average_precision_score(y[test], scores):.3f})")
    ax.axhline(float(y[test].mean()), color="gray", linestyle="--", label="Test prevalence")
    ax.set(xlabel="Recall", ylabel="Precision", title="Held-out chronological test: 3-day spike onset", xlim=(0, 1), ylim=(0, 1))
    ax.legend()
    fig.tight_layout()
    fig.savefig(args.output / "precision_recall.png", dpi=180)
    print(json.dumps({"complete": True, "models": models, "latest_countries": len(items)}), flush=True)


if __name__ == "__main__":
    main()
