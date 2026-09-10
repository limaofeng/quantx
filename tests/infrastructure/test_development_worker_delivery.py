"""Worker-owned development demands reach real published local proofs."""
# ruff: noqa: F811

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts.market_data_service import HistoryDemand
from quantx_infrastructure.services import development_history_import as importer
from quantx_market_data import worker
from sqlalchemy import text

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_default_delivery import (
  delivery,  # noqa: F401
)
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


@pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)
async def test_worker_links_imports_and_stops_sweeping_completed_partition(delivery):
  case = delivery
  case.first.demand_source_kind = "REMOTE"
  demand = HistoryDemand.model_validate(case.request.model_dump())
  identity = await case.first.submit_history_demand(demand)
  assert await case.first.plan_history_demand()
  assert (await case.first.history_demand(identity))["delivery_id"] == case.identity
  assert await worker.advance_development_delivery(case.first)
  assert (await case.first.history_demand(identity))[
    "delivery_status"
  ] == "LOCAL_VERIFIED"
  calls = (len(case.calls), len(case.connection.lines), len(case.connection.queries))
  assert not await worker.advance_development_delivery(case.first)
  assert not await worker.advance_development_delivery(case.first)
  assert (
    len(case.calls),
    len(case.connection.lines),
    len(case.connection.queries),
  ) == calls
  await case.first.release()
  assert await case.second.acquire()
  case.second.demand_source_kind = "REMOTE"
  assert not await worker.advance_development_delivery(case.second)


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_due_selection_honors_backoff_and_owner(delivery):
  case = delivery
  case.first.demand_source_kind = "REMOTE"
  case.connection.unavailable = True
  assert await worker.advance_development_delivery(case.first)
  assert not await worker.advance_development_delivery(case.first)
  async with case.factory() as db:
    await db.execute(
      text(
        "UPDATE development_data_ingestion SET progress=jsonb_set(progress,'{next_retry_at}','null')"
      )
    )
    await db.commit()
  case.connection.unavailable = False
  assert await worker.advance_development_delivery(case.first)
  async with case.factory() as db:
    await db.execute(
      text(
        "UPDATE development_data_export SET state='WAITING_LOCAL_PROOF' WHERE id=:id"
      ),
      {"id": case.identity},
    )
    await db.execute(
      text(
        "UPDATE development_data_download_budget SET next_probe_at=clock_timestamp()+INTERVAL '1 hour'"
      )
    )
    await db.commit()
  assert not await worker.advance_development_delivery(case.first)
  await case.first.release()
  assert await case.second.acquire()
  with pytest.raises(RuntimeError, match="lease was lost"):
    await worker.advance_development_delivery(case.first)


@pytest.mark.parametrize("prepared", [False], indirect=True)
async def test_malformed_partition_isolated_from_next_delivery(delivery):
  case = delivery
  case.first.demand_source_kind = "REMOTE"
  async with case.factory() as db:
    await db.execute(
      text(
        "INSERT INTO development_data_export(id,request,state,updated_at) VALUES ('bad','{}','QUEUED','2000-01-01')"
      )
    )
    await db.commit()
  assert await worker.advance_development_delivery(case.first)
  async with case.factory() as db:
    bad = (
      await db.execute(
        text("SELECT state,error FROM development_data_export WHERE id='bad'")
      )
    ).one()
    assert bad == ("BLOCKED", "DELIVERY_SOURCE_INVALID")
  assert await worker.advance_development_delivery(case.first)
  assert not await worker.advance_development_delivery(case.first)


async def test_download_wait_does_not_block_renewal_receipts_or_shutdown(monkeypatch):
  from quantx_infrastructure.services import archive_recovery, realtime_archive_worker

  monkeypatch.setattr(
    archive_recovery, "advance_archive_recovery", AsyncMock(return_value=False)
  )

  monkeypatch.setattr(
    realtime_archive_worker, "advance_realtime_archive", AsyncMock(return_value=False)
  )
  entered, settled, renewed, receipt = (asyncio.Event() for _ in range(4))
  stop = asyncio.Event()

  async def advance(_store):
    entered.set()
    try:
      await asyncio.Event().wait()
    finally:
      await asyncio.sleep(0)
      settled.set()

  async def renew():
    renewed.set()
    return True

  async def consume():
    if entered.is_set():
      receipt.set()
    return 0

  async def release():
    assert settled.is_set()

  async def pause(stop, _seconds):
    try:
      await asyncio.wait_for(stop.wait(), 0.01)
    except TimeoutError:
      pass

  async def cleanup(stop, **kwargs):
    await stop.wait()

  from quantx_infrastructure.services import market_data_staging_cleanup

  monkeypatch.setattr(
    market_data_staging_cleanup, "run_market_data_staging_sweeper", cleanup
  )
  monkeypatch.setattr(worker, "advance_development_delivery", advance)
  monkeypatch.setattr(worker, "sweep", AsyncMock())
  monkeypatch.setattr(worker, "_pause", pause)
  store = SimpleNamespace(
    demand_source_kind="REMOTE",
    acquire=AsyncMock(return_value=True),
    renew=renew,
    consume_collection_receipts=consume,
    release=release,
    dispatch_history_collection=AsyncMock(),
  )
  task = asyncio.create_task(worker.run(store, stop))
  try:
    await asyncio.wait_for(
      asyncio.gather(entered.wait(), renewed.wait(), receipt.wait()), 1
    )
  finally:
    stop.set()
    await asyncio.wait_for(task, 1)


@pytest.mark.parametrize("completed", [False, True])
async def test_range_partition_submits_and_queries_without_executing(
  monkeypatch, completed
):
  from quantx_contracts.data_exchange import HistoryPartitionRequest
  from quantx_infrastructure.services import local_market_data_client

  request = HistoryPartitionRequest(
    instrument="600000.SH", period="1m", trading_date="2026-09-07"
  )
  client = SimpleNamespace(
    submit_history_demand=AsyncMock(return_value="d" * 64),
    history_demand=AsyncMock(
      return_value=SimpleNamespace(
        delivery_status="LOCAL_VERIFIED" if completed else "WAITING_LOCAL_INGESTION",
        delivery_id="delivery",
        reason_code="LOCAL_READBACK_UNAVAILABLE",
      )
    ),
    history_demand_result=AsyncMock(return_value=None),
    close=AsyncMock(),
  )
  monkeypatch.setattr(local_market_data_client, "LocalMarketDataClient", lambda: client)
  monkeypatch.setattr(
    importer,
    "import_partition",
    AsyncMock(side_effect=AssertionError("caller executed import")),
  )
  monkeypatch.setattr(
    importer, "get_export", AsyncMock(return_value={"state": "WAITING_LOCAL_PROOF"})
  )
  result = await importer.request_partition_delivery(request)
  assert result["status"] == (
    "WAITING_LOCAL_PROOF" if completed else "WAITING_LOCAL_INGESTION"
  )
  client.submit_history_demand.assert_awaited_once()
  client.history_demand.assert_awaited_once()
  client.close.assert_awaited_once()
