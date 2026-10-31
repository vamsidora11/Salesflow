from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_JWT_SECRET = "change-me-in-env"


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (and .env for host runs)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    app_env: str = "local"
    log_level: str = "INFO"

    # Defaults target services published on localhost by docker-compose.yml.
    # Inside Compose these are overridden via environment variables.
    database_url: str = "postgresql://salesflow:change-me@localhost:5432/salesflow"
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap_servers: str = "localhost:29092"

    # Upper bound for each dependency probe in /health/ready.
    health_check_timeout_seconds: float = 2.0

    # Auth
    jwt_secret: str = _INSECURE_JWT_SECRET
    jwt_expire_minutes: int = 60

    # Kafka topics (plan section 5)
    events_topic: str = "sales.events.v1"
    dlq_topic: str = "sales.events.dlq.v1"
    consumer_group: str = "sales-writer"

    # Ingestion (plan section 6)
    ingest_max_events: int = 500
    ingest_max_body_bytes: int = 256 * 1024
    ingest_rate_per_second: int = 50
    ingest_rate_burst: int = 100
    kafka_delivery_timeout_seconds: float = 5.0
    kafka_retry_after_seconds: int = 5

    # Consumer (plan section 7)
    consumer_batch_size: int = 500
    consumer_batch_timeout_seconds: float = 1.0
    consumer_max_attempts: int = 3
    consumer_backoff_seconds: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)
    consumer_metrics_port: int = 9100

    # Realtime (plan section 9)
    realtime_enabled: bool = True
    ws_auth_timeout_seconds: float = 5.0
    ws_heartbeat_seconds: float = 25.0
    ws_max_pending_updates: int = 500
    ws_send_timeout_seconds: float = 5.0

    # Analytics / exports (plan sections 10, 11)
    hourly_max_range_days: int = 366
    daily_max_range_days: int = 1830
    csv_sync_max_rows: int = 1_000_000
    excel_max_rows_per_sheet: int = 1_048_576
    export_chunk_rows: int = 5_000
    export_ttl_days: int = 7
    export_download_ttl_seconds: int = 15 * 60

    # Object storage (MinIO locally, S3 in production)
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_public_endpoint_url: str | None = None
    s3_access_key: str = "salesflow"
    s3_secret_key: str = "change-me"
    s3_bucket: str = "salesflow-exports"
    s3_region: str = "us-east-1"

    # Set (e.g. 5) to make every Celery Beat job fire on a short interval for testing.
    beat_test_interval_seconds: float | None = None

    # Retention (plan section 4)
    raw_retention_days: int = 90

    @model_validator(mode="after")
    def _reject_default_secret_outside_local(self) -> "Settings":
        if self.app_env != "local" and self.jwt_secret == _INSECURE_JWT_SECRET:
            raise ValueError("JWT_SECRET must be set when APP_ENV is not 'local'")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
