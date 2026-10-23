"""BigQuery SDK client with bounded REST retries and foreground query waits."""

from typing import Any

from google.api_core.retry import Retry
from google.cloud import bigquery
from google.cloud.bigquery.table import RowIterator

from .config import BigQueryConfig


def bounded_timeout(value: Any, seconds: float) -> float | tuple[Any, ...]:
    """Clamp an SDK scalar or connect/read tuple to a finite fallback budget.

    Args:
        value: Caller-supplied timeout; absent or nonpositive values use seconds.
        seconds: Positive maximum in seconds.

    Returns:
        Bounded scalar or tuple preserving any tighter caller limit.
    """
    if isinstance(value, (int, float)) and value > 0:
        return min(value, seconds)
    if isinstance(value, tuple):
        return tuple(bounded_timeout(part, seconds) for part in value)
    return seconds


class BoundedBigQueryClient(bigquery.Client):
    """BigQuery client with per-request, retry and foreground wait budgets.

    Args:
        timeout_seconds: Maximum REST call and retry budget.
        query_timeout_seconds: Maximum foreground query wait.
        **kwargs: Native BigQuery client arguments, including authentication.
    """

    def __init__(self, *, timeout_seconds: int, query_timeout_seconds: int, **kwargs: Any) -> None:
        self.timeout_seconds = timeout_seconds
        self.query_timeout_seconds = query_timeout_seconds
        super().__init__(**kwargs)

    def _call_api(self, retry: Retry | None, *args: Any, **kwargs: Any) -> Any:
        # Includes metadata and paginated result requests, not only query submit.
        kwargs["timeout"] = bounded_timeout(kwargs.get("timeout"), self.timeout_seconds)
        if retry is not None:
            retry = retry.with_timeout(self.timeout_seconds)
        return super()._call_api(retry, *args, **kwargs)

    def query_and_wait(self, query: str, **kwargs: Any) -> RowIterator:
        """Run a foreground query with bounded API and result-wait budgets.

        Args:
            query: SQL to execute.
            **kwargs: Native query options; tighter caller limits are preserved.

        Returns:
            Query result rows from the native SDK.
        """
        kwargs["api_timeout"] = bounded_timeout(kwargs.get("api_timeout"), self.timeout_seconds)
        kwargs["wait_timeout"] = bounded_timeout(kwargs.get("wait_timeout"), self.query_timeout_seconds)
        kwargs["job_retry"] = None
        return super().query_and_wait(query, **kwargs)


def create_client(config: BigQueryConfig) -> BoundedBigQueryClient:
    """Create an owned, bounded client while preserving dialect auth semantics.

    Args:
        config: Validated project, credentials, dataset and timeout settings.

    Returns:
        Client whose HTTP session must be closed by its owning engine.
    """
    import base64
    import json

    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    # Preserve the dialect's scopes, including Drive-backed external tables.
    scopes = [
        "https://www.googleapis.com/auth/bigquery",
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/drive",
    ]
    if config.credentials_path:
        credentials = service_account.Credentials.from_service_account_file(config.credentials_path, scopes=scopes)
    elif config.credentials_info or config.credentials_base64:
        info = (
            config.credentials_info.get_secret_value()
            if config.credentials_info
            else json.loads(base64.b64decode(config.credentials_base64.get_secret_value()))
        )
        credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        credentials, _ = google.auth.default(scopes=scopes)
    query_seconds = config.query_timeout_seconds or config.timeout_seconds
    job_config = bigquery.QueryJobConfig(job_timeout_ms=query_seconds * 1000)
    if config.dataset:
        job_config.default_dataset = f"{config.project}.{config.dataset}"
    return BoundedBigQueryClient(
        project=config.billing_project_id or config.project,
        credentials=credentials,
        location=config.location,
        default_query_job_config=job_config,
        timeout_seconds=config.timeout_seconds,
        query_timeout_seconds=query_seconds,
        _http=AuthorizedSession(credentials, refresh_timeout=config.timeout_seconds, max_refresh_attempts=1),
    )
