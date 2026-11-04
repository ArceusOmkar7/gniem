"""Read-only experimental spike-onset predictions; no training in API workers."""
from datetime import date, datetime, timedelta, UTC
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from backend.infrastructure.config.settings import settings
from backend.infrastructure.services.lookup_service import lookup_service

router = APIRouter(prefix="/analytics/crisis-onset", tags=["experimental-crisis-onset"])


class Prediction(BaseModel):
    country_code: str
    country_name: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)
    alert: bool
    status: str
    event_count: int = Field(ge=0)
    baseline_daily_events: float = Field(ge=0)


class Predictions(BaseModel):
    status: str
    model: str
    as_of: date
    horizon_start: date
    horizon_end: date
    generated_at: datetime
    threshold: float = Field(ge=0, le=1)
    score_kind: str
    test_metrics: dict[str, Any]
    label: str
    data: list[Prediction]


def read_predictions() -> Predictions:
    try:
        payload = Predictions.model_validate_json(
            (Path(settings.cache_path) / "crisis_onset" / "latest.json").read_text()
        )
    except (OSError, ValidationError, ValueError):
        raise HTTPException(503, "Spike-onset predictions are not available yet")
    now = datetime.now(UTC)
    if payload.generated_at.tzinfo is None or not -timedelta(minutes=5) <= now - payload.generated_at <= timedelta(hours=36):
        raise HTTPException(503, "Spike-onset predictions are stale; refresh the daily inference job")
    if not timedelta(days=0) < now.date() - payload.as_of <= timedelta(days=2):
        raise HTTPException(503, "Spike-onset input data is stale or incomplete")
    return payload


@router.get("", response_model=Predictions)
def crisis_onset(limit: int = Query(10, ge=1, le=300)) -> Predictions:
    payload = read_predictions()
    rows = [row.model_copy(update={"country_name": lookup_service.get_country_name(row.country_code)})
            for row in payload.data[:limit]]
    return payload.model_copy(update={"data": rows})


@router.get("/evaluation")
def crisis_evaluation() -> dict:
    try:
        report = json.loads((Path(settings.cache_path) / "crisis_onset" / "evaluation.json").read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "Spike-onset evaluation has not been generated")
    return report
