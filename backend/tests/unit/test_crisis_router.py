from datetime import datetime, timedelta, UTC
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api.routers import crisis


@pytest.fixture
def client_and_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(crisis, "settings", SimpleNamespace(cache_path=str(tmp_path)))
    monkeypatch.setattr(crisis.lookup_service, "get_country_name", lambda code: "United States")
    folder = tmp_path / "crisis_onset"
    folder.mkdir()
    app = FastAPI()
    app.include_router(crisis.router, prefix="/api/v1")
    return TestClient(app), folder


def payload():
    now = datetime.now(UTC)
    as_of = now.date() - timedelta(days=1)
    return {"status": "research_candidate", "model": "tcn_focal", "as_of": str(as_of),
            "horizon_start": str(as_of + timedelta(days=1)), "horizon_end": str(as_of + timedelta(days=3)),
            "generated_at": now.isoformat(), "threshold": .5, "score_kind": "uncalibrated_model_score",
            "test_metrics": {}, "label": "future rule onset", "data": [
                {"country_code": "US", "score": .6, "alert": True, "status": "eligible",
                 "event_count": 100, "baseline_daily_events": 90.}]}


def test_untrained_is_unavailable(client_and_cache):
    client, _ = client_and_cache
    assert client.get("/api/v1/analytics/crisis-onset").status_code == 503


def test_fresh_cache_is_served_with_country_names(client_and_cache):
    client, folder = client_and_cache
    (folder / "latest.json").write_text(json.dumps(payload()))
    response = client.get("/api/v1/analytics/crisis-onset?limit=1")
    assert response.status_code == 200
    assert response.json()["data"][0]["country_name"] == "United States"
    assert response.json()["score_kind"] == "uncalibrated_model_score"


@pytest.mark.parametrize("kind", ["timestamp", "data", "corrupt", "invalid_score"])
def test_stale_or_invalid_cache_is_not_presented_as_fresh(client_and_cache, kind):
    client, folder = client_and_cache
    value = payload()
    if kind == "timestamp":
        value["generated_at"] = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    elif kind == "data":
        value["as_of"] = str(datetime.now(UTC).date() - timedelta(days=10))
    elif kind == "invalid_score":
        value["data"][0]["score"] = 2.
    (folder / "latest.json").write_text("invalid" if kind == "corrupt" else json.dumps(value))
    assert client.get("/api/v1/analytics/crisis-onset").status_code == 503
