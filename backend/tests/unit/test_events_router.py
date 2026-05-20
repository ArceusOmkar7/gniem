"""Unit tests for the events router."""

from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routers.events import _get_analyze_use_case, _get_hot_repository, _get_use_case
from backend.domain.models.event import Event, EventAnalysis, ExtractedArticle, EntityGroup
from backend.application.use_cases.get_events import GetEventsUseCase
from backend.application.use_cases.analyze_event import AnalyzeEventUseCase
from backend.infrastructure.data_access.duckdb_repository import DuckDbRepository

client = TestClient(app)

def _mock_get_events():
    mock = MagicMock(spec=GetEventsUseCase)
    mock.execute.return_value = [
        Event(global_event_id=1, sql_date=date(2024, 1, 1), source_url="http://test.com")
    ]
    return mock

def _mock_analyze_event():
    mock = MagicMock(spec=AnalyzeEventUseCase)
    mock.execute.return_value = EventAnalysis(
        summary="Test analysis",
        sentiment="Positive",
        entities=EntityGroup(),
        themes=[],
        confidence=0.9,
        images=[],
        embeds=[]
    )
    return mock


def _mock_hot_repository():
    mock = MagicMock(spec=DuckDbRepository)
    mock.get_top_sources.return_value = [
        {"name": "example.com", "count": 3},
    ]
    mock.get_top_organizations.return_value = [
        {"name": "Example Org", "count": 5},
    ]
    mock.get_top_cities.return_value = [
        {"name": "Test City", "count": 8},
    ]
    return mock

@pytest.fixture
def override_dependencies():
    get_mock = _mock_get_events()
    analyze_mock = _mock_analyze_event()
    hot_repo_mock = _mock_hot_repository()

    app.dependency_overrides[_get_use_case] = lambda: get_mock
    app.dependency_overrides[_get_analyze_use_case] = lambda: analyze_mock
    app.dependency_overrides[_get_hot_repository] = lambda: hot_repo_mock

    yield get_mock, analyze_mock, hot_repo_mock

    app.dependency_overrides.clear()

def test_get_events(override_dependencies):
    get_mock, _, _ = override_dependencies
    
    response = client.get("/api/v1/events?limit=10")
    
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["data"][0]["global_event_id"] == 1
    
    get_mock.execute.assert_called_once()

def test_analyze_event_api(override_dependencies):
    _, analyze_mock, _ = override_dependencies
    
    response = client.get("/api/v1/events/1/analyze")
    
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == "Test analysis"
    assert data["sentiment"] == "Positive"
    
    analyze_mock.execute.assert_called_once_with(1)


def test_top_sources_api(override_dependencies):
    _, _, hot_repo_mock = override_dependencies

    response = client.get("/api/v1/events/top-sources?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["data"][0]["name"] == "example.com"
    assert data["data"][0]["count"] == 3

    hot_repo_mock.get_top_sources.assert_called_once()


def test_top_organizations_api(override_dependencies):
    _, _, hot_repo_mock = override_dependencies

    response = client.get("/api/v1/events/top-organizations?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["data"][0]["name"] == "Example Org"
    assert data["data"][0]["count"] == 5

    hot_repo_mock.get_top_organizations.assert_called_once()


def test_top_cities_api(override_dependencies):
    _, _, hot_repo_mock = override_dependencies

    response = client.get("/api/v1/events/top-cities?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["data"][0]["name"] == "Test City"
    assert data["data"][0]["count"] == 8

    hot_repo_mock.get_top_cities.assert_called_once()
