"""Real transactional scope planning, original demand IDs and default supervision."""
# ruff: noqa: F811

import asyncio
from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from quantx_contracts.market_data_service import HistoryDemand
from quantx_contracts.realtime_archive import ArchiveRecoveryScope
from quantx_infrastructure.services.archive_recovery import (
  SHANGHAI,
  advance_archive_recovery,
)
from quantx_market_data import worker
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.infrastructure.test_engine_archive_generation import (  # noqa: F401
  archive_db,
  lock,
  unlock,
)
from tests.infrastructure.test_realtime_archive_delivery import (
  archive_case,  # noqa: F401
)


async def set_scope(case, day):
  async with case.engine.begin() as db:
    await db.execute(text("DELETE FROM engine_archive_scope"))
  case.scope = ArchiveRecoveryScope(
    generation=case.request.generation,
    instrument=case.request.instrument,
    start_minute=datetime.combine(day, time(9, 30), SHANGHAI),
  )
  await case.client.register_archive_scope(case.scope)
  case.day = day


@pytest.fixture
async def recovery_case(archive_case):
  case = archive_case
  async with case.engine.connect() as db:
    now = await db.scalar(text("SELECT clock_timestamp()"))
  day = now.astimezone(SHANGHAI).date() - timedelta(days=2)
  while day.weekday() >= 5 or (day.month, day.day) == (1, 1):
    day -= timedelta(days=1)
  await set_scope(case, day)
  async with case.engine.begin() as db:
    await db.execute(
      text("INSERT INTO holidays VALUES ('SH',:year,:day,'calendar fixture')"),
      {
        "year": day.year,
        "day": date(day.year, 1, 1),
      },
    )
  return case


async def scope_row(case):
  async with case.engine.connect() as db:
    return (
      (await db.execute(text("SELECT * FROM engine_archive_scope"))).mappings().one()
    )


async def force_due(case):
  async with case.engine.begin() as db:
    await db.execute(
      text("UPDATE engine_archive_scope SET next_probe_at=clock_timestamp()")
    )


async def test_recovery_preserves_existing_demand_source_and_worker_restart(
  recovery_case,
):
  case = recovery_case
  demand = HistoryDemand(
    instrument=case.request.instrument, period="1m", trading_date=case.day
  )
  original_id = await case.first.submit_history_demand(demand)
  async with case.engine.begin() as db:
    await db.execute(
      text("INSERT INTO market_data_request(request_id) VALUES ('original-source')")
    )
    await db.execute(
      text(
        "UPDATE market_data_demand SET source_request_id='original-source' WHERE demand_id=:id"
      ),
      {"id": original_id},
    )
  assert await advance_archive_recovery(case.first)
  async with case.engine.connect() as db:
    row = (
      (await db.execute(text("SELECT * FROM engine_archive_recovery"))).mappings().one()
    )
    assert row["demand_id"] == original_id and row["state"] == "WAITING"
    assert row["evidence"] is None
    assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 1
    assert (
      await db.scalar(text("SELECT source_request_id FROM market_data_demand"))
      == "original-source"
    )
  assert (await scope_row(case))["next_day"] == case.day + timedelta(days=1)
  await case.first.release()
  assert await case.second.acquire()
  with pytest.raises(RuntimeError):
    await advance_archive_recovery(case.first)
  await force_due(case)
  assert await advance_archive_recovery(case.second)
  async with case.engine.connect() as db:
    assert (
      await db.scalar(
        text(
          "SELECT count(*) FROM engine_archive_recovery WHERE trading_date=:day AND demand_id=:id"
        ),
        {"day": case.day, "id": original_id},
      )
      == 1
    )


async def test_demand_and_cursor_rollback_together_when_scope_update_fails(
  recovery_case,
):
  case = recovery_case
  async with case.engine.begin() as db:
    await db.execute(
      text("""
      CREATE FUNCTION reject_scope_advance() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'injected scope checkpoint failure'; END $$
    """)
    )
    await db.execute(
      text(
        "CREATE TRIGGER reject_scope BEFORE UPDATE ON engine_archive_scope FOR EACH ROW EXECUTE FUNCTION reject_scope_advance()"
      )
    )
  with pytest.raises(DBAPIError):
    await advance_archive_recovery(case.first)
  async with case.engine.begin() as db:
    assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 0
    assert await db.scalar(text("SELECT count(*) FROM engine_archive_recovery")) == 0
    assert (
      await db.scalar(text("SELECT next_day FROM engine_archive_scope")) == case.day
    )
    await db.execute(text("DROP TRIGGER reject_scope ON engine_archive_scope"))
  assert await advance_archive_recovery(case.first)
  assert (await scope_row(case))["next_day"] == case.day + timedelta(days=1)


@pytest.mark.parametrize("kind", ["weekend", "holiday", "missing"])
async def test_calendar_evidence_or_wait_without_creating_native_work(
  recovery_case, kind
):
  case = recovery_case
  if kind == "weekend":
    await set_scope(case, case.day - timedelta(days=(case.day.weekday() - 5) % 7))
  async with case.engine.begin() as db:
    if kind == "holiday":
      await db.execute(
        text("INSERT INTO holidays VALUES ('SH',:year,:day,'closed')"),
        {"year": case.day.year, "day": case.day},
      )
    elif kind == "missing":
      await db.execute(text("DELETE FROM holidays"))
  assert await advance_archive_recovery(case.first)
  async with case.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 0
    rows = (
      (await db.execute(text("SELECT * FROM engine_archive_recovery"))).mappings().all()
    )
  if kind == "missing":
    assert rows == []
    assert (await scope_row(case))["reason"] == "WAITING_CALENDAR"
    assert (await scope_row(case))["next_day"] == case.day
  else:
    assert rows[0]["state"] == "NO_SESSION" and rows[0]["demand_id"] is None
    assert rows[0]["evidence"]["kind"] == (
      "A_SHARE_WEEKEND" if kind == "weekend" else "CALENDAR_HOLIDAY"
    )


async def test_source_exit_records_conservative_end_and_stops_planning_after_it(
  recovery_case,
):
  case = recovery_case
  await unlock(case.source)
  try:
    assert await advance_archive_recovery(case.first)
    row = await scope_row(case)
    assert row["ended_at"] is not None
    async with case.engine.begin() as db:
      await db.execute(
        text(
          "UPDATE engine_archive_scope SET next_day=:day,next_probe_at=clock_timestamp()"
        ),
        {"day": row["ended_at"].astimezone(SHANGHAI).date() + timedelta(days=1)},
      )
    assert not await advance_archive_recovery(case.first)
  finally:
    await lock(case.source)


async def test_closed_scope_allows_only_accepted_replay_even_if_source_relocks(
  recovery_case,
):
  case = recovery_case
  request = case.request.model_copy(update={"minute": case.scope.start_minute})
  identity = await case.client.submit_archive(request)
  await unlock(case.source)
  try:
    assert await advance_archive_recovery(case.first)
  finally:
    await lock(case.source)
  assert await case.client.submit_archive(request) == identity
  with pytest.raises(httpx.HTTPStatusError) as error:
    await case.client.submit_archive(
      request.model_copy(update={"sequence": request.sequence + 1})
    )
  assert error.value.response.status_code == 409


async def test_future_session_wait_is_persisted_without_demand(recovery_case):
  case = recovery_case
  async with case.engine.connect() as db:
    now = await db.scalar(text("SELECT clock_timestamp()"))
  day = now.astimezone(SHANGHAI).date() + timedelta(days=1)
  await set_scope(case, day)
  assert await advance_archive_recovery(case.first)
  row = await scope_row(case)
  assert row["reason"] == "WAITING_SESSION_CLOSE"
  assert row["next_probe_at"] == datetime.combine(day, time(15, 1), SHANGHAI)
  async with case.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 0


async def test_holiday_cannot_skip_an_observed_archive(recovery_case):
  case = recovery_case
  async with case.engine.begin() as db:
    await db.execute(
      text("INSERT INTO holidays VALUES ('SH',:year,:day,'closed')"),
      {"year": case.day.year, "day": case.day},
    )
  await case.client.submit_archive(
    case.request.model_copy(update={"minute": case.scope.start_minute})
  )
  assert await advance_archive_recovery(case.first)
  row = await scope_row(case)
  assert row["next_day"] == case.day
  assert row["reason"] == "CALENDAR_ARCHIVE_CONFLICT"
  async with case.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM engine_archive_recovery")) == 0


async def test_pending_demand_capacity_retains_unplanned_day(recovery_case):
  case = recovery_case
  async with case.engine.begin() as db:
    await db.execute(
      text("""
      INSERT INTO market_data_demand(demand_id,partition,source_kind)
      SELECT lpad(to_hex(n),64,'0'),jsonb_build_object(
        'instrument',lpad(n::text,6,'0')||'.SZ','period','1m','trading_date',CAST(:day AS text)
      ),'AGENT' FROM generate_series(1,5000) AS n
    """),
      {"day": str(case.day)},
    )
  assert await advance_archive_recovery(case.first)
  row = await scope_row(case)
  assert row["next_day"] == case.day
  assert row["reason"] == "HISTORY_DEMAND_CAPACITY"
  async with case.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM engine_archive_recovery")) == 0
    assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 5000


async def test_default_worker_plans_recovery_while_ingestion_is_waiting(
  recovery_case, monkeypatch
):
  from quantx_infrastructure.services import market_data_staging_cleanup

  case = recovery_case
  await case.first.release()
  stop = asyncio.Event()

  async def idle(*args, **kwargs):
    await stop.wait()

  async def observe():
    async with case.engine.connect() as db:
      if await db.scalar(text("SELECT count(*) FROM engine_archive_recovery")):
        stop.set()

  monkeypatch.setattr(
    market_data_staging_cleanup, "run_market_data_staging_sweeper", idle
  )
  monkeypatch.setattr(worker, "sweep", idle)
  monkeypatch.setattr(case.first, "consume_collection_receipts", observe)
  monkeypatch.setattr(case.first, "dispatch_history_collection", AsyncMock())
  monkeypatch.setenv("ENV", "testing")
  await asyncio.wait_for(worker.run(case.first, stop), 3)
  async with case.engine.connect() as db:
    assert await db.scalar(text("SELECT count(*) FROM market_data_demand")) == 1
    assert (
      await db.scalar(text("SELECT state FROM engine_archive_recovery")) == "WAITING"
    )
