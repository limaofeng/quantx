from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import quantx_infrastructure.services.t_trade_replay_projection_service as module
from quantx_infrastructure.services.t_trade_replay_projection_service import (
  TTradeReplayProjectionService,
  TTradeReplayUpdateKind,
)


@pytest.mark.asyncio
async def test_replay_projection_is_monotonic_and_publishes_after_changes(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rows = {}
  now = datetime(2026, 8, 16, 10, 0)

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
  monkeypatch.setattr(module, "TTradeReplayProjectionRepository", FakeRepository)
  monkeypatch.setattr(module.redis_pubsub, "publish", publish)
  service = TTradeReplayProjectionService()

  created = await service.create(run_id="run-1", account_id="account-1")
  progressed = await service.update(
    run_id="run-1",
    account_id="account-1",
    status="RUNNING",
    progress_pct=42.0,
    phase="REPLAYING",
    processed_until=now,
    kind=TTradeReplayUpdateKind.PROGRESS,
  )
  regressed = await service.update(
    run_id="run-1",
    account_id="account-1",
    status="RUNNING",
    progress_pct=12.0,
    processed_until=now - timedelta(minutes=1),
    kind=TTradeReplayUpdateKind.PROGRESS,
  )
  completed = await service.update(
    run_id="run-1",
    account_id="account-1",
    status="COMPLETED",
    progress_pct=50.0,
    processed_until=now + timedelta(hours=1),
    kind=TTradeReplayUpdateKind.RESULT_READY,
  )
  late_progress = await service.update(
    run_id="run-1",
    account_id="account-1",
    status="RUNNING",
    progress_pct=99.9,
    processed_until=now + timedelta(hours=2),
    kind=TTradeReplayUpdateKind.PROGRESS,
  )

  assert created["revision"] == "1"
  assert progressed["revision"] == "2"
  assert progressed["phase"] == "REPLAYING"
  assert progressed["phase_progress_pct"] == 42.0
  assert regressed["revision"] == "2"
  assert regressed["progress_pct"] == 42.0
  assert regressed["phase_progress_pct"] == 42.0
  assert regressed["processed_until"] == now
  assert completed["revision"] == "3"
  assert completed["progress_pct"] == 100.0
  assert completed["phase"] == "COMPLETED"
  assert completed["phase_progress_pct"] == 100.0
  assert completed["processed_until"] == now + timedelta(hours=1)
  assert late_progress == completed
  assert publish.await_count == 3
  assert publish.await_args_list[-1].args[1]["kind"] == "RESULT_READY"
