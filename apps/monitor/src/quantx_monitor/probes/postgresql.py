"""PostgreSQL reachability probe."""

from __future__ import annotations

import asyncpg

from ..models import MonitorStatus, ProbeResult, utc_now
from .base import timed_result


def _asyncpg_dsn(value: str) -> str:
  return value.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
    "postgresql+psycopg2://", "postgresql://", 1
  )


class PostgreSQLProbe:
  target_id = "postgresql"

  def __init__(self, database_url: str, timeout_seconds: float) -> None:
    self.database_url = database_url.strip()
    self.timeout_seconds = timeout_seconds

  async def run(self) -> ProbeResult:
    if not self.database_url:
      return ProbeResult(
        target_id=self.target_id,
        checked_at=utc_now(),
        observed_status=MonitorStatus.DISABLED,
      )

    async def query() -> int:
      connection = await asyncpg.connect(
        _asyncpg_dsn(self.database_url),
        timeout=self.timeout_seconds,
        command_timeout=self.timeout_seconds,
      )
      try:
        return int(await connection.fetchval("SELECT 1"))
      finally:
        await connection.close(timeout=self.timeout_seconds)

    def evaluate(value: int) -> tuple[MonitorStatus, int | None, str | None]:
      if value == 1:
        return MonitorStatus.HEALTHY, None, None
      return MonitorStatus.UNAVAILABLE, None, "PROTOCOL_ERROR"

    return await timed_result(self.target_id, query, evaluate)
