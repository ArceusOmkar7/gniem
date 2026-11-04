"""BigQuery client wrapper — the only module that imports google.cloud.bigquery.

Provides a thin, injectable abstraction over the BigQuery SDK so that:
1. No other module in the codebase touches the BigQuery SDK directly.
2. Swapping to a different data warehouse means rewriting only this file.
3. Query execution is instrumented with structured logging.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog
from google.cloud import bigquery
from google.cloud.exceptions import GoogleCloudError
from google.oauth2 import service_account

from backend.infrastructure.config.settings import Settings

logger = structlog.get_logger(__name__)


class BigQueryClientError(Exception):
    """Raised when a BigQuery operation fails."""

    def __init__(self, message: str, query: str | None = None) -> None:
        self.query = query
        super().__init__(message)


class BigQueryClient:
    """Thin wrapper around google.cloud.bigquery.Client.

    Usage::

        client = BigQueryClient(settings)
        rows = client.execute_query("SELECT 1 AS n", params={})
        # rows == [{"n": 1}]
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: bigquery.Client | None = None
        try:
            credentials = None
            creds_path = settings.google_application_credentials
            if creds_path and Path(creds_path).is_file():
                credentials = service_account.Credentials.from_service_account_file(
                    creds_path,
                    scopes=["https://www.googleapis.com/auth/bigquery"],
                )
                logger.info("bigquery_using_service_account", path=creds_path)

            self._client = bigquery.Client(
                project=settings.gcp_project_id,
                credentials=credentials,
            )
            logger.info(
                "bigquery_client_initialized",
                project=settings.gcp_project_id,
                dataset=settings.gdelt_dataset,
            )
        except Exception as exc:
            logger.warning(
                "bigquery_client_init_failed",
                error=str(exc),
                project=settings.gcp_project_id,
                hint="Server will start but BigQuery queries will fail. "
                     "Set up Application Default Credentials or provide "
                     "GOOGLE_APPLICATION_CREDENTIALS to enable BigQuery.",
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a parameterised SQL query and return rows as dicts.

        Args:
            sql: BigQuery Standard SQL string. Use ``@param`` placeholders
                 for parameterised queries.
            params: Mapping of parameter names to ``bigquery.ScalarQueryParameter``
                    objects.  Pass ``None`` or ``{}`` for queries without params.

        Returns:
            A list of row dicts.  Column names are used as keys.

        Raises:
            BigQueryClientError: On any BigQuery SDK or network error.
        """
        if self._client is None:
            raise BigQueryClientError(
                "BigQuery client is not initialized — no valid credentials found.",
                query=sql,
            )

        query_parameters = list(params.values()) if params else []

        dry_run_config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            query_parameters=query_parameters,
        )

        try:
            dry_run_job = self._client.query(sql, job_config=dry_run_config)
            estimated_bytes = int(dry_run_job.total_bytes_processed or 0)
        except GoogleCloudError as exc:
            logger.error(
                "bigquery_dry_run_failed",
                error=str(exc),
                sql=sql[:500],
            )
            raise BigQueryClientError(
                f"BigQuery dry run failed: {exc}",
                query=sql,
            ) from exc
        except Exception as exc:
            logger.error(
                "bigquery_dry_run_unexpected_error",
                error=str(exc),
                sql=sql[:500],
            )
            raise BigQueryClientError(
                f"Unexpected error during BigQuery dry run: {exc}",
                query=sql,
            ) from exc

        max_scan_bytes = int(self._settings.bq_max_scan_bytes)
        if estimated_bytes > max_scan_bytes:
            logger.warning(
                "bigquery_query_rejected_scan_limit",
                estimated_bytes=estimated_bytes,
                max_scan_bytes=max_scan_bytes,
                sql_preview=sql[:200],
            )
            raise BigQueryClientError(
                "Query aborted: estimated scan bytes exceed configured limit "
                f"({estimated_bytes} > {max_scan_bytes}).",
                query=sql,
            )

        logger.debug(
            "bigquery_dry_run_success",
            estimated_bytes=estimated_bytes,
            sql_preview=sql[:200],
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=query_parameters,
            maximum_bytes_billed=max_scan_bytes,
        )

        start = time.monotonic()
        try:
            query_job = self._client.query(sql, job_config=job_config)
            results = query_job.result()  # blocks until complete
            rows = [dict(row) for row in results]
        except GoogleCloudError as exc:
            logger.error(
                "bigquery_query_failed",
                error=str(exc),
                sql=sql[:500],
            )
            raise BigQueryClientError(
                f"BigQuery query failed: {exc}",
                query=sql,
            ) from exc
        except Exception as exc:
            logger.error(
                "bigquery_unexpected_error",
                error=str(exc),
                sql=sql[:500],
            )
            raise BigQueryClientError(
                f"Unexpected error during BigQuery query: {exc}",
                query=sql,
            ) from exc

        elapsed_ms = round((time.monotonic() - start) * 1000)
        logger.info(
            "bigquery_query_success",
            rows_returned=len(rows),
            elapsed_ms=elapsed_ms,
            estimated_bytes=estimated_bytes,
            sql_preview=sql[:200],
        )
        return rows

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        """Run a trivial query to verify BigQuery connectivity.

        Returns:
            Dict with ``connected`` (bool), ``project`` (str),
            ``latency_ms`` (int), and optionally ``error`` (str).
        """
        if self._client is None:
            return {
                "connected": False,
                "project": self._settings.gcp_project_id,
                "dataset": self._settings.gdelt_dataset,
                "latency_ms": 0,
                "error": "BigQuery client not initialized — no valid credentials.",
            }

        start = time.monotonic()
        try:
            self.execute_query("SELECT 1 AS healthcheck")
            latency_ms = round((time.monotonic() - start) * 1000)
            return {
                "connected": True,
                "project": self._settings.gcp_project_id,
                "dataset": self._settings.gdelt_dataset,
                "latency_ms": latency_ms,
            }
        except BigQueryClientError as exc:
            latency_ms = round((time.monotonic() - start) * 1000)
            return {
                "connected": False,
                "project": self._settings.gcp_project_id,
                "dataset": self._settings.gdelt_dataset,
                "latency_ms": latency_ms,
                "error": str(exc),
            }
