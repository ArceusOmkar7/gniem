"""Unit tests for BigQueryClient."""

import pytest
from unittest.mock import MagicMock, patch

from backend.infrastructure.config.settings import Settings
from backend.infrastructure.data_access.bigquery_client import BigQueryClient, BigQueryClientError
from google.cloud.exceptions import GoogleCloudError

@pytest.fixture
def test_settings():
    return Settings(
        gcp_project_id="test-project",
        bq_max_scan_bytes=1000,
        google_application_credentials=None
    )

class TestBigQueryClient:
    @patch('backend.infrastructure.data_access.bigquery_client.bigquery.Client')
    def test_init_success(self, mock_bq_client, test_settings):
        client = BigQueryClient(test_settings)
        assert client._client is not None
        mock_bq_client.assert_called_once_with(project="test-project", credentials=None)
        
    @patch('backend.infrastructure.data_access.bigquery_client.bigquery.Client')
    def test_init_failure(self, mock_bq_client, test_settings):
        mock_bq_client.side_effect = Exception("Init error")
        client = BigQueryClient(test_settings)
        # Should gracefully handle failure and set _client to None
        assert client._client is None

    def test_execute_query_without_client(self, test_settings):
        client = BigQueryClient(test_settings)
        client._client = None
        
        with pytest.raises(BigQueryClientError, match="not initialized"):
            client.execute_query("SELECT 1")

    @patch('backend.infrastructure.data_access.bigquery_client.bigquery.Client')
    def test_execute_query_scan_limit_exceeded(self, mock_bq_client, test_settings):
        client = BigQueryClient(test_settings)
        
        mock_job = MagicMock()
        mock_job.total_bytes_processed = 5000  # Exceeds max 1000
        client._client.query.return_value = mock_job
        
        with pytest.raises(BigQueryClientError, match="estimated scan bytes exceed configured limit"):
            client.execute_query("SELECT * FROM massive_table")

    @patch('backend.infrastructure.data_access.bigquery_client.bigquery.Client')
    def test_execute_query_dry_run_failure(self, mock_bq_client, test_settings):
        client = BigQueryClient(test_settings)
        
        client._client.query.side_effect = GoogleCloudError("Bad query")
        
        with pytest.raises(BigQueryClientError, match="dry run failed"):
            client.execute_query("SELECT invalid syntax")

    @patch('backend.infrastructure.data_access.bigquery_client.bigquery.Client')
    def test_execute_query_success(self, mock_bq_client, test_settings):
        client = BigQueryClient(test_settings)
        
        # Setup dry run job
        mock_dry_run = MagicMock()
        mock_dry_run.total_bytes_processed = 500
        
        # Setup actual query job
        mock_query_job = MagicMock()
        # Mock rows returned
        mock_query_job.result.return_value = [{"n": 1}]
        
        # Return dry run first, then actual
        client._client.query.side_effect = [mock_dry_run, mock_query_job]
        
        rows = client.execute_query("SELECT 1 AS n")
        assert rows == [{"n": 1}]
        assert client._client.query.call_count == 2
        actual_config = client._client.query.call_args.kwargs["job_config"]
        assert actual_config.maximum_bytes_billed == test_settings.bq_max_scan_bytes

    @patch('backend.infrastructure.data_access.bigquery_client.bigquery.Client')
    def test_health_check_success(self, mock_bq_client, test_settings):
        client = BigQueryClient(test_settings)
        
        mock_dry_run = MagicMock()
        mock_dry_run.total_bytes_processed = 10
        mock_query_job = MagicMock()
        client._client.query.side_effect = [mock_dry_run, mock_query_job]
        
        res = client.health_check()
        assert res["connected"] is True
        assert res["project"] == "test-project"

    def test_health_check_no_client(self, test_settings):
        client = BigQueryClient(test_settings)
        client._client = None
        
        res = client.health_check()
        assert res["connected"] is False
        assert "not initialized" in res["error"]
