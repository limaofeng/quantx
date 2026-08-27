"""Configuration owned by the standalone monitor process."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _environment_files() -> tuple[str, ...]:
  configured = os.environ.get("QUANTX_ENV_FILE", "").strip()
  candidates = [configured] if configured else []
  candidates.extend([".env.production", ".env"])
  return tuple(value for value in candidates if value)


class MonitorSettings(BaseSettings):
  """Small, explicit settings surface for known QuantX targets."""

  host: str = Field(default="127.0.0.1", validation_alias="MONITOR_HOST")
  port: int = Field(default=18083, validation_alias="MONITOR_PORT")
  database_path: Path = Field(
    default=Path(".runtime/monitor/quantx-monitor.sqlite3"),
    validation_alias="MONITOR_DATABASE_PATH",
  )
  check_interval_seconds: float = Field(
    default=30.0,
    ge=5.0,
    le=300.0,
    validation_alias="MONITOR_CHECK_INTERVAL_SECONDS",
  )
  max_concurrency: int = Field(
    default=8,
    ge=1,
    le=32,
    validation_alias="MONITOR_MAX_CONCURRENCY",
  )
  http_timeout_seconds: float = Field(
    default=5.0,
    gt=0,
    le=30.0,
    validation_alias="MONITOR_HTTP_TIMEOUT_SECONDS",
  )
  postgresql_timeout_seconds: float = Field(
    default=5.0,
    gt=0,
    le=30.0,
    validation_alias="MONITOR_POSTGRESQL_TIMEOUT_SECONDS",
  )
  redis_timeout_seconds: float = Field(
    default=2.0,
    gt=0,
    le=30.0,
    validation_alias="MONITOR_REDIS_TIMEOUT_SECONDS",
  )
  raw_retention_days: int = Field(
    default=90,
    ge=7,
    le=365,
    validation_alias="MONITOR_RAW_RETENTION_DAYS",
  )
  rollup_retention_days: int = Field(
    default=365,
    ge=30,
    le=3650,
    validation_alias="MONITOR_ROLLUP_RETENTION_DAYS",
  )

  public_base_url: str = Field(
    default="http://127.0.0.1:8080",
    validation_alias="MONITOR_PUBLIC_BASE_URL",
  )
  api_url: str = Field(
    default="http://127.0.0.1:18081",
    validation_alias="MONITOR_API_URL",
  )
  market_data_service_url: str = Field(
    default="http://127.0.0.1:18082",
    validation_alias="MONITOR_MARKET_DATA_SERVICE_URL",
  )
  qmt_agent_health_url: str = Field(
    default="http://127.0.0.1:18084",
    validation_alias="MONITOR_QMT_AGENT_HEALTH_URL",
    repr=False,
  )
  database_url: str = Field(default="", validation_alias="DATABASE_URL")
  redis_host: str = Field(default="127.0.0.1", validation_alias="REDIS_HOST")
  redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
  redis_db: int = Field(default=0, validation_alias="REDIS_DB")
  redis_password: str = Field(default="", validation_alias="REDIS_PASSWORD")
  influxdb_host: str = Field(default="", validation_alias="INFLUXDB_HOST")
  influxdb_token: str = Field(default="", validation_alias="INFLUXDB_TOKEN")
  influxdb_ssl_verify: bool = Field(
    default=True,
    validation_alias="INFLUXDB_SSL_VERIFY",
  )
  prefect_enabled: bool = Field(default=True, validation_alias="PREFECT_ENABLED")
  prefect_api_url: str = Field(
    default="http://127.0.0.1:4200/api",
    validation_alias="PREFECT_API_URL",
  )

  model_config = SettingsConfigDict(
    env_file=_environment_files(),
    env_file_encoding="utf-8",
    case_sensitive=False,
    extra="ignore",
    hide_input_in_errors=True,
  )

  @field_validator("qmt_agent_health_url")
  @classmethod
  def validate_qmt_agent_health_url(cls, value: str) -> str:
    raw = value.strip()
    try:
      parsed = urlsplit(raw)
      port = parsed.port
    except ValueError:
      raise ValueError("QMT Agent health URL is invalid") from None
    if (
      parsed.scheme.lower() not in {"http", "https"}
      or not parsed.hostname
      or parsed.username is not None
      or parsed.password is not None
      or parsed.path not in {"", "/"}
      or parsed.query
      or parsed.fragment
      or port is None
    ):
      raise ValueError("QMT Agent health URL must be an absolute HTTP(S) root")
    return f"{parsed.scheme.lower()}://{parsed.netloc}"


settings = MonitorSettings()
