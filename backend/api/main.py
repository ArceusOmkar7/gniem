"""FastAPI application factory — wires dependency injection and registers routers.

This is the only module that knows about concrete infrastructure classes.
All other modules depend only on abstract ports or the use-case layer.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.routers import events as events_router
from backend.api.routers import health as health_router
from backend.api.routers import analytics as analytics_router
from backend.api.routers import map as map_router
from backend.api.routers import crisis as crisis_router
from backend.api.routers.events import _get_use_case, _get_analyze_use_case, _get_hot_repository as _get_events_hot_repo
from backend.api.routers.health import (
    _get_bq_client,
    _get_settings,
    _get_hot_repository as _get_health_hot_repo,
    set_app_start_time,
)
from backend.api.routers.analytics import (
    _get_cluster_use_case,
    _get_forecast_use_case,
    _get_hot_repository as _get_analytics_hot_repo,
)
from backend.api.routers.map import _get_use_case as _get_map_use_case
from backend.api.request_context import set_request_user_id
from backend.application.use_cases.get_events import GetEventsUseCase
from backend.application.use_cases.cluster_events import ClusterEventsUseCase
from backend.application.use_cases.forecast_events import ForecastEventsUseCase
from backend.application.use_cases.analyze_event import AnalyzeEventUseCase
from backend.domain.services.clustering_service import ClusteringService
from backend.domain.services.forecasting_service import ForecastingService
from backend.infrastructure.config.settings import Settings, settings
from backend.infrastructure.data_access.bigquery_client import BigQueryClient, BigQueryClientError
from backend.infrastructure.data_access.duckdb_repository import DuckDbRepository
from backend.infrastructure.data_access.gdelt_repository import GdeltRepository
from backend.infrastructure.data_access.routed_repository import (
    ColdTierPolicyError,
    RoutedRepository,
)
from backend.infrastructure.services.scraper_service import ScraperService
from backend.infrastructure.services.llm_analysis_service import LLMAnalysisService

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — initialise and tear down shared resources."""
    logger.info("app_startup", environment=settings.environment)
    set_app_start_time(time.monotonic())

    # --- Build the dependency graph ---
    bq_client = BigQueryClient(settings)
    cold_repository = GdeltRepository(bq_client, settings)
    hot_repository = DuckDbRepository(settings)
    repository = RoutedRepository(hot_repository, cold_repository, settings)
    
    # Phase 1
    events_use_case = GetEventsUseCase(repository)
    
    # Phase 2
    clustering_service = ClusteringService()
    forecasting_service = ForecastingService()
    cluster_use_case = ClusterEventsUseCase(repository, clustering_service)
    forecast_use_case = ForecastEventsUseCase(repository, forecasting_service)

    # Phase 3 (optional): keep core API available even if AI dependencies are absent.
    analyze_use_case: AnalyzeEventUseCase | None = None
    try:
        scraper_service = ScraperService(settings)
        llm_service = LLMAnalysisService(settings)
        analyze_use_case = AnalyzeEventUseCase(repository, scraper_service, llm_service)
    except Exception as exc:
        logger.warning("analyze_use_case_disabled", reason=str(exc))

    # --- Wire dependencies into routers via overrides ---
    app.dependency_overrides[_get_use_case] = lambda: events_use_case
    app.dependency_overrides[_get_map_use_case] = lambda: events_use_case
    def _provide_analyze_use_case() -> AnalyzeEventUseCase:
        if analyze_use_case is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Analyze endpoint is unavailable. "
                    "Configure GROQ_API_KEY and AI dependencies to enable it."
                ),
            )
        return analyze_use_case

    app.dependency_overrides[_get_analyze_use_case] = _provide_analyze_use_case
    app.dependency_overrides[_get_cluster_use_case] = lambda: cluster_use_case
    app.dependency_overrides[_get_forecast_use_case] = lambda: forecast_use_case
    app.dependency_overrides[_get_bq_client] = lambda: bq_client
    app.dependency_overrides[_get_settings] = lambda: settings
    app.dependency_overrides[_get_health_hot_repo] = lambda: hot_repository
    app.dependency_overrides[_get_analytics_hot_repo] = lambda: hot_repository
    app.dependency_overrides[_get_events_hot_repo] = lambda: hot_repository

    yield

    logger.info("app_shutdown")


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    app = FastAPI(
        title="GNIEM — Global News Intelligence & Event Monitoring",
        description=(
            "AI-powered intelligence dashboard built on GDELT 2.1. "
            "Provides conflict forecasting, event clustering, and "
            "geospatial event analysis."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- CORS ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Routers ---
    app.include_router(
        health_router.router,
        prefix=settings.api_v1_prefix,
    )
    app.include_router(
        events_router.router,
        prefix=settings.api_v1_prefix,
    )
    app.include_router(
        analytics_router.router,
        prefix=settings.api_v1_prefix,
    )
    app.include_router(
        map_router.router,
        prefix=settings.api_v1_prefix,
    )

    app.include_router(crisis_router.router, prefix=settings.api_v1_prefix)

    # --- Structured exception handlers ---
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        user_id = request.headers.get("x-user-id")
        if not user_id:
            xff = request.headers.get("x-forwarded-for")
            if xff:
                user_id = xff.split(",")[0].strip()
        if not user_id:
            user_id = request.client.host if request.client else "anonymous"

        set_request_user_id(user_id)
        return await call_next(request)

    @app.exception_handler(BigQueryClientError)
    async def bigquery_error_handler(
        request: Request,
        exc: BigQueryClientError,
    ) -> JSONResponse:
        logger.error("bigquery_request_error", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=502,
            content={
                "error": "data_source_error",
                "message": "Failed to retrieve data from the upstream data source.",
                "detail": str(exc) if settings.environment == "development" else None,
            },
        )

    @app.exception_handler(ColdTierPolicyError)
    async def cold_tier_policy_error_handler(
        request: Request,
        exc: ColdTierPolicyError,
    ) -> JSONResponse:
        status_code = 429 if "monthly user limit" in str(exc).lower() else 400
        logger.warning("cold_tier_policy_error", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=status_code,
            content={
                "error": "cold_tier_policy_error",
                "message": str(exc),
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            error=str(exc),
            error_type=type(exc).__name__,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": "An unexpected error occurred.",
                "detail": str(exc) if settings.environment == "development" else None,
            },
        )

    return app


# Module-level app instance for uvicorn
app = create_app()
