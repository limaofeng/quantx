from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import quantx_infrastructure.services.exit_plan_replay_projection_service as module
from quantx_infrastructure.services.exit_plan_replay_projection_service import (
  ExitPlanReplayProjectionService,
  ExitPlanReplayUpdateKind,
)


@pytest.mark.asyncio
async def test_exit_plan_replay_projection_is_monotonic(monkeypatch) -> None:
  rows = {}
  now = datetime(2026, 8, 20, 10, 0)

  class FakeDb:
    def add(self, row) -> None:
      rows[row.run_id] = row

    async def commit(self) -> None:
      return None

    async def refresh(self, row) -> None:
      row.created_at = getattr(row, "created_at", None) or now
      row.updated_at = now + timedelta(seconds=int(row.revision or 0))

  class FakeRepository:
    def __init__(self, _db) -> None:
      pass

    async def get(self, run_id: str, *, for_update: bool = False):
      del for_update
      return rows.get(run_id)

  async def fake_get_async_db():
    yield FakeDb()

  publish = AsyncMock(return_value=1)
  monkeypatch.setattr(module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(module, "ExitPlanReplayProjectionRepository", FakeRepository)
  monkeypatch.setattr(module.redis_pubsub, "publish", publish)
  service = ExitPlanReplayProjectionService()

  created = await service.create(
    run_id="run-1",
    account_id="account-1",
    plan_id="plan-1",
    instrument_code="000001.SZ",
  )
  progressed = await service.update(
    run_id="run-1",
    account_id="account-1",
    status="RUNNING",
    progress_pct=42.0,
    processed_until=now,
    kind=ExitPlanReplayUpdateKind.PROGRESS,
  )
  regressed = await service.update(
    run_id="run-1",
    account_id="account-1",
    status="RUNNING",
    progress_pct=12.0,
    processed_until=now - timedelta(minutes=1),
    kind=ExitPlanReplayUpdateKind.PROGRESS,
  )
  completed = await service.update(
    run_id="run-1",
    account_id="account-1",
    status="COMPLETED",
    progress_pct=50.0,
    processed_until=now + timedelta(hours=1),
    kind=ExitPlanReplayUpdateKind.RESULT_READY,
  )

  assert created["revision"] == "1"
  assert progressed["revision"] == "2"
  assert regressed["revision"] == "2"
  assert regressed["progress_pct"] == 42.0
  assert completed["progress_pct"] == 100.0
  assert publish.await_count == 3
