"""Redis PING probe."""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis

from ..models import MonitorStatus, ProbeResult, utc_now
from .base import timed_result


class RedisProbe:
  target_id = "redis"

  def __init__(
    self,
    *,
    host: str,
    port: int,
    database: int,
    password: str,
    timeout_seconds: float,
  ) -> None:
    self.host = host
    self.port = port
    self.database = database
    self.password = password
    self.timeout_seconds = timeout_seconds

  async def run(self) -> ProbeResult:
    if not self.host.strip():
      return ProbeResult(
        target_id=self.target_id,
        checked_at=utc_now(),
        observed_status=MonitorStatus.DISABLED,
      )
    client = aioredis.Redis(
      host=self.host,
      port=self.port,
      db=self.database,
      password=self.password or None,
      socket_connect_timeout=self.timeout_seconds,
      socket_timeout=self.timeout_seconds,
      decode_responses=True,
    )

    async def ping() -> bool:
      return bool(await asyncio.wait_for(client.ping(), timeout=self.timeout_seconds))

    def evaluate(value: bool) -> tuple[MonitorStatus, int | None, str | None]:
      if value:
        return MonitorStatus.HEALTHY, None, None
      return MonitorStatus.UNAVAILABLE, None, "DEPENDENCY_NOT_READY"

    try:
      return await timed_result(self.target_id, ping, evaluate)
    finally:
      close = getattr(client, "aclose", client.close)
      await close()
