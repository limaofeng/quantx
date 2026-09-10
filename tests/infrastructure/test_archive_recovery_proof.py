"""Real PG recovery closure from genuinely written immutable native versions."""
# ruff: noqa: F811

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from quantx_contracts.market_data_service import HistoryDemand
from quantx_infrastructure.services.archive_recovery import verify_archive_recovery
from quantx_market_data import worker
from sqlalchemy import text

from tests.infrastructure.test_engine_archive_generation import archive_db  # noqa: F401
from tests.infrastructure.test_realtime_archive_delivery import (
  archive_case,  # noqa: F401
)
from tests.infrastructure.test_realtime_archive_reader import (
  original,
  publish_native,
  reader_case,  # noqa: F401
)


async def pending(case):
  day = case.scope.start_minute.date()
  demand = await case.first.submit_history_demand(
    HistoryDemand(instrument=case.request.instrument, period="1m", trading_date=day)
  )
  async with case.engine.begin() as db:
    await db.execute(
      text("INSERT INTO market_data_request(request_id) VALUES ('original-source')")
    )
    await db.execute(
      text(
        "UPDATE market_data_demand SET source_request_id='original-source' WHERE demand_id=:id"
      ),
      {"id": demand},
    )
    await db.execute(
      text("""
      INSERT INTO engine_archive_recovery(generation,instrument,trading_date,demand_id,state)
      VALUES (:generation,:instrument,:day,:id,'WAITING')
    """),
      {
        "generation": case.scope.generation,
        "instrument": case.scope.instrument,
        "day": day,
        "id": demand,
      },
    )
    # Scope planning is independently tested; leave only the proof work due.
    await db.execute(
      text(
        "UPDATE engine_archive_scope SET next_probe_at=clock_timestamp()+interval '1 day'"
      )
    )
  return demand


async def result(case):
  async with case.engine.connect() as db:
    return (
      (await db.execute(text("SELECT * FROM engine_archive_recovery"))).mappings().one()
    )


async def test_proof_keeps_original_identity_and_survives_owner_restart(reader_case):
  case = reader_case
  demand = await pending(case)
  audit = await publish_native(
    case,
    [original(case)],
    created_at=case.request.minute.replace(tzinfo=None) + timedelta(hours=6),
  )
  assert await verify_archive_recovery(case.first)
  saved = await result(case)
  assert saved["state"] == "VERIFIED" and saved["verified_at"]
  assert saved["evidence"]["demand_id"] == demand
  assert saved["evidence"]["original_source_request_id"] == "original-source"
  assert saved["evidence"]["proof_source_request_id"] == "native-0"
  assert saved["evidence"]["storage_version"] == audit["native_storage_version"]
  assert (
    saved["evidence"]["content_sha256"]
    == audit["content_verification"]["source_sha256"]
  )
  async with case.engine.connect() as db:
    assert (
      await db.scalar(text("SELECT source_request_id FROM market_data_demand"))
      == "original-source"
    )
    assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 1
  await case.first.release()
  assert await case.second.acquire()
  with pytest.raises(RuntimeError):
    await verify_archive_recovery(case.first)
  assert not await verify_archive_recovery(case.second)
  assert await result(case) == saved


@pytest.mark.parametrize(
  "kind,reason",
  [
    ("missing", "WAITING_NATIVE_VERSION"),
    ("intraday", "WAITING_FULL_SESSION_VERSION"),
    ("corrupt", "NATIVE_VERSION_PROOF_INVALID"),
    ("remote", "WAITING_REMOTE_SESSION_PROOF"),
  ],
)
async def test_insufficient_proof_keeps_gap_and_delays_probe(reader_case, kind, reason):
  case = reader_case
  demand = await pending(case)
  if kind in {"intraday", "corrupt"}:

    def corrupt(audit):
      if kind == "corrupt":
        audit["content_verification"]["persisted_sha256"] = "0" * 64

    await publish_native(
      case,
      [original(case)],
      corrupt=corrupt,
      created_at=case.request.minute.replace(tzinfo=None)
      + timedelta(hours=6 if kind == "corrupt" else 0),
    )
  if kind == "remote":
    async with case.engine.begin() as db:
      await db.execute(
        text(
          "UPDATE market_data_demand SET source_kind='REMOTE',source_request_id=NULL WHERE demand_id=:id"
        ),
        {"id": demand},
      )
  assert not await verify_archive_recovery(case.first)
  saved = await result(case)
  assert saved["state"] == "WAITING" and saved["reason"] == reason
  assert saved["verified_at"] is None and saved["evidence"] is None
  assert saved["next_probe_at"] > saved["created_at"]
  assert not await verify_archive_recovery(case.first)
  assert await result(case) == saved


async def test_default_worker_closes_proof_while_ingestion_waits(
  reader_case, monkeypatch
):
  from quantx_infrastructure.services import market_data_staging_cleanup

  case = reader_case
  await pending(case)
  await publish_native(
    case,
    [original(case)],
    created_at=case.request.minute.replace(tzinfo=None) + timedelta(hours=6),
  )
  await case.first.release()
  stop = asyncio.Event()

  async def idle(*args, **kwargs):
    await stop.wait()

  async def observe():
    if (await result(case))["state"] == "VERIFIED":
      stop.set()

  monkeypatch.setattr(worker, "sweep", idle)
  monkeypatch.setattr(
    market_data_staging_cleanup, "run_market_data_staging_sweeper", idle
  )
  monkeypatch.setattr(case.first, "consume_collection_receipts", observe)
  monkeypatch.setattr(case.first, "dispatch_history_collection", AsyncMock())
  monkeypatch.setenv("ENV", "testing")
  await asyncio.wait_for(worker.run(case.first, stop), 4)
  assert (await result(case))["state"] == "VERIFIED"


async def test_migration_refuses_to_remove_original_recovery_evidence(reader_case):
  case = reader_case
  await pending(case)
  async with case.engine.begin() as db:

    def downgrade(connection):
      from alembic.migration import MigrationContext
      from alembic.operations import Operations

      case.recovery_migration.op = Operations(MigrationContext.configure(connection))
      case.recovery_migration.downgrade()

    with pytest.raises(RuntimeError, match="cannot remove"):
      await db.run_sync(downgrade)
  assert (await result(case))["state"] == "WAITING"
