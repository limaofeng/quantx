from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import quantx_infrastructure.services.limit_up_board_replay_projection_service as module
from quantx_infrastructure.services.limit_up_board_replay_projection_service import (
  LimitUpBoardReplayProjectionService,
)


@pytest.mark.asyncio
async def test_job_inputs_bind_once_and_preflight_error_is_terminal(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rows = {}
  now = datetime(2026, 8, 20, 10, 0)

  class FakeDb:
    def add(self, row) -> None:
      rows[row.id] = row

    async def commit(self) -> None:
      return None

    async def refresh(self, row) -> None:
      row.created_at = getattr(row, "created_at", None) or now
      row.updated_at = now + timedelta(seconds=int(row.revision or 0))

  class FakeRepository:
    def __init__(self, _db) -> None:
      pass

    async def get_job(self, job_id: str, *, for_update: bool = False):
      del for_update
      return rows.get(job_id)

    async def list_scenarios(self, _job_id: str):
      return []

  async def fake_get_async_db():
    yield FakeDb()

  publish = AsyncMock(return_value=1)
  monkeypatch.setattr(module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(module, "LimitUpBoardReplayRepository", FakeRepository)
  monkeypatch.setattr(module.redis_pubsub, "publish", publish)
  service = LimitUpBoardReplayProjectionService()
  manifest = {
    "schema_version": 1,
    "dataset_fingerprint": "d" * 64,
    "config_fingerprint": "c" * 64,
  }
  quality = {"status": "OK", "executable": True}

  created = await service.create_job(
    job_id="job-1",
    account_id="account-1",
    scenario_profile="STANDARD_V1",
    request={"start": "2026-08-20"},
    dataset_fingerprint="d" * 64,
    config_fingerprint="c" * 64,
    input_manifest=manifest,
    data_quality=quality,
  )
  repeated = await service.create_job(
    job_id="job-1",
    account_id="account-1",
    scenario_profile="STANDARD_V1",
    request={"start": "2026-08-20"},
    dataset_fingerprint="d" * 64,
    config_fingerprint="c" * 64,
    input_manifest=manifest,
    data_quality=quality,
  )
  with pytest.raises(ValueError, match="输入 manifest"):
    await service.create_job(
      job_id="job-1",
      account_id="account-1",
      scenario_profile="STANDARD_V1",
      request={"start": "2026-08-20"},
      dataset_fingerprint="d" * 64,
      config_fingerprint="c" * 64,
      input_manifest={**manifest, "artifacts": {}},
      data_quality=quality,
    )

  failed = await service.update_job_error(
    job_id="job-1",
    error_message="raw tick coverage is incomplete",
  )
  late_cancel = await service.cancel_job(job_id="job-1", reason="too late")

  assert created["revision"] == "1"
  assert repeated == created
  assert failed["status"] == "ERROR"
  assert failed["revision"] == "2"
  assert failed["progress_pct"] == 0.0
  assert failed["completed_at"] is not None
  assert late_cancel == failed
  assert publish.await_count == 2
  assert publish.await_args_list[-1].args[1]["kind"] == "RESULT_READY"


@pytest.mark.asyncio
async def test_bind_scenario_rejects_optimistic_execution_parameters() -> None:
  service = LimitUpBoardReplayProjectionService()

  with pytest.raises(ValueError, match="成交参与率"):
    await service.bind_scenario(
      job_id="job-1",
      scenario_id="base",
      backtest_id="backtest-1",
      confirmation_delay_ms=1000,
      participation_cap_pct=0.0,
      book_depth_participation_pct=0.1,
    )
  with pytest.raises(ValueError, match="五档盘口参与率"):
    await service.bind_scenario(
      job_id="job-1",
      scenario_id="base",
      backtest_id="backtest-1",
      confirmation_delay_ms=1000,
      participation_cap_pct=0.1,
      book_depth_participation_pct=1.1,
    )
  with pytest.raises(ValueError, match="确认延迟"):
    await service.bind_scenario(
      job_id="job-1",
      scenario_id="base",
      backtest_id="backtest-1",
      confirmation_delay_ms=-1,
      participation_cap_pct=0.1,
      book_depth_participation_pct=0.1,
    )
