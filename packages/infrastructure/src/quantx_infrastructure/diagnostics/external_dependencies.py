"""Report external persistence-service connectivity and versions without mutation."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from quantx_infrastructure.config.settings import settings

WINDOWS_ABSOLUTE_PATH_PATTERN = r"^(?:[A-Za-z]:[\\/]|\\\\)"
ENGINE_LOCK_NAME = "quantx-engine-singleton-v1"
WINDOWS_PATH_AUDIT_COLUMNS = (
  ("strategies", "file_path"),
  ("strategy_backtests", "result_path"),
  ("strategy_grid_book_snapshots", "source_path"),
)


def _failure(exc: Exception) -> dict[str, Any]:
  return {
    "status": "unavailable",
    "error": exc.__class__.__name__,
  }


async def _postgresql() -> dict[str, Any]:
  url = make_url(settings.database_url)
  endpoint = f"{url.host or 'localhost'}:{url.port or 5432}"
  engine = create_async_engine(settings.database_url, pool_pre_ping=True)

  async def query_version_and_paths() -> tuple[
    Any,
    dict[str, int],
    dict[str, int],
    bool,
  ]:
    async with engine.connect() as connection:
      version = (await connection.execute(text("SHOW server_version"))).scalar_one()
      path_audit: dict[str, int] = {}
      for table_name, column_name in WINDOWS_PATH_AUDIT_COLUMNS:
        count = (
          await connection.execute(
            text(
              f'SELECT count(*) FROM "{table_name}" '
              f'WHERE "{column_name}" ~ :windows_path_pattern'
            ),
            {"windows_path_pattern": WINDOWS_ABSOLUTE_PATH_PATTERN},
          )
        ).scalar_one()
        path_audit[f"{table_name}.{column_name}"] = int(count or 0)
      active_market_data_count = (
        await connection.execute(
          text(
            "SELECT count(*) FROM market_data_transfer AS transfer "
            "JOIN market_data_request AS request "
            "ON request.request_id = transfer.request_id "
            "WHERE transfer.storage_reference ~ :windows_path_pattern "
            "AND upper(request.status) NOT IN ('COMPLETED', 'FAILED')"
          ),
          {
            "windows_path_pattern": WINDOWS_ABSOLUTE_PATH_PATTERN,
          },
        )
      ).scalar_one()
      terminal_market_data_count = (
        await connection.execute(
          text(
            "SELECT count(*) FROM market_data_transfer AS transfer "
            "JOIN market_data_request AS request "
            "ON request.request_id = transfer.request_id "
            "WHERE transfer.storage_reference ~ :windows_path_pattern "
            "AND upper(request.status) IN ('COMPLETED', 'FAILED')"
          ),
          {
            "windows_path_pattern": WINDOWS_ABSOLUTE_PATH_PATTERN,
          },
        )
      ).scalar_one()
      path_audit["market_data_transfer.storage_reference"] = int(
        active_market_data_count or 0
      )
      historical_path_audit = {
        "market_data_transfer.storage_reference": int(
          terminal_market_data_count or 0
        )
      }
      engine_lease_held = bool(
        (
          await connection.execute(
            text(
              "WITH engine_key AS ("
              "SELECT hashtext(:lock_name)::bigint AS value"
              ") "
              "SELECT EXISTS ("
              "SELECT 1 FROM pg_locks, engine_key "
              "WHERE locktype = 'advisory' "
              "AND classid = ((engine_key.value >> 32) & 4294967295) "
              "AND objid = (engine_key.value & 4294967295) "
              "AND objsubid = 1 AND granted"
              ")"
            ),
            {"lock_name": ENGINE_LOCK_NAME},
          )
        ).scalar_one()
      )
      return version, path_audit, historical_path_audit, engine_lease_held

  try:
    version, path_audit, historical_path_audit, engine_lease_held = await asyncio.wait_for(
      query_version_and_paths(), timeout=8.0
    )
    return {
      "status": "reachable",
      "endpoint": endpoint,
      "version": str(version),
      "windowsAbsolutePaths": path_audit,
      "windowsAbsolutePathCount": sum(path_audit.values()),
      "historicalWindowsAbsolutePaths": historical_path_audit,
      "historicalWindowsAbsolutePathCount": sum(historical_path_audit.values()),
      "engineLeaseHeld": engine_lease_held,
    }
  except Exception as exc:
    return {"endpoint": endpoint, **_failure(exc)}
  finally:
    await engine.dispose()


async def _redis() -> dict[str, Any]:
  endpoint = f"{settings.redis_host}:{settings.redis_port}"
  client = aioredis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    db=settings.redis_db,
    password=settings.redis_password or None,
    socket_connect_timeout=2.0,
    socket_timeout=2.0,
    decode_responses=True,
  )
  try:
    server = await asyncio.wait_for(client.info("server"), timeout=3.0)
    return {
      "status": "reachable",
      "endpoint": endpoint,
      "version": str(server.get("redis_version") or "unknown"),
    }
  except Exception as exc:
    return {"endpoint": endpoint, **_failure(exc)}
  finally:
    close = getattr(client, "aclose", client.close)
    await close()


async def _influxdb() -> dict[str, Any]:
  host = settings.influxdb_host.rstrip("/")
  if not host:
    return {
      "status": "not_configured",
      "endpoint": "",
      "version": "unknown",
    }
  headers = (
    {"Authorization": f"Token {settings.influxdb_token}"}
    if settings.influxdb_token
    else {}
  )
  try:
    async with httpx.AsyncClient(
      timeout=3.0,
      verify=settings.influxdb_ssl_verify,
      headers=headers,
    ) as client:
      response = await client.get(f"{host}/health")
      metrics_response = None
      if response.is_success:
        try:
          metrics_response = await client.get(f"{host}/metrics")
        except httpx.HTTPError:
          pass
    body: dict[str, Any] = {}
    try:
      value = response.json()
      if isinstance(value, dict):
        body = value
    except ValueError:
      pass
    version = (
      body.get("version")
      or response.headers.get("X-Influxdb-Version")
      or response.headers.get("X-InfluxDB-Version")
      or "unknown"
    )
    if version == "unknown" and metrics_response is not None:
      match = re.search(
        r'process_start_time_seconds\{[^}]*version="([^"]+)"',
        metrics_response.text,
      )
      if match:
        version = match.group(1)
    return {
      "status": "reachable" if response.is_success else "unavailable",
      "endpoint": host,
      "version": str(version),
      "statusCode": response.status_code,
    }
  except Exception as exc:
    return {"endpoint": host, **_failure(exc)}


async def collect_external_dependency_status() -> dict[str, dict[str, Any]]:
  postgresql, influxdb, redis = await asyncio.gather(
    _postgresql(),
    _influxdb(),
    _redis(),
  )
  return {
    "PostgreSQL": postgresql,
    "InfluxDB": influxdb,
    "Redis": redis,
  }


def main() -> None:
  print(
    json.dumps(
      asyncio.run(collect_external_dependency_status()),
      ensure_ascii=False,
      separators=(",", ":"),
    )
  )


if __name__ == "__main__":
  main()
