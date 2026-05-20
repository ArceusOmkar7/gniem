# ---- Build stage ----
FROM python:3.11-slim AS builder

WORKDIR /build

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.11-slim

LABEL maintainer="GNIEM Team"
LABEL description="GNIEM Backend API — Global News Intelligence & Event Monitoring"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY backend /app/backend

# Copy initial data into the image
# This allows named volumes to be auto-populated on first run
COPY data /data

# Non-root user for security
RUN useradd --create-home appuser && chown -R appuser:appuser /app /data
USER appuser

# Ensure backend package is discoverable
ENV PYTHONPATH=/app

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
