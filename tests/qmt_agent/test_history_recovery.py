"""Bounded recovery rotates retained jobs and isolates corrupt local evidence."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

from quantx_contracts.history_session import HistoryRequest
from quantx_contracts.history_upload import HistoryUploadSnapshot
from quantx_qmt_agent.history_pipeline import HistoryPipeline
from quantx_qmt_agent.runtime import AgentRuntime


async def test_recovery_is_bounded_and_bad_request_does_not_starve_next(tmp_path):
  runtime = SimpleNamespace(
    _market_spool_root=tmp_path,
    configuration=SimpleNamespace(
      device_id=str(UUID(int=9)), api_url="http://local.test"
    ),
    _market_data_upload_client=lambda: Mock(),
    _history_access_token=AsyncMock(),
    _historical_worker_lock=asyncio.Lock(),
    journal=SimpleNamespace(history_upload_retired=lambda *args: False),
  )
  pipeline = HistoryPipeline(runtime)
  ids = [UUID(int=value) for value in (1, 2, 3)]
  for request_id in ids:
    job = pipeline.jobs.retain(
      HistoryRequest(
        request_id=request_id,
        payload={"operation": "instrument_details", "stock_list": ["000001.SZ"]},
        unit_count=1,
        completed_units=0,
      ),
      reserve=Mock(),
      release=Mock(),
    )
    if request_id == ids[0]:
      (job.directory / "request.json").write_text("broken", encoding="utf-8")
  queried = []

  async def snapshot(request_id):
    queried.append(request_id)
    return HistoryUploadSnapshot(
      verified_at=None,
      request_id=request_id,
      status="DELIVERED",
      total_chunks=None,
      chunks=[],
    )

  pipeline._upload_snapshot = snapshot
  first = await pipeline.recover_retained_uploads()
  assert first == {
    str(ids[0]): "HISTORY_RECOVERY_BLOCKED",
    str(ids[1]): "WAITING_HISTORY_SESSION",
  }
  second = await pipeline.recover_retained_uploads()
  assert second[str(ids[2])] == "WAITING_HISTORY_SESSION"
  assert len(second) == 2
  assert queried == [str(ids[1]), str(ids[2])]


async def test_recovery_supervisor_stops_without_control_session():
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._stopped = asyncio.Event()

  async def recover():
    runtime._stopped.set()
    return {"request": "UPLOAD_ACCEPTED"}

  runtime._history_pipeline = SimpleNamespace(
    recover_retained_uploads=AsyncMock(side_effect=recover)
  )
  await asyncio.wait_for(runtime._history_recovery_supervisor(), 1)
  assert runtime._history_recovery_results == {"request": "UPLOAD_ACCEPTED"}
  runtime._history_pipeline.recover_retained_uploads.assert_awaited_once()
