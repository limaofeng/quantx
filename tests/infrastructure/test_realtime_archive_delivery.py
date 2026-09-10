"""Real local PG, HTTP admission and SDK/Arrow version publication."""
# ruff: noqa: F811

import asyncio
import importlib.util
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_contracts.realtime_archive import ArchiveRevision
from quantx_infrastructure.services import realtime_archive_store as catalog
from quantx_infrastructure.services import realtime_archive_worker as archive_worker
from quantx_infrastructure.services.engine_archive_generation import (
  register_engine_archive_generation,
)
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_infrastructure.services.market_data_worker_store import (
  MarketDataWorkerStore,
)
from quantx_market_data import worker
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_engine_archive_generation import (  # noqa: F401
  archive_db,
  lock,
  unlock,
)
from tests.infrastructure.test_immutable_bar_storage import VersionStorage


@pytest.fixture
async def archive_case(archive_db, monkeypatch):
  engine = archive_db.engine
  root = (
    Path(__file__).resolve().parents[2] / "packages/infrastructure/alembic/versions"
  )
  for name in (
    "20260909_0065_market_data_worker_lease.py",
    "20260910_0085_realtime_archive_inbox.py",
  ):
    spec = importlib.util.spec_from_file_location("archive_dependency", root / name)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with engine.begin() as db:
      if name.startswith("20260909"):
        await db.execute(
          text("CREATE TABLE market_data_request(request_id text PRIMARY KEY)")
        )

      def upgrade(connection):
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

      await db.run_sync(upgrade)
  owners = []
  for _ in range(2):
    owner = object.__new__(MarketDataWorkerStore)
    owner.engine, owner.owner_id, owner.epoch = engine, str(uuid4()), None
    owner.demand_source_kind = "AGENT"
    owners.append(owner)
  assert await owners[0].acquire()
  storage = VersionStorage()
  monkeypatch.setattr(archive_worker, "get_timeseries_connection", lambda: storage)
  async with engine.connect() as source:
    await lock(source)
    generation = await register_engine_archive_generation(source, str(uuid4()))
    request = ArchiveRevision(
      instrument="600000.SH",
      minute="2026-09-10T09:31:00+08:00",
      generation=generation,
      continuity_generation=1,
      stream_id=uuid4(),
      sequence=10,
      sealed=False,
      bar={
        "open": 10.0,
        "high": 10.2,
        "low": 9.9,
        "close": 10.1,
        "pre_close": 9.8,
        "volume": 100.0,
        "amount": 1010.0,
        "settelement_price": 0.0,
        "open_interest": 0,
        "suspend_flag": 0,
      },
    )
    app = create_app(store=owners[0], token="internal", reader=SimpleNamespace())
    try:
      async with app.router.lifespan_context(app):
        client = LocalMarketDataClient(
          transport=httpx.ASGITransport(app), token="internal"
        )
        try:
          yield SimpleNamespace(
            engine=engine,
            first=owners[0],
            second=owners[1],
            source=source,
            storage=storage,
            request=request,
            store=catalog.RealtimeArchiveStore(engine),
            client=client,
            migration=migration,
          )
        finally:
          await client.close()
    finally:
      await unlock(source)


def change(request, **updates):
  return ArchiveRevision.model_validate({**request.model_dump(), **updates})


async def due(case):
  async with case.engine.begin() as db:
    await db.execute(
      text(
        "UPDATE realtime_archive_revision SET next_retry_at=clock_timestamp()-INTERVAL '1 second' WHERE phase IN ('WRITE','READBACK')"
      )
    )


async def publish(case, owner=None):
  for _ in range(2):
    assert await archive_worker.advance_realtime_archive(owner or case.first)


async def test_http_acceptance_is_not_proof_and_default_worker_publishes(
  archive_case, monkeypatch
):
  case = archive_case
  identity = await case.client.submit_archive(case.request)
  assert identity == case.request.identity()
  assert await case.client.submit_archive(case.request) == identity
  status = await case.client.archive_status(case.request)
  assert status.phase == "WRITE" and status.proof is None
  assert not case.storage.lines
  await case.first.release()
  stop = asyncio.Event()

  async def idle(*args, **kwargs):
    await stop.wait()

  async def receipts():
    if (await case.store.status(identity)).phase == "VERIFIED":
      stop.set()

  from quantx_infrastructure.services import market_data_staging_cleanup

  monkeypatch.setattr(
    market_data_staging_cleanup, "run_market_data_staging_sweeper", idle
  )
  monkeypatch.setattr(worker, "sweep", idle)
  monkeypatch.setattr(case.first, "dispatch_history_collection", AsyncMock())
  monkeypatch.setattr(case.first, "consume_collection_receipts", receipts)
  monkeypatch.setenv("ENV", "testing")
  await asyncio.wait_for(worker.run(case.first, stop), 5)
  status = await case.client.archive_status(case.request)
  assert status.phase == "VERIFIED" and status.proof.records_verified == 1
  assert status.write_attempts == status.read_attempts == 1
  selected = await case.store.published(case.request.instrument, case.request.minute)
  assert selected.request_id == identity
  assert len(case.storage.lines) == 1
  assert all(table == "kline_1m_versions" for table, _ in case.storage.points.values())


async def test_conflict_old_revision_stream_change_and_seal_rules(archive_case):
  case = archive_case
  await case.client.submit_archive(case.request)
  for bad in (
    change(case.request, bar={**case.request.bar.model_dump(), "close": 10.15}),
    change(case.request, sequence=9),
    change(case.request, stream_id=uuid4(), sequence=11),
  ):
    with pytest.raises(httpx.HTTPStatusError) as error:
      await case.client.submit_archive(bad)
    assert error.value.response.status_code == 409
  sealed = change(case.request, sealed=True)
  await case.client.submit_archive(sealed)
  with pytest.raises(httpx.HTTPStatusError) as error:
    await case.client.submit_archive(change(sealed, sequence=11))
  assert error.value.response.status_code == 409
  next_minute = change(
    case.request,
    minute=case.request.minute + timedelta(minutes=1),
    sequence=12,
    continuity_generation=2,
    stream_id=uuid4(),
  )
  await case.client.submit_archive(next_minute)
  with pytest.raises(httpx.HTTPStatusError) as error:
    await case.client.submit_archive(
      change(
        case.request, minute=next_minute.minute + timedelta(minutes=1), sequence=13
      )
    )
  assert error.value.response.status_code == 409


async def test_newest_unverified_revision_hides_old_proof_and_late_write_is_isolated(
  archive_case,
):
  case = archive_case
  await case.client.submit_archive(case.request)
  await publish(case)
  newer = change(
    case.request, sequence=11, bar={**case.request.bar.model_dump(), "close": 10.2}
  )
  await case.client.submit_archive(newer)
  assert await case.store.published(newer.instrument, newer.minute) is None
  await publish(case)
  archive_worker.write(case.request, case.storage)  # Delayed old external write.
  selected = await case.store.published(newer.instrument, newer.minute)
  assert selected.request == newer
  rows = [
    row
    for _, row in case.storage.points.values()
    if row["storage_version"] == newer.storage_version()
  ]
  assert len(rows) == 1 and rows[0]["close"] == 10.2
  assert len(case.storage.points) == 2


async def test_readback_retry_does_not_rewrite_and_attempt_budget_survives_owner_change(
  archive_case, monkeypatch
):
  case = archive_case
  await case.client.submit_archive(case.request)
  assert await archive_worker.advance_realtime_archive(case.first)

  def failed(**kwargs):
    raise OSError("unavailable")

  query = case.storage.query
  monkeypatch.setattr(case.storage, "query", failed)
  for index in range(4):
    await due(case)
    owner = case.first if index == 0 else case.second
    if index == 1:
      await case.first.release()
      assert await case.second.acquire()
    assert await archive_worker.advance_realtime_archive(owner)
  status = await case.client.archive_status(case.request)
  assert status.phase == "BLOCKED" and status.read_attempts == 4
  assert len(case.storage.lines) == 1
  monkeypatch.setattr(case.storage, "query", query)
  await due(case)
  assert not await archive_worker.advance_realtime_archive(case.second)


async def test_old_worker_cannot_commit_after_real_write(archive_case, monkeypatch):
  case = archive_case
  await case.client.submit_archive(case.request)
  original = archive_worker._joined_thread

  async def lose_owner(function, *args):
    result = await original(function, *args)
    await case.first.release()
    assert await case.second.acquire()
    return result

  monkeypatch.setattr(archive_worker, "_joined_thread", lose_owner)
  with pytest.raises(RuntimeError, match="lease was lost"):
    await archive_worker.advance_realtime_archive(case.first)
  status = await case.client.archive_status(case.request)
  assert status.phase == "WRITE" and status.proof is None and status.write_attempts == 1
  monkeypatch.setattr(archive_worker, "_joined_thread", original)
  await due(case)
  await publish(case, case.second)
  assert (await case.client.archive_status(case.request)).phase == "VERIFIED"


async def test_capacity_and_request_body_limits_precede_new_evidence(
  archive_case, monkeypatch
):
  case = archive_case
  monkeypatch.setattr(catalog, "MAX_ARCHIVE_PENDING", 1)
  await case.client.submit_archive(case.request)
  assert await case.client.submit_archive(case.request) == case.request.identity()
  with pytest.raises(httpx.HTTPStatusError) as error:
    await case.client.submit_archive(change(case.request, sequence=11))
  assert error.value.response.status_code == 429
  reply = await case.client.client.post(
    "/market-data/internal/v1/archives", content=b" " * 4097
  )
  assert reply.status_code == 413
  reply = await case.client.client.post("/market-data/internal/v1/archives", json={})
  assert reply.status_code == 422
  reply = await case.client.client.post(
    "/market-data/internal/v1/archives",
    headers={"Authorization": "Bearer wrong"},
    json=case.request.model_dump(mode="json"),
  )
  assert reply.status_code == 401


async def test_source_exit_allows_receipt_replay_but_not_new_admission(archive_case):
  case = archive_case
  await case.client.submit_archive(case.request)
  await unlock(case.source)
  try:
    assert await case.client.submit_archive(case.request) == case.request.identity()
    with pytest.raises(httpx.HTTPStatusError) as error:
      await case.client.submit_archive(change(case.request, sequence=11))
    assert error.value.response.status_code == 409
    await publish(case)
    assert (await case.client.archive_status(case.request)).phase == "VERIFIED"
  finally:
    await lock(case.source)


async def test_downgrade_preserves_accepted_evidence(archive_case):
  case = archive_case
  await case.client.submit_archive(case.request)
  async with case.engine.begin() as db:

    def downgrade(connection):
      case.migration.op = Operations(MigrationContext.configure(connection))
      case.migration.downgrade()

    with pytest.raises(RuntimeError, match="cannot remove realtime archive evidence"):
      await db.run_sync(downgrade)
    assert await db.scalar(text("SELECT count(*) FROM realtime_archive_revision")) == 1


async def test_archive_write_wait_keeps_renewal_receipts_and_orderly_stop(
  archive_case, monkeypatch
):
  import threading

  case = archive_case
  await case.client.submit_archive(case.request)
  await case.first.release()
  entered, release = threading.Event(), threading.Event()
  renewed, consumed = asyncio.Event(), asyncio.Event()
  stop = asyncio.Event()
  original_write, original_renew = case.storage.write, case.first.renew

  def slow_write(**kwargs):
    entered.set()
    assert release.wait(5)
    return original_write(**kwargs)

  async def renew():
    result = await original_renew()
    if entered.is_set():
      renewed.set()
    return result

  async def receipts():
    if entered.is_set():
      consumed.set()

  async def idle(*args, **kwargs):
    await stop.wait()

  pause = worker._pause

  async def fast_pause(event, _seconds):
    await pause(event, 0.01)

  from quantx_infrastructure.services import market_data_staging_cleanup

  monkeypatch.setattr(
    market_data_staging_cleanup, "run_market_data_staging_sweeper", idle
  )
  monkeypatch.setattr(worker, "sweep", idle)
  monkeypatch.setattr(worker, "_pause", fast_pause)
  monkeypatch.setattr(case.storage, "write", slow_write)
  monkeypatch.setattr(case.first, "renew", renew)
  monkeypatch.setattr(case.first, "dispatch_history_collection", AsyncMock())
  monkeypatch.setattr(case.first, "consume_collection_receipts", receipts)
  monkeypatch.setenv("ENV", "testing")
  task = asyncio.create_task(worker.run(case.first, stop))
  try:
    await asyncio.wait_for(asyncio.gather(renewed.wait(), consumed.wait()), 3)
    stop.set()
    await asyncio.sleep(0.03)
    assert not task.done()
    release.set()
    await asyncio.wait_for(task, 3)
    status = await case.client.archive_status(case.request)
    assert status.phase == "WRITE" and status.write_attempts == 1
    assert status.proof is None
  finally:
    release.set()
    stop.set()
    await asyncio.gather(task, return_exceptions=True)


async def test_write_failures_are_bounded_without_hidden_sdk_retry(
  archive_case, monkeypatch
):
  case = archive_case
  await case.client.submit_archive(case.request)
  calls = []

  def fail(**kwargs):
    calls.append(1)
    raise OSError("offline")

  monkeypatch.setattr(case.storage, "write", fail)
  for _ in range(4):
    await due(case)
    assert await archive_worker.advance_realtime_archive(case.first)
  status = await case.client.archive_status(case.request)
  assert status.phase == "BLOCKED" and status.write_attempts == 4
  assert len(calls) == 4 and status.read_attempts == 0


async def test_claim_abandoned_before_io_still_consumes_budget(archive_case):
  case = archive_case
  await case.client.submit_archive(case.request)
  for _ in range(4):
    await due(case)
    assert await case.store.claim(case.first)
  await case.first.release()
  assert await case.second.acquire()
  await due(case)
  assert await case.store.claim(case.second) is None
  status = await case.client.archive_status(case.request)
  assert status.phase == "BLOCKED" and status.write_attempts == 4
  assert not case.storage.lines


@pytest.mark.parametrize(
  "invalid",
  [
    "boolean_price",
    "nan_price",
    "extra_field",
    "naive_time",
    "subminute",
    "boolean_sequence",
  ],
)
async def test_invalid_wire_revision_is_rejected_before_admission(
  archive_case, invalid
):
  case = archive_case
  value = case.request.model_dump(mode="json")
  if invalid == "boolean_price":
    value["bar"]["open"] = True
  elif invalid == "nan_price":
    value["bar"]["open"] = "NaN"
  elif invalid == "extra_field":
    value["account_id"] = "unexpected"
  elif invalid == "naive_time":
    value["minute"] = "2026-09-10T09:31:00"
  elif invalid == "subminute":
    value["minute"] = "2026-09-10T09:31:00.000001+08:00"
  else:
    value["sequence"] = True
  result = await case.client.client.post(
    "/market-data/internal/v1/archives", json=value
  )
  assert result.status_code == 422
  async with case.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM realtime_archive_revision")) == 0


async def test_receipt_transaction_failure_recovers_by_readback_without_rewrite(
  archive_case,
):
  case = archive_case
  identity = await case.client.submit_archive(case.request)
  assert await archive_worker.advance_realtime_archive(case.first)
  async with case.engine.begin() as db:
    await db.execute(
      text("""
      CREATE FUNCTION reject_archive_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.phase='VERIFIED' THEN RAISE EXCEPTION 'injected receipt failure'; END IF;
        RETURN NEW;
      END $$
    """)
    )
    await db.execute(
      text(
        "CREATE TRIGGER receipt_failure BEFORE UPDATE ON realtime_archive_revision FOR EACH ROW EXECUTE FUNCTION reject_archive_receipt()"
      )
    )
  from sqlalchemy.exc import SQLAlchemyError

  with pytest.raises(SQLAlchemyError):
    await archive_worker.advance_realtime_archive(case.first)
  status = await case.client.archive_status(case.request)
  assert (
    status.phase == "READBACK" and status.proof is None and status.read_attempts == 1
  )
  async with case.engine.begin() as db:
    await db.execute(text("DROP TRIGGER receipt_failure ON realtime_archive_revision"))
  await case.first.release()
  assert await case.second.acquire()
  await due(case)
  assert await archive_worker.advance_realtime_archive(case.second)
  status = await case.client.archive_status(case.request)
  assert status.request_id == identity and status.phase == "VERIFIED"
  assert status.write_attempts == 1 and status.read_attempts == 2
  assert len(case.storage.lines) == 1
