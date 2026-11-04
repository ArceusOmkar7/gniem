# GNIEM deployment

Project: `project-1da496a0-2d32-499b-975`
VM: `gniem-vm`, zone `us-central1-a`, Ubuntu 22.04, `e2-standard-2`, 50 GB balanced boot disk.
Public URL: https://gniem.omkarmahindrakar.tech
Static IP: `34.172.175.83`, reserved as `gniem-public-ip` in `us-central1`.
Repository on VM: `/opt/gniem`.
Repository baseline: `5c56027eb8fb9b8d583f7e338c7709729937fcf0`. Deployment fixes are present in this local checkout and on the VM; they have not been pushed to GitHub.

The dedicated runtime service account has BigQuery Job User access. Google client libraries use the attached VM identity; no service account JSON key is needed. Queries have a 2 GB server-enforced billing limit in addition to the existing dry-run check.

SSH is permitted only through Google IAP. The dashboard is public without a login, as requested. Caddy handles HTTPS and certificate renewal, with Nginx request throttling for the API and stricter limits for article analysis. A private maintenance endpoint also listens on VM loopback port 8080. Open a local tunnel:

```powershell
gcloud compute ssh gniem-vm --project=project-1da496a0-2d32-499b-975 --zone=us-central1-a --tunnel-through-iap --ssh-flag=-N --ssh-flag=-L --ssh-flag=8080:127.0.0.1:8080
```

Then open http://localhost:8080. Keep that terminal running while using the dashboard.

For a remote terminal:

```powershell
gcloud compute ssh gniem-vm --project=project-1da496a0-2d32-499b-975 --zone=us-central1-a --tunnel-through-iap
```

On the VM:

```bash
cd /opt/gniem
sudo docker compose --env-file .env.gcp -f compose.gcp.yml ps
sudo docker compose --env-file .env.gcp -f compose.gcp.yml logs --tail=50
curl --fail http://127.0.0.1:8080/api/v1/health
```

To manage the full public stack, include both Compose files:

```bash
sudo docker compose --env-file .env.gcp -f compose.gcp.yml -f compose.public.yml up -d --wait
sudo docker compose --env-file .env.gcp -f compose.gcp.yml -f compose.public.yml logs --tail=50 proxy
```

The DNS A record `gniem.omkarmahindrakar.tech` must point directly to the static IP. Public inbound ports are 80 and 443; port 80 redirects to HTTPS. The certificate state is stored in a Docker volume so renewals survive container restarts.

Runtime secrets are in `/opt/gniem/.env.gcp`, mode 600. The Mapbox public token is embedded in the frontend build as required by Mapbox. Rebuild the web service after changing it. Never commit the completed environment file.

Containers restart automatically when Docker starts. Data lives in the `gniem_gniem-data` Docker volume on the boot disk. That disk is not a separate backup; deleting the VM may delete its data. The scheduler fetches live events every 15 minutes, runs daily BigQuery ingestion at 02:00 UTC, and runs AI jobs at 03:00 UTC.
The `init-data` service sets shared directory ownership before the backend starts. An exited status with code 0 is expected for this one-time service. Runtime containers use UID 1000.

When `OPENROUTER_API_KEY` is configured, article analysis and nightly briefings first try `OPENROUTER_MODEL_NAME=google/gemma-4-26b-a4b-it:free`. The default free fallback is `OPENROUTER_FALLBACK_MODEL_NAME=nvidia/nemotron-3.5-super-vl:free`. Both settings reject paid model IDs. Nightly calls are spaced by at least 3.2 seconds to reduce minute-limit errors. OpenRouter's account quota remains authoritative; public visitors share it, and there is no paid-model fallback. If no OpenRouter key is configured, the previous Groq configuration remains available.

Initial ingestion loaded live events plus 163,001 events from November 2, 2026; the historical query scanned approximately 410 MB. Forecasts and anomaly detection need more daily history to become meaningful. The first nightly run generated all four caches for 30 countries using Groq; 18 briefings used the model and 12 used statistical fallbacks after rate limits. Later nightly runs use the currently configured provider. No provider billing plan was upgraded.

To stop VM compute usage (disk and reserved IP charges continue):

```powershell
gcloud compute instances stop gniem-vm --project=project-1da496a0-2d32-499b-975 --zone=us-central1-a
```

The reserved static IP stays assigned across stop/start cycles. IAP access uses the VM name and does not depend on that IP.

## Historical backfill

`scripts/backfill_range.py` loads an explicit range of up to 31 complete days, preserving valid existing daily files. New files are validated and renamed atomically so the dashboard never reads an incomplete download. It supports up to three workers and retains the application's 2 GB per-query billing cap. Live ingestion continues during the pull.

The current 30-day request covers October 4 through November 2, 2026. Its status report is `/data/cache/backfill_status.json` inside the shared data volume. The named temporary job is `gniem-backfill-30d`. To inspect progress on the VM:

```bash
sudo docker logs --tail=20 gniem-backfill-30d
sudo docker exec gniem-backend-1 cat /data/cache/backfill_status.json
```

The job container removes itself after exiting; its status report and daily Parquet files remain. A complete report must contain 30 successful date entries and no errors. A rerun skips the validated files already present.

This backfill completed successfully: all 30 dates are present, totaling 4,915,797 historical rows. Independent DuckDB validation is saved in `/data/cache/backfill_verification.json`. Today's live feed remains separate, and public API health and the full historical date-range query were verified after completion.

## Analytics processing

The completed October 4–November 2 dataset was processed using `scripts/process_hot_tier.py`. It isolates the 30 completed daily files, supports `--resume`, and publishes completed analytics files atomically while keeping previous outputs as backups.

Verified outputs: 50 country forecasts with 30 future days each (1,500 Prophet points); 226 countries/regions scored for anomalies; eight theme categories; and 30 AI-generated country briefings. No region crossed the configured anomaly alert threshold. The first Gemma run was rate-limited for 29 briefings; `scripts/retry_free_briefings.py` recovered all 29 with free alternatives. Future analyses and nightly briefings use the configured free fallback automatically.

Theme aggregation now runs inside DuckDB, preserving the previous first-match category priority and duplicate theme counts without materializing millions of strings in Python. The focused routing and aggregation tests passed. Clustering was verified on a 1,000-event sample, producing five groups.

Processing status is saved in `/data/cache/processing_status.json`; independent output validation is in `/data/cache/processing_verification.json`. The published dashboard endpoints for briefings, anomalies, themes, and forecasting were checked after processing.
