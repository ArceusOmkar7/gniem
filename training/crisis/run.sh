#!/bin/sh
set -eu
python -m training.crisis.export_daily --output /data/crisis/daily_features.parquet
python -m training.crisis.train --input /data/crisis/daily_features.parquet --output /data/crisis/model --cache /data/cache/crisis_onset --epochs 20
