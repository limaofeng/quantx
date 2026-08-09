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


def _failure(exc: Exception) -> dict[str, Any]:
  return {
    "status": "unavailable",
    "error": exc.__class__.__name__,
  }


async def _postgresql() -> dict[str, Any]:
  url = make_url(settings.database_url)
  endpoint = f"{url.host or 'localhost'}:{url.port or 5432}"
  engine = create_async_engine(settings.database_url, pool_pre_ping=True)

  async def query_version() -> Any:
    async with engine.connect() as connection:
      return (await connection.execute(text("SHOW server_version"))).scalar_one()

  try:
    version = await asyncio.wait_for(query_version(), timeout=8.0)
    return {
      "status": "reachable",
      "endpoint": endpoint,
      "version": str(version),
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
