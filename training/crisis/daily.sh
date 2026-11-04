#!/bin/sh
set -eu
python -m training.crisis.export_daily --append
python -m training.crisis.predict
