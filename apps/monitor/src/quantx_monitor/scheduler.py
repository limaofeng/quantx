"""Concurrent fixed-target scheduler and retention loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic

import httpx

from .config import MonitorSettings
from .models import ProbeResult, utc_now
from .probes import (
  HttpProbe,
  PostgreSQLProbe,
  QmtAgentHealthProbe,
  RedisProbe,
  RuntimeSnapshotProbe,
  combine_qmt_agent_probe,
)
from .probes.http import json_status
from .storage import MonitorStorage

logger = logging.getLogger(__name__)


class MonitorScheduler:
  def __init__(self, settings: MonitorSettings, storage: MonitorStorage) -> None:
    self.settings = settings
    self.storage = storage
    self._stop = asyncio.Event()
    self._cycle_lock = asyncio.Lock()
    self._tasks: list[asyncio.Task[None]] = []
    self.last_cycle_at: float | None = None
    self.last_persist_error: str | None = None
    self._client: httpx.AsyncClient | None = None

  @property
  def running(self) -> bool:
    return bool(self._tasks) and all(not task.done() for task in self._tasks)

  async def start(self) -> None:
    self._stop.clear()
    self._client = httpx.AsyncClient(trust_env=False)
    self._tasks = [
      asyncio.create_task(self._run_cycles(), name="monitor-probe-cycles"),
      asyncio.create_task(self._run_maintenance(), name="monitor-retention"),
    ]

  async def stop(self) -> None:
    self._stop.set()
    for task in self._tasks:
      task.cancel()
    await asyncio.gather(*self._tasks, return_exceptions=True)
    self._tasks = []
    if self._client is not None:
      await self._client.aclose()
      self._client = None

  async def _run_cycles(self) -> None:
    while not self._stop.is_set():
      started = monotonic()
      try:
        await self.run_cycle()
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception("monitor probe cycle failed")
      elapsed = monotonic() - started
      delay = max(0.1, self.settings.check_interval_seconds - elapsed)
      try:
        await asyncio.wait_for(self._stop.wait(), timeout=delay)
      except TimeoutError:
        pass

  async def run_cycle(self) -> None:
    if self._cycle_lock.locked():
      return
    async with self._cycle_lock:
      assert self._client is not None
      semaphore = asyncio.Semaphore(self.settings.max_concurrency)
      direct: list[Callable[[], Awaitable[ProbeResult]]] = []

      postgresql = PostgreSQLProbe(
        self.settings.database_url,
        self.settings.postgresql_timeout_seconds,
      )
      redis = RedisProbe(
        host=self.settings.redis_host,
        port=self.settings.redis_port,
        database=self.settings.redis_db,
        password=self.settings.redis_password,
        timeout_seconds=self.settings.redis_timeout_seconds,
      )
      direct.extend([postgresql.run, redis.run])

      influx_headers = (
        {"Authorization": f"Token {self.settings.influxdb_token}"}
        if self.settings.influxdb_token
        else {}
      )
      prefect_url = self.settings.prefect_api_url.rstrip("/")
      if not prefect_url.endswith("/api"):
        prefect_url += "/api"
      http_probes = [
        HttpProbe(
          "influxdb",
          f"{self.settings.influxdb_host.rstrip('/')}/health",
          timeout_seconds=self.settings.http_timeout_seconds,
          verify=self.settings.influxdb_ssl_verify,
          headers=influx_headers,
          enabled=bool(self.settings.influxdb_host.strip()),
        ),
        HttpProbe(
          "prefect-server",
          f"{prefect_url}/health",
          timeout_seconds=self.settings.http_timeout_seconds,
          enabled=self.settings.prefect_enabled,
        ),
        HttpProbe(
          "web-entry",
          f"{self.settings.public_base_url.rstrip('/')}/",
          timeout_seconds=self.settings.http_timeout_seconds,
        ),
        HttpProbe(
          "docs",
          f"{self.settings.public_base_url.rstrip('/')}/docs/",
          timeout_seconds=self.settings.http_timeout_seconds,
        ),
        HttpProbe(
          "api-public",
          f"{self.settings.public_base_url.rstrip('/')}/health/live",
          timeout_seconds=self.settings.http_timeout_seconds,
          evaluator=json_status("alive"),
        ),
        HttpProbe(
          "api-process",
          f"{self.settings.api_url.rstrip('/')}/health/live",
          timeout_seconds=self.settings.http_timeout_seconds,
          evaluator=json_status("alive"),
        ),
        HttpProbe(
          "market-gateway",
          f"{self.settings.market_gateway_url.rstrip('/')}/health/ready",
          timeout_seconds=self.settings.http_timeout_seconds,
          evaluator=json_status("ready"),
        ),
      ]
      direct.extend(
        [lambda probe=probe: probe.run(self._client) for probe in http_probes]
      )
      qmt_agent_probe = QmtAgentHealthProbe(
        self.settings.qmt_agent_health_url,
        self.settings.http_timeout_seconds,
      )
      direct.append(lambda: qmt_agent_probe.run(self._client))

      async def guarded(action: Callable[[], Awaitable[ProbeResult]]) -> ProbeResult:
        async with semaphore:
          return await action()

      direct_results = await asyncio.gather(
        *(guarded(action) for action in direct),
      )
      snapshot = RuntimeSnapshotProbe(
        f"{self.settings.api_url.rstrip('/')}/health/components",
        self.settings.http_timeout_seconds,
      )
      async with semaphore:
        derived_results = await snapshot.run(self._client)
      qmt_direct = next(
        result for result in direct_results if result.target_id == "qmt-agent"
      )
      qmt_semantic = next(
        result for result in derived_results if result.target_id == "qmt-agent"
      )
      qmt_result = combine_qmt_agent_probe(qmt_direct, qmt_semantic)
      results = [
        *(result for result in direct_results if result.target_id != "qmt-agent"),
        *(result for result in derived_results if result.target_id != "qmt-agent"),
        qmt_result,
      ]
      try:
        await self.storage.record_results(results)
        self.last_cycle_at = utc_now().timestamp()
        self.last_persist_error = None
      except Exception as exc:
        self.last_persist_error = exc.__class__.__name__
        raise

  async def _run_maintenance(self) -> None:
    while not self._stop.is_set():
      try:
        now = utc_now().timestamp()
        await self.storage.rollup_and_retain(
          now=now,
          raw_retention_seconds=self.settings.raw_retention_days * 86400,
          rollup_retention_seconds=self.settings.rollup_retention_days * 86400,
        )
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception("monitor retention cycle failed")
      try:
        await asyncio.wait_for(self._stop.wait(), timeout=3600)
      except TimeoutError:
        pass
