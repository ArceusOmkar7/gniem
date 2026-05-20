FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://github.com/aptible/supercronic/releases/download/v0.2.29/supercronic-linux-amd64 \
    -o /usr/local/bin/supercronic \
    && chmod +x /usr/local/bin/supercronic

# Build context is project root
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend
COPY scripts /app/scripts
COPY scheduler/schedule.cron /app/schedule.cron

# Lookup files are provided via /data volume and environment variables in docker-compose.yml

ENV PYTHONPATH=/app

CMD ["supercronic", "/app/schedule.cron"]
