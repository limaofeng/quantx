"""Bounded, run-scoped progress independent of completed data batches."""

import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

PROGRESS_INTERVAL_SECONDS = 15.0


@dataclass
class MarketSyncObservation:
  logger: Any
  total: int = 0
  completed: int = 0
  failed: int = 0
  saved: int = 0
  phase: str = "规划分区"
  started: float = field(default_factory=time.monotonic)
  requests: dict[str, dict[str, Any]] = field(default_factory=dict)
  audit: Any = None
  instruments: list[dict[str, Any]] | None = None

  def request(self, request_id: str, **progress: Any) -> None:
    previous = self.requests.get(request_id, {})
    changed = previous.get("phase") != progress.get("phase")
    current = {**previous, **progress}
    current.setdefault("started", time.monotonic())
    if changed:
      current["phase_started"] = time.monotonic()
    self.requests[request_id] = current
    if changed:
      self.logger.info(
        "行情阶段变化: request_id=%s phase=%s detail=%s",
        request_id,
        current.get("phase"),
        current.get("detail", ""),
      )

  def emit(self) -> None:
    now = time.monotonic()
    self.logger.info(
      "行情同步心跳: phase=%s completed=%s/%s failed=%s active=%s "
      "saved=%s elapsed=%.1fs",
      self.phase,
      self.completed,
      self.total,
      self.failed,
      len(self.requests),
      self.saved,
      now - self.started,
    )
    for request_id, item in self.requests.items():
      self.logger.info(
        "行情请求进度: request_id=%s phase=%s phase_elapsed=%.1fs "
        "elapsed=%.1fs detail=%s",
        request_id,
        item.get("phase"),
        now - item.get("phase_started", now),
        now - item["started"],
        item.get("detail", ""),
      )


observation: ContextVar[MarketSyncObservation | None] = ContextVar(
  "market_sync_observation", default=None
)


@asynccontextmanager
async def observe_market_sync(logger: Any):
  observer = MarketSyncObservation(logger)
  token = observation.set(observer)

  async def report():
    while True:
      observer.emit()
      await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)

  reporter = asyncio.create_task(report(), name="market-sync-progress")
  try:
    yield observer
  finally:
    reporter.cancel()
    await asyncio.gather(reporter, return_exceptions=True)
    observer.emit()
    observation.reset(token)


def report_request(request_id: str, phase: str, detail: str = "") -> None:
  observer = observation.get()
  if observer is not None:
    observer.request(request_id, phase=phase, detail=detail)
