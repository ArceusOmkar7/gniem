# Three-day news-activity onset detector

Research TCN with binary focal loss, integrated into the public GNIEM dashboard.
This predicts a GDELT news-activity proxy, **not independently verified crises**.

## Label and inputs

For country/region c, a daily spike is at least twice the mean event count of the
preceding seven complete UTC days, with positive baseline and at least 10 events.
An onset is a spike following a non-spike day. At decision day t the target is
any onset in t+1 through t+3. Days already spiking at t are excluded from training
and evaluation; inference marks them `ongoing_spike` without a score.

The 28-day input contains 11 causal features: log event/conflict counts, conflict
fraction, tone, Goldstein scale, log mentions/sources, log prior baseline,
activity ratio, and sine/cosine weekday. Country codes are GDELT/FIPS codes, not
ISO codes. Regions need 100 events in training history to qualify. Country-level
missing rows become zero only after checking that the global feed has every day.

Export uses only `gdelt-bq.gdeltv2.events_partitioned`, distinct event IDs and
same-day ingestion partitions/SQLDATE. No GKG join or duplicated enrichment rows.
Late/backdated events outside that partition convention are excluded. GDELT is
not a versioned historical snapshot: this is a retrospective chronological test,
not a proof of exact historical real-time availability.

## Model and evaluation

Three causal residual convolution blocks (kernel 3; dilations 1,2,4; channels 16;
dropout 0.1) feed a binary head. AdamW, learning rate .001, batch 256, focal gamma 2,
training-prevalence-derived alpha capped at .95, seed 42, up to 20 CPU epochs.
Early stopping uses validation average precision, patience four.

Global calendar splits are approximately 60/20/20. Three-day label windows that
cross train/validation or validation/test boundaries are purged. Normalization
and country eligibility use training only. Each threshold maximizes validation
F1. Test labels are used only for the final report, not model/threshold selection.
Historical feature windows may overlap across splits; future outcome labels do
not cross them. Overlapping windows and country dependence mean individual
country-days are not independent trials.

Comparators: balanced logistic regression on the same normalized input, current
activity ratio with validation-tuned threshold, always-no-spike, and the current
spike rule (always off in the non-spiking onset risk set). The current rule cannot
anticipate a future onset; the ratio and logistic are the useful causal baselines.
Report precision, recall, F1, confusion counts, prevalence, non-interpolated
average precision (AP), and trapezoidal PR-AUC. Constant-score trapezoidal PR-AUC
is misleading due to endpoint interpolation; use AP for ranking comparisons.

See [the committed run](../../docs/crisis-onset/RESULTS.md). This is a single
chronological split and seed. Scores are uncalibrated, false positives remain
substantial, and there is no causal or verified-crisis interpretation. Rolling
backtests, per-country analysis, calibration, lead-time/event-level recall and
external crisis labels are the next research steps before operational reliance.

## Run on the existing GCP VM

```sh
# Uses the attached service account; do not create/download JSON credentials.
docker compose --env-file .env.gcp -f compose.gcp.yml -f compose.crisis.yml --profile training build crisis-worker
docker compose --env-file .env.gcp -f compose.gcp.yml -f compose.crisis.yml --profile training run --rm --no-deps crisis-worker sh training/crisis/run.sh
# Append only newly completed days and score with frozen weights:
docker compose --env-file .env.gcp -f compose.gcp.yml -f compose.crisis.yml --profile training run --rm --no-deps crisis-worker sh training/crisis/daily.sh
sudo cp training/crisis/gniem-crisis-daily.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gniem-crisis-daily.timer
```

Export dry-runs every new query and caps it at 2 GB, with 20 GB cumulative per
export invocation. Daily append ordinarily queries one day. The timer runs at
03:30 UTC, scores frozen weights and never retrains automatically. Inspect
`journalctl -u gniem-crisis-daily.service` for failures. Retrain explicitly when
needed and archive the preceding report before replacing it.

Artifacts stay on the existing data volume under `/data/crisis/`: aggregate
Parquet/manifest, model weights, preprocessing, test predictions, report and PR
plot. Only the report/plot belong in Git; raw data and weights do not. Back up the
data volume to preserve the trained model. API workers read atomically published
`/data/cache/crisis_onset/latest.json`; stale inputs/scores return HTTP 503.
Endpoints: `/api/v1/analytics/crisis-onset` and `/evaluation` beneath it.

## Local checks

```sh
python -m pip install -r training/crisis/requirements.txt
python -m pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu
python -m pytest training/crisis/tests -q
```

Tests cover causal features/convolutions, future labels, split purging, missing
global feed days, duplicate rows, focal gradients, checkpoint restoration and
the scoring publication contract. Model reports record the data SHA256 and Torch
version; dependencies use bounded ranges, so bit-identical cross-version reruns
are not guaranteed.
